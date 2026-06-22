"""Utility helpers — pure-Python functions and OS-specific delegates.

Pure helpers (rand_int, delete_directory) live here.
OS-specific implementations are in util_os.py.
"""
from __future__ import annotations

import random as _random
import shutil
from pathlib import Path
from typing import Union

from .disk_usage_info import DiskUsageInfo


# ---------------------------------------------------------------------------
# Pure helpers (used across all phases)
# ---------------------------------------------------------------------------

def rand_int(min_val: int, max_val: int) -> int:
    """Return a pseudo-random integer in [min_val, max_val] inclusive.

    Mirrors Util.randInt() in jdm-java (inclusive of both endpoints).
    """
    return _random.randint(min_val, max_val)


def delete_directory(path: Union[str, Path]) -> bool:
    """Recursively delete *path* and all contents.

    Returns True if the directory was removed, False if it did not exist
    or could not be removed.  Mirrors Util.deleteDirectory() in Java.
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        shutil.rmtree(p)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# OS-specific delegates (backed by util_os.py)
# ---------------------------------------------------------------------------

def get_drive_model(location_dir: Union[str, Path]) -> str:
    """Return the drive model string for the drive hosting *location_dir*."""
    from .util_os import get_drive_model as _impl
    return _impl(location_dir)


def get_partition_id(path: Union[str, Path]) -> str:
    """Return the partition identifier (drive letter on Windows; /dev/... on POSIX)."""
    from .util_os import get_partition_id as _impl
    return _impl(path)


def get_disk_usage(path: Union[str, Path]) -> DiskUsageInfo:
    """Return disk usage info for the volume containing *path*."""
    from .util_os import get_disk_usage as _impl
    return _impl(path)


def get_processor_name() -> str:
    """Return the CPU brand string."""
    from .util_os import get_processor_name as _impl
    return _impl()


def get_jvm_info() -> str:
    """Return Python runtime version string (replaces Java's getJvmInfo())."""
    import sys
    return f"Python {sys.version.split()[0]}"


def get_filesystem(path) -> str:
    """Return the volume filesystem type (e.g. 'NTFS')."""
    from .util_os import get_filesystem as _impl
    return _impl(path)


def get_bus_type(path) -> str:
    """Return the drive interface bus type (e.g. 'NVMe', 'SATA')."""
    from .util_os import get_bus_type as _impl
    return _impl(path)


def get_sector_sizes(path) -> str:
    """Return logical/physical sector sizes (e.g. '512 B / 4096 B')."""
    from .util_os import get_sector_sizes as _impl
    return _impl(path)

