"""
FF Draft Assistant - a desktop app that tells you who to draft.

Run it with:   python ffdraft.py

What it does
------------
* Pulls LIVE Average Draft Position (ADP) data from thousands of recent real
  fantasy drafts (auto-refreshes; falls back to a local cache when offline).
* You log every pick as it happens in your draft room:
    - double-click a player  -> "drafted by another team" (the common case)
    - "Draft to MY team"      -> adds him to your roster
* It always shows you:
    - the top 5 players you should take RIGHT NOW (roster-aware, scarcity-aware)
    - the best 5 available at every position
    - your current roster (starters + bench, needs, bye stacks)
* A "Season" tab adds in-season tools: NFL news, the injury report (your
  players highlighted), trending waiver pickups, and best K/DEF streaming
  matchups derived from Vegas odds. See season.py for the data layer.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

import dataclasses

import engine
from engine import (
    Draft,
    RosterConfig,
    SCORING_FORMATS,
    POSITIONS,
    pos_label,
    load_players,
    cache_age_string,
)
import season
import sleeper

APP_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOSAVE = os.path.join(APP_DIR, "draft_autosave.json")
LEAGUE_FILE = os.path.join(APP_DIR, "league_settings.json")

# Editable roster slots: (attr, label, tooltip)
ROSTER_SLOTS = [
    ("qb", "QB", "Quarterbacks"),
    ("rb", "RB", "Running backs"),
    ("wr", "WR", "Wide receivers"),
    ("te", "TE", "Tight ends"),
    ("flex", "FLEX", "RB / WR / TE"),
    ("superflex", "SUPERFLEX", "QB / RB / WR / TE (a.k.a. OP)"),
    ("k", "K", "Kicker"),
    ("dst", "DEF", "Team defense"),
    ("bench", "BENCH", "Bench spots"),
]

# Quick presets for common league types.
ROSTER_PRESETS = {
    "Standard (1 QB)": dict(qb=1, rb=2, wr=2, te=1, flex=1, superflex=0,
                            k=1, dst=1, bench=6),
    "Superflex / 2-QB": dict(qb=1, rb=2, wr=2, te=1, flex=1, superflex=1,
                             k=1, dst=1, bench=6),
    "3-WR PPR": dict(qb=1, rb=2, wr=3, te=1, flex=1, superflex=0,
                     k=1, dst=1, bench=6),
    "No K / DEF": dict(qb=1, rb=2, wr=2, te=1, flex=2, superflex=0,
                       k=0, dst=0, bench=7),
}

TEAM_OPTIONS = [8, 10, 12, 14, 16]
POS_FILTERS = ["ALL"] + [pos_label(p) for p in POSITIONS]

# Short codes for Sleeper injury statuses, shown as badges on the board.
_INJURY_CODES = {
    "Questionable": "Q", "Doubtful": "D", "Out": "O",
    "Injured Reserve": "IR", "IR": "IR", "PUP": "PUP",
    "Sus": "SUS", "Suspended": "SUS", "COV": "COV", "DNR": "DNR", "NA": "NA",
}


def injury_code(status: str) -> str:
    """Compact badge for an injury status, e.g. 'Questionable' -> 'Q'."""
    if not status:
        return ""
    return _INJURY_CODES.get(status, str(status)[:3].upper())

# ---------------------------------------------------------------------------
# Theming
# ---------------------------------------------------------------------------
# Position color palettes. Bright tones read well on dark backgrounds; the
# light variant uses deeper, more saturated tones so names stay legible on
# pale table rows.
DARK_POS = {
    "QB": "#ff7a93", "RB": "#8fe05a", "WR": "#79b2ff",
    "TE": "#f2bd5c", "K": "#c5a6ff", "DEF": "#6fdcff",
}
LIGHT_POS = {
    "QB": "#d11f54", "RB": "#1f8f3e", "WR": "#1f6fe0",
    "TE": "#b9760a", "K": "#7b3ff2", "DEF": "#0b86a8",
}


def _theme(bg, panel, card, stripe, head, border, accent, accent2, gold, sel,
           text, muted, warn, pos, mine=None):
    return dict(bg=bg, panel=panel, card=card, stripe=stripe, head=head,
                border=border, accent=accent, accent2=accent2, gold=gold,
                sel=sel, text=text, muted=muted, mine=mine or accent, warn=warn,
                pos=pos)


# A curated set of visually appealing themes (both dark and light). No team
# colors -- just clean, cohesive palettes.
THEMES = {
    # ---- dark ----
    "Midnight":     _theme("#0d1322", "#141b2e", "#1a2238", "#212c46",
                           "#0a0f1c", "#283353", "#5b8cff", "#37d4a7",
                           "#f2c14e", "#2c4a86",
                           text="#e9eefb", muted="#8593b3", warn="#f0a73a",
                           pos=DARK_POS),
    "Obsidian":     _theme("#101012", "#17181b", "#1e2024", "#26282e",
                           "#0c0c0e", "#33363d", "#ff6b3d", "#4fd1c5",
                           "#ffc857", "#3a3d45",
                           text="#ecedf0", muted="#8d9099", warn="#ff8a4d",
                           pos=DARK_POS),
    "Emerald Night":_theme("#0a1612", "#0f1f19", "#142a22", "#1b362b",
                           "#07110d", "#214a3b", "#2ee6a0", "#5ad1ff",
                           "#ffd166", "#1c5a45",
                           text="#e6f5ee", muted="#7fa595", warn="#f4b24a",
                           pos=DARK_POS),
    "Plum Dusk":    _theme("#140e1f", "#1d1430", "#261a3f", "#30224e",
                           "#0e0917", "#3d2c5e", "#c77dff", "#5ce1e6",
                           "#ffd24d", "#42306b",
                           text="#efe8fb", muted="#9c8fc0", warn="#f2a64a",
                           pos=DARK_POS),
    # ---- light ----
    "Daylight":     _theme("#eef1f6", "#f7f9fc", "#ffffff", "#eef2f8",
                           "#e4e9f1", "#d4dbe6", "#2f6bed", "#0ea5a5",
                           "#e0a800", "#cfe0fb",
                           text="#1c2535", muted="#6b7689", warn="#c2700a",
                           pos=LIGHT_POS),
    "Parchment":    _theme("#efe6d4", "#f7f0e2", "#fffaf0", "#f1e7d4",
                           "#e7dcc4", "#dccbab", "#c0622d", "#3f8a6e",
                           "#bf9000", "#ecd9b8",
                           text="#3a2f1f", muted="#8a7a5f", warn="#b5651d",
                           pos=LIGHT_POS),
    "Mint Fresh":   _theme("#e6f2ec", "#f1faf5", "#ffffff", "#e8f5ee",
                           "#daeee3", "#c4e0d2", "#10a37f", "#2f7bed",
                           "#d39e00", "#cdeede",
                           text="#13312a", muted="#5f8276", warn="#bf7d12",
                           pos=LIGHT_POS),
}
DEFAULT_THEME = "Midnight"

# These module-level names are (re)assigned by apply_palette() and referenced
# throughout the widget-building code.
C_BG = C_PANEL = C_CARD = C_STRIPE = C_TEXT = C_MUTED = ""
C_ACCENT = C_ACCENT2 = C_GOLD = C_WARN = C_MINE = ""
C_HEAD = C_BORDER = C_SEL = C_TOPPICK = ""
# derived contrast / interaction colors
C_ACCENT_TEXT = C_ACCENT2_TEXT = C_SEL_TEXT = ""
C_BTN_HOVER = C_BTN_PRESS = C_ACCENT_HOVER = C_ACCENT_PRESS = C_WARN_BG = ""
POS_COLORS = {}


def _blend(hex_a, hex_b, t):
    a, b = hex_a.lstrip("#"), hex_b.lstrip("#")
    c = tuple(round(int(a[i:i+2], 16) * (1 - t) + int(b[i:i+2], 16) * t)
              for i in (0, 2, 4))
    return "#%02x%02x%02x" % c


def _luminance(hexcolor):
    r, g, b = (int(hexcolor.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ideal_ink(hexcolor):
    """Return a dark or light text color that reads well on hexcolor."""
    return "#10151c" if _luminance(hexcolor) > 0.55 else "#ffffff"


def apply_palette(name):
    """Set the module-level color globals from the named theme."""
    global C_BG, C_PANEL, C_CARD, C_STRIPE, C_TEXT, C_MUTED, C_ACCENT
    global C_ACCENT2, C_GOLD, C_WARN, C_MINE, C_HEAD, C_BORDER, C_SEL
    global C_TOPPICK, POS_COLORS
    global C_ACCENT_TEXT, C_ACCENT2_TEXT, C_SEL_TEXT
    global C_BTN_HOVER, C_BTN_PRESS, C_ACCENT_HOVER, C_ACCENT_PRESS, C_WARN_BG
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    C_BG, C_PANEL, C_CARD, C_STRIPE = t["bg"], t["panel"], t["card"], t["stripe"]
    C_TEXT, C_MUTED = t["text"], t["muted"]
    C_ACCENT, C_ACCENT2, C_GOLD = t["accent"], t["accent2"], t["gold"]
    C_WARN, C_MINE = t["warn"], t["mine"]
    C_HEAD, C_BORDER, C_SEL = t["head"], t["border"], t["sel"]
    C_TOPPICK = _blend(t["bg"], t["gold"], 0.18)
    POS_COLORS = t["pos"]

    # derived interaction + contrast colors so themes work in light or dark
    C_ACCENT_TEXT = _ideal_ink(C_ACCENT)
    C_ACCENT2_TEXT = _ideal_ink(C_ACCENT2)
    C_SEL_TEXT = _ideal_ink(C_SEL)
    C_BTN_HOVER = _blend(C_CARD, C_TEXT, 0.12)
    C_BTN_PRESS = _blend(C_CARD, C_TEXT, 0.22)
    C_ACCENT_HOVER = _blend(C_ACCENT, "#ffffff", 0.14)
    C_ACCENT_PRESS = _blend(C_ACCENT, "#000000", 0.12)
    C_WARN_BG = _blend(C_CARD, C_WARN, 0.22)


apply_palette(DEFAULT_THEME)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FF Draft Assistant")
        self.geometry("1540x860")
        self.minsize(1240, 700)
        self.configure(bg=C_BG)
        try:
            self.iconbitmap(os.path.join(APP_DIR, "football.ico"))
        except Exception:
            pass

        self.draft: Draft | None = None
        self.players: list = []
        self.meta: dict = {}
        self.source: str = ""
        self._queue: queue.Queue = queue.Queue()
        self.roster_config = RosterConfig()
        self.theme_name = DEFAULT_THEME
        self.theme_var = tk.StringVar(value=DEFAULT_THEME)

        self.scoring_var = tk.StringVar(value="PPR")
        self.teams_var = tk.IntVar(value=12)
        self.filter_var = tk.StringVar(value="ALL")
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Loading draft data...")
        self.hide_drafted_var = tk.BooleanVar(value=True)
        self.show_upside_var = tk.BooleanVar(value=False)

        # Sleeper linkage + live draft sync
        self.sleeper: dict = {}
        self.sync_var = tk.BooleanVar(value=False)
        self.sync_status_var = tk.StringVar(value="")
        self._sync_bridge = None
        self._sync_queue: queue.Queue = queue.Queue()
        self._sync_running = False
        self._sync_stop = None

        # League Teams view (other managers' rosters, live from the pick feed)
        self.team_pick_var = tk.StringVar()
        self.teams_status_var = tk.StringVar(value="")
        self._team_labels: dict = {}
        self._team_label_to_rid: dict = {}
        self._draft_picks_raw: list = []
        self._teams_refresh_queue: queue.Queue = queue.Queue()

        # Live draft pulse (on the clock / runs / likely-gone) + draft meta
        self._draft_meta: dict = {}
        self.pulse_clock_var = tk.StringVar(value="")
        self.pulse_run_var = tk.StringVar(value="")
        self.pulse_gone_var = tk.StringVar(value="")
        self._was_on_clock = False

        # Team grades / power rankings
        self.grade_summary_var = tk.StringVar(value="")
        self._last_rankings: list = []
        self._rank_iid_to_rid: dict = {}

        # available-table sorting
        self.sort_col = "adp"
        self.sort_reverse = False

        # season tab
        self._season_queue: queue.Queue = queue.Queue()
        self._season_loaded = False
        self._season_data: dict = {}
        self.season_status_var = tk.StringVar(value="")
        self.inj_fantasy_only = tk.BooleanVar(value=True)
        self.inj_mine_only = tk.BooleanVar(value=False)
        # league-aware season (FAAB waivers / roster / start-sit / matchup)
        self.faab_status_var = tk.StringVar(value="")
        self.waiver_needs_only = tk.BooleanVar(value=False)
        self.addrop_var = tk.StringVar(value="")
        self.startsit_var = tk.StringVar(value="")
        self.bye_var = tk.StringVar(value="")
        self.matchup_var = tk.StringVar(value="")
        # trade analyzer
        self.trade_opp_var = tk.StringVar()
        self.trade_result_var = tk.StringVar(value="")
        self._trade_opp_label_to_rid: dict = {}

        self._load_league_settings()
        self.theme_var.set(self.theme_name)
        apply_palette(self.theme_name)
        self._build_style()
        self._build_widgets()
        self.search_var.trace_add("write", lambda *_: self.refresh_views())

        self.after(100, lambda: self.load_data(force=False, restore=True))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ style
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=C_PANEL, foreground=C_TEXT,
                        fieldbackground=C_CARD, bordercolor=C_BORDER)
        style.configure("TFrame", background=C_PANEL)
        style.configure("Card.TFrame", background=C_CARD)
        style.configure("TLabel", background=C_PANEL, foreground=C_TEXT)
        style.configure("Muted.TLabel", background=C_PANEL, foreground=C_MUTED)
        style.configure("Head.TLabel", background=C_PANEL, foreground=C_TEXT,
                        font=("Segoe UI Semibold", 11, "bold"))
        style.configure("Title.TLabel", background=C_BG, foreground=C_TEXT,
                        font=("Segoe UI", 16, "bold"))
        style.configure("Status.TLabel", background=C_BG, foreground=C_MUTED,
                        font=("Segoe UI", 9))
        style.configure("TButton", background=C_CARD, foreground=C_TEXT,
                        borderwidth=0, padding=(11, 7), font=("Segoe UI", 9))
        style.map("TButton",
                  background=[("active", C_BTN_HOVER), ("pressed", C_BTN_PRESS)])
        style.configure("Accent.TButton", background=C_ACCENT,
                        foreground=C_ACCENT_TEXT, font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton",
                  background=[("active", C_ACCENT_HOVER),
                              ("pressed", C_ACCENT_PRESS)],
                  foreground=[("active", C_ACCENT_TEXT)])
        style.configure("Warn.TButton", background=C_WARN_BG, foreground=C_TEXT)
        style.map("Warn.TButton",
                  background=[("active", _blend(C_WARN_BG, C_TEXT, 0.12))])

        style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                        foreground=C_TEXT, rowheight=28, borderwidth=0,
                        relief="flat", bordercolor=C_CARD, lightcolor=C_CARD,
                        darkcolor=C_CARD, font=("Segoe UI", 10))
        # clam draws a 3D bevel via these element layouts; flatten it so there
        # is no light/white border around the tables.
        try:
            style.layout("Treeview", [
                ("Treeview.treearea", {"sticky": "nswe"})])
        except tk.TclError:
            pass
        style.configure("Treeview.Heading", background=C_HEAD, foreground=C_MUTED,
                        font=("Segoe UI", 9, "bold"), relief="flat",
                        borderwidth=0, bordercolor=C_HEAD, lightcolor=C_HEAD,
                        darkcolor=C_HEAD, padding=(6, 7))
        style.map("Treeview.Heading",
                  background=[("active", C_PANEL)],
                  foreground=[("active", C_TEXT)])
        style.map("Treeview", background=[("selected", C_SEL)],
                  foreground=[("selected", C_SEL_TEXT)])

        # flat, minimal scrollbars (no arrow buttons, no 3D bevel)
        for orient in ("Vertical", "Horizontal"):
            try:
                style.layout(f"{orient}.TScrollbar", [
                    (f"{orient}.Scrollbar.trough", {"sticky": "nswe", "children": [
                        (f"{orient}.Scrollbar.thumb",
                         {"expand": 1, "sticky": "nswe"})]})])
            except tk.TclError:
                pass
            style.configure(f"{orient}.TScrollbar", troughcolor=C_PANEL,
                            background=C_BORDER, bordercolor=C_PANEL,
                            lightcolor=C_PANEL, darkcolor=C_PANEL,
                            arrowcolor=C_MUTED, relief="flat", borderwidth=0)
            style.map(f"{orient}.TScrollbar",
                      background=[("active", C_SEL)])

        # flat comboboxes / spinboxes (kill the bevel)
        style.configure("TCombobox", fieldbackground=C_CARD, background=C_CARD,
                        foreground=C_TEXT, arrowcolor=C_TEXT, relief="flat",
                        borderwidth=0, bordercolor=C_CARD, lightcolor=C_CARD,
                        darkcolor=C_CARD, padding=5)
        style.map("TCombobox",
                  fieldbackground=[("readonly", C_CARD)],
                  background=[("readonly", C_CARD)],
                  foreground=[("readonly", C_TEXT)],
                  bordercolor=[("focus", C_ACCENT2)])
        style.configure("TSpinbox", fieldbackground=C_CARD, background=C_CARD,
                        foreground=C_TEXT, arrowcolor=C_TEXT, relief="flat",
                        borderwidth=0, bordercolor=C_CARD, lightcolor=C_CARD,
                        darkcolor=C_CARD, padding=3)
        style.configure("TCheckbutton", background=C_PANEL, foreground=C_MUTED)
        style.map("TCheckbutton", background=[("active", C_PANEL)],
                  foreground=[("active", C_TEXT)])
        style.configure("TSeparator", background=C_BORDER)

        # notebook tabs (flat, themed)
        style.configure("TNotebook", background=C_BG, borderwidth=0,
                        tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab", background=C_CARD, foreground=C_MUTED,
                        padding=(14, 5), borderwidth=0,
                        font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", C_ACCENT), ("active", C_BTN_HOVER)],
                  foreground=[("selected", C_ACCENT_TEXT), ("active", C_TEXT)],
                  expand=[("selected", (2, 3, 2, 0))],
                  font=[("selected", ("Segoe UI Semibold", 13, "bold"))])
        try:
            style.layout("TNotebook.Tab", [
                ("Notebook.tab", {"sticky": "nswe", "children": [
                    ("Notebook.padding", {"side": "top", "sticky": "nswe",
                                          "children": [
                        ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
        except tk.TclError:
            pass

        # combobox drop-down list (a tk Listbox under the hood)
        self.option_add("*TCombobox*Listbox.background", C_CARD)
        self.option_add("*TCombobox*Listbox.foreground", C_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", C_SEL)
        self.option_add("*TCombobox*Listbox.selectForeground", C_SEL_TEXT)
        self.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.option_add("*TCombobox*Listbox.relief", "flat")

    def _section_header(self, parent, text, accent, hint=None):
        """A section title with a colored accent bar on the left."""
        f = tk.Frame(parent, bg=C_PANEL)
        tk.Frame(f, bg=accent, width=4, height=20).pack(
            side="left", fill="y", padx=(0, 9))
        tk.Label(f, text=text, bg=C_PANEL, fg=C_TEXT,
                 font=("Segoe UI Semibold", 12, "bold")).pack(side="left")
        if hint:
            tk.Label(f, text="   " + hint, bg=C_PANEL, fg=C_MUTED,
                     font=("Segoe UI", 9)).pack(side="left", pady=(3, 0))
        return f

    def _apply_row_tags(self, tree):
        """Configure the shared row tags (stripes, position colors, states)."""
        tree.tag_configure("row_even", background=C_CARD)
        tree.tag_configure("row_odd", background=C_STRIPE)
        for pos, color in POS_COLORS.items():
            tree.tag_configure(f"pos_{pos}", foreground=color)
        tree.tag_configure("drafted", foreground=C_MUTED)
        tree.tag_configure("mine", foreground=C_ACCENT)
        tree.tag_configure("top_pick", background=C_TOPPICK,
                           font=("Segoe UI", 10, "bold"))
        tree.tag_configure("upside", font=("Segoe UI", 10, "bold"))
        tree.tag_configure("empty", foreground=C_MUTED)

    # ----------------------------------------------------------- table sorting
    _SORT_DEFAULT_REVERSE = {"adp": False, "name": False, "pos": False,
                             "team": False, "bye": False, "vorp": True,
                             "risk": True}

    def _on_sort(self, col):
        if col == self.sort_col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = self._SORT_DEFAULT_REVERSE.get(col, False)
        self._render_available()

    def _update_sort_headers(self):
        arrow = "  \u25BC" if self.sort_reverse else "  \u25B2"
        for c, label in self._avail_heads.items():
            self.tree.heading(c, text=label + (arrow if c == self.sort_col else ""))

    def _sorted_players_for_table(self):
        keymap = {
            "adp": lambda p: p.adp,
            "name": lambda p: p.name.lower(),
            "pos": lambda p: (p.display_pos, p.adp),
            "team": lambda p: (p.team, p.adp),
            "bye": lambda p: (p.bye or 0, p.adp),
            "vorp": lambda p: p.vorp,
            "risk": lambda p: (p.upside, p.risk),
        }
        key = keymap.get(self.sort_col, lambda p: p.adp)
        return sorted(self.players, key=key, reverse=self.sort_reverse)

    # ---------------------------------------------------------------- widgets
    def _build_widgets(self):
        self.configure(bg=C_BG)
        # --- top settings bar -------------------------------------------------
        top = tk.Frame(self, bg=C_BG)
        top.pack(side="top", fill="x", padx=14, pady=(12, 6))

        ttk.Label(top, text="\U0001F3C8  FF Draft Assistant",
                  style="Title.TLabel").pack(side="left")

        bar = tk.Frame(top, bg=C_BG)
        bar.pack(side="right")

        ttk.Label(bar, text="Scoring", style="Status.TLabel").grid(row=0, column=0, padx=(0, 4))
        sc = ttk.Combobox(bar, textvariable=self.scoring_var, width=16,
                          values=list(SCORING_FORMATS.keys()), state="readonly")
        sc.grid(row=0, column=1, padx=(0, 12))
        sc.bind("<<ComboboxSelected>>", lambda e: self.on_settings_change())

        ttk.Label(bar, text="Teams", style="Status.TLabel").grid(row=0, column=2, padx=(0, 4))
        tm = ttk.Combobox(bar, textvariable=self.teams_var, width=4,
                          values=TEAM_OPTIONS, state="readonly")
        tm.grid(row=0, column=3, padx=(0, 12))
        tm.bind("<<ComboboxSelected>>", lambda e: self.on_settings_change())

        ttk.Label(bar, text="Theme", style="Status.TLabel").grid(row=0, column=4, padx=(0, 4))
        th = ttk.Combobox(bar, textvariable=self.theme_var, width=16,
                          values=list(THEMES.keys()), state="readonly")
        th.grid(row=0, column=5, padx=(0, 12))
        th.bind("<<ComboboxSelected>>",
                lambda e: self.after(1, lambda: self.change_theme(self.theme_var.get())))

        ttk.Button(bar, text="\u2699 League Settings", style="Accent.TButton",
                   command=self.open_settings).grid(row=0, column=6, padx=(0, 10))
        ttk.Button(bar, text="\u21BB Refresh", command=lambda: self.load_data(force=True)
                   ).grid(row=0, column=7, padx=3)
        ttk.Button(bar, text="Undo", command=self.undo).grid(row=0, column=8, padx=3)
        ttk.Button(bar, text="Save", command=self.save_draft).grid(row=0, column=9, padx=3)
        ttk.Button(bar, text="Load", command=self.load_draft).grid(row=0, column=10, padx=3)
        ttk.Button(bar, text="Reset", style="Warn.TButton",
                   command=self.reset_draft).grid(row=0, column=11, padx=3)

        statusrow = tk.Frame(self, bg=C_BG)
        statusrow.pack(side="top", fill="x", padx=16, pady=(0, 6))
        ttk.Label(statusrow, textvariable=self.status_var, style="Status.TLabel"
                  ).pack(side="left", anchor="w")
        self.league_var = tk.StringVar(value="")
        ttk.Label(statusrow, textvariable=self.league_var, style="Status.TLabel"
                  ).pack(side="right", anchor="e")

        # --- tabbed content: Draft | Season ----------------------------------
        self.nb = ttk.Notebook(self)
        self.nb.pack(side="top", fill="both", expand=True, padx=12, pady=(0, 10))
        draft_tab = tk.Frame(self.nb, bg=C_BG)
        teams_tab = tk.Frame(self.nb, bg=C_BG)
        season_tab = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(draft_tab, text="  \U0001F4CB  Draft  ")
        self.nb.add(teams_tab, text="  \U0001F465  Teams  ")
        self.nb.add(season_tab, text="  \U0001F4F0  Season  ")
        self._build_draft_tab(draft_tab)
        self._build_teams_tab(teams_tab)
        self._build_season_tab(season_tab)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_draft_tab(self, parent):
        # three columns: Available | Insights | My Team
        main = tk.PanedWindow(parent, orient="horizontal", bg=C_BG, sashwidth=8,
                              bd=0, sashrelief="flat")
        main.pack(side="top", fill="both", expand=True)

        left = tk.Frame(main, bg=C_PANEL)
        middle = tk.Frame(main, bg=C_PANEL)
        right = tk.Frame(main, bg=C_PANEL)
        main.add(left, minsize=480, width=540, stretch="always")
        main.add(middle, minsize=440, width=560, stretch="always")
        main.add(right, minsize=340, width=400, stretch="always")

        self._build_available(left)
        self._build_insights(middle)
        self._build_team(right)

    def _build_available(self, parent):
        self._section_header(
            parent, "Available Players", C_ACCENT2,
            hint="double-click = drafted by another team  \u00b7  click a column to sort"
        ).pack(fill="x", padx=12, pady=(12, 8))

        # filter row
        frow = tk.Frame(parent, bg=C_PANEL)
        frow.pack(fill="x", padx=12, pady=(0, 8))
        self.filter_buttons = {}
        for pos in POS_FILTERS:
            b = tk.Button(frow, text=pos, bd=0, padx=12, pady=5,
                          bg=C_CARD, fg=C_TEXT, activebackground=C_SEL,
                          activeforeground=C_TEXT, font=("Segoe UI", 9, "bold"),
                          relief="flat", cursor="hand2", highlightthickness=0,
                          command=lambda p=pos: self.set_filter(p))
            b.pack(side="left", padx=2)
            self.filter_buttons[pos] = b

        sframe = tk.Frame(frow, bg=C_CARD)
        sframe.pack(side="right", padx=(12, 0))
        tk.Label(sframe, text="\U0001F50D", bg=C_CARD, fg=C_MUTED).pack(
            side="left", padx=(8, 2))
        ent = tk.Entry(sframe, textvariable=self.search_var, bg=C_CARD, fg=C_TEXT,
                       insertbackground=C_TEXT, relief="flat", width=18,
                       font=("Segoe UI", 10))
        ent.pack(side="left", ipady=5, padx=(0, 6))

        # table (borderless, blends into the panel)
        cols = ("adp", "name", "pos", "team", "bye", "vorp", "risk")
        self._avail_heads = {"adp": "ADP", "name": "Player", "pos": "Pos",
                             "team": "Team", "bye": "Bye", "vorp": "Value",
                             "risk": "Risk"}
        widths = {"adp": 64, "name": 210, "pos": 54, "team": 58,
                  "bye": 50, "vorp": 70, "risk": 84}
        inner = tk.Frame(parent, bg=C_CARD, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=12)
        self.tree = ttk.Treeview(inner, columns=cols, show="headings",
                                 selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=self._avail_heads[c],
                              command=lambda col=c: self._on_sort(col))
            anchor = "w" if c == "name" else "center"
            self.tree.column(c, width=widths[c], anchor=anchor,
                             stretch=(c == "name"))
        vsb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.draft_selected("other"))

        self._apply_row_tags(self.tree)

        # action buttons
        act = tk.Frame(parent, bg=C_PANEL)
        act.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Button(act, text="\u2705  Draft to MY team", style="Accent.TButton",
                   command=lambda: self.draft_selected("me")).pack(side="left")
        ttk.Button(act, text="\u274C  Drafted by another team",
                   command=lambda: self.draft_selected("other")).pack(side="left", padx=8)
        ttk.Checkbutton(act, text="Hide drafted", variable=self.hide_drafted_var,
                        command=self.refresh_views,
                        style="TCheckbutton").pack(side="right")
        ttk.Checkbutton(act, text="Show upside picks",
                        variable=self.show_upside_var,
                        command=self._on_toggle_upside,
                        style="TCheckbutton").pack(side="right", padx=(0, 12))

        # Sleeper live-draft sync
        sync = tk.Frame(parent, bg=C_PANEL)
        sync.pack(fill="x", padx=12, pady=(0, 12))
        self.sync_check = ttk.Checkbutton(
            sync, text="\U0001F504  Sync Sleeper draft", variable=self.sync_var,
            command=self._toggle_sync, style="TCheckbutton")
        self.sync_check.pack(side="left")
        ttk.Label(sync, textvariable=self.sync_status_var,
                  style="Muted.TLabel").pack(side="left", padx=(10, 0))

    def _build_insights(self, parent):
        # Draft Pulse (live, from the Sleeper pick feed while sync is on)
        pulse = tk.Frame(parent, bg=C_CARD, highlightthickness=0)
        pulse.pack(fill="x", padx=12, pady=(12, 0))
        self.pulse_clock_lbl = tk.Label(
            pulse, textvariable=self.pulse_clock_var, bg=C_CARD, fg=C_GOLD,
            font=("Segoe UI Semibold", 12, "bold"), anchor="w", justify="left")
        self.pulse_clock_lbl.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(pulse, textvariable=self.pulse_run_var, bg=C_CARD, fg=C_WARN,
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=520).pack(fill="x", padx=10, pady=(0, 2))
        tk.Label(pulse, textvariable=self.pulse_gone_var, bg=C_CARD, fg=C_MUTED,
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=520).pack(fill="x", padx=10, pady=(0, 8))

        # Recommendations
        self._section_header(parent, "\u2B50 Draft These Now", C_GOLD,
                             hint="double-click to add to your team").pack(
            anchor="w", fill="x", padx=12, pady=(12, 6))
        rec_wrap = tk.Frame(parent, bg=C_CARD, highlightthickness=0)
        rec_wrap.pack(fill="x", padx=12)
        self.rec_tree = ttk.Treeview(
            rec_wrap, columns=("rank", "name", "pos", "adp", "why"),
            show="headings", height=5, selectmode="browse")
        for c, (label, w, anc) in {
            "rank": ("#", 30, "center"), "name": ("Player", 150, "w"),
            "pos": ("Pos", 45, "center"), "adp": ("ADP", 50, "center"),
            "why": ("Why", 300, "w")}.items():
            self.rec_tree.heading(c, text=label)
            self.rec_tree.column(c, width=w, anchor=anc, stretch=(c == "why"))
        self.rec_tree.pack(fill="x")
        self.rec_tree.bind("<Double-1>", self._draft_from_rec)

        # Best by position
        self._section_header(parent, "\U0001F4CA Best Available by Position",
                             C_ACCENT).pack(anchor="w", fill="x",
                                            padx=12, pady=(14, 6))
        pos_wrap = tk.Frame(parent, bg=C_CARD, highlightthickness=0)
        pos_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.pos_tree = ttk.Treeview(
            pos_wrap, columns=("adp", "team", "bye"), show="tree headings",
            selectmode="browse")
        self.pos_tree.heading("#0", text="Player")
        self.pos_tree.heading("adp", text="ADP")
        self.pos_tree.heading("team", text="Team")
        self.pos_tree.heading("bye", text="Bye")
        self.pos_tree.column("#0", width=210, anchor="w")
        self.pos_tree.column("adp", width=55, anchor="center")
        self.pos_tree.column("team", width=55, anchor="center")
        self.pos_tree.column("bye", width=45, anchor="center")
        pvsb = ttk.Scrollbar(pos_wrap, orient="vertical", command=self.pos_tree.yview)
        self.pos_tree.configure(yscrollcommand=pvsb.set)
        self.pos_tree.pack(side="left", fill="both", expand=True)
        pvsb.pack(side="right", fill="y")
        self.pos_tree.bind("<Double-1>", self._draft_from_postree)

        self._apply_row_tags(self.rec_tree)
        self._apply_row_tags(self.pos_tree)
        self.pos_tree.tag_configure("group", font=("Segoe UI Semibold", 10, "bold"),
                                    foreground=C_TEXT, background=C_HEAD)

    def _build_team(self, parent):
        self._section_header(parent, "\U0001F465 My Team", C_ACCENT2).pack(
            anchor="w", fill="x", padx=12, pady=(12, 6))

        # needs + bye-conflict callouts
        info = tk.Frame(parent, bg=C_CARD, highlightthickness=0)
        info.pack(fill="x", padx=12, pady=(0, 8))
        self.needs_var = tk.StringVar(value="")
        self.byes_var = tk.StringVar(value="")
        tk.Label(info, textvariable=self.needs_var, bg=C_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9), justify="left", anchor="w",
                 wraplength=360).pack(fill="x", padx=10, pady=(8, 2))
        self.byes_label = tk.Label(info, textvariable=self.byes_var, bg=C_CARD,
                                   fg=C_WARN, font=("Segoe UI", 9),
                                   justify="left", anchor="w", wraplength=360)
        self.byes_label.pack(fill="x", padx=10, pady=(0, 8))

        ros_wrap = tk.Frame(parent, bg=C_CARD, highlightthickness=0)
        ros_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.roster_tree = ttk.Treeview(
            ros_wrap, columns=("name", "pos", "bye"), show="tree headings",
            selectmode="browse")
        self.roster_tree.heading("#0", text="Slot")
        self.roster_tree.heading("name", text="Player")
        self.roster_tree.heading("pos", text="Pos")
        self.roster_tree.heading("bye", text="Bye")
        self.roster_tree.column("#0", width=92, minwidth=92, anchor="w")
        self.roster_tree.column("name", width=170, anchor="w", stretch=True)
        self.roster_tree.column("pos", width=46, anchor="center")
        self.roster_tree.column("bye", width=46, anchor="center")
        rvsb = ttk.Scrollbar(ros_wrap, orient="vertical",
                             command=self.roster_tree.yview)
        self.roster_tree.configure(yscrollcommand=rvsb.set)
        self.roster_tree.pack(side="left", fill="both", expand=True)
        rvsb.pack(side="right", fill="y")
        self.roster_tree.bind("<Double-1>", self._undraft_from_roster)
        ttk.Label(parent, text="   double-click a player to drop him from your team",
                  style="Muted.TLabel").pack(anchor="w", padx=12, pady=(0, 10))

        self._apply_row_tags(self.roster_tree)
        self.roster_tree.tag_configure("group", font=("Segoe UI Semibold", 10, "bold"),
                                       foreground=C_TEXT, background=C_HEAD)

    # -------------------------------------------------------------- teams tab
    def _build_teams_tab(self, parent):
        bar = tk.Frame(parent, bg=C_BG)
        bar.pack(side="top", fill="x", pady=(8, 6))
        tk.Label(bar, text="\U0001F465  League Teams", bg=C_BG, fg=C_TEXT,
                 font=("Segoe UI Semibold", 12, "bold")).pack(side="left",
                                                              padx=(4, 12))
        ttk.Label(bar, textvariable=self.teams_status_var,
                  style="Status.TLabel").pack(side="left")
        ttk.Button(bar, text="\u21BB Refresh",
                   command=self._refresh_teams).pack(side="right")
        self.team_combo = ttk.Combobox(bar, textvariable=self.team_pick_var,
                                       state="readonly", width=32, values=[])
        self.team_combo.pack(side="right", padx=(0, 10))
        self.team_combo.bind("<<ComboboxSelected>>",
                             lambda e: self._on_team_select())
        ttk.Label(bar, text="Team", style="Status.TLabel").pack(
            side="right", padx=(0, 6))

        split = tk.PanedWindow(parent, orient="horizontal", bg=C_BG,
                               sashwidth=8, bd=0, sashrelief="flat")
        split.pack(side="top", fill="both", expand=True)
        left = tk.Frame(split, bg=C_PANEL)
        right = tk.Frame(split, bg=C_PANEL)
        split.add(left, minsize=290, width=360, stretch="always")
        split.add(right, minsize=360, width=560, stretch="always")

        # --- Power rankings (left) ---
        self._section_header(left, "\U0001F3C6 Power Rankings", C_GOLD,
                             hint="by total draft value").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        rwrap = tk.Frame(left, bg=C_CARD, highlightthickness=0)
        rwrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.rank_tree = ttk.Treeview(rwrap, columns=("value", "grade"),
                                      show="tree headings", selectmode="browse")
        self.rank_tree.heading("#0", text="Team")
        self.rank_tree.heading("value", text="Value")
        self.rank_tree.heading("grade", text="Grade")
        self.rank_tree.column("#0", width=200, anchor="w")
        self.rank_tree.column("value", width=70, anchor="center", stretch=False)
        self.rank_tree.column("grade", width=60, anchor="center", stretch=False)
        rvsb = ttk.Scrollbar(rwrap, orient="vertical",
                             command=self.rank_tree.yview)
        self.rank_tree.configure(yscrollcommand=rvsb.set)
        self.rank_tree.pack(side="left", fill="both", expand=True)
        rvsb.pack(side="right", fill="y")
        self.rank_tree.bind("<<TreeviewSelect>>", self._on_rank_select)
        self._apply_row_tags(self.rank_tree)
        self.rank_tree.tag_configure("mine_row", background=C_TOPPICK,
                                     font=("Segoe UI", 10, "bold"))

        # --- Selected team roster + grade (right) ---
        self._section_header(right, "\U0001F4CB Roster", C_ACCENT2).pack(
            anchor="w", fill="x", padx=12, pady=(10, 4))
        tk.Label(right, textvariable=self.grade_summary_var, bg=C_PANEL,
                 fg=C_TEXT, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=520).pack(fill="x", padx=14, pady=(0, 6))
        inner = tk.Frame(right, bg=C_CARD, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.teams_tree = ttk.Treeview(
            inner, columns=("pick", "pos", "team"), show="tree headings",
            selectmode="browse")
        self.teams_tree.heading("#0", text="Player")
        self.teams_tree.heading("pick", text="Pick")
        self.teams_tree.heading("pos", text="Pos")
        self.teams_tree.heading("team", text="Tm")
        self.teams_tree.column("#0", width=250, anchor="w")
        self.teams_tree.column("pick", width=70, anchor="center", stretch=False)
        self.teams_tree.column("pos", width=60, anchor="center", stretch=False)
        self.teams_tree.column("team", width=60, anchor="center", stretch=False)
        tvsb = ttk.Scrollbar(inner, orient="vertical",
                             command=self.teams_tree.yview)
        self.teams_tree.configure(yscrollcommand=tvsb.set)
        self.teams_tree.pack(side="left", fill="both", expand=True)
        tvsb.pack(side="right", fill="y")
        self._apply_row_tags(self.teams_tree)
        self.teams_tree.tag_configure(
            "group", font=("Segoe UI Semibold", 10, "bold"),
            foreground=C_TEXT, background=C_HEAD)
        # restore any labels/picks already loaded (e.g. after a theme rebuild)
        if self._team_labels:
            self._set_team_labels(self._team_labels)
        self._render_teams_view()

    def _set_team_labels(self, labels: dict):
        self._team_labels = labels or {}
        my_rid = str((self.sleeper or {}).get("roster_id"))
        items = sorted(self._team_labels.items(),
                       key=lambda kv: (int(kv[0]) if str(kv[0]).isdigit()
                                       else 9999))
        self._team_label_to_rid = {}
        display = []
        for rid, name in items:
            lbl = ("\u2605 " if str(rid) == my_rid else "") + name
            self._team_label_to_rid[lbl] = rid
            display.append(lbl)
        if hasattr(self, "team_combo"):
            self.team_combo.configure(values=display)
        cur = self.team_pick_var.get()
        if cur not in self._team_label_to_rid and display:
            mine = [d for d in display if d.startswith("\u2605 ")]
            self.team_pick_var.set(mine[0] if mine else display[0])

    def _render_teams(self):
        if not hasattr(self, "teams_tree"):
            return
        self.teams_tree.delete(*self.teams_tree.get_children())
        rid = self._team_label_to_rid.get(self.team_pick_var.get())
        picks = [p for p in self._draft_picks_raw
                 if rid is not None and str(p.get("roster_id")) == str(rid)]
        if not picks:
            hint = ("Turn on \u201cSync Sleeper draft\u201d or click Refresh."
                    if not self._draft_picks_raw else "(no picks yet)")
            self.teams_tree.insert("", "end", text="  " + hint,
                                   values=("", "", ""), tags=("empty",))
            return
        from collections import defaultdict
        by_pos = defaultdict(list)
        for pk in picks:
            md = pk.get("metadata") or {}
            pos = (md.get("position") or "?").upper()
            pos = "K" if pos == "PK" else pos
            by_pos[pos].append(pk)
        order = ["QB", "RB", "WR", "TE", "K", "DEF"]
        allpos = order + [p for p in by_pos if p not in set(order)]
        for pos in allpos:
            plist = by_pos.get(pos)
            if not plist:
                continue
            self.teams_tree.insert("", "end", text=f"  {pos}  ({len(plist)})",
                                   values=("", "", ""), tags=("group",))
            for pk in sorted(plist, key=lambda x: x.get("pick_no") or 0):
                md = pk.get("metadata") or {}
                name = (" ".join(x for x in (md.get("first_name"),
                                             md.get("last_name")) if x)
                        or str(pk.get("player_id") or "?"))
                code = injury_code(md.get("injury_status") or "")
                if code:
                    name = f"{name}  {code}"
                rnd, pno = pk.get("round"), pk.get("pick_no")
                picklbl = f"R{rnd}\u00b7{pno}" if rnd and pno else (
                    f"#{pno}" if pno else "")
                disp_pos = "K" if pos == "PK" else pos
                tag = f"pos_{disp_pos}" if disp_pos in POS_COLORS else "row_even"
                self.teams_tree.insert(
                    "", "end", text="  " + name,
                    values=(picklbl, disp_pos, md.get("team") or "-"),
                    tags=(tag,))

    def _render_teams_view(self):
        """Refresh everything on the Teams tab (rankings + roster + grade)."""
        self._render_rankings()
        self._render_teams()
        self._update_grade_summary()

    def _on_team_select(self):
        self._render_teams()
        self._update_grade_summary()

    def _on_rank_select(self, event):
        sel = self.rank_tree.selection()
        if not sel:
            return
        rid = self._rank_iid_to_rid.get(sel[0])
        if rid is None:
            return
        for lbl, r in self._team_label_to_rid.items():
            if str(r) == str(rid):
                self.team_pick_var.set(lbl)
                break
        self._render_teams()
        self._update_grade_summary()

    def _compute_power_rankings(self):
        """Rank every roster by total VORP of its drafted players."""
        bridge = self._sync_bridge or {}
        picks = self._draft_picks_raw
        if not bridge or not picks:
            self._last_rankings = []
            return []
        from collections import defaultdict
        by_rid = defaultdict(list)
        for pk in picks:
            by_rid[str(pk.get("roster_id"))].append(pk)
        rows = []
        for rid, pk_list in by_rid.items():
            val = 0.0
            for pk in pk_list:
                eng = bridge.get(str(pk.get("player_id")))
                if eng:
                    val += eng.vorp
            rows.append({"rid": rid,
                         "label": self._team_labels.get(rid, f"Team {rid}"),
                         "value": round(val, 1), "n": len(pk_list)})
        rows.sort(key=lambda r: r["value"], reverse=True)
        grades = ["A+", "A", "A-", "B+", "B", "B-",
                  "C+", "C", "C-", "D+", "D", "D-"]
        for i, r in enumerate(rows):
            r["rank"] = i + 1
            r["grade"] = grades[i] if i < len(grades) else "D-"
        self._last_rankings = rows
        return rows

    def _render_rankings(self):
        if not hasattr(self, "rank_tree"):
            return
        self.rank_tree.delete(*self.rank_tree.get_children())
        self._rank_iid_to_rid = {}
        rows = self._compute_power_rankings()
        if not rows:
            self.rank_tree.insert("", "end", text="  (awaiting picks)",
                                  values=("", ""), tags=("empty",))
            return
        my_rid = str((self.sleeper or {}).get("roster_id"))
        for r in rows:
            iid = f"rank:{r['rid']}"
            self._rank_iid_to_rid[iid] = r["rid"]
            star = "\u2605 " if str(r["rid"]) == my_rid else ""
            tags = (("mine_row",) if str(r["rid"]) == my_rid
                    else (("row_odd",) if r["rank"] % 2 else ("row_even",)))
            self.rank_tree.insert(
                "", "end", iid=iid,
                text=f"  {r['rank']}. {star}{r['label']}",
                values=(f"{r['value']:+.0f}", r["grade"]), tags=tags)

    def _team_grade_detail(self, rid):
        """Best pick / biggest steal / biggest reach for one roster."""
        bridge = self._sync_bridge or {}
        picks = [p for p in self._draft_picks_raw
                 if str(p.get("roster_id")) == str(rid)]
        if not bridge or not picks:
            return ""
        best = steal = reach = None
        for pk in picks:
            eng = bridge.get(str(pk.get("player_id")))
            if not eng:
                continue
            pno = pk.get("pick_no") or 0
            if best is None or eng.vorp > best[0].vorp:
                best = (eng, pno)
            sval = pno - eng.adp        # drafted later than ADP -> value
            if steal is None or sval > steal[1]:
                steal = (eng, sval, pno)
            rval = eng.adp - pno        # drafted earlier than ADP -> reach
            if reach is None or rval > reach[1]:
                reach = (eng, rval, pno)
        bits = []
        if best:
            bits.append(f"Best: {best[0].name} (VORP {best[0].vorp:+.0f})")
        if steal and steal[1] >= 8:
            bits.append(f"Steal: {steal[0].name} "
                        f"(pick {steal[2]}, ADP {steal[0].adp:.0f})")
        if reach and reach[1] >= 8:
            bits.append(f"Reach: {reach[0].name} "
                        f"(pick {reach[2]}, ADP {reach[0].adp:.0f})")
        return "      ".join(bits)

    def _update_grade_summary(self):
        rows = self._last_rankings or self._compute_power_rankings()
        rid = self._team_label_to_rid.get(self.team_pick_var.get())
        if not rows or rid is None:
            self.grade_summary_var.set(
                "Draft grades appear once picks are in "
                "(turn on sync or click Refresh).")
            return
        me = next((x for x in rows if str(x["rid"]) == str(rid)), None)
        if not me:
            self.grade_summary_var.set("")
            return
        head = (f"#{me['rank']} of {len(rows)}    \u00b7    Grade {me['grade']}"
                f"    \u00b7    Value {me['value']:+.0f}"
                f"    \u00b7    {me['n']} picks")
        detail = self._team_grade_detail(rid)
        self.grade_summary_var.set(head + (("\n" + detail) if detail else ""))

    def _refresh_teams(self):
        sl = self.sleeper or {}
        draft_id = sl.get("draft_id")
        league_id = sl.get("league_id")
        if not draft_id:
            self.teams_status_var.set(
                "Link your Sleeper league in \u2699 League Settings first.")
            return
        self.teams_status_var.set("Loading teams\u2026")
        q = self._teams_refresh_queue

        def worker():
            try:
                labels = sleeper.team_labels(league_id) if league_id else {}
                picks = sleeper.get_draft_picks(draft_id)
                meta = sleeper.draft_meta(sleeper.get_draft(draft_id))
                q.put(("ok", labels, picks, meta))
            except Exception as e:
                q.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_teams_refresh)

    def _poll_teams_refresh(self):
        try:
            msg = self._teams_refresh_queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_teams_refresh)
            return
        if msg[0] == "err":
            self.teams_status_var.set(f"Couldn't load teams: {msg[1]}")
            return
        _, labels, picks, meta = msg
        if labels:
            self._set_team_labels(labels)
        self._draft_picks_raw = picks or []
        if meta:
            self._draft_meta = meta
        self._render_teams_view()
        self._update_draft_pulse()
        self.teams_status_var.set(
            f"{len(self._draft_picks_raw)} picks \u00b7 "
            f"{len(self._team_labels)} teams")

    # ------------------------------------------------------------- season tab
    def _tree_in(self, parent, columns, headings, tree_h=None):
        """Helper: a scrollable treeview inside a card frame. Returns the tree."""
        wrap = tk.Frame(parent, bg=C_CARD)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        tree = ttk.Treeview(wrap, columns=columns, show="tree headings",
                            selectmode="browse", height=tree_h)
        for col, (lbl, w, anc, st) in headings.items():
            key = "#0" if col == "#0" else col
            tree.heading(key, text=lbl)
            tree.column(key, width=w, anchor=anc, stretch=st)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return tree

    def _build_season_tab(self, parent):
        # control bar
        bar = tk.Frame(parent, bg=C_BG)
        bar.pack(side="top", fill="x", pady=(8, 6))
        ttk.Label(bar, textvariable=self.season_status_var,
                  style="Status.TLabel").pack(side="left", padx=(2, 0))
        ttk.Button(bar, text="\u21BB Refresh", style="Accent.TButton",
                   command=lambda: self._load_season(force=True)).pack(side="right")

        self.snb = ttk.Notebook(parent)
        self.snb.pack(side="top", fill="both", expand=True)
        pg_waivers = tk.Frame(self.snb, bg=C_BG)
        pg_team = tk.Frame(self.snb, bg=C_BG)
        pg_matchup = tk.Frame(self.snb, bg=C_BG)
        pg_league = tk.Frame(self.snb, bg=C_BG)
        pg_trade = tk.Frame(self.snb, bg=C_BG)
        pg_news = tk.Frame(self.snb, bg=C_BG)
        self.snb.add(pg_waivers, text="  Waivers & FAAB  ")
        self.snb.add(pg_team, text="  My Team  ")
        self.snb.add(pg_matchup, text="  Matchup  ")
        self.snb.add(pg_league, text="  League  ")
        self.snb.add(pg_trade, text="  Trade  ")
        self.snb.add(pg_news, text="  News & Injuries  ")

        self._build_waivers_page(pg_waivers)
        self._build_myteam_page(pg_team)
        self._build_matchup_page(pg_matchup)
        self._build_league_page(pg_league)
        self._build_trade_page(pg_trade)
        self._build_news_page(pg_news)

        trees = [self.news_tree, self.inj_tree, self.fa_tree, self.faab_tree,
                 self.str_tree, self.myroster_tree, self.me_tree, self.opp_tree,
                 self.standings_tree, self.power_tree,
                 self.trade_me_tree, self.trade_opp_tree]
        for t in trees:
            self._apply_row_tags(t)
            t.tag_configure("group", font=("Segoe UI Semibold", 10, "bold"),
                            foreground=C_TEXT, background=C_HEAD)
            t.tag_configure("mine_row", background=C_TOPPICK,
                            font=("Segoe UI", 10, "bold"))
            t.tag_configure("st_out", foreground="#e5484d")
            t.tag_configure("st_warn", foreground=C_WARN)
            t.tag_configure("need", foreground=C_ACCENT)
            t.tag_configure("swap", foreground=C_ACCENT2,
                            font=("Segoe UI", 10, "bold"))

    def _build_waivers_page(self, parent):
        top = tk.Frame(parent, bg=C_BG)
        top.pack(side="top", fill="x", pady=(8, 2), padx=2)
        tk.Label(top, textvariable=self.faab_status_var, bg=C_BG, fg=C_GOLD,
                 font=("Segoe UI Semibold", 11, "bold")).pack(side="left")
        ttk.Checkbutton(top, text="Only fills my needs",
                        variable=self.waiver_needs_only, style="TCheckbutton",
                        command=self._render_waivers).pack(side="right", padx=6)

        split = tk.PanedWindow(parent, orient="horizontal", bg=C_BG,
                               sashwidth=8, bd=0, sashrelief="flat")
        split.pack(side="top", fill="both", expand=True)
        left = tk.Frame(split, bg=C_PANEL)
        right = tk.Frame(split, bg=C_PANEL)
        split.add(left, minsize=440, width=620, stretch="always")
        split.add(right, minsize=300, width=360, stretch="always")

        self._section_header(
            left, "\U0001F4C8 Available in Your League", C_ACCENT2,
            hint="most-added free agents \u00b7 needs starred \u00b7 suggested bid").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.fa_tree = self._tree_in(
            left, ("pos", "team", "bye", "adds", "bid"), {
                "#0": ("Player", 180, "w", True),
                "pos": ("Pos", 48, "center", False),
                "team": ("Tm", 46, "center", False),
                "bye": ("Bye", 44, "center", False),
                "adds": ("Adds", 78, "e", False),
                "bid": ("Sugg. Bid", 120, "w", False)})
        self.fa_tree.bind("<<TreeviewSelect>>", self._on_fa_select)

        self._section_header(right, "\U0001F501 Add / Drop", C_ACCENT).pack(
            anchor="w", fill="x", padx=12, pady=(10, 4))
        tk.Label(right, textvariable=self.addrop_var, bg=C_PANEL, fg=C_TEXT,
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=330).pack(fill="x", padx=14, pady=(0, 8))
        self._section_header(right, "\U0001F4B0 Recent FAAB Bids", C_GOLD,
                             hint="what winning bids cost").pack(
            anchor="w", fill="x", padx=12, pady=(4, 6))
        self.faab_tree = self._tree_in(
            right, ("pos", "mgr", "bid"), {
                "#0": ("Player", 130, "w", True),
                "pos": ("Pos", 46, "center", False),
                "mgr": ("Mgr", 110, "w", True),
                "bid": ("Bid", 60, "e", False)})

    def _build_myteam_page(self, parent):
        split = tk.PanedWindow(parent, orient="horizontal", bg=C_BG,
                               sashwidth=8, bd=0, sashrelief="flat")
        split.pack(side="top", fill="both", expand=True)
        left = tk.Frame(split, bg=C_PANEL)
        right = tk.Frame(split, bg=C_PANEL)
        split.add(left, minsize=420, width=600, stretch="always")
        split.add(right, minsize=300, width=360, stretch="always")

        self._section_header(
            left, "\U0001F4CB My Roster", C_ACCENT2,
            hint="live from Sleeper \u00b7 score = value + Vegas matchup").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.myroster_tree = self._tree_in(
            left, ("pos", "team", "opp", "imp", "score"), {
                "#0": ("Player", 170, "w", True),
                "pos": ("Pos", 48, "center", False),
                "team": ("Tm", 46, "center", False),
                "opp": ("Opp", 66, "center", False),
                "imp": ("Impl", 52, "center", False),
                "score": ("Score", 60, "e", False)})

        self._section_header(right, "\U0001F504 Start / Sit", C_ACCENT).pack(
            anchor="w", fill="x", padx=12, pady=(10, 4))
        tk.Label(right, textvariable=self.startsit_var, bg=C_PANEL, fg=C_TEXT,
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=330).pack(fill="x", padx=14, pady=(0, 8))
        self._section_header(right, "\U0001F3AF Streaming K/DEF", C_GOLD,
                             hint="from Vegas odds").pack(
            anchor="w", fill="x", padx=12, pady=(4, 6))
        self.str_tree = self._tree_in(
            right, ("matchup", "metric"), {
                "#0": ("Team", 80, "w", False),
                "matchup": ("Matchup", 110, "w", True),
                "metric": ("Implied", 74, "center", False)}, tree_h=8)
        self._section_header(right, "\U0001F634 Bye Weeks", C_WARN).pack(
            anchor="w", fill="x", padx=12, pady=(4, 4))
        tk.Label(right, textvariable=self.bye_var, bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=330).pack(fill="x", padx=14, pady=(0, 10))

    def _build_matchup_page(self, parent):
        tk.Label(parent, textvariable=self.matchup_var, bg=C_BG, fg=C_GOLD,
                 font=("Segoe UI Semibold", 12, "bold")).pack(
            side="top", anchor="w", padx=14, pady=(10, 4))
        body = tk.Frame(parent, bg=C_BG)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="mu")
        body.columnconfigure(1, weight=1, uniform="mu")
        body.rowconfigure(0, weight=1)
        cols = {"#0": ("Player", 170, "w", True),
                "pos": ("Pos", 48, "center", False),
                "opp": ("Opp", 70, "center", False),
                "pts": ("Pts", 56, "e", False)}
        lf = tk.Frame(body, bg=C_PANEL)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._section_header(lf, "\u2B50 You", C_ACCENT2).pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.me_tree = self._tree_in(lf, ("pos", "opp", "pts"), cols)
        rf = tk.Frame(body, bg=C_PANEL)
        rf.grid(row=0, column=1, sticky="nsew")
        self._section_header(rf, "\U0001F94A Opponent", "#e5484d").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.opp_tree = self._tree_in(rf, ("pos", "opp", "pts"), cols)

    def _build_league_page(self, parent):
        split = tk.PanedWindow(parent, orient="horizontal", bg=C_BG,
                               sashwidth=8, bd=0, sashrelief="flat")
        split.pack(side="top", fill="both", expand=True)
        left = tk.Frame(split, bg=C_PANEL)
        right = tk.Frame(split, bg=C_PANEL)
        split.add(left, minsize=380, width=520, stretch="always")
        split.add(right, minsize=300, width=380, stretch="always")

        self._section_header(left, "\U0001F3C5 Standings", C_ACCENT2,
                             hint="record \u00b7 points for").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.standings_tree = self._tree_in(
            left, ("rec", "pf", "pa"), {
                "#0": ("Team", 190, "w", True),
                "rec": ("W-L-T", 80, "center", False),
                "pf": ("PF", 74, "e", False),
                "pa": ("PA", 74, "e", False)})

        self._section_header(right, "\U0001F4AA Power Rankings", C_GOLD,
                             hint="by current roster value (VORP)").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.power_tree = self._tree_in(
            right, ("value", "n"), {
                "#0": ("Team", 190, "w", True),
                "value": ("Value", 74, "e", False),
                "n": ("Plrs", 50, "center", False)})

    def _build_trade_page(self, parent):
        top = tk.Frame(parent, bg=C_BG)
        top.pack(side="top", fill="x", pady=(8, 2), padx=4)
        ttk.Label(top, text="Trade with:", style="Status.TLabel").pack(
            side="left", padx=(0, 6))
        self.trade_combo = ttk.Combobox(top, textvariable=self.trade_opp_var,
                                        state="readonly", width=30, values=[])
        self.trade_combo.pack(side="left")
        self.trade_combo.bind("<<ComboboxSelected>>",
                              lambda e: self._on_trade_opp_change())
        ttk.Label(top,
                  text="  \u2013  Ctrl/Shift-click players on each side to include",
                  style="Status.TLabel").pack(side="left", padx=(10, 0))

        body = tk.Frame(parent, bg=C_BG)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="tr")
        body.columnconfigure(1, weight=1, uniform="tr")
        body.rowconfigure(0, weight=1)
        cols = {"#0": ("Player", 170, "w", True),
                "pos": ("Pos", 48, "center", False),
                "team": ("Tm", 46, "center", False),
                "vorp": ("VORP", 62, "e", False)}
        lf = tk.Frame(body, bg=C_PANEL)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._section_header(lf, "\u2B50 You send", C_ACCENT2).pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.trade_me_tree = self._tree_in(lf, ("pos", "team", "vorp"), cols)
        self.trade_me_tree.configure(selectmode="extended")
        self.trade_me_tree.bind("<<TreeviewSelect>>",
                                lambda e: self._eval_trade())
        rf = tk.Frame(body, bg=C_PANEL)
        rf.grid(row=0, column=1, sticky="nsew")
        self._section_header(rf, "\U0001F91D You receive", "#e5484d").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.trade_opp_tree = self._tree_in(rf, ("pos", "team", "vorp"), cols)
        self.trade_opp_tree.configure(selectmode="extended")
        self.trade_opp_tree.bind("<<TreeviewSelect>>",
                                 lambda e: self._eval_trade())

        res = tk.Frame(parent, bg=C_PANEL)
        res.pack(side="top", fill="x")
        tk.Label(res, textvariable=self.trade_result_var, bg=C_PANEL, fg=C_GOLD,
                 font=("Segoe UI Semibold", 11, "bold"), anchor="w",
                 justify="left", wraplength=900).pack(
            fill="x", padx=14, pady=(8, 10))

    def _build_news_page(self, parent):
        bar = tk.Frame(parent, bg=C_BG)
        bar.pack(side="top", fill="x", pady=(6, 2), padx=2)
        ttk.Checkbutton(bar, text="Only my players", variable=self.inj_mine_only,
                        style="TCheckbutton",
                        command=self._render_injuries).pack(side="right", padx=10)
        ttk.Checkbutton(bar, text="Skill positions only",
                        variable=self.inj_fantasy_only, style="TCheckbutton",
                        command=self._render_injuries).pack(side="right", padx=4)
        body = tk.Frame(parent, bg=C_BG)
        body.pack(side="top", fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="ni")
        body.columnconfigure(1, weight=1, uniform="ni")
        body.rowconfigure(0, weight=1)
        p_news = tk.Frame(body, bg=C_PANEL)
        p_news.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._section_header(p_news, "\U0001F4F0 NFL News", C_ACCENT,
                             hint="double-click to open").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.news_tree = self._tree_in(
            p_news, ("date",), {
                "#0": ("Headline", 320, "w", True),
                "date": ("Date", 90, "center", False)})
        self.news_tree.bind("<Double-1>", self._open_news_link)

        p_inj = tk.Frame(body, bg=C_PANEL)
        p_inj.grid(row=0, column=1, sticky="nsew")
        self._section_header(p_inj, "\U0001FA79 Injury Report", "#e5484d",
                             hint="your players highlighted").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        self.inj_tree = self._tree_in(
            p_inj, ("pos", "team", "status", "note"), {
                "#0": ("Player", 150, "w", True),
                "pos": ("Pos", 46, "center", False),
                "team": ("Tm", 46, "center", False),
                "status": ("Status", 90, "center", False),
                "note": ("Detail", 200, "w", True)})

    def _on_tab_changed(self, event):
        try:
            tab = self.nb.tab(self.nb.select(), "text")
        except tk.TclError:
            return
        if "Season" in tab and not self._season_loaded:
            self._load_season()
        if "Teams" in tab and not self._draft_picks_raw and not self._sync_running:
            self._refresh_teams()

    # ---- season data loading ----------------------------------------------
    def _load_season(self, force=False):
        self.season_status_var.set("Loading season data\u2026")

        def worker():
            res = {}
            try:
                res["state"] = season.get_state(force=force)
            except Exception as e:
                res["state_err"] = str(e)
            for key, fn in (
                ("news", lambda: season.get_news(20, force=force)[0]),
                ("injuries", lambda: season.get_injuries(force=force)[0]),
                ("pickups", lambda: season.get_pickups(25, force=force)[0]),
            ):
                try:
                    res[key] = fn()
                except Exception as e:
                    res[key + "_err"] = str(e)
            try:
                games, _, wk = season.get_matchups(force=force)
                res["games"], res["week"] = games, wk
                res["streamers"] = season.best_streamers(games)
            except Exception as e:
                res["games_err"] = str(e)

            # --- league-aware data (rosters, FAAB, matchup) --------------
            sl = self.sleeper or {}
            lid = sl.get("league_id")
            my_rid = sl.get("roster_id")
            if lid:
                try:
                    res["player_map"] = season.get_player_map(force=force)
                except Exception as e:
                    res["player_map_err"] = str(e)
                try:
                    res["league"] = sleeper.get_league(lid)
                    res["rosters"] = sleeper.get_rosters(lid)
                    res["labels"] = sleeper.team_labels(lid)
                except Exception as e:
                    res["league_err"] = str(e)
                # current NFL week for matchup + transactions
                stt = res.get("state") or {}
                cur = int(stt.get("week") or stt.get("display_week") or 1)
                res["nfl_week"] = cur
                try:
                    res["matchups"] = sleeper.get_matchups(lid, cur)
                except Exception as e:
                    res["matchups_err"] = str(e)
                try:
                    tx = []
                    for w in range(max(1, cur - 3), cur + 1):
                        tx.extend(sleeper.get_transactions(lid, w))
                    res["transactions"] = tx
                except Exception as e:
                    res["transactions_err"] = str(e)
            self._season_queue.put(res)

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_season)

    def _poll_season(self):
        try:
            res = self._season_queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_season)
            return
        self._season_data = res
        self._season_loaded = True
        self._render_news()
        self._render_injuries()
        self._update_faab_header()
        self._render_waivers()
        self._render_faab_tracker()
        self._render_streaming()
        self._render_myroster()
        self._render_matchup()
        self._render_league()
        self._render_trade()
        label = season.state_label(res.get("state", {}))
        errs = [k.replace("_err", "") for k in res if k.endswith("_err")]
        msg = label or "Season data loaded."
        if errs:
            msg += f"   (couldn't load: {', '.join(errs)})"
        self.season_status_var.set(msg)

    def _my_roster_obj(self) -> dict:
        """The Sleeper roster dict for my team, from loaded season data."""
        my_rid = (self.sleeper or {}).get("roster_id")
        for r in self._season_data.get("rosters", []) or []:
            if str(r.get("roster_id")) == str(my_rid):
                return r
        return {}

    # ---- roster-aware helpers ---------------------------------------------
    def _my_norm_names(self) -> set:
        if not self.draft:
            return set()
        return {season.norm_name(p.name) for p in self.draft.my_players()}

    def _my_need_positions(self) -> set:
        if not self.draft:
            return set()
        out = set()
        expand = {"FLEX": {"RB", "WR", "TE"},
                  "SFLX": {"QB", "RB", "WR", "TE"}}
        for label in self.draft.needs_summary():
            base = label.split("\u00d7")[-1]
            out |= expand.get(base, {base})
        return out

    # ---- season rendering -------------------------------------------------
    def _render_news(self):
        self.news_tree.delete(*self.news_tree.get_children())
        self._news_links = {}
        for i, n in enumerate(self._season_data.get("news", [])):
            stripe = "row_odd" if i % 2 else "row_even"
            iid = f"news:{i}"
            self._news_links[iid] = n.get("link", "")
            self.news_tree.insert("", "end", iid=iid, text="  " + n["headline"],
                                  values=(n.get("published", "")[:10],),
                                  tags=(stripe,))

    def _open_news_link(self, event):
        sel = self.news_tree.selection()
        if not sel:
            return
        link = getattr(self, "_news_links", {}).get(sel[0], "")
        if link:
            webbrowser.open(link)

    def _render_injuries(self):
        if not hasattr(self, "inj_tree"):
            return
        self.inj_tree.delete(*self.inj_tree.get_children())
        mine = self._my_norm_names()
        skill = {"QB", "RB", "WR", "TE", "K", "PK"}
        i = 0
        for r in self._season_data.get("injuries", []):
            if self.inj_fantasy_only.get() and r["pos"] not in skill:
                continue
            is_mine = season.norm_name(r["name"]) in mine
            if self.inj_mine_only.get() and not is_mine:
                continue
            stripe = "row_odd" if i % 2 else "row_even"
            status = r["status"]
            tags = [stripe]
            if is_mine:
                tags = ["mine_row"]
            elif status in ("Out", "Injured Reserve", "Doubtful"):
                tags.append("st_out")
            elif status in ("Questionable", "Day-To-Day"):
                tags.append("st_warn")
            detail = r["type"]
            if r["note"]:
                detail = (r["type"] + " \u2014 " + r["note"]) if r["type"] else r["note"]
            mark = "\u2605 " if is_mine else "  "
            pos = "K" if r["pos"] == "PK" else r["pos"]
            status_disp = {"Injured Reserve": "IR", "Day-To-Day": "DTD"}.get(
                status, status)
            self.inj_tree.insert("", "end", text=mark + r["name"],
                                 values=(pos, r["team"], status_disp, detail),
                                 tags=tuple(tags))
            i += 1
        if i == 0:
            self.inj_tree.insert("", "end", text="  (no injuries to show)",
                                 values=("", "", "", ""), tags=("empty",))

    # ---- FAAB / waivers ---------------------------------------------------
    def _faab_left(self) -> int:
        st = season.faab_status(self._season_data.get("league", {}),
                                self._my_roster_obj())
        return st.get("left", 0)

    def _update_faab_header(self):
        data = self._season_data
        if not data.get("league"):
            self.faab_status_var.set(
                "Link your Sleeper league (\u2699 League Settings) to see "
                "FAAB + who's free in your league.")
            return
        st = season.faab_status(data["league"], self._my_roster_obj())
        wp = st.get("waiver_position")
        wp_txt = f"    \u00b7    waiver #{wp}" if wp else ""
        self.faab_status_var.set(
            f"FAAB:  ${st['left']} left of ${st['budget']}    "
            f"(${st['used']} spent){wp_txt}")

    def _render_waivers(self):
        if not hasattr(self, "fa_tree"):
            return
        self.fa_tree.delete(*self.fa_tree.get_children())
        data = self._season_data
        pickups = data.get("pickups", []) or []
        rostered = season.rostered_ids(data.get("rosters", []) or [])
        needs = self._my_need_positions()
        bridge = self._sync_bridge or {}
        fas = season.free_agents(pickups, rostered, needs, bridge, limit=50)
        if self.waiver_needs_only.get():
            fas = [f for f in fas if f["fills_need"]]
        self._fa_rows = {}
        faab = self._faab_left()
        if not fas:
            self.fa_tree.insert(
                "", "end", text="  (no free agents to show yet)",
                values=("", "", "", "", ""), tags=("empty",))
            return
        for i, f in enumerate(fas):
            iid = f"fa:{f['player_id']}"
            f["rank"] = i
            f["has_value"] = (f.get("vorp") or 0) > 0
            self._fa_rows[iid] = f
            stripe = "row_odd" if i % 2 else "row_even"
            pos = f["pos"]
            tags = [stripe]
            if pos in POS_COLORS:
                tags.append(f"pos_{pos}")
            mark = "\u2605 " if f["fills_need"] else "  "
            code = injury_code(f["injury_status"])
            nm = mark + (f["name"] or "?") + (f"  {code}" if code else "")
            bid = season.suggest_bid(i, faab, f["fills_need"], f["has_value"])
            bidtxt = (f"${bid['dollars']} \u00b7 {bid['tier']}"
                      if bid["dollars"] else "\u2014")
            self.fa_tree.insert(
                "", "end", iid=iid, text=nm,
                values=(pos, f["team"], f["bye"] or "-",
                        f"+{f['count']:,}" if f["count"] else "", bidtxt),
                tags=tuple(tags))

    def _my_drop_candidates(self):
        bridge = self._sync_bridge or {}
        roster = self._my_roster_obj()
        engs = [bridge.get(str(pid)) for pid in (roster.get("players") or [])]
        engs = [e for e in engs if e]
        if not engs and self.draft:
            engs = self.draft.my_players()
        return season.drop_candidates(engs, n=6)

    def _on_fa_select(self, event):
        sel = self.fa_tree.selection()
        fa = getattr(self, "_fa_rows", {}).get(sel[0]) if sel else None
        if not fa:
            return
        faab = self._faab_left()
        bid = season.suggest_bid(fa.get("rank", 0), faab, fa["fills_need"],
                                 fa.get("has_value", False))
        lines = [f"ADD   {fa['name']}  ({fa['pos']} \u00b7 {fa['team']})",
                 f"Suggested bid:  ${bid['dollars']} of ${faab}   "
                 f"({bid['tier']}, {bid['pct']*100:.0f}% of budget)"]
        drops = self._my_drop_candidates()
        if drops:
            d = drops[0]
            inj = injury_code(getattr(d, "injury_status", ""))
            extra = f"  \u2014 {inj}" if inj else ""
            if getattr(d, "vorp", None) is not None:
                extra += f"   VORP {d.vorp:+.0f}"
            lines.append(f"DROP  {d.name}  ({d.position}){extra}")
        else:
            lines.append("DROP  (roster fills in after your draft)")
        self.addrop_var.set("\n".join(lines))

    def _render_faab_tracker(self):
        if not hasattr(self, "faab_tree"):
            return
        self.faab_tree.delete(*self.faab_tree.get_children())
        data = self._season_data
        rows = season.parse_transactions(data.get("transactions", []),
                                         data.get("player_map", {}),
                                         data.get("labels", {}))
        if not rows:
            self.faab_tree.insert("", "end", text="  (no waiver bids yet)",
                                  values=("", "", ""), tags=("empty",))
            return
        for i, r in enumerate(rows[:40]):
            stripe = "row_odd" if i % 2 else "row_even"
            pos = r["pos"] or "?"
            tags = [stripe]
            if pos in POS_COLORS:
                tags.append(f"pos_{pos}")
            bid = f"${r['bid']}" if r["bid"] is not None else "\u2014"
            self.faab_tree.insert("", "end", text="  " + r["name"],
                                  values=(pos, r["manager"], bid),
                                  tags=tuple(tags))

    # ---- my roster / start-sit / byes -------------------------------------
    def _render_myroster(self):
        if not hasattr(self, "myroster_tree"):
            return
        self.myroster_tree.delete(*self.myroster_tree.get_children())
        data = self._season_data
        roster = self._my_roster_obj()
        bridge = self._sync_bridge or {}
        if not roster or not roster.get("players"):
            self.myroster_tree.insert(
                "", "end", text="  (your roster fills in after the draft)",
                values=("", "", "", "", ""), tags=("empty",))
            self.startsit_var.set("")
            self.bye_var.set("")
            return
        rows = season.roster_detail(roster, data.get("player_map", {}), bridge,
                                    data.get("games", []))
        groups = [
            ("STARTERS", [r for r in rows if r["is_starter"]]),
            ("BENCH", [r for r in rows
                       if not r["is_starter"] and not r["is_reserve"]]),
            ("IR", [r for r in rows if r["is_reserve"]])]
        for gname, grp in groups:
            if not grp:
                continue
            self.myroster_tree.insert("", "end", text=f"  {gname}",
                                      values=("", "", "", "", ""),
                                      tags=("group",))
            for i, r in enumerate(sorted(grp, key=lambda x: -x["score"])):
                stripe = "row_odd" if i % 2 else "row_even"
                pos = r["pos"]
                tags = [stripe]
                if pos in POS_COLORS:
                    tags.append(f"pos_{pos}")
                code = injury_code(r["injury_status"])
                nm = "  " + r["name"] + (f"  {code}" if code else "")
                opp = (f"{r['homeaway']} {r['opp']}".strip()
                       if r["opp"] else "-")
                imp = f"{r['implied']:.0f}" if r["implied"] is not None else "-"
                self.myroster_tree.insert(
                    "", "end", text=nm,
                    values=(pos, r["team"], opp, imp, f"{r['score']:.0f}"),
                    tags=tuple(tags))
        sugg = season.start_sit(rows)
        if sugg:
            lines = [f"START {s['start']['name']} ({s['start']['pos']}) "
                     f"over {s['sit']['name']}   (+{s['delta']:.0f})"
                     for s in sugg[:4]]
            self.startsit_var.set("\n".join(lines))
        else:
            self.startsit_var.set(
                "Lineup looks set for the matchups we can see.")
        engs = [bridge.get(str(pid)) for pid in roster.get("players") or []]
        engs = [e for e in engs if e]
        byes = season.bye_report(engs, data.get("nfl_week", 0))
        if byes:
            parts = []
            for b in byes[:6]:
                pos_txt = ",".join(f"{n}{p}" for p, n in b["by_pos"].items())
                parts.append(f"Wk {b['week']}: {b['count']} ({pos_txt})")
            self.bye_var.set("   ".join(parts))
        else:
            self.bye_var.set("No upcoming byes on your roster.")

    # ---- weekly head-to-head matchup --------------------------------------
    def _render_matchup(self):
        if not hasattr(self, "me_tree"):
            return
        self.me_tree.delete(*self.me_tree.get_children())
        self.opp_tree.delete(*self.opp_tree.get_children())
        data = self._season_data
        my_rid = (self.sleeper or {}).get("roster_id")
        mine, opp = season.my_matchup(data.get("matchups", []) or [], my_rid)
        labels = data.get("labels", {})
        wk = data.get("nfl_week")
        if not mine:
            self.matchup_var.set(
                "Your weekly matchup appears once the league schedule is live.")
            self.me_tree.insert("", "end", text="  (no matchup yet)",
                                values=("", "", ""), tags=("empty",))
            return
        me_lbl = labels.get(str(my_rid), "You")
        opp_lbl = (labels.get(str(opp.get("roster_id")), "Opponent")
                   if opp else "(bye week)")
        mp = mine.get("points") or 0
        op = (opp.get("points") if opp else 0) or 0
        self.matchup_var.set(
            f"Week {wk}:   {me_lbl}  {mp:.1f}    vs    {opp_lbl}  {op:.1f}")
        pm = data.get("player_map", {})
        bridge = self._sync_bridge or {}
        games = data.get("games", [])

        def fill(tree, entry):
            rows = season.lineup_rows(entry, pm, bridge, games)
            if not rows:
                tree.insert("", "end", text="  (empty)",
                            values=("", "", ""), tags=("empty",))
                return
            for i, r in enumerate(rows):
                stripe = "row_odd" if i % 2 else "row_even"
                pos = r["pos"]
                tags = [stripe]
                if pos in POS_COLORS:
                    tags.append(f"pos_{pos}")
                tree.insert("", "end", text="  " + r["name"],
                            values=(pos, r["opp"] or "-", f"{r['points']:.1f}"),
                            tags=tuple(tags))

        fill(self.me_tree, mine)
        fill(self.opp_tree, opp)

    # ---- league standings / power rankings --------------------------------
    def _roster_engines_for(self, rid):
        bridge = self._sync_bridge or {}
        r = next((x for x in self._season_data.get("rosters", []) or []
                  if str(x.get("roster_id")) == str(rid)), {})
        return season.roster_engines(r, bridge)

    def _render_league(self):
        if not hasattr(self, "standings_tree"):
            return
        data = self._season_data
        rosters = data.get("rosters", []) or []
        labels = data.get("labels", {})
        my_rid = str((self.sleeper or {}).get("roster_id"))

        self.standings_tree.delete(*self.standings_tree.get_children())
        st = season.standings(rosters, labels)
        if not st:
            self.standings_tree.insert(
                "", "end", text="  (link your Sleeper league)",
                values=("", "", ""), tags=("empty",))
        for r in st:
            tags = (("mine_row",) if r["rid"] == my_rid
                    else (("row_odd",) if r["rank"] % 2 else ("row_even",)))
            star = "\u2605 " if r["rid"] == my_rid else ""
            rec = (f"{r['wins']}-{r['losses']}"
                   + (f"-{r['ties']}" if r["ties"] else ""))
            self.standings_tree.insert(
                "", "end", text=f"  {r['rank']}. {star}{r['label']}",
                values=(rec, f"{r['pf']:.1f}", f"{r['pa']:.1f}"), tags=tags)

        self.power_tree.delete(*self.power_tree.get_children())
        pr = season.power_rankings(rosters, labels, self._sync_bridge or {})
        if pr and any(r["value"] for r in pr):
            for r in pr:
                tags = (("mine_row",) if r["rid"] == my_rid
                        else (("row_odd",) if r["rank"] % 2 else ("row_even",)))
                star = "\u2605 " if r["rid"] == my_rid else ""
                self.power_tree.insert(
                    "", "end", text=f"  {r['rank']}. {star}{r['label']}",
                    values=(f"{r['value']:+.0f}", r["n"]), tags=tags)
        else:
            self.power_tree.insert(
                "", "end", text="  (fills in after the draft)",
                values=("", ""), tags=("empty",))

    # ---- trade analyzer ---------------------------------------------------
    def _render_trade(self):
        if not hasattr(self, "trade_me_tree"):
            return
        labels = self._season_data.get("labels", {})
        my_rid = str((self.sleeper or {}).get("roster_id"))
        self._trade_opp_label_to_rid = {}
        vals = []
        for rid, lbl in sorted(labels.items(),
                               key=lambda kv: (int(kv[0]) if kv[0].isdigit()
                                               else 9999)):
            if str(rid) == my_rid:
                continue
            self._trade_opp_label_to_rid[lbl] = rid
            vals.append(lbl)
        self.trade_combo.configure(values=vals)
        if self.trade_opp_var.get() not in self._trade_opp_label_to_rid and vals:
            self.trade_opp_var.set(vals[0])
        self._fill_trade_tree(self.trade_me_tree, my_rid)
        self._on_trade_opp_change()

    def _on_trade_opp_change(self):
        rid = self._trade_opp_label_to_rid.get(self.trade_opp_var.get())
        self._fill_trade_tree(self.trade_opp_tree, rid)
        self._eval_trade()

    def _fill_trade_tree(self, tree, rid):
        tree.delete(*tree.get_children())
        if rid is None:
            return
        engs = self._roster_engines_for(rid)
        if not engs:
            tree.insert("", "end", text="  (roster fills in after the draft)",
                        values=("", "", ""), tags=("empty",))
            return
        order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "PK": 4, "DEF": 5}
        for i, e in enumerate(sorted(
                engs, key=lambda x: (order.get(x.position, 9), -x.vorp))):
            stripe = "row_odd" if i % 2 else "row_even"
            pos = "K" if e.position == "PK" else e.position
            tags = [stripe]
            if pos in POS_COLORS:
                tags.append(f"pos_{pos}")
            code = injury_code(getattr(e, "injury_status", ""))
            nm = "  " + e.name + (f"  {code}" if code else "")
            tree.insert("", "end", iid=f"tr:{e.sleeper_id}", text=nm,
                        values=(pos, e.team, f"{e.vorp:+.0f}"), tags=tuple(tags))

    def _selected_sids(self, tree):
        return {iid[3:] for iid in tree.selection() if iid.startswith("tr:")}

    def _eval_trade(self):
        if not hasattr(self, "trade_me_tree"):
            return
        my_rid = str((self.sleeper or {}).get("roster_id"))
        opp_rid = self._trade_opp_label_to_rid.get(self.trade_opp_var.get())
        a_eng = self._roster_engines_for(my_rid)
        b_eng = self._roster_engines_for(opp_rid) if opp_rid else []
        res = season.trade_eval(a_eng, self._selected_sids(self.trade_me_tree),
                                b_eng, self._selected_sids(self.trade_opp_tree),
                                self.roster_config)
        parts = [res["verdict"]]
        if res["give_a"] or res["give_b"]:
            send = ", ".join(f"{e.name} ({e.vorp:+.0f})"
                             for e in res["give_a"]) or "\u2014"
            recv = ", ".join(f"{e.name} ({e.vorp:+.0f})"
                             for e in res["give_b"]) or "\u2014"
            parts.append(f"Send: {send}")
            parts.append(f"Get:  {recv}")
        if res["notes"]:
            parts.append("      ".join(res["notes"]))
        self.trade_result_var.set(
            parts[0] if len(parts) == 1 else "\n".join(parts))

    def _render_streaming(self):
        self.str_tree.delete(*self.str_tree.get_children())
        streamers = self._season_data.get("streamers")
        wk = self._season_data.get("week")
        if not streamers:
            self.str_tree.insert("", "end", text="  (no odds posted yet)",
                                 values=("", ""), tags=("empty",))
            return
        defenses, kickers = streamers
        wk_txt = f" \u2014 Week {wk}" if wk else ""

        my_def_teams = self._my_teams_for("DEF")
        my_k_teams = self._my_teams_for("PK")

        self.str_tree.insert("", "end", text="  BEST DEFENSES" + wk_txt,
                             values=("(opponent implied total)", ""),
                             tags=("group",))
        for i, d in enumerate(defenses[:6]):
            stripe = "row_odd" if i % 2 else "row_even"
            tags = ["mine_row"] if d["team"] in my_def_teams else [stripe]
            self.str_tree.insert("", "end", text="  " + d["team"],
                                 values=(f"{d['vs']} {d['opp']}",
                                         f"{d['opp_implied']:.1f}"),
                                 tags=tuple(tags))

        self.str_tree.insert("", "end", text="  BEST KICKERS" + wk_txt,
                             values=("(own implied total)", ""),
                             tags=("group",))
        for i, k in enumerate(kickers[:6]):
            stripe = "row_odd" if i % 2 else "row_even"
            tags = ["mine_row"] if k["team"] in my_k_teams else [stripe]
            self.str_tree.insert("", "end", text="  " + k["team"],
                                 values=(f"{k['vs']} {k['opp']}",
                                         f"{k['implied']:.1f}"),
                                 tags=tuple(tags))

    def _my_teams_for(self, position: str) -> set:
        if not self.draft:
            return set()
        return {p.team for p in self.draft.my_players() if p.position == position}

    # ------------------------------------------------------------- data loading
    def load_data(self, force=False, restore=False):
        # A reload rebuilds the player pool, so any running sync (and its
        # id-bridge, which points at the old Player objects) is now stale.
        self._stop_sync()
        self._sync_bridge = None
        scoring = SCORING_FORMATS[self.scoring_var.get()]
        teams = int(self.teams_var.get())
        self.status_var.set("Fetching latest ADP data..." if force or not self.players
                            else "Updating...")
        self._set_controls_state("disabled")

        def worker():
            try:
                players, meta, source = load_players(scoring, teams, force_refresh=force)
                self._queue.put(("ok", players, meta, source, restore))
            except Exception as e:
                self._queue.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._poll_queue)

    def _poll_queue(self):
        try:
            msg = self._queue.get_nowait()
        except queue.Empty:
            self.after(80, self._poll_queue)
            return

        self._set_controls_state("normal")
        if msg[0] == "err":
            self.status_var.set("Error loading data.")
            messagebox.showerror(
                "Data error",
                "Could not load draft data and no cache is available.\n\n"
                f"{msg[1]}\n\nCheck your internet connection and click Refresh.")
            return

        _, players, meta, source, restore = msg
        # preserve current picks (by id) across a settings/refresh reload
        prev_state = self.draft.state() if self.draft else None
        self.players = players
        self.meta = meta
        self.source = source

        self.draft = Draft(players, self._roster_copy(),
                           teams=int(self.teams_var.get()))

        if prev_state:
            self.draft.apply_state(prev_state)
        elif restore and os.path.exists(AUTOSAVE):
            self._try_restore_autosave()

        self.status_var.set(
            f"{cache_age_string(meta, source)}    "
            f"{len(players)} players loaded.")
        self._update_league_label()
        self.refresh_views()
        # Build the Sleeper id-bridge in the background so injury badges and
        # team grades are ready even before draft sync is turned on.
        if self.sleeper.get("league_id"):
            self._start_bridge_prebuild()

    def _try_restore_autosave(self):
        try:
            with open(AUTOSAVE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return
        picks = state.get("picks", [])
        if picks and state.get("teams") == self.draft.teams:
            self.draft.apply_state(state)

    def _set_controls_state(self, state):
        # avoid disabling everything; just give visual feedback via status
        pass

    # --------------------------------------------------------------- settings
    def on_settings_change(self):
        # If switching to a 2-QB/superflex format with a 1-QB roster, add a
        # superflex slot automatically so QB value is handled correctly.
        if (self.scoring_var.get().startswith("2-QB")
                and self.roster_config.superflex == 0
                and self.roster_config.qb <= 1):
            self.roster_config.superflex = 1
        self._save_league_settings()
        # reload data for new format/size; picks persist by id
        self.load_data(force=False)

    def _roster_copy(self) -> RosterConfig:
        return dataclasses.replace(self.roster_config)

    def _update_league_label(self):
        r = self.roster_config
        bits = [f"QB{r.qb}", f"RB{r.rb}", f"WR{r.wr}", f"TE{r.te}"]
        if r.flex:
            bits.append(f"FLEX{r.flex}")
        if r.superflex:
            bits.append(f"SF{r.superflex}")
        if r.k:
            bits.append(f"K{r.k}")
        if r.dst:
            bits.append(f"DEF{r.dst}")
        bits.append(f"BN{r.bench}")
        prefix = ""
        name = (self.sleeper or {}).get("league_name")
        if name:
            prefix = f"{name}  \u00b7  "
        self.league_var.set(prefix + "Lineup:  " + " ".join(bits)
                            + f"   ({r.total_roster} roster)")

    def _load_league_settings(self):
        try:
            with open(LEAGUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if data.get("scoring") in SCORING_FORMATS:
            self.scoring_var.set(data["scoring"])
        if data.get("teams") in TEAM_OPTIONS:
            self.teams_var.set(int(data["teams"]))
        if data.get("theme") in THEMES:
            self.theme_name = data["theme"]
        if isinstance(data.get("show_upside"), bool):
            self.show_upside_var.set(data["show_upside"])
        if isinstance(data.get("sleeper"), dict):
            self.sleeper = data["sleeper"]
        roster = data.get("roster")
        if isinstance(roster, dict):
            valid = {f.name for f in dataclasses.fields(RosterConfig)}
            self.roster_config = RosterConfig(
                **{k: int(v) for k, v in roster.items() if k in valid})

    def _save_league_settings(self):
        try:
            with open(LEAGUE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "scoring": self.scoring_var.get(),
                    "teams": int(self.teams_var.get()),
                    "theme": self.theme_name,
                    "show_upside": bool(self.show_upside_var.get()),
                    "sleeper": self.sleeper,
                    "roster": dataclasses.asdict(self.roster_config),
                }, f, indent=2)
        except Exception:
            pass

    def change_theme(self, name):
        """Switch the active color theme and rebuild the UI in place."""
        if name not in THEMES or name == self.theme_name:
            return
        self.theme_name = name
        self.theme_var.set(name)
        self._save_league_settings()
        self._rebuild_ui()

    def _rebuild_ui(self):
        apply_palette(self.theme_name)
        # destroy main-window content but leave any open Toplevel dialogs alone
        for w in list(self.winfo_children()):
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self._build_style()
        self._build_widgets()
        self._update_league_label()
        if self.draft:
            self.refresh_views()
        if self._season_loaded:
            self._render_news()
            self._render_injuries()
            self._update_faab_header()
            self._render_waivers()
            self._render_faab_tracker()
            self._render_streaming()
            self._render_myroster()
            self._render_matchup()
            self._render_league()
            self._render_trade()

    def open_settings(self):
        SettingsDialog(self)

    def apply_league_settings(self, scoring, teams, roster_config, theme=None):
        """Called by the settings dialog when the user clicks Apply."""
        scoring_changed = scoring != self.scoring_var.get()
        teams_changed = int(teams) != int(self.teams_var.get())
        theme_changed = theme is not None and theme != self.theme_name
        self.scoring_var.set(scoring)
        self.teams_var.set(int(teams))
        self.roster_config = roster_config
        if theme in THEMES:
            self.theme_name = theme
        self._save_league_settings()

        if scoring_changed or teams_changed or not self.draft:
            # need fresh ADP data for the new format/size (picks persist by id)
            if theme_changed:
                apply_palette(self.theme_name)
                self._rebuild_ui()
            self.load_data(force=False)
            return

        # roster changed: rebuild the draft in place, keep picks
        prev_state = self.draft.state() if self.draft else None
        self.draft = Draft(self.players, self._roster_copy(),
                           teams=int(self.teams_var.get()))
        if prev_state:
            self.draft.apply_state(prev_state)

        if theme_changed:
            apply_palette(self.theme_name)
            self._rebuild_ui()
        else:
            self._update_league_label()
            self.refresh_views()

    def set_filter(self, pos):
        self.filter_var.set(pos)
        self.refresh_views()

    # ----------------------------------------------------------- draft actions
    def _selected_player_id(self, tree):
        sel = tree.selection()
        if not sel:
            return None
        iid = sel[0]
        try:
            return int(iid.split(":")[-1])
        except ValueError:
            return None

    def draft_selected(self, who):
        if not self.draft:
            return
        pid = self._selected_player_id(self.tree)
        if pid is None:
            self.status_var.set("Select a player first.")
            return
        self.draft.mark(pid, who)
        self._autosave()
        self.refresh_views()

    def _draft_from_rec(self, event):
        pid = self._selected_player_id(self.rec_tree)
        if pid is not None and self.draft:
            self.draft.mark(pid, "me")
            self._autosave()
            self.refresh_views()

    def _draft_from_postree(self, event):
        pid = self._selected_player_id(self.pos_tree)
        if pid is not None and self.draft:
            # ask which team via quick: default to "other"; modifier? keep simple menu
            self._popup_choice(pid)

    def _popup_choice(self, pid):
        pl = self.draft.by_id.get(pid)
        if not pl:
            return
        win = tk.Toplevel(self)
        win.title("Draft player")
        win.configure(bg=C_PANEL)
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text=f"{pl.name} ({pl.display_pos})",
                  style="Head.TLabel").pack(padx=20, pady=(16, 10))
        btns = tk.Frame(win, bg=C_PANEL)
        btns.pack(padx=20, pady=(0, 16))

        def pick(who):
            self.draft.mark(pid, who)
            self._autosave()
            win.destroy()
            self.refresh_views()

        ttk.Button(btns, text="\u2705 My team", style="Accent.TButton",
                   command=lambda: pick("me")).pack(side="left", padx=6)
        ttk.Button(btns, text="\u274C Another team",
                   command=lambda: pick("other")).pack(side="left", padx=6)
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{x}+{y}")

    def _undraft_from_roster(self, event):
        pid = self._selected_player_id(self.roster_tree)
        if pid is not None and self.draft:
            pl = self.draft.by_id.get(pid)
            if pl and pl.drafted_by == "me":
                self.draft.mark(pid, "")
                self._autosave()
                self.refresh_views()

    def undo(self):
        if self.draft:
            pl = self.draft.undo()
            if pl:
                self.status_var.set(f"Undid: {pl.name}")
            self._autosave()
            self.refresh_views()

    def reset_draft(self):
        if not self.draft:
            return
        if messagebox.askyesno("Reset draft",
                               "Clear all drafted players and start over?"):
            self.draft.reset()
            self._autosave()
            self.refresh_views()

    # ----------------------------------------------------- Sleeper draft sync
    def _toggle_sync(self):
        if self.sync_var.get():
            self._start_sync()
        else:
            self._stop_sync()

    def _start_sync(self):
        if not self.draft:
            self.sync_var.set(False)
            return
        draft_id = (self.sleeper or {}).get("draft_id")
        if not draft_id:
            self.sync_status_var.set(
                "No Sleeper draft linked \u2014 import your league in "
                "\u2699 League Settings.")
            self.sync_var.set(False)
            return

        self._sync_stop = threading.Event()
        stop = self._sync_stop
        q = self._sync_queue
        players = self.players
        cached_bridge = self._sync_bridge
        my_rid = (self.sleeper or {}).get("roster_id")
        my_uid = (self.sleeper or {}).get("user_id")
        league_id = (self.sleeper or {}).get("league_id")

        def loop():
            try:
                bridge = cached_bridge or sleeper.build_id_bridge(players)
                q.put(("bridge", bridge))
            except Exception as e:
                q.put(("err", f"couldn't load player map: {e}"))
                return
            # Team names are static during a draft -> fetch once, not per tick.
            if league_id:
                try:
                    q.put(("labels", sleeper.team_labels(league_id)))
                except Exception:
                    pass
            # One metadata fetch up front so each poll only needs a single call
            # (just the picks). Completion is derived from the expected pick
            # count (rounds x teams), keeping us to ~60 calls/min at 1s.
            expected = 0
            try:
                d = sleeper.get_draft(draft_id)
                s = (d or {}).get("settings") or {}
                expected = int(s.get("rounds", 0) or 0) * int(s.get("teams", 0) or 0)
                q.put(("draftmeta", sleeper.draft_meta(d)))
            except Exception:
                expected = 0
            while not stop.is_set():
                try:
                    picks = sleeper.get_draft_picks(draft_id)
                    mapping, unmatched = sleeper.map_picks(
                        picks, bridge, my_rid, my_uid)
                    complete = bool(expected) and len(picks) >= expected
                    q.put(("picks", mapping, unmatched, len(picks),
                           "complete" if complete else "", picks))
                    if complete:
                        break
                except Exception as e:
                    q.put(("warn", str(e)))
                stop.wait(1.0)
            q.put(("stopped", None))

        self._sync_running = True
        self.sync_status_var.set("Sleeper: connecting\u2026")
        threading.Thread(target=loop, daemon=True).start()
        self.after(150, self._poll_sync)

    def _poll_sync(self):
        try:
            while True:
                msg = self._sync_queue.get_nowait()
                kind = msg[0]
                if kind == "bridge":
                    self._sync_bridge = msg[1]
                elif kind == "err":
                    self.sync_status_var.set(f"Sleeper sync error: {msg[1]}")
                    self._stop_sync()
                    return
                elif kind == "warn":
                    self.sync_status_var.set(
                        f"Sleeper: retrying\u2026 ({msg[1]})")
                elif kind == "labels":
                    self._set_team_labels(msg[1])
                elif kind == "draftmeta":
                    self._draft_meta = msg[1]
                    self._update_draft_pulse()
                elif kind == "picks":
                    _, mapping, unmatched, total, status, raw = msg
                    changed = self.draft.apply_external_picks(mapping)
                    self._draft_picks_raw = raw
                    if changed:
                        self._autosave()
                        self.refresh_views()
                    self._update_draft_pulse()
                    # only re-render the teams view when the pick count changed,
                    # so a live sync doesn't reset scroll while you're browsing.
                    if len(raw) != getattr(self, "_last_team_pick_count", -1):
                        self._last_team_pick_count = len(raw)
                        self._render_teams_view()
                    extra = f" \u00b7 {unmatched} unmatched" if unmatched else ""
                    done = " \u00b7 draft complete" if status == "complete" else ""
                    self.sync_status_var.set(
                        f"Sleeper: {total} picks synced{extra}{done}")
                elif kind == "stopped":
                    self._sync_running = False
                    if self.sync_var.get():
                        self.sync_var.set(False)
        except queue.Empty:
            pass
        if self._sync_running:
            self.after(400, self._poll_sync)

    def _stop_sync(self):
        if self._sync_stop is not None:
            self._sync_stop.set()
        self._sync_running = False
        if self.sync_var.get():
            self.sync_var.set(False)
        if self.sync_status_var.get().startswith("Sleeper: connecting"):
            self.sync_status_var.set("")

    def _update_draft_pulse(self):
        """Recompute the live 'draft pulse' (on the clock / runs / likely-gone)."""
        meta = self._draft_meta
        if not meta or not meta.get("teams"):
            self.pulse_clock_var.set("")
            self.pulse_run_var.set("")
            self.pulse_gone_var.set("")
            return
        picks = self._draft_picks_raw
        p = len(picks)
        my_rid = (self.sleeper or {}).get("roster_id")
        total = (meta.get("teams") * meta.get("rounds")
                 if meta.get("rounds") else None)
        until = sleeper.picks_until_my_turn(meta, p, my_rid)

        if total and p >= total:
            self.pulse_clock_var.set("\u2714 Draft complete")
        else:
            on_slot = sleeper.slot_on_clock(meta, p)
            on_rid = (meta.get("slot_to_roster_id") or {}).get(str(on_slot))
            on_label = self._team_labels.get(str(on_rid), f"Team {on_rid}")
            if until == 0:
                self.pulse_clock_var.set("\u23F0  YOU'RE ON THE CLOCK")
            elif until is None:
                self.pulse_clock_var.set(f"On the clock:  {on_label}")
            else:
                s = "s" if until != 1 else ""
                self.pulse_clock_var.set(
                    f"On the clock:  {on_label}        "
                    f"You're up in {until} pick{s}")

        runs = sleeper.positional_runs(picks, 6)
        self.pulse_run_var.set(
            "Run watch:    " + "     ".join(f"{pos} ({n} of last 6)"
                                            for pos, n in runs)
            if runs else "")

        if until and until > 0 and self.draft:
            names = [pl.name for pl in self.draft.best_available(n=until)][:8]
            self.pulse_gone_var.set(
                "Likely gone before your pick:  " + ", ".join(names)
                if names else "")
        else:
            self.pulse_gone_var.set("")

        now_up = (until == 0) and not (total and p >= total)
        if now_up and not self._was_on_clock:
            try:
                self.bell()
            except Exception:
                pass
        self._was_on_clock = now_up

    def _start_bridge_prebuild(self):
        """Build the Sleeper id-bridge in the background after a data load, so
        injury badges and team grades are ready without waiting for sync."""
        players = self.players
        if not players:
            return
        q = queue.Queue()

        def worker():
            try:
                q.put(sleeper.build_id_bridge(players))
            except Exception:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                b = q.get_nowait()
            except queue.Empty:
                self.after(250, poll)
                return
            if b:
                self._sync_bridge = b
                self.refresh_views()            # surface injury badges
                if self._draft_picks_raw:
                    self._render_teams_view()    # grades now computable

        self.after(250, poll)

    # ------------------------------------------------------------ save / load
    def _autosave(self):
        if not self.draft:
            return
        try:
            state = self.draft.state()
            state["scoring"] = self.scoring_var.get()
            with open(AUTOSAVE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass

    def save_draft(self):
        if not self.draft:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialdir=APP_DIR,
            filetypes=[("Draft files", "*.json")], title="Save draft")
        if not path:
            return
        state = self.draft.state()
        state["scoring"] = self.scoring_var.get()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        self.status_var.set(f"Saved to {os.path.basename(path)}")

    def load_draft(self):
        path = filedialog.askopenfilename(
            initialdir=APP_DIR, filetypes=[("Draft files", "*.json")],
            title="Load draft")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return
        if state.get("scoring") in SCORING_FORMATS:
            self.scoring_var.set(state["scoring"])
        if state.get("teams"):
            self.teams_var.set(state["teams"])
        if isinstance(state.get("roster"), dict):
            valid = {f.name for f in dataclasses.fields(RosterConfig)}
            self.roster_config = RosterConfig(
                **{k: int(v) for k, v in state["roster"].items() if k in valid})
        # reload data for those settings, then apply picks
        scoring = SCORING_FORMATS[self.scoring_var.get()]
        teams = int(self.teams_var.get())

        def worker():
            try:
                players, meta, source = load_players(scoring, teams)
                self._queue.put(("loadstate", players, meta, source, state))
            except Exception as e:
                self._queue.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._poll_load_queue)

    def _poll_load_queue(self):
        try:
            msg = self._queue.get_nowait()
        except queue.Empty:
            self.after(80, self._poll_load_queue)
            return
        if msg[0] == "err":
            messagebox.showerror("Load error", msg[1])
            return
        _, players, meta, source, state = msg
        self.players, self.meta, self.source = players, meta, source
        self.draft = Draft(players, self._roster_copy(),
                           teams=int(self.teams_var.get()))
        self.draft.apply_state(state)
        self._save_league_settings()
        self._update_league_label()
        self.status_var.set(f"{cache_age_string(meta, source)}    loaded draft.")
        self.refresh_views()

    # --------------------------------------------------------------- rendering
    def _matches_filter(self, p):
        f = self.filter_var.get()
        if f != "ALL" and p.display_pos != f:
            return False
        s = self.search_var.get().strip().lower()
        if s and s not in p.name.lower() and s not in p.team.lower():
            return False
        return True

    def refresh_views(self):
        if not self.draft:
            return
        self._update_filter_buttons()
        self._render_available()
        self._render_recommendations()
        self._render_positions()
        self._render_team()

    def _update_filter_buttons(self):
        active = self.filter_var.get()
        for pos, b in self.filter_buttons.items():
            if pos == active:
                b.configure(bg=C_ACCENT2, fg=C_ACCENT2_TEXT)
            else:
                b.configure(bg=C_CARD, fg=C_TEXT)

    def _on_toggle_upside(self):
        self.refresh_views()
        self._save_league_settings()

    def _render_available(self):
        self.tree.delete(*self.tree.get_children())
        self._update_sort_headers()
        hide = self.hide_drafted_var.get()
        show_upside = self.show_upside_var.get()
        i = 0
        for p in self._sorted_players_for_table():
            if hide and not p.available:
                continue
            if not self._matches_filter(p):
                continue
            stripe = "row_odd" if i % 2 else "row_even"
            code = injury_code(p.injury_status)
            badge = f"   {code}" if code else ""
            if p.available:
                name = p.name + badge
                tags = (stripe, f"pos_{p.display_pos}")
                if show_upside and p.available and p.risk_label in (
                        "Upside", "Boom-Bust"):
                    tags = tags + ("upside",)
            else:
                mark = "\u2713 " if p.drafted_by == "me" else "\u00d7 "
                name = mark + p.name + badge
                tags = (stripe, "mine" if p.drafted_by == "me" else "drafted")
            risk = p.risk_label if p.risk_label not in ("thin",) else ""
            self.tree.insert(
                "", "end", iid=f"av:{p.player_id}",
                values=(f"{p.adp:.1f}", name, p.display_pos, p.team,
                        p.bye or "-", f"{p.vorp:+.0f}", risk),
                tags=tags)
            i += 1

    def _render_recommendations(self):
        self.rec_tree.delete(*self.rec_tree.get_children())
        recs = self.draft.recommendations(5, show_upside=self.show_upside_var.get())
        for idx, (p, score, reason) in enumerate(recs):
            if idx == 0:
                rankstr, tags = "\u2605", ("top_pick", f"pos_{p.display_pos}")
            else:
                stripe = "row_odd" if idx % 2 else "row_even"
                rankstr, tags = str(idx + 1), (stripe, f"pos_{p.display_pos}")
            self.rec_tree.insert(
                "", "end", iid=f"rec:{p.player_id}",
                values=(rankstr, p.name, p.display_pos, f"{p.adp:.1f}", reason),
                tags=tags)

    def _render_positions(self):
        self.pos_tree.delete(*self.pos_tree.get_children())
        top = self.draft.top_by_position(5)
        for pos in POSITIONS:
            label = pos_label(pos)
            cliff = self.draft.position_cliff(pos)
            gname = f"  {label}"
            if cliff >= 12:
                gname += f"      \u26A0 steep drop after #1 (~{cliff:.0f} ADP)"
            gid = f"grp:{pos}"
            self.pos_tree.insert("", "end", iid=gid, text=gname, open=True,
                                 tags=("group",))
            for j, p in enumerate(top.get(pos, [])):
                stripe = "row_odd" if j % 2 else "row_even"
                self.pos_tree.insert(
                    gid, "end", iid=f"pos:{p.player_id}", text="    " + p.name,
                    values=(f"{p.adp:.1f}", p.team, p.bye or "-"),
                    tags=(stripe, f"pos_{label}"))

    def _render_team(self):
        self.roster_tree.delete(*self.roster_tree.get_children())
        rows = self.draft.roster_summary()
        groups = {"starter": "STARTERS", "bench": "BENCH"}
        last_kind = None
        stripe_i = 0
        for slot, p, kind in rows:
            if kind != last_kind:
                self.roster_tree.insert("", "end", text="  " + groups[kind],
                                        values=("", "", ""), tags=("group",))
                last_kind = kind
                stripe_i = 0
            stripe = "row_odd" if stripe_i % 2 else "row_even"
            stripe_i += 1
            if p is None:
                self.roster_tree.insert(
                    "", "end", text="  " + slot,
                    values=("\u2014", "", ""), tags=(stripe, "empty"))
            else:
                code = injury_code(p.injury_status)
                nm = p.name + (f"  {code}" if code else "")
                self.roster_tree.insert(
                    "", "end", iid=f"ros:{p.player_id}", text="  " + slot,
                    values=(nm, p.display_pos, p.bye or "-"),
                    tags=(stripe, f"pos_{p.display_pos}"))

        # needs + bye conflicts
        needs = self.draft.needs_summary()
        if needs:
            self.needs_var.set("Still need:  " + "   ".join(needs))
        else:
            self.needs_var.set("\u2714 All starting spots filled")

        conflicts = self.draft.bye_conflicts()
        if conflicts:
            parts = [f"Wk {bye}: {', '.join(names)}"
                     for bye, names in conflicts[:4]]
            self.byes_var.set("\u26A0 Bye stacks  \u2014  " + "   |   ".join(parts))
        else:
            self.byes_var.set("")

    # ------------------------------------------------------------------ close
    def _on_close(self):
        self._stop_sync()
        self._autosave()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """League rules editor: scoring, team count, and full roster composition."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("League Settings")
        self.configure(bg=C_PANEL)
        self.transient(app)
        self.resizable(False, False)
        self.grab_set()

        self.scoring_var = tk.StringVar(value=app.scoring_var.get())
        self.teams_var = tk.IntVar(value=int(app.teams_var.get()))
        self.theme_var = tk.StringVar(value=app.theme_name)
        self.slot_vars = {
            attr: tk.IntVar(value=getattr(app.roster_config, attr))
            for attr, _, _ in ROSTER_SLOTS
        }
        self._panels = []  # tk.Frames to recolor when the theme changes live

        # Sleeper connect state
        self.sl_user_var = tk.StringVar(
            value=(app.sleeper or {}).get("username", ""))
        self.sl_league_var = tk.StringVar(value="")
        self.sl_status_var = tk.StringVar(value="")
        self._sl_user = None
        self._sl_leagues = []
        self._sl_result = None

        pad = {"padx": 14}
        ttk.Label(self, text="League Rules", style="Head.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(14, 6), **pad)

        # --- Connect to Sleeper (auto-fills scoring / teams / roster below) ---
        ttk.Label(self, text="Connect to Sleeper", style="Head.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=14, pady=(4, 2))
        slf = tk.Frame(self, bg=C_PANEL)
        slf.grid(row=2, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 2))
        self._panels.append(slf)
        ttk.Label(slf, text="Username").pack(side="left")
        ent = ttk.Entry(slf, textvariable=self.sl_user_var, width=16)
        ent.pack(side="left", padx=(6, 6))
        ent.bind("<Return>", lambda e: self._sl_find())
        ttk.Button(slf, text="Find my leagues",
                   command=self._sl_find).pack(side="left")
        self.sl_combo = ttk.Combobox(slf, textvariable=self.sl_league_var,
                                     state="readonly", width=30, values=[])
        self.sl_combo.pack(side="left", padx=(8, 6))
        ttk.Button(slf, text="Import", style="Accent.TButton",
                   command=self._sl_import).pack(side="left")
        ttk.Label(self, textvariable=self.sl_status_var, style="Muted.TLabel").grid(
            row=3, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 4))

        # scoring + teams
        ttk.Label(self, text="Scoring").grid(row=4, column=0, sticky="w", **pad)
        ttk.Combobox(self, textvariable=self.scoring_var, state="readonly",
                     width=20, values=list(SCORING_FORMATS.keys())).grid(
            row=4, column=1, sticky="w", pady=3)
        ttk.Label(self, text="Teams").grid(row=4, column=2, sticky="e", **pad)
        ttk.Combobox(self, textvariable=self.teams_var, state="readonly",
                     width=5, values=TEAM_OPTIONS).grid(
            row=4, column=3, sticky="w", pady=3)

        # theme (applies live for instant preview)
        ttk.Label(self, text="Theme").grid(row=5, column=0, sticky="w",
                                           pady=(6, 0), **pad)
        theme_cb = ttk.Combobox(self, textvariable=self.theme_var,
                                state="readonly", width=20,
                                values=list(THEMES.keys()))
        theme_cb.grid(row=5, column=1, sticky="w", pady=(6, 0))
        theme_cb.bind("<<ComboboxSelected>>", lambda e: self._preview_theme())
        ttk.Label(self, text="changes instantly", style="Muted.TLabel").grid(
            row=5, column=2, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Separator(self).grid(row=6, column=0, columnspan=4,
                                 sticky="ew", pady=10, padx=14)

        ttk.Label(self, text="Starting lineup & bench", style="Head.TLabel").grid(
            row=7, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 6))

        # roster spinboxes in a grid (2 columns of slots)
        grid = tk.Frame(self, bg=C_PANEL)
        grid.grid(row=8, column=0, columnspan=4, sticky="ew", padx=14)
        self._panels.append(grid)
        for i, (attr, label, tip) in enumerate(ROSTER_SLOTS):
            r, c = divmod(i, 2)
            cell = tk.Frame(grid, bg=C_PANEL)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 28), pady=4)
            self._panels.append(cell)
            ttk.Spinbox(cell, from_=0, to=10, width=3,
                        textvariable=self.slot_vars[attr],
                        command=self._update_total).pack(side="left")
            self.slot_vars[attr].trace_add("write", lambda *_: self._update_total())
            ttk.Label(cell, text=f"  {label}",
                      font=("Segoe UI", 10, "bold")).pack(side="left")
            ttk.Label(cell, text=f"  {tip}", style="Muted.TLabel").pack(side="left")

        ttk.Separator(self).grid(row=9, column=0, columnspan=4,
                                 sticky="ew", pady=10, padx=14)

        # presets
        ttk.Label(self, text="Presets:", style="Muted.TLabel").grid(
            row=10, column=0, sticky="w", padx=14)
        prow = tk.Frame(self, bg=C_PANEL)
        prow.grid(row=10, column=1, columnspan=3, sticky="w")
        self._panels.append(prow)
        for name in ROSTER_PRESETS:
            ttk.Button(prow, text=name,
                       command=lambda n=name: self._apply_preset(n)).pack(
                side="left", padx=3, pady=2)

        self.total_var = tk.StringVar()
        ttk.Label(self, textvariable=self.total_var, style="Muted.TLabel").grid(
            row=11, column=0, columnspan=4, sticky="w", padx=14, pady=(8, 0))

        # action buttons
        actions = tk.Frame(self, bg=C_PANEL)
        actions.grid(row=12, column=0, columnspan=4, sticky="e",
                     padx=14, pady=14)
        self._panels.append(actions)
        ttk.Button(actions, text="Close", command=self.destroy).pack(
            side="right", padx=4)
        ttk.Button(actions, text="Apply", style="Accent.TButton",
                   command=self._apply).pack(side="right", padx=4)

        self._update_total()
        self.update_idletasks()
        x = app.winfo_x() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_y() + (app.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _preview_theme(self):
        self.app.change_theme(self.theme_var.get())
        self.reskin()
        self.lift()
        self.focus_force()

    def reskin(self):
        """Recolor the dialog's plain frames after a live theme change."""
        try:
            self.configure(bg=C_PANEL)
            for f in self._panels:
                f.configure(bg=C_PANEL)
        except tk.TclError:
            pass

    # ------------------------------------------------------- Sleeper connect
    def _sl_find(self):
        uname = self.sl_user_var.get().strip()
        if not uname:
            self.sl_status_var.set("Enter your Sleeper username first.")
            return
        self.sl_status_var.set("Looking up your leagues\u2026")
        q = queue.Queue()

        def worker():
            try:
                user = sleeper.resolve_user(uname)
                st = season.get_state()
                seas = (st.get("season") or st.get("league_season")
                        or str(datetime.now().year))
                leagues = sleeper.list_leagues(user["user_id"], seas)
                q.put(("ok", user, seas, leagues))
            except Exception as e:
                q.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self._sl_poll(q)

    def _sl_poll(self, q):
        try:
            msg = q.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._sl_poll(q))
            return
        if msg[0] == "err":
            self.sl_status_var.set(f"Lookup failed: {msg[1]}")
            return
        _, user, seas, leagues = msg
        self._sl_user = user
        self._sl_leagues = leagues
        if not leagues:
            self.sl_combo.configure(values=[])
            self.sl_league_var.set("")
            self.sl_status_var.set(
                f"No {seas} NFL leagues for {user.get('display_name')}.")
            return
        labels = [f"{lg['name']}  [{lg['status']}]" for lg in leagues]
        self.sl_combo.configure(values=labels)
        self.sl_league_var.set(labels[0])
        self.sl_status_var.set(
            f"Found {len(leagues)} league(s) for {user.get('display_name')}. "
            "Pick one and click Import.")

    def _sl_import(self):
        if not self._sl_leagues:
            self.sl_status_var.set("Click 'Find my leagues' first.")
            return
        idx = self.sl_combo.current()
        if idx < 0:
            idx = 0
        lg = self._sl_leagues[idx]
        self.sl_status_var.set(f"Importing '{lg.get('name')}'\u2026")
        q = queue.Queue()

        def worker():
            try:
                scoring, teams, roster = sleeper.league_to_config(lg)
                rid = sleeper.find_roster_id(lg["league_id"],
                                             self._sl_user["user_id"])
                q.put(("ok", scoring, teams, roster, rid, lg))
            except Exception as e:
                q.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self._sl_import_poll(q)

    def _sl_import_poll(self, q):
        try:
            msg = q.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._sl_import_poll(q))
            return
        if msg[0] == "err":
            self.sl_status_var.set(f"Import failed: {msg[1]}")
            return
        _, scoring, teams, roster, rid, lg = msg
        if scoring in SCORING_FORMATS:
            self.scoring_var.set(scoring)
        if teams:
            self.teams_var.set(
                teams if teams in TEAM_OPTIONS
                else min(TEAM_OPTIONS, key=lambda t: abs(t - teams)))
        for attr in self.slot_vars:
            self.slot_vars[attr].set(getattr(roster, attr))
        self._update_total()
        self._sl_result = {
            "username": self.sl_user_var.get().strip(),
            "user_id": self._sl_user.get("user_id"),
            "league_id": lg.get("league_id"),
            "draft_id": lg.get("draft_id"),
            "roster_id": rid,
            "league_name": lg.get("name"),
        }
        note = "" if teams in TEAM_OPTIONS else f" (rounded from {teams})"
        self.sl_status_var.set(
            f"Imported '{lg.get('name')}': {scoring}, "
            f"{self.teams_var.get()} teams{note}. Click Apply to save.")

    def _apply_preset(self, name):
        for attr, val in ROSTER_PRESETS[name].items():
            if attr in self.slot_vars:
                self.slot_vars[attr].set(val)
        if "Superflex" in name and not self.scoring_var.get().startswith("2-QB"):
            self.scoring_var.set("2-QB / Superflex")
        self._update_total()

    def _read_roster(self) -> RosterConfig:
        def g(attr):
            try:
                return max(0, int(self.slot_vars[attr].get()))
            except (tk.TclError, ValueError):
                return 0
        return RosterConfig(**{attr: g(attr) for attr, _, _ in ROSTER_SLOTS})

    def _update_total(self):
        r = self._read_roster()
        self.total_var.set(
            f"Total roster size: {r.total_roster} players per team "
            f"({r.starter_slots} starters + {r.bench} bench)")

    def _apply(self):
        roster = self._read_roster()
        if roster.total_roster < 1:
            messagebox.showwarning("Invalid roster",
                                   "You need at least one roster spot.",
                                   parent=self)
            return
        if self._sl_result:
            self.app.sleeper = dict(self._sl_result)
        self.app.apply_league_settings(
            self.scoring_var.get(), int(self.teams_var.get()), roster,
            self.theme_var.get())
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
