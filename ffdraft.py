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
        tree.tag_configure("empty", foreground=C_MUTED)

    # ----------------------------------------------------------- table sorting
    _SORT_DEFAULT_REVERSE = {"adp": False, "name": False, "pos": False,
                             "team": False, "bye": False, "vorp": True}

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
        season_tab = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(draft_tab, text="  \U0001F4CB  Draft  ")
        self.nb.add(season_tab, text="  \U0001F4F0  Season  ")
        self._build_draft_tab(draft_tab)
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
        cols = ("adp", "name", "pos", "team", "bye", "vorp")
        self._avail_heads = {"adp": "ADP", "name": "Player", "pos": "Pos",
                             "team": "Team", "bye": "Bye", "vorp": "Value"}
        widths = {"adp": 64, "name": 210, "pos": 54, "team": 58,
                  "bye": 50, "vorp": 70}
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
        act.pack(fill="x", padx=12, pady=12)
        ttk.Button(act, text="\u2705  Draft to MY team", style="Accent.TButton",
                   command=lambda: self.draft_selected("me")).pack(side="left")
        ttk.Button(act, text="\u274C  Drafted by another team",
                   command=lambda: self.draft_selected("other")).pack(side="left", padx=8)
        ttk.Checkbutton(act, text="Hide drafted", variable=self.hide_drafted_var,
                        command=self.refresh_views,
                        style="TCheckbutton").pack(side="right")

    def _build_insights(self, parent):
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

    # ------------------------------------------------------------- season tab
    def _build_season_tab(self, parent):
        # control bar
        bar = tk.Frame(parent, bg=C_BG)
        bar.pack(side="top", fill="x", pady=(8, 6))
        ttk.Label(bar, textvariable=self.season_status_var,
                  style="Status.TLabel").pack(side="left", padx=(2, 0))
        ttk.Button(bar, text="\u21BB Refresh", style="Accent.TButton",
                   command=lambda: self._load_season(force=True)).pack(side="right")
        ttk.Checkbutton(bar, text="Only my players", variable=self.inj_mine_only,
                        style="TCheckbutton",
                        command=self._render_injuries).pack(side="right", padx=10)
        ttk.Checkbutton(bar, text="Skill positions only",
                        variable=self.inj_fantasy_only, style="TCheckbutton",
                        command=self._render_injuries).pack(side="right", padx=4)

        grid = tk.Frame(parent, bg=C_BG)
        grid.pack(side="top", fill="both", expand=True)
        for r in (0, 1):
            grid.rowconfigure(r, weight=1, uniform="srow")
        for c in (0, 1):
            grid.columnconfigure(c, weight=1, uniform="scol")

        def panel(r, c):
            f = tk.Frame(grid, bg=C_PANEL)
            f.grid(row=r, column=c, sticky="nsew",
                   padx=(0 if c == 0 else 6, 0), pady=(0 if r == 0 else 8, 0))
            return f

        # --- News (top-left) ---
        p_news = panel(0, 0)
        self._section_header(p_news, "\U0001F4F0 NFL News", C_ACCENT,
                             hint="double-click to open").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        nw = tk.Frame(p_news, bg=C_CARD)
        nw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.news_tree = ttk.Treeview(nw, columns=("date",), show="tree headings",
                                      selectmode="browse")
        self.news_tree.heading("#0", text="Headline")
        self.news_tree.heading("date", text="Date")
        self.news_tree.column("#0", width=360, anchor="w")
        self.news_tree.column("date", width=90, anchor="center", stretch=False)
        nvsb = ttk.Scrollbar(nw, orient="vertical", command=self.news_tree.yview)
        self.news_tree.configure(yscrollcommand=nvsb.set)
        self.news_tree.pack(side="left", fill="both", expand=True)
        nvsb.pack(side="right", fill="y")
        self.news_tree.bind("<Double-1>", self._open_news_link)

        # --- Injuries (top-right) ---
        p_inj = panel(0, 1)
        self._section_header(p_inj, "\U0001FA79 Injury Report", "#e5484d",
                             hint="your players highlighted").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        iw = tk.Frame(p_inj, bg=C_CARD)
        iw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.inj_tree = ttk.Treeview(
            iw, columns=("pos", "team", "status", "note"),
            show="tree headings", selectmode="browse")
        self.inj_tree.heading("#0", text="Player")
        for c, (lbl, w, st) in {
            "pos": ("Pos", 46, False), "team": ("Tm", 46, False),
            "status": ("Status", 90, False), "note": ("Detail", 220, True)
        }.items():
            self.inj_tree.heading(c, text=lbl)
            self.inj_tree.column(c, width=w, anchor="w" if c == "note" else "center",
                                 stretch=st)
        self.inj_tree.column("#0", width=150, anchor="w")
        ivsb = ttk.Scrollbar(iw, orient="vertical", command=self.inj_tree.yview)
        self.inj_tree.configure(yscrollcommand=ivsb.set)
        self.inj_tree.pack(side="left", fill="both", expand=True)
        ivsb.pack(side="right", fill="y")

        # --- Pickups / trending (bottom-left) ---
        p_pick = panel(1, 0)
        self._section_header(p_pick, "\U0001F4C8 Waiver Pickups", C_ACCENT2,
                             hint="most-added players \u00b7 fills your needs starred").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        pw = tk.Frame(p_pick, bg=C_CARD)
        pw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.pick_tree = ttk.Treeview(
            pw, columns=("pos", "team", "adds", "status"),
            show="tree headings", selectmode="browse")
        self.pick_tree.heading("#0", text="Player")
        for c, (lbl, w, anc, st) in {
            "pos": ("Pos", 46, "center", False), "team": ("Tm", 46, "center", False),
            "adds": ("Adds", 80, "e", False),
            "status": ("Inj", 110, "w", True)}.items():
            self.pick_tree.heading(c, text=lbl)
            self.pick_tree.column(c, width=w, anchor=anc, stretch=st)
        self.pick_tree.column("#0", width=170, anchor="w")
        pvsb = ttk.Scrollbar(pw, orient="vertical", command=self.pick_tree.yview)
        self.pick_tree.configure(yscrollcommand=pvsb.set)
        self.pick_tree.pack(side="left", fill="both", expand=True)
        pvsb.pack(side="right", fill="y")

        # --- Streaming K/DEF (bottom-right) ---
        p_str = panel(1, 1)
        self._section_header(p_str, "\U0001F3AF Streaming Matchups", C_GOLD,
                             hint="from Vegas odds").pack(
            anchor="w", fill="x", padx=12, pady=(10, 6))
        sw = tk.Frame(p_str, bg=C_CARD)
        sw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.str_tree = ttk.Treeview(sw, columns=("matchup", "metric"),
                                     show="tree headings", selectmode="browse")
        self.str_tree.heading("#0", text="Team")
        self.str_tree.heading("matchup", text="Matchup")
        self.str_tree.heading("metric", text="Implied")
        self.str_tree.column("#0", width=80, anchor="w")
        self.str_tree.column("matchup", width=110, anchor="w", stretch=True)
        self.str_tree.column("metric", width=80, anchor="center", stretch=False)
        svsb = ttk.Scrollbar(sw, orient="vertical", command=self.str_tree.yview)
        self.str_tree.configure(yscrollcommand=svsb.set)
        self.str_tree.pack(side="left", fill="both", expand=True)
        svsb.pack(side="right", fill="y")

        for t in (self.news_tree, self.inj_tree, self.pick_tree, self.str_tree):
            self._apply_row_tags(t)
            t.tag_configure("group", font=("Segoe UI Semibold", 10, "bold"),
                            foreground=C_TEXT, background=C_HEAD)
            t.tag_configure("mine_row", background=C_TOPPICK,
                            font=("Segoe UI", 10, "bold"))
            t.tag_configure("st_out", foreground="#e5484d")
            t.tag_configure("st_warn", foreground=C_WARN)
            t.tag_configure("need", foreground=C_ACCENT)

    def _on_tab_changed(self, event):
        try:
            tab = self.nb.tab(self.nb.select(), "text")
        except tk.TclError:
            return
        if "Season" in tab and not self._season_loaded:
            self._load_season()

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
        self._render_pickups()
        self._render_streaming()
        label = season.state_label(res.get("state", {}))
        errs = [k.replace("_err", "") for k in res if k.endswith("_err")]
        msg = label or "Season data loaded."
        if errs:
            msg += f"   (couldn't load: {', '.join(errs)})"
        self.season_status_var.set(msg)

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

    def _render_pickups(self):
        self.pick_tree.delete(*self.pick_tree.get_children())
        needs = self._my_need_positions()
        for i, r in enumerate(self._season_data.get("pickups", [])):
            stripe = "row_odd" if i % 2 else "row_even"
            pos = r["pos"] or "?"
            fills_need = pos in needs
            tags = [stripe]
            if pos in POS_COLORS:
                tags.append(f"pos_{pos}")
            mark = "\u2605 " if fills_need else "  "
            adds = f"+{r['count']:,}" if r["count"] else ""
            self.pick_tree.insert("", "end", text=mark + r["name"],
                                  values=(pos, r["team"], adds,
                                          r.get("injury_status", "")),
                                  tags=tuple(tags))

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
        self.league_var.set("Lineup:  " + " ".join(bits)
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
            self._render_pickups()
            self._render_streaming()

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

    def _render_available(self):
        self.tree.delete(*self.tree.get_children())
        self._update_sort_headers()
        hide = self.hide_drafted_var.get()
        i = 0
        for p in self._sorted_players_for_table():
            if hide and not p.available:
                continue
            if not self._matches_filter(p):
                continue
            stripe = "row_odd" if i % 2 else "row_even"
            if p.available:
                name = p.name
                tags = (stripe, f"pos_{p.display_pos}")
            else:
                mark = "\u2713 " if p.drafted_by == "me" else "\u00d7 "
                name = mark + p.name
                tags = (stripe, "mine" if p.drafted_by == "me" else "drafted")
            self.tree.insert(
                "", "end", iid=f"av:{p.player_id}",
                values=(f"{p.adp:.1f}", name, p.display_pos, p.team,
                        p.bye or "-", f"{p.vorp:+.0f}"),
                tags=tags)
            i += 1

    def _render_recommendations(self):
        self.rec_tree.delete(*self.rec_tree.get_children())
        for idx, (p, score, reason) in enumerate(self.draft.recommendations(5)):
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
                self.roster_tree.insert(
                    "", "end", iid=f"ros:{p.player_id}", text="  " + slot,
                    values=(p.name, p.display_pos, p.bye or "-"),
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

        pad = {"padx": 14}
        ttk.Label(self, text="League Rules", style="Head.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(14, 8), **pad)

        # scoring + teams
        ttk.Label(self, text="Scoring").grid(row=1, column=0, sticky="w", **pad)
        ttk.Combobox(self, textvariable=self.scoring_var, state="readonly",
                     width=20, values=list(SCORING_FORMATS.keys())).grid(
            row=1, column=1, sticky="w", pady=3)
        ttk.Label(self, text="Teams").grid(row=1, column=2, sticky="e", **pad)
        ttk.Combobox(self, textvariable=self.teams_var, state="readonly",
                     width=5, values=TEAM_OPTIONS).grid(
            row=1, column=3, sticky="w", pady=3)

        # theme (applies live for instant preview)
        ttk.Label(self, text="Theme").grid(row=2, column=0, sticky="w",
                                           pady=(6, 0), **pad)
        theme_cb = ttk.Combobox(self, textvariable=self.theme_var,
                                state="readonly", width=20,
                                values=list(THEMES.keys()))
        theme_cb.grid(row=2, column=1, sticky="w", pady=(6, 0))
        theme_cb.bind("<<ComboboxSelected>>", lambda e: self._preview_theme())
        ttk.Label(self, text="changes instantly", style="Muted.TLabel").grid(
            row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Separator(self).grid(row=3, column=0, columnspan=4,
                                 sticky="ew", pady=10, padx=14)

        ttk.Label(self, text="Starting lineup & bench", style="Head.TLabel").grid(
            row=4, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 6))

        # roster spinboxes in a grid (2 columns of slots)
        grid = tk.Frame(self, bg=C_PANEL)
        grid.grid(row=5, column=0, columnspan=4, sticky="ew", padx=14)
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

        ttk.Separator(self).grid(row=6, column=0, columnspan=4,
                                 sticky="ew", pady=10, padx=14)

        # presets
        ttk.Label(self, text="Presets:", style="Muted.TLabel").grid(
            row=7, column=0, sticky="w", padx=14)
        prow = tk.Frame(self, bg=C_PANEL)
        prow.grid(row=7, column=1, columnspan=3, sticky="w")
        self._panels.append(prow)
        for name in ROSTER_PRESETS:
            ttk.Button(prow, text=name,
                       command=lambda n=name: self._apply_preset(n)).pack(
                side="left", padx=3, pady=2)

        self.total_var = tk.StringVar()
        ttk.Label(self, textvariable=self.total_var, style="Muted.TLabel").grid(
            row=8, column=0, columnspan=4, sticky="w", padx=14, pady=(8, 0))

        # action buttons
        actions = tk.Frame(self, bg=C_PANEL)
        actions.grid(row=9, column=0, columnspan=4, sticky="e",
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
        self.app.apply_league_settings(
            self.scoring_var.get(), int(self.teams_var.get()), roster,
            self.theme_var.get())
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
