from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import zipfile

from .. import reference
from ..atomic_io import atomic_write_bytes, atomic_write_json


REMOTE_CONFIG_NAME = "remote-api.json"
REMOTE_CACHE_DIR = "remote-api-cache"
ACTIVE_SITE_DIR = "site"
PACKAGE_SCHEMA_ID = "gfl2-tools-offline-data-package"
PACKAGE_SCHEMA_VERSION = 2
_DATASET_KEY_RE = re.compile(r"^[a-z0-9_]+$")
PAGES_SUPPLEMENTAL_DATASETS = ("unclassified_polarity_plans",)
DEFAULT_GITHUB_RELEASE_URL = "https://github.com/Kimbyeoungjang/GF2Tools-api/releases"
DEFAULT_STATIC_API_URL = "https://raw.githubusercontent.com/Kimbyeoungjang/GF2Tools-api/main"

DEFAULT_REMOTE_API_CONFIG: dict[str, Any] = {
    "schema": 4,
    "enabled": True,
    "github_release_url": DEFAULT_GITHUB_RELEASE_URL,
    "pages_base_url": DEFAULT_STATIC_API_URL,
    # Compatibility alias for the static API root.
    "base_url": DEFAULT_STATIC_API_URL,
    "timeout_seconds": 20,
    "active_provider": "",
    "data_version": "",
    "game_version": "",
}


@dataclass(frozen=True)
class RemoteCatalogStartupResult:
    configured: bool
    config_path: Path
    cache_dir: Path
    message: str


@dataclass(frozen=True)
class RemoteCatalogSyncResult:
    configured: bool
    changed: bool
    message: str
    provider: str = ""
    data_version: str = ""
    game_version: str = ""


@dataclass(frozen=True)
class RemoteCatalogUpdateCheck:
    configured: bool
    reachable: bool
    update_available: bool
    message: str
    provider: str = ""
    current_game_version: str = ""
    latest_game_version: str = ""
    current_data_version: str = ""
    latest_data_version: str = ""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(value: object) -> str:
    text = str(value or "").replace("\\", "/").lstrip("/")
    if not text or text.startswith("../") or "/../" in f"/{text}/":
        raise ValueError(f"안전하지 않은 패키지 경로입니다: {value}")
    return text


