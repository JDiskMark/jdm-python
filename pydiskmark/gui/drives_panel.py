"""DrivesPanel - drive selection and info tab.

Mirrors jdm-java Drives tab / SelectDriveFrame.

Startup performance
-------------------
refresh() is split into two phases:

  FAST (main thread, <5 ms):
    Enumerate drives and call shutil.disk_usage() only.
    Table is populated immediately with "..." placeholders for model/fs/bus/sectors.
    The selected drive is determined from app.location_dir.

  SLOW (background daemon thread):
    Per-drive: get_drive_model, get_filesystem, get_bus_type, get_sector_sizes.
    The selected drive is fetched first so the info card fills in quickly.
    Each result is pushed back to the main thread via after(0, ...).
    A refresh_gen counter lets stale callbacks from cancelled refreshes be ignored.
"""
from __future__ import annotations

import os
import platform
import queue
import shutil
import string
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Drive enumeration - fast (disk-usage only)
# ---------------------------------------------------------------------------

def _list_drives_fast() -> list[dict]:
    """Return list of dicts with path + disk-usage data (no model/fs)."""
    drives: list[dict] = []
    system = platform.system()
    candidates: list[str] = []

    if system == "Windows":
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
        # On Linux, only include mounts backed by a real physical block device
        # that is a meaningful benchmark target.
        # /proc/mounts columns: device  mountpoint  fstype  options  ...
        #
        # Excluded device prefixes (virtual/non-physical devices):
        #   /dev/loop* — snap squashfs mounts (Ubuntu has 20-30+ of these)
        #   /dev/ram*  — RAM disks
        #   /dev/zram* — compressed RAM swap devices
        #
        # Excluded mountpoint prefixes (system partitions — real devices but
        # not useful benchmark targets):
        #   /boot      — covers both /boot and /boot/efi
        #
        # Deduplicate by device so bind-mounts of the same partition don't
        # appear twice.
        _EXCLUDE_DEV_PREFIXES   = ("/dev/loop", "/dev/ram", "/dev/zram")
        _EXCLUDE_MOUNT_PREFIXES = ("/boot",)
        seen_devices: set[str] = set()
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        device, mountpoint = parts[0], parts[1]
                        if (
                            device.startswith("/dev/")
                            and not any(device.startswith(p) for p in _EXCLUDE_DEV_PREFIXES)
                            and not any(mountpoint.startswith(p) for p in _EXCLUDE_MOUNT_PREFIXES)
                            and device not in seen_devices
                        ):
                            seen_devices.add(device)
                            candidates.append(mountpoint)
        except Exception:
            candidates = ["/"]

    for path in candidates:
        try:
            usage = shutil.disk_usage(path)
            total_gb = usage.total / (1024 ** 3)
            used_gb  = usage.used  / (1024 ** 3)
            free_gb  = usage.free  / (1024 ** 3)
            pct = (usage.used / usage.total * 100) if usage.total else 0
            drives.append({
                "path":     path,
                "total_gb": total_gb,
                "used_gb":  used_gb,
                "free_gb":  free_gb,
                "pct":      pct,
                # slow fields filled by background thread
                "model":      "...",
                "filesystem": "...",
                "bus_type":   "...",
                "sectors":    "...",
            })
        except (PermissionError, OSError):
            pass

    return drives


def _fetch_slow_metadata(drive: dict) -> None:
    """Populate slow metadata fields in-place for a single drive dict."""
    path = drive["path"]
    try:
        from ..util import get_drive_model
        drive["model"] = get_drive_model(path) or path
    except Exception:
        drive["model"] = path

    try:
        from ..util import get_filesystem, get_bus_type, get_sector_sizes
        drive["filesystem"] = get_filesystem(path)
        drive["bus_type"]   = get_bus_type(path)
        drive["sectors"]    = get_sector_sizes(path)
    except Exception:
        drive.setdefault("filesystem", "-")
        drive.setdefault("bus_type",   "-")
        drive.setdefault("sectors",    "-")


def _resolve_location_for_mount(mount: str) -> str:
    """Return a writable user-accessible location on the volume at *mount*.

    Priority for all platforms:
      1. User's home dir, if it lives on this mount / drive letter.
      2. Mount root itself, if it is directly writable by the current user.
      3. Home dir as an unconditional safe fallback.

    This prevents data_dir being set to e.g. '/pdm-data' or 'C:\\pdm-data',
    which would require elevated privileges to create.
    """
    import platform as _platform
    home = Path.home()
    system = _platform.system()

    try:
        if system == "Linux":
            # Compare mount points via /proc/mounts (longest-prefix match).
            home_mount = _get_home_mount()
            if home_mount and str(Path(mount)) == home_mount:
                return str(home)

        elif system == "Windows":
            # Compare drive-letter roots, e.g. 'C:\\' == 'C:\\'.
            home_root = str(home.anchor).upper()        # e.g. 'C:\\'
            mount_root = str(Path(mount).anchor).upper()
            if home_root == mount_root:
                return str(home)

        elif system == "Darwin":
            # On macOS the root volume appears as '/' and external drives as
            # '/Volumes/Name'.  Home (/Users/…) lives on the root volume.
            home_str = str(home)
            mount_str = str(Path(mount))
            if home_str.startswith(mount_str.rstrip("/") + "/") or mount_str == "/":
                return str(home)

    except Exception:
        pass

    # Mount root directly writable (e.g. a data partition owned by the user)?
    if os.access(mount, os.W_OK):
        return mount

    # Safe fallback — home is always writable for the current user.
    return str(home)


