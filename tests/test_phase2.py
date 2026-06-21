"""Phase 2 test suite — MODERN engine + OS-specific utilities.

Covers:
  - get_processor_name: returns a non-empty string
  - get_disk_usage: returns non-zero totals for current drive
  - get_partition_id: returns a drive letter on Windows
  - get_drive_model: returns a non-empty string (may be 'Unknown Drive' in CI)
  - is_admin: returns a bool
  - alloc_aligned: buffer address is properly aligned
  - MODERN engine WRITE smoke: 5 samples, 4 blocks × 4 KB
  - MODERN engine READ_WRITE smoke
  - MODERN engine READ-only smoke (includes prep phase)
  - MODERN engine with Direct I/O (FILE_FLAG_NO_BUFFERING)
  - MODERN engine multi-thread smoke
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_app_state(tmp_path: Path) -> None:
    """Fully reset app module state between tests."""
    import pydiskmark.app as app

    app.os_name = ""
    app.arch = ""
    app.processor_name = ""
    app.runtime = ""
    app.username = "test_user"
    app.is_admin = False
    app.is_root = False
    app.location_dir = str(tmp_path)
    app.data_dir = str(tmp_path / "pdm-data")
    app.export_path = None
    app.auto_save = False
    app.auto_remove_data = False
    app.auto_reset = True
    app.verbose = False
    app.next_sample_number = 1
    app.w_max = app.w_min = app.w_avg = app.w_acc = -1.0
    app.w_iops = -1
    app.r_max = app.r_min = app.r_avg = app.r_acc = -1.0
    app.r_iops = -1


def _make_config(
    tmp_path: Path,
    benchmark_type=None,
    io_engine=None,
    direct_io=False,
    write_sync=False,
    num_samples=5,
    num_blocks=4,
    block_size_kb=4,
    num_threads=1,
):
    """Build a small BenchmarkConfig for smoke tests."""
    from pydiskmark.benchmark import (
        BenchmarkConfig, BenchmarkType, BlockSequence, IoEngine,
        SectorAlignment, KILOBYTE,
    )

    if benchmark_type is None:
        benchmark_type = BenchmarkType.WRITE
    if io_engine is None:
        io_engine = IoEngine.MODERN

    cfg = BenchmarkConfig()
    cfg.app_version = "0.1.0-test"
    cfg.benchmark_type = benchmark_type
    cfg.block_order = BlockSequence.SEQUENTIAL
    cfg.num_blocks = num_blocks
    cfg.block_size = block_size_kb * KILOBYTE
    cfg.num_samples = num_samples
    cfg.num_threads = num_threads
    cfg.tx_size = block_size_kb * num_blocks * num_samples
    cfg.io_engine = io_engine
    cfg.direct_io_enabled = direct_io
    cfg.write_sync_enabled = write_sync
    cfg.sector_alignment = SectorAlignment.ALIGN_4K
    cfg.multi_file_enabled = False
    cfg.test_dir = str(tmp_path / "pdm-data")
    os.makedirs(cfg.test_dir, exist_ok=True)
    return cfg


class _DummyListener:
    """Minimal listener for tests."""

    def __init__(self):
        self.samples = []
        self.progress = []

    def on_sample_complete(self, sample):
        self.samples.append(sample)

    def on_progress_update(self, completed, total):
        self.progress.append((completed, total))

    def is_cancelled(self):
        return False

    def attempt_cache_drop(self):
        pass


# ===========================================================================
# OS-Specific Utility Tests
# ===========================================================================


class TestProcessorName:
    def test_returns_non_empty_string(self):
        from pydiskmark.util_os import get_processor_name
        name = get_processor_name()
        assert isinstance(name, str)
        assert len(name) > 0
        assert name != "Unknown CPU"

    def test_via_util_delegate(self):
        from pydiskmark.util import get_processor_name
        name = get_processor_name()
        assert isinstance(name, str)
        assert len(name) > 0


class TestDiskUsage:
    def test_returns_non_zero_totals(self, tmp_path):
        from pydiskmark.util_os import get_disk_usage
        info = get_disk_usage(tmp_path)
        assert info.total_gb > 0
        assert info.used_gb >= 0
        assert info.free_gb >= 0
        assert 0 <= info.percent_used <= 100

    def test_via_util_delegate(self, tmp_path):
        from pydiskmark.util import get_disk_usage
        info = get_disk_usage(tmp_path)
        assert info.total_gb > 0


class TestPartitionId:
    def test_returns_string(self, tmp_path):
        from pydiskmark.util_os import get_partition_id
        pid = get_partition_id(tmp_path)
        assert isinstance(pid, str)
        assert len(pid) > 0

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows only")
    def test_windows_drive_letter(self, tmp_path):
        from pydiskmark.util_os import get_partition_id
        pid = get_partition_id(tmp_path)
        # Should be a single uppercase letter
        assert len(pid) == 1
        assert pid.isalpha()
        assert pid.isupper()


class TestDriveModel:
    def test_returns_string(self, tmp_path):
        from pydiskmark.util_os import get_drive_model
        model = get_drive_model(tmp_path)
        assert isinstance(model, str)
        assert len(model) > 0


class TestIsAdmin:
    def test_returns_bool(self):
        from pydiskmark.util_os import is_admin
        result = is_admin()
        assert isinstance(result, bool)


# ===========================================================================
# Aligned Buffer Tests
# ===========================================================================


class TestAllocAligned:
    def test_buffer_is_4k_aligned(self):
        from pydiskmark.io_engine import alloc_aligned, free_aligned, get_buffer_address
        buf = alloc_aligned(4096, 4096)
        try:
            addr = get_buffer_address(buf)
            assert addr % 4096 == 0, f"Buffer address {addr:#x} is not 4K-aligned"
        finally:
            free_aligned(buf)

    def test_buffer_size(self):
        from pydiskmark.io_engine import alloc_aligned, free_aligned
        buf = alloc_aligned(8192, 4096)
        try:
            assert len(buf) == 8192
        finally:
            free_aligned(buf)

    def test_buffer_is_writable(self):
        from pydiskmark.io_engine import alloc_aligned, free_aligned
        buf = alloc_aligned(4096, 4096)
        try:
            buf[0] = b'\xff'
            buf[4095] = b'\x00'
        finally:
            free_aligned(buf)


# ===========================================================================
# MODERN Engine Smoke Tests
# ===========================================================================


class TestModernEngineSmoke:
    """Smoke tests for the MODERN I/O engine.

    These run real I/O on tmpdir — small enough (5 × 4 × 4 KB = 80 KB)
    to complete in under a second.
    """

    def test_modern_write(self, tmp_path):
        """WRITE with MODERN engine produces non-zero bandwidth."""
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(tmp_path, benchmark_type=BenchmarkType.WRITE)
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 1
        op = benchmark.operations[0]
        assert len(op.samples) == 5
        for s in op.samples:
            assert s.bw_mb_sec > 0
            assert s.access_time_ms > 0

    def test_modern_read_write(self, tmp_path):
        """READ_WRITE with MODERN engine produces two operations."""
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(tmp_path, benchmark_type=BenchmarkType.READ_WRITE)
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 2
        write_op = benchmark.operations[0]
        read_op = benchmark.operations[1]
        assert len(write_op.samples) == 5
        assert len(read_op.samples) == 5
        for s in write_op.samples:
            assert s.bw_mb_sec > 0
        for s in read_op.samples:
            assert s.bw_mb_sec > 0

    def test_modern_read_only(self, tmp_path):
        """READ-only benchmark uses prepare_read_modern then measures reads."""
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(tmp_path, benchmark_type=BenchmarkType.READ)
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 1
        op = benchmark.operations[0]
        assert len(op.samples) == 5
        for s in op.samples:
            assert s.bw_mb_sec > 0

    def test_modern_multi_thread(self, tmp_path):
        """MODERN engine with 2 threads produces correct sample count."""
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(
            tmp_path,
            benchmark_type=BenchmarkType.WRITE,
            num_threads=2,
            num_samples=6,  # 6 samples / 2 threads = 3 each
        )
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 1
        op = benchmark.operations[0]
        assert len(op.samples) == 6
        for s in op.samples:
            assert s.bw_mb_sec > 0

    def test_modern_direct_io_write(self, tmp_path):
        """MODERN engine with Direct I/O (no admin required for FILE_FLAG_NO_BUFFERING)."""
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(
            tmp_path,
            benchmark_type=BenchmarkType.WRITE,
            direct_io=True,
        )
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        # Should succeed (either with Direct I/O or fallback to buffered)
        assert len(benchmark.operations) == 1
        op = benchmark.operations[0]
        assert len(op.samples) == 5
        for s in op.samples:
            assert s.bw_mb_sec > 0

    def test_modern_direct_io_read_write(self, tmp_path):
        """MODERN engine with Direct I/O in READ_WRITE mode."""
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(
            tmp_path,
            benchmark_type=BenchmarkType.READ_WRITE,
            direct_io=True,
        )
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 2
        for op in benchmark.operations:
            assert len(op.samples) == 5
            for s in op.samples:
                assert s.bw_mb_sec > 0


class TestModernEngineCumulativeStats:
    """Verify cumulative stats are populated on MODERN engine samples."""

    def test_cumulative_avg_populated(self, tmp_path):
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(tmp_path, benchmark_type=BenchmarkType.WRITE, num_samples=3)
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        op = benchmark.operations[0]
        for s in op.samples:
            assert s.cum_avg > 0
            assert s.cum_max > 0
            assert s.cum_min > 0
            assert s.cum_acc_time_ms > 0


class TestModernEngineEnvironmentInfo:
    """Verify that drive model and disk usage are populated."""

    def test_drive_info_populated(self, tmp_path):
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(tmp_path, benchmark_type=BenchmarkType.WRITE, num_samples=2)
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        # Drive info should be populated with real data
        assert benchmark.drive_info.total_gb > 0
        assert benchmark.drive_info.used_gb >= 0
        assert isinstance(benchmark.drive_info.drive_model, str)
        assert len(benchmark.drive_info.drive_model) > 0
        assert isinstance(benchmark.drive_info.partition_id, str)
        assert len(benchmark.drive_info.partition_id) > 0

    def test_system_info_populated(self, tmp_path):
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        import pydiskmark.app as app
        app.init()

        cfg = _make_config(tmp_path, benchmark_type=BenchmarkType.WRITE, num_samples=2)
        listener = _DummyListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.system_info.processor_name) > 0
        assert len(benchmark.system_info.os) > 0
        assert benchmark.system_info.runtime.startswith("Python")
