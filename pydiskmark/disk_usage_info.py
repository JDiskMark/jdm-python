"""Disk usage information collected before a benchmark run."""
from __future__ import annotations


class DiskUsageInfo:
    """Snapshot of disk capacity at benchmark start time."""

    def __init__(
        self,
        percent_used: float = 0.0,
        free_gb: float = 0.0,
        used_gb: float = 0.0,
        total_gb: float = 0.0,
    ) -> None:
        self.percent_used: int = round(percent_used)
        self.free_gb: float = free_gb
        self.used_gb: float = used_gb
        self.total_gb: float = total_gb

    def calc_percentage_used(self) -> int:
        if self.total_gb:
            self.percent_used = round(100 * self.used_gb / self.total_gb)
        return self.percent_used

    def get_usage_title_display(self) -> str:
        return f"{self.percent_used}% ({self.used_gb:.0f}/{self.total_gb:.0f} GB)"

    def to_display_string(self) -> str:
        return f"{self.percent_used}% {self.used_gb:.0f}/{self.total_gb:.0f} GB"

    def __repr__(self) -> str:
        return (
            f"DiskUsageInfo(percent_used={self.percent_used}, "
            f"used_gb={self.used_gb:.1f}, total_gb={self.total_gb:.1f})"
        )
