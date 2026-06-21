"""HistoryPanel — benchmark operations history table.

Mirrors jdm-java's "Benchmark Operations" tab.
Clicking a row loads that benchmark into the chart and settings panels.
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Callable, Optional


class HistoryPanel(ttk.Frame):
    """Treeview listing past benchmark operations with load-on-click."""

    COLUMNS = (
        ("drive", "Drive Model", 160),
        ("profile", "Profile", 80),
        ("type", "Type", 65),
        ("order", "Order", 85),
        ("samples", "Samples", 65),
        ("blocks", "Blocks (Size)", 90),
        ("threads", "Thread", 55),
        ("start", "Start Time", 140),
        ("elapsed", "Time (ms)", 75),
        ("lat", "Lat (ms)", 70),
        ("iops", "IOPS", 65),
        ("bw", "BW (MB/s)", 80),
    )

    def __init__(
        self,
        parent: tk.Widget,
        on_load: Callable[[int], None],
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._on_load = on_load
        self._row_ids: list[int] = []  # maps treeview row index → DB id

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

        for col_id, heading, width in self.COLUMNS:
            self._tree.heading(col_id, text=heading)
            anchor = "w" if col_id in ("drive", "profile", "start") else "e"
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

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Return>", self._on_double_click)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload history from the DB."""
        try:
            from .. import db
            rows = db.load_history()
        except Exception:
            rows = []

        # Clear
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._row_ids.clear()

        for r in rows:
            block_label = f"{r['num_blocks']} ({r['num_blocks'] * r['block_size_kb'] * 1024})"
            start = r.get("start_time", "") or ""
            # Trim ISO timestamp to human-friendly
            try:
                dt = datetime.fromisoformat(start)
                start = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

            self._tree.insert("", tk.END, values=(
                r["drive_model"] or "—",
                r["profile"] or "—",
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
            self._row_ids.append(r["id"])

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_double_click(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        children = list(self._tree.get_children())
        try:
            idx = children.index(sel[0])
            db_id = self._row_ids[idx]
            self._on_load(db_id)
        except (ValueError, IndexError):
            pass
