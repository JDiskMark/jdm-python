"""Entry point: python -m pydiskmark [run|gui]"""
import sys

from .cli import main


def _entry() -> None:
    """Route to GUI if 'gui' subcommand is given, otherwise use CLI."""
    # If invoked with 'gui' subcommand, launch the desktop GUI
    args = sys.argv[1:]
    if args and args[0] == "gui":
        from .gui import launch_gui
        launch_gui()
    else:
        main()


if __name__ == "__main__":
    _entry()
