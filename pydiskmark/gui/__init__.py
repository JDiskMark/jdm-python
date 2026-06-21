"""pydiskmark GUI package — desktop interface for pydiskmark.

Launch with:
    python -m pydiskmark gui
"""
from __future__ import annotations


def launch_gui() -> None:
    """Create and run the pydiskmark desktop GUI."""
    import pydiskmark.app as app

    # Signal GUI mode before init
    app.mode = app.Mode.GUI
    app.init()

    from .main_window import MainWindow

    window = MainWindow()
    window.run()
