from __future__ import annotations

import argparse
import ast
import io
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import tokenize
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git", ".venv", ".gfl2_build", ".gfl2_runtime", ".gfl2_update", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "build", "dist", "release", "htmlcov", "data", "archive_tools",
}
EXCLUDED_FILES = {".coverage", "coverage.xml", "release-source.json"}
FORBIDDEN_ACTIVE = (
    "legacy_gui",
    "gfl2gui-tk",
    "gui-tk",
    "start_gfl2_tools_legacy",
    "--legacy-gui",
    "launch_legacy",
    "logger_compat",
    "extract_owned_dolls",
    "set_recording",
    "unity_asset_index.json",
    "rp" + "fv",
)
SOURCE_MANIFEST_SCHEMA = 2

PROHIBITED_RUNTIME_IMPORT_ROOTS = {
    "mitmproxy", "UnityPy", "scapy", "pyshark", "socket", "psutil",
    "pymem", "frida", "win32process", "win32api",
}
PROHIBITED_RUNTIME_TOKENS = (
    "AssetBundles_Windows", "GF2_Exilium_Data", "StreamingAssets",
    "ReadProcessMemory", "OpenProcess", "CreateToolhelp32Snapshot",
    "Npcap", "WinPcap", "protobuf_wire",
)

ACTIVE_TEXT_FILES = (
    "bootstrap.py",
    "launcher.ps1",
    "pyproject.toml",
    "start_gfl2_tools.bat",
    "repair_ocr.bat",
    "reset_environment.bat",
)

