"""Phase 3 test suite — CLI, exporter, argparse.

Covers:
  - Argument parser: profile loading, all flag types, override precedence
  - Exporter: JSON/YAML/CSV round-trip via in-memory objects
  - CLI integration smoke: run with small config, exit 0, results printed
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

def _make_benchmark(tmp_path: Path):
    """Build a minimal completed Benchmark object for exporter tests."""
    from pydiskmark.benchmark import (
        Benchmark, BenchmarkConfig, BenchmarkDriveInfo, BenchmarkSystemInfo,
        BenchmarkType, BlockSequence, IOMode, IoEngine, SectorAlignment, KILOBYTE,
    )
    from pydiskmark.benchmark_operation import BenchmarkOperation
    from pydiskmark.benchmark_profile import BenchmarkProfile
    from pydiskmark.sample import Sample

    cfg = BenchmarkConfig()
    cfg.app_version = "0.1.0-test"
    cfg.profile = BenchmarkProfile.QUICK_TEST
    cfg.benchmark_type = BenchmarkType.WRITE
    cfg.block_order = BlockSequence.SEQUENTIAL
    cfg.num_blocks = 4
    cfg.block_size = 4 * KILOBYTE
    cfg.num_samples = 3
    cfg.num_threads = 1
    cfg.io_engine = IoEngine.MODERN
    cfg.direct_io_enabled = False
    cfg.write_sync_enabled = False
    cfg.multi_file_enabled = False
    cfg.test_dir = str(tmp_path)

    b = Benchmark(config=cfg)
    b.username = "test_user"
    b.system_info = BenchmarkSystemInfo(
        os="Windows", arch="AMD64",
        processor_name="Intel i9", runtime="Python 3.11.9",
        location_dir=str(tmp_path),
    )
    b.drive_info = BenchmarkDriveInfo(
        drive_model="Samsung 990 Pro", partition_id="C",
        percent_used=55, used_gb=250.0, total_gb=512.0,
    )
    b.start_time = datetime(2026, 6, 21, 12, 0, 0)
    b.end_time = datetime(2026, 6, 21, 12, 0, 10)

    op = BenchmarkOperation()
    op.io_mode = IOMode.WRITE
    op.block_order = BlockSequence.SEQUENTIAL
    op.num_blocks = 4
    op.block_size = 4 * KILOBYTE
    op.num_samples = 3
    op.num_threads = 1
    op.bw_avg = 520.0
    op.bw_max = 530.0
    op.bw_min = 510.0
    op.acc_avg = 0.95
    op.iops = 12345
    op.start_time = b.start_time
    op.end_time = b.end_time

    for i in range(1, 4):
        s = Sample(type_=IOMode.WRITE, sample_num=i)
        s.bw_mb_sec = 520.0 + i
        s.cum_avg = 520.5
        s.cum_max = 523.0
        s.cum_min = 521.0
        s.access_time_ms = 0.95
        s.cum_acc_time_ms = 0.95
        op.samples.append(s)

    b.operations.append(op)
    return b


# ===========================================================================
# Argparse tests
# ===========================================================================

class TestArgparse:

    def _parse(self, args: list[str]):
        """Parse a run sub-command argument list."""
        from pydiskmark.cli import _build_parser
        return _build_parser().parse_args(["run"] + args)

    def test_default_profile(self):
        ns = self._parse([])
        assert ns.profile == "QUICK_TEST"

    def test_profile_short(self):
        ns = self._parse(["-p", "MAX_THROUGHPUT"])
        assert ns.profile == "MAX_THROUGHPUT"

    def test_profile_long(self):
        ns = self._parse(["--profile", "SEQ_WRITE_STRESS"])
        assert ns.profile == "SEQ_WRITE_STRESS"

    def test_type_override(self):
        ns = self._parse(["-t", "WRITE"])
        assert ns.benchmark_type == "WRITE"

    def test_threads(self):
        ns = self._parse(["-T", "4"])
        assert ns.threads == 4

    def test_blocks(self):
        ns = self._parse(["-b", "16"])
        assert ns.blocks == 16

    def test_block_size(self):
        ns = self._parse(["-z", "512"])
        assert ns.block_size_kb == 512

    def test_samples(self):
        ns = self._parse(["-n", "10"])
        assert ns.samples == 10

    def test_direct_flag(self):
        ns = self._parse(["-d"])
        assert ns.direct is True

    def test_write_sync_flag(self):
        ns = self._parse(["-y"])
        assert ns.write_sync is True

    def test_multi_file_flag(self):
        ns = self._parse(["-m"])
        assert ns.multi_file is True

    def test_clean_flag(self):
        ns = self._parse(["-c"])
        assert ns.clean is True

    def test_verbose_flag(self):
        ns = self._parse(["-v"])
        assert ns.verbose is True

    def test_export_path(self):
        ns = self._parse(["-e", "/tmp/results.json"])
        assert ns.export == "/tmp/results.json"

    def test_location(self):
        ns = self._parse(["-l", "C:\\Temp"])
        assert ns.location == "C:\\Temp"

    def test_order_random(self):
        ns = self._parse(["-o", "RANDOM"])
        assert ns.block_order == "RANDOM"

    def test_gc_retry_noop(self):
        ns = self._parse(["-g"])
        assert ns.gc_retry is True   # parsed but ignored at runtime

    def test_run_subcommand_required(self):
        from pydiskmark.cli import _build_parser
        with pytest.raises(SystemExit):
            _build_parser().parse_args([])


# ===========================================================================
# Exporter — JSON
# ===========================================================================

class TestExporterJson:

    def test_json_is_valid(self, tmp_path):
        from pydiskmark.exporter import to_json
        b = _make_benchmark(tmp_path)
        text = to_json(b)
        data = json.loads(text)  # must not raise
        assert isinstance(data, dict)

    def test_json_has_id(self, tmp_path):
        from pydiskmark.exporter import to_json
        b = _make_benchmark(tmp_path)
        data = json.loads(to_json(b))
        assert "_id" in data
        assert data["_id"] == b.id

    def test_json_operations(self, tmp_path):
        from pydiskmark.exporter import to_json
        b = _make_benchmark(tmp_path)
        data = json.loads(to_json(b))
        assert len(data["operations"]) == 1
        op = data["operations"][0]
        assert op["ioMode"] == "Write"
        assert len(op["samples"]) == 3

    def test_json_system_info(self, tmp_path):
        from pydiskmark.exporter import to_json
        b = _make_benchmark(tmp_path)
        data = json.loads(to_json(b))
        assert data["systemInfo"]["processorName"] == "Intel i9"
        assert data["systemInfo"]["os"] == "Windows"

    def test_json_drive_info(self, tmp_path):
        from pydiskmark.exporter import to_json
        b = _make_benchmark(tmp_path)
        data = json.loads(to_json(b))
        assert data["driveInfo"]["driveModel"] == "Samsung 990 Pro"
        assert data["driveInfo"]["totalGb"] == 512.0

    def test_json_sample_fields(self, tmp_path):
        from pydiskmark.exporter import to_json
        b = _make_benchmark(tmp_path)
        data = json.loads(to_json(b))
        s = data["operations"][0]["samples"][0]
        assert "sn" in s
        assert "bw" in s
        assert "bt" in s
        assert "la" in s
        assert "lt" in s
        assert "mn" in s
        assert "mx" in s

    def test_export_writes_json_file(self, tmp_path):
        from pydiskmark.exporter import export
        b = _make_benchmark(tmp_path)
        out = tmp_path / "results.json"
        export(b, out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert "_id" in data


# ===========================================================================
# Exporter — YAML
# ===========================================================================

class TestExporterYaml:

    def test_yaml_is_valid(self, tmp_path):
        import yaml
        from pydiskmark.exporter import to_yaml
        b = _make_benchmark(tmp_path)
        text = to_yaml(b)
        data = yaml.safe_load(text)
        assert isinstance(data, dict)

    def test_yaml_has_operations(self, tmp_path):
        import yaml
        from pydiskmark.exporter import to_yaml
        b = _make_benchmark(tmp_path)
        data = yaml.safe_load(to_yaml(b))
        assert len(data["operations"]) == 1

    def test_export_writes_yml_file(self, tmp_path):
        from pydiskmark.exporter import export
        b = _make_benchmark(tmp_path)
        out = tmp_path / "results.yml"
        export(b, out)
        assert out.exists()

    def test_export_detects_yaml_extension(self, tmp_path):
        from pydiskmark.exporter import export
        b = _make_benchmark(tmp_path)
        out = tmp_path / "results.yaml"
        export(b, out)
        assert out.exists()


# ===========================================================================
# Exporter — CSV
# ===========================================================================

class TestExporterCsv:

    def test_csv_has_header_comments(self, tmp_path):
        from pydiskmark.exporter import to_csv
        b = _make_benchmark(tmp_path)
        text = to_csv(b)
        assert "# pydiskmark" in text
        assert "# Date:" in text
        assert "# Model:" in text

    def test_csv_has_column_row(self, tmp_path):
        from pydiskmark.exporter import to_csv
        b = _make_benchmark(tmp_path)
        text = to_csv(b)
        assert "sn,ioMode,bw,bt,la,lt,mn,mx" in text

    def test_csv_sample_count(self, tmp_path):
        import csv
        from pydiskmark.exporter import to_csv
        b = _make_benchmark(tmp_path)
        text = to_csv(b)
        # Filter out comment lines
        data_lines = [l for l in text.splitlines() if l and not l.startswith("#")]
        # First data line is the header
        reader = list(csv.reader(data_lines))
        assert reader[0] == ["sn", "ioMode", "bw", "bt", "la", "lt", "mn", "mx"]
        assert len(reader) == 1 + 3  # header + 3 samples

    def test_export_writes_csv_file(self, tmp_path):
        from pydiskmark.exporter import export
        b = _make_benchmark(tmp_path)
        out = tmp_path / "results.csv"
        export(b, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_csv_result_line_in_comments(self, tmp_path):
        from pydiskmark.exporter import to_csv
        b = _make_benchmark(tmp_path)
        text = to_csv(b)
        assert "WRITE Result:" in text or "Write Result:" in text


# ===========================================================================
# Exporter — unsupported format
# ===========================================================================

class TestExporterErrors:

    def test_unknown_extension_raises(self, tmp_path):
        from pydiskmark.exporter import export
        b = _make_benchmark(tmp_path)
        with pytest.raises(ValueError, match="Unsupported export format"):
            export(b, tmp_path / "results.txt")


# ===========================================================================
# CLI integration smoke
# ===========================================================================

class TestCliSmoke:
    """Run a very small benchmark end-to-end through cli.main()."""

    def _run(self, args: list[str], tmp_path: Path):
        """Call cli.main() capturing stdout; returns the printed text."""
        import pydiskmark.app as app

        # Reset global state
        app.reset_test_data()
        app.reset_sequence()
        app.location_dir = str(tmp_path)
        app.data_dir = str(tmp_path / "pdm-data")

        from pydiskmark.cli import main
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            with pytest.raises(SystemExit) as exc_info:
                main(args)
        finally:
            sys.stdout = old_stdout

        exit_code = exc_info.value.code
        output = captured.getvalue()
        return exit_code, output

    def test_run_write_exits_0(self, tmp_path):
        code, out = self._run(
            ["run", "-p", "QUICK_TEST",
             "-t", "WRITE", "-n", "3", "-b", "2", "-z", "4",
             "-l", str(tmp_path)],
            tmp_path,
        )
        assert code == 0

    def test_run_outputs_mbs(self, tmp_path):
        code, out = self._run(
            ["run", "-p", "QUICK_TEST",
             "-t", "WRITE", "-n", "3", "-b", "2", "-z", "4",
             "-l", str(tmp_path)],
            tmp_path,
        )
        assert "MB/s" in out, f"Expected 'MB/s' in output:\n{out}"

    def test_run_shows_profile(self, tmp_path):
        code, out = self._run(
            ["run", "-p", "QUICK_TEST",
             "-t", "WRITE", "-n", "2", "-b", "2", "-z", "4",
             "-l", str(tmp_path)],
            tmp_path,
        )
        assert "QUICK_TEST" in out

    def test_run_exports_json(self, tmp_path):
        export_file = tmp_path / "out.json"
        code, out = self._run(
            ["run", "-p", "QUICK_TEST",
             "-t", "WRITE", "-n", "2", "-b", "2", "-z", "4",
             "-l", str(tmp_path),
             "-e", str(export_file)],
            tmp_path,
        )
        assert code == 0
        assert export_file.exists()
        data = json.loads(export_file.read_text())
        assert "_id" in data
        assert len(data["operations"]) == 1

    def test_invalid_profile_exits_nonzero(self, tmp_path):
        code, out = self._run(
            ["run", "-p", "NONEXISTENT_PROFILE",
             "-l", str(tmp_path)],
            tmp_path,
        )
        assert code != 0
