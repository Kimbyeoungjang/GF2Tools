from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def _load_packager():
    spec = importlib.util.spec_from_file_location("gfl2_packager", ROOT / "tools/package_release.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_archive_is_overlay_root_and_contains_source_integrity_manifest(tmp_path):
    packager = _load_packager()
    version = packager.package_version()
    archive, _, _ = packager.package(version, tmp_path)

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "pyproject.toml" in names
        assert "bootstrap.py" in names
        assert "src/gfl2tool/_version.py" in names
        assert "release-source.json" in names
        assert not any(name.startswith(f"gfl2-tools-rebuild-v{version}/") for name in names)
        payload = json.loads(zf.read("release-source.json"))
        assert payload["schema"] == 2
        assert payload["version"] == version
        assert "remove" not in payload
        for rel in ("bootstrap.py", "pyproject.toml", "src/gfl2tool/_version.py", "src/gfl2tool/__init__.py"):
            expected = payload["files"][rel]
            actual = hashlib.sha256(zf.read(rel)).hexdigest()
            assert actual == expected


def test_overlaying_release_replaces_tracked_source_identity(tmp_path):
    packager = _load_packager()
    version = packager.package_version()
    archive, _, _ = packager.package(version, tmp_path / "release")

    target = tmp_path / "existing-project"
    package_dir = target / "src" / "gfl2tool"
    package_dir.mkdir(parents=True)
    (target / "pyproject.toml").write_text('[project]\nname="gfl2-tools"\nversion="0.51.0"\n', encoding="utf-8")
    (package_dir / "__init__.py").write_text('__version__ = "0.40.0"\n', encoding="utf-8")
    (package_dir / "old_only.py").write_text("OLD = True\n", encoding="utf-8")

    # A normal overlay replaces tracked files. Removed stale modules are detected
    # separately by the release source integrity check instead of being silently used.
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)

    assert (package_dir / "_version.py").is_file()
    assert f'__version__ = "{version}"' in (package_dir / "_version.py").read_text(encoding="utf-8")
    assert "from ._version import __version__" in (package_dir / "__init__.py").read_text(encoding="utf-8")
    assert (target / "release-source.json").is_file()


def test_release_package_excludes_build_metadata_and_test_artifacts(tmp_path):
    packager = _load_packager()
    version = packager.package_version()

    fake_egg = ROOT / "src" / "temporary-test.egg-info"
    fake_egg.mkdir(exist_ok=True)
    (fake_egg / "PKG-INFO").write_text("temporary", encoding="utf-8")
    try:
        archive, _, _ = packager.package(version, tmp_path)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        assert not any(".egg-info/" in name or ".dist-info/" in name for name in names)
        assert ".coverage" not in names
        assert "coverage.xml" not in names
        assert not any(name.startswith("data/") for name in names)
    finally:
        for child in fake_egg.iterdir():
            child.unlink()
        fake_egg.rmdir()


def test_release_audit_enforces_single_version_literal():
    packager = _load_packager()
    report = packager.audit(packager.package_version())
    assert report["version_literal_duplicates"] == []
    assert report["failures"] == []


def test_release_zip_timestamp_is_reproducible_but_changes_between_versions():
    packager = _load_packager()
    current = packager.package_version()
    assert packager._release_zip_datetime(current) == packager._release_zip_datetime(current)
    assert packager._release_zip_datetime(current) != packager._release_zip_datetime("0.52.0")
