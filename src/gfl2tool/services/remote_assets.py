from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from ..atomic_io import atomic_write_bytes


@lru_cache(maxsize=16)
def _program_dolls(path_text: str, revision: tuple[int, int]) -> dict[int, dict]:
    del revision
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    out: dict[int, dict] = {}
    for row in payload.get("items") or [] if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        try:
            did = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if did > 0:
            out[did] = dict(row)
    return out


def _revision(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return 0, 0


def _asset_relative_path(data_root: Path, doll_id: int, kind: str) -> str:
    catalog_path = data_root / "reference_data" / "program_dolls.json"
    row = _program_dolls(str(catalog_path), _revision(catalog_path)).get(int(doll_id), {})
    assets = row.get("assets") if isinstance(row.get("assets"), dict) else {}
    key = "gacha" if kind == "gacha" else "portrait"
    return str(assets.get(key) or "").replace("\\", "/").lstrip("/")


def site_asset_cache_path(data_root: str | Path, relative_path: str | Path | None) -> Path | None:
    """Return the local cache target for one asset path advertised by program data."""
    relative = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not relative or relative.startswith("../") or "/../" in f"/{relative}/":
        return None
    return Path(data_root) / "remote-api-cache" / "site" / relative


def remote_asset_cache_path(data_root: str | Path, doll_id: int, *, kind: str = "portrait") -> Path:
    """Return the active package/Pages cache path for a Doll image."""
    root = Path(data_root)
    relative = _asset_relative_path(root, int(doll_id), kind)
    if relative:
        return root / "remote-api-cache" / "site" / relative
    # Compatibility fallback for data created by v0.85-v0.90.
    suffix = "_gacha" if kind == "gacha" else ""
    return root / "master_data" / "illustrations" / f"doll_{int(doll_id)}{suffix}.png"


def request_from_cache_path(path: str | Path) -> tuple[int, str] | None:
    target = Path(path)
    name = target.name.lower()
    if name.startswith("doll_") and name.endswith(".png"):
        stem = target.stem
        body = stem[5:]
        if body.endswith("_gacha"):
            body = body[:-6]
            kind = "gacha"
        else:
            kind = "portrait"
        if body.isdigit() and int(body) > 0:
            return int(body), kind
    # New package paths are resolved through program_dolls.json rather than filename IDs.
    return None


def _data_root_from_site_asset(path: Path) -> Path | None:
    parts = path.parts
    try:
        index = parts.index("remote-api-cache")
    except ValueError:
        return None
    if index + 1 >= len(parts) or parts[index + 1] != "site":
        return None
    if index <= 0:
        return None
    return Path(*parts[:index])


def _expected_sha(data_root: Path, relative: str) -> str:
    index_path = data_root / "remote-api-cache" / "site" / "api/v1/images.json"
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    target = relative.replace("\\", "/").lstrip("/")
    for item in payload.get("items") or [] if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        path_value = str(item.get("path") or "").replace("\\", "/")
        while path_value.startswith("../"):
            path_value = path_value[3:]
        if path_value == target:
            return str(item.get("sha256") or "").lower()
    return ""


def ensure_cache_path(path: str | Path) -> bool:
    """Ensure one Pages-hosted image is available in the local static-site cache.

    Full Release/offline packages already contain their images, so this returns
    immediately. Pages fallback downloads only the image currently requested by
    the UI and verifies its SHA-256 when ``images.json`` provides one.
    """
    target = Path(path)
    try:
        if target.is_file() and target.stat().st_size > 0:
            return True
    except OSError:
        pass
    data_root = _data_root_from_site_asset(target)
    if data_root is None:
        return False
    try:
        relative = target.relative_to(data_root / "remote-api-cache" / "site").as_posix()
    except ValueError:
        return False
    if not relative.startswith("assets/"):
        return False
    config_path = data_root / "remote-api.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    base = str(config.get("pages_base_url") or config.get("base_url") or "").strip().rstrip("/")
    if not base:
        return False
    timeout = max(3, min(120, int(config.get("timeout_seconds") or 20)))
    try:
        request = Request(urljoin(base + "/", relative), headers={"User-Agent": "GF2Tools-program-data/1"})
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
        expected = _expected_sha(data_root, relative)
        if expected and hashlib.sha256(payload).hexdigest().lower() != expected:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        atomic_write_bytes(temporary, payload, durable=False)
        temporary.replace(target)
        return target.stat().st_size > 0
    except (OSError, ValueError):
        return False


_REMOTE_ASSET_COMPAT_API = (request_from_cache_path,)
