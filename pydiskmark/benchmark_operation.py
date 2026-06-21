"""BenchmarkOperation — aggregated results of one I/O phase (READ or WRITE).

Maps to BenchmarkOperation.java in jdm-java.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from .benchmark import BlockSequence, IOMode

if TYPE_CHECKING:
    from .benchmark import Benchmark
    from .sample import Sample


class BenchmarkOperation:
    """Aggregated results of a single directed I/O phase within a Benchmark.

    Created by BenchmarkRunner at the start of each operation; populated
    incrementally as samples complete; finalised once all threads finish.
    """

    def __init__(self) -> None:
        # ------------------------------------------------------------------
        # Parameters (copied from BenchmarkConfig by BenchmarkRunner)
        # ------------------------------------------------------------------
        self.io_mode: Optional[IOMode] = None
        self.block_order: BlockSequence = BlockSequence.SEQUENTIAL
        self.num_blocks: int = 0
        self.block_size: int = 0       # bytes
        self.num_samples: int = 0
        self.tx_size: int = 0          # KB
        self.num_threads: int = 1
        # None for READ operations (write-sync is only meaningful for WRITE)
        self.write_sync_enabled: Optional[bool] = None

        # ------------------------------------------------------------------
        # Timestamps
        # ------------------------------------------------------------------
        self.start_time: datetime = datetime.now()
        self.end_time: Optional[datetime] = None

        # ------------------------------------------------------------------
        # Sample list — append is protected by _lock for thread safety
        # ------------------------------------------------------------------
        self._lock = threading.Lock()
        self.samples: list["Sample"] = []

        # GC retry tracking (always empty in Python; kept for serialisation compat)
        self.gc_retried_samples: list[int] = []

        # ------------------------------------------------------------------
        # Aggregate results — updated after every sample
        # ------------------------------------------------------------------
        self.bw_avg: float = 0.0
        self.bw_max: float = 0.0
        self.bw_min: float = 0.0
        self.acc_avg: float = 0.0   # average latency in ms
        self.iops: int = 0

        # Back-reference (not serialised)
        self.benchmark: Optional["Benchmark"] = None

    # ------------------------------------------------------------------
    # Thread-safe sample accumulation
    # ------------------------------------------------------------------

    def add(self, sample: "Sample") -> None:
        """Append *sample* to the sample list (thread-safe)."""
        with self._lock:
            self.samples.append(sample)

    def get_samples(self) -> list["Sample"]:
        """Return a snapshot of the sample list (thread-safe)."""
        with self._lock:
            return list(self.samples)

    # ------------------------------------------------------------------
    # IOPS calculation — called once per operation after all threads finish
    # ------------------------------------------------------------------

    def set_total_ops(self, total_ops: int) -> None:
        """Compute IOPS from *total_ops* and the operation's elapsed time.

        Mirrors BenchmarkOperation.setTotalOps() in Java.
        iops = total_ops / elapsed_seconds
        """
        if self.end_time is None:
            return
        elapsed_sec = (self.end_time - self.start_time).total_seconds()
        if elapsed_sec > 0:
            self.iops = round(total_ops / elapsed_sec)

    # ------------------------------------------------------------------
    # Duration helper
    # ------------------------------------------------------------------

    def get_duration_ms(self) -> Optional[int]:
        if self.end_time is None:
            return None
        return int((self.end_time - self.start_time).total_seconds() * 1_000)

    # ------------------------------------------------------------------
    # Display helpers (mirror BenchmarkOperation.java display methods)
    # ------------------------------------------------------------------

    def get_mode_display(self) -> str:
        """'Write*' when write-sync was enabled; otherwise 'Read' / 'Write'."""
        if self.io_mode == IOMode.WRITE and self.write_sync_enabled is True:
            return "Write*"
        return self.io_mode.value if self.io_mode else ""

    def get_blocks_display(self) -> str:
        return f"{self.num_blocks} ({self.block_size})"

    def get_bw_avg_display(self) -> str:
        return "- -" if self.bw_avg == -1 else f"{self.bw_avg:.2f}"

    def get_bw_min_display(self) -> str:
        return "- -" if self.bw_min == -1 else f"{self.bw_min:.2f}"

    def get_bw_max_display(self) -> str:
        return "- -" if self.bw_max == -1 else f"{self.bw_max:.2f}"

    def get_bw_min_max_display(self) -> str:
        if self.bw_max == -1:
            return "- -"
        return f"{self.bw_min:.0f}/{self.bw_max:.0f}"

    def get_acc_time_display(self) -> str:
        return "- -" if self.acc_avg == -1 else f"{self.acc_avg:.2f}"

    def __repr__(self) -> str:
        return (
            f"BenchmarkOperation(mode={self.io_mode}, "
            f"order={self.block_order.value}, "
            f"samples={len(self.samples)}, bw_avg={self.bw_avg:.2f})"
        )
