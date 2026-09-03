from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "src" / "gfl2tool" / "_version.py"
SOURCE_MANIFEST = ROOT / "release-source.json"
SOURCE_MANIFEST_SCHEMA = 2


def _source_version() -> str:
    """Read the package version without importing the application package."""

    try:
        tree = ast.parse(VERSION_FILE.read_text(encoding="utf-8"), filename=str(VERSION_FILE))
    except (OSError, SyntaxError):
        return "unknown"
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return "unknown"


APP_VERSION = _source_version()
RUNTIME_PREFIX = "env-v400"
RUNTIME_ROOT = ROOT / ".gfl2_runtime"
CURRENT_FILE = RUNTIME_ROOT / "current.txt"
BOOTSTRAP_LOG = RUNTIME_ROOT / "bootstrap.log"
CORE_VERIFY_LOG = RUNTIME_ROOT / "core-verify.log"
OCR_FAIL_MARKER = RUNTIME_ROOT / ".ocr-install-failed.json"
OCR_ROOT = RUNTIME_ROOT / "ocr"
OCR_ENGINE = OCR_ROOT / "engine"
OCR_TESSDATA = OCR_ROOT / "tessdata"
OCR_INSTALL_LOG = RUNTIME_ROOT / "ocr-install.log"
SOURCE_BYTECODE_MARKER = "source-bytecode.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_relative_path(raw: object) -> str | None:
    rel = str(raw).replace("\\", "/").strip("/")
    if not rel:
        return None
    parts = Path(rel).parts
    if ".." in parts or "." in parts:
        return None
    first = parts[0] if parts else ""
    if Path(rel).is_absolute() or ":" in first:
        return None
    return Path(*parts).as_posix()


def _quarantine_stale_sources(paths: list[str]) -> list[str]:
    """Move files outside the current release ownership set out of the source tree."""

    failures: list[str] = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for raw in paths:
        rel = _managed_relative_path(raw)
        if rel is None:
            failures.append(f"invalid stale path: {raw}")
            continue
        source = ROOT / rel
        if not source.exists():
            continue
        if not source.is_file():
            failures.append(f"stale path is not a file: {rel}")
            continue
        target = RUNTIME_ROOT / "stale-source" / stamp / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            log(f"[GFL2 Tools] Moved stale source file aside: {rel}")
        except OSError as exc:
            failures.append(f"could not quarantine stale source: {rel} ({exc})")
    return failures


def _source_manifest_fingerprint() -> str:
    try:
        return hashlib.sha256(SOURCE_MANIFEST.read_bytes()).hexdigest()
    except OSError:
        return APP_VERSION


def prepare_source_bytecode() -> bool:
    """Remove app bytecode once per packaged source generation.

    Release archives use deterministic timestamps.  Older releases used the same
    timestamp for every file, so a timestamp-based ``.pyc`` from an earlier
    version could survive an in-place overlay when the new source had the same
    byte length.  Python would then execute the stale bytecode even though the
    on-disk ``.py`` files and their SHA-256 manifest were correct.

    Official releases clear only ``gfl2tool`` bytecode when the release manifest
    changes.  Development checkouts have no manifest and are left untouched.
    """

    if not SOURCE_MANIFEST.is_file():
        return True
    package_root = ROOT / "src" / "gfl2tool"
    if not package_root.is_dir():
        return True

    fingerprint = _source_manifest_fingerprint()
    marker = RUNTIME_ROOT / SOURCE_BYTECODE_MARKER
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if payload.get("version") == APP_VERSION and payload.get("manifest_sha256") == fingerprint:
        return True

    failures: list[str] = []
    removed = 0
    cache_dirs = sorted(package_root.rglob("__pycache__"), key=lambda path: len(path.parts), reverse=True)
    for cache_dir in cache_dirs:
        try:
            shutil.rmtree(cache_dir)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{cache_dir.relative_to(ROOT)} ({exc})")

    for pyc in package_root.rglob("*.pyc"):
        try:
            pyc.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{pyc.relative_to(ROOT)} ({exc})")

    if failures:
        log("[GFL2 Tools] Could not clear stale application bytecode.")
        for item in failures[:8]:
            log(f"[GFL2 Tools]   {item}")
        log("[GFL2 Tools] Close other GFL2 Tools/Python processes using this project and retry.")
        return False

    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"version": APP_VERSION, "manifest_sha256": fingerprint},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(marker)
    except OSError as exc:
        log(f"[GFL2 Tools] Could not record source bytecode cleanup state: {exc}")
        return False

    if removed:
        log(f"[GFL2 Tools] Cleared stale application bytecode cache ({removed} item(s)).")
    return True


