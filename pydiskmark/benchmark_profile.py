"""Pre-defined benchmark profiles.

Maps to BenchmarkProfile.java in jdm-java.
Each profile is a named set of configuration parameters.  CLI users can
override individual parameters without changing the profile.
"""
from __future__ import annotations

from enum import Enum

from .benchmark import BenchmarkType, BlockSequence, IoEngine, SectorAlignment


class BenchmarkProfile(Enum):
    """Named, pre-defined sets of benchmark configuration parameters.

    Enum values are tuples of constructor arguments; the __new__ method
    unpacks them into named attributes so callers can write:

        profile.display_name  -> "Quick Test"
        profile.num_samples   -> 50
    """

    # symbol (Python .name)  display_name                  type              order            T    n    blk  KB    engine         direct  sync   align                 mf
    QUICK_TEST            = ("Quick Test",            BenchmarkType.READ_WRITE,  BlockSequence.SEQUENTIAL,  1,   50,  32,   1024,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  False)
    MAX_THROUGHPUT        = ("Max Throughput",         BenchmarkType.READ_WRITE,  BlockSequence.SEQUENTIAL,  1,  100, 256,   1024,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  False)
    HIGH_LOAD_RANDOM_T32  = ("Random 4K (T32)",       BenchmarkType.READ_WRITE,  BlockSequence.RANDOM,     32,  200, 128,      4,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  True)
    LOW_LOAD_RANDOM_T1    = ("Random 4K (T1)",        BenchmarkType.READ_WRITE,  BlockSequence.RANDOM,      1,  150,  64,      4,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  False)
    MAX_WRITE_STRESS      = ("Max Write Stress (T4)", BenchmarkType.WRITE,        BlockSequence.SEQUENTIAL,  4,  250, 512,    512,  IoEngine.MODERN,  True,  True,  SectorAlignment.ALIGN_4K,  True)
    MEDIA_PLAYBACK        = ("Media Playback",        BenchmarkType.READ,         BlockSequence.SEQUENTIAL,  1,  160,  64,   2048,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  False)
    VIDEO_EXPORTING       = ("Video Exporting",       BenchmarkType.WRITE,        BlockSequence.SEQUENTIAL,  4,  500, 128,   1024,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  False)
    PHOTO_LIBRARY         = ("Photo Library",         BenchmarkType.READ,         BlockSequence.RANDOM,      8, 1000,   8,    128,  IoEngine.MODERN,  True,  False, SectorAlignment.ALIGN_4K,  True)

    # ------------------------------------------------------------------
    # Enum machinery — unpack the tuple into named attributes
    # ------------------------------------------------------------------

    def __new__(
        cls,
        display_name: str,
        benchmark_type: BenchmarkType,
        block_sequence: BlockSequence,
        num_threads: int,
        num_samples: int,
        num_blocks: int,
        block_size_kb: int,
        io_engine: IoEngine,
        direct_enable: bool,
        write_sync_enable: bool,
        sector_alignment: SectorAlignment,
        multi_file: bool,
    ) -> "BenchmarkProfile":
        obj = object.__new__(cls)
        # Python's Enum requires _value_ to be set in __new__
        obj._value_ = display_name
        obj.display_name = display_name
        obj.benchmark_type = benchmark_type
        obj.block_sequence = block_sequence
        obj.num_threads = num_threads
        obj.num_samples = num_samples
        obj.num_blocks = num_blocks
        obj.block_size_kb = block_size_kb
        obj.io_engine = io_engine
        obj.direct_enable = direct_enable
        obj.write_sync_enable = write_sync_enable
        obj.sector_alignment = sector_alignment
        obj.multi_file = multi_file
        return obj

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def symbol(self) -> str:
        """The enum name (e.g. 'QUICK_TEST') — mirrors Java's getSymbol()."""
        return self.name

    @classmethod
    def get_defaults(cls) -> list["BenchmarkProfile"]:
        """All profiles in display order."""
        return [
            cls.QUICK_TEST,
            cls.MAX_THROUGHPUT,
            cls.HIGH_LOAD_RANDOM_T32,
            cls.LOW_LOAD_RANDOM_T1,
            cls.MAX_WRITE_STRESS,
            cls.MEDIA_PLAYBACK,
            cls.VIDEO_EXPORTING,
            cls.PHOTO_LIBRARY,
        ]

    @classmethod
    def from_symbol(cls, symbol: str) -> "BenchmarkProfile":
        """Look up a profile by its enum name (case-insensitive)."""
        upper = symbol.upper()
        for member in cls:
            if member.name == upper:
                return member
        raise ValueError(f"Unknown profile symbol: {symbol!r}")

    def __str__(self) -> str:
        return self.display_name

    def __repr__(self) -> str:
        return f"BenchmarkProfile.{self.name}"
