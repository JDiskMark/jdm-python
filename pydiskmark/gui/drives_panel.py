"""DrivesPanel — drive selection and info tab.

Mirrors jdm-java's Drives tab / SelectDriveFrame:
  Left:  Drive Info card (model, partition, usage, access)
  Right: All Drives table (Drive/Mount, Total, Used, Free, Usage %)
  Bottom: Test Directory path + Browse button
"""
from __future__ import annotations

import os
import platform
import shutil
import string
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Drive enumeration
# ---------------------------------------------------------------------------

def _list_drives() -> list[dict]:
    """Return list of dicts: path, total_gb, used_gb, free_gb, pct."""
    drives: list[dict] = []
    system = platform.system()

    candidates: list[str] = []
    if system == "Windows":
        # Enumerate by letter
        bitmask = 0
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            pass
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                candidates.append(f"{letter}:\\")
    elif system == "Darwin":
        for vol in Path("/Volumes").iterdir():
            candidates.append(str(vol))
    else:
        # Linux: read /proc/mounts
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith("/"):
                        candidates.append(parts[1])
        except Exception:
            candidates = ["/"]

    for path in candidates:
        try:
            usage = shutil.disk_usage(path)
            total_gb = usage.total / (1024 ** 3)
            used_gb = usage.used / (1024 ** 3)
            free_gb = usage.free / (1024 ** 3)
            pct = (usage.used / usage.total * 100) if usage.total else 0
            drives.append({
                "path": path,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "pct": pct,
            })
        except (PermissionError, OSError):
            pass

    return drives


def _get_drive_model_for(path: str) -> str:
    """Return drive model string for the given path (best-effort)."""
    try:
        from ..util import get_drive_model
        return get_drive_model(path)
    except Exception:
        return "Unknown"


def _check_access(path: str) -> tuple[bool, bool]:
    """Return (can_read, can_write) for path."""
    return os.access(path, os.R_OK), os.access(path, os.W_OK)


# ---------------------------------------------------------------------------
# DrivesPanel widget
# ---------------------------------------------------------------------------

