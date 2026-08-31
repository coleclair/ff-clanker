"""
Sleeper integration: pull a user's real league settings and live draft picks
from Sleeper's free, read-only public API (no auth or API key required).

Docs: https://docs.sleeper.com

This module has NO GUI dependencies so it can be tested headlessly:

    python sleeper.py <your_sleeper_username>

It reuses season.py's cached Sleeper player database (get_player_map) and name
normalizer (norm_name), and engine.py's RosterConfig, so the values it produces
drop straight into the app's existing settings + draft engine.
"""

from __future__ import annotations

import sys

# Use the OS certificate store so HTTPS works behind corporate proxies/MITM.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import requests

from engine import RosterConfig, SCORING_FORMATS
from season import norm_name, get_player_map

SLEEPER = "https://api.sleeper.app/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (FFDraftAssistant)"}


class SleeperError(RuntimeError):
    """Raised for user-facing problems (bad username, no leagues, etc.)."""


def _get(path: str, params: dict | None = None, timeout: int = 20):
    url = f"{SLEEPER}/{path.lstrip('/')}"
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Users, leagues, drafts (thin wrappers over the read API)
# ---------------------------------------------------------------------------
def resolve_user(username_or_id: str) -> dict:
    """Return the Sleeper user object for a username or numeric user_id."""
    u = (username_or_id or "").strip()
    if not u:
        raise SleeperError("Enter a Sleeper username.")
    data = _get(f"user/{u}")
    if not data or not data.get("user_id"):
        raise SleeperError(f"No Sleeper user found for '{u}'.")
    return data


def list_leagues(user_id: str, season) -> list:
    """Return this user's NFL leagues for a season (simplified dicts)."""
    data = _get(f"user/{user_id}/leagues/nfl/{season}")
    out = []
    for lg in data or []:
        out.append({
            "league_id": lg.get("league_id"),
            "name": lg.get("name", ""),
            "total_rosters": lg.get("total_rosters"),
            "status": lg.get("status", ""),
            "draft_id": lg.get("draft_id"),
            "scoring_settings": lg.get("scoring_settings") or {},
            "roster_positions": lg.get("roster_positions") or [],
            "settings": lg.get("settings") or {},
            "season": str(lg.get("season", season)),
        })
    return out


def get_league(league_id: str) -> dict:
    return _get(f"league/{league_id}") or {}


def get_league_drafts(league_id: str) -> list:
    return _get(f"league/{league_id}/drafts") or []


def get_draft(draft_id: str) -> dict:
    return _get(f"draft/{draft_id}") or {}


def get_draft_picks(draft_id: str) -> list:
    return _get(f"draft/{draft_id}/picks") or []


def get_rosters(league_id: str) -> list:
    return _get(f"league/{league_id}/rosters") or []


def get_league_users(league_id: str) -> list:
    return _get(f"league/{league_id}/users") or []


def get_matchups(league_id: str, week: int) -> list:
    return _get(f"league/{league_id}/matchups/{week}") or []


def get_transactions(league_id: str, week: int) -> list:
    """Waiver / free-agent / trade transactions for a scoring week."""
    return _get(f"league/{league_id}/transactions/{week}") or []


def find_roster_id(league_id: str, user_id: str):
    """Return the roster_id owned by ``user_id`` in this league, or None."""
    for r in get_rosters(league_id):
        if str(r.get("owner_id")) == str(user_id):
            return r.get("roster_id")
    return None


def team_labels(league_id: str) -> dict:
    """Return ``{roster_id: "Team Name (manager)"}`` for a league.

    Roster ids are stringified so they line up with the ``roster_id`` field on
    draft picks (which can come back as strings). This is static during a draft,
    so it only needs to be fetched once.
    """
    users = {str(u.get("user_id")): u for u in get_league_users(league_id)}
    labels = {}
    for r in get_rosters(league_id):
        rid = str(r.get("roster_id"))
        u = users.get(str(r.get("owner_id"))) or {}
        disp = u.get("display_name") or u.get("username") or f"Team {rid}"
        team_name = (u.get("metadata") or {}).get("team_name")
        labels[rid] = f"{team_name} ({disp})" if team_name else disp
    return labels


# ---------------------------------------------------------------------------
# Translating Sleeper league config -> app settings
# ---------------------------------------------------------------------------
# Roster-slot tokens Sleeper uses for a superflex (a.k.a. OP) slot.
_SUPERFLEX_TOKENS = {"SUPER_FLEX", "SUPERFLEX"}
# Tokens Sleeper uses for a standard RB/WR/TE-style flex (we treat them alike).
_FLEX_TOKENS = {"FLEX", "WRRB_FLEX", "REC_FLEX", "WRRB_WRT"}


def parse_scoring(scoring_settings: dict, roster_positions: list) -> str:
    """Map a Sleeper league to one of the app's SCORING_FORMATS keys.

    Superflex / 2-QB rosters use the dedicated ADP dataset regardless of PPR;
    otherwise the reception value picks PPR / Half-PPR / Standard.
    """
    positions = roster_positions or []
    has_superflex = any(p in _SUPERFLEX_TOKENS for p in positions)
    qb_slots = sum(1 for p in positions if p == "QB")
    if has_superflex or qb_slots >= 2:
        return "2-QB / Superflex"
    rec = float((scoring_settings or {}).get("rec", 0) or 0)
    if rec >= 1.0:
        return "PPR"
    if rec > 0:
        return "Half-PPR"
    return "Standard"


