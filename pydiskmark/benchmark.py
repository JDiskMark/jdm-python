"""Core enumerations and data model for JDiskMark benchmarks.

Maps to Benchmark.java, BenchmarkConfig.java, BenchmarkSystemInfo.java,
BenchmarkDriveInfo.java in jdm-java.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .benchmark_operation import BenchmarkOperation

# ---------------------------------------------------------------------------
# Binary unit constants (power-of-2, matching Java's App.KILOBYTE / MEGABYTE)
# ---------------------------------------------------------------------------
KILOBYTE: int = 1_024
MEGABYTE: int = 1_024 * KILOBYTE
GIGABYTE: int = 1_024 * MEGABYTE


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BenchmarkType(Enum):
    """Top-level benchmark mode: which I/O phase(s) to run."""
    READ = "Read"
    WRITE = "Write"
    READ_WRITE = "Read & Write"


class IOMode(Enum):
    """Direction of a single I/O operation."""
    READ = "Read"
    WRITE = "Write"


class BlockSequence(Enum):
    """Order in which blocks are accessed within a sample."""
    SEQUENTIAL = "Sequential"
    RANDOM = "Random"


class IoEngine(Enum):
    """I/O implementation strategy.

    MODERN — positional I/O via os.pwrite/pread (Windows: CreateFileW/WriteFile)
             with optional Direct I/O (FILE_FLAG_NO_BUFFERING / O_DIRECT).
    """
    MODERN = "Modern (os.pwrite/pread)"


class SectorAlignment(Enum):
    """Buffer alignment used with the MODERN engine and Direct I/O."""

    NONE = -1          # OS chooses alignment
    ALIGN_512 = 512
    ALIGN_4K = 4_096
    ALIGN_8K = 8_192
    ALIGN_16K = 16_384
    ALIGN_64K = 65_536

    @property
    def bytes(self) -> int:
        """Alignment in bytes; -1 means OS default."""
        return self.value


# ---------------------------------------------------------------------------
# Embedded info objects (mapped to Java @Embeddable classes)
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSystemInfo:
    """System environment captured at benchmark start."""
    os: str = ""
    arch: str = ""
    processor_name: str = ""
    # 'runtime' replaces Java's 'jdk' field — e.g. "Python 3.11.9"
    runtime: str = ""
    location_dir: str = ""


@dataclass
class BenchmarkDriveInfo:
    """Drive metadata captured at benchmark start."""
    drive_model: str = ""
    partition_id: str = ""      # drive letter on Windows; /dev/sdX on Linux
    percent_used: int = 0
    used_gb: float = 0.0
    total_gb: float = 0.0

    def get_usage_title_display(self) -> str:
        return f"{self.percent_used}% ({self.used_gb:.0f}/{self.total_gb:.0f} GB)"

    def get_usage_column_display(self) -> str:
        return f"{self.percent_used}%"


@dataclass
class BenchmarkConfig:
    """Immutable snapshot of benchmark parameters, captured at run start.

    Equivalent to BenchmarkConfig.java (JPA @Embeddable).
    """
    app_version: str = ""
    profile: Optional[object] = None          # BenchmarkProfile enum value
    profile_modified: bool = False

    # Workload definition
    benchmark_type: BenchmarkType = BenchmarkType.WRITE
    block_order: BlockSequence = BlockSequence.SEQUENTIAL
    num_blocks: int = 32          # blocks per sample
    block_size: int = 0           # bytes; = block_size_kb * KILOBYTE
    num_samples: int = 200
    num_threads: int = 1
    tx_size: int = 0              # KB; = block_size_kb * num_blocks * num_samples

    # I/O engine settings
    io_engine: IoEngine = IoEngine.MODERN
    direct_io_enabled: bool = False
    write_sync_enabled: bool = False
    sector_alignment: SectorAlignment = SectorAlignment.ALIGN_4K
    multi_file_enabled: bool = True

    # GC tuning (no-op in Python; kept for API / serialisation compatibility)
    gc_retry_enabled: bool = False
    gc_hints_enabled: bool = False

    # File system target
    test_dir: str = ""

    # ------------------------------------------------------------------
    # Predicates (mirror BenchmarkConfig.java)
    # ------------------------------------------------------------------

    def has_write_operation(self) -> bool:
        return self.benchmark_type in (BenchmarkType.WRITE, BenchmarkType.READ_WRITE)

    def has_read_operation(self) -> bool:
        return self.benchmark_type in (BenchmarkType.READ, BenchmarkType.READ_WRITE)


# ---------------------------------------------------------------------------
# Benchmark — top-level result container
# ---------------------------------------------------------------------------

@dataclass
class Benchmark:
    """A single top-level benchmark run.

    May contain one WRITE and/or one READ BenchmarkOperation.
    Equivalent to Benchmark.java.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    username: str = "anonymous"
    system_info: BenchmarkSystemInfo = field(default_factory=BenchmarkSystemInfo)
    drive_info: BenchmarkDriveInfo = field(default_factory=BenchmarkDriveInfo)
    config: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    # list[BenchmarkOperation] — typed loosely to avoid circular import
    operations: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def record_start_time(self) -> None:
        self.start_time = datetime.now()

    def record_end_time(self) -> None:
        self.end_time = datetime.now()

    def get_duration_ms(self) -> Optional[int]:
        if self.start_time is None or self.end_time is None:
            return None
        return int((self.end_time - self.start_time).total_seconds() * 1_000)

    def get_start_time_string(self) -> str:
        if self.start_time is None:
            return ""
        return self.start_time.strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Operation accessors
    # ------------------------------------------------------------------

    def get_operation(self, mode: IOMode) -> Optional["BenchmarkOperation"]:
        """Return the first operation matching *mode*, or None."""
        for op in self.operations:
            if op.io_mode == mode:
                return op
        return None

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def get_drive_info_display(self) -> str:
        return (
            f"{self.drive_info.drive_model} - "
            f"{self.drive_info.partition_id}: "
            f"{self.drive_info.get_usage_title_display()}"
        )

    def to_result_string(self, version: str = "0.1.0") -> str:
        """Human-readable result summary (mirrors Benchmark.toResultString())."""
        profile_name = (
            self.config.profile.display_name
            if self.config.profile is not None
            else "custom"
        )
        lines = [
            "",
            "-------------------------------------------",
            f"JDiskMark Benchmark Results (v{version})",
            "-------------------------------------------",
            f"Profile: {profile_name}",
            f"Benchmark: {self.config.benchmark_type.value}",
            f"Drive: {self.drive_info.drive_model}",
            f"Capacity: {self.drive_info.get_usage_title_display()}",
            f"Timestamp: {self.start_time}",
            f"CPU: {self.system_info.processor_name}",
            f"System: {self.system_info.os} / {self.system_info.arch}",
            f"Runtime: {self.system_info.runtime}",
            f"Path: {self.system_info.location_dir}",
        ]
        for op in self.operations:
            lines += [
                "-------------------------------------------",
                f"Order: {op.block_order.value}",
                f"IOMode: {op.io_mode.value}",
                f"Thread(s): {op.num_threads}",
                f"Blocks(size): {op.num_blocks}({op.block_size})",
                f"Samples: {op.num_samples}",
                f"TxSize(KB): {op.tx_size}",
                f"Speed(MB/s): {op.bw_avg:.2f}",
                f"SpeedMin(MB/s): {op.bw_min:.2f}",
                f"SpeedMax(MB/s): {op.bw_max:.2f}",
                f"Latency(ms): {op.acc_avg:.2f}",
                f"IOPS: {op.iops}",
            ]
        lines.append("-------------------------------------------")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"Benchmark(type={self.config.benchmark_type.value!r}, "
            f"start={self.start_time}, ops={len(self.operations)})"
        )
