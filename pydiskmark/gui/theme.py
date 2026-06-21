"""Theme configuration for the pydiskmark GUI.

Uses sv_ttk (Sun Valley theme) for modern dark/light appearance.
Provides chart color constants matching jdm-java's Gui.Palette.CLASSIC.
"""
from __future__ import annotations

import sv_ttk


# ---------------------------------------------------------------------------
# Theme management
# ---------------------------------------------------------------------------

def apply_dark_theme() -> None:
    """Apply the Sun Valley dark theme."""
    sv_ttk.set_theme("dark")


def apply_light_theme() -> None:
    """Apply the Sun Valley light theme."""
    sv_ttk.set_theme("light")


def toggle_theme() -> None:
    """Toggle between dark and light themes."""
    sv_ttk.toggle_theme()


def is_dark() -> bool:
    """Return True if the current theme is dark."""
    return sv_ttk.get_theme() == "dark"


# ---------------------------------------------------------------------------
# Chart colors — mirrors jdm-java Gui.Palette.CLASSIC
# ---------------------------------------------------------------------------

# Dark theme background for the matplotlib chart area
CHART_BG_DARK = "#2b2b2b"
CHART_BG_LIGHT = "#f5f5f5"
CHART_PLOT_BG_DARK = "#1e1e1e"
CHART_PLOT_BG_LIGHT = "#ffffff"
CHART_TEXT_DARK = "#cccccc"
CHART_TEXT_LIGHT = "#333333"
CHART_GRID_DARK = "#404040"
CHART_GRID_LIGHT = "#dddddd"

# Series colors
WRITE_COLOR = "#ff8c00"       # warm orange
WRITE_AVG_COLOR = "#ffb347"   # lighter orange (dashed)
WRITE_LAT_COLOR = "#ff6600"   # darker orange (latency dots)
READ_COLOR = "#00bfff"        # cyan
READ_AVG_COLOR = "#7fdbff"    # lighter cyan (dashed)
READ_LAT_COLOR = "#0099cc"    # darker cyan (latency dots)


def get_chart_style() -> dict:
    """Return a dict of chart style values based on the current theme."""
    dark = is_dark()
    return {
        "bg": CHART_BG_DARK if dark else CHART_BG_LIGHT,
        "plot_bg": CHART_PLOT_BG_DARK if dark else CHART_PLOT_BG_LIGHT,
        "text": CHART_TEXT_DARK if dark else CHART_TEXT_LIGHT,
        "grid": CHART_GRID_DARK if dark else CHART_GRID_LIGHT,
    }