def parse_roster(roster_positions: list) -> RosterConfig:
    """Build a RosterConfig by counting Sleeper roster-position tokens.

    IDP slots (DL/LB/DB/IDP_FLEX) and TAXI/reserve are ignored; they don't map
    to this app's skill-position draft model.
    """
    counts = {"qb": 0, "rb": 0, "wr": 0, "te": 0, "flex": 0,
              "superflex": 0, "k": 0, "dst": 0, "bench": 0}
    for tok in roster_positions or []:
        t = str(tok).upper()
        if t == "QB":
            counts["qb"] += 1
        elif t == "RB":
            counts["rb"] += 1
        elif t == "WR":
            counts["wr"] += 1
        elif t == "TE":
            counts["te"] += 1
        elif t in _FLEX_TOKENS:
            counts["flex"] += 1
        elif t in _SUPERFLEX_TOKENS:
            counts["superflex"] += 1
        elif t in ("K", "PK"):
            counts["k"] += 1
        elif t in ("DEF", "DST"):
            counts["dst"] += 1
        elif t == "BN":
            counts["bench"] += 1
        # else: IDP / TAXI / reserve -> ignored
    return RosterConfig(**counts)


def league_to_config(league: dict):
    """Return (scoring_label, teams, RosterConfig) for a league dict."""
    scoring = parse_scoring(league.get("scoring_settings"),
                            league.get("roster_positions"))
    teams = int(league.get("total_rosters")
                or (league.get("settings") or {}).get("num_teams") or 0)
    roster = parse_roster(league.get("roster_positions"))
    return scoring, teams, roster


# ---------------------------------------------------------------------------
# Bridging Sleeper player_ids <-> engine.Player (for live draft sync)
# ---------------------------------------------------------------------------
# Sleeper uses "K"/"DST" where the ADP feed uses "PK"/"DEF".
def _eng_pos(sleeper_pos: str) -> str:
    p = (sleeper_pos or "").upper()
    if p == "K":
        return "PK"
    if p == "DST":
        return "DEF"
    return p


# Normalize team codes that differ between feeds (mostly relocations) so team
# defenses and same-name players line up.
_TEAM_ALIASES = {
    "JAC": "JAX", "JAX": "JAX",
    "WSH": "WAS", "WAS": "WAS",
    "LA": "LAR", "LAR": "LAR", "STL": "LAR",
    "OAK": "LV", "LV": "LV", "LVR": "LV",
    "SD": "LAC", "LAC": "LAC",
    "ARZ": "ARI", "ARI": "ARI",
    "BLT": "BAL", "CLV": "CLE", "HST": "HOU",
}


def _norm_team(team: str) -> str:
    t = (team or "").upper()
    return _TEAM_ALIASES.get(t, t)


def build_id_bridge(players: list, player_map: dict | None = None) -> dict:
    """Return ``{sleeper_player_id: engine.Player}`` for the given players.

    Skill players (QB/RB/WR/TE/K) are matched on normalized name + position,
    disambiguated by team when needed; team defenses match on team code (a
    Sleeper DEF's player_id is the team abbreviation, e.g. "DET"). Matched
    players also get their ``sleeper_id`` attribute set.
    """
    if player_map is None:
        player_map = get_player_map()

    eng_by_key: dict = {}
    eng_def_by_team: dict = {}
    for pl in players:
        if pl.position == "DEF":
            eng_def_by_team[_norm_team(pl.team)] = pl
        eng_by_key.setdefault((norm_name(pl.name), pl.position), []).append(pl)

    bridge: dict = {}
    for sid, info in (player_map or {}).items():
        if not isinstance(info, dict):
            continue
        pos = _eng_pos(info.get("position") or "")
        if pos == "DEF":
            eng = eng_def_by_team.get(_norm_team(info.get("team") or sid))
            if eng:
                bridge[str(sid)] = eng
                eng.sleeper_id = str(sid)
                eng.injury_status = info.get("injury_status") or ""
            continue
        if pos not in ("QB", "RB", "WR", "TE", "PK"):
            continue
        full = info.get("full_name") or " ".join(
            x for x in (info.get("first_name"), info.get("last_name")) if x)
        if not full:
            continue
        cands = eng_by_key.get((norm_name(full), pos))
        if not cands:
            continue
        eng = cands[0]
        if len(cands) > 1:
            steam = _norm_team(info.get("team") or "")
            for c in cands:
                if _norm_team(c.team) == steam:
                    eng = c
                    break
        bridge[str(sid)] = eng
        eng.sleeper_id = str(sid)
        eng.injury_status = info.get("injury_status") or ""
    return bridge


