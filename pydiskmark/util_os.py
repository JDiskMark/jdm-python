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
# Extra drive info (no admin required)
# ---------------------------------------------------------------------------

def get_filesystem(path: Union[str, Path]) -> str:
    """Return the volume filesystem type (e.g. 'NTFS', 'FAT32').

    Uses GetVolumeInformationW — no admin required on Windows.
    Falls back to 'Unknown' on errors or non-Windows platforms.
    """
    if _SYSTEM != "Windows":
        return _get_filesystem_posix(path)
    try:
        import ctypes
        root = str(Path(path).anchor)  # e.g. "C:\\"
        fs_buf = ctypes.create_unicode_buffer(256)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            root, None, 0, None, None, None, fs_buf, 256,
        )
        return fs_buf.value if ok and fs_buf.value else "Unknown"
    except Exception:
        return "Unknown"


def _get_filesystem_posix(path: Union[str, Path]) -> str:
    try:
        result = subprocess.run(
            ["df", "-T", str(path)],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            if str(path) in line or line.startswith("/dev/"):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1]
    except Exception:
        pass
    return "Unknown"


def get_bus_type(path: Union[str, Path]) -> str:
    """Return the drive interface/bus type (e.g. 'NVMe', 'SATA', 'USB').

    Windows: PowerShell Get-Partition | Get-Disk | BusType — no admin needed.
    Other platforms: lsblk TRAN or diskutil.
    """
    if _SYSTEM == "Windows":
        return _get_bus_type_windows(Path(path).resolve())
    elif _SYSTEM == "Linux":
        return _get_bus_type_linux(Path(path).resolve())
    elif _SYSTEM == "Darwin":
        return _get_bus_type_macos(Path(path).resolve())
    return "Unknown"


def _get_bus_type_windows(path: Path) -> str:
    drive_letter = _get_drive_letter_windows(path)
    if not drive_letter:
        return "Unknown"
    ps_cmd = (
        f"Get-Partition -DriveLetter '{drive_letter}' | "
        f"Get-Disk | Select-Object -ExpandProperty BusType"
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
        logger.warning("Bus type query failed: %s", exc)
    return "Unknown"


def _get_bus_type_linux(path: Path) -> str:
    partition = _get_partition_linux(path)
    if not partition:
        return "Unknown"
    try:
        result = subprocess.run(
            ["lsblk", "-no", "TRAN", partition],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            t = line.strip().upper()
            if t:
                return t
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "Unknown"


def _get_bus_type_macos(path: Path) -> str:
    device = _get_device_macos(path)
    if not device:
        return "Unknown"
    try:
        result = subprocess.run(
            ["diskutil", "info", device],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            if "Protocol:" in line:
                return line.split("Protocol:")[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "Unknown"


def get_sector_sizes(path: Union[str, Path]) -> str:
    """Return logical / physical sector sizes as a formatted string.

    e.g. '512 B / 4096 B' or '4096 B'.
    Uses GetDiskFreeSpaceW (logical) and DeviceIoControl
    StorageAccessAlignmentProperty (physical) — both no-admin on Windows.
    """
    if _SYSTEM == "Windows":
        return _get_sector_sizes_windows(Path(path).resolve())
    elif _SYSTEM == "Linux":
        return _get_sector_sizes_linux(Path(path).resolve())
    return "Unknown"


def _get_sector_sizes_windows(path: Path) -> str:
    import ctypes
    import ctypes.wintypes
    root = str(path.anchor)

    # Logical sector size via GetDiskFreeSpaceW
    logical = 0
    try:
        spc = ctypes.c_uint32()
        bps = ctypes.c_uint32()
        ffc = ctypes.c_uint32()
        tc = ctypes.c_uint32()
        ctypes.windll.kernel32.GetDiskFreeSpaceW(
            root,
            ctypes.byref(spc), ctypes.byref(bps),
            ctypes.byref(ffc), ctypes.byref(tc),
        )
        logical = bps.value
    except Exception:
        pass

    # Physical sector size via DeviceIoControl (StorageAccessAlignmentProperty)
    physical = 0
    drive_letter = _get_drive_letter_windows(path)
    if drive_letter:
        try:
            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 0x01
            FILE_SHARE_WRITE = 0x02
            OPEN_EXISTING = 3
            IOCTL = 0x002D1400  # IOCTL_STORAGE_QUERY_PROPERTY

            class _Query(ctypes.Structure):
                _fields_ = [("PropertyId", ctypes.c_uint),
                             ("QueryType", ctypes.c_uint),
                             ("AdditionalParameters", ctypes.c_byte * 1)]

            class _AlignDesc(ctypes.Structure):
                _fields_ = [
                    ("Version", ctypes.c_uint),
                    ("Size", ctypes.c_uint),
                    ("TotalCacheSize", ctypes.c_uint64),
                    ("BytesPerCacheLine", ctypes.c_uint),
                    ("BytesOffsetForCacheAlignment", ctypes.c_uint),
                    ("BytesPerLogicalSector", ctypes.c_uint),
                    ("BytesPerPhysicalSector", ctypes.c_uint),
                    ("BytesOffsetForSectorAlignment", ctypes.c_uint),
                ]

            device_path = f"\\\\.\\{drive_letter}:"
            h = ctypes.windll.kernel32.CreateFileW(
                device_path, GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None, OPEN_EXISTING, 0, None,
            )
            INVALID = ctypes.wintypes.HANDLE(-1).value
            if h != INVALID:
                q = _Query(PropertyId=6, QueryType=0)  # StorageAccessAlignmentProperty
                desc = _AlignDesc()
                returned = ctypes.c_uint32()
                ok = ctypes.windll.kernel32.DeviceIoControl(
                    h, IOCTL,
                    ctypes.byref(q), ctypes.sizeof(q),
                    ctypes.byref(desc), ctypes.sizeof(desc),
                    ctypes.byref(returned), None,
                )
                ctypes.windll.kernel32.CloseHandle(h)
                if ok:
                    physical = desc.BytesPerPhysicalSector
        except Exception:
            pass

    if logical and physical and physical != logical:
        return f"{logical} B / {physical} B"
    elif logical:
        return f"{logical} B"
    return "Unknown"


def _get_sector_sizes_linux(path: Path) -> str:
    partition = _get_partition_linux(path)
    if not partition:
        return "Unknown"
    try:
        result = subprocess.run(
            ["lsblk", "-no", "LOG-SEC,PHY-SEC", partition],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                logical, physical = parts[0], parts[1]
                if logical == physical:
                    return f"{logical} B"
                return f"{logical} B / {physical} B"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "Unknown"




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
