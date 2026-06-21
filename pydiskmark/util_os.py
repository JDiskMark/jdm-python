"""OS-specific utility functions for pydiskmark.

Maps to UtilOs.java in jdm-java.

Provides platform-specific implementations for:
  - Processor name detection
  - Drive model detection
  - Partition / drive letter identification
  - Disk usage (capacity) reporting
  - Cache flushing and page-cache drop
  - Admin / root privilege detection
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

from .disk_usage_info import DiskUsageInfo

logger = logging.getLogger(__name__)

_SYSTEM = platform.system()  # "Windows", "Linux", "Darwin"


# ---------------------------------------------------------------------------
# Admin / root detection
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """Return True if the current process has elevated privileges."""
    if _SYSTEM == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        return os.geteuid() == 0


# ---------------------------------------------------------------------------
# Processor name
# ---------------------------------------------------------------------------

def get_processor_name() -> str:
    """Return the CPU brand string.

    Windows: wmic, then PowerShell Get-CimInstance fallback.
    Linux: lscpu → 'Model name:'.
    macOS: sysctl -n machdep.cpu.brand_string.
    """
    if _SYSTEM == "Windows":
        return _get_processor_name_windows()
    elif _SYSTEM == "Darwin":
        return _get_processor_name_macos()
    elif _SYSTEM == "Linux":
        return _get_processor_name_linux()
    return platform.processor() or "Unknown CPU"


def _get_processor_name_windows() -> str:
    # Try wmic first (fast, available on older Windows)
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "Name"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("Name"):
                return stripped
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: PowerShell Get-CimInstance
    try:
        result = subprocess.run(
            ["powershell.exe", "-Command",
             "Get-CimInstance -Class Win32_Processor | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return platform.processor() or "Unknown CPU"


def _get_processor_name_macos() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        name = result.stdout.strip()
        if name:
            return name
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return platform.processor() or "Unknown CPU"


def _get_processor_name_linux() -> str:
    try:
        result = subprocess.run(
            ["lscpu"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            if line.startswith("Model name:"):
                return line.split(":", 1)[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return platform.processor() or "Unknown CPU"


# ---------------------------------------------------------------------------
# Drive model
# ---------------------------------------------------------------------------

def get_drive_model(location_dir: Union[str, Path]) -> str:
    """Return the physical drive model string for the volume hosting *location_dir*.

    Windows: PowerShell Get-Partition → Get-Disk pipeline.
    Linux: df → lsblk → MODEL.
    macOS: df → diskutil info → Device / Media Name.
    """
    path = Path(location_dir).resolve()

    if _SYSTEM == "Windows":
        return _get_drive_model_windows(path)
    elif _SYSTEM == "Darwin":
        return _get_drive_model_macos(path)
    elif _SYSTEM == "Linux":
        return _get_drive_model_linux(path)
    return "Unknown Drive"


def _get_drive_model_windows(path: Path) -> str:
    """Resolve drive letter → physical disk model via PowerShell."""
    drive_letter = _get_drive_letter_windows(path)
    if not drive_letter:
        return "Unknown Drive"

    # PowerShell one-liner: Get-Partition → Get-Disk → Model
    ps_cmd = (
        f"Get-Partition -DriveLetter '{drive_letter}' | "
        f"Get-Disk | Select-Object -ExpandProperty FriendlyName"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("PowerShell drive model query failed: %s", exc)

    return "Unknown Drive"


def _get_drive_model_linux(path: Path) -> str:
    """df → lsblk -no pkname → lsblk --output MODEL."""
    partition = _get_partition_linux(path)
    if not partition:
        return "Unknown Drive"

    # Get parent device name(s)
    try:
        result = subprocess.run(
            ["lsblk", "-no", "pkname", partition],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        device_names = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "Unknown Drive"

    if not device_names:
        return "Unknown Drive"

    models = []
    for dname in device_names:
        device_path = f"/dev/{dname}"
        try:
            result = subprocess.run(
                ["lsblk", device_path, "--output", "MODEL"],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            )
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped and stripped != "MODEL":
                    models.append(stripped)
                    break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if len(models) == 1:
        return models[0]
    elif len(models) > 1:
        return "Multiple drives: " + ":".join(models)
    return "Unknown Drive"


def _get_drive_model_macos(path: Path) -> str:
    """df → diskutil info → Device / Media Name."""
    device_path = _get_device_macos(path)
    if not device_path:
        return "Unknown Drive"

    try:
        result = subprocess.run(
            ["diskutil", "info", device_path],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            if "Device / Media Name:" in line:
                return line.split("Device / Media Name:")[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "Unknown Drive"


# ---------------------------------------------------------------------------
# Partition / drive letter
# ---------------------------------------------------------------------------

def get_partition_id(path: Union[str, Path]) -> str:
    """Return partition identifier for the volume containing *path*.

    Windows: drive letter (e.g. 'C').
    Linux: partition path (e.g. 'sda2').
    macOS: device path (e.g. 'disk1s1').
    """
    p = Path(path).resolve()

    if _SYSTEM == "Windows":
        letter = _get_drive_letter_windows(p)
        return letter if letter else str(p.anchor)
    elif _SYSTEM == "Linux":
        partition = _get_partition_linux(p)
        if partition and "/dev/" in partition:
            return partition.split("/dev/")[1]
        return partition or str(p.anchor)
    elif _SYSTEM == "Darwin":
        device = _get_device_macos(p)
        if device and "/dev/" in device:
            return device.split("/dev/")[1]
        return device or str(p.anchor)
    return str(p.anchor)


def _get_drive_letter_windows(path: Path) -> Optional[str]:
    """Extract drive letter from path root (e.g. 'C')."""
    root = str(path.anchor)
    if len(root) >= 2 and root[1] == ":":
        letter = root[0]
        if letter.isalpha():
            return letter.upper()
    return None


def _get_partition_linux(path: Path) -> Optional[str]:
    """Use df to find the partition hosting *path*."""
    try:
        result = subprocess.run(
            ["df", "-k", str(path)],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            if "/dev/" in line:
                return line.split()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _get_device_macos(path: Path) -> Optional[str]:
    """Use df to find the device hosting *path*."""
    try:
        result = subprocess.run(
            ["df", "-k", str(path)],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            if "/dev/" in line:
                return line.split()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ---------------------------------------------------------------------------
# Disk usage (capacity)
# ---------------------------------------------------------------------------

def get_disk_usage(path: Union[str, Path]) -> DiskUsageInfo:
    """Return disk usage info for the volume containing *path*.

    Uses shutil.disk_usage() — pure Python, cross-platform, no subprocess
    overhead, no locale issues.  Replaces Java's capacity.ps1 approach.
    """
    try:
        usage = shutil.disk_usage(str(path))
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        percent_used = (used_gb / total_gb * 100) if total_gb > 0 else 0
        return DiskUsageInfo(
            percent_used=percent_used,
            free_gb=free_gb,
            used_gb=used_gb,
            total_gb=total_gb,
        )
    except OSError as exc:
        logger.warning("shutil.disk_usage() failed for %s: %s", path, exc)
        return DiskUsageInfo()


# ---------------------------------------------------------------------------
# Cache flush / drop
# ---------------------------------------------------------------------------

def flush_and_drop_cache() -> None:
    """Flush dirty data and drop the OS page cache.

    Requires elevated privileges. If non-privileged, prints manual
    instructions and blocks until the user confirms.

    Linux: sync + echo 1 > /proc/sys/vm/drop_caches
    macOS: sync + purge
    Windows: EmptyStandbyList.exe
    """
    if _SYSTEM == "Linux":
        _flush_and_drop_linux()
    elif _SYSTEM == "Darwin":
        _flush_and_drop_macos()
    elif _SYSTEM == "Windows":
        _flush_and_drop_windows()


def _flush_and_drop_linux() -> None:
    if not is_admin():
        print("\n[Cache Drop] Run as root to auto-flush, or run manually:")
        print("  sudo sh -c 'sync; echo 1 > /proc/sys/vm/drop_caches'")
        input("Press Enter after flushing cache to continue...")
        return

    # sync first
    try:
        subprocess.run(["sync"], timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("sync failed: %s", exc)

    # drop caches
    try:
        subprocess.run(
            ["/bin/sh", "-c", "echo 1 > /proc/sys/vm/drop_caches"],
            timeout=10, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("drop_caches failed: %s", exc)


def _flush_and_drop_macos() -> None:
    if not is_admin():
        print("\n[Cache Drop] Run as root to auto-flush, or run manually:")
        print("  sudo purge")
        input("Press Enter after flushing cache to continue...")
        return

    try:
        subprocess.run(["sync"], timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        subprocess.run(["purge"], timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("purge failed: %s", exc)


def _flush_and_drop_windows() -> None:
    """Attempt to clear Windows standby cache.

    Tries EmptyStandbyList.exe (if available on PATH or in the app directory).
    If not available or not admin, prints instructions.
    """
    if not is_admin():
        print("\n[Cache Drop] Run as Administrator to auto-flush, or manually clear standby list.")
        print("  You can use RAMMap (Sysinternals) → Empty → Empty Standby List")
        input("Press Enter after flushing cache to continue...")
        return

    # Try to find EmptyStandbyList.exe
    for candidate in ["EmptyStandbyList.exe", ".\\EmptyStandbyList.exe"]:
        try:
            subprocess.run([candidate], timeout=30, check=False)
            logger.info("EmptyStandbyList.exe ran successfully")
            return
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            logger.warning("EmptyStandbyList.exe timed out")
            return

    print("\n[Cache Drop] EmptyStandbyList.exe not found.")
    print("  Download from Sysinternals or clear cache manually.")
    input("Press Enter to continue...")
