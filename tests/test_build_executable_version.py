from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_executable  # noqa: E402


def test_windows_numeric_version_accepts_release_candidate_suffix():
    assert build_executable._windows_numeric_version("1.0.4-RC0") == (1, 0, 4, 0)
    assert build_executable._windows_numeric_version("1.0.4-RC12") == (1, 0, 4, 0)


def test_windows_numeric_version_accepts_pep440_prerelease_suffix():
    assert build_executable._windows_numeric_version("1.0.4rc0") == (1, 0, 4, 0)
    assert build_executable._windows_numeric_version("1.0.4b2") == (1, 0, 4, 0)


def test_windows_numeric_version_preserves_four_numeric_components():
    assert build_executable._windows_numeric_version("1.2.3.45") == (1, 2, 3, 45)


def test_version_file_keeps_full_display_version(tmp_path):
    target = build_executable._version_file("1.0.4-RC0", tmp_path / "version_info.txt")
    text = target.read_text(encoding="utf-8")
    assert "filevers=(1,0,4,0)" in text
    assert "prodvers=(1,0,4,0)" in text
    assert "StringStruct('FileVersion', '1.0.4-RC0')" in text
    assert "StringStruct('ProductVersion', '1.0.4-RC0')" in text
