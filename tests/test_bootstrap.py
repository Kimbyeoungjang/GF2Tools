from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import tomllib

import gfl2tool


ROOT = Path(__file__).resolve().parents[1]


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("gfl2_bootstrap", ROOT / "bootstrap.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_version_matches_single_source_and_dynamic_pyproject():
    bootstrap = _load_bootstrap()
    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)
    assert bootstrap.APP_VERSION == gfl2tool.__version__
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "gfl2tool._version.__version__"


def test_bootstrap_runtime_prefix_changes_for_pyside6_dependency_generation():
    bootstrap = _load_bootstrap()
    # The current runtime generation must include the image dependencies used by the Qt UI.
    assert bootstrap.RUNTIME_PREFIX == "env-v400"
    assert "PySide6" in inspect.getsource(bootstrap.verify_core)
    dependencies = bootstrap.project_requirements()
    selected = bootstrap.select_requirements(dependencies, ("pyside6-essentials",))
    assert selected and selected[0].startswith("PySide6-Essentials")



def test_bootstrap_is_qt_only():
    bootstrap = _load_bootstrap()
    verify_source = inspect.getsource(bootstrap.verify_core)
    install_source = inspect.getsource(bootstrap.install_core)
    main_source = inspect.getsource(bootstrap.main)
    assert "PySide6" in verify_source
    assert "select_requirements" in install_source
    assert "require_qt" not in verify_source
    assert "legacy_gui" not in main_source
    assert "--legacy-gui" not in (ROOT / "bootstrap.py").read_text(encoding="utf-8")