REQUIRED_RELEASE_DOCS = (
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ignored(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return path.name in EXCLUDED_FILES or any(
        part in EXCLUDED_DIRS or part.endswith((".egg-info", ".dist-info"))
        for part in rel.parts
    )


def package_version() -> str:
    path = ROOT / "src/gfl2tool/_version.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise RuntimeError("src/gfl2tool/_version.py does not contain a literal __version__")


def version_state() -> dict[str, str]:
    version = package_version()
    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)
    dynamic = project.get("project", {}).get("dynamic") or []
    dynamic_version = project.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version", {})
    attr = dynamic_version.get("attr") if isinstance(dynamic_version, dict) else None
    init_text = (ROOT / "src/gfl2tool/__init__.py").read_text(encoding="utf-8")
    bootstrap_text = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    if "version" not in dynamic or attr != "gfl2tool._version.__version__":
        raise RuntimeError("pyproject must derive its version from gfl2tool._version.__version__")
    if "from ._version import __version__" not in init_text:
        raise RuntimeError("gfl2tool.__init__ must re-export _version.__version__")
    if "APP_VERSION = _source_version()" not in bootstrap_text:
        raise RuntimeError("bootstrap must derive APP_VERSION from src/gfl2tool/_version.py")
    return {"package": version, "pyproject": version, "bootstrap": version}


def _source_manifest_files(files: list[Path]) -> list[Path]:
    tracked: list[Path] = []
    root_scripts = {
        "bootstrap.py",
        "launcher.ps1",
        "start_gfl2_tools.bat",
        "repair_ocr.bat",
        "reset_environment.bat",
        "pyproject.toml",
        "tools/apply_program_update.py",
        "tools/package_release.py",
        "tools/build_executable.py",
        "tools/frozen_main.py",
    }
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if rel in root_scripts or rel.startswith("src/gfl2tool/"):
            tracked.append(path)
    return tracked


def source_manifest(version: str, files: list[Path]) -> bytes:
    hashes = {
        path.relative_to(ROOT).as_posix(): sha256(path)
        for path in sorted(_source_manifest_files(files), key=lambda item: item.relative_to(ROOT).as_posix())
    }
    owned_roots = ("src/gfl2tool/", "tests/", "docs/", "schemas/", "tools/")
    owned = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())
        if path.relative_to(ROOT).as_posix().startswith(owned_roots)
    ]
    payload = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "version": version,
        "files": hashes,
        "owned": owned,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def audit(expected_version: str) -> dict[str, object]:
    failures: list[str] = []
    for rel in REQUIRED_RELEASE_DOCS:
        if not (ROOT / rel).is_file():
            failures.append(f"required release document missing: {rel}")
    versions = version_state()
    if set(versions.values()) != {expected_version}:
        failures.append(f"version mismatch: {versions!r}, expected={expected_version}")

    version_literal_duplicates: list[str] = []
    for rel in ACTIVE_TEXT_FILES + ("build_release.bat", "tools/package_release.py"):
        path = ROOT / rel
        if path.is_file() and expected_version in path.read_text(encoding="utf-8"):
            version_literal_duplicates.append(rel)
    if version_literal_duplicates:
        failures.append(f"duplicated application version literal outside _version.py: {version_literal_duplicates}")

    legacy_files = [
        ROOT / "src/gfl2tool/gui.py",
        ROOT / "src/gfl2tool/legacy_gui.py",
        ROOT / "start_gfl2_tools_legacy.bat",
    ]
    for path in legacy_files:
        if path.exists():
            failures.append(f"legacy file remains: {path.relative_to(ROOT)}")
    active_paths = [ROOT / x for x in ACTIVE_TEXT_FILES]
    active_paths += [p for p in (ROOT / "src").rglob("*.py") if not ignored(p)]
    for path in active_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        for token in FORBIDDEN_ACTIVE:
            if token in text:
                failures.append(f"active legacy token {token!r}: {rel}")
        if path.suffix == ".py" and ("import tkinter" in text or "from tkinter" in text):
            failures.append(f"tkinter import remains: {rel}")

    # Main-runtime trust boundary: no game-installation readers, packet/network
    # capture stacks, or process-memory tooling may enter the application.
    runtime_boundary_violations: list[str] = []
    runtime_root = ROOT / "src/gfl2tool"
    for path in runtime_root.rglob("*.py"):
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        for token in PROHIBITED_RUNTIME_TOKENS:
            if token.casefold() in text.casefold():
                runtime_boundary_violations.append(f"forbidden runtime token {token!r}: {rel}")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        denied = sorted(imported_roots & PROHIBITED_RUNTIME_IMPORT_ROOTS)
        if denied:
            runtime_boundary_violations.append(f"forbidden runtime import {denied}: {rel}")
        if "ctypes" in imported_roots and rel.as_posix() != "src/gfl2tool/qtui/tactic_overlay.py":
            runtime_boundary_violations.append(f"ctypes outside overlay window integration: {rel}")
        if "urllib" in imported_roots or "requests" in imported_roots or "httpx" in imported_roots:
            approved_network = {
                "src/gfl2tool/services/remote_catalog.py",
                "src/gfl2tool/services/remote_assets.py",
                "src/gfl2tool/services/app_update.py",
            }
            if rel.as_posix() not in approved_network:
                runtime_boundary_violations.append(f"network client outside program-data delivery boundary: {rel}")
    failures.extend(runtime_boundary_violations)

    ast_errors: list[str] = []
    duplicates: list[str] = []
    duplicate_module_definitions: list[str] = []
    py_files = [p for p in ROOT.rglob("*.py") if not ignored(p)]
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            ast_errors.append(f"{path.relative_to(ROOT)}: {exc}")
            continue

        module_seen: dict[str, int] = {}
        for item in tree.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if item.name in module_seen:
                duplicate_module_definitions.append(
                    f"{path.relative_to(ROOT)}:{item.name} lines {module_seen[item.name]}/{item.lineno}"
                )
            module_seen[item.name] = item.lineno

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen: dict[str, int] = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in seen:
                        duplicates.append(
                            f"{path.relative_to(ROOT)}:{node.name}.{item.name} lines {seen[item.name]}/{item.lineno}"
                        )
                    seen[item.name] = item.lineno
    duplicate_decorators: list[str] = []
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [ast.unparse(item) for item in node.decorator_list]
            if len(decorators) != len(set(decorators)):
                duplicate_decorators.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}:{decorators}"
                )
    failures.extend(f"AST: {x}" for x in ast_errors)
    failures.extend(f"duplicate module definition: {x}" for x in duplicate_module_definitions)
    failures.extend(f"duplicate method: {x}" for x in duplicates)
    failures.extend(f"duplicate decorator: {x}" for x in duplicate_decorators)

    qt_raw_sql = []
    qt_resize_columns = []
    for path in (ROOT / "src/gfl2tool/qtui").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "con.execute" in text:
            qt_raw_sql.append(str(path.relative_to(ROOT)))
        if "resizeColumnsToContents" in text:
            qt_resize_columns.append(str(path.relative_to(ROOT)))
    if qt_raw_sql:
        failures.append(f"raw Qt sqlite calls: {qt_raw_sql}")
    if qt_resize_columns:
        failures.append(f"hot auto-size calls: {qt_resize_columns}")

    direct_workers = []
    direct_tables = []
    direct_portraits = []
    worker_pattern = re.compile(r"\b(?:Worker|ProgressWorker)\s*\(")
    table_patterns = ("setSortingEnabled(", "setColumnWidth(", "setColumnHidden(", "setStretchLastSection(")
    for path in (ROOT / "src/gfl2tool/qtui").rglob("*.py"):
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        if path.name != "workers.py" and worker_pattern.search(text):
            direct_workers.append(str(rel))
        if path.name != "widgets.py" and any(token in text for token in table_patterns):
            direct_tables.append(str(rel))
        if path.name != "data.py" and "resolve_portrait_path" in text:
            direct_portraits.append(str(rel))
    if direct_workers:
        failures.append(f"direct Worker construction outside workers.py: {direct_workers}")
    if direct_tables:
        failures.append(f"direct QTableView configuration outside widgets.py: {direct_tables}")
    if direct_portraits:
        failures.append(f"direct portrait path resolution outside data.py: {direct_portraits}")

    direct_durable_writes = []
    for path in (ROOT / "src/gfl2tool").rglob("*.py"):
        if path.name == "atomic_io.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.write_(?:text|bytes)\s*\(", text):
            direct_durable_writes.append(str(path.relative_to(ROOT)))
    if direct_durable_writes:
        failures.append(f"direct durable file writes outside atomic_io: {direct_durable_writes}")

    primary_page_layout_violations = []
    primary_pages = ("dashboard", "inventory", "formation", "remolding_optimizer", "tactics", "data_sync")
    for name in primary_pages:
        path = ROOT / f"src/gfl2tool/qtui/pages/{name}.py"
        if not path.is_file():
            primary_page_layout_violations.append(f"missing:{name}")
            continue
        text = path.read_text(encoding="utf-8")
        if "page_layout(" not in text:
            primary_page_layout_violations.append(f"missing shared layout:{name}")
        if "setContentsMargins(20,18,20,18)" in text.replace(" ", ""):
            primary_page_layout_violations.append(f"direct root margins:{name}")
    if primary_page_layout_violations:
        failures.append(f"primary page layout contract violations: {primary_page_layout_violations}")

    direct_critical_dialogs = []
    unused_qt_imports = []
    qt_hardcoded_colors = []
    for path in (ROOT / "src/gfl2tool/qtui").rglob("*.py"):
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        if path.name != "widgets.py" and "QMessageBox.critical(" in text:
            direct_critical_dialogs.append(str(rel))
        if path.name != "theme.py" and re.search(r"#[0-9A-Fa-f]{6}\b", text):
            qt_hardcoded_colors.append(str(rel))
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    imported[local] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imported[alias.asname or alias.name] = node.lineno
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        for name, line in imported.items():
            if name not in used:
                unused_qt_imports.append(f"{rel}:{line}:{name}")
    if direct_critical_dialogs:
        failures.append(f"direct critical dialogs outside widgets.py: {direct_critical_dialogs}")
    if qt_hardcoded_colors:
        failures.append(f"hard-coded Qt colors outside theme.py: {qt_hardcoded_colors}")
    if unused_qt_imports:
        failures.append(f"unused Qt UI imports: {unused_qt_imports}")

    source_statement_semicolons = []
    for path in (ROOT / "src/gfl2tool").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
            lines = sorted({
                token.start[0]
                for token in tokens
                if token.type == tokenize.OP and token.string == ";"
            })
        except (IndentationError, tokenize.TokenError):
            lines = []
        if lines:
            source_statement_semicolons.append(
                f"{path.relative_to(ROOT)}:{','.join(str(line) for line in lines)}"
            )
    if source_statement_semicolons:
        failures.append(
            f"multiple Python statements on one line: {source_statement_semicolons}"
        )

    silent_broad_exception_handlers = []
    for path in (ROOT / "src/gfl2tool").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            if ast.unparse(node.type) != "Exception":
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                silent_broad_exception_handlers.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}"
                )
    if silent_broad_exception_handlers:
        failures.append(
            "silent 'except Exception: pass' handlers: "
            f"{silent_broad_exception_handlers}"
        )

    # A private module/class helper that appears only at its own definition is
    # definitely dead source.  Use a deliberately conservative token-count
    # check: comments/strings may hide a dead helper (false negative), but an
    # active helper will never be removed merely because this audit guesses.
    source_paths = list((ROOT / "src/gfl2tool").rglob("*.py"))
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    unused_private_definitions = []
    for path in source_paths:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                candidates.append(node)
            elif isinstance(node, ast.ClassDef):
                candidates.extend(
                    item for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for node in candidates:
            if not node.name.startswith("_") or node.name.startswith("__"):
                continue
            if len(re.findall(rf"\b{re.escape(node.name)}\b", source_text)) == 1:
                unused_private_definitions.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}"
                )
    if unused_private_definitions:
        failures.append(f"unused private module/class helpers: {unused_private_definitions}")

    # Public module-level helpers can also become dead after a refactor. Unlike
    # methods, they are not invoked by Qt/proxy frameworks, so require at least
    # one source reference outside their own body. Console-script ``main``
    # functions are referenced by pyproject metadata rather than Python source.
    source_references: dict[str, list[tuple[Path, int]]] = {}
    parsed_sources: dict[Path, ast.AST] = {}
    for path in source_paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        parsed_sources[path] = tree
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                source_references.setdefault(node.id, []).append((path, node.lineno))
            elif isinstance(node, ast.Attribute):
                source_references.setdefault(node.attr, []).append((path, node.lineno))

    unused_public_module_functions = []
    for path, tree in parsed_sources.items():
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") or node.name == "main":
                continue
            refs = source_references.get(node.name, [])
            external_refs = [
                (ref_path, line)
                for ref_path, line in refs
                if not (
                    ref_path == path
                    and node.lineno <= line <= getattr(node, "end_lineno", node.lineno)
                )
            ]
            if not external_refs:
                unused_public_module_functions.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}"
                )
    if unused_public_module_functions:
        failures.append(
            f"unused public module helpers: {unused_public_module_functions}"
        )

    unused_source_imports = []
    for path in (ROOT / "src/gfl2tool").rglob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    imported[local] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    if alias.name != "*":
                        imported[alias.asname or alias.name] = node.lineno
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        rel = path.relative_to(ROOT)
        for name, line in imported.items():
            if name not in used:
                unused_source_imports.append(f"{rel}:{line}:{name}")
    if unused_source_imports:
        failures.append(f"unused source imports: {unused_source_imports}")

    return {
        "version": expected_version,
        "versions": versions,
        "version_literal_duplicates": version_literal_duplicates,
        "python_files": len(py_files),
        "ast_errors": ast_errors,
        "duplicate_module_definitions": duplicate_module_definitions,
        "duplicate_methods": duplicates,
        "runtime_boundary_violations": runtime_boundary_violations,
        "duplicate_decorators": duplicate_decorators,
        "qt_raw_sql": qt_raw_sql,
        "qt_resize_columns": qt_resize_columns,
        "direct_workers": direct_workers,
        "direct_table_configuration": direct_tables,
        "direct_portrait_resolution": direct_portraits,
        "direct_durable_writes": direct_durable_writes,
        "primary_page_layout_violations": primary_page_layout_violations,
        "direct_critical_dialogs": direct_critical_dialogs,
        "qt_hardcoded_colors": qt_hardcoded_colors,
        "unused_qt_imports": unused_qt_imports,
        "source_statement_semicolons": source_statement_semicolons,
        "silent_broad_exception_handlers": silent_broad_exception_handlers,
        "unused_private_definitions": unused_private_definitions,
        "unused_public_module_functions": unused_public_module_functions,
        "unused_source_imports": unused_source_imports,
        "failures": failures,
    }