def source_tree_consistent() -> bool:
    """Validate packaged source files and report mixed-version overlays precisely.

    Development/source checkouts do not contain ``release-source.json`` and are
    allowed through.  Official release archives contain it, so an in-place
    update that leaves old ``src`` files behind fails *before* any runtime is
    created or package installation is attempted.
    """

    if APP_VERSION == "unknown":
        log("[GFL2 Tools] Could not read src/gfl2tool/_version.py.")
        return False
    if not SOURCE_MANIFEST.is_file():
        return True

    try:
        payload = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
        manifest_schema = int(payload.get("schema") or 0)
        expected_version = str(payload.get("version") or "")
        files = payload.get("files")
        owned_raw = payload.get("owned")
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[GFL2 Tools] Release source manifest is unreadable: {exc}")
        return False
    if (
        manifest_schema != SOURCE_MANIFEST_SCHEMA
        or expected_version != APP_VERSION
        or not isinstance(files, dict)
        or (owned_raw is not None and not isinstance(owned_raw, list))
    ):
        log("[GFL2 Tools] Release source manifest does not match this source tree.")
        log(f"[GFL2 Tools] manifest version: {expected_version or 'unknown'}")
        log(f"[GFL2 Tools] source version: {APP_VERSION}")
        return False

    mismatches: list[str] = []
    tracked = set()
    for rel, expected_hash in files.items():
        rel_text = _managed_relative_path(rel)
        if rel_text is None:
            mismatches.append(f"invalid managed path: {rel}")
            continue
        tracked.add(rel_text)
        path = ROOT / rel_text
        if not path.is_file():
            mismatches.append(f"missing: {rel_text}")
            continue
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            mismatches.append(f"unreadable: {rel_text} ({exc})")
            continue
        if actual != str(expected_hash):
            mismatches.append(f"outdated/modified: {rel_text}")

    owned: set[str] = set(tracked)
    managed_roots = ("src/gfl2tool",)
    if isinstance(owned_raw, list):
        for raw in owned_raw:
            rel = _managed_relative_path(raw)
            if rel is None:
                mismatches.append(f"invalid owned path: {raw}")
                continue
            owned.add(rel)
        managed_roots = ("src/gfl2tool", "tests", "docs", "schemas")

    stale_package_files: list[str] = []
    for root_name in managed_roots:
        managed_root = ROOT / root_name
        if not managed_root.is_dir():
            continue
        for path in managed_root.rglob("*"):
            if not path.is_file():
                continue
            rel_path = path.relative_to(ROOT)
            if "__pycache__" in rel_path.parts or path.suffix.lower() == ".pyc":
                continue
            rel = rel_path.as_posix()
            if rel not in owned:
                stale_package_files.append(rel)
    mismatches.extend(_quarantine_stale_sources(stale_package_files))

    if not mismatches:
        return True

    log("[GFL2 Tools] Mixed or incomplete source update detected.")
    for item in mismatches[:12]:
        log(f"[GFL2 Tools]   {item}")
    if len(mismatches) > 12:
        log(f"[GFL2 Tools]   ... and {len(mismatches) - 12} more")
    log("[GFL2 Tools] Re-extract the release ZIP directly into this project folder and overwrite all files.")
    log("[GFL2 Tools] Release ZIPs use an overlay-friendly root layout.")
    return False


