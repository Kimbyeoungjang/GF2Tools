# Third-party software notices

GFL2 Tools 1.0.0 project code is distributed under **GNU GPL v3 only**. This file summarizes the principal third-party software used by the official Windows executable distribution. It is an engineering notice, not legal advice; the exact license files bundled with a release remain authoritative.

## Binary distribution model

`build_release.bat` creates a PyInstaller **one-directory** Windows distribution. `GF2Tools.exe` is the user-facing entry point and dynamically loaded Qt/PySide DLLs remain separate inside `_internal/`. The release also contains `THIRD_PARTY_LICENSES/` and a GPLv3 Corresponding Source ZIP.

The official Windows executable bundle includes a project-local Tesseract engine plus Korean/English tessdata so OCR works without a separate system installation. Source/development runs may still use a separately installed or project-local Tesseract runtime.

## PySide6-Essentials / Qt for Python / Qt

- Project: Qt for Python / PySide6
- Upstream: https://doc.qt.io/qtforpython-6/
- Licensing offered upstream includes LGPLv3, GPLv3 and commercial terms depending on the component/distribution.
- GFL2 Tools uses `PySide6-Essentials` and distributes Qt/PySide runtime files as separate files in the one-directory bundle rather than statically linking them into one monolithic executable.
- The executable release includes the relevant license material collected from the installed wheel plus an LGPLv3 text in `THIRD_PARTY_LICENSES/`.

## Pillow

- Project: Pillow
- Upstream: https://github.com/python-pillow/Pillow
- License: MIT-CMU style license distributed by Pillow.
- The builder copies Pillow's license into `THIRD_PARTY_LICENSES/`.

## CPython

- Project: CPython
- Upstream: https://www.python.org/
- License: Python Software Foundation License Version 2 and incorporated-software notices.
- PyInstaller bundles the Python runtime used for the Windows build. The builder copies the Python installation's license file into `THIRD_PARTY_LICENSES/` when available.

## PyInstaller

- Project: PyInstaller
- Upstream: https://pyinstaller.org/
- PyInstaller is a build-time dependency; its bootloader becomes part of the generated executable under PyInstaller's licensing/bootloader exception terms.
- The builder collects license/notice files from the installed PyInstaller distribution into `THIRD_PARTY_LICENSES/`.

## Tesseract OCR

- Project: Tesseract OCR
- Upstream: https://github.com/tesseract-ocr/tesseract
- License: Apache License 2.0.
- The official Windows executable bundle includes the Tesseract engine and Korean/English tessdata. `THIRD_PARTY_LICENSES/` includes the Apache-2.0 text; any additional notices shipped by the selected Windows Tesseract distribution should also be preserved by the release builder.

## Game data and images

Game names, characters, artwork, images, text and original game data are not software dependencies covered by the GFL2 Tools GPLv3 license. Rights remain with their respective owners. GFL2 Tools' program-data packages should redistribute such assets/data only when the distributor has the necessary rights or permission.
