"""
Season-mode data layer: news, injuries, waiver/pickup trends, and weekly
matchups with streaming (best K / DEF) suggestions.

All data comes from FREE public JSON endpoints -- no API keys, no scraping:
  * ESPN   (news, injuries, scoreboard + Vegas odds)
  * Sleeper (league-wide trending adds, player database, season state)

Everything is cached locally so the tab is fast and works offline. This module
has NO GUI dependencies so it can be tested headlessly.
"""

from __future__ import annotations

import json
import os
import re
import time

# Use the OS certificate store so HTTPS works behind corporate proxies/MITM.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import requests


ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
SLEEPER = "https://api.sleeper.app/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (FFDraftAssistant)"}

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

FANTASY_POS = {"QB", "RB", "WR", "TE", "K", "PK", "DEF", "DST"}


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------
def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"season_{name}.json")


def _get_json(url: str, params: dict | None = None, timeout: int = 20):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _cached(name: str, url: str, max_age_hours: float,
            params: dict | None = None, force: bool = False):
    """Return (data, source). Network first (unless cache fresh); cache fallback."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(name)
    cache = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = None

    if cache and not force:
        if time.time() - cache.get("_fetched_at", 0) < max_age_hours * 3600:
            return cache["data"], "cache"

    try:
        data = _get_json(url, params)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"_fetched_at": time.time(), "data": data}, f)
        return data, "network"
    except Exception:
        if cache:
            return cache["data"], "cache"
        raise


def norm_name(s: str) -> str:
    """Normalize a player name for fuzzy matching across data sources."""
    s = s.lower().replace("&", "and")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


# ---------------------------------------------------------------------------
# Season state
# ---------------------------------------------------------------------------
def get_state(force: bool = False) -> dict:
    try:
        data, _ = _cached("state", f"{SLEEPER}/state/nfl", 6, force=force)
        return data
    except Exception:
        return {}


def state_label(state: dict) -> str:
    if not state:
        return ""
    season = state.get("season", "")
    stype = state.get("season_type", "")
    wk = state.get("display_week") or state.get("week") or 0
    pretty = {"off": "offseason", "pre": "preseason",
              "regular": "regular season", "post": "playoffs"}.get(stype, stype)
    if stype == "off":
        return f"{season} {pretty} \u00b7 Week {wk} preview"
    return f"{season} {pretty} \u00b7 Week {wk}"


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
def get_news(limit: int = 20, force: bool = False):
    data, src = _cached("news", f"{ESPN}/news", 1, force=force)
    out = []
    for a in data.get("articles", []):
        link = ""
        links = a.get("links") or {}
        if isinstance(links, dict):
            link = ((links.get("web") or {}).get("href")
                    or (links.get("mobile") or {}).get("href") or "")
        out.append({
            "headline": a.get("headline", "") or a.get("title", ""),
            "description": a.get("description", "") or "",
            "published": a.get("published", "") or "",
            "link": link,
        })
    return out[:limit], src


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------
def get_injuries(force: bool = False):
    data, src = _cached("injuries", f"{ESPN}/injuries", 1, force=force)
    out = []
    for team in data.get("injuries", []):
        team_name = team.get("displayName", "")
        nick = team_name.split()[-1] if team_name else ""
        for it in team.get("injuries", []):
            ath = it.get("athlete") or {}
            pos = ""
            p = ath.get("position")
            if isinstance(p, dict):
                pos = p.get("abbreviation", "") or ""
            tm = ath.get("team")
            abbr = (tm or {}).get("abbreviation") if isinstance(tm, dict) else ""
            det = it.get("details") or {}
            out.append({
                "name": ath.get("displayName", "") or "",
                "pos": pos,
                "team": abbr or nick,
                "status": it.get("status", "") or "",
                "type": det.get("type", "") or "",
                "return": det.get("returnDate", "") or "",
                "note": (it.get("shortComment") or it.get("longComment") or ""),
            })
    # sort by severity: Out first, then Doubtful/Questionable, then the rest
    sev = {"Out": 0, "Injured Reserve": 0, "Doubtful": 1,
           "Questionable": 2, "Day-To-Day": 3}
    out.sort(key=lambda r: (sev.get(r["status"], 4), r["name"]))
    return out, src


# ---------------------------------------------------------------------------
# Player database (Sleeper) + trending pickups
# ---------------------------------------------------------------------------
def get_player_map(force: bool = False) -> dict:
    data, _ = _cached("players", f"{SLEEPER}/players/nfl", 24, force=force)
    return data if isinstance(data, dict) else {}


def get_pickups(limit: int = 25, lookback_hours: int = 168, force: bool = False):
    trend, src = _cached(
        "trending_add",
        f"{SLEEPER}/players/nfl/trending/add",
        2, params={"lookback_hours": lookback_hours, "limit": limit}, force=force)
    pm = get_player_map(force=force)
    out = []
    for t in trend or []:
        pid = str(t.get("player_id"))
        info = pm.get(pid, {})
        pos = (info.get("position") or "").upper()
        name = (info.get("full_name")
                or " ".join(x for x in (info.get("first_name"),
                                        info.get("last_name")) if x)
                or pid)
        out.append({
            "player_id": pid,
            "name": name,
            "pos": "K" if pos == "PK" else pos,
            "team": info.get("team") or "FA",
            "count": int(t.get("count", 0)),
            "injury_status": info.get("injury_status") or "",
        })
    return out, src


# ---------------------------------------------------------------------------
# Matchups + streaming (from Vegas odds)
# ---------------------------------------------------------------------------
def get_matchups(force: bool = False):
    data, src = _cached("scoreboard", f"{ESPN}/scoreboard", 1, force=force)
    week = (data.get("week") or {}).get("number")
    games = []
    for ev in data.get("events", []):
        comps = ev.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        home = away = None
        for c in comp.get("competitors", []):
            t = c.get("team") or {}
            side = {"abbr": t.get("abbreviation", ""),
                    "name": t.get("shortDisplayName") or t.get("displayName", "")}
            if c.get("homeAway") == "home":
                home = side
            else:
                away = side
        if not home or not away:
            continue
        odds = (comp.get("odds") or [{}])
        odds = odds[0] if odds else {}
        total = odds.get("overUnder")
        spread = odds.get("spread")  # home line: negative = home favored
        home_imp = away_imp = None
        if isinstance(total, (int, float)) and isinstance(spread, (int, float)):
            home_imp = round(total / 2 - spread / 2, 1)
            away_imp = round(total / 2 + spread / 2, 1)
        games.append({
            "home": home, "away": away,
            "total": total, "spread": spread,
            "home_implied": home_imp, "away_implied": away_imp,
            "date": comp.get("date") or ev.get("date") or "",
        })
    return games, src, week


def best_streamers(games: list):
    """From matchup odds, rank streaming defenses and kickers.

    DEF: best = facing the offense with the LOWEST implied team total.
    K:   best = on the team with the HIGHEST implied team total.
    """
    defenses, kickers = [], []
    for g in games:
        hi, ai = g.get("home_implied"), g.get("away_implied")
        if hi is None or ai is None:
            continue
        h, a = g["home"], g["away"]
        defenses.append({"team": h["abbr"], "opp": a["abbr"],
                         "vs": "vs", "opp_implied": ai})
        defenses.append({"team": a["abbr"], "opp": h["abbr"],
                         "vs": "@", "opp_implied": hi})
        kickers.append({"team": h["abbr"], "opp": a["abbr"],
                        "vs": "vs", "implied": hi})
        kickers.append({"team": a["abbr"], "opp": h["abbr"],
                        "vs": "@", "implied": ai})
    defenses.sort(key=lambda x: x["opp_implied"])
    kickers.sort(key=lambda x: -x["implied"])
    return defenses, kickers


# ---------------------------------------------------------------------------
# League-aware analytics: FAAB waivers, roster, start/sit, byes, matchup
#
# These are PURE functions (no network) so they stay headless-testable. The GUI
# fetches the raw Sleeper data (rosters, transactions, matchups) and the ADP
# id-bridge, then feeds them in here.
# ---------------------------------------------------------------------------
import math

_HURT = {"out": 3, "injured reserve": 3, "ir": 3, "pup": 3, "doubtful": 2,
         "suspended": 2, "questionable": 1, "day-to-day": 1}
AVG_TEAM_TOTAL = 22.0  # rough league-average implied points, for matchup deltas


def rostered_ids(rosters: list) -> set:
    """Every player_id owned by any team in the league (as strings)."""
    out = set()
    for r in rosters or []:
        for pid in (r.get("players") or []):
            out.add(str(pid))
    return out


def faab_status(league: dict, roster: dict) -> dict:
    settings = (league or {}).get("settings") or {}
    budget = int(settings.get("waiver_budget", 0) or 0)
    rs = (roster or {}).get("settings") or {}
    used = int(rs.get("waiver_budget_used", 0) or 0)
    return {"budget": budget, "used": used, "left": max(budget - used, 0),
            "waiver_position": rs.get("waiver_position")}


def _bridge_player(bridge, pid):
    return (bridge or {}).get(str(pid))


def free_agents(pickups: list, rostered: set, need_positions=None,
                bridge=None, limit: int = 40) -> list:
    """Trending adds that are still FREE in *your* league, annotated.

    ``pickups`` is get_pickups() output (has player_id/count). We drop anyone
    already rostered, then enrich with bye/vorp/adp from the ADP id-bridge and
    whether the position fills one of your roster needs.
    """
    need_positions = need_positions or set()
    out = []
    for p in pickups or []:
        pid = str(p.get("player_id") or "")
        if not pid or pid in rostered:
            continue
        eng = _bridge_player(bridge, pid)
        pos = p.get("pos") or (eng.position if eng else "?")
        pos = "K" if pos == "PK" else ("DEF" if pos == "DST" else pos)
        out.append({
            "player_id": pid,
            "name": p.get("name"),
            "pos": pos,
            "team": p.get("team") or (eng.team if eng else "FA"),
            "count": int(p.get("count", 0) or 0),
            "injury_status": p.get("injury_status") or "",
            "bye": (eng.bye if eng else 0) or 0,
            "vorp": (eng.vorp if eng else None),
            "adp": (eng.adp if eng else None),
            "fills_need": pos in need_positions,
        })
        if len(out) >= limit:
            break
    return out


def suggest_bid(rank: int, faab_left: int, fills_need: bool,
                has_value: bool = False) -> dict:
    """A FAAB bid recommendation.

    Driven by the player's rank on your waiver board (demand), whether they
    fill a roster need, and whether they carry real redraft value (a startable
    player who slipped to waivers deserves the biggest bid). Deliberately
    conservative -- most pickups should be a few dollars, not a third of budget.
    """
    if faab_left <= 0:
        return {"dollars": 0, "pct": 0.0, "tier": "\u2014"}
    pct = 0.12 * (0.66 ** max(rank, 0))    # #1 ~12%, decays fast down the board
    if fills_need:
        pct += 0.06
    if has_value:
        pct += 0.12                        # an actually-startable FA is gold
    pct = min(pct, 0.5)
    dollars = max(1, round(faab_left * pct))
    tier = ("Priority" if pct >= 0.22 else
            "Solid" if pct >= 0.09 else "Speculative")
    return {"dollars": dollars, "pct": pct, "tier": tier}


def _hurt_rank(injury_status: str) -> int:
    return _HURT.get((injury_status or "").lower(), 0)


def drop_candidates(my_players: list, n: int = 6) -> list:
    """Your most-droppable players first (injured, then lowest value)."""
    def key(p):
        return (-_hurt_rank(getattr(p, "injury_status", "")),
                getattr(p, "vorp", 0.0))
    return sorted(my_players or [], key=key)[:n]


def team_implied_map(games: list) -> dict:
    """{team_abbr: implied_team_total} from Vegas odds."""
    m = {}
    for g in games or []:
        hi, ai = g.get("home_implied"), g.get("away_implied")
        if hi is not None:
            m[g["home"]["abbr"]] = hi
        if ai is not None:
            m[g["away"]["abbr"]] = ai
    return m


def _opp_map(games: list) -> dict:
    """{team_abbr: (opp_abbr, home_or_away)} for the week."""
    m = {}
    for g in games or []:
        h, a = g["home"]["abbr"], g["away"]["abbr"]
        m[h] = (a, "vs")
        m[a] = (h, "@")
    return m


def week_score(vorp, implied) -> float:
    """Matchup-adjusted weekly score: season value + game environment.

    Deliberately simple/transparent (no weekly projections exist in the free
    feeds): talent from VORP plus a Vegas game-environment bump.
    """
    talent = float(vorp or 0.0)
    env = (float(implied) - AVG_TEAM_TOTAL) if implied is not None else 0.0
    return round(talent * 0.5 + env * 2.0, 1)


def roster_detail(roster: dict, player_map: dict, bridge: dict,
                  games: list = None) -> list:
    """Turn a Sleeper roster into rich rows for display / start-sit.

    Returns dicts with name/pos/team/bye/vorp/injury, whether they're a current
    starter, their game opponent, implied total, and a matchup-adjusted score.
    """
    starters = set(str(x) for x in (roster.get("starters") or []) if x)
    reserve = set(str(x) for x in (roster.get("reserve") or []) if x)
    implied = team_implied_map(games)
    opps = _opp_map(games)
    rows = []
    for pid in (roster.get("players") or []):
        pid = str(pid)
        eng = _bridge_player(bridge, pid)
        info = (player_map or {}).get(pid, {}) if not eng else {}
        if eng:
            name, pos, team = eng.name, eng.position, eng.team
            bye, vorp = eng.bye, eng.vorp
            inj = getattr(eng, "injury_status", "")
        else:
            pos = (info.get("position") or "?").upper()
            pos = "K" if pos == "PK" else ("DEF" if pos == "DST" else pos)
            name = (info.get("full_name")
                    or " ".join(x for x in (info.get("first_name"),
                                            info.get("last_name")) if x) or pid)
            team = info.get("team") or "FA"
            bye, vorp = 0, None
            inj = info.get("injury_status") or ""
        disp_pos = "K" if pos == "PK" else ("DEF" if pos == "DST" else pos)
        opp, homeaway = opps.get(team, ("", ""))
        imp = implied.get(team)
        rows.append({
            "player_id": pid, "name": name, "pos": disp_pos, "team": team,
            "bye": bye or 0, "vorp": vorp, "injury_status": inj,
            "is_starter": pid in starters, "is_reserve": pid in reserve,
            "opp": opp, "homeaway": homeaway, "implied": imp,
            "score": week_score(vorp, imp),
        })
    return rows


_FLEX_ELIGIBLE = {"RB", "WR", "TE"}


def start_sit(roster_rows: list) -> list:
    """Suggest lineup swaps where a bench player out-scores a starter.

    Compares within a position (QB/RB/WR/TE/K/DEF) and for FLEX-eligible
    positions, using the matchup-adjusted week_score. Returns a list of
    suggestion dicts (bench player, the starter they'd replace, delta).
    """
    starters = [r for r in roster_rows if r["is_starter"]]
    bench = [r for r in roster_rows
             if not r["is_starter"] and not r["is_reserve"]]
    suggestions = []
    used_bench = set()
    for st in sorted(starters, key=lambda r: r["score"]):
        pool = [b for b in bench
                if b["player_id"] not in used_bench
                and (b["pos"] == st["pos"]
                     or (st["pos"] in _FLEX_ELIGIBLE
                         and b["pos"] in _FLEX_ELIGIBLE))]
        if not pool:
            continue
        best = max(pool, key=lambda b: b["score"])
        if best["score"] - st["score"] >= 3.0:
            suggestions.append({
                "start": best, "sit": st,
                "delta": round(best["score"] - st["score"], 1)})
            used_bench.add(best["player_id"])
    suggestions.sort(key=lambda s: -s["delta"])
    return suggestions


def bye_report(my_players: list, current_week: int = 0) -> list:
    """Upcoming byes on your roster, grouped by week (soonest first)."""
    from collections import defaultdict
    weeks = defaultdict(list)
    for p in my_players or []:
        bye = getattr(p, "bye", 0) or 0
        if bye and bye >= (current_week or 0):
            weeks[bye].append(p)
    out = []
    for wk in sorted(weeks):
        players = weeks[wk]
        from collections import Counter
        by_pos = Counter(getattr(p, "position", "?") for p in players)
        out.append({"week": wk, "players": players, "count": len(players),
                    "by_pos": dict(by_pos)})
    return out


def parse_transactions(transactions: list, player_map: dict,
                       labels: dict = None) -> list:
    """FAAB waiver results: who was added, by whom, for how much."""
    labels = labels or {}
    out = []
    for t in transactions or []:
        if t.get("status") != "complete":
            continue
        if t.get("type") not in ("waiver", "free_agent"):
            continue
        bid = ((t.get("settings") or {}).get("waiver_bid"))
        adds = t.get("adds") or {}
        if not adds:
            continue
        for pid, rid in adds.items():
            info = (player_map or {}).get(str(pid), {})
            name = (info.get("full_name")
                    or " ".join(x for x in (info.get("first_name"),
                                            info.get("last_name")) if x)
                    or str(pid))
            pos = (info.get("position") or "").upper()
            out.append({
                "name": name,
                "pos": "K" if pos == "PK" else pos,
                "team": info.get("team") or "FA",
                "manager": labels.get(str(rid), f"Team {rid}"),
                "bid": bid,
                "week": t.get("leg"),
            })
    out.sort(key=lambda r: (-(r["week"] or 0), -(r["bid"] or 0)))
    return out


def my_matchup(matchups: list, my_roster_id) -> tuple:
    """Return (my_entry, opponent_entry) for the week, or (None, None)."""
    mine = next((m for m in matchups or []
                 if str(m.get("roster_id")) == str(my_roster_id)), None)
    if not mine:
        return None, None
    mid = mine.get("matchup_id")
    opp = next((m for m in matchups or []
                if m.get("matchup_id") == mid
                and str(m.get("roster_id")) != str(my_roster_id)), None)
    return mine, opp


def lineup_rows(entry: dict, player_map: dict, bridge: dict,
                games: list = None) -> list:
    """Starting lineup of a matchup entry as display rows (in slot order)."""
    if not entry:
        return []
    implied = team_implied_map(games)
    opps = _opp_map(games)
    pts = entry.get("starters_points") or []
    rows = []
    for i, pid in enumerate(entry.get("starters") or []):
        pid = str(pid)
        if not pid or pid == "0":
            rows.append({"name": "(empty)", "pos": "", "team": "",
                         "points": 0, "opp": "", "implied": None})
            continue
        eng = _bridge_player(bridge, pid)
        info = (player_map or {}).get(pid, {}) if not eng else {}
        if eng:
            name, pos, team = eng.name, eng.position, eng.team
        else:
            pos = (info.get("position") or "?").upper()
            name = (info.get("full_name")
                    or " ".join(x for x in (info.get("first_name"),
                                            info.get("last_name")) if x) or pid)
            team = info.get("team") or ""
        disp_pos = "K" if pos == "PK" else ("DEF" if pos == "DST" else pos)
        opp, homeaway = opps.get(team, ("", ""))
        rows.append({
            "name": name, "pos": disp_pos, "team": team,
            "points": pts[i] if i < len(pts) else 0,
            "opp": f"{homeaway} {opp}".strip(), "implied": implied.get(team)})
    return rows


# ---------------------------------------------------------------------------
# Standings, power rankings, and trade evaluation
# ---------------------------------------------------------------------------
_POS_DISP = {"PK": "K", "DEF": "DEF"}


def _disp_pos(pos: str) -> str:
    return _POS_DISP.get(pos, pos)


def roster_engines(roster: dict, bridge: dict) -> list:
    """Engine Player objects for a roster's players (skips deep, unmatched)."""
    out = []
    for pid in (roster.get("players") or []):
        e = (bridge or {}).get(str(pid))
        if e:
            out.append(e)
    return out


def position_counts(engines: list) -> dict:
    from collections import Counter
    c = Counter()
    for e in engines:
        c[getattr(e, "position", "?")] += 1
    return dict(c)


def standings(rosters: list, labels: dict = None) -> list:
    """League standings from Sleeper roster settings (record + points for)."""
    labels = labels or {}
    out = []
    for r in rosters or []:
        s = r.get("settings") or {}
        rid = str(r.get("roster_id"))
        pf = float(s.get("fpts", 0) or 0) + float(s.get("fpts_decimal", 0) or 0) / 100.0
        pa = (float(s.get("fpts_against", 0) or 0)
              + float(s.get("fpts_against_decimal", 0) or 0) / 100.0)
        out.append({"rid": rid, "label": labels.get(rid, f"Team {rid}"),
                    "wins": int(s.get("wins", 0) or 0),
                    "losses": int(s.get("losses", 0) or 0),
                    "ties": int(s.get("ties", 0) or 0),
                    "pf": round(pf, 1), "pa": round(pa, 1)})
    out.sort(key=lambda x: (-x["wins"], -x["pf"]))
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out


def power_rankings(rosters: list, labels: dict, bridge: dict) -> list:
    """Rank teams by the total VORP of their *current* roster."""
    labels = labels or {}
    out = []
    for r in rosters or []:
        rid = str(r.get("roster_id"))
        val = sum(e.vorp for e in roster_engines(r, bridge))
        out.append({"rid": rid, "label": labels.get(rid, f"Team {rid}"),
                    "value": round(val, 1),
                    "n": len(r.get("players") or [])})
    out.sort(key=lambda x: -x["value"])
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out


def _starter_reqs(cfg) -> dict:
    """Minimum dedicated starters by base position (flex handled separately)."""
    if not cfg:
        return {}
    return {"QB": cfg.qb, "RB": cfg.rb, "WR": cfg.wr, "TE": cfg.te,
            "PK": cfg.k, "DEF": cfg.dst}


def trade_eval(a_engines: list, give_a_ids: set,
               b_engines: list, give_b_ids: set, cfg=None) -> dict:
    """Evaluate a proposed trade between two rosters.

    ``a_engines``/``b_engines`` are the two teams' current engine players;
    ``give_a_ids``/``give_b_ids`` are the sleeper_ids each side sends away.
    Returns value swing (VORP) plus positional/needs notes. VORP is zero-sum
    on a swap, so the side that gains value is the winner on paper.
    """
    give_a_ids = set(str(x) for x in (give_a_ids or set()))
    give_b_ids = set(str(x) for x in (give_b_ids or set()))
    ga = [e for e in a_engines if e.sleeper_id in give_a_ids]
    gb = [e for e in b_engines if e.sleeper_id in give_b_ids]
    give_a_val = sum(e.vorp for e in ga)
    give_b_val = sum(e.vorp for e in gb)
    net_a = round(give_b_val - give_a_val, 1)      # A receives gb, sends ga

    a_after = [e for e in a_engines if e.sleeper_id not in give_a_ids] + gb
    b_after = [e for e in b_engines if e.sleeper_id not in give_b_ids] + ga

    notes = []
    if cfg and (ga or gb):
        reqs = _starter_reqs(cfg)
        for who, before, after in (("You", a_engines, a_after),
                                   ("They", b_engines, b_after)):
            pb = position_counts(before)
            pa = position_counts(after)
            for pos, req in reqs.items():
                if not req:
                    continue
                b_ok = pb.get(pos, 0) >= req
                a_ok = pa.get(pos, 0) >= req
                if b_ok and not a_ok:
                    notes.append(
                        f"\u26A0 {who} fall below {req} startable "
                        f"{_disp_pos(pos)}")
                elif not b_ok and a_ok:
                    notes.append(
                        f"\u2714 {who} now field {req} startable "
                        f"{_disp_pos(pos)}")

    if not ga and not gb:
        verdict = "Select players on both sides to evaluate."
    elif net_a == 0:
        verdict = "Even swap on value."
    elif net_a > 0:
        verdict = f"You win the value: +{net_a:.0f} VORP"
    else:
        verdict = f"They win the value: +{-net_a:.0f} VORP"

    return {
        "net_a": net_a, "net_b": -net_a,
        "give_a": ga, "give_b": gb,
        "give_a_val": round(give_a_val, 1), "give_b_val": round(give_b_val, 1),
        "verdict": verdict, "notes": notes,
        "a_after": a_after, "b_after": b_after,
    }


if __name__ == "__main__":
    st = get_state(force=True)
    print("state:", state_label(st))
    news, _ = get_news(5)
    print("\nNEWS")
    for n in news:
        print("  -", n["headline"])
    inj, _ = get_injuries()
    print(f"\nINJURIES ({len(inj)}) top 5:")
    for r in inj[:5]:
        print(f"  {r['name']:20s} {r['pos']:3s} {r['team']:4s} {r['status']:12s} {r['type']}")
    pick, _ = get_pickups(8)
    print("\nTRENDING ADDS")
    for r in pick:
        print(f"  {r['name']:20s} {r['pos']:3s} {r['team']:4s} +{r['count']}")
    games, _, wk = get_matchups()
    d, k = best_streamers(games)
    print(f"\nWEEK {wk}: {len(games)} games")
    print("Best DEF streams:", [f"{x['team']} (opp {x['opp_implied']})" for x in d[:5]])
    print("Best K streams:", [f"{x['team']} ({x['implied']})" for x in k[:5]])
