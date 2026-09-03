from __future__ import annotations

from pathlib import Path
import sys


def install_root() -> Path:
    """Return the writable application installation root.

    Source checkouts use the repository root. PyInstaller builds use the
    directory that contains GF2Tools.exe rather than the temporary extraction
    directory used for bundled Python modules.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