class DrivesPanel(ttk.Frame):
    """Drive selection and info panel (Drives tab content)."""

    def __init__(
        self,
        parent: tk.Widget,
        on_location_change: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._on_location_change = on_location_change
        self._selected_path: Optional[str] = None

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ---- Top: drive selector dropdown ----
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=5, pady=(5, 3))
        ttk.Label(top, text="Drive:").pack(side=tk.LEFT, padx=(0, 5))
        self._drive_var = tk.StringVar()
        self._drive_combo = ttk.Combobox(
            top, textvariable=self._drive_var, state="readonly", width=30,
        )
        self._drive_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._drive_combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        # ---- Middle: drive info (left) + all drives table (right) ----
        middle = ttk.Frame(self)
        middle.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        # Drive info card
        info_frame = ttk.LabelFrame(middle, text="Drive Info", padding=8)
        info_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        info_frame.configure(width=200)

        self._model_label = ttk.Label(info_frame, text="Model: —", wraplength=190)
        self._model_label.pack(anchor="w", pady=1)
        self._partition_label = ttk.Label(info_frame, text="Partition: —")
        self._partition_label.pack(anchor="w", pady=1)
        self._usage_label = ttk.Label(info_frame, text="Usage: —")
        self._usage_label.pack(anchor="w", pady=1)
        self._access_label = ttk.Label(info_frame, text="Access: —")
        self._access_label.pack(anchor="w", pady=1)

        # Usage bar
        ttk.Separator(info_frame, orient="horizontal").pack(fill=tk.X, pady=5)
        self._usage_bar = ttk.Progressbar(
            info_frame, maximum=100, mode="determinate", length=180,
        )
        self._usage_bar.pack(anchor="w")
        self._usage_pct_label = ttk.Label(info_frame, text="")
        self._usage_pct_label.pack(anchor="w", pady=2)

        # All drives table
        table_frame = ttk.LabelFrame(middle, text="All Drives", padding=5)
        table_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        cols = ("drive", "total", "used", "free", "pct")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings",
            selectmode="browse", height=8,
        )
        self._tree.heading("drive", text="Drive / Mount")
        self._tree.heading("total", text="Total (GB)")
        self._tree.heading("used", text="Used (GB)")
        self._tree.heading("free", text="Free (GB)")
        self._tree.heading("pct", text="Usage %")

        self._tree.column("drive", width=100, anchor="w")
        self._tree.column("total", width=85, anchor="e")
        self._tree.column("used", width=85, anchor="e")
        self._tree.column("free", width=85, anchor="e")
        self._tree.column("pct", width=65, anchor="e")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # ---- Bottom: Test Directory ----
        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=5, pady=(3, 5))
        ttk.Label(bottom, text="Test Directory:").pack(side=tk.LEFT)
        self._dir_var = tk.StringVar()
        dir_entry = ttk.Entry(bottom, textvariable=self._dir_var, state="readonly")
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(bottom, text="Browse...", command=self._browse).pack(side=tk.LEFT, padx=(0, 3))

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload drive list and update UI."""
        import pydiskmark.app as app

        self._drives = _list_drives()

        # Populate combo
        labels = []
        for d in self._drives:
            gb = d["total_gb"]
            labels.append(f"{d['path']}  —  {gb:.0f} GB")
        self._drive_combo["values"] = labels

        # Populate treeview
        for item in self._tree.get_children():
            self._tree.delete(item)
        for d in self._drives:
            self._tree.insert("", tk.END, values=(
                d["path"],
                f"{d['total_gb']:.1f}",
                f"{d['used_gb']:.1f}",
                f"{d['free_gb']:.1f}",
                f"{d['pct']:.1f}%",
            ))

        # Select the current app location
        loc = app.location_dir or str(Path.home())
        self._select_by_path(loc)
        self._dir_var.set(app.data_dir or str(Path(loc) / "pdm-data"))

    def _select_by_path(self, path: str) -> None:
        """Highlight the drive row that best matches *path*."""
        best_idx = 0
        best_len = 0
        for i, d in enumerate(self._drives):
            dp = d["path"].rstrip("\\/")
            if path.lower().startswith(dp.lower()) and len(dp) > best_len:
                best_idx = i
                best_len = len(dp)

        if self._drives:
            self._update_drive_info(self._drives[best_idx])
            self._drive_combo.current(best_idx)
            children = self._tree.get_children()
            if children and best_idx < len(children):
                self._tree.selection_set(children[best_idx])

    def _update_drive_info(self, drive: dict) -> None:
        """Update the left-side drive info card."""
        import pydiskmark.app as app

        self._selected_path = drive["path"]
        model = _get_drive_model_for(drive["path"])
        can_read, can_write = _check_access(drive["path"])

        # Derive partition label
        path_str = drive["path"].rstrip("\\/")
        partition = path_str.split(":")[-1].strip("\\/ ") or path_str

        self._model_label.config(text=f"Model: {model}")
        self._partition_label.config(text=f"Partition: {path_str}")
        self._usage_label.config(
            text=f"Usage: {drive['pct']:.0f}%  {drive['used_gb']:.0f} / {drive['total_gb']:.0f} GB"
        )

        read_str = "Read ✓" if can_read else "Read ✗"
        write_str = "Write ✓" if can_write else "Write ✗"
        self._access_label.config(text=f"Access: {read_str}  {write_str}")

        pct = min(100, max(0, drive["pct"]))
        self._usage_bar["value"] = pct
        self._usage_pct_label.config(text=f"{pct:.0f}%")

        # Update app state
        app.set_location_dir(drive["path"])
        data_dir = str(Path(drive["path"]) / "pdm-data")
        self._dir_var.set(data_dir)
        self._on_location_change(drive["path"])

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_combo_select(self, _event=None) -> None:
        idx = self._drive_combo.current()
        if 0 <= idx < len(self._drives):
            self._update_drive_info(self._drives[idx])
            children = self._tree.get_children()
            if children and idx < len(children):
                self._tree.selection_set(children[idx])

    def _on_tree_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        children = list(self._tree.get_children())
        idx = children.index(sel[0])
        if 0 <= idx < len(self._drives):
            self._update_drive_info(self._drives[idx])
            self._drive_combo.current(idx)

    def _browse(self) -> None:
        import pydiskmark.app as app
        path = filedialog.askdirectory(
            title="Select Benchmark Location",
            initialdir=app.location_dir or str(Path.home()),
        )
        if path:
            app.set_location_dir(path)
            self._dir_var.set(app.data_dir)
            self._on_location_change(path)
            self.refresh()
