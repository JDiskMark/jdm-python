"""Global application state and lifecycle functions.

Maps to App.java in jdm-java.

In the Java implementation App is a God class of static fields and methods.
Here they are module-level variables and functions, which is idiomatic Python
and equivalent at runtime.

The update_metrics() function is the most performance-critical piece:
it maintains running cumulative statistics (avg / max / min / latency) across
all samples in a benchmark run.  The formula must match Java exactly because
the cum_* fields on each Sample are what get stored in the database and
exported to JSON/CSV.
"""
from __future__ import annotations

import os
import platform
import sys
import threading
from pathlib import Path
from typing import Optional

from .benchmark import IOMode
from .benchmark_profile import BenchmarkProfile
from .disk_usage_info import DiskUsageInfo

VERSION: str = "0.1.0"
APP_NAME: str = "pydiskmark"
DATADIRNAME: str = "pdm-data"

# Cache directory mirrors ~/.jdm/<VERSION>/ in jdm-java.
# Resolved at runtime (after VERSION is set) via config._cache_dir().
APP_CACHE_DIR_NAME: str = str(Path.home() / ".pdm" / VERSION)
APP_CACHE_DIR: Path = Path(APP_CACHE_DIR_NAME)

# ---------------------------------------------------------------------------
# Application mode
# ---------------------------------------------------------------------------

class Mode:
    CLI = "CLI"
    GUI = "GUI"

mode: str = Mode.CLI

# ---------------------------------------------------------------------------
# System info (populated by init())
# ---------------------------------------------------------------------------

os_name: str = ""
arch: str = ""
processor_name: str = ""
runtime: str = ""
username: str = "anonymous"

is_root: bool = False
is_admin: bool = False

# ---------------------------------------------------------------------------
# Benchmark configuration
# ---------------------------------------------------------------------------

from .benchmark import BenchmarkType, BlockSequence, IoEngine, SectorAlignment

active_profile: BenchmarkProfile = BenchmarkProfile.QUICK_TEST
profile_modified: bool = False
benchmark_type: BenchmarkType = BenchmarkType.READ_WRITE
block_sequence: BlockSequence = BlockSequence.SEQUENTIAL
num_of_samples: int = 200
num_of_blocks: int = 32
block_size_kb: int = 512
num_of_threads: int = 1
io_engine: IoEngine = IoEngine.MODERN
direct_enable: bool = False
write_sync_enable: bool = False
sector_alignment: SectorAlignment = SectorAlignment.ALIGN_4K
multi_file: bool = True

# ---------------------------------------------------------------------------
# Run control
# ---------------------------------------------------------------------------

auto_save: bool = False
auto_remove_data: bool = True
auto_reset: bool = True
verbose: bool = False

# Internal: last-loaded theme string from pdm.properties.
# Written by config.load_config(); read by gui/__init__.py to apply before
# MainWindow is constructed (sv_ttk requires an active Tk root).
_saved_theme: str = "dark"

# ---------------------------------------------------------------------------
# File system locations
# ---------------------------------------------------------------------------

location_dir: str = ""
data_dir: str = ""
export_path: Optional[str] = None

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

next_sample_number: int = 1       # global monotonic sample counter

# ---------------------------------------------------------------------------
# Running benchmark statistics
# Initialised to -1 to distinguish "never set" from 0.
# ---------------------------------------------------------------------------

w_max: float = -1.0
w_min: float = -1.0
w_avg: float = -1.0
w_acc: float = -1.0
w_iops: int = -1

r_max: float = -1.0
r_min: float = -1.0
r_avg: float = -1.0
r_acc: float = -1.0
r_iops: int = -1

# Lock protecting all w_*/r_* stats — update_metrics may be called from
# multiple sample threads simultaneously.
_metrics_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def init() -> None:
    """Populate system info from the runtime environment.

    Called once before the first benchmark run.  Equivalent to App.init()
    in jdm-java (the parts that do not touch Swing / Derby).
    """
    global os_name, arch, processor_name, runtime, username
    global location_dir, data_dir, is_admin, is_root

    os_name = platform.system()
    arch = platform.machine()
    runtime = f"Python {sys.version.split()[0]}"
    username = _get_username()

    from .util import get_processor_name
    processor_name = get_processor_name()

    # Detect elevated privileges
    from .util_os import is_admin as _check_admin
    _elevated = _check_admin()
    is_admin = _elevated
    is_root = _elevated

    # Load persisted settings (mirrors App.loadConfig() call in App.init()).
    # Must happen before location_dir defaulting so a saved path is honoured.
    from . import config as _config
    _config.load_config()

    if not location_dir:
        location_dir = str(Path.home())
    if not data_dir:
        data_dir = str(Path(location_dir) / DATADIRNAME)


def _get_username() -> str:
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USERNAME", os.environ.get("USER", "anonymous"))


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def load_profile(profile: BenchmarkProfile) -> None:
    """Apply all settings from *profile* to global state.

    Mirrors App.loadProfile() in jdm-java which calls saveConfig() in a
    finally block after updating all fields.
    """
    global active_profile, profile_modified
    global benchmark_type, block_sequence, num_of_threads, num_of_samples
    global num_of_blocks, block_size_kb, io_engine, direct_enable
    global write_sync_enable, sector_alignment, multi_file

    try:
        active_profile = profile
        profile_modified = False
        benchmark_type = profile.benchmark_type
        block_sequence = profile.block_sequence
        num_of_threads = profile.num_threads
        num_of_samples = profile.num_samples
        num_of_blocks = profile.num_blocks
        block_size_kb = profile.block_size_kb
        io_engine = profile.io_engine
        direct_enable = profile.direct_enable
        write_sync_enable = profile.write_sync_enable
        sector_alignment = profile.sector_alignment
        multi_file = profile.multi_file
    finally:
        from . import config as _config
        _config.save_config()


