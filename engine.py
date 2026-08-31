"""
Draft engine: data fetching, caching, and the recommendation logic.

This module has NO GUI dependencies so it can be tested headlessly and reused.

Data source: Fantasy Football Calculator public ADP API.
  https://fantasyfootballcalculator.com/api/v1/adp/<format>?teams=<n>
ADP = Average Draft Position, aggregated from thousands of recent real drafts.
It is the single most reliable "who is worth what" signal available for free.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# Use the OS certificate store so HTTPS works behind corporate proxies/MITM.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

import requests


API_BASE = "https://fantasyfootballcalculator.com/api/v1/adp"

# Scoring formats the API understands -> friendly labels for the UI.
SCORING_FORMATS = {
    "PPR": "ppr",
    "Half-PPR": "half-ppr",
    "Standard": "standard",
    "2-QB / Superflex": "2qb",
}

# Positions we care about, in display order. The API uses "PK" for kicker.
POSITIONS = ["QB", "RB", "WR", "TE", "PK", "DEF"]
POSITION_LABELS = {"PK": "K"}  # what to show the user


def pos_label(pos: str) -> str:
    return POSITION_LABELS.get(pos, pos)


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


# ---------------------------------------------------------------------------
# Roster configuration
# ---------------------------------------------------------------------------

@dataclass
class RosterConfig:
    """Starting-lineup requirements for one team (standard 12-team defaults)."""

    qb: int = 1
    rb: int = 2
    wr: int = 2
    te: int = 1
    flex: int = 1            # RB/WR/TE
    superflex: int = 0       # QB/RB/WR/TE (a.k.a. OP / superflex)
    k: int = 1
    dst: int = 1
    bench: int = 6

    @property
    def starters(self) -> dict:
        return {"QB": self.qb, "RB": self.rb, "WR": self.wr,
                "TE": self.te, "PK": self.k, "DEF": self.dst}

    @property
    def starter_slots(self) -> int:
        return (self.qb + self.rb + self.wr + self.te + self.flex
                + self.superflex + self.k + self.dst)

    @property
    def total_roster(self) -> int:
        return self.starter_slots + self.bench


FLEX_POSITIONS = {"RB", "WR", "TE"}
SUPERFLEX_POSITIONS = {"QB", "RB", "WR", "TE"}


# ---------------------------------------------------------------------------
# Player model
# ---------------------------------------------------------------------------

@dataclass
class Player:
    player_id: int
    name: str
    position: str
    team: str
    adp: float
    bye: int = 0
    stdev: float = 0.0
    times_drafted: int = 0
    high: int = 0
    low: int = 0
    # runtime state
    drafted_by: str = ""   # "" = available, "me" = my team, "other" = someone else
    sleeper_id: str = ""   # Sleeper player_id, filled in when matched for draft sync
    injury_status: str = ""  # Sleeper injury tag (Questionable/Out/IR/...), if any
    # computed
    vorp: float = 0.0
    pos_rank: int = 0
    risk: float = 0.0        # relative volatility (~1.0 = typical for this ADP)
    upside: float = 0.0      # asymmetric-upside score (higher = bigger sleeper)
    risk_label: str = ""     # Safe / Volatile / Boom-Bust / Upside / Bust risk / thin

    @property
    def available(self) -> bool:
        return self.drafted_by == ""

    @property
    def display_pos(self) -> str:
        return pos_label(self.position)


# ---------------------------------------------------------------------------
# Data fetching + caching
# ---------------------------------------------------------------------------

def _cache_path(scoring: str, teams: int) -> str:
    return os.path.join(CACHE_DIR, f"adp_{scoring}_{teams}.json")


def fetch_adp(scoring: str = "ppr", teams: int = 12, timeout: int = 20) -> dict:
    """Fetch the latest ADP. No year param => most recent data available."""
    url = f"{API_BASE}/{scoring}"
    resp = requests.get(url, params={"teams": teams}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "Success" or not isinstance(data.get("players"), list):
        raise RuntimeError(f"ADP API returned no data: {data.get('errors', data)}")
    return data


def load_players(scoring: str = "ppr", teams: int = 12,
                 force_refresh: bool = False, max_age_hours: float = 12.0):
    """
    Return (players, meta, source) where source is "network" or "cache".
    Tries the network first (unless a fresh cache exists); falls back to cache
    on failure so the app still works offline.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(scoring, teams)

    cache = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = None

    cache_fresh = False
    if cache and not force_refresh:
        age = time.time() - cache.get("_fetched_at", 0)
        cache_fresh = age < max_age_hours * 3600

    if cache_fresh:
        return _players_from_raw(cache["players"]), cache.get("meta", {}), "cache"

    # Try network.
    try:
        data = fetch_adp(scoring, teams)
        data["_fetched_at"] = time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return _players_from_raw(data["players"]), data.get("meta", {}), "network"
    except Exception as net_err:
        if cache:
            return (_players_from_raw(cache["players"]),
                    cache.get("meta", {}), "cache")
        raise net_err