def _dataset_items(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    return [dict(item) for item in payload.get("items") or [] if isinstance(item, dict)]


class RemoteCatalogBootstrap:
    """Program-data delivery boundary shared by Release, Pages and offline ZIP.

    Provider priority for explicit synchronization is:
    1) configured GitHub Release direct/latest-download URL,
    2) configured GitHub Pages static REST mirror,
    3) user-selected offline package through :meth:`install_offline_package`.

    Both network providers and the offline package activate the same static-site
    layout under ``data/remote-api-cache/site`` and then generate the legacy
    normalized reference files consumed by the rest of GF2Tools.
    """

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)
        self.config_path = self.data_root / REMOTE_CONFIG_NAME
        self.cache_dir = self.data_root / REMOTE_CACHE_DIR
        self.site_dir = self.cache_dir / ACTIVE_SITE_DIR

    def ensure_placeholder(self) -> RemoteCatalogStartupResult:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            atomic_write_json(self.config_path, DEFAULT_REMOTE_API_CONFIG, ensure_ascii=False, indent=2)
        payload = self.load_config()
        changed = False
        schema = int(payload.get("schema") or 0)
        # v0.87-v0.90 used base_url as the only REST address. v0.91 introduced
        # two provider fields but left their defaults blank. From schema 4 onward
        # the official repository is the default, while explicit older custom
        # addresses are preserved.
        if schema < 4:
            legacy_pages = str(payload.get("pages_base_url") or payload.get("base_url") or "").strip().rstrip("/")
            legacy_github = str(payload.get("github_release_url") or "").strip().rstrip("/")
            migrated = dict(DEFAULT_REMOTE_API_CONFIG)
            migrated["github_release_url"] = legacy_github or DEFAULT_GITHUB_RELEASE_URL
            migrated["pages_base_url"] = legacy_pages or DEFAULT_STATIC_API_URL
            migrated["base_url"] = migrated["pages_base_url"]
            migrated["enabled"] = True
            migrated["data_version"] = str(payload.get("data_version") or "")
            migrated["game_version"] = str(payload.get("game_version") or "")
            payload = migrated
            changed = True
        for key, default in DEFAULT_REMOTE_API_CONFIG.items():
            if key not in payload:
                payload[key] = json.loads(json.dumps(default))
                changed = True
        if changed:
            atomic_write_json(self.config_path, payload, ensure_ascii=False, indent=2)
        configured = bool(self.github_release_url() or self.pages_base_url())
        version = str(payload.get("data_version") or "").strip()
        message = (
            f"프로그램 데이터 {version} · 다운로드 주소 설정됨" if configured and version
            else "프로그램 데이터 다운로드 주소 설정됨" if configured
            else "GitHub Release / 정적 API 주소 미설정"
        )
        return RemoteCatalogStartupResult(configured, self.config_path, self.cache_dir, message)

    def load_config(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return json.loads(json.dumps(DEFAULT_REMOTE_API_CONFIG))
        return payload if isinstance(payload, dict) else json.loads(json.dumps(DEFAULT_REMOTE_API_CONFIG))

    @staticmethod
    def _normalize_url(value: str) -> str:
        url = str(value or "").strip().rstrip("/")
        if url and re.fullmatch(r"https?://[^\s]+", url, flags=re.IGNORECASE) is None:
            raise ValueError("다운로드 주소는 http:// 또는 https://로 시작하는 올바른 주소여야 합니다.")
        return url

    def github_release_url(self) -> str:
        return str(self.load_config().get("github_release_url") or "").strip()

    def pages_base_url(self) -> str:
        payload = self.load_config()
        return str(payload.get("pages_base_url") or payload.get("base_url") or "").strip().rstrip("/")

    def base_url(self) -> str:
        """Compatibility alias for the Pages REST root."""
        return self.pages_base_url()

    def set_provider_urls(self, github_release_url: str, pages_base_url: str) -> tuple[str, str]:
        github = self._normalize_url(github_release_url)
        pages = self._normalize_url(pages_base_url)
        payload = self.load_config()
        payload.update({
            "schema": int(DEFAULT_REMOTE_API_CONFIG["schema"]),
            "github_release_url": github,
            "pages_base_url": pages,
            "base_url": pages,
            "enabled": bool(github or pages),
            "timeout_seconds": int(payload.get("timeout_seconds") or DEFAULT_REMOTE_API_CONFIG["timeout_seconds"]),
        })
        self.data_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.config_path, payload, ensure_ascii=False, indent=2)
        return github, pages

    def set_base_url(self, value: str) -> str:
        github = self.github_release_url()
        _github, pages = self.set_provider_urls(github, value)
        return pages

    def startup_sync(self) -> RemoteCatalogStartupResult:
        # This method only prepares/migrates local state. MainWindow schedules
        # sync_now() on a worker after the first frame when a provider is configured.
        return self.ensure_placeholder()

    def _timeout(self) -> int:
        try:
            return max(3, min(120, int(self.load_config().get("timeout_seconds") or 20)))
        except (TypeError, ValueError):
            return 20

    def _download(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": "GF2Tools-program-data/1", "Accept": "application/vnd.github+json"})
        with urlopen(request, timeout=self._timeout()) as response:
            return response.read()

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
    def _version_tuple(value: str) -> tuple[int, ...]:
        values = [int(part) for part in re.findall(r"\d+", str(value or ""))]
        return tuple(values)

    @classmethod
    def _version_is_newer(cls, latest: str, current: str) -> bool:
        latest_parts = cls._version_tuple(latest)
        current_parts = cls._version_tuple(current)
        if not latest_parts:
            return False
        if not current_parts:
            return True
        width = max(len(latest_parts), len(current_parts))
        return latest_parts + (0,) * (width - len(latest_parts)) > current_parts + (0,) * (width - len(current_parts))

    def _installed_version_info(self) -> dict[str, str]:
        payload: dict[str, Any] = {}
        local = self.data_root / "reference_data" / "program_version.json"
        if local.is_file():
            try:
                raw = json.loads(local.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    payload = raw
            except (OSError, UnicodeError, json.JSONDecodeError):
                payload = {}
        if not payload:
            try:
                payload = reference.program_version()
            except Exception:
                payload = {}
        config = self.load_config()
        return {
            "game_version": str(payload.get("game_version") or config.get("game_version") or "").strip(),
            "data_version": str(payload.get("data_version") or config.get("data_version") or "").strip(),
        }

    def _latest_release(self, release_url: str) -> dict[str, str]:
        url = str(release_url or "").strip()
        if url.lower().endswith(".zip"):
            return {"download_url": url, "game_version": "", "data_version": "", "tag": ""}
        slug = self._github_repo_slug(url)
        if slug is None:
            raise ValueError("GitHub Release 주소에서 저장소 owner/repo를 확인할 수 없습니다.")
        owner, repo = slug
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        raw = json.loads(self._download(api_url).decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("GitHub 최신 Release 응답이 올바르지 않습니다.")
        tag = str(raw.get("tag_name") or "").strip()
        game_version = tag[1:] if tag.lower().startswith("v") else tag
        candidates: list[tuple[int, str, str]] = []
        for asset in raw.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip()
            download = str(asset.get("browser_download_url") or "").strip()
            if not download or not name.lower().endswith(".zip"):
                continue
            score = 0
            lower = name.lower()
            if lower.startswith("gfl2-gf2tools-offline-table"):
                score += 100
            if "offline" in lower:
                score += 20
            if "table" in lower:
                score += 10
            candidates.append((score, name, download))
        if not candidates:
            raise ValueError("최신 GitHub Release에서 GF2Tools 오프라인 데이터 ZIP을 찾지 못했습니다.")
        candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
        _score, asset_name, download_url = candidates[0]
        return {
            "download_url": download_url,
            "game_version": game_version,
            "data_version": "",
            "tag": tag,
            "asset_name": asset_name,
        }

    def _latest_static_info(self, base_url: str) -> dict[str, str]:
        root = str(base_url or "").strip().rstrip("/") + "/"
        latest = json.loads(self._download(urljoin(root, "latest.json")).decode("utf-8"))
        if not isinstance(latest, dict):
            raise ValueError("정적 API latest.json 형식이 올바르지 않습니다.")
        game_version = str(latest.get("game_version") or "").strip()
        if not game_version:
            try:
                game = json.loads(self._download(urljoin(root, "api/v1/game_version.json")).decode("utf-8"))
            except Exception:
                game = {}
            if isinstance(game, dict):
                game_version = str(game.get("game_version") or "").strip()
        return {
            "game_version": game_version,
            "data_version": str(latest.get("data_version") or "").strip(),
        }

    def check_for_update(self) -> RemoteCatalogUpdateCheck:
        self.ensure_placeholder()
        github = self.github_release_url()
        pages = self.pages_base_url()
        current = self._installed_version_info()
        errors: list[str] = []
        if github:
            try:
                latest = self._latest_release(github)
                latest_game = str(latest.get("game_version") or "").strip()
                available = self._version_is_newer(latest_game, current["game_version"])
                message = (
                    f"새 게임 데이터 버전이 있습니다: {current['game_version'] or '미설치'} → {latest_game}"
                    if available else f"최신 게임 데이터입니다: {current['game_version'] or latest_game or '버전 미상'}"
                )
                return RemoteCatalogUpdateCheck(True, True, available, message, "GitHub Release", current["game_version"], latest_game, current["data_version"], str(latest.get("data_version") or ""))
            except Exception as exc:
                errors.append(f"GitHub Release: {exc}")
        if pages:
            try:
                latest = self._latest_static_info(pages)
                latest_game = str(latest.get("game_version") or "").strip()
                available = self._version_is_newer(latest_game, current["game_version"])
                message = (
                    f"새 게임 데이터 버전이 있습니다: {current['game_version'] or '미설치'} → {latest_game}"
                    if available else f"최신 게임 데이터입니다: {current['game_version'] or latest_game or '버전 미상'}"
                )
                return RemoteCatalogUpdateCheck(True, True, available, message, "정적 API", current["game_version"], latest_game, current["data_version"], str(latest.get("data_version") or ""))
            except Exception as exc:
                errors.append(f"정적 API: {exc}")
        configured = bool(github or pages)
        message = "프로그램 데이터 서버에 접근하지 못했습니다. 데이터 동기화에서 오프라인 패키지를 직접 가져와 주세요."
        if errors:
            message += "\n" + "\n".join(errors)
        return RemoteCatalogUpdateCheck(configured, False, False, message, "", current["game_version"], "", current["data_version"], "")

    def sync_now(self) -> RemoteCatalogSyncResult:
        self.ensure_placeholder()
        errors: list[str] = []
        github = self.github_release_url()
        pages = self.pages_base_url()
        if github:
            try:
                release = self._latest_release(github)
                payload = self._download(str(release.get("download_url") or ""))
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
                    handle.write(payload)
                    temp_path = Path(handle.name)
                try:
                    result = self.install_offline_package(temp_path, provider=f"GitHub Release {release.get('tag') or ''}".strip())
                finally:
                    temp_path.unlink(missing_ok=True)
                return result
            except Exception as exc:
                errors.append(f"GitHub Release: {exc}")
        if pages:
            try:
                return self._sync_pages(pages)
            except Exception as exc:
                errors.append(f"정적 API: {exc}")
        if not github and not pages:
            return RemoteCatalogSyncResult(False, False, "GitHub Release / 정적 API 다운로드 주소가 아직 설정되지 않았습니다.")
        return RemoteCatalogSyncResult(True, False, "자동 다운로드에 실패했습니다.\n" + "\n".join(errors))

    def install_offline_package(self, package_path: str | Path, *, provider: str = "오프라인 패키지") -> RemoteCatalogSyncResult:
        package_path = Path(package_path)
        if not package_path.is_file():
            raise ValueError("오프라인 데이터 패키지를 찾을 수 없습니다.")
        with zipfile.ZipFile(package_path, "r") as archive:
            if "gfl2tools-package.json" not in archive.namelist():
                raise ValueError("gfl2tools-package.json이 없는 GF2Tools 오프라인 패키지입니다.")
            package = json.loads(archive.read("gfl2tools-package.json").decode("utf-8"))
            if not isinstance(package, dict) or package.get("schema_id") != PACKAGE_SCHEMA_ID:
                raise ValueError("지원하지 않는 오프라인 패키지 schema입니다.")
            if int(package.get("schema_version") or 0) != PACKAGE_SCHEMA_VERSION:
                raise ValueError("지원하지 않는 오프라인 패키지 schema version입니다.")
            files = package.get("files") or []
            if not isinstance(files, list) or not files:
                raise ValueError("오프라인 패키지 파일 manifest가 비어 있습니다.")
            names = set(archive.namelist())
            for entry in files:
                if not isinstance(entry, dict):
                    raise ValueError("오프라인 패키지 파일 manifest가 올바르지 않습니다.")
                rel = _safe_relative(entry.get("path"))
                if rel not in names:
                    raise ValueError(f"패키지 파일이 누락되었습니다: {rel}")
                raw = archive.read(rel)
                if int(entry.get("bytes") or -1) != len(raw):
                    raise ValueError(f"패키지 파일 크기가 일치하지 않습니다: {rel}")
                if str(entry.get("sha256") or "").lower() != _sha256_bytes(raw):
                    raise ValueError(f"패키지 SHA-256 검증에 실패했습니다: {rel}")

            with tempfile.TemporaryDirectory(prefix="gfl2-program-data-") as temp:
                staging = Path(temp) / "site"
                staging.mkdir(parents=True)
                for entry in files:
                    rel = _safe_relative(entry.get("path"))
                    target = staging / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_bytes(target, archive.read(rel), durable=False)
                # Keep package metadata alongside the mirror for diagnostics/version display.
                atomic_write_json(
                    staging / "gfl2tools-package.json", package, ensure_ascii=False, indent=2, durable=False
                )
                return self._activate_site(
                    staging,
                    provider=provider,
                    data_version=str(package.get("data_version") or ""),
                    pages_base_url="",
                )

    def _sync_pages(self, base_url: str) -> RemoteCatalogSyncResult:
        root = base_url.rstrip("/") + "/"
        latest_raw = self._download(urljoin(root, "latest.json"))
        latest = json.loads(latest_raw.decode("utf-8"))
        if not isinstance(latest, dict):
            raise ValueError("latest.json 형식이 올바르지 않습니다.")
        data_version = str(latest.get("data_version") or "").strip()
        manifest_rel = _safe_relative(latest.get("manifest") or "api/v1/manifest.json")
        manifest_raw = self._download(urljoin(root, manifest_rel))
        manifest = json.loads(manifest_raw.decode("utf-8"))
        if not isinstance(manifest, dict) or str(manifest.get("data_version") or "") != data_version:
            raise ValueError("latest.json과 manifest의 data_version이 일치하지 않습니다.")

        current = str(self.load_config().get("data_version") or "")
        if current == data_version and (self.site_dir / "api/v1/dolls.json").is_file():
            return RemoteCatalogSyncResult(True, False, f"최신 프로그램 데이터입니다: {data_version}", "정적 API", data_version)

        bundles = manifest.get("bundles") if isinstance(manifest.get("bundles"), dict) else {}
        core = bundles.get("core_gzip") if isinstance(bundles, dict) else None
        if not isinstance(core, dict):
            raise ValueError("manifest에서 권장 core_gzip bundle을 찾지 못했습니다.")
        core_rel = _safe_relative(core.get("path"))
        # Bundle paths are relative to api/v1/, matching the exported static tree.
        core_url = urljoin(root, "api/v1/" + core_rel)
        core_raw = self._download(core_url)
        if str(core.get("sha256") or "").lower() != _sha256_bytes(core_raw):
            raise ValueError("core-data SHA-256 검증에 실패했습니다.")
        decoded = json.loads(gzip.decompress(core_raw).decode("utf-8"))
        if not isinstance(decoded, dict) or str(decoded.get("data_version") or "") != data_version:
            raise ValueError("core-data 형식 또는 data_version이 올바르지 않습니다.")
        datasets = decoded.get("datasets")
        if not isinstance(datasets, dict):
            raise ValueError("core-data datasets가 없습니다.")

        with tempfile.TemporaryDirectory(prefix="gfl2-pages-data-") as temp:
            staging = Path(temp) / "site"
            api = staging / "api/v1"
            api.mkdir(parents=True)
            atomic_write_bytes(staging / "latest.json", latest_raw, durable=False)
            atomic_write_bytes(api / "manifest.json", manifest_raw, durable=False)
            bundle_target = api / core_rel
            bundle_target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(bundle_target, core_raw, durable=False)
            for key, payload in datasets.items():
                dataset_key = str(key or "").strip().lower()
                if _DATASET_KEY_RE.fullmatch(dataset_key) is None or not isinstance(payload, dict):
                    continue
                atomic_write_json(api / f"{dataset_key}.json", payload, ensure_ascii=False, indent=2)

            manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
            for dataset_key in PAGES_SUPPLEMENTAL_DATASETS:
                if (api / f"{dataset_key}.json").is_file():
                    continue
                metadata = manifest_files.get(dataset_key) if isinstance(manifest_files, dict) else None
                if not isinstance(metadata, dict):
                    continue
                relative = _safe_relative(metadata.get("path") or f"{dataset_key}.json")
                if not relative.endswith(".json"):
                    continue
                payload = self._download(urljoin(root, "api/v1/" + relative))
                expected = str(metadata.get("sha256") or "").lower()
                if expected and _sha256_bytes(payload).lower() != expected:
                    raise ValueError(f"{dataset_key} SHA-256 검증에 실패했습니다.")
                atomic_write_bytes(api / relative, payload, durable=False)

            return self._activate_site(staging, provider="정적 API", data_version=data_version, pages_base_url=root.rstrip("/"))

    def _activate_site(self, staging: Path, *, provider: str, data_version: str, pages_base_url: str) -> RemoteCatalogSyncResult:
        self._validate_core_site(staging)
        normalized = self._normalized_reference_payloads(staging)
        reference_dir = self.data_root / "reference_data"
        reference_dir.mkdir(parents=True, exist_ok=True)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        previous = self.cache_dir / f"{ACTIVE_SITE_DIR}.old"
        incoming = self.cache_dir / f"{ACTIVE_SITE_DIR}.new"
        shutil.rmtree(incoming, ignore_errors=True)
        shutil.copytree(staging, incoming)
        shutil.rmtree(previous, ignore_errors=True)
        if self.site_dir.exists():
            os.replace(self.site_dir, previous)
        try:
            os.replace(incoming, self.site_dir)
        except Exception:
            if not self.site_dir.exists() and previous.exists():
                os.replace(previous, self.site_dir)
            raise
        shutil.rmtree(previous, ignore_errors=True)

        for filename, payload in normalized.items():
            atomic_write_json(reference_dir / filename, payload, ensure_ascii=False, indent=2)
        reference.configure_override_root(self.data_root)

        config = self.load_config()
        config["schema"] = int(DEFAULT_REMOTE_API_CONFIG["schema"])
        config["enabled"] = bool(self.github_release_url() or self.pages_base_url())
        config["active_provider"] = provider
        config["data_version"] = data_version
        version_payload = normalized.get("program_version.json") if isinstance(normalized, dict) else {}
        if isinstance(version_payload, dict):
            config["game_version"] = str(version_payload.get("game_version") or "")
        if pages_base_url:
            config["pages_base_url"] = pages_base_url
            config["base_url"] = pages_base_url
        atomic_write_json(self.config_path, config, ensure_ascii=False, indent=2)
        game_version = str(config.get("game_version") or "")
        version_text = f" · 게임 {game_version}" if game_version else ""
        return RemoteCatalogSyncResult(True, True, f"프로그램 데이터 {data_version}{version_text} 설치 완료 · {provider}", provider, data_version, game_version)

    @staticmethod
    def _read_site_json(site: Path, relative: str) -> dict[str, Any]:
        path = site / relative
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"프로그램 데이터 JSON을 읽을 수 없습니다: {relative}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"프로그램 데이터 JSON 형식이 올바르지 않습니다: {relative}")
        return payload

    def _validate_core_site(self, site: Path) -> None:
        dolls = self._read_site_json(site, "api/v1/dolls.json")
        weapons = self._read_site_json(site, "api/v1/weapons.json")
        if int(dolls.get("count") or 0) != len(_dataset_items(dolls)) or not _dataset_items(dolls):
            raise ValueError("인형 데이터 count가 올바르지 않습니다.")
        if int(weapons.get("count") or 0) != len(_dataset_items(weapons)) or not _dataset_items(weapons):
            raise ValueError("무기 데이터 count가 올바르지 않습니다.")
        for rel in ("api/v1/common_keys.json", "api/v1/unique_keys.json", "api/v1/expansion_keys.json", "api/v1/remolding.json"):
            self._read_site_json(site, rel)
        # The current static API splits the old remolding umbrella into focused
        # datasets. Older packages remain readable, while new packages are
        # validated when those files are advertised/present.
        for rel in (
            "api/v1/weapon_part_effects.json",
            "api/v1/weapon_part_power_map.json",
            "api/v1/weapon_part_types.json",
            "api/v1/item_ranks.json",
            "api/v1/unclassified_polarity_plans.json",
        ):
            if (site / rel).is_file():
                payload = self._read_site_json(site, rel)
                if "count" in payload and int(payload.get("count") or 0) != len(_dataset_items(payload)):
                    raise ValueError(f"프로그램 데이터 count가 올바르지 않습니다: {rel}")

    def _normalized_reference_payloads(self, site: Path) -> dict[str, object]:
        dolls_payload = self._read_site_json(site, "api/v1/dolls.json")
        dolls = _dataset_items(dolls_payload)
        doll_names = {str(int(item["id"])): str(item.get("name_ko") or item.get("resource_name") or f"인형 {item['id']}") for item in dolls if int(item.get("id") or 0) > 0}
        aliases: dict[str, list[str]] = {}
        for item in dolls:
            did = int(item.get("id") or 0)
            if did <= 0:
                continue
            values = [str(item.get("resource_name") or "")]
            assets = item.get("assets") if isinstance(item.get("assets"), dict) else {}
            for key in ("portrait", "gacha", "fullbody"):
                path = str(assets.get(key) or "")
                if path:
                    values.append(Path(path).stem.split("--", 1)[0])
            aliases[str(did)] = list(dict.fromkeys(value for value in values if value))

        def catalog_rows(name: str, kind: str) -> list[dict[str, Any]]:
            payload = self._read_site_json(site, f"api/v1/{name}.json")
            rows = []
            for item in _dataset_items(payload):
                row = {
                    "id": int(item.get("id") or 0),
                    "name": str(item.get("name_ko") or ""),
                    "description": str(item.get("description_ko") or item.get("stat_description_ko") or ""),
                    "source": str(item.get("category_ko") or "GF2Tools program data"),
                }
                if kind == "weapon":
                    row.update({
                        "weapon_type": str(item.get("weapon_type_ko") or ""),
                        "weapon_type_code": str(item.get("weapon_type_code") or ""),
                        "rarity": int(item.get("rarity") or 0),
                        "resource_name": str(item.get("resource_name") or ""),
                        "image": str(((item.get("assets") or {}).get("image") if isinstance(item.get("assets"), dict) else "") or ""),
                    })
                elif item.get("gun_id") is not None:
                    row["doll_id"] = int(item.get("gun_id") or 0)
                rows.append(row)
            return rows

        equipment = {
            "schema": 1,
            "weapons": catalog_rows("weapons", "weapon"),
            "common_keys": catalog_rows("common_keys", "key"),
            "fixed_keys": catalog_rows("unique_keys", "key"),
            "expansion_keys": catalog_rows("expansion_keys", "key"),
            "verified_name_counts": {},
            "needs_refresh": False,
        }
        equipment["verified_name_counts"] = {
            key: len(equipment[key]) for key in ("weapons", "common_keys", "fixed_keys", "expansion_keys")
        }

        def optional_items(name: str) -> list[dict[str, Any]]:
            path = site / f"api/v1/{name}.json"
            if not path.is_file():
                return []
            return _dataset_items(self._read_site_json(site, f"api/v1/{name}.json"))

        remolding_payload = self._read_site_json(site, "api/v1/remolding.json")
        if "items" in remolding_payload:
            program_remolding = {
                "schema_version": 2,
                "source_table_version": str(remolding_payload.get("source_table_version") or ""),
                "property_definitions": _dataset_items(remolding_payload),
                "power_effects": optional_items("weapon_part_effects"),
                "power_map": optional_items("weapon_part_power_map"),
                "mod_types": optional_items("weapon_part_types"),
                "ranks": optional_items("item_ranks"),
                "polarity_plans": optional_items("unclassified_polarity_plans"),
            }
        else:
            # Compatibility with the pre-split static API.
            program_remolding = {
                "schema_version": 1,
                "source_table_version": str(remolding_payload.get("source_table_version") or ""),
                "property_definitions": list(remolding_payload.get("property_definitions") or []),
                "power_effects": list(remolding_payload.get("power_effects") or []),
                "power_map": list(remolding_payload.get("power_map") or []),
                "mod_types": list(remolding_payload.get("mod_types") or []),
                "ranks": list(remolding_payload.get("ranks") or []),
                "polarity_plans": list(remolding_payload.get("polarity_plans") or []),
            }

        game_version_payload: dict[str, Any] = {}
        game_version_path = site / "api/v1/game_version.json"
        if game_version_path.is_file():
            game_version_payload = self._read_site_json(site, "api/v1/game_version.json")
        manifest_payload: dict[str, Any] = {}
        manifest_path = site / "api/v1/manifest.json"
        if manifest_path.is_file():
            manifest_payload = self._read_site_json(site, "api/v1/manifest.json")
        latest_payload: dict[str, Any] = {}
        latest_path = site / "latest.json"
        if latest_path.is_file():
            latest_payload = self._read_site_json(site, "latest.json")
        program_version = {
            "schema_version": 1,
            "game_version": str(game_version_payload.get("game_version") or manifest_payload.get("game_version") or latest_payload.get("game_version") or ""),
            "data_version": str(manifest_payload.get("data_version") or latest_payload.get("data_version") or ""),
            "source_table_version": str(game_version_payload.get("source_table_version") or manifest_payload.get("source_table_version") or latest_payload.get("source_table_version") or ""),
            "bv_version": str(game_version_payload.get("bv_version") or manifest_payload.get("bv_version") or latest_payload.get("bv_version") or ""),
        }

        return {
            "program_version.json": program_version,
            "dolls.json": doll_names,
            "doll_asset_aliases.json": aliases,
            "program_dolls.json": {
                "schema_version": int(dolls_payload.get("schema_version") or 1),
                "source_table_version": str(dolls_payload.get("source_table_version") or ""),
                "items": dolls,
            },
            "program_remolding_catalog.json": program_remolding,
            "tactic_equipment_catalog.json": equipment,
        }
