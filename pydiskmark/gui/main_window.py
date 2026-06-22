"""MainWindow — top-level pydiskmark GUI window.

Layout mirrors jdm-java's MainFrame:

  ┌──────────────────────────────────────────────────────────┐
  │  Menu: File | Action | Options | Help                    │
  ├──────┬───────────────────────────────────────────────────┤
  │      │                                                   │
  │  D   │              ChartPanel                           │
  │  r   │         (matplotlib dual-axis)                    │
  │  i   │                                                   │
  │  v   │                                                   │
  │  e   │                                                   │
  │  s   │                                                   │
  ├──────┤                                                   │
  │  B   │   [left tab content switches between             │
  │  e   │    DrivesPanel and ControlPanel]                  │
  │  n   │                                                   │
  │  c   │                                                   │
  │  h   │                                                   │
  ├──────┴───────────────────────────────────────────────────┤
  │  [Benchmark Operations] [Events]                         │
  │   history treeview                                       │
  ├──────────────────────────────────────────────────────────┤
  │  [progress bar]        Total Tx (KB): N                  │
  └──────────────────────────────────────────────────────────┘

Threading model:
  BenchmarkRunner runs in a daemon thread.
  GuiListener posts events to a queue.Queue.
  _poll_queue() drains the queue every 50 ms via root.after().
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import pydiskmark.app as app
from ..benchmark import IOMode
from ..benchmark_runner import BenchmarkRunner
from ..util import delete_directory
from .chart_panel import ChartPanel
from .control_panel import ControlPanel
from .drives_panel import DrivesPanel
from .history_panel import HistoryPanel
from .listener import (
    EVT_COMPLETE, EVT_ERROR,
    EVT_PROGRESS, EVT_SAMPLE, GuiListener,
)
from . import theme


_POLL_MS = 50   # queue poll interval in milliseconds


def _autohide_scroll(scrollbar: ttk.Scrollbar, sticky: str, first: str, last: str) -> None:
    """Show or hide *scrollbar* depending on whether all content fits on screen.

    Pass as yscrollcommand / xscrollcommand:
        widget.configure(yscrollcommand=lambda f, l: _autohide_scroll(sb, 'ns', f, l))
    """
    f, l = float(first), float(last)
    if f <= 0.0 and l >= 1.0:
        scrollbar.grid_remove()
    else:
        scrollbar.grid(sticky=sticky)
    scrollbar.set(first, last)


# Custom vertical tab panel (replaces ttk.Notebook tabposition='wn' which
# requires Tcl/Tk >= 8.6.6 — not available in Windows Store Python builds)
# ---------------------------------------------------------------------------

class _VertTabPanel(ttk.Frame):
    """Slim left strip of rotated-text canvas tabs + swappable content area.

    Uses tk.Canvas.create_text(angle=90) to rotate labels 90° counter-clockwise
    (reads bottom-to-top, matching Swing's LEFT tab placement).
    Works on all Tk versions with no extra dependencies.
    """

    _STRIP_W    = 42           # pixel width of the strip
    _TAB_PAD    = 20           # vertical padding above/below text
    _FONT       = ("Segoe UI", 11)

    # Colour sets — retheme() picks the right set at runtime
    _ACTIVE_BG  = "#005fb8"   # blue — same in both themes
    _ACTIVE_FG  = "#ffffff"

    # Dark defaults (overwritten by retheme when switching to light)
    _INACTIVE_BG = "#1c1c1c"
    _HOVER_BG   = "#2b2b2b"
    _INACTIVE_FG = "#909090"

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self._tabs: list[tuple[tk.Canvas, ttk.Frame]] = []
        self._active_idx = -1

        # Use a plain tk.Frame so we can set a background colour
        self._strip_frame = tk.Frame(
            self, width=self._STRIP_W, bg=self._INACTIVE_BG,
        )
        self._strip_frame.pack(side=tk.LEFT, fill=tk.Y)
        self._strip_frame.pack_propagate(False)

        ttk.Separator(self, orient="vertical").pack(side=tk.LEFT, fill=tk.Y)

        # Pages live in here as children so tkraise() works
        self._area = ttk.Frame(self)
        self._area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def make_page(self) -> ttk.Frame:
        """Create and return a full-size content frame inside the content area."""
        page = ttk.Frame(self._area)
        page.place(x=0, y=0, relwidth=1, relheight=1)
        return page

    def add(self, page: ttk.Frame, text: str) -> None:
        """Register *page* (from make_page()) as a tab with a rotated label."""
        idx = len(self._tabs)

        # Height scales with text so nothing is clipped
        tab_h = max(90, len(text) * 12 + self._TAB_PAD * 2)
        cx = self._STRIP_W // 2

        canvas = tk.Canvas(
            self._strip_frame,
            width=self._STRIP_W,
            height=tab_h,
            bg=self._INACTIVE_BG,
            bd=0,
            highlightthickness=1,
            highlightbackground=self._INACTIVE_BG,
            cursor="hand2",
        )
        canvas.pack(pady=(6, 0))

        # Rotated text: angle=90 → counter-clockwise → reads bottom-to-top
        canvas.create_text(
            cx, tab_h // 2,
            text=text,
            angle=90,
            fill=self._INACTIVE_FG,
            font=self._FONT,
            tags="lbl",
        )

        canvas.bind("<Button-1>", lambda _e, i=idx: self.select(i))
        canvas.bind("<Enter>",   lambda _e, c=canvas, i=idx: self._hover(c, i, True))
        canvas.bind("<Leave>",   lambda _e, c=canvas, i=idx: self._hover(c, i, False))

        self._tabs.append((canvas, page))
        # All pages except the first start hidden underneath
        if idx != 0:
            page.lower()

    def _hover(self, canvas: tk.Canvas, idx: int, entering: bool) -> None:
        if idx == self._active_idx:
            return  # don't change active tab on hover
        canvas.configure(
            bg=self._HOVER_BG if entering else self._INACTIVE_BG,
            highlightbackground=self._HOVER_BG if entering else self._INACTIVE_BG,
        )

    def select(self, idx: int) -> None:
        """Bring tab *idx* to the front and highlight its button."""
        self._active_idx = idx
        for i, (canvas, page) in enumerate(self._tabs):
            if i == idx:
                page.tkraise()
                canvas.configure(
                    bg=self._ACTIVE_BG,
                    highlightbackground=self._ACTIVE_BG,
                )
                canvas.itemconfigure("lbl", fill=self._ACTIVE_FG)
            else:
                canvas.configure(
                    bg=self._INACTIVE_BG,
                    highlightbackground=self._INACTIVE_BG,
                )
                canvas.itemconfigure("lbl", fill=self._INACTIVE_FG)

    def retheme(self) -> None:
        """Refresh tab colours after a dark/light theme toggle."""
        from . import theme as _theme
        if _theme.is_dark():
            _VertTabPanel._INACTIVE_BG = "#1c1c1c"
            _VertTabPanel._HOVER_BG   = "#2b2b2b"
            _VertTabPanel._INACTIVE_FG = "#909090"
        else:
            _VertTabPanel._INACTIVE_BG = "#ffffff"
            _VertTabPanel._HOVER_BG   = "#e8e8e8"
            _VertTabPanel._INACTIVE_FG = "#333333"

        # Update strip frame background
        self._strip_frame.configure(bg=self._INACTIVE_BG)

        # Re-select to repaint all buttons with the updated colour vars
        if self._active_idx >= 0:
            self.select(self._active_idx)
        else:
            # No tab selected yet — repaint all as inactive
            for canvas, _ in self._tabs:
                canvas.configure(
                    bg=self._INACTIVE_BG,
                    highlightbackground=self._INACTIVE_BG,
                )
                canvas.itemconfigure("lbl", fill=self._INACTIVE_FG)



# ---------------------------------------------------------------------------
# Splash screen
# ---------------------------------------------------------------------------

class _SplashScreen:
    """Borderless branded splash shown while MainWindow builds off-screen.

    Displayed as a Toplevel on the already-created (but withdrawn) root so
    it inherits the Tk event loop without needing a second Tk() instance.
    Destroyed by calling .close() once the main window is ready to show.

    Pass win_x/win_y/win_w/win_h to centre the splash over the region where
    the main window will appear.  If omitted, falls back to screen-centre.
    """

    _W = 360   # splash width  (px)
    _H = 210   # splash height (px) — taller to fit progress bar

    def __init__(
        self,
        root: tk.Tk,
        is_dark_theme: bool,
        *,
        win_x: int = -1,
        win_y: int = -1,
        win_w: int = 0,
        win_h: int = 0,
    ) -> None:
        self._top = tk.Toplevel(root)
        self._top.overrideredirect(True)   # no title bar / chrome
        self._top.resizable(False, False)
        self._progress = 0.0

        # ── Colours matching the active theme ──
        bg     = "#1c1c1c" if is_dark_theme else "#f0f0f0"
        fg     = "#e0e0e0" if is_dark_theme else "#222222"
        sub_fg = "#888888" if is_dark_theme else "#666666"
        self._top.configure(bg=bg)

        # ── Blue border frame ──
        border = tk.Frame(self._top, bg="#005fb8", padx=2, pady=2)
        border.pack(fill=tk.BOTH, expand=True)
        inner = tk.Frame(border, bg=bg)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # ── Turtle icon ──
        self._img_ref = None
        icon_path = Path(__file__).parent / "turtle_icon.png"
        try:
            from PIL import Image, ImageTk
            img = Image.open(str(icon_path)).resize((72, 72), Image.LANCZOS)
            self._img_ref = ImageTk.PhotoImage(img)
            tk.Label(inner, image=self._img_ref, bg=bg, bd=0).pack(pady=(14, 4))
        except Exception:
            tk.Label(inner, text="🐢", font=("", 36), bg=bg).pack(pady=(14, 4))

        # ── App name ──
        tk.Label(
            inner, text=f"pydiskmark  {app.VERSION}",
            font=("Segoe UI", 13, "bold"), bg=bg, fg=fg,
        ).pack()

        # ── Status label ──
        self._status_var = tk.StringVar(value="Loading…")
        tk.Label(
            inner, textvariable=self._status_var,
            font=("Segoe UI", 9), bg=bg, fg=sub_fg,
        ).pack(pady=(4, 6))

        # ── Progress bar ──
        pb_frame = tk.Frame(inner, bg=bg)
        pb_frame.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._pb_var = tk.DoubleVar(value=0.0)
        self._pb = ttk.Progressbar(
            pb_frame, variable=self._pb_var,
            maximum=100, mode="determinate",
            length=self._W - 48,
        )
        self._pb.pack(fill=tk.X)

        # ── Position: centre over main window region (or screen centre) ──
        self._top.update_idletasks()
        if win_x >= 0 and win_w > 0:
            x = win_x + (win_w - self._W) // 2
            y = win_y + (win_h - self._H) // 2
        else:
            sw = self._top.winfo_screenwidth()
            sh = self._top.winfo_screenheight()
            x = (sw - self._W) // 2
            y = (sh - self._H) // 2
        self._top.geometry(f"{self._W}x{self._H}+{x}+{y}")
        self._top.lift()
        self._top.update()

    # ------------------------------------------------------------------

    def set_status(self, text: str, progress: float | None = None) -> None:
        """Update status label and optionally advance the progress bar."""
        self._status_var.set(text)
        if progress is not None:
            self._progress = min(100.0, float(progress))
            self._pb_var.set(self._progress)
        self._top.update_idletasks()

    def get_progress(self) -> float:
        return self._progress

    def animate_to(self, target: float, over_ms: float) -> None:
        """Smoothly animate the progress bar from its current value to *target*
        (0-100) over *over_ms* milliseconds.  Blocks until complete.
        Renders at ~60 fps using update_idletasks() to keep the splash alive.
        """
        import time as _t
        if over_ms <= 0 or target <= self._progress:
            self._pb_var.set(target)
            self._progress = target
            return
        fps       = 60
        step_s    = 1.0 / fps
        n_steps   = max(1, int(over_ms / 1000 * fps))
        delta     = (target - self._progress) / n_steps
        for _ in range(n_steps):
            self._progress += delta
            self._pb_var.set(self._progress)
            self._top.update_idletasks()
            _t.sleep(step_s)
        self._progress = target
        self._pb_var.set(target)

    def close(self) -> None:
        """Destroy the splash window."""
        try:
            self._top.destroy()
        except Exception:
            pass


class MainWindow:
    """Main application window."""

    def __init__(self) -> None:
        import time as _time

        def _tick(label: str, t0: float, steps: list) -> float:
            t1 = _time.perf_counter()
            steps.append((label, t1 - t0))
            return t1

        _steps: list[tuple[str, float]] = []
        _t = _time.perf_counter()
        _total_start = _t

        # ── Tk root: create and hide immediately ──────────────────────────────
        self._root = tk.Tk()
        self._root.withdraw()
        _t = _tick("tk.Tk() + withdraw()", _t, _steps)

        # Window title
        cpu = app.processor_name or "Unknown CPU"
        self._root.title(f"pydiskmark {app.VERSION}  —  {app.arch}  —  {cpu}")

        # Centre on screen (pre-compute position for splash alignment)
        _WIN_W, _WIN_H = 1000, 620
        self._root.update_idletasks()
        _sw = self._root.winfo_screenwidth()
        _sh = self._root.winfo_screenheight()
        _win_x = (_sw - _WIN_W) // 2
        _win_y = (_sh - _WIN_H) // 2
        self._root.geometry(f"{_WIN_W}x{_WIN_H}+{_win_x}+{_win_y}")
        self._root.minsize(900, 500)
        _t = _tick("window geometry / centre", _t, _steps)

        # ── Apply persisted theme BEFORE building any widgets ─────────────────
        import pydiskmark.app as _app
        saved = getattr(_app, "_saved_theme", "dark")
        is_dark = (saved != "light")
        if is_dark:
            theme.apply_dark_theme()
        else:
            theme.apply_light_theme()
        _t = _tick(f"apply_{'dark' if is_dark else 'light'}_theme()", _t, _steps)

        # ── Splash screen ─────────────────────────────────────────────────────
        splash = _SplashScreen(self._root, is_dark,
                               win_x=_win_x, win_y=_win_y,
                               win_w=_WIN_W, win_h=_WIN_H)
        _t = _tick("splash screen render", _t, _steps)

        # ── Listener / run state ──────────────────────────────────────────────
        self._listener = GuiListener()
        self._benchmark = None
        self._worker_thread: Optional[threading.Thread] = None
        self._target_tx_kb: int = 0

        # ── Menu bar ──────────────────────────────────────────────────────────
        splash.set_status("Building menu…", progress=10)
        self._build_menu()
        _t = _tick("_build_menu()", _t, _steps)

        # ── Bottom status bar ─────────────────────────────────────────────────
        splash.set_status("Building status bar…", progress=18)
        self._build_bottom_bar()
        _t = _tick("_build_bottom_bar()", _t, _steps)

        # ── Bottom history tabs ───────────────────────────────────────────────
        splash.set_status("Building history tabs…", progress=26)
        self._build_bottom_tabs()
        _t = _tick("_build_bottom_tabs()", _t, _steps)

        # ── Left tab panel + ControlPanel ─────────────────────────────────────
        splash.set_status("Building control panel…", progress=38)
        content = ttk.Frame(self._root)
        content.pack(fill=tk.BOTH, expand=True)
        self._left_nb = _VertTabPanel(content)
        self._left_nb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        drives_page = self._left_nb.make_page()
        bench_page  = self._left_nb.make_page()
        ctrl_frame = ttk.Frame(bench_page, width=260)
        ctrl_frame.pack(side=tk.LEFT, fill=tk.Y)
        ctrl_frame.pack_propagate(False)
        self._controls = ControlPanel(
            ctrl_frame,
            on_start=self._start_benchmark,
            on_stop=self._stop_benchmark,
        )
        self._controls.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        _t = _tick("_VertTabPanel + ControlPanel", _t, _steps)

        # ── Chart (matplotlib) ────────────────────────────────────────────────
        splash.set_status("Initialising matplotlib chart…", progress=52)
        ttk.Separator(bench_page, orient="vertical").pack(side=tk.LEFT, fill=tk.Y)
        chart_frame = ttk.Frame(bench_page)
        chart_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._chart = ChartPanel(chart_frame)
        self._chart.pack(fill=tk.BOTH, expand=True)
        _t = _tick("ChartPanel (matplotlib)", _t, _steps)

        # ── DrivesPanel + tab registration ────────────────────────────────────
        splash.set_status("Loading drive info…", progress=62)
        self._drives_panel = DrivesPanel(
            drives_page, on_location_change=self._on_location_change,
        )
        self._drives_panel.pack(fill=tk.BOTH, expand=True)
        self._left_nb.add(drives_page, text="Drives")
        self._left_nb.add(bench_page,  text="Benchmark")
        self._left_nb.select(1)
        _t = _tick("DrivesPanel + tab registration", _t, _steps)

        # ── Keyboard shortcuts + protocol ─────────────────────────────────────
        self._root.bind("<Control-r>", lambda _: self._start_benchmark())
        self._root.bind("<Escape>",    lambda _: self._stop_benchmark())
        self._root.bind("<Control-l>", lambda _: self._reset_chart())
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()
        _t = _tick("bindings + refresh_status()", _t, _steps)

        # ── Final layout pass ─────────────────────────────────────────────────
        splash.set_status("Laying out widgets…", progress=72)
        self._root.update_idletasks()
        _t = _tick("update_idletasks()", _t, _steps)

        # ── Retheme ───────────────────────────────────────────────────────────
        splash.set_status("Applying theme…", progress=80)
        self._chart.retheme()
        self._left_nb.retheme()
        _t = _tick("retheme() chart + tabs", _t, _steps)

        # ── Reveal: alpha=0 flush trick ───────────────────────────────────────
        splash.set_status("Ready", progress=100)
        self._root.wm_attributes("-alpha", 0)
        self._root.deiconify()
        self._root.update()                       # drain full event queue while invisible
        self._root.wm_attributes("-alpha", 1)     # snap main window to visible
        splash.close()                            # dismiss splash only after main is opaque
        _t = _tick("alpha=0 → deiconify → update → alpha=1 → splash.close()", _t, _steps)



        # ── Print startup timing breakdown ────────────────────────────────────
        total_ms = (_t - _total_start) * 1000
        print(f"\n{'─' * 52}")
        print(f"  pydiskmark startup timing")
        print(f"{'─' * 52}")
        for label, elapsed in _steps:
            bar = "█" * max(1, int(elapsed * 1000 / 10))   # 1 block per 10 ms
            print(f"  {label:<38}  {elapsed * 1000:6.1f} ms  {bar}")
        print(f"{'─' * 52}")
        print(f"  {'TOTAL':<38}  {total_ms:6.1f} ms")
        print(f"{'─' * 52}\n")


    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self._root)

        # ── File ──────────────────────────────────────────────────────────
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export...\tCtrl+E", command=self._export_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # ── Action ────────────────────────────────────────────────────────
        action_menu = tk.Menu(menubar, tearoff=0)
        action_menu.add_command(label="Start\tCtrl+R", command=self._start_benchmark)
        action_menu.add_command(label="Stop\tEsc",    command=self._stop_benchmark)
        action_menu.add_separator()
        action_menu.add_command(label="Reset Chart\tCtrl+L", command=self._reset_chart)
        action_menu.add_separator()
        action_menu.add_command(label="Clear Event Logs",        command=self._clear_event_logs)
        action_menu.add_command(label="Delete Data Directory",   command=self._delete_data_dir)
        action_menu.add_command(label="Delete Selected Benchmark", command=self._delete_selected_benchmark)
        action_menu.add_command(label="Delete All Benchmarks",   command=self._delete_all_benchmarks)
        menubar.add_cascade(label="Action", menu=action_menu)

        # ── Options ───────────────────────────────────────────────────────
        options_menu = tk.Menu(menubar, tearoff=0)

        # Instant-apply boolean flags
        self._auto_remove_var = tk.BooleanVar(value=app.auto_remove_data)
        self._auto_reset_var  = tk.BooleanVar(value=app.auto_reset)

        options_menu.add_checkbutton(
            label="Auto Remove Data Dir",
            variable=self._auto_remove_var,
            command=lambda: setattr(app, "auto_remove_data", self._auto_remove_var.get()),
        )
        options_menu.add_checkbutton(
            label="Auto Reset",
            variable=self._auto_reset_var,
            command=lambda: setattr(app, "auto_reset", self._auto_reset_var.get()),
        )
        options_menu.add_separator()
        options_menu.add_command(label="Toggle Theme",      command=self._toggle_theme)
        options_menu.add_separator()
        options_menu.add_command(label="Advanced Options…", command=self._show_advanced_options)
        menubar.add_cascade(label="Options", menu=options_menu)

        # ── Help ──────────────────────────────────────────────────────────
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self._root.config(menu=menubar)

    # ------------------------------------------------------------------
    # Main content: left notebook + chart
    # ------------------------------------------------------------------

    def _build_main_content(self) -> None:
        content = ttk.Frame(self._root)
        content.pack(fill=tk.BOTH, expand=True)

        # Tab strip + content area fills the whole window.
        # Drives page → fills full width (no chart visible).
        # Benchmark page → ControlPanel left + ChartPanel right, side-by-side.
        self._left_nb = _VertTabPanel(content)
        self._left_nb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Build Drives page content (frame only — no button yet) ──
        drives_page = self._left_nb.make_page()

        # ── Build Benchmark page content (chart created here) ──
        bench_page = self._left_nb.make_page()

        # Left: settings controls (fixed width)
        ctrl_frame = ttk.Frame(bench_page, width=260)
        ctrl_frame.pack(side=tk.LEFT, fill=tk.Y)
        ctrl_frame.pack_propagate(False)
        self._controls = ControlPanel(
            ctrl_frame,
            on_start=self._start_benchmark,
            on_stop=self._stop_benchmark,
        )
        self._controls.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        ttk.Separator(bench_page, orient="vertical").pack(side=tk.LEFT, fill=tk.Y)

        # Right: chart fills the remaining space
        chart_frame = ttk.Frame(bench_page)
        chart_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._chart = ChartPanel(chart_frame)
        self._chart.pack(fill=tk.BOTH, expand=True)

        # ── Now add tab buttons in display order: Drives first, Benchmark second ──
        # _chart now exists so DrivesPanel.refresh() → _refresh_status() is safe
        self._drives_panel = DrivesPanel(
            drives_page, on_location_change=self._on_location_change,
        )
        self._drives_panel.pack(fill=tk.BOTH, expand=True)
        self._left_nb.add(drives_page, text="Drives")    # index 0 — button first
        self._left_nb.add(bench_page, text="Benchmark")  # index 1 — button second

        # Start on Benchmark tab
        self._left_nb.select(1)

    # ------------------------------------------------------------------
    # Bottom: history tabs
    # ------------------------------------------------------------------

    def _build_bottom_tabs(self) -> None:
        self._bottom_nb = ttk.Notebook(self._root)
        self._bottom_nb.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 0))

        # Benchmark Operations tab
        ops_frame = ttk.Frame(self._bottom_nb)
        self._history = HistoryPanel(
            ops_frame,
            on_load=lambda benchmark_id: self._root.after(0, self._load_from_history, benchmark_id),
        )
        self._history.pack(fill=tk.BOTH, expand=True)
        self._bottom_nb.add(ops_frame, text="Benchmark Operations")

        # Events tab (simple log) — scrollbar auto-hides when not needed
        events_frame = ttk.Frame(self._bottom_nb)
        events_frame.rowconfigure(0, weight=1)
        events_frame.columnconfigure(0, weight=1)
        self._events_text = tk.Text(
            events_frame, height=5, state="disabled",
            wrap="none", font=("Courier", 9),
        )
        ev_scroll = ttk.Scrollbar(events_frame, orient="vertical",
                                   command=self._events_text.yview)
        self._events_text.configure(
            yscrollcommand=lambda f, l: _autohide_scroll(ev_scroll, "ns", f, l)
        )
        self._events_text.grid(row=0, column=0, sticky="nsew")
        ev_scroll.grid(row=0, column=1, sticky="ns")
        self._bottom_nb.add(events_frame, text="Events")

    # ------------------------------------------------------------------
    # Bottom bar: progress + total tx
    # ------------------------------------------------------------------

    def _build_bottom_bar(self) -> None:
        bar = ttk.Frame(self._root, relief="sunken")
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._status_label = ttk.Label(bar, text="Ready", anchor="w", padding=(6, 2))
        self._status_label.pack(side=tk.LEFT)

        # Total Tx label (right-aligned, like jdm-java)
        self._tx_label = ttk.Label(bar, text="Total Tx (KB): —", anchor="e", padding=(6, 2))
        self._tx_label.pack(side=tk.RIGHT)

        self._progress_var = tk.IntVar(value=0)
        self._progress_bar = ttk.Progressbar(
            bar, variable=self._progress_var,
            maximum=100, mode="determinate", length=200,
        )
        self._progress_bar.pack(side=tk.RIGHT, padx=(0, 6))

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        try:
            model = app.get_drive_model()
            partition = app.get_partition_id()
            usage = app.get_disk_usage()
            self._status_label.config(
                text=f"{model}  │  {partition}  │  "
                     f"{usage.used_gb:.0f} / {usage.total_gb:.0f} GB  "
                     f"({usage.percent_used:.0f}% used)"
            )
            # Chart title — guard: chart is created after drives panel during init
            if hasattr(self, "_chart"):
                title = (
                    f"{model}  —  {partition}:  "
                    f"{usage.percent_used:.0f}%  "
                    f"({usage.used_gb:.0f}/{usage.total_gb:.0f} GB)"
                )
                self._chart.set_title(title)
        except Exception:
            self._status_label.config(text="Drive info unavailable")

    def _on_location_change(self, path: str) -> None:
        """Called when user selects a different drive/directory."""
        self._refresh_status()

    # ------------------------------------------------------------------
    # Benchmark lifecycle
    # ------------------------------------------------------------------

    def _start_benchmark(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return

        # Reset state
        app.reset_test_data()
        app.reset_sequence()
        self._listener.reset()
        self._chart.clear()
        self._controls.reset_metrics()
        self._controls.set_running(True)
        self._progress_var.set(0)
        self._benchmark = None

        # Ensure data dir
        location = Path(app.location_dir) if app.location_dir else Path.home()
        data_dir = Path(app.data_dir) if app.data_dir else location / "pdm-data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot config
        self._controls.apply_to_app()
        cfg = app.get_config()

        # Compute target tx size for progress label
        self._target_tx_kb = cfg.num_blocks * (cfg.block_size // 1024) * cfg.num_samples
        self._tx_label.config(text=f"Total Tx (KB): 0 / {self._target_tx_kb:,}")
        self._status_label.config(text="Running benchmark…")
        self._log_event(f"Benchmark started — profile={cfg.profile.symbol if cfg.profile else 'custom'}")

        self._worker_thread = threading.Thread(
            target=self._run_worker, args=(cfg,), daemon=True,
        )
        self._worker_thread.start()
        self._poll_queue()

    def _run_worker(self, cfg) -> None:
        try:
            runner = BenchmarkRunner(self._listener, cfg)
            benchmark = runner.execute()
            self._listener._queue.put((EVT_COMPLETE, benchmark))
        except Exception as exc:
            self._listener._queue.put((EVT_ERROR, str(exc)))

    def _stop_benchmark(self) -> None:
        self._listener.cancel()
        self._status_label.config(text="Cancelling…")

    def _reset_chart(self) -> None:
        """Reset chart data and metrics display."""
        self._chart.clear()
        self._controls.reset_metrics()
        self._log_event("Chart reset")

    # ------------------------------------------------------------------
    # Queue polling — bridge between worker thread and Tkinter
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        events = self._listener.drain()

        should_reschedule = True
        needs_write_refresh = False
        needs_read_refresh = False

        for event in events:
            evt_type = event[0]

            if evt_type == EVT_SAMPLE:
                sample = event[1]
                self._chart.add_sample(sample)
                if sample.type_ == IOMode.WRITE:
                    needs_write_refresh = True
                else:
                    needs_read_refresh = True

            elif evt_type == EVT_PROGRESS:
                completed = event[1]
                self._progress_var.set(completed)
                done_kb = int(self._target_tx_kb * completed / 100)
                self._tx_label.config(
                    text=f"Total Tx (KB): {done_kb:,} / {self._target_tx_kb:,}"
                )


            elif evt_type == EVT_COMPLETE:
                self._benchmark = event[1]
                self._on_benchmark_complete()
                should_reschedule = False
                break

            elif evt_type == EVT_ERROR:
                self._on_benchmark_error(event[1])
                should_reschedule = False
                break

        # One chart redraw per poll cycle, not one per sample
        self._chart.flush()
        if needs_write_refresh:
            self._controls.refresh_write_metrics()
        if needs_read_refresh:
            self._controls.refresh_read_metrics()


        if not should_reschedule:
            return

        if self._worker_thread and self._worker_thread.is_alive():
            self._root.after(_POLL_MS, self._poll_queue)
        else:
            # Worker finished — do one extra drain to catch COMPLETE/ERROR
            # posted between our drain() call and is_alive() returning False
            final = self._listener.drain()
            for event in final:
                if event[0] == EVT_COMPLETE:
                    self._benchmark = event[1]
                    break
                elif event[0] == EVT_ERROR:
                    self._on_benchmark_error(event[1])
                    return
            self._on_benchmark_complete()

    def _on_benchmark_complete(self) -> None:
        self._chart.flush()
        self._controls.set_running(False)
        self._controls.refresh_write_metrics()
        self._controls.refresh_read_metrics()
        self._progress_var.set(100)
        self._tx_label.config(text=f"Total Tx (KB): {self._target_tx_kb:,}")

        if self._listener.is_cancelled():
            self._status_label.config(text="Benchmark cancelled")
            self._log_event("Benchmark cancelled")
        elif self._benchmark:
            elapsed = None
            if self._benchmark.start_time and self._benchmark.end_time:
                elapsed = (
                    self._benchmark.end_time - self._benchmark.start_time
                ).total_seconds()
            status = "Benchmark complete"
            if elapsed is not None:
                status += f"  —  {elapsed:.1f} s"
            self._status_label.config(text=status)
            self._log_event(status)

            # Auto-save to DB
            try:
                from .. import db
                db.save_benchmark(self._benchmark)
                self._history.refresh()
            except Exception as exc:
                self._log_event(f"DB save failed: {exc}")
        else:
            self._status_label.config(text="Benchmark complete")

        self._refresh_status()

    def _on_benchmark_error(self, error_msg: str) -> None:
        self._chart.flush()
        self._controls.set_running(False)
        self._progress_var.set(0)
        self._status_label.config(text=f"Error: {error_msg}")
        self._log_event(f"Error: {error_msg}")
        messagebox.showerror("Benchmark Error", error_msg, parent=self._root)

    # ------------------------------------------------------------------
    # Load benchmark from history (DB replay)
    # ------------------------------------------------------------------

    def _load_from_history(self, benchmark_id: str) -> None:
        """Replay a historical benchmark into the chart and restore its settings.

        Loads benchmark metadata for UI restoration, then reads each
        operation's sample file and plots them sequentially (Write first,
        then Read) with a single flush at the end.
        """
        from .. import db

        metadata = db.load_benchmark(benchmark_id)
        if not metadata:
            messagebox.showwarning(
                "Load Error", "Could not load benchmark data.", parent=self._root
            )
            return

        ops = db.load_benchmark_ops(benchmark_id)
        if not ops:
            messagebox.showwarning(
                "Load Error", "No operations found for this benchmark.", parent=self._root
            )
            return

        # ── Reset chart and metrics ──
        self._chart.clear()
        self._controls.reset_metrics()

        # ── Restore control panel settings from benchmark config ──
        self._controls.load_settings_from_data(metadata)

        # ── Plot each operation sequentially (Write then Read) ──
        # ops are already ordered Write-first by the DB query.
        # We add all samples from all ops before the single flush so
        # the chart renders both series in one pass — fast and race-free.
        import pydiskmark.app as _app
        _app.reset_test_data()

        for op_row in ops:
            op_data = db.load_op_data(op_row["data_file_path"])
            if not op_data:
                continue

            mode_str = op_data.get("ioMode", "")
            try:
                mode = IOMode(mode_str)
            except ValueError:
                mode = IOMode.WRITE

            for s in op_data.get("samples", []):
                sample = _HistorySample(
                    type_=mode,
                    sample_num=s.get("sn", 0),
                    bw_mb_sec=s.get("bw", 0.0),
                    cum_avg=s.get("bt", 0.0),
                    access_time_ms=s.get("la", 0.0),
                )
                self._chart.add_sample(sample)

            # Restore summary metrics
            bw   = op_data.get("bandwidth", -1.0)
            lat  = op_data.get("latency",   -1.0)
            iops = op_data.get("iops",      -1)
            if mode == IOMode.WRITE:
                _app.w_avg  = bw
                _app.w_acc  = lat
                _app.w_iops = iops
            else:
                _app.r_avg  = bw
                _app.r_acc  = lat
                _app.r_iops = iops

        # One render pass covering all operations
        self._chart.flush()
        self._controls.refresh_write_metrics()
        self._controls.refresh_read_metrics()

        # ── Update chart title ──
        di    = metadata.get("driveInfo", {})
        model = di.get("driveModel", "—")
        pct   = di.get("percentUsed", 0)
        used  = di.get("usedGb", 0)
        total = di.get("totalGb", 0)
        self._chart.set_title(
            f"{model}  —  {pct:.0f}%  ({used:.0f}/{total:.0f} GB)"
        )

        # ── Switch to the Benchmark tab so the chart is visible ──
        self._left_nb.select(1)

        self._log_event(f"Loaded benchmark {benchmark_id[:8]}… from history")


    # ------------------------------------------------------------------
    # Events log
    # ------------------------------------------------------------------

    def _log_event(self, message: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._events_text.configure(state="normal")
        self._events_text.insert(tk.END, f"[{ts}]  {message}\n")
        self._events_text.see(tk.END)
        self._events_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def _export_dialog(self) -> None:
        if not self._benchmark:
            messagebox.showwarning("No Results", "Run a benchmark first.", parent=self._root)
            return

        path = filedialog.asksaveasfilename(
            parent=self._root,
            title="Export Results",
            defaultextension=".json",
            filetypes=[
                ("JSON", "*.json"),
                ("YAML", "*.yml *.yaml"),
                ("CSV", "*.csv"),
            ],
        )
        if not path:
            return

        try:
            from ..exporter import export
            export(self._benchmark, path)
            self._status_label.config(text=f"Exported → {path}")
            self._log_event(f"Exported to {path}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc), parent=self._root)

    def _toggle_theme(self) -> None:
        theme.toggle_theme()
        self._chart.retheme()
        self._left_nb.retheme()
        # Persist the new theme choice immediately
        from pydiskmark import config
        config.save_config()

    # ------------------------------------------------------------------
    # Action menu handlers
    # ------------------------------------------------------------------

    def _clear_event_logs(self) -> None:
        """Clear all text in the Events log tab."""
        self._events_text.configure(state="normal")
        self._events_text.delete("1.0", tk.END)
        self._events_text.configure(state="disabled")

    def _delete_data_dir(self) -> None:
        """Delete the benchmark data directory after user confirmation."""
        data_dir = app.data_dir
        if not data_dir:
            messagebox.showinfo(
                "No Data Directory",
                "No data directory is configured.",
                parent=self._root,
            )
            return
        if not messagebox.askyesno(
            "Delete Data Directory",
            f"Delete all test files in:\n{data_dir}\n\nThis cannot be undone.",
            icon="warning",
            parent=self._root,
        ):
            return
        try:
            delete_directory(data_dir)
            self._log_event(f"Deleted data directory: {data_dir}")
            self._status_label.config(text="Data directory deleted")
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc), parent=self._root)

    def _delete_selected_benchmark(self) -> None:
        """Delete the benchmark currently selected in the history panel."""
        benchmark_id = self._history.get_selected_benchmark_id()
        if not benchmark_id:
            messagebox.showinfo(
                "Nothing Selected",
                "Select a benchmark in the history list first.",
                parent=self._root,
            )
            return
        if not messagebox.askyesno(
            "Delete Benchmark",
            "Delete the selected benchmark and all its data?\nThis cannot be undone.",
            icon="warning",
            parent=self._root,
        ):
            return
        try:
            from .. import db
            db.delete_benchmark(benchmark_id)
            self._history.refresh()
            self._log_event(f"Deleted benchmark {benchmark_id[:8]}…")
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc), parent=self._root)

    def _delete_all_benchmarks(self) -> None:
        """Delete every benchmark from the history after confirmation."""
        if not messagebox.askyesno(
            "Delete All Benchmarks",
            "Delete ALL benchmarks from history?\nThis cannot be undone.",
            icon="warning",
            parent=self._root,
        ):
            return
        try:
            from .. import db
            db.delete_all_benchmarks()
            self._history.refresh()
            self._log_event("All benchmarks deleted")
        except Exception as exc:
            messagebox.showerror("Delete Error", str(exc), parent=self._root)

    # ------------------------------------------------------------------
    # Advanced Options dialog
    # ------------------------------------------------------------------

    def _show_advanced_options(self) -> None:
        """Open an instant-apply Advanced Options dialog."""
        from ..benchmark import IoEngine, SectorAlignment

        dlg = tk.Toplevel(self._root)
        dlg.title("Advanced Options")
        dlg.resizable(False, False)
        dlg.transient(self._root)
        dlg.grab_set()

        outer = ttk.Frame(dlg, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        row = 0

        # --- IO Engine ---
        ttk.Label(outer, text="IO Engine").grid(
            row=row, column=0, sticky="w", pady=4, padx=(0, 12)
        )
        _engine_var = tk.StringVar(value=app.io_engine.name)
        engine_combo = ttk.Combobox(
            outer,
            textvariable=_engine_var,
            values=[e.name for e in IoEngine],
            state="readonly",
            width=20,
        )
        engine_combo.grid(row=row, column=1, sticky="ew", pady=4)

        def _on_engine(*_):
            for e in IoEngine:
                if e.name == _engine_var.get():
                    app.io_engine = e
                    break

        engine_combo.bind("<<ComboboxSelected>>", _on_engine)
        row += 1

        # --- Sector Alignment ---
        ttk.Label(outer, text="Sector Alignment").grid(
            row=row, column=0, sticky="w", pady=4, padx=(0, 12)
        )
        _align_var = tk.StringVar(value=app.sector_alignment.name)
        align_combo = ttk.Combobox(
            outer,
            textvariable=_align_var,
            values=[s.name for s in SectorAlignment],
            state="readonly",
            width=20,
        )
        align_combo.grid(row=row, column=1, sticky="ew", pady=4)

        def _on_align(*_):
            for s in SectorAlignment:
                if s.name == _align_var.get():
                    app.sector_alignment = s
                    break

        align_combo.bind("<<ComboboxSelected>>", _on_align)
        row += 1

        ttk.Separator(outer, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=8
        )
        row += 1

        # --- Checkboxes ---
        _direct_var = tk.BooleanVar(value=app.direct_enable)
        ttk.Checkbutton(
            outer,
            text="Direct IO (unbuffered)",
            variable=_direct_var,
            command=lambda: setattr(app, "direct_enable", _direct_var.get()),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        _sync_var = tk.BooleanVar(value=app.write_sync_enable)
        ttk.Checkbutton(
            outer,
            text="Write Sync",
            variable=_sync_var,
            command=lambda: setattr(app, "write_sync_enable", _sync_var.get()),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        _multi_var = tk.BooleanVar(value=app.multi_file)
        ttk.Checkbutton(
            outer,
            text="Multi Data File",
            variable=_multi_var,
            command=lambda: setattr(app, "multi_file", _multi_var.get()),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        ttk.Separator(outer, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=8
        )
        row += 1

        ttk.Button(outer, text="Close", command=dlg.destroy, width=10).grid(
            row=row, column=0, columnspan=2, pady=(0, 2)
        )

        outer.columnconfigure(1, weight=1)

        # Centre over parent
        dlg.update_idletasks()
        px = self._root.winfo_x() + (self._root.winfo_width()  - dlg.winfo_width())  // 2
        py = self._root.winfo_y() + (self._root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{px}+{py}")
        dlg.wait_window()

    def _show_about(self) -> None:
        """Show the About dialog with turtle icon on the left, info on the right."""
        dlg = tk.Toplevel(self._root)
        dlg.title("About pydiskmark")
        dlg.resizable(False, False)
        dlg.transient(self._root)
        dlg.grab_set()

        outer = ttk.Frame(dlg)
        outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # ── Left: turtle icon ──
        icon_frame = ttk.Frame(outer)
        icon_frame.pack(side=tk.LEFT, padx=(0, 20), anchor="n")

        icon_path = Path(__file__).parent / "turtle_icon.png"
        self._about_img = None  # keep reference to prevent GC
        try:
            from PIL import Image, ImageTk
            img = Image.open(str(icon_path)).resize((220, 220), Image.LANCZOS)
            self._about_img = ImageTk.PhotoImage(img)
            tk.Label(icon_frame, image=self._about_img, bd=0).pack()
        except Exception:
            # Pillow not available or image missing — show text placeholder
            ttk.Label(icon_frame, text="🐢", font=("", 60)).pack()

        # ── Right: text info ──
        info_frame = ttk.Frame(outer)
        info_frame.pack(side=tk.LEFT, anchor="n")

        ttk.Label(info_frame, text=f"pydiskmark  {app.VERSION}",
                  font=("", 13, "bold")).pack(anchor="w", pady=(4, 8))
        ttk.Label(info_frame, text=f"Python: {sys.version.split()[0]}").pack(anchor="w", pady=2)
        ttk.Label(info_frame, text=f"OS: {app.os_name}  {app.arch}").pack(anchor="w", pady=2)
        ttk.Label(info_frame, text=f"CPU: {app.processor_name}").pack(anchor="w", pady=2)
        ttk.Separator(info_frame, orient="horizontal").pack(fill=tk.X, pady=10)
        ttk.Label(info_frame, text="Apache License 2.0",
                  foreground="gray").pack(anchor="w", pady=(0, 6))

        # Clickable website link
        link = tk.Label(info_frame, text="www.jdiskmark.net",
                        foreground="#4da6ff", cursor="hand2",
                        font=("", 9, "underline"))
        link.pack(anchor="w", pady=(0, 10))
        link.bind("<Button-1>", lambda _e: __import__("webbrowser").open_new_tab(
            "https://www.jdiskmark.net"))

        ttk.Button(info_frame, text="OK", command=dlg.destroy, width=7).pack(anchor="w")


        # Centre over parent
        dlg.update_idletasks()
        px = self._root.winfo_x() + (self._root.winfo_width() - dlg.winfo_width()) // 2
        py = self._root.winfo_y() + (self._root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{px}+{py}")
        dlg.wait_window()

    def _on_close(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._listener.cancel()
        self._root.destroy()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._root.mainloop()


# ---------------------------------------------------------------------------
# Lightweight replay sample (avoids importing Sample for history loads)
# ---------------------------------------------------------------------------

class _HistorySample:
    """Minimal sample-like object for replaying historical data in the chart."""
    __slots__ = ("type_", "sample_num", "bw_mb_sec", "cum_avg",
                 "access_time_ms", "cum_min", "cum_max", "cum_acc_time_ms")

    def __init__(self, *, type_, sample_num, bw_mb_sec, cum_avg, access_time_ms):
        self.type_ = type_
        self.sample_num = sample_num
        self.bw_mb_sec = bw_mb_sec
        self.cum_avg = cum_avg
        self.access_time_ms = access_time_ms
        self.cum_min = 0.0
        self.cum_max = 0.0
        self.cum_acc_time_ms = 0.0
