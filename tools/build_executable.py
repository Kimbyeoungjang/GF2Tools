from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

import package_release


ROOT = Path(__file__).resolve().parents[1]
BINARY_MANIFEST = "release-binary.json"
OWNED_ROOTS = [
    "GF2Tools.exe",
    "GF2ToolsUpdater.exe",
    "_internal",
    "ocr",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_LICENSES",
    BINARY_MANIFEST,
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_windows_x64() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows EXE 릴리스는 Windows에서 build_release.bat으로 빌드해야 합니다.")
    if platform.machine().lower() not in {"amd64", "x86_64"} or sys.maxsize <= 2**32:
        raise RuntimeError("GF2Tools 공식 EXE는 64-bit Windows/Python 빌드만 지원합니다.")


def _pyinstaller(*args: str) -> None:
    command = [sys.executable, "-m", "PyInstaller", *args]
    subprocess.run(command, cwd=ROOT, check=True)


def _version_file(version: str, target: Path) -> Path:
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    a, b, c, d = parts[:4]
    payload = f'''VSVersionInfo(\n  ffi=FixedFileInfo(filevers=({a},{b},{c},{d}), prodvers=({a},{b},{c},{d}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n  kids=[StringFileInfo([StringTable('040904B0', [\n    StringStruct('CompanyName', 'GFL2 Tools'),\n    StringStruct('FileDescription', 'GFL2 Tools'),\n    StringStruct('FileVersion', '{version}'),\n    StringStruct('InternalName', 'GF2Tools'),\n    StringStruct('LegalCopyright', 'GNU GPL v3'),\n    StringStruct('OriginalFilename', 'GF2Tools.exe'),\n    StringStruct('ProductName', 'GFL2 Tools'),\n    StringStruct('ProductVersion', '{version}')\n  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n'''
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return target


def _copy_dist_license(distribution_name: str, target: Path) -> bool:
    try:
        dist = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return False
    copied = False
    for rel in dist.files or []:
        rel_text = str(rel).replace("\\", "/")
        name = Path(rel_text).name.lower()
        if "licenses/" not in rel_text.lower() and not name.startswith(("license", "copying", "notice")):
            continue
        source = Path(dist.locate_file(rel))
        if not source.is_file():
            continue
        safe = f"{distribution_name}-{Path(rel_text).name}"
        shutil.copy2(source, target / safe)
        copied = True
    return copied


def _collect_licenses(bundle: Path) -> None:
    target = bundle / "THIRD_PARTY_LICENSES"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "licenses" / "LGPL-3.0.txt", target / "Qt-LGPL-3.0.txt")
    shutil.copy2(ROOT / "licenses" / "Pillow-LICENSE.txt", target / "Pillow-LICENSE.txt")
    shutil.copy2(ROOT / "licenses" / "Apache-2.0.txt", target / "Tesseract-Apache-2.0.txt")

    for dist_name in ("PySide6-Essentials", "shiboken6", "PyInstaller"):
        _copy_dist_license(dist_name, target)

    python_license_candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
    ]
    for source in python_license_candidates:
        if source.is_file():
            shutil.copy2(source, target / "Python-LICENSE.txt")
            break




def _bundle_ocr(bundle: Path) -> None:
    runtime_ocr = ROOT / ".gfl2_runtime" / "ocr"
    engine = runtime_ocr / "engine"
    executable = engine / "tesseract.exe"
    tessdata = runtime_ocr / "tessdata"
    if not executable.is_file() or not (tessdata / "kor.traineddata").is_file() or not (tessdata / "eng.traineddata").is_file():
        subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--repair-ocr", "--no-launch"],
            cwd=ROOT,
            check=True,
        )
    if not executable.is_file():
        raise RuntimeError("공식 EXE에 포함할 Tesseract OCR 엔진을 준비하지 못했습니다.")
    target_engine = bundle / "ocr" / "engine"
    shutil.copytree(engine, target_engine)
    target_tessdata = target_engine / "tessdata"
    target_tessdata.mkdir(parents=True, exist_ok=True)
    for name in ("kor.traineddata", "eng.traineddata"):
        source = tessdata / name
        if not source.is_file():
            raise RuntimeError(f"공식 EXE에 포함할 OCR 언어 데이터가 없습니다: {name}")
        shutil.copy2(source, target_tessdata / name)