# ---------------------------------------------------------------------------
# Config snapshot
# ---------------------------------------------------------------------------


def get_config():
    """Capture current global state as an immutable BenchmarkConfig snapshot.

    Mirrors App.getConfig() in jdm-java.
    """
    from .benchmark import BenchmarkConfig, KILOBYTE

    cfg = BenchmarkConfig()
    cfg.app_version = VERSION
    cfg.profile = active_profile
    cfg.profile_modified = profile_modified
    cfg.benchmark_type = benchmark_type
    cfg.block_order = block_sequence
    cfg.num_blocks = num_of_blocks
    cfg.block_size = block_size_kb * KILOBYTE
    cfg.num_samples = num_of_samples
    cfg.num_threads = num_of_threads
    cfg.tx_size = block_size_kb * num_of_blocks * num_of_samples
    cfg.io_engine = io_engine
    cfg.direct_io_enabled = direct_enable
    cfg.write_sync_enabled = write_sync_enable
    cfg.sector_alignment = sector_alignment
    cfg.multi_file_enabled = multi_file
    cfg.gc_retry_enabled = False
    cfg.gc_hints_enabled = False
    cfg.test_dir = data_dir
    return cfg


# ---------------------------------------------------------------------------
# Running statistics — update_metrics()
# ---------------------------------------------------------------------------


def update_metrics(sample) -> None:
    """Update global running stats from a completed sample.

    Also writes cumulative fields back onto the sample so they can be
    stored / exported (cum_avg, cum_max, cum_min, cum_acc_time_ms).

    Must exactly replicate App.updateMetrics() in jdm-java:

        if max == -1 or bw > max: max = bw
        if min == -1 or bw < min: min = bw
        if avg == -1:
            avg = bw
        else:
            avg = ((n-1) * avg + bw) / n   # n = sample.sample_num
        (same formula for access time)
    """
    global w_max, w_min, w_avg, w_acc
    global r_max, r_min, r_avg, r_acc

    with _metrics_lock:
        n = sample.sample_num
        bw = sample.bw_mb_sec
        lat = sample.access_time_ms

        if sample.type_ == IOMode.WRITE:
            # --- bandwidth ---
            if w_max == -1.0 or bw > w_max:
                w_max = bw
            if w_min == -1.0 or bw < w_min:
                w_min = bw
            if w_avg == -1.0:
                w_avg = bw
            else:
                w_avg = ((n - 1) * w_avg + bw) / n
            # --- latency ---
            if w_acc == -1.0:
                w_acc = lat
            else:
                w_acc = ((n - 1) * w_acc + lat) / n
            # --- write back to sample ---
            sample.cum_avg = w_avg
            sample.cum_max = w_max
            sample.cum_min = w_min
            sample.cum_acc_time_ms = w_acc

        else:  # READ
            if r_max == -1.0 or bw > r_max:
                r_max = bw
            if r_min == -1.0 or bw < r_min:
                r_min = bw
            if r_avg == -1.0:
                r_avg = bw
            else:
                r_avg = ((n - 1) * r_avg + bw) / n
            if r_acc == -1.0:
                r_acc = lat
            else:
                r_acc = ((n - 1) * r_acc + lat) / n
            sample.cum_avg = r_avg
            sample.cum_max = r_max
            sample.cum_min = r_min
            sample.cum_acc_time_ms = r_acc


# ---------------------------------------------------------------------------
# State reset helpers
# ---------------------------------------------------------------------------


def reset_test_data() -> None:
    """Reset running statistics (not the sample counter).

    Mirrors App.resetTestData() in jdm-java.
    Called before each benchmark when auto_reset=True.
    """
    global w_max, w_min, w_avg, w_acc, w_iops
    global r_max, r_min, r_avg, r_acc, r_iops

    with _metrics_lock:
        w_max = w_min = w_avg = w_acc = -1.0
        w_iops = -1
        r_max = r_min = r_avg = r_acc = -1.0
        r_iops = -1


def reset_sequence() -> None:
    """Reset the global sample counter back to 1."""
    global next_sample_number
    next_sample_number = 1


def set_location_dir(directory: str) -> None:
    """Set location_dir and derive data_dir from it."""
    global location_dir, data_dir
    location_dir = str(directory)
    data_dir = str(Path(directory) / DATADIRNAME)


# ---------------------------------------------------------------------------
# OS convenience helpers (Phase 1 delegates to util stubs)
# ---------------------------------------------------------------------------


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_windows() -> bool:
    return platform.system() == "Windows"


def get_drive_model() -> str:
    from .util import get_drive_model as _gdm
    return _gdm(location_dir or Path.home())


def get_partition_id() -> str:
    from .util import get_partition_id as _gpi
    return _gpi(location_dir or Path.home())


def get_disk_usage() -> DiskUsageInfo:
    from .util import get_disk_usage as _gdu
    return _gdu(location_dir or Path.home())


# ---------------------------------------------------------------------------
# Messaging helpers
# ---------------------------------------------------------------------------


def msg(message: str) -> None:
    """Print a status message (mirrors App.msg() CLI branch)."""
    if verbose:
        print(message)


def err(message: str) -> None:
    """Print an error message."""
    import sys as _sys
    print(message, file=_sys.stderr)
