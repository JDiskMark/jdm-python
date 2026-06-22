"""HistoryPanel — benchmark operations history table.

Mirrors jdm-java's "Benchmark Operations" tab.
Clicking a row loads that benchmark into the chart and settings panels,
and also highlights all other operations from the same benchmark run.
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Callable, Optional


def _profile_display(symbol: Optional[str], BenchmarkProfile) -> str:
    """Convert a stored profile symbol to its friendly display name."""
    if not symbol:
        return "—"
    try:
        return BenchmarkProfile.from_symbol(symbol).display_name
    except (ValueError, AttributeError):
        return symbol or "—"




class HistoryPanel(ttk.Frame):
    """Treeview listing past benchmark operations with load-on-click."""

    COLUMNS = (
        # (id,          heading,        width, anchor)
        ("drive",    "Drive Model",   180,  "w"),
        ("profile",  "Profile",       110,  "w"),
        ("type",     "Type",           46,  "center"),
        ("order",    "Order",          65,  "w"),
        ("samples",  "Samples",        52,  "center"),
        ("blocks",   "Blocks (Size)",  90,  "e"),
        ("threads",  "Thread",         42,  "center"),
        ("start",    "Start Time",    140,  "center"),
        ("elapsed",  "Time (ms)",      58,  "center"),
        ("lat",      "Lat (ms)",       55,  "center"),
        ("iops",     "IOPS",           52,  "center"),
        ("bw",       "BW (MB/s)",      80,  "center"),
    )

    def __init__(
        self,
        parent: tk.Widget,
        on_load: Callable[[int], None],
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._on_load = on_load
        self._benchmark_ids: list[str] = []  # treeview index → benchmark_id (FK)
        self._refreshing: bool = False        # suppress selection events during refresh()

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        col_ids = [c[0] for c in self.COLUMNS]
        self._tree = ttk.Treeview(
            self, columns=col_ids, show="headings",
            selectmode="browse", height=5,
        )

        for col_id, heading, width, anchor in self.COLUMNS:
            self._tree.heading(col_id, text=heading)
            self._tree.column(col_id, width=width, anchor=anchor, minwidth=40)

        yscroll = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self._tree.xview)

        def _ys(f, l):
            if float(f) <= 0.0 and float(l) >= 1.0:
                yscroll.grid_remove()
            else:
                yscroll.grid(row=0, column=1, sticky="ns")
            yscroll.set(f, l)

        def _xs(f, l):
            if float(f) <= 0.0 and float(l) >= 1.0:
                xscroll.grid_remove()
            else:
                xscroll.grid(row=1, column=0, sticky="ew")
            xscroll.set(f, l)

        self._tree.configure(yscrollcommand=_ys, xscrollcommand=_xs)

        self._tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Return>", self._on_select)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload history from the DB."""
        try:
            from .. import db
            from ..benchmark_profile import BenchmarkProfile
            rows = db.load_history()
        except Exception:
            rows = []

        # Suppress selection events while rebuilding the tree
        self._refreshing = True
        try:
            for item in self._tree.get_children():
                self._tree.delete(item)
            self._benchmark_ids.clear()


            for r in rows:
                block_label = f"{r['num_blocks']} ({r['num_blocks'] * r['block_size_kb'] * 1024})"
                start = r.get("start_time", "") or ""
                try:
                    dt = datetime.fromisoformat(start)
                    start = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

                self._tree.insert("", tk.END, values=(
                    r["drive_model"] or "—",
                    _profile_display(r["profile"], BenchmarkProfile),
                    r["io_mode"] or r.get("benchmark_type", "—"),
                    r["block_order"] or "—",
                    r["num_samples"],
                    block_label,
                    r["num_threads"],
                    start,
                    r["elapsed_ms"] if r["elapsed_ms"] is not None else "—",
                    f"{r['lat_avg_ms']:.2f}" if r["lat_avg_ms"] is not None else "—",
                    r["iops"] if r["iops"] else "—",
                    f"{r['bw_mb_sec']:.1f}" if r["bw_mb_sec"] is not None else "—",
                ))
                self._benchmark_ids.append(r.get("benchmark_id", ""))
        finally:
            self._refreshing = False

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_selected_benchmark_id(self) -> Optional[str]:
        """Return the benchmark_id (UUID) of the currently selected row, or None."""
        sel = self._tree.selection()
        if not sel:
            return None
        children = list(self._tree.get_children())
        try:
            idx = children.index(sel[0])
            return self._benchmark_ids[idx] or None
        except (ValueError, IndexError):
            return None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_select(self, _event=None) -> None:
        if self._refreshing:  # ignore events fired during tree rebuild
            return
        sel = self._tree.selection()
        if not sel:
            return

        children = list(self._tree.get_children())
        try:
            idx = children.index(sel[0])
            benchmark_id = self._benchmark_ids[idx]
        except (ValueError, IndexError):
            return

        if benchmark_id:
            self._on_load(benchmark_id)

