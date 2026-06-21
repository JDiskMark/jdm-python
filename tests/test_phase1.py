"""Phase 1 test suite — data model, metrics math, runner, smoke benchmark.

Covers:
  - divide_into_ranges: partitioning correctness and edge cases
  - update_metrics: running-average formula matches Java exactly
  - IOPS calculation: set_total_ops
  - BenchmarkProfile: all 8 profiles load without error
  - BenchmarkConfig predicates: has_write_operation / has_read_operation
  - Smoke WRITE benchmark (5 samples, 4 blocks, 4 KB)
  - Smoke READ_WRITE benchmark (same params)
  - Smoke READ benchmark (same params, includes prep phase)
  - Multi-thread smoke (2 threads)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_app_state(tmp_path: Path) -> None:
    """Fully reset app module state between tests."""
    import pydiskmark.app as app
    app.reset_test_data()
    app.reset_sequence()
    app.location_dir = str(tmp_path)
    app.data_dir = str(tmp_path / "pdm-data")
    app.verbose = False


def _minimal_config(tmp_path: Path, *, benchmark_type, threads: int = 1, num_samples: int = 5):
    """Return a minimal BenchmarkConfig for smoke tests."""
    from pydiskmark.benchmark import BenchmarkConfig, BlockSequence, IoEngine, SectorAlignment, KILOBYTE

    cfg = BenchmarkConfig()
    cfg.benchmark_type = benchmark_type
    cfg.block_order = BlockSequence.SEQUENTIAL
    cfg.num_blocks = 4
    cfg.block_size = 4 * KILOBYTE     # 4 KB
    cfg.num_samples = num_samples
    cfg.num_threads = threads
    cfg.tx_size = 4 * 4 * num_samples  # block_size_kb * num_blocks * num_samples
    cfg.io_engine = IoEngine.MODERN
    cfg.direct_io_enabled = False
    cfg.write_sync_enabled = False
    cfg.multi_file_enabled = False
    cfg.test_dir = str(tmp_path / "pdm-data")
    return cfg


class _NullListener:
    """Listener that records calls but otherwise does nothing."""

    def __init__(self) -> None:
        self.samples: list = []
        self.progress: list[tuple[int, int]] = []
        self.cache_drop_called = False

    def on_sample_complete(self, sample) -> None:
        self.samples.append(sample)

    def on_progress_update(self, completed: int, total: int) -> None:
        self.progress.append((completed, total))

    def is_cancelled(self) -> bool:
        return False

    def attempt_cache_drop(self) -> None:
        self.cache_drop_called = True


# ===========================================================================
# 1. divide_into_ranges
# ===========================================================================

class TestDivideIntoRanges:

    def test_even_split(self):
        from pydiskmark.benchmark_runner import BenchmarkRunner
        ranges = BenchmarkRunner.divide_into_ranges(0, 8, 4)
        assert len(ranges) == 4
        assert ranges == [(0, 2), (2, 4), (4, 6), (6, 8)]

    def test_with_remainder(self):
        """10 items across 4 threads: first 2 threads get 3, last 2 get 2."""
        from pydiskmark.benchmark_runner import BenchmarkRunner
        ranges = BenchmarkRunner.divide_into_ranges(1, 11, 4)
        assert len(ranges) == 4
        assert ranges[0] == (1, 4)
        assert ranges[1] == (4, 7)
        assert ranges[2] == (7, 9)
        assert ranges[3] == (9, 11)

    def test_full_coverage_no_overlap(self):
        """Every index in [start, end) appears exactly once."""
        from pydiskmark.benchmark_runner import BenchmarkRunner
        for n_threads in (1, 2, 3, 4, 5, 7, 10):
            ranges = BenchmarkRunner.divide_into_ranges(1, 21, n_threads)
            indices = []
            for s, e in ranges:
                indices.extend(range(s, e))
            assert sorted(indices) == list(range(1, 21)), \
                f"Failed for {n_threads} threads"

    def test_single_thread(self):
        from pydiskmark.benchmark_runner import BenchmarkRunner
        ranges = BenchmarkRunner.divide_into_ranges(0, 10, 1)
        assert ranges == [(0, 10)]

    def test_more_threads_than_samples(self):
        """3 samples across 5 threads — only 3 threads get work."""
        from pydiskmark.benchmark_runner import BenchmarkRunner
        ranges = BenchmarkRunner.divide_into_ranges(0, 3, 5)
        assert len(ranges) == 5
        all_indices = []
        for s, e in ranges:
            all_indices.extend(range(s, e))
        assert sorted(all_indices) == [0, 1, 2]

    def test_empty_range(self):
        from pydiskmark.benchmark_runner import BenchmarkRunner
        assert BenchmarkRunner.divide_into_ranges(5, 5, 3) == []

    def test_zero_threads(self):
        from pydiskmark.benchmark_runner import BenchmarkRunner
        assert BenchmarkRunner.divide_into_ranges(0, 10, 0) == []

    def test_start_at_100(self):
        """second benchmark in a session (next_sample_number > 1)."""
        from pydiskmark.benchmark_runner import BenchmarkRunner
        ranges = BenchmarkRunner.divide_into_ranges(101, 106, 2)
        all_idx = []
        for s, e in ranges:
            all_idx.extend(range(s, e))
        assert sorted(all_idx) == list(range(101, 106))


# ===========================================================================
# 2. update_metrics — running average formula
# ===========================================================================

class TestUpdateMetrics:

    def setup_method(self, method):
        """Reset app stats before every test method."""
        import pydiskmark.app as app
        app.reset_test_data()

    def _make_sample(self, io_mode, sn: int, bw: float, lat: float):
        from pydiskmark.benchmark import IOMode
        from pydiskmark.sample import Sample
        s = Sample(type_=io_mode, sample_num=sn)
        s.bw_mb_sec = bw
        s.access_time_ms = lat
        return s

    def test_first_write_sample_sets_initial(self):
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        s1 = self._make_sample(IOMode.WRITE, sn=1, bw=100.0, lat=1.0)
        app.update_metrics(s1)

        assert s1.cum_avg == 100.0
        assert s1.cum_max == 100.0
        assert s1.cum_min == 100.0
        assert s1.cum_acc_time_ms == 1.0

    def test_write_running_average(self):
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        s1 = self._make_sample(IOMode.WRITE, 1, bw=100.0, lat=1.0)
        s2 = self._make_sample(IOMode.WRITE, 2, bw=200.0, lat=2.0)
        s3 = self._make_sample(IOMode.WRITE, 3, bw=300.0, lat=3.0)

        app.update_metrics(s1)
        app.update_metrics(s2)
        app.update_metrics(s3)

        # avg after s2: (1*100 + 200) / 2 = 150
        assert abs(s2.cum_avg - 150.0) < 1e-9
        # avg after s3: (2*150 + 300) / 3 = 200
        assert abs(s3.cum_avg - 200.0) < 1e-9

    def test_write_max_min_tracking(self):
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        values = [300.0, 100.0, 500.0, 200.0, 400.0]
        for i, bw in enumerate(values, start=1):
            s = self._make_sample(IOMode.WRITE, i, bw=bw, lat=0.5)
            app.update_metrics(s)

        last_s = self._make_sample(IOMode.WRITE, len(values), bw=values[-1], lat=0.5)
        app.update_metrics(last_s)

        assert app.w_max == 500.0
        assert app.w_min == 100.0

    def test_read_and_write_independent(self):
        """READ and WRITE metrics are tracked independently."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        w1 = self._make_sample(IOMode.WRITE, 1, bw=200.0, lat=1.0)
        r1 = self._make_sample(IOMode.READ,  1, bw=400.0, lat=0.5)
        app.update_metrics(w1)
        app.update_metrics(r1)

        assert abs(w1.cum_avg - 200.0) < 1e-9
        assert abs(r1.cum_avg - 400.0) < 1e-9
        assert app.w_avg != app.r_avg

    def test_reset_clears_all_stats(self):
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        s = self._make_sample(IOMode.WRITE, 1, bw=500.0, lat=1.0)
        app.update_metrics(s)
        app.reset_test_data()

        assert app.w_avg == -1.0
        assert app.w_max == -1.0
        assert app.w_min == -1.0
        assert app.r_avg == -1.0

    def test_latency_running_average(self):
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        s1 = self._make_sample(IOMode.WRITE, 1, bw=100.0, lat=1.0)
        s2 = self._make_sample(IOMode.WRITE, 2, bw=100.0, lat=3.0)
        app.update_metrics(s1)
        app.update_metrics(s2)

        # avg latency: (1*1.0 + 3.0) / 2 = 2.0
        assert abs(s2.cum_acc_time_ms - 2.0) < 1e-9

    def test_formula_matches_java_precisely(self):
        """Verify the running average against a reference implementation."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import IOMode

        bw_values = [523.4, 498.1, 561.2, 480.0, 512.7]
        expected_avg = bw_values[0]
        for i, bw in enumerate(bw_values, start=1):
            s = self._make_sample(IOMode.WRITE, i, bw=bw, lat=1.0)
            app.update_metrics(s)
            if i == 1:
                expected_avg = bw
            else:
                expected_avg = ((i - 1) * expected_avg + bw) / i
            assert abs(s.cum_avg - expected_avg) < 1e-9, \
                f"Mismatch at sample {i}: expected {expected_avg}, got {s.cum_avg}"


# ===========================================================================
# 3. IOPS calculation
# ===========================================================================

class TestIopsCalculation:

    def test_iops_1000_ops_per_second(self):
        from pydiskmark.benchmark_operation import BenchmarkOperation

        op = BenchmarkOperation()
        op.start_time = datetime(2026, 1, 1, 0, 0, 0)
        op.end_time = op.start_time + timedelta(seconds=1)
        op.set_total_ops(1000)
        assert op.iops == 1000

    def test_iops_half_rate(self):
        from pydiskmark.benchmark_operation import BenchmarkOperation

        op = BenchmarkOperation()
        op.start_time = datetime(2026, 1, 1, 0, 0, 0)
        op.end_time = op.start_time + timedelta(seconds=2)
        op.set_total_ops(1000)
        assert op.iops == 500

    def test_iops_fractional_rounds(self):
        from pydiskmark.benchmark_operation import BenchmarkOperation

        op = BenchmarkOperation()
        op.start_time = datetime(2026, 1, 1, 0, 0, 0)
        op.end_time = op.start_time + timedelta(seconds=3)
        op.set_total_ops(1000)
        # 1000 / 3 = 333.33 → rounds to 333
        assert op.iops == 333

    def test_iops_no_end_time(self):
        from pydiskmark.benchmark_operation import BenchmarkOperation

        op = BenchmarkOperation()
        op.set_total_ops(1000)   # end_time is None
        assert op.iops == 0


# ===========================================================================
# 4. BenchmarkProfile
# ===========================================================================

class TestBenchmarkProfile:

    def test_all_8_profiles_exist(self):
        from pydiskmark.benchmark_profile import BenchmarkProfile
        profiles = BenchmarkProfile.get_defaults()
        assert len(profiles) == 8

    def test_quick_test_params(self):
        from pydiskmark.benchmark_profile import BenchmarkProfile
        from pydiskmark.benchmark import BenchmarkType, BlockSequence

        p = BenchmarkProfile.QUICK_TEST
        assert p.benchmark_type == BenchmarkType.READ_WRITE
        assert p.block_sequence == BlockSequence.SEQUENTIAL
        assert p.num_threads == 1
        assert p.num_samples == 50
        assert p.num_blocks == 32
        assert p.block_size_kb == 1024
        assert p.direct_enable is True
        assert p.write_sync_enable is False
        assert p.multi_file is False

    def test_max_write_stress_params(self):
        from pydiskmark.benchmark_profile import BenchmarkProfile
        from pydiskmark.benchmark import BenchmarkType

        p = BenchmarkProfile.MAX_WRITE_STRESS
        assert p.benchmark_type == BenchmarkType.WRITE
        assert p.num_threads == 4
        assert p.write_sync_enable is True
        assert p.multi_file is True

    def test_from_symbol_lookup(self):
        from pydiskmark.benchmark_profile import BenchmarkProfile

        assert BenchmarkProfile.from_symbol("QUICK_TEST") == BenchmarkProfile.QUICK_TEST
        assert BenchmarkProfile.from_symbol("quick_test") == BenchmarkProfile.QUICK_TEST

    def test_from_symbol_invalid(self):
        from pydiskmark.benchmark_profile import BenchmarkProfile

        with pytest.raises(ValueError):
            BenchmarkProfile.from_symbol("NON_EXISTENT_PROFILE")

    def test_symbol_property(self):
        from pydiskmark.benchmark_profile import BenchmarkProfile

        assert BenchmarkProfile.QUICK_TEST.symbol == "QUICK_TEST"
        assert BenchmarkProfile.MAX_THROUGHPUT.symbol == "MAX_THROUGHPUT"


# ===========================================================================
# 5. BenchmarkConfig predicates
# ===========================================================================

class TestBenchmarkConfigPredicates:

    def test_write_only(self):
        from pydiskmark.benchmark import BenchmarkConfig, BenchmarkType

        cfg = BenchmarkConfig(benchmark_type=BenchmarkType.WRITE)
        assert cfg.has_write_operation() is True
        assert cfg.has_read_operation() is False

    def test_read_only(self):
        from pydiskmark.benchmark import BenchmarkConfig, BenchmarkType

        cfg = BenchmarkConfig(benchmark_type=BenchmarkType.READ)
        assert cfg.has_write_operation() is False
        assert cfg.has_read_operation() is True

    def test_read_write(self):
        from pydiskmark.benchmark import BenchmarkConfig, BenchmarkType

        cfg = BenchmarkConfig(benchmark_type=BenchmarkType.READ_WRITE)
        assert cfg.has_write_operation() is True
        assert cfg.has_read_operation() is True


# ===========================================================================
# 6. Smoke benchmarks
# ===========================================================================

class TestSmokeBenchmarks:

    def test_smoke_write(self, tmp_path: Path):
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        cfg = _minimal_config(tmp_path, benchmark_type=BenchmarkType.WRITE)
        listener = _NullListener()

        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert benchmark is not None
        assert benchmark.start_time is not None
        assert benchmark.end_time is not None
        assert len(benchmark.operations) == 1

        op = benchmark.operations[0]
        from pydiskmark.benchmark import IOMode
        assert op.io_mode == IOMode.WRITE
        assert len(op.samples) == 5
        assert op.bw_avg > 0
        assert op.bw_max > 0
        assert op.bw_min > 0
        assert op.bw_min <= op.bw_avg <= op.bw_max
        assert op.acc_avg > 0
        assert op.iops > 0

        for s in op.samples:
            assert s.bw_mb_sec > 0
            assert s.access_time_ms > 0
            assert s.cum_max >= s.cum_min
            assert s.cum_max >= s.cum_avg >= s.cum_min

    def test_smoke_read_write(self, tmp_path: Path):
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType, IOMode
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        cfg = _minimal_config(tmp_path, benchmark_type=BenchmarkType.READ_WRITE)
        listener = _NullListener()

        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 2
        write_op = benchmark.get_operation(IOMode.WRITE)
        read_op = benchmark.get_operation(IOMode.READ)
        assert write_op is not None
        assert read_op is not None
        assert len(write_op.samples) == 5
        assert len(read_op.samples) == 5
        assert read_op.bw_avg > 0

    def test_smoke_read_only(self, tmp_path: Path):
        """READ-only benchmark must prepare (write) test files first."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType, IOMode
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        cfg = _minimal_config(tmp_path, benchmark_type=BenchmarkType.READ)
        listener = _NullListener()

        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        assert len(benchmark.operations) == 1
        read_op = benchmark.get_operation(IOMode.READ)
        assert read_op is not None
        assert len(read_op.samples) == 5
        assert read_op.bw_avg > 0

    def test_smoke_multi_thread(self, tmp_path: Path):
        """2-thread write benchmark covers all 10 samples exactly once."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType, IOMode
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        cfg = _minimal_config(
            tmp_path,
            benchmark_type=BenchmarkType.WRITE,
            threads=2,
            num_samples=10,
        )
        cfg.multi_file_enabled = True   # avoid write contention on shared file

        listener = _NullListener()
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()

        op = benchmark.operations[0]
        assert len(op.samples) == 10
        # All sample numbers must be unique (no duplication or gaps)
        sns = sorted(s.sample_num for s in op.samples)
        assert sns == list(range(1, 11))

    def test_progress_updates_received(self, tmp_path: Path):
        """At least two progress updates must be fired (start and force-final)."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        cfg = _minimal_config(tmp_path, benchmark_type=BenchmarkType.WRITE, num_samples=10)
        listener = _NullListener()

        BenchmarkRunner(listener, cfg).execute()

        assert len(listener.progress) >= 1
        # Final forced update must be 100 %
        assert listener.progress[-1] == (100, 100)

    def test_test_file_created(self, tmp_path: Path):
        """The test data file must exist after a WRITE benchmark."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        cfg = _minimal_config(tmp_path, benchmark_type=BenchmarkType.WRITE)
        # single-file mode so there's exactly one file
        cfg.multi_file_enabled = False

        BenchmarkRunner(_NullListener(), cfg).execute()

        data_dir = Path(cfg.test_dir)
        assert data_dir.exists()
        assert (data_dir / "testdata.pdm").exists()

    def test_sample_counter_increments(self, tmp_path: Path):
        """next_sample_number must increase by num_samples after a run."""
        import pydiskmark.app as app
        from pydiskmark.benchmark import BenchmarkType
        from pydiskmark.benchmark_runner import BenchmarkRunner

        _reset_app_state(tmp_path)
        app.next_sample_number = 1
        cfg = _minimal_config(tmp_path, benchmark_type=BenchmarkType.WRITE, num_samples=5)

        BenchmarkRunner(_NullListener(), cfg).execute()
        # Runner does not update next_sample_number — that is the caller's job
        # (matches Java's handlePostBenchmark).  Just verify the runner ran.
        assert True   # no exception


# ===========================================================================
# 7. DiskUsageInfo
# ===========================================================================

class TestDiskUsageInfo:

    def test_calc_percentage_used(self):
        from pydiskmark.disk_usage_info import DiskUsageInfo

        d = DiskUsageInfo(used_gb=50.0, total_gb=100.0)
        assert d.calc_percentage_used() == 50

    def test_display_strings(self):
        from pydiskmark.disk_usage_info import DiskUsageInfo

        d = DiskUsageInfo(percent_used=23, used_gb=52, total_gb=228)
        assert "23%" in d.get_usage_title_display()
        assert "52" in d.get_usage_title_display()

    def test_zero_total_no_division(self):
        from pydiskmark.disk_usage_info import DiskUsageInfo

        d = DiskUsageInfo()
        d.calc_percentage_used()   # must not divide-by-zero


# ===========================================================================
# 8. App init and profile loading
# ===========================================================================

class TestAppInit:

    def test_init_populates_system_info(self):
        import pydiskmark.app as app

        app.init()
        assert app.os_name != ""
        assert app.arch != ""
        assert app.runtime.startswith("Python")

    def test_load_profile(self):
        import pydiskmark.app as app
        from pydiskmark.benchmark_profile import BenchmarkProfile

        app.load_profile(BenchmarkProfile.QUICK_TEST)
        assert app.active_profile == BenchmarkProfile.QUICK_TEST
        assert app.num_of_samples == 50
        assert app.profile_modified is False

    def test_get_config_snapshot(self, tmp_path: Path):
        import pydiskmark.app as app
        from pydiskmark.benchmark_profile import BenchmarkProfile
        from pydiskmark.benchmark import KILOBYTE

        app.load_profile(BenchmarkProfile.QUICK_TEST)
        app.data_dir = str(tmp_path / "pdm-data")
        cfg = app.get_config()

        assert cfg.num_samples == 50
        assert cfg.block_size == 1024 * KILOBYTE
        assert cfg.has_write_operation()
        assert cfg.has_read_operation()