def _get_home_mount() -> str:
    """Return the mount point of the user's home directory by reading /proc/mounts."""
    home = str(Path.home())
    best = ""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mp = parts[1]
                    if home.startswith(mp) and len(mp) > len(best):
                        best = mp
    except Exception:
        pass
    return best


def _check_access(path: str) -> tuple[bool, bool]:
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
        self._drives: list[dict] = []
        self._bg_thread: Optional[threading.Thread] = None
        # Thread-safe queue: background thread puts completed drive dicts here;
        # _poll_update_queue (main thread) drains it via after().
        self._update_queue: queue.Queue = queue.Queue()
        # Incremented on each refresh; lets callbacks detect and ignore stale results.
        self._refresh_gen = 0

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI construction
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
        info_frame.configure(width=260)

        self._model_label     = ttk.Label(info_frame, text="Model: -", wraplength=250)
        self._model_label.pack(anchor="w", pady=1)
        self._partition_label = ttk.Label(info_frame, text="Partition: -")
        self._partition_label.pack(anchor="w", pady=1)
        self._access_label    = ttk.Label(info_frame, text="Access: -")
        self._access_label.pack(anchor="w", pady=1)
        self._usage_label     = ttk.Label(info_frame, text="Usage: -")
        self._usage_label.pack(anchor="w", pady=1)

        ttk.Separator(info_frame, orient="horizontal").pack(fill=tk.X, pady=5)
        self._usage_bar = ttk.Progressbar(
            info_frame, maximum=100, mode="determinate", length=240,
        )
        self._usage_bar.pack(anchor="w")
        self._usage_pct_label = ttk.Label(info_frame, text="")
        self._usage_pct_label.pack(anchor="w", pady=2)

        ttk.Separator(info_frame, orient="horizontal").pack(fill=tk.X, pady=5)
        self._fs_label      = ttk.Label(info_frame, text="File System: -")
        self._fs_label.pack(anchor="w", pady=1)
        self._bus_label     = ttk.Label(info_frame, text="Interface: -")
        self._bus_label.pack(anchor="w", pady=1)
        self._sectors_label = ttk.Label(info_frame, text="Sector Size: -")
        self._sectors_label.pack(anchor="w", pady=1)

        # All drives table
        table_frame = ttk.LabelFrame(middle, text="All Drives", padding=5)
        table_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        cols = ("model", "drive", "total", "used", "free", "pct")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings",
            selectmode="browse", height=8,
        )
        self._tree.heading("model", text="Model")
        self._tree.heading("drive", text="Drive / Mount")
        self._tree.heading("total", text="Total (GB)")
        self._tree.heading("used",  text="Used (GB)")
        self._tree.heading("free",  text="Free (GB)")
        self._tree.heading("pct",   text="Usage %")

        self._tree.column("model", width=180, anchor="w")
        self._tree.column("drive", width=70,  anchor="w")
        self._tree.column("total", width=75,  anchor="e")
        self._tree.column("used",  width=70,  anchor="e")
        self._tree.column("free",  width=70,  anchor="e")
        self._tree.column("pct",   width=58,  anchor="e")

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
        """Reload drive list.

        Fast phase (shutil.disk_usage only) runs on the main thread in <5 ms.
        Slow phase (model, filesystem, bus, sectors) runs in a background daemon
        thread and patches each row/info-card via after(0,...) as results arrive.
        The selected drive is processed first so the info card fills quickly.
        """
        import pydiskmark.app as app

        # Cancel any previous background refresh
        self._refresh_gen += 1
        gen = self._refresh_gen

        # ---- FAST: shutil.disk_usage only, runs immediately ----
        self._drives = _list_drives_fast()

        self._update_combo_labels()

        for item in self._tree.get_children():
            self._tree.delete(item)
        for d in self._drives:
            self._tree.insert("", tk.END, values=(
                d["model"],
                d["path"],
                f"{d['total_gb']:.1f}",
                f"{d['used_gb']:.1f}",
                f"{d['free_gb']:.1f}",
                f"{d['pct']:.1f}%",
            ))

        loc = app.location_dir or str(Path.home())
        self._select_by_path(loc)
        self._dir_var.set(app.data_dir or str(Path(loc) / "pdm-data"))

        # ---- SLOW: fetch per-drive metadata in background ----
        selected_idx = self._selected_index()
        ordered = (
            ([self._drives[selected_idx]] if selected_idx >= 0 else []) +
            [d for i, d in enumerate(self._drives) if i != selected_idx]
        )
        tree_iids = list(self._tree.get_children())

        def _bg_worker() -> None:
            for drive in ordered:
                if self._refresh_gen != gen:
                    return   # newer refresh started; abandon
                _fetch_slow_metadata(drive)
                # Hand result to the main thread via the queue (thread-safe).
                # Never call self.after() from here — Tkinter is not thread-safe.
                self._update_queue.put((drive, tree_iids, gen))

        self._bg_thread = threading.Thread(target=_bg_worker, daemon=True)
        self._bg_thread.start()

        # Start the main-thread poller (after() is always safe from the main thread).
        self.after(50, self._poll_update_queue)

    def _poll_update_queue(self) -> None:
        """Main-thread poller: drains any completed drive updates from the queue.

        Reschedules itself every 50 ms while the background thread is alive,
        then stops automatically once the thread finishes and the queue is empty.
        """
        try:
            while True:
                drive, iids, gen = self._update_queue.get_nowait()
                self._patch_drive_row(drive, iids, gen)
        except queue.Empty:
            pass
        # Keep polling until the background thread has finished.
        if self._bg_thread and self._bg_thread.is_alive():
            self.after(50, self._poll_update_queue)

    def _patch_drive_row(self, drive: dict, iids: list[str], gen: int) -> None:
        """Called on the main thread (via after) to update one completed drive row."""
        if self._refresh_gen != gen:
            return   # stale
        try:
            idx = self._drives.index(drive)
        except ValueError:
            return

        if idx < len(iids):
            self._tree.item(iids[idx], values=(
                drive["model"],
                drive["path"],
                f"{drive['total_gb']:.1f}",
                f"{drive['used_gb']:.1f}",
                f"{drive['free_gb']:.1f}",
                f"{drive['pct']:.1f}%",
            ))

        self._update_combo_labels()

        if drive["path"] == self._selected_path:
            self._update_drive_info_card(drive)

    def _update_combo_labels(self) -> None:
        """Rebuild the combobox value list from current drive data."""
        cur = self._drive_combo.current()
        labels = [
            f"{d['path']}  -  {d['model']}  -  {d['total_gb']:.0f} GB"
            for d in self._drives
        ]
        self._drive_combo["values"] = labels
        if cur >= 0:
            self._drive_combo.current(cur)

    def _selected_index(self) -> int:
        if not self._selected_path:
            return -1
        for i, d in enumerate(self._drives):
            if d["path"] == self._selected_path:
                return i
        return -1

    # ------------------------------------------------------------------
    # Drive selection helpers
    # ------------------------------------------------------------------

    def _select_by_path(self, path: str) -> None:
        """Highlight the drive row that best matches path."""
        best_idx = 0
        best_len = 0
        for i, d in enumerate(self._drives):
            dp = d["path"].rstrip("\\/")
            if path.lower().startswith(dp.lower()) and len(dp) > best_len:
                best_idx = i
                best_len = len(dp)

        if self._drives:
            self._update_drive_info_card(self._drives[best_idx])
            self._drive_combo.current(best_idx)
            children = self._tree.get_children()
            if children and best_idx < len(children):
                self._tree.selection_set(children[best_idx])

    def _update_drive_info_card(self, drive: dict) -> None:
        """Update the left-side drive info card from a drive dict."""
        import pydiskmark.app as app

        self._selected_path = drive["path"]
        model = drive.get("model") or drive["path"]

        # Resolve a writable user-accessible location on this drive *before*
        # checking access.  The raw mount point (e.g. '/') is often not
        # writable by the current user; we prefer home when it lives on the
        # same volume.  Access is then assessed on this resolved path so the
        # card reflects where the benchmark will actually write.
        location = _resolve_location_for_mount(drive["path"])
        can_read, can_write = _check_access(location)

        # Show the mount point as the partition label; the resolved location
        # is visible in the Test Directory field below.
        mount_str = drive["path"].rstrip("\\/") or drive["path"]
        self._model_label.config(text=f"Model: {model}")
        self._partition_label.config(text=f"Partition: {mount_str}")
        read_str  = "Read OK"  if can_read  else "Read X"
        write_str = "Write OK" if can_write else "Write X"
        self._access_label.config(text=f"Access: {read_str}  {write_str}")
        self._usage_label.config(
            text=f"Usage: {drive['pct']:.0f}%  "
                 f"{drive['used_gb']:.0f} / {drive['total_gb']:.0f} GB"
        )

        pct = min(100, max(0, drive["pct"]))
        self._usage_bar["value"] = pct
        self._usage_pct_label.config(text=f"{pct:.0f}%")

        self._fs_label.config(     text=f"File System: {drive.get('filesystem', '-')}")
        self._bus_label.config(    text=f"Interface: {drive.get('bus_type', '-')}")
        self._sectors_label.config(text=f"Sector Size: {drive.get('sectors', '-')}")

        app.set_location_dir(location)
        data_dir = str(Path(location) / "pdm-data")
        self._dir_var.set(data_dir)
        self._on_location_change(location)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_combo_select(self, _event=None) -> None:
        idx = self._drive_combo.current()
        if 0 <= idx < len(self._drives):
            self._update_drive_info_card(self._drives[idx])
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
            self._update_drive_info_card(self._drives[idx])
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
