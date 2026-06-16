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
