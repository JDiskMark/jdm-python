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
    EVT_CACHE_DROP, EVT_COMPLETE, EVT_ERROR,
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

    _STRIP_W    = 36           # pixel width of the strip
    _TAB_PAD    = 20           # vertical padding above/below text
    _FONT       = ("Segoe UI", 9)

    # Dark-mode defaults; updated in retheme()
    _ACTIVE_BG  = "#005fb8"
    _INACTIVE_BG = "#1c1c1c"
    _HOVER_BG   = "#2b2b2b"
    _ACTIVE_FG  = "#ffffff"
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
        tab_h = max(90, len(text) * 10 + self._TAB_PAD * 2)
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
        # Re-select to repaint all buttons with current colour vars
        if self._active_idx >= 0:
            self.select(self._active_idx)


class MainWindow:
    """Main application window."""

    def __init__(self) -> None:
        self._root = tk.Tk()

        # Window title includes arch + CPU (like jdm-java)
        cpu = app.processor_name or "Unknown CPU"
        self._root.title(
            f"pydiskmark {app.VERSION}  —  {app.arch}  —  {cpu}"
        )
        self._root.geometry("1024x680")
        self._root.minsize(800, 500)

        # Apply dark theme
        theme.apply_dark_theme()

        # Set icon (best-effort)
        self._set_icon()

        # Listener and run state
        self._listener = GuiListener()
        self._benchmark = None
        self._worker_thread: Optional[threading.Thread] = None
        self._target_tx_kb: int = 0

        # Build UI — bottom items must be packed before main content
        # so pack(side=BOTTOM) anchors correctly
        self._build_menu()
        self._build_bottom_bar()      # creates _status_label — must come first
        self._build_bottom_tabs()     # packs above bottom bar
        self._build_main_content()    # DrivesPanel.refresh() fires here — _status_label already exists

        # Keyboard shortcuts
        self._root.bind("<Control-r>", lambda _: self._start_benchmark())
        self._root.bind("<Escape>", lambda _: self._stop_benchmark())

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()

    # ------------------------------------------------------------------
    # Icon
    # ------------------------------------------------------------------

    def _set_icon(self) -> None:
        for candidate in [
            Path(r"c:\Users\james\git\jdm-media\icons\jdm-turtle-logo.ico"),
            Path(__file__).resolve().parents[4] / "jdm-media/icons/jdm-turtle-logo.ico",
        ]:
            if candidate.exists():
                try:
                    self._root.iconbitmap(str(candidate))
                    return
                except tk.TclError:
                    pass

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self._root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export...\tCtrl+E", command=self._export_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        action_menu = tk.Menu(menubar, tearoff=0)
        action_menu.add_command(label="Start\tCtrl+R", command=self._start_benchmark)
        action_menu.add_command(label="Stop\tEsc", command=self._stop_benchmark)
        menubar.add_cascade(label="Action", menu=action_menu)

        options_menu = tk.Menu(menubar, tearoff=0)
        options_menu.add_command(label="Toggle Theme", command=self._toggle_theme)
        menubar.add_cascade(label="Options", menu=options_menu)

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
        ctrl_frame = ttk.Frame(bench_page, width=340)
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
            ops_frame, on_load=self._load_from_history,
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

    # ------------------------------------------------------------------
    # Queue polling — bridge between worker thread and Tkinter
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        events = self._listener.drain()

        should_reschedule = True

        for event in events:
            evt_type = event[0]

            if evt_type == EVT_SAMPLE:
                sample = event[1]
                self._chart.add_sample(sample)
                if sample.type_ == IOMode.WRITE:
                    self._controls.refresh_write_metrics()
                else:
                    self._controls.refresh_read_metrics()

            elif evt_type == EVT_PROGRESS:
                completed = event[1]
                self._progress_var.set(completed)
                done_kb = int(self._target_tx_kb * completed / 100)
                self._tx_label.config(
                    text=f"Total Tx (KB): {done_kb:,} / {self._target_tx_kb:,}"
                )

            elif evt_type == EVT_CACHE_DROP:
                done_event = event[1]
                messagebox.showinfo(
                    "Clear Disk Cache",
                    "For a valid READ benchmark, please clear the disk cache now.\n\n"
                    "  Linux:   sudo sh -c 'sync; echo 1 > /proc/sys/vm/drop_caches'\n"
                    "  macOS:   sudo purge\n"
                    "  Windows: EmptyStandbyList.exe or RAMMap.exe\n\n"
                    "Click OK when the cache has been cleared.",
                    parent=self._root,
                )
                done_event.set()

            elif evt_type == EVT_COMPLETE:
                self._benchmark = event[1]
                self._on_benchmark_complete()
                should_reschedule = False
                break

            elif evt_type == EVT_ERROR:
                self._on_benchmark_error(event[1])
                should_reschedule = False
                break

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

    def _load_from_history(self, db_id: int) -> None:
        """Replay a historical benchmark into the chart."""
        try:
            from .. import db
            data = db.load_benchmark_json(db_id)
        except Exception:
            data = None

        if not data:
            messagebox.showwarning("Load Error", "Could not load benchmark data.", parent=self._root)
            return

        self._chart.clear()
        self._controls.reset_metrics()

        # Reconstruct samples for the chart from the JSON
        for op in data.get("operations", []):
            mode_str = op.get("ioMode", "WRITE")
            mode = IOMode.WRITE if mode_str == "WRITE" else IOMode.READ
            for s in op.get("samples", []):
                # Build a lightweight sample-like object
                sample = _HistorySample(
                    type_=mode,
                    sample_num=s.get("sn", 0),
                    bw_mb_sec=s.get("bw", 0.0),
                    cum_avg=s.get("bt", 0.0),
                    access_time_ms=s.get("la", 0.0),
                )
                self._chart.add_sample(sample)

        self._chart.flush()

        # Update chart title
        di = data.get("driveInfo", {})
        model = di.get("driveModel", "—")
        pct = di.get("percentUsed", 0)
        used = di.get("usedGb", 0)
        total = di.get("totalGb", 0)
        self._chart.set_title(f"{model}  —  {pct:.0f}%  ({used:.0f}/{total:.0f} GB)")

        self._log_event(f"Loaded benchmark #{db_id} from history")

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

    def _show_about(self) -> None:
        """Show the About dialog centred on the main window."""
        dlg = tk.Toplevel(self._root)
        dlg.title("About pydiskmark")
        dlg.resizable(False, False)
        dlg.transient(self._root)   # stays above parent
        dlg.grab_set()              # modal

        ttk.Label(dlg, text=f"pydiskmark  {app.VERSION}",
                  font=("", 13, "bold")).pack(padx=30, pady=(20, 5))
        ttk.Label(dlg, text=f"Python: {sys.version.split()[0]}").pack(pady=2)
        ttk.Label(dlg, text=f"OS: {app.os_name}  {app.arch}").pack(pady=2)
        ttk.Label(dlg, text=f"CPU: {app.processor_name}").pack(pady=2)
        ttk.Separator(dlg, orient="horizontal").pack(fill=tk.X, padx=20, pady=10)
        ttk.Label(dlg, text="Apache License 2.0", foreground="gray").pack(pady=(0, 5))
        ttk.Button(dlg, text="OK", command=dlg.destroy, width=10).pack(pady=(0, 20))

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