def runtime_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _find_tesseract_executable() -> Path | None:
    configured = str(os.environ.get("GFL2_TESSERACT_EXE") or "").strip()
    candidates = [Path(configured)] if configured else []
    # Prefer the project-local OCR engine so deleting .gfl2_runtime removes it
    # completely and no user/system Tesseract installation is required.
    candidates.extend([OCR_ENGINE / "tesseract.exe", OCR_ENGINE / "tesseract"])
    if OCR_ENGINE.is_dir():
        try:
            candidates.extend(OCR_ENGINE.rglob("tesseract.exe"))
        except OSError:
            pass
    discovered = shutil.which("tesseract")
    if discovered:
        candidates.append(Path(discovered))
    if os.name == "nt":
        candidates.extend(
            [
                Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
                Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe",
            ]
        )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _localize_tesseract(executable: Path) -> Path | None:
    """Copy an installed engine beside the tool when it is not already local."""
    try:
        resolved = executable.resolve()
        local_root = OCR_ENGINE.resolve()
        if local_root == resolved.parent or local_root in resolved.parents:
            return resolved
    except OSError:
        resolved = executable
    try:
        OCR_ENGINE.mkdir(parents=True, exist_ok=True)
        # The UB Mannheim distribution keeps DLLs next to tesseract.exe; copy
        # that directory as a unit instead of copying only the executable.
        shutil.copytree(executable.parent, OCR_ENGINE, dirs_exist_ok=True)
    except OSError as exc:
        log(f"[GFL2 Tools] Could not copy OCR engine into project runtime: {exc}")
        return None
    direct = OCR_ENGINE / executable.name
    if direct.is_file():
        return direct
    try:
        return next(OCR_ENGINE.rglob("tesseract.exe"))
    except (OSError, StopIteration):
        return None


def _ocr_runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    executable = _find_tesseract_executable()
    if executable is not None:
        env["GFL2_TESSERACT_EXE"] = str(executable)
        env["PATH"] = str(executable.parent) + os.pathsep + env.get("PATH", "")
    if (OCR_TESSDATA / "kor.traineddata").is_file() and (OCR_TESSDATA / "eng.traineddata").is_file():
        env["TESSDATA_PREFIX"] = str(OCR_TESSDATA)
    return env


def app_env() -> dict[str, str]:
    env = _ocr_runtime_env()
    src = str(ROOT / "src")
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not old else src + os.pathsep + old
    return env


def log(message: str) -> None:
    print(message, flush=True)
    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        with BOOTSTRAP_LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(message + "\n")
    except OSError:
        pass


def run(cmd: list[str], *, stdout=None, stderr=None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=app_env(), stdout=stdout, stderr=stderr, check=check)


def supported_base_python() -> bool:
    return (3, 11) <= sys.version_info[:2] < (3, 14) and sys.maxsize > 2**32


def _distribution_name(requirement: str) -> str:
    head = requirement.split(";", 1)[0].split("[", 1)[0]
    for marker in ("<", ">", "=", "!", "~"):
        head = head.split(marker, 1)[0]
    return head.strip().lower().replace("_", "-")


