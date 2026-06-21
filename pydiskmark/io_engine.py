"""Platform-abstracted file I/O for the MODERN engine.

Provides a uniform API for positional read/write with optional Direct I/O
(unbuffered) and write-sync support across Linux, macOS, and Windows.

POSIX (Linux/macOS):
    Uses os.open() + os.pwrite() / os.pread() with O_DIRECT / F_NOCACHE.

Windows:
    Uses ctypes calls to kernel32 CreateFileW / WriteFile / ReadFile with
    FILE_FLAG_NO_BUFFERING and OVERLAPPED structs for positional I/O.

If Direct I/O cannot be opened, silently falls back to buffered I/O.
"""
from __future__ import annotations

import ctypes
import logging
import mmap
import os
import platform
import sys
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

_SYSTEM = platform.system()
_IS_WINDOWS = _SYSTEM == "Windows"
_IS_LINUX = _SYSTEM == "Linux"
_IS_DARWIN = _SYSTEM == "Darwin"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Windows kernel32 constants
if _IS_WINDOWS:
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    CREATE_ALWAYS = 2
    OPEN_ALWAYS = 4
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_NO_BUFFERING = 0x20000000
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MEM_COMMIT = 0x00001000
    MEM_RESERVE = 0x00002000
    MEM_RELEASE = 0x00008000
    PAGE_READWRITE = 0x04
    NULL = 0


# ---------------------------------------------------------------------------
# Aligned buffer allocation
# ---------------------------------------------------------------------------

def alloc_aligned(size: int, alignment: int) -> ctypes.Array:
    """Allocate a byte buffer of *size* bytes aligned to *alignment*.

    On Windows: uses VirtualAlloc (always page-aligned, >= 4096).
    On POSIX: uses mmap anonymous mapping (page-aligned).

    Returns a ctypes char array that can be passed to pwrite/pread.
    The buffer is zero-filled.
    """
    if alignment <= 0:
        alignment = 4096

    if _IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32

        # Properly type VirtualAlloc so it returns a pointer, not a truncated int
        _VirtualAlloc = kernel32.VirtualAlloc
        _VirtualAlloc.argtypes = [
            ctypes.c_void_p,  # lpAddress
            ctypes.c_size_t,  # dwSize
            ctypes.c_ulong,   # flAllocationType
            ctypes.c_ulong,   # flProtect
        ]
        _VirtualAlloc.restype = ctypes.c_void_p

        ptr = _VirtualAlloc(
            None,
            size,
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        )
        if not ptr:
            raise OSError(f"VirtualAlloc failed (size={size})")

        # Wrap the raw pointer in a ctypes char array
        buf = (ctypes.c_char * size).from_address(ptr)
        buf._alloc_ptr = ptr  # stash for free_aligned
        return buf
    else:
        # POSIX: mmap anonymous mapping is always page-aligned
        buf_mmap = mmap.mmap(-1, size)
        # Wrap as ctypes array
        buf = (ctypes.c_char * size).from_buffer(buf_mmap)
        buf._mmap = buf_mmap  # prevent GC
        return buf


def free_aligned(buf: ctypes.Array) -> None:
    """Free a buffer previously allocated by alloc_aligned."""
    if _IS_WINDOWS and hasattr(buf, "_alloc_ptr"):
        kernel32 = ctypes.windll.kernel32
        _VirtualFree = kernel32.VirtualFree
        _VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong]
        _VirtualFree.restype = ctypes.c_int
        _VirtualFree(buf._alloc_ptr, 0, MEM_RELEASE)
    elif hasattr(buf, "_mmap"):
        buf._mmap.close()


def get_buffer_address(buf: ctypes.Array) -> int:
    """Return the memory address of the buffer (for alignment checks)."""
    return ctypes.addressof(buf)


# ---------------------------------------------------------------------------
# File open
# ---------------------------------------------------------------------------

def open_file(
    path: Union[str, Path],
    write: bool,
    create: bool = False,
    direct: bool = False,
    sync: bool = False,
) -> int:
    """Open a file for the MODERN engine.

    Returns a file descriptor (POSIX) or Windows HANDLE (as int).
    If Direct I/O fails, falls back to buffered and logs a warning.

    Args:
        path: File path.
        write: True for write access, False for read-only.
        create: True to create the file if it doesn't exist.
        direct: True to request unbuffered / Direct I/O.
        sync: True to request synchronous writes (write-through).
    """
    filepath = str(path)

    if _IS_WINDOWS:
        return _open_file_windows(filepath, write, create, direct, sync)
    else:
        return _open_file_posix(filepath, write, create, direct, sync)


