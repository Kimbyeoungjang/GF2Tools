from __future__ import annotations

from dataclasses import dataclass
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable
from urllib.request import Request, urlopen
import zipfile
import io

from .. import __version__
from ..atomic_io import atomic_write_bytes, atomic_write_json


MAX_UPDATE_BYTES = 1024 * 1024 * 1024
UPDATE_DIR_NAME = ".gfl2_update"
_BINARY_MANIFEST_NAME = "release-binary.json"
_REQUIRED_RELEASE_FILES = {
    "bootstrap.py",
    "start_gfl2_tools.bat",
    "pyproject.toml",
    "src/gfl2tool/_version.py",
    "release-source.json",
}


@dataclass(frozen=True)
class ApplicationUpdateCheck:
    configured: bool
    reachable: bool
    update_available: bool
    message: str
    current_version: str
    latest_version: str = ""
    tag: str = ""
    asset_name: str = ""
    download_url: str = ""
    release_notes: str = ""


@dataclass(frozen=True)
class StagedApplicationUpdate:
    version: str
    tag: str
    asset_name: str
    package_path: Path
    sha256: str
    package_kind: str = "source"


class ApplicationUpdater:
    """Check, validate and stage a source-release update for GF2Tools.

    The running GUI never overwrites its own source tree.  It only stages a
    verified release ZIP.  ``tools/apply_program_update.py`` performs the
    overlay after the GUI process exits, preserving ``data/`` and the local
    runtime directory.
    """

    def __init__(self, project_root: str | Path, *, timeout_seconds: int = 20):
        self.project_root = Path(project_root).resolve()
        self.update_dir = self.project_root / UPDATE_DIR_NAME
        self.timeout_seconds = max(3, min(120, int(timeout_seconds)))

    @staticmethod
    def normalize_release_url(value: str) -> str:
        url = str(value or "").strip().rstrip("/")
        if not url:
            return ""
        if re.fullmatch(r"https?://[^\s]+", url, flags=re.IGNORECASE) is None:
            raise ValueError("프로그램 업데이트 주소는 http:// 또는 https://로 시작해야 합니다.")
        return url

    @staticmethod
    def _github_repo_slug(url: str) -> tuple[str, str] | None:
        match = re.match(r"^https?://github\.com/([^/]+)/([^/?#]+)", str(url or "").strip(), flags=re.IGNORECASE)
        if match is None:
            return None
        owner = match.group(1).strip()
        repo = match.group(2).strip()
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        return (owner, repo) if owner and repo else None

    @staticmethod
    def _version_key(value: str) -> tuple[tuple[int, ...], int, int]:
        text = str(value or "").strip().lower()
        core_match = re.search(r"(\d+(?:\.\d+)+)", text)
        if core_match is None:
            return (), -1, 0
        core = tuple(int(part) for part in core_match.group(1).split("."))
        suffix = text[core_match.end():]
        prerelease = re.search(r"(?:[-_.]?)\b(alpha|a|beta|b|rc)(\d*)\b", suffix)
        if prerelease is None:
            return core, 3, 0
        label = prerelease.group(1)
        rank = 0 if label in {"alpha", "a"} else 1 if label in {"beta", "b"} else 2
        number = int(prerelease.group(2) or 0)
        return core, rank, number

    @classmethod
    def version_is_newer(cls, latest: str, current: str) -> bool:
        latest_core, latest_rank, latest_pre = cls._version_key(latest)
        current_core, current_rank, current_pre = cls._version_key(current)
        if not latest_core:
            return False
        if not current_core:
            return True
        width = max(len(latest_core), len(current_core))
        latest_core = latest_core + (0,) * (width - len(latest_core))
        current_core = current_core + (0,) * (width - len(current_core))
        return (latest_core, latest_rank, latest_pre) > (current_core, current_rank, current_pre)

    def _download(
        self,
        url: str,
        *,
        accept: str = "application/octet-stream",
        progress: Callable[[str], None] | None = None,
        progress_label: str = "다운로드",
    ) -> bytes:
        request = Request(
            url,
            headers={
                "User-Agent": f"GF2Tools/{__version__} application-updater",
                "Accept": accept,
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > MAX_UPDATE_BYTES:
                        raise ValueError("업데이트 패키지가 허용 크기(1 GiB)를 초과합니다.")
                except ValueError as exc:
                    if "초과" in str(exc):
                        raise
            chunks: list[bytes] = []
            total = 0
            expected = int(length) if length and str(length).isdigit() else 0
            last_percent = -1
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPDATE_BYTES:
                    raise ValueError("업데이트 패키지가 허용 크기(1 GiB)를 초과합니다.")
                chunks.append(chunk)
                if progress is not None:
                    if expected > 0:
                        percent = min(100, int(total * 100 / expected))
                        if percent >= last_percent + 5 or percent == 100:
                            last_percent = percent
                            progress(f"{progress_label} {percent}% ({total / (1024 * 1024):.1f} MB)")
                    elif total == len(chunk) or total % (8 * 1024 * 1024) < len(chunk):
                        progress(f"{progress_label} 중… {total / (1024 * 1024):.1f} MB")
            return b"".join(chunks)

    @staticmethod
    def _release_version(raw: dict[str, Any]) -> tuple[str, str]:
        tag = str(raw.get("tag_name") or "").strip()
        name = str(raw.get("name") or "").strip()
        source = tag or name
        match = re.search(r"(?<!\d)(\d+\.\d+\.\d+(?:\.\d+)?(?:[-_.]?(?:alpha|beta|rc)\d*)?)(?!\d)", source, flags=re.IGNORECASE)
        return (match.group(1) if match else "", tag)

    @staticmethod
    def _select_release_asset(raw: dict[str, Any], version: str) -> tuple[str, str]:
        candidates: list[tuple[int, str, str]] = []
        for asset in raw.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip()
            download = str(asset.get("browser_download_url") or "").strip()
            lower = name.lower()
            if not name or not download or not lower.endswith(".zip"):
                continue
            score = 0
            if "win64" in lower and ("gf2tools" in lower or "gfl2tools" in lower or "gfl2-tools" in lower):
                score += 220
            if lower.startswith("gfl2-tools-rebuild-v"):
                score += 90
            if "gf2tools" in lower or "gfl2-tools" in lower or "gfl2tools" in lower:
                score += 40
            if "source" in lower:
                score -= 30
            if "rebuild" in lower:
                score += 10
            if version and version.lower() in lower:
                score += 30
            if "offline-table" in lower or "api" in lower:
                score -= 200
            candidates.append((score, name, download))
        if not candidates:
            raise ValueError("최신 Release에서 GF2Tools 프로그램 ZIP을 찾지 못했습니다.")
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        score, name, download = candidates[0]
        if score < 40:
            raise ValueError("최신 Release의 ZIP 중 프로그램 업데이트 패키지를 안전하게 판별하지 못했습니다.")
        return name, download

    def _latest_release(self, release_url: str) -> dict[str, str]:
        url = self.normalize_release_url(release_url)
        if not url:
            raise ValueError("프로그램 업데이트 Release 주소가 설정되지 않았습니다.")
        if url.lower().endswith(".zip"):
            name = url.rsplit("/", 1)[-1]
            version_match = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?(?:[-_.]?(?:alpha|beta|rc)\d*)?)", name, flags=re.IGNORECASE)
            version = version_match.group(1) if version_match else ""
            return {"version": version, "tag": "", "asset_name": name, "download_url": url, "release_notes": ""}
        slug = self._github_repo_slug(url)
        if slug is None:
            raise ValueError("GitHub Release 주소에서 owner/repo를 확인할 수 없습니다.")
        owner, repo = slug
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        raw = json.loads(self._download(api_url, accept="application/vnd.github+json").decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("GitHub latest Release 응답이 올바르지 않습니다.")
        version, tag = self._release_version(raw)
        if not version:
            raise ValueError("최신 Release 태그에서 프로그램 버전을 확인하지 못했습니다.")
        asset_name, download_url = self._select_release_asset(raw, version)
        return {
            "version": version,
            "tag": tag,
            "asset_name": asset_name,
            "download_url": download_url,
            "release_notes": str(raw.get("body") or "").strip(),
        }

    def check_for_update(self, release_url: str) -> ApplicationUpdateCheck:
        url = self.normalize_release_url(release_url)
        if not url:
            return ApplicationUpdateCheck(False, False, False, "프로그램 자동 업데이트 주소가 아직 설정되지 않았습니다.", __version__)
        try:
            latest = self._latest_release(url)
            latest_version = str(latest.get("version") or "").strip()
            available = self.version_is_newer(latest_version, __version__)
            message = (
                f"새 GFL2 Tools 버전이 있습니다: v{__version__} → v{latest_version}"
                if available
                else f"GFL2 Tools v{__version__}이 최신 버전입니다."
            )
            return ApplicationUpdateCheck(
                True,
                True,
                available,
                message,
                __version__,
                latest_version,
                str(latest.get("tag") or ""),
                str(latest.get("asset_name") or ""),
                str(latest.get("download_url") or ""),
                str(latest.get("release_notes") or ""),
            )
        except Exception as exc:
            return ApplicationUpdateCheck(True, False, False, f"프로그램 업데이트 서버에 접근하지 못했습니다.\n{exc}", __version__)

    @staticmethod
    def _zip_entry_is_safe(name: str) -> bool:
        text = str(name or "").replace("\\", "/")
        if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
            return False
        return ".." not in Path(text).parts

    @staticmethod
    def _version_from_source(source: bytes) -> str:
        tree = ast.parse(source.decode("utf-8"), filename="src/gfl2tool/_version.py")
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
        return ""

    @staticmethod
    def _binary_manifest(archive: zipfile.ZipFile) -> dict[str, Any] | None:
        try:
            payload = json.loads(archive.read(_BINARY_MANIFEST_NAME).decode("utf-8"))
        except KeyError:
            return None
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("업데이트 ZIP의 release-binary.json이 올바르지 않습니다.") from exc
        if not isinstance(payload, dict):
            raise ValueError("업데이트 ZIP의 binary manifest가 올바르지 않습니다.")
        return payload

    @classmethod
    def validate_release_package(cls, payload: bytes, *, expected_version: str = "") -> tuple[str, str]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload), "r")
        except zipfile.BadZipFile as exc:
            raise ValueError("다운로드한 프로그램 업데이트 ZIP이 손상되었습니다.") from exc
        with archive:
            names = {info.filename for info in archive.infolist() if not info.is_dir()}
            unsafe = [name for name in names if not cls._zip_entry_is_safe(name)]
            if unsafe:
                raise ValueError(f"업데이트 ZIP에 안전하지 않은 경로가 있습니다: {unsafe[0]}")

            binary_manifest = cls._binary_manifest(archive)
            if binary_manifest is not None:
                if int(binary_manifest.get("schema") or 0) != 1 or str(binary_manifest.get("kind") or "") != "gfl2-tools-windows-binary":
                    raise ValueError("지원하지 않는 프로그램 binary manifest입니다.")
                binary_version = str(binary_manifest.get("version") or "").strip()
                if not binary_version:
                    raise ValueError("프로그램 binary manifest에 버전이 없습니다.")
                if expected_version and binary_version != expected_version:
                    raise ValueError(f"Release 버전(v{expected_version})과 ZIP 내부 버전(v{binary_version})이 다릅니다.")
                required = {"GF2Tools.exe", "GF2ToolsUpdater.exe", "ocr/engine/tesseract.exe", "LICENSE", _BINARY_MANIFEST_NAME}
                missing = sorted(required - names)
                if missing:
                    raise ValueError(f"프로그램 실행형 ZIP에 필수 파일이 없습니다: {', '.join(missing)}")
                files = binary_manifest.get("files")
                if not isinstance(files, dict) or not files:
                    raise ValueError("프로그램 실행형 ZIP의 무결성 목록이 비어 있습니다.")
                for rel, expected_hash in files.items():
                    rel_text = str(rel or "").replace("\\", "/")
                    if rel_text not in names or not cls._zip_entry_is_safe(rel_text):
                        raise ValueError(f"프로그램 실행형 ZIP의 추적 파일이 누락되었습니다: {rel_text}")
                    actual_hash = hashlib.sha256(archive.read(rel_text)).hexdigest()
                    if actual_hash != str(expected_hash):
                        raise ValueError(f"프로그램 실행형 ZIP의 파일 무결성 검증에 실패했습니다: {rel_text}")
                return binary_version, hashlib.sha256(payload).hexdigest()

            missing = sorted(_REQUIRED_RELEASE_FILES - names)
            if missing:
                raise ValueError(f"프로그램 업데이트 ZIP에 필수 파일이 없습니다: {', '.join(missing)}")
            source_version = cls._version_from_source(archive.read("src/gfl2tool/_version.py"))
            if not source_version:
                raise ValueError("업데이트 ZIP의 프로그램 버전을 읽지 못했습니다.")
            if expected_version and source_version != expected_version:
                raise ValueError(f"Release 버전(v{expected_version})과 ZIP 내부 버전(v{source_version})이 다릅니다.")
            try:
                manifest = json.loads(archive.read("release-source.json").decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("업데이트 ZIP의 release-source.json이 올바르지 않습니다.") from exc
            if not isinstance(manifest, dict) or int(manifest.get("schema") or 0) != 2:
                raise ValueError("지원하지 않는 release-source manifest입니다.")
            if str(manifest.get("version") or "") != source_version:
                raise ValueError("업데이트 ZIP의 source manifest 버전이 일치하지 않습니다.")
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                raise ValueError("업데이트 ZIP의 source 무결성 목록이 비어 있습니다.")
            for rel, expected_hash in files.items():
                rel_text = str(rel or "").replace("\\", "/")
                if rel_text not in names or not cls._zip_entry_is_safe(rel_text):
                    raise ValueError(f"업데이트 ZIP의 추적 파일이 누락되었습니다: {rel_text}")
                actual_hash = hashlib.sha256(archive.read(rel_text)).hexdigest()
                if actual_hash != str(expected_hash):
                    raise ValueError(f"업데이트 ZIP의 파일 무결성 검증에 실패했습니다: {rel_text}")
        return source_version, hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _package_kind(payload: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            return "binary" if _BINARY_MANIFEST_NAME in archive.namelist() else "source"

    def stage_latest(
        self,
        release_url: str,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> StagedApplicationUpdate:
        if progress is not None:
            progress("최신 Release 정보 확인 중…")
        latest = self._latest_release(release_url)
        version = str(latest.get("version") or "").strip()
        if not self.version_is_newer(version, __version__):
            raise ValueError(f"현재 v{__version__}보다 새로운 프로그램 Release가 아닙니다.")
        if progress is not None:
            progress("업데이트 ZIP 다운로드 시작…")
        payload = self._download(
            str(latest.get("download_url") or ""),
            progress=progress,
            progress_label="업데이트 다운로드",
        )
        if progress is not None:
            progress("업데이트 ZIP 무결성 검증 중…")
        source_version, digest = self.validate_release_package(payload, expected_version=version)
        package_kind = self._package_kind(payload)
        self.update_dir.mkdir(parents=True, exist_ok=True)
        package_path = self.update_dir / f"gfl2-tools-update-v{source_version}.zip"
        if progress is not None:
            progress("검증된 업데이트 파일 저장 중…")
        atomic_write_bytes(package_path, payload)
        atomic_write_json(
            self.update_dir / "pending.json",
            {
                "schema": 1,
                "current_version": __version__,
                "version": source_version,
                "tag": str(latest.get("tag") or ""),
                "asset_name": str(latest.get("asset_name") or ""),
                "package": package_path.name,
                "sha256": digest,
                "package_kind": package_kind,
            },
            ensure_ascii=False,
            indent=2,
        )
        if progress is not None:
            progress("업데이트 적용 준비 완료")
        return StagedApplicationUpdate(
            source_version,
            str(latest.get("tag") or ""),
            str(latest.get("asset_name") or ""),
            package_path,
            digest,
            package_kind,
        )

    def launch_staged_update(self, staged: StagedApplicationUpdate, *, parent_pid: int | None = None) -> None:
        package_path = Path(staged.package_path).resolve()
        try:
            package_path.relative_to(self.update_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("업데이트 패키지가 허용된 스테이징 폴더 밖에 있습니다.") from exc

        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            helper = self.project_root / "GF2ToolsUpdater.exe"
            if not helper.is_file():
                raise RuntimeError("프로그램 업데이트 적용 실행 파일이 없습니다.")
            self.update_dir.mkdir(parents=True, exist_ok=True)
            staged_helper = self.update_dir / "GF2ToolsUpdater.exe"
            shutil.copy2(helper, staged_helper)
            command = [
                str(staged_helper),
                "--root", str(self.project_root),
                "--package", str(package_path),
                "--parent-pid", str(int(parent_pid or os.getpid())),
                "--expected-version", staged.version,
                "--restart",
            ]
        else:
            helper = self.project_root / "tools" / "apply_program_update.py"
            if not helper.is_file():
                raise RuntimeError("프로그램 업데이트 적용 도구가 없습니다.")
            command = [
                sys.executable, str(helper),
                "--root", str(self.project_root),
                "--package", str(package_path),
                "--parent-pid", str(int(parent_pid or os.getpid())),
                "--expected-version", staged.version,
                "--restart",
            ]

        kwargs: dict[str, Any] = {"cwd": str(self.project_root)}
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
