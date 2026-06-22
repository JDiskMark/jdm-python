"""ControlPanel — benchmark settings and results display.

Mirrors jdm-java's BenchmarkControlPanel: profile/type/threads/order/blocks/
block-size/samples dropdowns, a Start/Stop button, and a 3-column results grid
showing Bandwidth / Latency / IOPS for Write and Read.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

import pydiskmark.app as app
from ..benchmark import BenchmarkType, BlockSequence
from ..benchmark_profile import BenchmarkProfile


# ---------------------------------------------------------------------------
# Dropdown option lists (match jdm-java's BenchmarkControlPanel)
# ---------------------------------------------------------------------------

THREAD_OPTIONS = [1, 2, 4, 8, 16, 32]
BLOCK_COUNT_OPTIONS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048]
BLOCK_SIZE_OPTIONS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048]
SAMPLE_OPTIONS = [25, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000]


class ControlPanel(ttk.Frame):
    """Settings panel with dropdowns, start button, and results grid."""

    def __init__(
        self,
        parent: tk.Widget,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._on_start = on_start
        self._on_stop = on_stop
        self._running = False

        self._build_ui()
        self.refresh_from_app()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── 3-column grid (top section: combo rows) ──────────────────────
        #
        #   col 0 : narrow anchor — holds "Profile" / "Type" labels alone
        #   col 1 : 40 % of remaining space  ┐  Profile/Type combos span 1+2
        #   col 2 : 60 % of remaining space  ┘  Threads→Samples combos at col 2 only
        #
        #   Row layout:
        #     Profile / Type  → label@col0,       combo@cols1+2 (wide)
        #     Threads→Samples → label@cols0+1,    combo@col2    (narrower)
        #
        # The results_frame (below separator) is an independent inner frame
        # with its own 3-column sizing — it does NOT share these column widths.
        row = 0

        # ── Top-section column weights ─────────────────────────────────────
        #   col 0 : fixed narrow anchor (just "Profile" / "Type" label width)
        #   col 1 : 40 % of expandable space → label overflow for rows 3-7
        #   col 2 : 60 % of expandable space → combo-only column for rows 3-7
        self.columnconfigure(0, weight=0, minsize=65)
        self.columnconfigure(1, weight=4, minsize=65)
        self.columnconfigure(2, weight=1)

        # --- Profile ---
        ttk.Label(self, text="Profile").grid(row=row, column=0, sticky="w", padx=(5, 2), pady=2)
        self._profile_var = tk.StringVar()
        profiles = [p.display_name for p in BenchmarkProfile.get_defaults()]
        self._profile_combo = ttk.Combobox(
            self, textvariable=self._profile_var,
            values=profiles, state="readonly",
        )
        self._profile_combo.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 5), pady=2)
        self._profile_combo.bind("<<ComboboxSelected>>", self._on_profile_change)
        row += 1

        # --- Type ---
        ttk.Label(self, text="Type").grid(row=row, column=0, sticky="w", padx=(5, 2), pady=2)
        self._type_var = tk.StringVar()
        self._type_combo = ttk.Combobox(
            self, textvariable=self._type_var,
            values=[t.value for t in BenchmarkType], state="readonly",
        )
        self._type_combo.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 5), pady=2)
        row += 1

        # --- Threads ---
        ttk.Label(self, text="Threads").grid(row=row, column=0, columnspan=2, sticky="w", padx=(5, 2), pady=2)
        self._threads_var = tk.StringVar()
        self._threads_combo = ttk.Combobox(
            self, textvariable=self._threads_var,
            values=[str(t) for t in THREAD_OPTIONS], state="readonly",
        )
        self._threads_combo.grid(row=row, column=2, sticky="ew", padx=(0, 5), pady=2)
        row += 1

        # --- Block Order ---
        ttk.Label(self, text="Block Order").grid(row=row, column=0, columnspan=2, sticky="w", padx=(5, 2), pady=2)
        self._order_var = tk.StringVar()
        self._order_combo = ttk.Combobox(
            self, textvariable=self._order_var,
            values=[s.value for s in BlockSequence], state="readonly",
        )
        self._order_combo.grid(row=row, column=2, sticky="ew", padx=(0, 5), pady=2)
        row += 1

        # --- Blocks / Sample ---
        ttk.Label(self, text="Blocks / Sample").grid(row=row, column=0, columnspan=2, sticky="w", padx=(5, 2), pady=2)
        self._blocks_var = tk.StringVar()
        self._blocks_combo = ttk.Combobox(
            self, textvariable=self._blocks_var,
            values=[str(b) for b in BLOCK_COUNT_OPTIONS], state="readonly",
        )
        self._blocks_combo.grid(row=row, column=2, sticky="ew", padx=(0, 5), pady=2)
        row += 1

        # --- Block Size (KB) ---
        ttk.Label(self, text="Block Size (KB)").grid(row=row, column=0, columnspan=2, sticky="w", padx=(5, 2), pady=2)
        self._block_size_var = tk.StringVar()
        self._block_size_combo = ttk.Combobox(
            self, textvariable=self._block_size_var,
            values=[str(s) for s in BLOCK_SIZE_OPTIONS], state="readonly",
        )
        self._block_size_combo.grid(row=row, column=2, sticky="ew", padx=(0, 5), pady=2)
        row += 1

        # --- Samples ---
        ttk.Label(self, text="Samples").grid(row=row, column=0, columnspan=2, sticky="w", padx=(5, 2), pady=2)
        self._samples_var = tk.StringVar()
        self._samples_combo = ttk.Combobox(
            self, textvariable=self._samples_var,
            values=[str(s) for s in SAMPLE_OPTIONS], state="readonly",
        )
        self._samples_combo.grid(row=row, column=2, sticky="ew", padx=(0, 5), pady=2)
        row += 1

        # --- Start / Stop Button ---
        self._start_btn = ttk.Button(self, text="Start", command=self._on_start_stop)
        self._start_btn.grid(row=row, column=0, columnspan=3, sticky="ew", padx=5, pady=(10, 5))
        row += 1

        # --- Separator ---
        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=5, pady=5,
        )
        row += 1

        # ── Results inner frame (independent grid — not constrained by above cols) ──
        results_frame = ttk.Frame(self)
        results_frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=5)

        # Header: blank metric cell | Write | Read
        ttk.Label(results_frame, text="").grid(row=0, column=0, sticky="w", padx=3)
        ttk.Label(results_frame, text="Write", font=("", 9, "bold")).grid(
            row=0, column=1, sticky="ew", padx=3,
        )
        ttk.Label(results_frame, text="Read", font=("", 9, "bold")).grid(
            row=0, column=2, sticky="ew", padx=3,
        )

        # Bandwidth
        ttk.Label(results_frame, text="Bandwidth (MB/s)").grid(row=1, column=0, sticky="w", padx=3, pady=1)
        self._w_bw_label = ttk.Label(results_frame, text="— —", anchor="center")
        self._w_bw_label.grid(row=1, column=1, sticky="ew", padx=3, pady=1)
        self._r_bw_label = ttk.Label(results_frame, text="— —", anchor="center")
        self._r_bw_label.grid(row=1, column=2, sticky="ew", padx=3, pady=1)

        # Latency
        ttk.Label(results_frame, text="Latency (ms)").grid(row=2, column=0, sticky="w", padx=3, pady=1)
        self._w_lat_label = ttk.Label(results_frame, text="— —", anchor="center")
        self._w_lat_label.grid(row=2, column=1, sticky="ew", padx=3, pady=1)
        self._r_lat_label = ttk.Label(results_frame, text="— —", anchor="center")
        self._r_lat_label.grid(row=2, column=2, sticky="ew", padx=3, pady=1)

        # IOPS
        ttk.Label(results_frame, text="IOPS").grid(row=3, column=0, sticky="w", padx=3, pady=1)
        self._w_iops_label = ttk.Label(results_frame, text="— —", anchor="center")
        self._w_iops_label.grid(row=3, column=1, sticky="ew", padx=3, pady=1)
        self._r_iops_label = ttk.Label(results_frame, text="— —", anchor="center")
        self._r_iops_label.grid(row=3, column=2, sticky="ew", padx=3, pady=1)

        # Results inner grid: metric label is fixed, Write/Read share remaining space
        # Write column wider, Read column narrower
        results_frame.columnconfigure(1, weight=2, minsize=80)
        results_frame.columnconfigure(2, weight=1, minsize=50)



    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_profile_change(self, _event=None) -> None:
        """Load the selected profile into app state and refresh combos."""
        display = self._profile_var.get()
        try:
            profile = next(
                p for p in BenchmarkProfile.get_defaults()
                if p.display_name == display
            )
        except StopIteration:
            return
        app.load_profile(profile)
        self.refresh_from_app()

    def _on_start_stop(self) -> None:
        if self._running:
            self._on_stop()
        else:
            self.apply_to_app()
            self._on_start()

    # ------------------------------------------------------------------
    # State sync
    # ------------------------------------------------------------------

    def apply_to_app(self) -> None:
        """Write current combo values into app global state."""
        # Type
        type_val = self._type_var.get()
        for bt in BenchmarkType:
            if bt.value == type_val:
                app.benchmark_type = bt
                break

        # Threads
        try:
            app.num_of_threads = int(self._threads_var.get())
        except ValueError:
            pass

        # Order
        order_val = self._order_var.get()
        for bs in BlockSequence:
            if bs.value == order_val:
                app.block_sequence = bs
                break

        # Blocks
        try:
            app.num_of_blocks = int(self._blocks_var.get())
        except ValueError:
            pass

        # Block size
        try:
            app.block_size_kb = int(self._block_size_var.get())
        except ValueError:
            pass

        # Samples
        try:
            app.num_of_samples = int(self._samples_var.get())
        except ValueError:
            pass

    def refresh_from_app(self) -> None:
        """Read current app state into the combo boxes."""
        self._profile_var.set(app.active_profile.display_name)
        self._type_var.set(app.benchmark_type.value)
        self._threads_var.set(str(app.num_of_threads))
        self._order_var.set(app.block_sequence.value)
        self._blocks_var.set(str(app.num_of_blocks))
        self._block_size_var.set(str(app.block_size_kb))
        self._samples_var.set(str(app.num_of_samples))

    def load_settings_from_data(self, data: dict) -> None:
        """Restore dropdowns and app state from a saved benchmark dict.

        *data* is the top-level dict produced by exporter.benchmark_to_dict()
        (keys: config, driveInfo, operations, …).  Missing keys are silently
        ignored so an incomplete record never crashes the UI.
        """
        from ..benchmark import BenchmarkType, BlockSequence
        from ..benchmark_profile import BenchmarkProfile

        cfg = data.get("config", {})

        # --- Profile ---
        profile_sym = cfg.get("profile", "")
        try:
            profile = BenchmarkProfile.from_symbol(profile_sym)
            app.load_profile(profile)
        except (ValueError, AttributeError):
            pass  # unrecognised profile — leave current selection

        # --- Type (overrides the profile default when present) ---
        type_val = cfg.get("benchmarkType", "")
        for bt in BenchmarkType:
            if bt.value == type_val:
                app.benchmark_type = bt
                break

        # --- Threads ---
        try:
            app.num_of_threads = int(cfg["numThreads"])
        except (KeyError, ValueError, TypeError):
            pass

        # --- Block order ---
        order_val = cfg.get("blockOrder", "")
        for bs in BlockSequence:
            if bs.value == order_val or bs.name == order_val:
                app.block_sequence = bs
                break

        # --- Blocks ---
        try:
            app.num_of_blocks = int(cfg["numBlocks"])
        except (KeyError, ValueError, TypeError):
            pass

        # --- Block size ---
        try:
            app.block_size_kb = int(cfg["blockSizeKb"])
        except (KeyError, ValueError, TypeError):
            pass

        # --- Samples ---
        try:
            app.num_of_samples = int(cfg["numSamples"])
        except (KeyError, ValueError, TypeError):
            pass

        # Reflect updated app state back into the combo boxes
        self.refresh_from_app()

    def refresh_write_metrics(self) -> None:
        """Update write results labels from app state."""
        self._w_bw_label.config(
            text=f"{app.w_avg:.1f}" if app.w_avg != -1 else "— —",
        )
        self._w_lat_label.config(
            text=f"{app.w_acc:.3f}" if app.w_acc != -1 else "— —",
        )
        self._w_iops_label.config(
            text=str(app.w_iops) if app.w_iops != -1 else "— —",
        )

    def refresh_read_metrics(self) -> None:
        """Update read results labels from app state."""
        self._r_bw_label.config(
            text=f"{app.r_avg:.1f}" if app.r_avg != -1 else "— —",
        )
        self._r_lat_label.config(
            text=f"{app.r_acc:.3f}" if app.r_acc != -1 else "— —",
        )
        self._r_iops_label.config(
            text=str(app.r_iops) if app.r_iops != -1 else "— —",
        )

    def set_running(self, running: bool) -> None:
        """Toggle button text and disable/enable controls."""
        self._running = running
        self._start_btn.config(text="Stop" if running else "Start")

        state = "disabled" if running else "readonly"
        for combo in (
            self._profile_combo, self._type_combo, self._threads_combo,
            self._order_combo, self._blocks_combo, self._block_size_combo,
            self._samples_combo,
        ):
            combo.config(state=state)

    def reset_metrics(self) -> None:
        """Reset all result labels to blank."""
        for label in (
            self._w_bw_label, self._w_lat_label, self._w_iops_label,
            self._r_bw_label, self._r_lat_label, self._r_iops_label,
        ):
            label.config(text="— —")


