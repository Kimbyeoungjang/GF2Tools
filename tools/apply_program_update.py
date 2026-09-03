from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile


PROTECTED_ROOTS = {"data", ".gfl2_runtime", ".gfl2_update", ".git"}
BINARY_MANIFEST = "release-binary.json"


def _safe_entry(name: str) -> bool:
    text = str(name or "").replace("\\", "/")
    if not text or text.startswith("/"):
        return False
    if len(text) >= 2 and text[1] == ":":
        return False
    return ".." not in Path(text).parts


def _read_source_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return ""


def _wait_for_process(pid: int, timeout: float = 60.0) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
        if not handle:
            return True
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            return result == wait_object_0 or result != wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _archive_manifest(package: Path) -> dict[str, object] | None:
    with zipfile.ZipFile(package, "r") as archive:
        try:
            payload = json.loads(archive.read(BINARY_MANIFEST).decode("utf-8"))
        except KeyError:
            return None
    if not isinstance(payload, dict):
        raise RuntimeError("binary update manifest가 올바르지 않습니다.")
    return payload


def _validate_archive(package: Path, expected_version: str) -> str:
    with zipfile.ZipFile(package, "r") as archive:
        names = {item.filename for item in archive.infolist() if not item.is_dir()}
        if any(not _safe_entry(name) for name in names):
            raise RuntimeError("업데이트 ZIP에 안전하지 않은 경로가 있습니다.")

        try:
            binary = json.loads(archive.read(BINARY_MANIFEST).decode("utf-8"))
        except KeyError:
            binary = None
        if binary is not None:
            if not isinstance(binary, dict) or int(binary.get("schema") or 0) != 1:
                raise RuntimeError("binary update manifest를 검증하지 못했습니다.")
            if str(binary.get("kind") or "") != "gfl2-tools-windows-binary":
                raise RuntimeError("지원하지 않는 binary update package입니다.")
            if str(binary.get("version") or "") != expected_version:
                raise RuntimeError("binary update manifest 버전이 예상 버전과 다릅니다.")
            required = {"GF2Tools.exe", "GF2ToolsUpdater.exe", "ocr/engine/tesseract.exe", "LICENSE", BINARY_MANIFEST}
            if not required.issubset(names):
                raise RuntimeError("binary update ZIP에 필수 실행 파일이 없습니다.")
            files = binary.get("files")
            if not isinstance(files, dict) or not files:
                raise RuntimeError("binary update hash 목록이 없습니다.")
            for rel, expected_hash in files.items():
                rel_text = str(rel or "").replace("\\", "/")
                if rel_text not in names or not _safe_entry(rel_text):
                    raise RuntimeError(f"업데이트 추적 파일이 누락되었습니다: {rel_text}")
                if hashlib.sha256(archive.read(rel_text)).hexdigest() != str(expected_hash):
                    raise RuntimeError(f"업데이트 파일 무결성 검증에 실패했습니다: {rel_text}")
            return "binary"

        required = {"src/gfl2tool/_version.py", "release-source.json", "start_gfl2_tools.bat", "bootstrap.py"}
        if not required.issubset(names):
            raise RuntimeError("업데이트 ZIP에 필수 프로그램 파일이 없습니다.")
        manifest = json.loads(archive.read("release-source.json").decode("utf-8"))
        if not isinstance(manifest, dict) or int(manifest.get("schema") or 0) != 2:
            raise RuntimeError("release-source manifest를 검증하지 못했습니다.")
        if str(manifest.get("version") or "") != expected_version:
            raise RuntimeError("업데이트 manifest 버전이 예상 버전과 다릅니다.")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise RuntimeError("업데이트 source hash 목록이 없습니다.")
        for rel, expected_hash in files.items():
            rel_text = str(rel or "").replace("\\", "/")
            if rel_text not in names or not _safe_entry(rel_text):
                raise RuntimeError(f"업데이트 추적 파일이 누락되었습니다: {rel_text}")
            if hashlib.sha256(archive.read(rel_text)).hexdigest() != str(expected_hash):
                raise RuntimeError(f"업데이트 파일 무결성 검증에 실패했습니다: {rel_text}")
        return "source"


def _extract_archive(package: Path, target: Path) -> None:
    with zipfile.ZipFile(package, "r") as archive:
        for item in archive.infolist():
            if not _safe_entry(item.filename):
                raise RuntimeError(f"안전하지 않은 업데이트 경로입니다: {item.filename}")
            archive.extract(item, target)


def _overlay_source(staging: Path, root: Path, rollback: Path) -> tuple[list[Path], list[Path]]:
    replaced: list[Path] = []
    created: list[Path] = []
    for source in sorted(staging.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(staging)
        if not rel.parts or rel.parts[0] in PROTECTED_ROOTS:
            continue
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            backup = rollback / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
            replaced.append(rel)
        else:
            created.append(rel)
        shutil.copy2(source, destination)
    return replaced, created


def _rollback_source(root: Path, rollback: Path, replaced: list[Path], created: list[Path]) -> None:
    for rel in reversed(created):
        try:
            (root / rel).unlink()
        except FileNotFoundError:
            pass
    for rel in reversed(replaced):
        backup = rollback / rel
        if backup.is_file():
            destination = root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)


