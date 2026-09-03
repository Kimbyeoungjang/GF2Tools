from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from gfl2tool.services.app_update import ApplicationUpdater


def _release_zip(version: str = "1.0.1") -> bytes:
    files = {
        "bootstrap.py": b"print('bootstrap')\n",
        "start_gfl2_tools.bat": b"@echo off\r\n",
        "pyproject.toml": b"[project]\nname='gfl2-tools'\n",
        "src/gfl2tool/_version.py": f'__version__ = "{version}"\n'.encode(),
    }
    manifest = {
        "schema": 2,
        "version": version,
        "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()},
        "owned": ["src/gfl2tool/_version.py"],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
        archive.writestr("release-source.json", json.dumps(manifest).encode())
    return stream.getvalue()


def test_application_update_version_comparison_is_numeric():
    assert ApplicationUpdater.version_is_newer("1.0.1", "1.0.0") is True
    assert ApplicationUpdater.version_is_newer("1.10.0", "1.9.9") is True
    assert ApplicationUpdater.version_is_newer("1.0.0", "1.0.0") is False
    assert ApplicationUpdater.version_is_newer("0.99.9", "1.0.0") is False


def test_application_update_release_asset_prefers_program_package():
    release = {
        "assets": [
            {"name": "gfl2-gf2tools-offline-table.123.zip", "browser_download_url": "https://example/data.zip"},
            {"name": "gfl2-tools-rebuild-v1.0.1.zip", "browser_download_url": "https://example/app.zip"},
        ]
    }
    name, url = ApplicationUpdater._select_release_asset(release, "1.0.1")
    assert name == "gfl2-tools-rebuild-v1.0.1.zip"
    assert url.endswith("app.zip")


def test_application_update_release_package_validates_source_manifest():
    payload = _release_zip("1.0.1")
    version, digest = ApplicationUpdater.validate_release_package(payload, expected_version="1.0.1")
    assert version == "1.0.1"
    assert digest == hashlib.sha256(payload).hexdigest()


def test_application_update_rejects_version_mismatch():
    with pytest.raises(ValueError, match="Release 버전"):
        ApplicationUpdater.validate_release_package(_release_zip("1.0.2"), expected_version="1.0.1")


def test_application_update_rejects_tampered_tracked_file():
    payload = _release_zip("1.0.1")
    src = io.BytesIO(payload)
    out = io.BytesIO()
    with zipfile.ZipFile(src, "r") as original, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as modified:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "bootstrap.py":
                data = b"tampered\n"
            modified.writestr(item.filename, data)
    with pytest.raises(ValueError, match="무결성"):
        ApplicationUpdater.validate_release_package(out.getvalue(), expected_version="1.0.1")


def test_update_helper_has_no_project_data_overlay_path():
    helper = Path("tools/apply_program_update.py").read_text(encoding="utf-8")
    tree = ast.parse(helper)
    assert "PROTECTED_ROOTS" in {node.targets[0].id for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    assert '"data"' in helper
    assert '".gfl2_runtime"' in helper


def test_update_helper_applies_verified_overlay(tmp_path):
    import subprocess
    import sys

    root = tmp_path / "app"
    (root / "src/gfl2tool").mkdir(parents=True)
    (root / "tools").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "src/gfl2tool/_version.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (root / "bootstrap.py").write_text("old\n", encoding="utf-8")
    (root / "start_gfl2_tools.bat").write_text("@echo off\n", encoding="utf-8")
    (root / "data/user.txt").write_text("keep", encoding="utf-8")

    package = tmp_path / "update.zip"
    package.write_bytes(_release_zip("1.0.1"))
    helper = Path("tools/apply_program_update.py").resolve()
    subprocess.run(
        [
            sys.executable,
            str(helper),
            "--root", str(root),
            "--package", str(package),
            "--parent-pid", "0",
            "--expected-version", "1.0.1",
        ],
        check=True,
    )
    assert (root / "src/gfl2tool/_version.py").read_text(encoding="utf-8").strip().endswith('"1.0.1"')
    assert (root / "data/user.txt").read_text(encoding="utf-8") == "keep"


def _binary_release_zip(version: str = "1.0.1") -> bytes:
    files = {
        "GF2Tools.exe": b"new-main-exe",
        "GF2ToolsUpdater.exe": b"new-updater-exe",
        "LICENSE": b"GPLv3",
        "THIRD_PARTY_NOTICES.md": b"notices",
        "_internal/runtime.bin": b"runtime-v2",
        "ocr/engine/tesseract.exe": b"tesseract",
        "ocr/engine/tessdata/kor.traineddata": b"kor",
        "ocr/engine/tessdata/eng.traineddata": b"eng",
    }
    manifest = {
        "schema": 1,
        "kind": "gfl2-tools-windows-binary",
        "version": version,
        "architecture": "win64",
        "entrypoint": "GF2Tools.exe",
        "updater": "GF2ToolsUpdater.exe",
        "owned_roots": ["GF2Tools.exe", "GF2ToolsUpdater.exe", "_internal", "ocr", "LICENSE", "THIRD_PARTY_NOTICES.md", "release-binary.json"],
        "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()},
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
        archive.writestr("release-binary.json", json.dumps(manifest).encode())
    return stream.getvalue()


def test_application_update_release_asset_prefers_win64_binary():
    release = {
        "assets": [
            {"name": "gfl2-tools-v1.0.1-source.zip", "browser_download_url": "https://example/source.zip"},
            {"name": "GF2Tools-v1.0.1-win64.zip", "browser_download_url": "https://example/win64.zip"},
            {"name": "gfl2-gf2tools-offline-table.123.zip", "browser_download_url": "https://example/data.zip"},
        ]
    }
    name, url = ApplicationUpdater._select_release_asset(release, "1.0.1")
    assert name == "GF2Tools-v1.0.1-win64.zip"
    assert url.endswith("win64.zip")


def test_application_update_release_package_validates_binary_manifest():
    payload = _binary_release_zip("1.0.1")
    version, digest = ApplicationUpdater.validate_release_package(payload, expected_version="1.0.1")
    assert version == "1.0.1"
    assert digest == hashlib.sha256(payload).hexdigest()
    assert ApplicationUpdater._package_kind(payload) == "binary"


def test_update_helper_replaces_binary_bundle_and_preserves_data(tmp_path):
    import subprocess
    import sys

    root = tmp_path / "binary-app"
    (root / "_internal").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "GF2Tools.exe").write_bytes(b"old-main")
    (root / "GF2ToolsUpdater.exe").write_bytes(b"old-updater")
    (root / "LICENSE").write_text("old", encoding="utf-8")
    (root / "_internal/runtime.bin").write_bytes(b"runtime-v1")
    (root / "data/user.txt").write_text("keep", encoding="utf-8")
    (root / "release-binary.json").write_text(
        json.dumps({"schema": 1, "kind": "gfl2-tools-windows-binary", "version": "1.0.0", "owned_roots": ["GF2Tools.exe", "GF2ToolsUpdater.exe", "_internal", "LICENSE", "release-binary.json"]}),
        encoding="utf-8",
    )

    package = tmp_path / "binary-update.zip"
    package.write_bytes(_binary_release_zip("1.0.1"))
    helper = Path("tools/apply_program_update.py").resolve()
    subprocess.run(
        [
            sys.executable,
            str(helper),
            "--root", str(root),
            "--package", str(package),
            "--parent-pid", "0",
            "--expected-version", "1.0.1",
        ],
        check=True,
    )
    assert (root / "GF2Tools.exe").read_bytes() == b"new-main-exe"
    assert (root / "_internal/runtime.bin").read_bytes() == b"runtime-v2"
    assert (root / "data/user.txt").read_text(encoding="utf-8") == "keep"
    installed = json.loads((root / "release-binary.json").read_text(encoding="utf-8"))
    assert installed["version"] == "1.0.1"