# ---------------------------------------------------------------------------
# Draft-order math (on the clock, picks until your turn) + run detection
# ---------------------------------------------------------------------------
def draft_meta(draft: dict) -> dict:
    """Pull the fields needed for draft-order math out of a draft object."""
    s = (draft or {}).get("settings") or {}
    slot_map = {str(k): v
                for k, v in ((draft or {}).get("slot_to_roster_id") or {}).items()}
    return {
        "teams": int(s.get("teams", 0) or 0),
        "rounds": int(s.get("rounds", 0) or 0),
        "type": (draft or {}).get("type", "snake"),
        "slot_to_roster_id": slot_map,
    }


def my_slot(meta: dict, my_roster_id):
    """Return your draft-board column (1-based) from slot_to_roster_id."""
    for slot, rid in (meta.get("slot_to_roster_id") or {}).items():
        if str(rid) == str(my_roster_id):
            try:
                return int(slot)
            except (TypeError, ValueError):
                return None
    return None


def _slot_for_pick(meta: dict, pick_no: int):
    """Board column (1-based) that owns overall pick_no (1-based)."""
    t = meta.get("teams") or 0
    if not t or pick_no < 1:
        return None
    idx0 = pick_no - 1
    r = idx0 // t          # 0-based round
    i = idx0 % t           # 0-based position in round
    if meta.get("type") == "linear" or r % 2 == 0:
        return i + 1
    return t - i           # snake reverses on odd rounds


def slot_on_clock(meta: dict, picks_made: int):
    """Board column currently on the clock, given how many picks are done."""
    return _slot_for_pick(meta, picks_made + 1)


def picks_until_my_turn(meta: dict, picks_made: int, my_roster_id):
    """Picks until you're up (0 = on the clock now). None if unknown/done."""
    t = meta.get("teams") or 0
    s = my_slot(meta, my_roster_id)
    if not t or not s:
        return None
    rounds = meta.get("rounds") or 0
    next_pick = picks_made + 1
    last = t * rounds if rounds else next_pick + t * 40
    for pk in range(next_pick, last + 1):
        if _slot_for_pick(meta, pk) == s:
            return pk - next_pick
    return None


def positional_runs(picks: list, window: int = 6, threshold: int = 3) -> list:
    """Positions that make up >= threshold of the last `window` picks."""
    recent = sorted(picks or [], key=lambda p: p.get("pick_no") or 0)[-window:]
    from collections import Counter
    c = Counter()
    for pk in recent:
        pos = ((pk.get("metadata") or {}).get("position") or "").upper()
        pos = "K" if pos == "PK" else pos
        if pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
            c[pos] += 1
    runs = [(pos, n) for pos, n in c.items() if n >= threshold]
    runs.sort(key=lambda x: -x[1])
    return runs


def map_picks(picks: list, bridge: dict, my_roster_id=None, my_user_id=None):
    """Translate Sleeper draft picks into ``{engine_player_id: "me"|"other"}``.

    A pick is "me" when its roster_id matches your roster (preferred, since
    ``picked_by`` can be empty), falling back to ``picked_by`` == your user_id.
    Returns ``(mapping, unmatched_count)`` where unmatched counts picks whose
    Sleeper player_id has no ADP-pool counterpart (usually deep bench players).
    """
    mapping: dict = {}
    unmatched = 0
    for pk in picks or []:
        sid = str(pk.get("player_id") or "")
        eng = bridge.get(sid)
        if not eng:
            unmatched += 1
            continue
        mine = False
        rid = pk.get("roster_id")
        if my_roster_id is not None and rid is not None \
                and str(rid) == str(my_roster_id):
            mine = True
        elif my_user_id and str(pk.get("picked_by") or "") == str(my_user_id):
            mine = True
        mapping[eng.player_id] = "me" if mine else "other"
    return mapping, unmatched


# ---------------------------------------------------------------------------
# CLI sanity check
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import season

    uname = sys.argv[1] if len(sys.argv) > 1 else "coleaclair"
    user = resolve_user(uname)
    print(f"User: {user.get('display_name')} (user_id {user.get('user_id')})")

    st = season.get_state()
    seas = st.get("season") or st.get("league_season") or "2026"
    leagues = list_leagues(user["user_id"], seas)
    print(f"\n{len(leagues)} NFL league(s) for {seas}:")
    for lg in leagues:
        scoring, teams, roster = league_to_config(lg)
        rid = find_roster_id(lg["league_id"], user["user_id"])
        print(f"\n  {lg['name']}  [{lg['status']}]")
        print(f"    league_id={lg['league_id']}  draft_id={lg['draft_id']}")
        print(f"    scoring={scoring}  teams={teams}  your roster_id={rid}")
        print(f"    roster: {roster.qb}QB {roster.rb}RB {roster.wr}WR "
              f"{roster.te}TE {roster.flex}FLEX {roster.superflex}SF "
              f"{roster.k}K {roster.dst}DEF {roster.bench}BN "
              f"({roster.total_roster} total)")
        if lg.get("draft_id"):
            d = get_draft(lg["draft_id"])
            ds = d.get("settings") or {}
            print(f"    draft: type={d.get('status')}/{d.get('type')} "
                  f"rounds={ds.get('rounds')} teams={ds.get('teams')}")