def _owned_binary_roots(manifest: dict[str, object] | None) -> set[str]:
    roots = {"GF2Tools.exe", "GF2ToolsUpdater.exe", "_internal", "ocr", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", BINARY_MANIFEST}
    if isinstance(manifest, dict):
        raw = manifest.get("owned_roots")
        if isinstance(raw, list):
            roots.update(str(item) for item in raw if str(item).strip())
    return {name for name in roots if name and name not in PROTECTED_ROOTS and "/" not in name and "\\" not in name}


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _replace_binary(staging: Path, root: Path, rollback: Path) -> None:
    new_manifest = json.loads((staging / BINARY_MANIFEST).read_text(encoding="utf-8"))
    old_manifest = None
    old_manifest_path = root / BINARY_MANIFEST
    if old_manifest_path.is_file():
        try:
            old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old_manifest = None
    owned = _owned_binary_roots(new_manifest) | _owned_binary_roots(old_manifest)

    if rollback.exists():
        shutil.rmtree(rollback)
    rollback.mkdir(parents=True, exist_ok=True)
    backed_up: list[str] = []
    try:
        for name in sorted(owned):
            current = root / name
            if current.exists():
                _copy_path(current, rollback / name)
                backed_up.append(name)
        for name in sorted(owned):
            current = root / name
            if current.is_dir():
                shutil.rmtree(current)
            elif current.exists():
                current.unlink()
        for name in sorted(_owned_binary_roots(new_manifest)):
            source = staging / name
            if source.exists():
                _copy_path(source, root / name)
        if not (root / "GF2Tools.exe").is_file():
            raise RuntimeError("업데이트 후 GF2Tools.exe가 없습니다.")
        installed_manifest = json.loads((root / BINARY_MANIFEST).read_text(encoding="utf-8"))
        if str(installed_manifest.get("version") or "") != str(new_manifest.get("version") or ""):
            raise RuntimeError("업데이트 후 실행형 프로그램 버전 검증에 실패했습니다.")
    except Exception:
        for name in sorted(owned):
            current = root / name
            if current.is_dir():
                shutil.rmtree(current, ignore_errors=True)
            elif current.exists():
                try:
                    current.unlink()
                except OSError:
                    pass
        for name in backed_up:
            backup = rollback / name
            if backup.exists():
                _copy_path(backup, root / name)
        raise


def _restart(root: Path) -> None:
    executable = root / "GF2Tools.exe"
    if executable.is_file():
        if os.name == "nt":
            os.startfile(str(executable))  # type: ignore[attr-defined]
        else:
            subprocess.Popen([str(executable)], cwd=root, start_new_session=True)
        return
    launcher = root / "start_gfl2_tools.bat"
    if not launcher.is_file():
        return
    if os.name == "nt":
        os.startfile(str(launcher))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["bash", str(launcher)], cwd=root, start_new_session=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    package = args.package.resolve()
    update_root = root / ".gfl2_update"
    update_root.mkdir(parents=True, exist_ok=True)
    log_path = update_root / "update.log"

    def log(message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")

    try:
        if not _wait_for_process(args.parent_pid):
            raise RuntimeError("기존 GFL2 Tools 프로세스가 종료되지 않아 업데이트를 적용하지 못했습니다.")
        package_kind = _validate_archive(package, args.expected_version)
        with tempfile.TemporaryDirectory(prefix="extract-", dir=update_root) as tmp_name:
            staging = Path(tmp_name)
            _extract_archive(package, staging)
            rollback = update_root / f"rollback-v{args.expected_version}"
            if package_kind == "binary":
                _replace_binary(staging, root, rollback)
            else:
                staged_version = _read_source_version(staging / "src/gfl2tool/_version.py")
                if staged_version != args.expected_version:
                    raise RuntimeError("압축 해제된 프로그램 버전이 예상 버전과 다릅니다.")
                if rollback.exists():
                    shutil.rmtree(rollback)
                rollback.mkdir(parents=True, exist_ok=True)
                replaced: list[Path] = []
                created: list[Path] = []
                try:
                    replaced, created = _overlay_source(staging, root, rollback)
                    installed = _read_source_version(root / "src/gfl2tool/_version.py")
                    if installed != args.expected_version:
                        raise RuntimeError("업데이트 적용 후 프로그램 버전 검증에 실패했습니다.")
                except Exception:
                    _rollback_source(root, rollback, replaced, created)
                    raise
        log(f"Updated GF2Tools to v{args.expected_version} ({package_kind})")
        try:
            (update_root / "pending.json").unlink()
        except FileNotFoundError:
            pass
        try:
            package.unlink()
        except FileNotFoundError:
            pass
        if args.restart:
            _restart(root)
        return 0
    except Exception as exc:
        log(f"Update failed: {exc}")
        if args.restart:
            _restart(root)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
