"""ChartPanel — real-time dual-axis matplotlib chart for pydiskmark.

Embeds a matplotlib Figure inside a Tkinter frame via FigureCanvasTkAgg.
Mirrors the JFreeChart setup in jdm-java's Gui.createChartPanel():
  - Left Y-axis: Bandwidth (MB/s)
  - Right Y-axis: Latency (ms)
  - X-axis: Sample number
  - Series: Write BW, Write Avg, Read BW, Read Avg, Write Lat, Read Lat
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional

import matplotlib
matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from ..benchmark import IOMode
from ..sample import Sample
from . import theme


class ChartPanel(tk.Frame):
    """Matplotlib chart embedded in a Tkinter Frame."""

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, **kwargs)

        # Data lists — parallel arrays keyed by sample_num
        self._w_x: list[int] = []
        self._w_bw: list[float] = []
        self._w_avg: list[float] = []
        self._w_lat: list[float] = []

        self._r_x: list[int] = []
        self._r_bw: list[float] = []
        self._r_avg: list[float] = []
        self._r_lat: list[float] = []

        # Create figure and axes
        self._fig = Figure(figsize=(7, 4), dpi=100)
        self._ax_bw = self._fig.add_subplot(111)
        self._ax_lat: Optional[matplotlib.axes.Axes] = None

        # Canvas
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Batch counter — redraw every N samples to avoid lag at high sample counts
        self._pending_updates = 0
        self._REDRAW_INTERVAL = 3  # redraw every 3rd sample (or on force)

        self._apply_style()

    # ------------------------------------------------------------------
    # Chart title
    # ------------------------------------------------------------------

    def set_title(self, title: str) -> None:
        """Update the chart title (shown above the plot area)."""
        self._fig.suptitle(title, fontsize=9, color=theme.get_chart_style()["text"])
        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _apply_style(self) -> None:
        """Apply chart styling based on current theme."""
        style = theme.get_chart_style()

        self._fig.set_facecolor(style["bg"])
        self._ax_bw.set_facecolor(style["plot_bg"])

        # Left axis — Bandwidth
        self._ax_bw.set_ylabel("Bandwidth (MB/s)", color=style["text"], fontsize=10)
        self._ax_bw.set_xlabel("Sample", color=style["text"], fontsize=10)
        self._ax_bw.tick_params(colors=style["text"], labelsize=8)
        self._ax_bw.grid(True, color=style["grid"], alpha=0.3, linestyle="--")

        # Right axis — Latency
        if self._ax_lat is None:
            self._ax_lat = self._ax_bw.twinx()
        self._ax_lat.set_ylabel("Latency (ms)", color=style["text"], fontsize=10)
        self._ax_lat.tick_params(colors=style["text"], labelsize=8)

        # Spine colors
        for spine in self._ax_bw.spines.values():
            spine.set_color(style["grid"])
        for spine in self._ax_lat.spines.values():
            spine.set_color(style["grid"])

        self._fig.tight_layout(pad=1.5)

    def retheme(self) -> None:
        """Re-apply style after a theme toggle, then redraw everything."""
        self._apply_style()
        self._redraw_all()

    # ------------------------------------------------------------------
    # Data management
    # ------------------------------------------------------------------

    def add_sample(self, sample: Sample) -> None:
        """Append a sample data point and schedule a redraw."""
        if sample.type_ == IOMode.WRITE:
            self._w_x.append(sample.sample_num)
            self._w_bw.append(sample.bw_mb_sec)
            self._w_avg.append(sample.cum_avg)
            self._w_lat.append(sample.access_time_ms)
        else:
            self._r_x.append(sample.sample_num)
            self._r_bw.append(sample.bw_mb_sec)
            self._r_avg.append(sample.cum_avg)
            self._r_lat.append(sample.access_time_ms)

        self._pending_updates += 1
        if self._pending_updates >= self._REDRAW_INTERVAL:
            self._redraw_all()
            self._pending_updates = 0

    def flush(self) -> None:
        """Force a redraw of any pending updates."""
        if self._pending_updates > 0:
            self._redraw_all()
            self._pending_updates = 0

    def clear(self) -> None:
        """Clear all data and redraw an empty chart."""
        self._w_x.clear()
        self._w_bw.clear()
        self._w_avg.clear()
        self._w_lat.clear()
        self._r_x.clear()
        self._r_bw.clear()
        self._r_avg.clear()
        self._r_lat.clear()
        self._pending_updates = 0
        self._redraw_all()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _redraw_all(self) -> None:
        """Clear axes and replot all data. Called from the main thread."""
        style = theme.get_chart_style()

        self._ax_bw.clear()
        if self._ax_lat is not None:
            self._ax_lat.clear()
        else:
            self._ax_lat = self._ax_bw.twinx()

        # Re-apply axis labels and style after clear
        self._ax_bw.set_ylabel("Bandwidth (MB/s)", color=style["text"], fontsize=10)
        self._ax_bw.set_xlabel("Sample", color=style["text"], fontsize=10)
        self._ax_bw.tick_params(colors=style["text"], labelsize=8)
        self._ax_bw.grid(True, color=style["grid"], alpha=0.3, linestyle="--")
        self._ax_bw.set_facecolor(style["plot_bg"])

        self._ax_lat.set_ylabel("Latency (ms)", color=style["text"], fontsize=10)
        self._ax_lat.tick_params(colors=style["text"], labelsize=8)

        # Plot write series
        if self._w_x:
            self._ax_bw.plot(
                self._w_x, self._w_bw,
                color=theme.WRITE_COLOR, linewidth=1.2,
                label="Write", alpha=0.9,
            )
            self._ax_bw.plot(
                self._w_x, self._w_avg,
                color=theme.WRITE_AVG_COLOR, linewidth=1.0,
                linestyle="--", label="Write Avg", alpha=0.7,
            )
            self._ax_lat.scatter(
                self._w_x, self._w_lat,
                color=theme.WRITE_LAT_COLOR, s=8, marker="s",
                label="Write Lat", alpha=0.6, zorder=5,
            )

        # Plot read series
        if self._r_x:
            self._ax_bw.plot(
                self._r_x, self._r_bw,
                color=theme.READ_COLOR, linewidth=1.2,
                label="Read", alpha=0.9,
            )
            self._ax_bw.plot(
                self._r_x, self._r_avg,
                color=theme.READ_AVG_COLOR, linewidth=1.0,
                linestyle="--", label="Read Avg", alpha=0.7,
            )
            self._ax_lat.scatter(
                self._r_x, self._r_lat,
                color=theme.READ_LAT_COLOR, s=8, marker="s",
                label="Read Lat", alpha=0.6, zorder=5,
            )

        # Legend — combine handles from both axes
        handles_bw, labels_bw = self._ax_bw.get_legend_handles_labels()
        handles_lat, labels_lat = self._ax_lat.get_legend_handles_labels()
        all_handles = handles_bw + handles_lat
        all_labels = labels_bw + labels_lat
        if all_handles:
            self._ax_bw.legend(
                all_handles, all_labels,
                loc="upper left", fontsize=7,
                facecolor=style["bg"], edgecolor=style["grid"],
                labelcolor=style["text"], framealpha=0.8,
            )

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()
