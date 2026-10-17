"""Launcher (also used as the PyInstaller entry point)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from vidshrink.app import main  # noqa: E402

if __name__ == "__main__":
    main()