def _players_from_raw(raw: list) -> list:
    players = []
    for p in raw:
        try:
            players.append(Player(
                player_id=int(p["player_id"]),
                name=p["name"],
                position=p["position"],
                team=p.get("team", "FA") or "FA",
                adp=float(p["adp"]),
                bye=int(p.get("bye") or 0),
                stdev=float(p.get("stdev") or 0.0),
                times_drafted=int(p.get("times_drafted") or 0),
                high=int(p.get("high") or 0),
                low=int(p.get("low") or 0),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    players.sort(key=lambda x: x.adp)
    # assign positional ranks (1 = best at position)
    counters: dict = {}
    for pl in players:
        counters[pl.position] = counters.get(pl.position, 0) + 1
        pl.pos_rank = counters[pl.position]
    return players


def cache_age_string(meta: dict, source: str) -> str:
    parts = []
    if meta:
        if meta.get("type"):
            parts.append(str(meta["type"]))
        if meta.get("total_drafts"):
            parts.append(f"{meta['total_drafts']:,} drafts")
        if meta.get("start_date") and meta.get("end_date"):
            parts.append(f"{meta['start_date']} - {meta['end_date']}")
    label = "live" if source == "network" else "cached"
    return f"[{label}] " + "  |  ".join(parts) if parts else f"[{label}]"


# ---------------------------------------------------------------------------
# Draft state + recommendation engine
# ---------------------------------------------------------------------------

class Draft:
    def __init__(self, players: list, roster: RosterConfig, teams: int = 12):
        self.players = players
        self.by_id = {p.player_id: p for p in players}
        self.roster = roster
        self.teams = teams
        self.history: list = []  # list of (player_id, previous_state)
        self._compute_vorp()
        self._compute_risk()

    # ---- replacement-level value (VORP, computed in ADP space) -------------
    def _replacement_ranks(self) -> dict:
        """How deep into each position the league drafts before 'replacement'."""
        t = self.teams
        s = self.roster.starters
        # Split flex / superflex demand across eligible positions by typical
        # real-world usage. Superflex is mostly used on a 2nd QB.
        flex_total = t * self.roster.flex
        sflex_total = t * self.roster.superflex
        flex_split = {"RB": 0.45, "WR": 0.45, "TE": 0.10}
        sflex_split = {"QB": 0.70, "RB": 0.10, "WR": 0.15, "TE": 0.05}
        ranks = {}
        for pos in POSITIONS:
            base = t * s.get(pos, 0)
            extra = (flex_total * flex_split.get(pos, 0.0)
                     + sflex_total * sflex_split.get(pos, 0.0))
            ranks[pos] = max(1, round(base + extra))
        return ranks

    def _compute_vorp(self):
        """VORP = replacement-player ADP minus this player's ADP (ADP space).

        Higher is better. This rewards players who are far better than the
        guy you'd otherwise be forced to settle for at the same position,
        which naturally captures positional scarcity.
        """
        repl_ranks = self._replacement_ranks()
        by_pos: dict = {p: [] for p in POSITIONS}
        for pl in self.players:
            if pl.position in by_pos:
                by_pos[pl.position].append(pl)
        for pos, plist in by_pos.items():
            plist.sort(key=lambda x: x.adp)
            rank = repl_ranks.get(pos, len(plist))
            coeff = self._pos_value_coeff(pos)
            if plist:
                idx = min(rank, len(plist)) - 1
                baseline_adp = plist[idx].adp
                # if our baseline rank is deeper than the pool, penalize.
                if rank > len(plist):
                    baseline_adp = plist[-1].adp + (rank - len(plist)) * 3
                for pl in plist:
                    pl.vorp = round((baseline_adp - pl.adp) * coeff, 1)

    # ---- risk / upside (from ADP dispersion) -------------------------------
    def _compute_risk(self):
        """Rate each player's boom-bust profile from ADP dispersion.

        The ADP API reports how much drafters DISAGREE about a player via
        stdev (spread of draft slots), high (earliest pick anyone spent) and
        low (latest slot anyone waited). A boom-bust / high-upside pick is one
        the crowd disagrees about AND where the believers reach well above his
        ADP -- exactly the "could pay off big or bust" profile.

        We normalize each player's stdev against a local baseline (the median
        stdev of nearby-ADP players), because raw stdev naturally grows the
        later a player goes. risk ~1.0 means typical volatility for that draft
        range; >1 means unusually polarizing.
        """
        MIN_SAMPLE = 15          # below this, dispersion numbers are noise
        WINDOW = 20              # ADP-neighbors used for the local baseline

        ranked = sorted(self.players, key=lambda x: x.adp)
        n = len(ranked)
        stdevs = [p.stdev for p in ranked]

        def median(vals: list) -> float:
            s = sorted(v for v in vals if v > 0)
            if not s:
                return 0.0
            m = len(s) // 2
            return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0

        for i, p in enumerate(ranked):
            lo = max(0, i - WINDOW)
            hi = min(n, i + WINDOW + 1)
            baseline = median(stdevs[lo:hi])

            if p.times_drafted < MIN_SAMPLE or baseline <= 0 or p.stdev <= 0:
                p.risk = 0.0
                p.upside = 0.0
                p.risk_label = "thin" if p.times_drafted < MIN_SAMPLE else ""
                continue

            p.risk = round(p.stdev / baseline, 2)

            # how far the optimists reach up vs. how far the doubters fade him
            upside_gap = max(0.0, p.adp - p.high) if p.high else 0.0
            downside_gap = max(0.0, p.low - p.adp) if p.low else 0.0
            skew = upside_gap - downside_gap
            p.upside = round(max(0.0, skew) * p.risk, 1)

            # label: volatility drives the tier, skew picks the direction
            if p.risk < 0.8:
                p.risk_label = "Safe"
            elif p.risk <= 1.25:
                p.risk_label = "Upside" if skew > 3 else "Steady"
            elif p.risk <= 1.6:
                p.risk_label = ("Upside" if skew > 3
                                else "Bust risk" if skew < -3 else "Volatile")
            else:
                p.risk_label = ("Boom-Bust" if abs(skew) <= 3
                                else "Upside" if skew > 0 else "Bust risk")

    # ---- draft actions -----------------------------------------------------
    def mark(self, player_id: int, who: str):
        pl = self.by_id.get(player_id)
        if not pl:
            return
        self.history.append((player_id, pl.drafted_by))
        pl.drafted_by = who

    def undo(self):
        if not self.history:
            return None
        player_id, prev = self.history.pop()
        pl = self.by_id.get(player_id)
        if pl:
            pl.drafted_by = prev
        return pl

    def reset(self):
        for pl in self.players:
            pl.drafted_by = ""
        self.history.clear()

    def apply_external_picks(self, mapping: dict) -> int:
        """Apply picks from an external source (e.g. a live Sleeper draft).

        ``mapping`` is ``{player_id: "me"|"other"}``. Only players that are
        currently available get marked, so this is idempotent and never
        clobbers picks you made by hand or already synced. Each change is
        pushed onto the undo history. Returns the number of newly marked
        players.
        """
        changed = 0
        for pid, who in mapping.items():
            if who not in ("me", "other"):
                continue
            pl = self.by_id.get(pid)
            if pl and pl.drafted_by == "":
                self.history.append((pid, pl.drafted_by))
                pl.drafted_by = who
                changed += 1
        return changed

    # ---- roster introspection ---------------------------------------------
    def my_players(self) -> list:
        return [p for p in self.players if p.drafted_by == "me"]

    def my_position_counts(self) -> dict:
        counts = {p: 0 for p in POSITIONS}
        for p in self.my_players():
            if p.position in counts:
                counts[p.position] += 1
        return counts

    def roster_summary(self) -> list:
        """Return list of (slot_label, player_or_None, kind) for display.

        kind is "starter" or "bench". Empty starter and bench slots are
        included so the whole team is visible at a glance.
        """
        mine = sorted(self.my_players(), key=lambda x: x.adp)
        used = set()
        rows = []

        def take(pos_filter):
            for p in mine:
                if p.player_id in used:
                    continue
                if pos_filter(p):
                    used.add(p.player_id)
                    return p
            return None

        r = self.roster
        slot_defs = (
            [("QB", lambda p: p.position == "QB")] * r.qb
            + [("RB", lambda p: p.position == "RB")] * r.rb
            + [("WR", lambda p: p.position == "WR")] * r.wr
            + [("TE", lambda p: p.position == "TE")] * r.te
            + [("FLEX", lambda p: p.position in FLEX_POSITIONS)] * r.flex
            + [("SFLX", lambda p: p.position in SUPERFLEX_POSITIONS)] * r.superflex
            + [("K", lambda p: p.position == "PK")] * r.k
            + [("DEF", lambda p: p.position == "DEF")] * r.dst
        )
        for label, f in slot_defs:
            rows.append((label, take(f), "starter"))

        # bench: leftover players first, then empty slots up to bench size
        leftovers = [p for p in mine if p.player_id not in used]
        n_bench = max(r.bench, len(leftovers))
        for i in range(n_bench):
            p = leftovers[i] if i < len(leftovers) else None
            rows.append((f"BN{i + 1}", p, "bench"))
        return rows

    def needs_summary(self) -> list:
        """Unfilled starter slots, aggregated, e.g. ['2xRB', 'TE', 'K']."""
        counts = {}
        order = []
        for label, p, kind in self.roster_summary():
            if kind == "starter" and p is None:
                if label not in counts:
                    order.append(label)
                counts[label] = counts.get(label, 0) + 1
        out = []
        for label in order:
            n = counts[label]
            out.append(f"{n}\u00d7{label}" if n > 1 else label)
        return out

    def bye_conflicts(self) -> list:
        """Bye weeks where I have multiple players (skill positions).

        Returns a list of (bye_week, [names]) sorted with the biggest
        conflicts first. Flags weeks with 2+ players sharing a position or
        3+ of my players total, since those create real lineup holes.
        """
        from collections import defaultdict
        by_bye = defaultdict(list)
        for p in self.my_players():
            if p.bye and p.position not in ("PK", "DEF"):
                by_bye[p.bye].append(p)
        out = []
        for bye, players in by_bye.items():
            pos_counts = {}
            for p in players:
                pos_counts[p.position] = pos_counts.get(p.position, 0) + 1
            same_pos = any(c >= 2 for c in pos_counts.values())
            if same_pos or len(players) >= 3:
                names = [p.name for p in sorted(players, key=lambda x: x.adp)]
                out.append((bye, names))
        out.sort(key=lambda x: len(x[1]), reverse=True)
        return out

    def _pos_value_coeff(self, pos: str) -> float:
        """Down-weight positions whose raw ADP value overstates real worth.

        ADP-space value (VORP) badly overrates QB in 1-QB leagues: only ~12
        QBs start league-wide, so 'replacement' is a very late pick, making
        elite QBs look enormously valuable -- but their actual point edge over
        a streamer is small and the pool is deep. So we discount QB heavily in
        single-QB formats (and flip it back to full value for superflex/2-QB,
        where a 2nd startable QB really is scarce). TE is mildly discounted for
        the same reason; RB/WR/K/DEF are left alone.
        """
        if pos == "QB":
            return 1.0 if self.roster.superflex > 0 else 0.5
        if pos == "TE":
            return 0.85
        return 1.0

    def _need_multiplier(self, pos: str) -> float:
        """How badly do I need this position right now? 0..1.2"""
        counts = self.my_position_counts()
        starters = self.roster.starters
        have = counts.get(pos, 0)
        need_start = starters.get(pos, 0)

        # Kicker / defense: don't recommend until roster is nearly full.
        if pos in ("PK", "DEF"):
            filled = len(self.my_players())
            spots_left = self.roster.total_roster - filled
            if have >= starters.get(pos, 1):
                return 0.05
            return 1.0 if spots_left <= 3 else 0.08

        if have < need_start:
            return 1.15  # still need a starter here -> highest priority

        # starter filled; do flex / superflex slots still want this position?
        def surplus(group):
            return sum(max(0, counts.get(p, 0) - starters.get(p, 0))
                       for p in group)

        if pos in FLEX_POSITIONS and surplus(FLEX_POSITIONS) < self.roster.flex:
            return 0.85
        if pos in SUPERFLEX_POSITIONS and self.roster.superflex > 0:
            # superflex can also be filled by leftover flex-eligible players
            open_flex_sflex = (self.roster.flex + self.roster.superflex)
            if surplus(SUPERFLEX_POSITIONS) < open_flex_sflex:
                return 0.8
        return 0.45 if pos in FLEX_POSITIONS else 0.4

    def position_cliff(self, pos: str) -> float:
        """ADP gap between best and 2nd-best available at a position.

        A large gap means there is a steep talent drop-off after the top guy.
        """
        avail = sorted((p for p in self.players
                        if p.available and p.position == pos),
                       key=lambda x: x.adp)
        if len(avail) >= 2:
            return round(avail[1].adp - avail[0].adp, 1)
        return 0.0

    # ---- the actual recommendations ---------------------------------------
    def best_available(self, pos: str = None, n: int = 5) -> list:
        avail = [p for p in self.players if p.available]
        if pos:
            avail = [p for p in avail if p.position == pos]
        avail.sort(key=lambda x: x.adp)
        return avail[:n]

    def top_by_position(self, n: int = 5) -> dict:
        return {pos: self.best_available(pos, n) for pos in POSITIONS}

    def recommendations(self, n: int = 5, show_upside: bool = False) -> list:
        """
        Smart 'who should I draft right now' list.

        ADP is the backbone: it is the consensus of thousands of real drafters
        and already prices in positional scarcity, injuries, hype, etc. So we
        rank primarily by ADP value, then ADJUST for:
          * roster need  -> prioritize positions you still must start
          * a small, capped scarcity nudge -> break ties toward tier cliffs
        This avoids the classic VBD trap of recommending a TE/QB run far too
        early.

        When ``show_upside`` is True, a small capped bonus is added for
        high-upside boom-bust players so those sleepers can crack the list;
        with it False the scoring is identical to the pure-ADP behavior.

        Returns list of (player, score, reason).
        """
        # best (lowest) ADP available, used to normalize "value"
        avail = [p for p in self.players if p.available]
        if not avail:
            return []
        scored = []
        for p in avail:
            need = self._need_multiplier(p.position)
            # positional value: discounts deep/streamable positions (QB in
            # 1-QB leagues, TE) so we don't reach for them early.
            coeff = self._pos_value_coeff(p.position)
            # ADP value: lower ADP = higher value, on a positive scale.
            base_value = max(0.0, 220.0 - p.adp)
            score = base_value * need * coeff

            # Small scarcity nudge, only for the best available at a position,
            # and capped so it can never leapfrog a much earlier-ADP player.
            nudge = 0.0
            best_at = self.best_available(p.position, 1)
            if best_at and best_at[0].player_id == p.player_id:
                cliff = self.position_cliff(p.position)
                nudge = min(cliff, 25.0) * 0.5 * need * coeff
            score += nudge

            # Optional upside tilt: let the best sleepers surface, capped so
            # they can never leapfrog a much better-value pick outright.
            if show_upside:
                score += min(p.upside, 40.0) * 0.5 * need

            scored.append((p, score, need))

        scored.sort(key=lambda x: x[1], reverse=True)
        out = []
        for p, score, need in scored[:n]:
            out.append((p, round(score, 1), self._reason(p, need, show_upside)))
        return out

    def _reason(self, p: Player, need: float, show_upside: bool = False) -> str:
        bits = []
        if need >= 1.1:
            bits.append(f"fills {pos_label(p.position)} starter need")
        elif need >= 0.8:
            bits.append("flex/depth fit")
        elif need <= 0.1 and p.position in ("PK", "DEF"):
            bits.append("late-round only")

        if p.vorp >= 30:
            bits.append(f"elite value (VORP {p.vorp:g})")
        elif p.vorp >= 10:
            bits.append(f"good value (VORP {p.vorp:g})")

        cliff = self.position_cliff(p.position)
        # only flag the cliff for the actual best-available at the position
        best_at = self.best_available(p.position, 1)
        if best_at and best_at[0].player_id == p.player_id and cliff >= 12:
            bits.append(f"last of tier ({pos_label(p.position)} drops ~{cliff:g} ADP after)")

        if show_upside:
            if p.risk_label == "Upside":
                bits.append("high upside (could pay off big)")
            elif p.risk_label == "Boom-Bust":
                bits.append("boom-bust gamble")
            elif p.risk_label == "Bust risk":
                bits.append("high bust risk")

        if not bits:
            bits.append(f"best {pos_label(p.position)} available")
        return ", ".join(bits)

    # ---- save / load ------------------------------------------------------
    def state(self) -> dict:
        return {
            "teams": self.teams,
            "roster": asdict(self.roster),
            "picks": [(p.player_id, p.drafted_by)
                      for p in self.players if p.drafted_by],
        }

    def apply_state(self, state: dict):
        self.reset()
        for pid, who in state.get("picks", []):
            pl = self.by_id.get(pid)
            if pl:
                pl.drafted_by = who


if __name__ == "__main__":
    # quick manual sanity check
    players, meta, source = load_players("ppr", 12, force_refresh=True)
    print("source:", source, "| players:", len(players))
    print(cache_age_string(meta, source))
    d = Draft(players, RosterConfig(), teams=12)
    print("\nTop recommendations to open the draft:")
    for p, score, reason in d.recommendations(5):
        print(f"  {p.name:22s} {p.display_pos:3s} ADP {p.adp:5.1f} "
              f"VORP {p.vorp:5.1f}  score {score:6.1f}  -> {reason}")
