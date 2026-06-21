"""pydiskmark CLI — entry point and argument parsing.

Usage:
    python -m pydiskmark run [OPTIONS]

All options follow SPEC §12.1.  Override precedence: CLI flag > profile default.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .benchmark import BenchmarkType, BlockSequence, SectorAlignment
from .benchmark_profile import BenchmarkProfile
from .benchmark_runner import BenchmarkListener, BenchmarkRunner
from .sample import Sample


# ---------------------------------------------------------------------------
# Progress bar renderer
# ---------------------------------------------------------------------------

_BAR_WIDTH = 50          # characters of fill
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


def _render_bar(completed: int, total: int) -> str:
    """Return a progress bar string ready to print with \\r."""
    pct = min(100, max(0, completed))
    filled = int(_BAR_WIDTH * pct / 100)
    bar = "#" * filled + " " * (_BAR_WIDTH - filled)
    return f"\rProgress: [{bar}] {pct:3d}%"


# ---------------------------------------------------------------------------
# CLI listener
# ---------------------------------------------------------------------------

class CliListener:
    """BenchmarkListener implementation for the terminal.

    Renders a live progress bar to stderr (so stdout stays clean for
    piped output) and handles cache-drop prompts.
    """

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._last_bar = ""

    # --- BenchmarkListener protocol ---

    def on_sample_complete(self, sample: Sample) -> None:
        if self.verbose:
            sys.stderr.write(
                f"\n  [{sample.type_.value}] sn={sample.sample_num} "
                f"bw={sample.bw_mb_sec:.3f} MB/s "
                f"lat={sample.access_time_ms:.3f} ms\n"
            )

    def on_progress_update(self, completed: int, total: int) -> None:
        bar = _render_bar(completed, total)
        with self._lock:
            if bar != self._last_bar:
                sys.stderr.write(bar)
                sys.stderr.flush()
                self._last_bar = bar

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def attempt_cache_drop(self) -> None:
        """Flush and drop the OS page cache between WRITE and READ phases."""
        sys.stderr.write("\n[Cache] Flushing OS page cache...\n")
        sys.stderr.flush()
        from .util_os import flush_and_drop_cache
        flush_and_drop_cache()

    # --- Terminal helpers ---

    def hide_cursor(self) -> None:
        sys.stderr.write(_HIDE_CURSOR)
        sys.stderr.flush()

    def show_cursor(self) -> None:
        sys.stderr.write(_SHOW_CURSOR)
        sys.stderr.flush()

    def finish_bar(self) -> None:
        """Print a final newline after the bar is done."""
        sys.stderr.write("\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Results printer
# ---------------------------------------------------------------------------

def _print_results(benchmark, *, file=None) -> None:
    """Print a results summary table to *file* (defaults to stdout)."""
    if file is None:
        file = sys.stdout

    cfg = benchmark.config
    si = benchmark.system_info
    di = benchmark.drive_info

    sep = "-" * 60

    print(sep, file=file)
    print(f"  pydiskmark {cfg.app_version}  --  Results", file=file)
    print(sep, file=file)
    print(f"  Profile   : {cfg.profile.symbol if cfg.profile else 'CUSTOM'}", file=file)
    print(f"  Type      : {cfg.benchmark_type.value}", file=file)
    print(f"  Engine    : {cfg.io_engine.name}", file=file)
    print(f"  Threads   : {cfg.num_threads}", file=file)
    print(f"  Blocks    : {cfg.num_blocks} x {cfg.block_size // 1024} KB", file=file)
    print(f"  Samples   : {cfg.num_samples}", file=file)
    print(f"  CPU       : {si.processor_name}", file=file)
    print(f"  OS        : {si.os}  {si.arch}", file=file)
    print(f"  Drive     : {di.drive_model}", file=file)
    print(f"  Partition : {di.partition_id}  "
          f"({di.used_gb:.1f} / {di.total_gb:.1f} GB  "
          f"{di.percent_used:.1f}% used)", file=file)
    print(sep, file=file)

    # Header
    print(f"  {'Mode':<6} {'Avg MB/s':>10} {'Max MB/s':>10} {'Min MB/s':>10} "
          f"{'Lat ms':>8} {'IOPS':>8}", file=file)
    print(f"  {'----':<6} {'---------':>10} {'---------':>10} {'---------':>10} "
          f"{'------':>8} {'----':>8}", file=file)

    for op in benchmark.operations:
        print(
            f"  {op.io_mode.value:<6} "
            f"{op.bw_avg:>10.3f} "
            f"{op.bw_max:>10.3f} "
            f"{op.bw_min:>10.3f} "
            f"{op.acc_avg:>8.3f} "
            f"{op.iops:>8}",
            file=file,
        )

    print(sep, file=file)

    elapsed = None
    if benchmark.start_time and benchmark.end_time:
        elapsed = (benchmark.end_time - benchmark.start_time).total_seconds()
        print(f"  Elapsed   : {elapsed:.1f} s", file=file)

    print(sep, file=file)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydiskmark",
        description="pydiskmark — Python disk benchmark utility",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a disk benchmark")

    run_p.add_argument("-p", "--profile", default="QUICK_TEST",
                       metavar="PROFILE",
                       help="Named benchmark profile (default: QUICK_TEST)")
    run_p.add_argument("-t", "--type", dest="benchmark_type",
                       choices=["READ", "WRITE", "READ_WRITE"],
                       help="Override benchmark type")
    run_p.add_argument("-T", "--threads", type=int,
                       help="Number of concurrent I/O threads")
    run_p.add_argument("-o", "--order", dest="block_order",
                       choices=["SEQUENTIAL", "RANDOM"],
                       help="Block access order")
    run_p.add_argument("-b", "--blocks", type=int,
                       help="Blocks per sample")
    run_p.add_argument("-z", "--block-size", type=int, dest="block_size_kb",
                       help="Block size in KB")
    run_p.add_argument("-n", "--samples", type=int,
                       help="Number of samples")
    run_p.add_argument("-d", "--direct", action="store_true", default=None,
                       help="Enable Direct I/O (FILE_FLAG_NO_BUFFERING / O_DIRECT)")
    run_p.add_argument("-y", "--write-sync", action="store_true", default=None,
                       dest="write_sync",
                       help="Enable write-sync (fsync after each block)")
    run_p.add_argument("-a", "--alignment", dest="alignment",
                       choices=["NONE", "ALIGN_512", "ALIGN_4K"],
                       help="Sector alignment")
    run_p.add_argument("-m", "--multi-file", action="store_true", default=None,
                       dest="multi_file",
                       help="One test file per sample")
    run_p.add_argument("-l", "--location", default=None,
                       metavar="DIR",
                       help="Directory for test files (default: home dir)")
    run_p.add_argument("-e", "--export", default=None,
                       metavar="FILE",
                       help="Export results to FILE (.json, .yml, .csv)")
    run_p.add_argument("-c", "--clean", action="store_true",
                       help="Delete existing data directory before run")
    run_p.add_argument("-v", "--verbose", action="store_true",
                       help="Print each sample result as it completes")
    run_p.add_argument("-g", "--gc-retry", action="store_true", dest="gc_retry",
                       help="(no-op, kept for API compatibility)")

    return parser


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _apply_args_to_app(args: argparse.Namespace) -> None:
    """Load profile then apply any explicit CLI overrides into global app state."""
    import pydiskmark.app as app

    # 1. Load profile defaults
    try:
        profile = BenchmarkProfile.from_symbol(args.profile)
    except ValueError:
        sys.exit(
            f"error: unknown profile '{args.profile}'. "
            f"Valid profiles: {', '.join(p.symbol for p in BenchmarkProfile.get_defaults())}"
        )
    app.load_profile(profile)

    # 2. Apply explicit overrides (only if the user actually specified them)
    if args.benchmark_type is not None:
        app.benchmark_type = BenchmarkType[args.benchmark_type]
    if args.threads is not None:
        app.num_of_threads = args.threads
    if args.block_order is not None:
        app.block_sequence = BlockSequence[args.block_order]
    if args.blocks is not None:
        app.num_of_blocks = args.blocks
    if args.block_size_kb is not None:
        app.block_size_kb = args.block_size_kb
    if args.samples is not None:
        app.num_of_samples = args.samples
    if args.direct:
        app.direct_enable = True
    if args.write_sync:
        app.write_sync_enable = True
    if args.alignment is not None:
        app.sector_alignment = SectorAlignment[args.alignment]
    if args.multi_file:
        app.multi_file = True

    # 3. Location directory
    if args.location:
        import pydiskmark.app as _app
        _app.location_dir = args.location
        _app.data_dir = str(Path(args.location) / "pdm-data")

    app.verbose = args.verbose


def _do_run(args: argparse.Namespace) -> int:
    """Execute the benchmark and handle output. Returns exit code."""
    import pydiskmark.app as app
    from .benchmark_runner import BenchmarkRunner
    from .util import delete_directory

    _apply_args_to_app(args)

    location = Path(app.location_dir) if app.location_dir else Path.home()
    data_dir = Path(app.data_dir) if app.data_dir else location / "pdm-data"

    # Validate writable
    if not location.exists():
        print(f"error: location directory does not exist: {location}", file=sys.stderr)
        return 1
    if not os.access(str(location), os.W_OK):
        print(f"error: location directory is not writable: {location}", file=sys.stderr)
        return 1

    # --clean
    if args.clean and data_dir.exists():
        sys.stderr.write(f"[Clean] Removing existing data directory: {data_dir}\n")
        delete_directory(data_dir)

    data_dir.mkdir(parents=True, exist_ok=True)

    # init() — collect OS/CPU info
    app.init()

    if app.verbose:
        sys.stderr.write(f"[Info] Profile   : {app.active_profile.symbol}\n")
        sys.stderr.write(f"[Info] Type      : {app.benchmark_type.value}\n")
        sys.stderr.write(f"[Info] Engine    : {app.io_engine.name}\n")
        sys.stderr.write(f"[Info] Location  : {location}\n")
        sys.stderr.write(f"[Info] Data dir  : {data_dir}\n")

    cfg = app.get_config()
    listener = CliListener(verbose=args.verbose)

    listener.hide_cursor()
    try:
        runner = BenchmarkRunner(listener, cfg)
        benchmark = runner.execute()
    except KeyboardInterrupt:
        listener._cancelled.set()
        listener.show_cursor()
        listener.finish_bar()
        print("\nBenchmark cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        listener.show_cursor()
        listener.finish_bar()
        print(f"\nerror: benchmark failed: {exc}", file=sys.stderr)
        return 1
    finally:
        listener.show_cursor()
        listener.finish_bar()

    # Print results
    _print_results(benchmark)

    # Export if requested
    if args.export:
        try:
            from .exporter import export
            export(benchmark, args.export)
            print(f"[Export] Results written to: {args.export}")
        except Exception as exc:
            print(f"warning: export failed: {exc}", file=sys.stderr)

    # Cleanup data directory if auto_remove_data
    if app.auto_remove_data and data_dir.exists():
        delete_directory(data_dir)

    return 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    """Parse arguments and dispatch sub-commands."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        sys.exit(_do_run(args))
    else:
        parser.print_help()
        sys.exit(1)