def refresh_source_manifest(version: str) -> Path:
    """Write the integrity manifest used by bootstrap/tests from the current source tree."""
    files = [p for p in ROOT.rglob("*") if p.is_file() and not ignored(p)]
    target = ROOT / "release-source.json"
    payload = source_manifest(version, files)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return target


def run_tests() -> None:
    subprocess.run([sys.executable, "-m", "compileall", "-q", "src", "bootstrap.py", "tests"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)


def _release_zip_datetime(version: str) -> tuple[int, int, int, int, int, int]:
    """Return a reproducible timestamp that changes with the release version.

    A single fixed timestamp across releases can make timestamp/size validated
    Python bytecode from an older overlay look current.  Deriving the timestamp
    from the version preserves byte-for-byte reproducible archives while making
    that cross-release cache collision practically impossible.
    """

    digest = hashlib.sha256(version.encode("utf-8")).digest()
    return (
        2000 + digest[0] % 80,
        1 + digest[1] % 12,
        1 + digest[2] % 28,
        digest[3] % 24,
        digest[4] % 60,
        (digest[5] % 30) * 2,
    )


def _write_zip_entry(
    zf: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    date_time: tuple[int, int, int, int, int, int],
) -> None:
    info = zipfile.ZipInfo(name, date_time=date_time)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    info.flag_bits |= 0x800
    zf.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=7)


