"""Windowless update entries, including rollback to an older application."""
import os
from pathlib import Path


def background_flags():
    # CREATE_NEW_PROCESS_GROUP alone creates a visible console when the parent
    # has none. CREATE_NO_WINDOW also prevents console-close events reaching us.
    return (0x00000200 | 0x08000000) if os.name == 'nt' else 0


def interpreter_for(root):
    root = Path(root)
    if os.name != 'nt':
        return root / 'runtime/bin/python3'
    windowless = root / 'runtime/pythonw.exe'
    # Old/custom packets may lack pythonw. Every caller also suppresses console
    # creation, so fallback retains diagnostics without creating a terminal.
    return windowless if windowless.is_file() else root / 'runtime/python.exe'
