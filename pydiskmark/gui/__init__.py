"""pydiskmark GUI package — desktop interface for pydiskmark.

Launch with:
    python -m pydiskmark gui
"""
from __future__ import annotations


def launch_gui() -> None:
    """Create and run the pydiskmark desktop GUI.

    Mirrors App.init() / App.main() GUI branch in jdm-java:
      - Loads persisted config (via app.init → config.load_config)
      - Applies the saved theme before the window is constructed
      - Saves config on exit (mirrors Java's Runtime shutdown hook)
    """
    import pydiskmark.app as app

    # Signal GUI mode before init (config.load_config is called inside init)
    app.mode = app.Mode.GUI
    app.init()

    from .main_window import MainWindow

    window = MainWindow()
    try:
        window.run()
    finally:
        # Save config on window close — mirrors Java's Runtime.addShutdownHook
        from pydiskmark import config
        config.save_config()
