# FF Draft Assistant

A no-football-knowledge-required desktop app that tells you **who to draft** in your
fantasy football draft. You log picks as they happen in your draft room, and it
shows you the best players to take right now.

It's built to fix the classic "the AI told me to draft a kicker in round 3"
problem: recommendations are anchored to real-world **ADP** (Average Draft
Position) and adjusted for your roster needs.

## What it does

- Pulls **live ADP data** aggregated from thousands of recent real drafts
  (auto-refreshes, and caches locally so it still works if your wifi drops).
- Matches **your league's rules**: scoring (PPR, Half-PPR, Standard,
  2-QB/Superflex), league size (8-16 teams), and a **fully customizable roster**
  (QB/RB/WR/TE/FLEX/SUPERFLEX/K/DEF/Bench counts). All of this feeds directly
  into the recommendations.
- As the draft happens you log every pick:
  - **double-click a player** = "drafted by another team"
  - **Draft to MY team** = adds him to your roster
- It always shows:
  - **Draft These Now** - the top 5 players you should take, with a reason
    (fills a starter need, elite value, last of a talent tier, etc.).
  - **Best Available by Position** - top 5 at QB/RB/WR/TE/K/DEF, with a warning
    when there's a steep talent cliff coming.
  - **My Roster** - your starters + bench as they fill in, with remaining
    needs and bye-week stack warnings.
- **Undo**, **Reset**, **Save/Load**, and auto-save (it restores your draft if
  you accidentally close the app).

## Season tab

Once you've drafted, the **Season** tab helps you manage your team all year. It
pulls from free public sources (ESPN + Sleeper, no API keys) and caches locally:

- **NFL News** - latest headlines (double-click to open in your browser).
- **Injury Report** - league-wide injuries (status, type, return date, note).
  Your own players are starred and highlighted; filter to skill positions or
  to just your roster.
- **Waiver Pickups** - the most-added players across fantasy leagues (a great
  signal for injury-driven handcuffs and breakouts); players that fill one of
  your roster needs are starred.
- **Streaming Matchups** - best **defenses** and **kickers** to stream this
  week, derived from Vegas odds (implied team totals): DEFs facing the
  lowest-scoring offenses, Ks on the highest-scoring teams.

(It's most useful in-season; during the offseason the news/injuries/odds still
load, but trending data is quiet.)

## Setup (one time)

You need Python 3.10+ (you have 3.12). From this folder:

```powershell
python -m pip install -r requirements.txt
```

## Run it

Easiest: double-click the **FF Draft Assistant** shortcut (the football icon) on
your Desktop, or the one in this folder. It launches the app with no console
window and auto-installs dependencies the first time.

Or from a terminal:

```powershell
python ffdraft.py
```

(The shortcut points at `run.bat`. If you move this folder, just re-run
`python make_icon.py` is not needed, but recreate the shortcut by pointing it at
the new `run.bat` location, or ask me to regenerate it.)

## Set up your league rules (do this first)

Click **League Settings** (top bar) to match your league exactly:

- **Scoring** - PPR, Half-PPR, Standard, or 2-QB/Superflex. This changes which
  ADP dataset is pulled, so the rankings reflect how your format actually drafts
  (e.g. in 2-QB, quarterbacks correctly go in the first round).
- **Teams** - 8 to 16. Affects how scarce each position is.
- **Starting lineup & bench** - set how many QB/RB/WR/TE/FLEX/SUPERFLEX/K/DEF
  and bench spots you start. The recommendation engine uses this to know what
  you still *need* and how deep the league drafts each position.
- **Presets** - one-click setups for Standard, Superflex/2-QB, 3-WR PPR, and
  No-K/DEF leagues.
- **Theme** - pick from a set of polished looks (changes instantly): dark
  themes (Midnight, Obsidian, Emerald Night, Plum Dusk) and light themes
  (Daylight, Parchment, Mint Fresh).

Your settings are saved automatically and restored next time you open the app.
The active lineup is always shown in the top-right.

## How to use it during your draft

1. Confirm **League Settings** match your league (ask your commissioner; most
   office leagues are 12-team PPR or Half-PPR with a standard lineup).
2. When it's someone else's pick, find the player they took in the big list and
   **double-click** him. He turns gray and drops out of the recommendations.
3. When it's **your** pick, look at **Draft These Now**, pick a guy, and either
   select him in the big list and click **Draft to MY team**, or just
   **double-click him in the recommendations panel**.
4. Repeat. Your roster fills in on the bottom right, and the advice adapts to
   what you still need.

Tip: don't draft a Kicker (K) or Defense (DEF) until the very last couple of
rounds. The app already knows this and won't recommend them early.

## How the recommendation logic works

ADP is the consensus of thousands of drafters, so it already prices in talent,
injuries, hype, and positional scarcity. The app:

1. Ranks available players primarily by **ADP value** (lower ADP = better).
2. Multiplies by a **roster-need factor** derived from *your* configured lineup -
   positions where you still need a starter get prioritized; once a position is
   filled it's only valued for your FLEX/SUPERFLEX/bench depth; and K/DEF are
   suppressed until your roster is nearly full.
3. Adds a small, capped **scarcity nudge** when a position is about to fall off
   a talent cliff, so close calls break toward the scarcer position.

The **Value** column is VORP (value over replacement) shown for context - how
much better a player is than the guy you'd be stuck with at his position later.

## Files

- `ffdraft.py` - the desktop app (GUI).
- `engine.py` - draft data fetching, caching, and the recommendation logic (no
  GUI, so it can be tested on its own: `python engine.py`).
- `season.py` - season-tab data (news/injuries/pickups/streaming); also runs
  standalone: `python season.py`.
- `run.bat` - double-click launcher (no console window).
- `football.ico` / `make_icon.py` - the app icon and the script that made it.
- `FF Draft Assistant.lnk` - shortcut with the football icon (also on Desktop).
- `cache/` - cached ADP data (auto-created).
- `draft_autosave.json` - your in-progress draft (auto-created).
- `league_settings.json` - your saved scoring/teams/roster/theme (auto-created).

## Data sources

All free, public, and key-less:

- **Draft (ADP):** [Fantasy Football Calculator](https://fantasyfootballcalculator.com/adp)
- **Season news / injuries / matchups + odds:** ESPN public NFL endpoints
- **Waiver trends / player database / season state:** [Sleeper API](https://docs.sleeper.com/)
