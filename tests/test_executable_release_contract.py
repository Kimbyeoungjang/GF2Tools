from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_project_declares_gplv3_and_standard_license_file():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["license"] == "GPL-3.0-only"
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 29 June 2007" in license_text


def test_build_release_targets_executable_builder_not_source_packager():
    batch = (ROOT / "build_release.bat").read_text(encoding="utf-8")
    assert "tools\\build_executable.py" in batch
    assert "tools\\package_release.py" not in batch
    assert "GF2Tools.exe" in batch
    assert ".gfl2_build" in batch


def test_executable_builder_uses_onedir_and_emits_binary_and_source_artifacts():
    source = (ROOT / "tools/build_executable.py").read_text(encoding="utf-8")
    assert '"--onedir"' in source
    assert '"--name", "GF2Tools"' in source
    assert '"GF2ToolsUpdater"' in source
    assert "release-binary.json" in source
    assert "-win64.zip" in source
    assert "package_release.package(version, source_dir)" in source


def test_frozen_runtime_uses_executable_directory_as_install_root():
    source = (ROOT / "src/gfl2tool/runtime_paths.py").read_text(encoding="utf-8")
    assert 'getattr(sys, "frozen", False)' in source
    assert "Path(sys.executable).resolve().parent" in source


def test_release_test_runner_avoids_pytest_assertion_rewrite_on_windows_builds():
    source = (ROOT / "tools/package_release.py").read_text(encoding="utf-8")
    assert '"--assert=plain"' in source
    assert 'PYTEST_DISABLE_PLUGIN_AUTOLOAD' in source



def test_windows_executable_build_does_not_invoke_pytest():
    source = (ROOT / "tools/build_executable.py").read_text(encoding="utf-8")
    assert "package_release.run_build_checks()" in source
    assert "package_release.run_tests()" not in source


def test_build_optional_dependencies_do_not_require_pytest():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    build = project["optional-dependencies"]["build"]
    assert any(item.startswith("pyinstaller") for item in build)
    assert not any(item.startswith("pytest") for item in build)
