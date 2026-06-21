"""BenchmarkRunner — orchestrates the full benchmark execution loop.

Maps to BenchmarkRunner.java in jdm-java.

Key responsibilities:
  - Thread range partitioning (divide_into_ranges)
  - Launching concurrent I/O threads via ThreadPoolExecutor
  - Throttled progress updates (UPDATE_INTERVAL_MS = 25)
  - Delegating per-sample I/O to Sample methods
  - Calling app.update_metrics() for running stats
  - Driving cache-drop via the listener between WRITE and READ phases
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from datetime import datetime
from typing import Protocol, runtime_checkable

from .benchmark import Benchmark, BenchmarkConfig, BenchmarkType, IOMode
from .benchmark_operation import BenchmarkOperation
from .sample import Sample

logger = logging.getLogger(__name__)

# Minimum milliseconds between listener.on_progress_update() calls.
UPDATE_INTERVAL_MS: int = 25


# ---------------------------------------------------------------------------
# Listener protocol — decouples runner from CLI / GUI output
# ---------------------------------------------------------------------------

@runtime_checkable
class BenchmarkListener(Protocol):
    """Observer interface for benchmark progress and lifecycle events.

    Mirrors BenchmarkRunner.BenchmarkListener in jdm-java.
    """

    def on_sample_complete(self, sample: Sample) -> None:
        """Called after each sample finishes (may be called from any thread)."""
        ...

    def on_progress_update(self, completed: int, total: int) -> None:
        """Called with throttled progress updates (0–100 completed, total=100)."""
        ...

    def is_cancelled(self) -> bool:
        """Return True to abort the benchmark early."""
        ...

    def attempt_cache_drop(self) -> None:
        """Request an OS page-cache flush before the READ phase."""
        ...


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """Executes one complete benchmark according to *config*.

    Usage::

        runner = BenchmarkRunner(listener, config)
        benchmark = runner.execute()
    """

    def __init__(self, listener: BenchmarkListener, config: BenchmarkConfig) -> None:
        self.listener = listener
        self.config = config

        # Progress counters — protected by _counter_lock
        self._counter_lock = threading.Lock()
        self._write_units: int = 0
        self._read_units: int = 0
        self._units_total: int = 0

        # Throttled progress
        self._last_update_ns: int = 0
        self._update_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Static utility: thread range partitioning
    # ------------------------------------------------------------------

    @staticmethod
    def divide_into_ranges(
        start_index: int, end_index: int, num_threads: int
    ) -> list[tuple[int, int]]:
        """Partition [start_index, end_index) evenly across *num_threads*.

        Returns a list of (start, end) exclusive ranges.
        Remainder items are distributed one-per-thread to the leading threads.

        Mirrors BenchmarkRunner.divideIntoRanges() in jdm-java.

        Example:
            divide_into_ranges(1, 11, 4)
            → [(1,4), (4,7), (7,9), (9,11)]   # 10 items, rem=2
        """
        if num_threads <= 0 or end_index <= start_index:
            return []

        n = end_index - start_index
        range_size, remainder = divmod(n, num_threads)
        ranges: list[tuple[int, int]] = []
        start = start_index

        for i in range(num_threads):
            extra = 1 if remainder > 0 else 0
            end = start + range_size + extra
            remainder = max(0, remainder - extra)
            ranges.append((start, end))
            start = end

        return ranges

    # ------------------------------------------------------------------
    # Top-level execute
    # ------------------------------------------------------------------

    def execute(self) -> Benchmark:
        """Run the full benchmark and return the populated Benchmark object.

        Mirrors BenchmarkRunner.execute() in jdm-java.
        """
        # Lazy import to avoid circular dependency (app imports BenchmarkRunner)
        import pydiskmark.app as _app

        blocks_per_phase = self.config.num_blocks * self.config.num_samples

        # For READ-only: the prep phase reuses the write counter
        w_units = blocks_per_phase if self.config.has_write_operation() else 0
        if self.config.benchmark_type == BenchmarkType.READ:
            w_units = blocks_per_phase   # prep phase counted as write units
        r_units = blocks_per_phase if self.config.has_read_operation() else 0

        with self._counter_lock:
            self._units_total = w_units + r_units
            self._write_units = 0
            self._read_units = 0

        # Collect environment info (Phase 1 stubs; real impls in Phase 2)
        drive_model = _app.get_drive_model()
        partition_id = _app.get_partition_id()
        usage_info = _app.get_disk_usage()

        # Build the top-level Benchmark container
        benchmark = Benchmark(config=self.config)
        self._map_environment(benchmark, drive_model, partition_id, usage_info)

        # Compute thread sample ranges
        start = _app.next_sample_number
        end = start + self.config.num_samples
        ranges = self.divide_into_ranges(start, end, self.config.num_threads)

        benchmark.record_start_time()

        # --- Execution phases ---
        if self.config.has_write_operation():
            self._run_operation(benchmark, IOMode.WRITE, ranges)
        elif self.config.has_read_operation():
            # READ-only: silently write test data before measuring reads
            self._run_read_preparation(ranges)

        self._throttled_progress_update(force=True)

        # Cache drop between WRITE and READ phases
        # Skip when Direct I/O is active (kernel already bypasses cache)
        # except on macOS where Direct I/O does NOT bypass the page cache.
        import platform as _platform
        is_macos = _platform.system() == "Darwin"
        if (
            not self.listener.is_cancelled()
            and self.config.has_read_operation()
            and (not self.config.direct_io_enabled or is_macos)
        ):
            self.listener.attempt_cache_drop()

        if self.config.has_read_operation() and not self.listener.is_cancelled():
            self._run_operation(benchmark, IOMode.READ, ranges)

        benchmark.record_end_time()

        return benchmark

    # ------------------------------------------------------------------
    # Single I/O operation (one phase: WRITE or READ)
    # ------------------------------------------------------------------

    def _run_operation(
        self, benchmark: Benchmark, mode: IOMode, ranges: list[tuple[int, int]]
    ) -> None:
        """Run one I/O phase, spawning one thread per range.

        Mirrors BenchmarkRunner.runOperation() in jdm-java.
        """
        import pydiskmark.app as _app

        op = self._create_op(benchmark, mode)
        engine_name = self.config.io_engine.name

        def _thread_task(range_start: int, range_end: int) -> None:
            for s in range(range_start, range_end):
                if self.listener.is_cancelled():
                    break

                sample = Sample(type_=mode, sample_num=s)

                # Dispatch to the I/O method
                try:
                    if mode == IOMode.WRITE:
                        sample.measure_write(
                            self.config.block_size,
                            self.config.num_blocks,
                            self,
                        )
                    else:
                        sample.measure_read(
                            self.config.block_size,
                            self.config.num_blocks,
                            self,
                        )
                except Exception as exc:
                    logger.error("I/O error during %s sample %d: %s", mode.value, s, exc)
                    raise RuntimeError(f"Threaded I/O failed at sample {s}") from exc

                # Update global running stats (and cum_* fields on the sample)
                _app.update_metrics(sample)

                # Update operation-level aggregate stats from the sample's cumulative fields
                with op._lock:
                    op.bw_max = sample.cum_max
                    op.bw_min = sample.cum_min
                    op.bw_avg = sample.cum_avg
                    op.acc_avg = sample.cum_acc_time_ms
                    op.samples.append(sample)

                # Increment progress counter (thread-safe)
                with self._counter_lock:
                    if mode == IOMode.WRITE:
                        self._write_units += 1
                    else:
                        self._read_units += 1

                self.listener.on_sample_complete(sample)
                self._throttled_progress_update(force=False)

        # Launch threads
        futures: list[Future] = []
        with ThreadPoolExecutor(max_workers=self.config.num_threads) as executor:
            for rng in ranges:
                futures.append(executor.submit(_thread_task, rng[0], rng[1]))
            # Propagate any thread exception to the caller
            for f in futures:
                f.result()

        op.end_time = datetime.now()

        with self._counter_lock:
            total_ops = (
                self._write_units if mode == IOMode.WRITE else self._read_units
            )
        op.set_total_ops(total_ops)

        import pydiskmark.app as _app2
        if mode == IOMode.WRITE:
            _app2.w_iops = op.iops
        else:
            _app2.r_iops = op.iops

    # ------------------------------------------------------------------
    # Read preparation (READ-only benchmarks)
    # ------------------------------------------------------------------

    def _run_read_preparation(self, ranges: list[tuple[int, int]]) -> None:
        """Write test files before a READ-only benchmark (not timed).

        Mirrors BenchmarkRunner.runReadPreparation() in jdm-java.
        Uses the write-progress counter so the progress bar is meaningful.
        """
        def _prep_task(range_start: int, range_end: int) -> None:
            for s in range(range_start, range_end):
                if self.listener.is_cancelled():
                    break
                sample = Sample(type_=IOMode.READ, sample_num=s)
                sample.prepare_read(
                    self.config.block_size, self.config.num_blocks, self,
                )

        futures: list[Future] = []
        with ThreadPoolExecutor(max_workers=self.config.num_threads) as executor:
            for rng in ranges:
                futures.append(executor.submit(_prep_task, rng[0], rng[1]))
            for f in futures:
                f.result()

    # ------------------------------------------------------------------
    # Throttled progress notification
    # ------------------------------------------------------------------

    def _throttled_progress_update(self, *, force: bool = False) -> None:
        """Emit a progress update to the listener, rate-limited to UPDATE_INTERVAL_MS.

        Thread-safe: uses a compare-and-swap on _last_update_ns.
        Mirrors BenchmarkRunner.throttledProgressUpdate() in jdm-java.
        """
        now_ns = time.perf_counter_ns()
        elapsed_ms = (now_ns - self._last_update_ns) / 1_000_000

        with self._counter_lock:
            completed = self._write_units + self._read_units
            total = self._units_total

        if force or elapsed_ms >= UPDATE_INTERVAL_MS:
            with self._update_lock:
                # Re-check inside lock to avoid duplicate rapid-fire updates
                now_ns2 = time.perf_counter_ns()
                if (now_ns2 - self._last_update_ns) / 1_000_000 >= UPDATE_INTERVAL_MS or force:
                    self._last_update_ns = now_ns2
                    pct = int(completed / total * 100) if total > 0 else 0
                    pct = max(0, min(100, pct))
                    self.listener.on_progress_update(pct, 100)

    # ------------------------------------------------------------------
    # Progress counter helpers (called by Sample)
    # ------------------------------------------------------------------

    def update_write_progress(self) -> None:
        """Increment the write-units counter and fire a throttled update."""
        with self._counter_lock:
            self._write_units += 1
        self._throttled_progress_update(force=False)

    def update_read_progress(self) -> None:
        """Increment the read-units counter and fire a throttled update."""
        with self._counter_lock:
            self._read_units += 1
        self._throttled_progress_update(force=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_op(self, benchmark: Benchmark, mode: IOMode) -> BenchmarkOperation:
        """Create and attach a BenchmarkOperation for *mode*."""
        op = BenchmarkOperation()
        op.benchmark = benchmark
        op.io_mode = mode
        op.block_order = self.config.block_order
        op.num_samples = self.config.num_samples
        op.num_blocks = self.config.num_blocks
        op.block_size = self.config.block_size
        op.tx_size = self.config.tx_size
        op.num_threads = self.config.num_threads
        if mode == IOMode.WRITE:
            op.write_sync_enabled = self.config.write_sync_enabled
        benchmark.operations.append(op)
        return op

    def _map_environment(
        self,
        benchmark: Benchmark,
        drive_model: str,
        partition_id: str,
        usage_info,
    ) -> None:
        """Populate system and drive info on the Benchmark object."""
        import pydiskmark.app as _app

        benchmark.username = _app.username
        benchmark.system_info.processor_name = _app.processor_name
        benchmark.system_info.os = _app.os_name
        benchmark.system_info.arch = _app.arch
        benchmark.system_info.runtime = _app.runtime
        benchmark.system_info.location_dir = _app.location_dir or ""
        benchmark.drive_info.drive_model = drive_model
        benchmark.drive_info.partition_id = partition_id
        benchmark.drive_info.percent_used = usage_info.percent_used
        benchmark.drive_info.used_gb = usage_info.used_gb
        benchmark.drive_info.total_gb = usage_info.total_gb