def _open_file_windows(
    filepath: str, write: bool, create: bool, direct: bool, sync: bool
) -> int:
    """Open via CreateFileW with optional FILE_FLAG_NO_BUFFERING."""
    kernel32 = ctypes.windll.kernel32

    access = GENERIC_WRITE | GENERIC_READ if write else GENERIC_READ
    share = FILE_SHARE_READ | FILE_SHARE_WRITE

    if create:
        disposition = OPEN_ALWAYS
    else:
        disposition = OPEN_EXISTING

    flags = FILE_ATTRIBUTE_NORMAL
    if direct:
        flags |= FILE_FLAG_NO_BUFFERING
    if sync:
        flags |= FILE_FLAG_WRITE_THROUGH

    handle = kernel32.CreateFileW(
        filepath,
        access,
        share,
        None,  # security attributes
        disposition,
        flags,
        None,  # template file
    )

    if handle == INVALID_HANDLE_VALUE:
        if direct:
            # Fallback: retry without FILE_FLAG_NO_BUFFERING
            logger.warning(
                "Direct I/O open failed for %s, falling back to buffered I/O.",
                filepath,
            )
            flags = FILE_ATTRIBUTE_NORMAL
            if sync:
                flags |= FILE_FLAG_WRITE_THROUGH
            handle = kernel32.CreateFileW(
                filepath, access, share, None, disposition, flags, None,
            )
            if handle == INVALID_HANDLE_VALUE:
                err = ctypes.GetLastError()
                raise OSError(f"CreateFileW failed (fallback), error={err}")
        else:
            err = ctypes.GetLastError()
            raise OSError(f"CreateFileW failed, error={err}")

    return handle


def _open_file_posix(
    filepath: str, write: bool, create: bool, direct: bool, sync: bool
) -> int:
    """Open via os.open() with optional O_DIRECT / F_NOCACHE."""
    if write:
        flags = os.O_RDWR
        if create:
            flags |= os.O_CREAT
    else:
        flags = os.O_RDONLY

    if direct and _IS_LINUX:
        flags |= getattr(os, "O_DIRECT", 0)
    if sync:
        flags |= getattr(os, "O_DSYNC", 0)

    try:
        fd = os.open(filepath, flags, 0o644)
    except OSError:
        if direct:
            # Fallback: retry without O_DIRECT
            logger.warning(
                "Direct I/O (O_DIRECT) open failed for %s, falling back to buffered.",
                filepath,
            )
            if _IS_LINUX:
                flags &= ~getattr(os, "O_DIRECT", 0)
            fd = os.open(filepath, flags, 0o644)
        else:
            raise

    # macOS: F_NOCACHE must be set after open
    if direct and _IS_DARWIN:
        try:
            import fcntl
            fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)
        except (ImportError, OSError) as exc:
            logger.warning("F_NOCACHE failed for %s: %s", filepath, exc)

    return fd


# ---------------------------------------------------------------------------
# Positional read / write
# ---------------------------------------------------------------------------

def pwrite(fd: int, buf: ctypes.Array, offset: int, nbytes: int) -> int:
    """Write *nbytes* from *buf* at file *offset*. Returns bytes written."""
    if _IS_WINDOWS:
        return _pwrite_windows(fd, buf, offset, nbytes)
    else:
        return os.pwrite(fd, bytes(buf[:nbytes]), offset)


def pread(fd: int, buf: ctypes.Array, offset: int, nbytes: int) -> int:
    """Read *nbytes* into *buf* at file *offset*. Returns bytes read."""
    if _IS_WINDOWS:
        return _pread_windows(fd, buf, offset, nbytes)
    else:
        data = os.pread(fd, nbytes, offset)
        # Copy into the provided buffer
        for i, b in enumerate(data):
            buf[i] = b
        return len(data)


def _pwrite_windows(handle: int, buf: ctypes.Array, offset: int, nbytes: int) -> int:
    """WriteFile with OVERLAPPED for positional write."""
    kernel32 = ctypes.windll.kernel32

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", ctypes.c_ulong),
            ("OffsetHigh", ctypes.c_ulong),
            ("hEvent", ctypes.c_void_p),
        ]

    overlapped = OVERLAPPED()
    overlapped.Offset = offset & 0xFFFFFFFF
    overlapped.OffsetHigh = (offset >> 32) & 0xFFFFFFFF
    overlapped.hEvent = None

    bytes_written = ctypes.c_ulong(0)
    success = kernel32.WriteFile(
        handle,
        buf,
        nbytes,
        ctypes.byref(bytes_written),
        ctypes.byref(overlapped),
    )
    if not success:
        err = ctypes.GetLastError()
        raise OSError(f"WriteFile failed, error={err}")
    return bytes_written.value


def _pread_windows(handle: int, buf: ctypes.Array, offset: int, nbytes: int) -> int:
    """ReadFile with OVERLAPPED for positional read."""
    kernel32 = ctypes.windll.kernel32

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", ctypes.c_ulong),
            ("OffsetHigh", ctypes.c_ulong),
            ("hEvent", ctypes.c_void_p),
        ]

    overlapped = OVERLAPPED()
    overlapped.Offset = offset & 0xFFFFFFFF
    overlapped.OffsetHigh = (offset >> 32) & 0xFFFFFFFF
    overlapped.hEvent = None

    bytes_read = ctypes.c_ulong(0)
    success = kernel32.ReadFile(
        handle,
        buf,
        nbytes,
        ctypes.byref(bytes_read),
        ctypes.byref(overlapped),
    )
    if not success:
        err = ctypes.GetLastError()
        raise OSError(f"ReadFile failed, error={err}")
    return bytes_read.value


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------

def close_file(fd: int) -> None:
    """Close a file descriptor / handle opened by open_file()."""
    if _IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32
        kernel32.CloseHandle(fd)
    else:
        os.close(fd)