def test_windows_batch_wrappers_do_not_duplicate_application_version():
    semver = re.compile(r"v?\d+\.\d+\.\d+")
    for name in ("start_gfl2_tools.bat", "repair_ocr.bat", "reset_environment.bat", "build_release.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not semver.search(text), name


def test_bootstrap_has_no_stale_runtime_constants():
    targets = [
        ROOT / "bootstrap.py",
        ROOT / "start_gfl2_tools.bat",
        ROOT / "repair_ocr.bat",
        ROOT / "reset_environment.bat",
    ]
    stale = re.compile(r"0\\.21\\.0|env-v210")
    for target in targets:
        assert not stale.search(target.read_text(encoding="utf-8")), target.name


def test_bootstrap_does_not_verify_core_twice_on_normal_launch():
    bootstrap = _load_bootstrap()
    source = inspect.getsource(bootstrap.main)
    tail = source.split("ensure_runtime()", 1)[1]
    assert "if env_dir is None or not verify_core" not in tail
    assert "if not verify_core" not in tail


def test_bootstrap_reads_app_version_from_single_version_module():
    bootstrap = _load_bootstrap()
    source = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert "APP_VERSION = _source_version()" in source
    assert "_project_version" not in source
    assert bootstrap._source_version() == bootstrap.APP_VERSION
    assert bootstrap.source_tree_consistent()




def test_launch_critical_dependency_failure_is_reported_by_name(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    monkeypatch.setattr(bootstrap, "runtime_python", lambda _env: fake_python)

    class Result:
        returncode = 0

    monkeypatch.setattr(bootstrap, "run", lambda *args, **kwargs: Result())
    probe = {
        "gfl2tool": {"ok": True, "version": bootstrap.APP_VERSION, "path": "src", "error": ""},
        "PySide6": {"ok": False, "version": "", "path": "", "error": "ImportError: DLL load failed"},
        "Pillow": {"ok": True, "version": "12", "path": "PIL", "error": ""},
    }
    monkeypatch.setattr(bootstrap, "_runtime_probe", lambda _env, _names=None: probe)
    messages = []
    monkeypatch.setattr(bootstrap, "log", messages.append)

    assert bootstrap.install_core(tmp_path) is False
    assert any("PySide6: ImportError: DLL load failed" in message for message in messages)


def test_unregistered_runtime_can_be_recovered_without_reinstall(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    runtime_root = tmp_path / ".gfl2_runtime"
    runtime_root.mkdir()
    candidate = runtime_root / "env-v400-reusable"
    candidate.mkdir()
    fake_python = candidate / "python.exe"
    fake_python.write_bytes(b"")

    monkeypatch.setattr(bootstrap, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(bootstrap, "CURRENT_FILE", runtime_root / "current.txt")
    monkeypatch.setattr(bootstrap, "runtime_python", lambda env: env / "python.exe")
    monkeypatch.setattr(bootstrap, "verify_core", lambda env: env == candidate)
    messages = []
    monkeypatch.setattr(bootstrap, "log", messages.append)

    recovered = bootstrap.try_reuse_runtime()
    assert recovered == candidate
    assert (runtime_root / "current.txt").read_text(encoding="utf-8") == candidate.name
    assert any("Recovered an already-installed" in message for message in messages)


def test_runtime_probe_explicitly_prefers_current_source_tree():
    bootstrap = _load_bootstrap()
    source = inspect.getsource(bootstrap._runtime_probe)
    assert "sys.path.insert(0" in source
    assert "GFL2_PROBE=" in source
    assert "core-verify.log" in (ROOT / "bootstrap.py").read_text(encoding="utf-8")


def test_release_source_manifest_detects_mixed_overlay_before_runtime_setup(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    package_dir = tmp_path / "src" / "gfl2tool"
    package_dir.mkdir(parents=True)
    version_file = package_dir / "_version.py"
    init_file = package_dir / "__init__.py"
    version_file.write_text(f'__version__ = "{bootstrap.APP_VERSION}"\n', encoding="utf-8")
    init_file.write_text("from ._version import __version__\n", encoding="utf-8")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = tmp_path / "release-source.json"
    manifest.write_text(
        json.dumps({
            "schema": 2,
            "version": bootstrap.APP_VERSION,
            "files": {
                "src/gfl2tool/_version.py": digest(version_file),
                "src/gfl2tool/__init__.py": digest(init_file),
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "VERSION_FILE", version_file)
    monkeypatch.setattr(bootstrap, "SOURCE_MANIFEST", manifest)
    messages = []
    monkeypatch.setattr(bootstrap, "log", messages.append)

    assert bootstrap.source_tree_consistent()
    init_file.write_text('from ._version import __version__\n# old mixed file\n', encoding="utf-8")
    assert not bootstrap.source_tree_consistent()
    assert any("outdated/modified: src/gfl2tool/__init__.py" in message for message in messages)




def test_bootstrap_dependency_versions_come_only_from_pyproject():
    bootstrap = _load_bootstrap()
    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    assert bootstrap.project_requirements() == project["dependencies"]
    source = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    for literal in ("protobuf>=5.29.4", "PySide6-Essentials>=6.8,<7", "UnityPy>=1.20,<2", "Pillow>=10", "mitmproxy==11.1.3"):
        assert literal not in source



def test_install_core_verifies_only_declared_core_runtime_dependencies():
    bootstrap = _load_bootstrap()
    import inspect

    source = inspect.getsource(bootstrap.install_core)
    assert '"protobuf"' not in source
    assert '"gfl2tool"' in source
    assert '"PySide6"' in source
    assert '"Pillow"' in source

def test_release_source_manifest_rejects_path_traversal(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    package_dir = tmp_path / "src" / "gfl2tool"
    package_dir.mkdir(parents=True)
    version_file = package_dir / "_version.py"
    version_file.write_text(f'__version__ = "{bootstrap.APP_VERSION}"\n', encoding="utf-8")
    manifest = tmp_path / "release-source.json"
    manifest.write_text(
        json.dumps({
            "schema": 2,
            "version": bootstrap.APP_VERSION,
            "files": {"../outside.py": "deadbeef"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "VERSION_FILE", version_file)
    monkeypatch.setattr(bootstrap, "SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "RUNTIME_ROOT", tmp_path / ".gfl2_runtime")
    monkeypatch.setattr(bootstrap, "log", lambda _message: None)

    assert not bootstrap.source_tree_consistent()


def test_release_bytecode_cleanup_repairs_same_timestamp_same_size_overlay(monkeypatch, tmp_path):
    import os
    import py_compile
    import subprocess
    import sys

    bootstrap = _load_bootstrap()
    package_dir = tmp_path / "src" / "gfl2tool"
    package_dir.mkdir(parents=True)
    init_file = package_dir / "__init__.py"
    version_file = package_dir / "_version.py"
    init_file.write_text("from ._version import __version__\n", encoding="utf-8")
    stale_version = "0.0.0-RC0" if "RC" in bootstrap.APP_VERSION.upper() else "0.9.9"
    version_file.write_text(f'__version__ = "{stale_version}"\n', encoding="utf-8")

    fixed_timestamp = 315532800
    os.utime(init_file, (fixed_timestamp, fixed_timestamp))
    os.utime(version_file, (fixed_timestamp, fixed_timestamp))
    py_compile.compile(str(init_file), doraise=True)
    py_compile.compile(str(version_file), doraise=True)

    # The new version has exactly the same source byte length and timestamp.
    # Timestamp-based pyc validation therefore reproduces the Windows overlay bug.
    version_file.write_text(f'__version__ = "{bootstrap.APP_VERSION}"\n', encoding="utf-8")
    os.utime(version_file, (fixed_timestamp, fixed_timestamp))
    probe = "import sys; sys.path.insert(0, {!r}); import gfl2tool; print(gfl2tool.__version__)".format(str(tmp_path / "src"))
    stale = subprocess.check_output([sys.executable, "-c", probe], text=True).strip()
    assert stale == stale_version

    manifest = tmp_path / "release-source.json"
    manifest.write_text("{}\n", encoding="utf-8")
    runtime_root = tmp_path / ".gfl2_runtime"
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(bootstrap, "log", lambda _message: None)

    assert bootstrap.prepare_source_bytecode()
    repaired = subprocess.check_output([sys.executable, "-c", probe], text=True).strip()
    assert repaired == bootstrap.APP_VERSION
    marker = json.loads((runtime_root / bootstrap.SOURCE_BYTECODE_MARKER).read_text(encoding="utf-8"))
    assert marker["version"] == bootstrap.APP_VERSION


def test_release_bytecode_cleanup_runs_only_when_manifest_generation_changes(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    package_dir = tmp_path / "src" / "gfl2tool"
    cache_dir = package_dir / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "stale.pyc").write_bytes(b"old")
    manifest = tmp_path / "release-source.json"
    manifest.write_text('{"version":"first"}\n', encoding="utf-8")
    runtime_root = tmp_path / ".gfl2_runtime"
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(bootstrap, "log", lambda _message: None)

    assert bootstrap.prepare_source_bytecode()
    assert not cache_dir.exists()

    cache_dir.mkdir(parents=True)
    same_release_cache = cache_dir / "current.pyc"
    same_release_cache.write_bytes(b"current")
    assert bootstrap.prepare_source_bytecode()
    assert same_release_cache.exists()

    manifest.write_text('{"version":"second"}\n', encoding="utf-8")
    assert bootstrap.prepare_source_bytecode()
    assert not cache_dir.exists()


def test_release_source_manifest_quarantines_untracked_package_files(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    package_dir = tmp_path / "src" / "gfl2tool"
    reference_dir = package_dir / "reference_data"
    reference_dir.mkdir(parents=True)
    version_file = package_dir / "_version.py"
    init_file = package_dir / "__init__.py"
    extra_py = package_dir / "retired_module.py"
    extra_json = reference_dir / "retired_rules.json"
    version_file.write_text(f'__version__ = "{bootstrap.APP_VERSION}"\n', encoding="utf-8")
    init_file.write_text("from ._version import __version__\n", encoding="utf-8")
    extra_py.write_text("OLD = True\n", encoding="utf-8")
    extra_json.write_text("{}\n", encoding="utf-8")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = tmp_path / "release-source.json"
    manifest.write_text(
        json.dumps({
            "schema": 2,
            "version": bootstrap.APP_VERSION,
            "files": {
                "src/gfl2tool/_version.py": digest(version_file),
                "src/gfl2tool/__init__.py": digest(init_file),
            },
        }),
        encoding="utf-8",
    )
    runtime_root = tmp_path / ".gfl2_runtime"
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "VERSION_FILE", version_file)
    monkeypatch.setattr(bootstrap, "SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(bootstrap, "log", lambda _message: None)

    assert bootstrap.source_tree_consistent()
    assert not extra_py.exists()
    assert not extra_json.exists()
    assert len(list((runtime_root / "stale-source").rglob("retired_module.py"))) == 1
    assert len(list((runtime_root / "stale-source").rglob("retired_rules.json"))) == 1


def test_release_manifest_owned_files_quarantine_stale_tests_docs_and_schemas(monkeypatch, tmp_path):
    bootstrap = _load_bootstrap()
    package_dir = tmp_path / "src" / "gfl2tool"
    package_dir.mkdir(parents=True)
    version_file = package_dir / "_version.py"
    version_file.write_text(f'__version__ = "{bootstrap.APP_VERSION}"\n', encoding="utf-8")
    keep_test = tmp_path / "tests" / "test_current.py"
    stale_test = tmp_path / "tests" / "test_retired.py"
    stale_doc = tmp_path / "docs" / "retired.md"
    stale_schema = tmp_path / "schemas" / "retired.proto"
    for path, text in (
        (keep_test, "def test_current(): pass\n"),
        (stale_test, "def test_retired(): pass\n"),
        (stale_doc, "retired\n"),
        (stale_schema, "syntax = \"proto3\";\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    manifest = tmp_path / "release-source.json"
    manifest.write_text(
        json.dumps({
            "schema": 2,
            "version": bootstrap.APP_VERSION,
            "files": {
                "src/gfl2tool/_version.py": hashlib.sha256(version_file.read_bytes()).hexdigest(),
            },
            "owned": ["src/gfl2tool/_version.py", "tests/test_current.py"],
        }),
        encoding="utf-8",
    )
    runtime_root = tmp_path / ".gfl2_runtime"
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "VERSION_FILE", version_file)
    monkeypatch.setattr(bootstrap, "SOURCE_MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(bootstrap, "log", lambda _message: None)

    assert bootstrap.source_tree_consistent()
    assert keep_test.is_file()
    for stale in (stale_test, stale_doc, stale_schema):
        assert not stale.exists()
        assert list((runtime_root / "stale-source").rglob(stale.name))


def test_bootstrap_ocr_auto_install_contract():
    text = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert "UB-Mannheim.TesseractOCR" in text
    assert "kor.traineddata" in text
    assert "eng.traineddata" in text
    assert "install_ocr(repair=False)" in text
    assert "--repair-ocr" in text
