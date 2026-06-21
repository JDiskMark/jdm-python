"""Sample — one timed I/O measurement unit.

Maps to Sample.java in jdm-java.

A sample writes or reads *num_blocks* blocks of *block_size* bytes,
records the elapsed time, and derives:
  - bw_mb_sec      : bandwidth for this sample
  - access_time_ms : average latency per block
  - cum_*          : running cumulative stats (updated by app.update_metrics)

I/O is performed via os.pwrite/pread (POSIX) or CreateFileW/WriteFile
(Windows), with optional Direct I/O controlled by BenchmarkConfig.
"""
from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .benchmark import MEGABYTE, BlockSequence, IOMode

if TYPE_CHECKING:
    from .benchmark_runner import BenchmarkRunner

logger = logging.getLogger(__name__)


class Sample:
    """A single timed I/O measurement.

    sampleNum is 1-based and set externally by BenchmarkRunner.
    The cum_* fields are populated by app.update_metrics() after each sample.
    """

    __slots__ = (
        "type_",
        "sample_num",
        "bw_mb_sec",
        "cum_avg",
        "cum_max",
        "cum_min",
        "access_time_ms",
        "cum_acc_time_ms",
    )

    def __init__(self, type_: IOMode, sample_num: int) -> None:
        self.type_: IOMode = type_
        self.sample_num: int = sample_num   # x-axis (1-based)
        self.bw_mb_sec: float = 0.0         # y-axis: bandwidth for this sample
        self.cum_avg: float = 0.0           # running cumulative average BW
        self.cum_max: float = 0.0           # running cumulative max BW
        self.cum_min: float = 0.0           # running cumulative min BW
        self.access_time_ms: float = 0.0    # latency: elapsed_ns/1e6 / num_blocks
        self.cum_acc_time_ms: float = 0.0   # running cumulative avg latency

    # ------------------------------------------------------------------
    # Test file path
    # ------------------------------------------------------------------

    def get_test_file(self, runner: "BenchmarkRunner") -> Path:
        """Return the Path of the test file for this sample.

        multi_file_enabled=True  → one file per sample: testdata<N>.pdm
        multi_file_enabled=False → single shared file:  testdata.pdm
        """
        base = Path(runner.config.test_dir)
        if runner.config.multi_file_enabled:
            return base / f"testdata{self.sample_num}.pdm"
        return base / "testdata.pdm"

    # ------------------------------------------------------------------
    # I/O methods
    # ------------------------------------------------------------------

    def measure_write(
        self,
        block_size: int,
        num_blocks: int,
        runner: "BenchmarkRunner",
    ) -> None:
        """Write *num_blocks* x *block_size* bytes using positional I/O.

        Uses aligned buffers via io_engine for optional Direct I/O
        (FILE_FLAG_NO_BUFFERING / O_DIRECT). Mirrors Sample.measureWrite()
        in jdm-java.
        """
        from . import io_engine

        test_file = self.get_test_file(runner)
        test_file.parent.mkdir(parents=True, exist_ok=True)

        alignment = runner.config.sector_alignment.bytes
        if alignment <= 0:
            alignment = 4096  # default to 4 KB

        buf = io_engine.alloc_aligned(block_size, alignment)

        start_ns = time.perf_counter_ns()
        total_bytes_written = 0

        try:
            fd = io_engine.open_file(
                test_file,
                write=True,
                create=True,
                direct=runner.config.direct_io_enabled,
                sync=runner.config.write_sync_enabled,
            )
            try:
                for b in range(num_blocks):
                    if runner.listener.is_cancelled():
                        break
                    if runner.config.block_order == BlockSequence.RANDOM:
                        block_index = random.randint(0, num_blocks - 1)
                    else:
                        block_index = b
                    byte_offset = block_index * block_size
                    written = io_engine.pwrite(fd, buf, byte_offset, block_size)
                    total_bytes_written += written
                    runner.update_write_progress()
            finally:
                io_engine.close_file(fd)
        except OSError as exc:
            logger.error("measure_write failed: %s", exc)
            return
        finally:
            io_engine.free_aligned(buf)

        elapsed_ns = time.perf_counter_ns() - start_ns
        self.access_time_ms = (elapsed_ns / 1_000_000) / num_blocks
        elapsed_sec = elapsed_ns / 1_000_000_000
        self.bw_mb_sec = (total_bytes_written / MEGABYTE) / elapsed_sec

    def measure_read(
        self,
        block_size: int,
        num_blocks: int,
        runner: "BenchmarkRunner",
    ) -> None:
        """Read *num_blocks* × *block_size* bytes using positional I/O.

        Uses aligned buffers via io_engine for optional Direct I/O.
        Mirrors Sample.measureRead() in jdm-java.
        """
        from . import io_engine

        test_file = self.get_test_file(runner)

        alignment = runner.config.sector_alignment.bytes
        if alignment <= 0:
            alignment = 4096

        buf = io_engine.alloc_aligned(block_size, alignment)

        start_ns = time.perf_counter_ns()
        total_bytes_read = 0

        try:
            fd = io_engine.open_file(
                test_file,
                write=False,
                create=False,
                direct=runner.config.direct_io_enabled,
            )
            try:
                for b in range(num_blocks):
                    if runner.listener.is_cancelled():
                        break
                    if runner.config.block_order == BlockSequence.RANDOM:
                        block_index = random.randint(0, num_blocks - 1)
                    else:
                        block_index = b
                    byte_offset = block_index * block_size
                    read_count = io_engine.pread(fd, buf, byte_offset, block_size)
                    total_bytes_read += read_count
                    runner.update_read_progress()
            finally:
                io_engine.close_file(fd)
        except OSError as exc:
            logger.error("measure_read failed: %s", exc)
            return
        finally:
            io_engine.free_aligned(buf)

        elapsed_ns = time.perf_counter_ns() - start_ns
        self.access_time_ms = (elapsed_ns / 1_000_000) / num_blocks
        elapsed_sec = elapsed_ns / 1_000_000_000
        self.bw_mb_sec = (total_bytes_read / MEGABYTE) / elapsed_sec

    def prepare_read(
        self,
        block_size: int,
        num_blocks: int,
        runner: "BenchmarkRunner",
    ) -> None:
        """Write test data before a READ-only benchmark (not timed).

        Uses aligned buffers via io_engine. Uses the write-progress counter
        so the progress bar is meaningful during the prep phase.
        Mirrors Sample.prepareRead() in jdm-java.
        Raises RuntimeError on failure.
        """
        from . import io_engine

        test_file = self.get_test_file(runner)
        test_file.parent.mkdir(parents=True, exist_ok=True)

        alignment = runner.config.sector_alignment.bytes
        if alignment <= 0:
            alignment = 4096

        buf = io_engine.alloc_aligned(block_size, alignment)

        try:
            # Open without Direct I/O for prep — just standard buffered write
            fd = io_engine.open_file(
                test_file,
                write=True,
                create=True,
                direct=False,
                sync=False,
            )
            try:
                total_bytes = 0
                for b in range(num_blocks):
                    if runner.listener.is_cancelled():
                        break
                    byte_offset = b * block_size
                    written = io_engine.pwrite(fd, buf, byte_offset, block_size)
                    total_bytes += written
                    runner.update_write_progress()
                logger.debug(
                    "prepare_read: wrote %d bytes to %s",
                    total_bytes, test_file.name,
                )
            finally:
                io_engine.close_file(fd)
        except OSError as exc:
            raise RuntimeError(
                f"Read preparation failed for {test_file.name}"
            ) from exc
        finally:
            io_engine.free_aligned(buf)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def get_bw_mb_sec_display(self) -> str:
        return f"{self.bw_mb_sec:.3f}"

    def get_avg_display(self) -> str:
        return f"{self.cum_avg:.3f}"

    def get_max_display(self) -> str:
        return f"{self.cum_max:.3f}"

    def get_min_display(self) -> str:
        return f"{self.cum_min:.3f}"

    def __repr__(self) -> str:
        return (
            f"Sample({self.type_.value!r}, sn={self.sample_num}, "
            f"bw={self.bw_mb_sec:.3f} MB/s, lat={self.access_time_ms:.3f} ms)"
        )
