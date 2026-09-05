# Third-party software notices

This file summarizes the principal third-party software used by GFL2 Tools 1.0.4. It is an engineering checklist, not legal advice. Always review the exact license texts for the versions you distribute.

## Distribution model

The official GFL2 Tools **source Release ZIP does not bundle** Python, Qt/PySide6, Pillow, or Tesseract binaries. `bootstrap.py` creates a project-local Python environment and installs Python packages, while OCR setup locates or installs Tesseract separately. If a future release bundles those binaries, include the corresponding license texts/notices with that binary distribution and re-check all obligations.

## PySide6-Essentials / Qt for Python / Qt

- Project: Qt for Python / PySide6
- Upstream: https://doc.qt.io/qtforpython-6/
- Licensing: LGPLv3 / GPLv3 / Qt commercial license, depending on the chosen distribution terms and Qt components.
- GFL2 Tools uses `PySide6-Essentials` rather than Qt WebEngine or PySide6-Addons.
- If redistributing Qt/PySide binaries, review the LGPLv3/GPLv3 obligations and the license files shipped with the exact wheel/version.

## Pillow

- Project: Pillow
- Upstream: https://github.com/python-pillow/Pillow
- Current project metadata/license text: MIT-CMU
- License file: https://github.com/python-pillow/Pillow/blob/main/LICENSE

## Python

- Project: CPython
- Upstream: https://www.python.org/
- License: Python Software Foundation License Version 2, with additional notices for incorporated software.
- GFL2 Tools supports Python 3.11~3.13.

## Tesseract OCR

- Project: Tesseract OCR
- Upstream: https://github.com/tesseract-ocr/tesseract
- License: Apache License 2.0
- Korean/English OCR language data may be downloaded separately; review the license/notice files from the exact tessdata source used by the release.

## Noto Sans KR

- Project: Noto Sans KR / Noto Sans CJK Korean subset
- Upstream: https://github.com/notofonts/noto-cjk
- Version used by the Windows release builder: Noto Sans CJK 2.004
- License: SIL Open Font License 1.1 (OFL-1.1)
- The source Release ZIP does not vendor the font binary. `build_release.bat` downloads the pinned upstream TTF and PyInstaller embeds it into the Windows executable bundle so tactic-sheet rendering is independent of fonts installed on the user's PC.
- License copy: `licenses/NotoSansKR-OFL-1.1.txt` in source releases and `THIRD_PARTY_LICENSES/NotoSansKR-OFL-1.1.txt` in executable releases.

## Python packaging tools

The project-local runtime may use `pip`, `setuptools`, and `wheel` while preparing dependencies. They are not included in the GF2 Tools source Release ZIP as project code. Their own licenses apply to copies installed into the local runtime.

## Game data and images

Game names, characters, artwork, images, text, and original game data are not third-party software dependencies. Rights remain with their respective owners. GF2 Tools' program-data packages should only redistribute assets/data when the distributor has the necessary rights or permission. This project notice does not grant any license to game content.
