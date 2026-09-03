from __future__ import annotations

import os

from .qtgui import main as gui_main
from .runtime_paths import install_root


def main() -> None:
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    gui_main(root / "data" / "gfl2.db")


if __name__ == "__main__":
    main()