def project_requirements(*, optional_group: str | None = None) -> list[str]:
    """Read dependency pins from pyproject so bootstrap has no second version list."""

    try:
        with (ROOT / "pyproject.toml").open("rb") as fh:
            project = tomllib.load(fh).get("project", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log(f"[GFL2 Tools] Could not read pyproject dependencies: {exc}")
        return []
    if optional_group is None:
        values = project.get("dependencies") or []
    else:
        values = (project.get("optional-dependencies") or {}).get(optional_group) or []
    return [str(value) for value in values]


def select_requirements(requirements: list[str], names: tuple[str, ...]) -> list[str]:
    by_name = {_distribution_name(item): item for item in requirements}
    selected: list[str] = []
    missing: list[str] = []
    for name in names:
        value = by_name.get(name.lower().replace("_", "-"))
        if value is None:
            missing.append(name)
        else:
            selected.append(value)
    if missing:
        log(f"[GFL2 Tools] Missing dependency declarations in pyproject.toml: {', '.join(missing)}")
        return []
    return selected


def _runtime_probe(
    env_dir: Path,
    names: tuple[str, ...] = ("gfl2tool", "PySide6", "Pillow"),
) -> dict[str, dict[str, str | bool]]:
    """Probe launch-critical and asset dependencies with actionable diagnostics.

    Keep application-source verification separate from dependency installation so a
    single optional image decoder cannot make the whole GUI look like a version
    mismatch.  The child process explicitly prepends ``ROOT/src`` instead of
    relying solely on inherited PYTHONPATH, which also avoids stale installed
    ``gfl2tool`` packages shadowing the current checkout.
    """

    py = runtime_python(env_dir)
    if not py.is_file():
        return {}

    source_dir = str((ROOT / "src").resolve())
    probe_code = f"""
import importlib, json, os, sys
sys.path.insert(0, {source_dir!r})
specs = {{
    "gfl2tool": "gfl2tool",
    "PySide6": "PySide6.QtCore",
    "Pillow": "PIL",
}}
checks = [(label, specs[label]) for label in {names!r} if label in specs]
results = {{}}
for label, module_name in checks:
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", "")
        if label == "PySide6":
            try:
                root = importlib.import_module("PySide6")
                version = getattr(root, "__version__", version)
            except Exception:
                pass
        path = getattr(module, "__file__", "") or ""
        results[label] = {{"ok": True, "version": str(version), "path": str(path), "error": ""}}
    except Exception as exc:
        results[label] = {{"ok": False, "version": "", "path": "", "error": f"{{type(exc).__name__}}: {{exc}}"}}
print("GFL2_PROBE=" + json.dumps(results, ensure_ascii=False))
"""
    try:
        result = subprocess.run(
            [str(py), "-c", probe_code],
            cwd=ROOT,
            env=app_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        return {"bootstrap": {"ok": False, "version": "", "path": str(py), "error": str(exc)}}

    raw = result.stdout or ""
    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        CORE_VERIFY_LOG.write_text(raw, encoding="utf-8", errors="replace")
    except OSError:
        pass

    marker = "GFL2_PROBE="
    payload = None
    for line in reversed(raw.splitlines()):
        if line.startswith(marker):
            payload = line[len(marker):]
            break
    if payload is None:
        return {
            "bootstrap": {
                "ok": False,
                "version": "",
                "path": str(py),
                "error": f"runtime probe exited with code {result.returncode}",
            }
        }
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        return {"bootstrap": {"ok": False, "version": "", "path": str(py), "error": f"invalid probe output: {exc}"}}
    return decoded if isinstance(decoded, dict) else {}


def _probe_ok(probe: dict[str, dict[str, str | bool]], names: tuple[str, ...]) -> bool:
    return all(bool(probe.get(name, {}).get("ok")) for name in names)


def _log_probe_failures(probe: dict[str, dict[str, str | bool]], names: tuple[str, ...]) -> None:
    for name in names:
        item = probe.get(name, {})
        if item.get("ok"):
            continue
        error = str(item.get("error") or "not detected")
        log(f"[GFL2 Tools]   {name}: {error}")


def verify_core(env_dir: Path) -> bool:
    critical = ("gfl2tool", "PySide6", "Pillow")
    probe = _runtime_probe(env_dir, critical)
    if not _probe_ok(probe, critical):
        return False
    source_version = str(probe.get("gfl2tool", {}).get("version") or "")
    return source_version == APP_VERSION


def read_current() -> Path | None:
    try:
        name = CURRENT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not name or Path(name).name != name:
        return None
    return RUNTIME_ROOT / name


def set_current(env_dir: Path) -> None:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = CURRENT_FILE.with_suffix(".tmp")
    tmp.write_text(env_dir.name, encoding="utf-8")
    tmp.replace(CURRENT_FILE)


def unique_runtime(prefix: str) -> Path:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    base = RUNTIME_ROOT / prefix
    if not base.exists():
        return base
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = RUNTIME_ROOT / f"{prefix}-{stamp}"
    i = 2
    while candidate.exists():
        candidate = RUNTIME_ROOT / f"{prefix}-{stamp}-{i}"
        i += 1
    return candidate


def reusable_runtimes(*, exclude: Path | None = None) -> list[Path]:
    """Return newest unregistered runtimes that may already contain dependencies."""

    try:
        candidates = [
            path
            for path in RUNTIME_ROOT.glob(f"{RUNTIME_PREFIX}*")
            if path.is_dir() and path != exclude and runtime_python(path).is_file()
        ]
    except OSError:
        return []

    def stamp(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    candidates.sort(key=stamp, reverse=True)
    return candidates[:8]


def try_reuse_runtime(*, exclude: Path | None = None) -> Path | None:
    for candidate in reusable_runtimes(exclude=exclude):
        if not verify_core(candidate):
            continue
        set_current(candidate)
        log(f"[GFL2 Tools] Recovered an already-installed project runtime:\n  {candidate}")
        return candidate
    return None


def install_core(env_dir: Path) -> bool:
    py = runtime_python(env_dir)
    dependencies = project_requirements()
    core_packages = select_requirements(dependencies, ("pyside6-essentials", "pillow"))
    if len(core_packages) != 2:
        return False

    log("[GFL2 Tools] Installing core dependencies: PySide6 + Pillow")
    result = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *core_packages])
    if result.returncode != 0:
        log(f"[GFL2 Tools] pip failed while installing core dependencies (exit={result.returncode}).")
        return False

    critical = ("gfl2tool", "PySide6", "Pillow")
    probe = _runtime_probe(env_dir, critical)
    if not _probe_ok(probe, critical):
        log("[GFL2 Tools] Core runtime verification failed.")
        _log_probe_failures(probe, critical)
        log(f"[GFL2 Tools] Detailed verification log: {CORE_VERIFY_LOG}")
        return False

    source_version = str(probe.get("gfl2tool", {}).get("version") or "")
    if source_version != APP_VERSION:
        source_path = str(probe.get("gfl2tool", {}).get("path") or "unknown")
        log("[GFL2 Tools] The runtime imported a different gfl2tool source tree.")
        log(f"[GFL2 Tools] expected version: {APP_VERSION}")
        log(f"[GFL2 Tools] imported version: {source_version or 'unknown'}")
        log(f"[GFL2 Tools] imported from: {source_path}")
        log(f"[GFL2 Tools] Detailed verification log: {CORE_VERIFY_LOG}")
        return False
    return True

def _ocr_failure(message: str) -> None:
    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        OCR_FAIL_MARKER.write_text(
            json.dumps({"version": APP_VERSION, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _ensure_ocr_language_data() -> bool:
    OCR_TESSDATA.mkdir(parents=True, exist_ok=True)
    urls = {
        "kor.traineddata": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/kor.traineddata",
        "eng.traineddata": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata",
    }
    for name, url in urls.items():
        target = OCR_TESSDATA / name
        if target.is_file() and target.stat().st_size > 100_000:
            continue
        temp = target.with_suffix(target.suffix + ".tmp")
        try:
            log(f"[GFL2 Tools] Downloading OCR language data: {name}")
            with urllib.request.urlopen(url, timeout=60) as response, temp.open("wb") as out:
                shutil.copyfileobj(response, out)
            if temp.stat().st_size <= 100_000:
                raise OSError("downloaded language file is unexpectedly small")
            temp.replace(target)
        except (OSError, urllib.error.URLError) as exc:
            with contextlib.suppress(OSError):
                temp.unlink()
            log(f"[GFL2 Tools] OCR language data download failed: {exc}")
            return False
    return True


def verify_ocr() -> bool:
    executable = _find_tesseract_executable()
    if executable is None:
        return False
    if not ((OCR_TESSDATA / "kor.traineddata").is_file() and (OCR_TESSDATA / "eng.traineddata").is_file()):
        return False
    env = _ocr_runtime_env()
    try:
        result = subprocess.run(
            [str(executable), "--list-langs"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    languages = {line.strip() for line in (result.stdout or "").splitlines()}
    return result.returncode == 0 and {"kor", "eng"}.issubset(languages)


def install_ocr(*, repair: bool = False) -> bool:
    existing = _find_tesseract_executable()
    if existing is not None:
        local = _localize_tesseract(existing)
        if local is not None:
            os.environ["GFL2_TESSERACT_EXE"] = str(local)
    if verify_ocr():
        with contextlib.suppress(OSError):
            OCR_FAIL_MARKER.unlink()
        return True
    if OCR_FAIL_MARKER.is_file() and not repair:
        try:
            payload = json.loads(OCR_FAIL_MARKER.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if str(payload.get("version") or "") == APP_VERSION:
            log("[GFL2 Tools] Previous OCR setup failed for this release; skipping repeated install attempt.")
            log("[GFL2 Tools] Use the OCR tab's repair button to retry.")
            return False

    executable = _find_tesseract_executable()
    if executable is not None:
        local = _localize_tesseract(executable)
        if local is not None:
            executable = local
            os.environ["GFL2_TESSERACT_EXE"] = str(local)
    if executable is None and os.name == "nt":
        winget = shutil.which("winget")
        if winget:
            RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
            OCR_ENGINE.mkdir(parents=True, exist_ok=True)
            log("[GFL2 Tools] Installing project-local OCR engine automatically (Tesseract OCR)...")
            log(f"[GFL2 Tools] OCR install location: {OCR_ENGINE}")
            log(f"[GFL2 Tools] OCR install log: {OCR_INSTALL_LOG}")
            try:
                with OCR_INSTALL_LOG.open("w", encoding="utf-8", errors="replace") as out:
                    result = subprocess.run(
                        [
                            winget, "install", "--id", "UB-Mannheim.TesseractOCR", "-e",
                            "--location", str(OCR_ENGINE),
                            "--accept-package-agreements", "--accept-source-agreements", "--silent",
                        ],
                        cwd=ROOT,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        timeout=300,
                    )
            except (OSError, subprocess.SubprocessError) as exc:
                _ocr_failure(str(exc))
                log(f"[GFL2 Tools] OCR engine installation could not complete: {exc}")
                return False
            if result.returncode != 0:
                _ocr_failure(f"winget exit={result.returncode}")
                log(f"[GFL2 Tools] OCR engine installation failed (exit={result.returncode}).")
                return False
            executable = _find_tesseract_executable()
            if executable is not None:
                local = _localize_tesseract(executable)
                if local is not None:
                    executable = local
                    os.environ["GFL2_TESSERACT_EXE"] = str(local)
        else:
            _ocr_failure("winget not found")
            log("[GFL2 Tools] OCR engine is missing and Windows Package Manager (winget) is unavailable.")
            return False
    if executable is None:
        # Non-Windows development environments are not modified automatically.
        _ocr_failure("tesseract executable not found")
        return False
    if not _ensure_ocr_language_data():
        _ocr_failure("language data download failed")
        return False
    ok = verify_ocr()
    if ok:
        with contextlib.suppress(OSError):
            OCR_FAIL_MARKER.unlink()
        log("[GFL2 Tools] OCR engine ready (Korean + English).")
        return True
    _ocr_failure("verification failed")
    log("[GFL2 Tools] OCR setup finished but verification failed.")
    return False


def repair_ocr() -> int:
    with contextlib.suppress(OSError):
        OCR_FAIL_MARKER.unlink()
    return 0 if install_ocr(repair=True) else 1


def create_runtime(*, prefix: str) -> Path | None:
    env_dir = unique_runtime(prefix)
    log(f"[GFL2 Tools] Creating project-local runtime:\n  {env_dir}")
    try:
        result = subprocess.run([sys.executable, "-m", "venv", str(env_dir)], cwd=ROOT)
    except OSError as exc:
        log(f"[GFL2 Tools] Failed to invoke venv: {exc}")
        return None
    if result.returncode != 0 or not runtime_python(env_dir).is_file():
        log("[GFL2 Tools] Failed to create the Python virtual environment.")
        with contextlib.suppress(OSError):
            shutil.rmtree(env_dir)
        return None

    if not install_core(env_dir):
        log("[GFL2 Tools] Core runtime preparation failed; see the preceding install/verification message.")
        log("[GFL2 Tools] Removing the failed unactivated runtime to avoid wasting disk space.")
        with contextlib.suppress(OSError):
            shutil.rmtree(env_dir)
        return None

    set_current(env_dir)
    return env_dir


def ensure_runtime() -> Path | None:
    current = read_current()
    if current and verify_core(current):
        log(f"[GFL2 Tools] Using project-local runtime:\n  {current}")
        return current

    if current:
        log("[GFL2 Tools] Existing active runtime is incomplete or incompatible.")

    recovered = try_reuse_runtime(exclude=current)
    if recovered is not None:
        return recovered

    if current:
        log("[GFL2 Tools] No reusable runtime was found; creating a clean one beside it.")
    return create_runtime(prefix=RUNTIME_PREFIX)

def reset_runtime() -> int:
    if not RUNTIME_ROOT.exists():
        log("[GFL2 Tools] No project-local runtime exists. Nothing to reset.")
        return 0
    log(f"[GFL2 Tools] Removing project-local runtime folder:\n  {RUNTIME_ROOT}")
    try:
        shutil.rmtree(RUNTIME_ROOT)
    except OSError as exc:
        log(f"[GFL2 Tools] Runtime reset failed: {exc}")
        log("[GFL2 Tools] Close every GFL2 Tools window and retry.")
        return 1
    print("[GFL2 Tools] Runtime removed. Project data was not touched.")
    return 0


def launch_gui(env_dir: Path) -> int:
    py = runtime_python(env_dir)
    log("[GFL2 Tools] Starting PySide6 GUI...")
    try:
        return subprocess.call([str(py), "-m", "gfl2tool.cli", "gui"], cwd=ROOT, env=app_env())
    except OSError as exc:
        log(f"[GFL2 Tools] GUI launch failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-ocr", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    print(f"[GFL2 Tools] Bootstrap v{APP_VERSION}")
    if not source_tree_consistent():
        return 1
    if not prepare_source_bytecode():
        return 1
    if not supported_base_python():
        print("[GFL2 Tools] Python 3.11 - 3.13 64-bit is required.")
        print(f"[GFL2 Tools] Current interpreter: {sys.executable} / {sys.version.split()[0]}")
        return 1

    if args.reset:
        return reset_runtime()
    if args.repair_ocr:
        return repair_ocr()

    env_dir = ensure_runtime()
    # ensure_runtime() only returns an environment after verify_core() succeeded
    # (or immediately after install_core(), which performs the same verification).
    # Re-importing PySide6/Pillow here made every normal launch pay that startup
    # cost twice without increasing safety.
    if env_dir is None:
        log("[GFL2 Tools] Could not prepare the core runtime.")
        return 1
    ocr_ok = install_ocr(repair=False)
    if not ocr_ok:
        log("[GFL2 Tools] OCR support is currently unavailable; manual entry and file synchronization remain usable.")
    if args.no_launch:
        return 0
    return launch_gui(env_dir)


if __name__ == "__main__":
    raise SystemExit(main())