def _write_binary_manifest(bundle: Path, version: str) -> Path:
    files: dict[str, str] = {}
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == BINARY_MANIFEST:
            continue
        files[path.relative_to(bundle).as_posix()] = sha256(path)
    payload = {
        "schema": 1,
        "kind": "gfl2-tools-windows-binary",
        "version": version,
        "architecture": "win64",
        "entrypoint": "GF2Tools.exe",
        "updater": "GF2ToolsUpdater.exe",
        "owned_roots": OWNED_ROOTS,
        "files": files,
    }
    target = bundle / BINARY_MANIFEST
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _zip_bundle(bundle: Path, release_dir: Path, version: str) -> tuple[Path, Path, Path]:
    archive = release_dir / f"GF2Tools-v{version}-win64.zip"
    checksum = release_dir / f"GF2Tools-v{version}-win64.sha256"
    release_manifest = release_dir / f"GF2Tools-v{version}-win64.release.json"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle).as_posix())
    digest = sha256(archive)
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    release_manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "kind": "windows-binary",
                "version": version,
                "archive": archive.name,
                "sha256": digest,
                "entrypoint": "GF2Tools.exe",
                "binary_manifest": BINARY_MANIFEST,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return archive, checksum, release_manifest


def build(output: Path, *, skip_tests: bool = False) -> dict[str, str]:
    _require_windows_x64()
    version = package_release.package_version()
    report = package_release.audit(version)
    if report["failures"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))
    package_release.refresh_source_manifest(version)
    if not skip_tests:
        package_release.run_tests()

    work = ROOT / "build" / "pyinstaller-release"
    shutil.rmtree(work, ignore_errors=True)
    bundle_root = output / f"GF2Tools-v{version}-win64"
    shutil.rmtree(bundle_root, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    version_file = _version_file(version, work / "version_info.txt")
    main_dist = work / "main-dist"
    main_work = work / "main-work"
    _pyinstaller(
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "GF2Tools",
        "--contents-directory", "_internal",
        "--paths", str(ROOT / "src"),
        "--distpath", str(main_dist),
        "--workpath", str(main_work),
        "--specpath", str(work / "spec"),
        "--version-file", str(version_file),
        "--collect-submodules", "PySide6",
        "--add-data", f"{ROOT / 'src' / 'gfl2tool' / 'reference_data'}{os.pathsep}gfl2tool/reference_data",
        "--add-data", f"{ROOT / 'src' / 'gfl2tool' / 'resources'}{os.pathsep}gfl2tool/resources",
        str(ROOT / "tools" / "frozen_main.py"),
    )
    built_main = main_dist / "GF2Tools"
    if not (built_main / "GF2Tools.exe").is_file():
        raise RuntimeError("PyInstaller가 GF2Tools.exe를 생성하지 못했습니다.")
    shutil.copytree(built_main, bundle_root)

    updater_dist = work / "updater-dist"
    _pyinstaller(
        "--noconfirm", "--clean", "--windowed", "--onefile",
        "--name", "GF2ToolsUpdater",
        "--distpath", str(updater_dist),
        "--workpath", str(work / "updater-work"),
        "--specpath", str(work / "updater-spec"),
        str(ROOT / "tools" / "apply_program_update.py"),
    )
    updater = updater_dist / "GF2ToolsUpdater.exe"
    if not updater.is_file():
        raise RuntimeError("PyInstaller가 GF2ToolsUpdater.exe를 생성하지 못했습니다.")
    shutil.copy2(updater, bundle_root / updater.name)

    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, bundle_root / name)
    _collect_licenses(bundle_root)
    _bundle_ocr(bundle_root)
    _write_binary_manifest(bundle_root, version)
    archive, checksum, binary_release_manifest = _zip_bundle(bundle_root, output, version)

    source_dir = output / "source"
    source_zip, source_sha, source_manifest = package_release.package(version, source_dir)
    return {
        "version": version,
        "exe": str(bundle_root / "GF2Tools.exe"),
        "bundle": str(bundle_root),
        "binary_zip": str(archive),
        "binary_sha256": str(checksum),
        "binary_release_manifest": str(binary_release_manifest),
        "source_zip": str(source_zip),
        "source_sha256": str(source_sha),
        "source_release_manifest": str(source_manifest),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the official Windows executable release for GFL2 Tools")
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    try:
        result = build(args.output.resolve(), skip_tests=args.skip_tests)
    except Exception as exc:
        print(f"[GFL2 Tools] EXE release build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
