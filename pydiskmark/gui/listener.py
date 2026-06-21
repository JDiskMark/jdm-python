"""GuiListener — thread-safe BenchmarkListener for the GUI.

Posts events to a queue.Queue so the Tkinter main loop can process
them safely via root.after() polling.  Mirrors the CliListener in
cli.py but targets a GUI event loop instead of stderr.
"""
from __future__ import annotations

import queue
import threading
from typing import Any

from ..sample import Sample


# Event type constants
EVT_SAMPLE = "sample"
EVT_PROGRESS = "progress"
EVT_COMPLETE = "complete"
EVT_ERROR = "error"


class GuiListener:
    """BenchmarkListener implementation that posts to a thread-safe queue.

    The Tkinter main loop drains this queue every ~50ms via
    MainWindow._poll_queue().
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, Any, ...]] = queue.Queue()
        self._cancelled = threading.Event()

    # --- BenchmarkListener protocol ---

    def on_sample_complete(self, sample: Sample) -> None:
        """Post a sample event to the queue (called from worker thread)."""
        self._queue.put((EVT_SAMPLE, sample))

    def on_progress_update(self, completed: int, total: int) -> None:
        """Post a progress event to the queue (called from worker thread)."""
        self._queue.put((EVT_PROGRESS, completed, total))

    def is_cancelled(self) -> bool:
        """Check if the user has requested cancellation."""
        return self._cancelled.is_set()

    # --- Control methods ---

    def cancel(self) -> None:
        """Signal the benchmark to stop."""
        self._cancelled.set()

    def reset(self) -> None:
        """Reset state for a new benchmark run."""
        self._cancelled.clear()
        # Drain any stale events
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def drain(self) -> list[tuple]:
        """Drain all pending events from the queue. Non-blocking."""
        events: list[tuple] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events