def package(version: str, output: Path) -> tuple[Path, Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    zip_path = output / f"gfl2-tools-v{version}-source.zip"
    sha_path = output / f"gfl2-tools-v{version}-source.sha256"
    manifest_path = output / f"gfl2-tools-v{version}-source.release.json"
    zip_path.unlink(missing_ok=True)

    # Release archives are intentionally rootless. Extracting directly into an
    # existing project folder therefore replaces src/ and root scripts instead
    # of creating a nested version directory that can leave a mixed source tree.
    files = [p for p in ROOT.rglob("*") if p.is_file() and not ignored(p)]
    source_manifest_bytes = source_manifest(version, files)
    zip_datetime = _release_zip_datetime(version)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as zf:
        for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
            rel = path.relative_to(ROOT).as_posix()
            _write_zip_entry(zf, rel, path.read_bytes(), date_time=zip_datetime)
        _write_zip_entry(zf, "release-source.json", source_manifest_bytes, date_time=zip_datetime)

    digest = sha256(zip_path)
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    manifest = {
        "version": version,
        "archive": zip_path.name,
        "sha256": digest,
        "file_count": len(files) + 1,
        "qt_only": True,
        "kind": "source",
        "archive_layout": "overlay-root",
        "source_integrity_manifest": "release-source.json",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return zip_path, sha_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and package the GPLv3 GFL2 Tools corresponding source release")
    parser.add_argument(
        "--version",
        help="Optional assertion. The actual version is always read from src/gfl2tool/_version.py.",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    version = package_version()
    if args.version and args.version != version:
        print(f"requested version {args.version!r} does not match source version {version!r}", file=sys.stderr)
        return 2

    report = audit(version)
    if report["failures"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    refresh_source_manifest(version)
    if not args.skip_tests:
        run_tests()
    zip_path, sha_path, manifest_path = package(version, args.output.resolve())
    print(json.dumps({
        "audit": report,
        "zip": str(zip_path),
        "sha256_file": str(sha_path),
        "manifest": str(manifest_path),
        "sha256": sha256(zip_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
