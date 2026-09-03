from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_json
from ..repository import Repository, utc_now
from .remolding_recommendation import RemoldingRecommendationService

PROFILE_FORMAT = "gfl2-tools-remolding-target-profiles"
PROFILE_VERSION = 1


def default_recommendation_profile_name() -> str:
    return "gfl2-remolding-targets.json"


def export_recommendation_profiles(repo: Repository, path: str | Path) -> int:
    """Export effective target values for every built-in character.

    Defaults are materialized into the file so another user receives exactly
    the same target levels/weights/priorities even when they never edited that
    character locally.
    """
    service = RemoldingRecommendationService(repo)
    characters: list[dict[str, Any]] = []
    for character in sorted(
        service.base_characters.values(),
        key=lambda row: (str(row.get("nameKR") or ""), str(row.get("key") or "")),
    ):
        key = str(character.get("key") or "")
        if not key:
            continue
        characters.append(
            {
                "key": key,
                "name": str(character.get("nameKR") or key),
                "targets": service.get_target_profile(key),
            }
        )

    payload = {
        "format": PROFILE_FORMAT,
        "version": PROFILE_VERSION,
        "exported_at": utc_now(),
        "characters": characters,
    }
    atomic_write_json(
        Path(path),
        payload,
        ensure_ascii=False,
        indent=2,
        durable=True,
    )
    return len(characters)


def _load_profile_file(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"추천 설정 파일을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("추천 설정 파일의 최상위 값은 객체여야 합니다.")
    if payload.get("format") != PROFILE_FORMAT or payload.get("version") != PROFILE_VERSION:
        raise ValueError(
            f"현재 추천 설정 형식({PROFILE_FORMAT} v{PROFILE_VERSION})이 아닙니다."
        )
    rows = payload.get("characters")
    if not isinstance(rows, list):
        raise ValueError("추천 설정 파일에 characters 목록이 없습니다.")
    return rows


def import_recommendation_profiles(repo: Repository, path: str | Path) -> dict[str, int]:
    """Import current-format target profiles as one atomic database update."""
    rows = _load_profile_file(path)
    service = RemoldingRecommendationService(repo)
    profiles: dict[str, dict[str, Any]] = {}
    skipped = 0

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"characters[{index}] 항목은 객체여야 합니다.")
        key = row.get("key")
        targets = row.get("targets")
        if not isinstance(key, str) or not key:
            raise ValueError(f"characters[{index}]의 key가 올바르지 않습니다.")
        if not isinstance(targets, dict):
            raise ValueError(f"characters[{index}]의 targets가 올바르지 않습니다.")
        if key not in service.base_characters:
            skipped += 1
            continue
        if key in profiles:
            raise ValueError(f"중복 캐릭터 key가 있습니다: {key}")
        profiles[key] = targets

    if not profiles:
        raise ValueError("현재 버전에서 적용할 수 있는 캐릭터 추천 설정이 없습니다.")
    saved = service.save_target_profiles(profiles)
    return {"imported": len(saved), "skipped": skipped}
