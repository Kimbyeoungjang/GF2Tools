from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

_OVERRIDE_ROOT: Path | None = None

REFERENCE_DATASETS: dict[str, dict[str, str]] = {
    "dolls": {"label": "인형 ID/기본명", "filename": "dolls.json"},
    "doll_asset_aliases": {"label": "인형 이미지 별칭", "filename": "doll_asset_aliases.json"},
    "program_dolls": {"label": "프로그램 인형 카탈로그", "filename": "program_dolls.json"},
    "program_version": {"label": "게임/프로그램 데이터 버전", "filename": "program_version.json"},
    "program_remolding_catalog": {"label": "프로그램 리몰딩/부착물 카탈로그", "filename": "program_remolding_catalog.json"},
    "remolding_rules": {"label": "리몰딩 규칙/현상 기준", "filename": "remolding_rules.json"},
    "cooking_permanent": {"label": "영구 요리 레시피", "filename": "cooking_permanent.json", "resource": "1"},
    "tactic_equipment_catalog": {"label": "무기 · 공용키 · 고유키 · 도약키 카탈로그", "filename": "tactic_equipment_catalog.json"},
}


def configure_override_root(data_dir: str | Path | None) -> None:
    """Point reference loaders at an updateable data/reference_data directory.

    Bundled files remain the immutable fallback. Imports never modify the
    installed package, which keeps packaged releases recoverable.
    """
    global _OVERRIDE_ROOT
    _OVERRIDE_ROOT = None if data_dir is None else Path(data_dir) / "reference_data"
    clear_caches()


def _bundled_path(name: str):
    if name == "cooking_permanent.json":
        return files("gfl2tool.resources").joinpath(name)
    return files("gfl2tool").joinpath("reference_data", name)


def _load(name: str) -> Any:
    if _OVERRIDE_ROOT is not None:
        candidate = _OVERRIDE_ROOT / name
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                # An empty/corrupt doll override used to hide the immutable
                # bundled catalog and made a fresh install look like it had no
                # basic character data.  Keep the bundled file as a hard
                # fallback; imports still validate before writing overrides.
                if name != "dolls.json" or (isinstance(payload, dict) and payload):
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
    return json.loads(_bundled_path(name).read_text(encoding="utf-8"))


def dataset_payload(key: str) -> Any:
    meta = REFERENCE_DATASETS.get(str(key))
    if meta is None:
        raise KeyError(f"unknown reference dataset: {key}")
    return _load(meta["filename"])


def validate_dataset_payload(key: str, payload: Any) -> None:
    if key == "dolls":
        if not isinstance(payload, dict) or not payload or any(not str(k).isdigit() for k in payload):
            raise ValueError("인형 레퍼런스는 하나 이상의 숫자 ID를 키로 하는 JSON 객체여야 합니다.")
        if any(not isinstance(v, str) or not v.strip() for v in payload.values()):
            raise ValueError("인형 레퍼런스의 이름 값이 올바르지 않습니다.")
        return
    if key == "program_dolls":
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("프로그램 인형 카탈로그는 items 배열을 포함해야 합니다.")
        return
    if key == "program_version":
        if not isinstance(payload, dict) or not str(payload.get("game_version") or "").strip():
            raise ValueError("프로그램 버전 데이터에는 game_version이 필요합니다.")
        return
    if key == "program_remolding_catalog":
        required = {"property_definitions", "power_effects", "power_map", "mod_types", "ranks", "polarity_plans"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise ValueError("프로그램 리몰딩 카탈로그의 분리 데이터셋이 올바르지 않습니다.")
        if any(not isinstance(payload.get(name), list) for name in required):
            raise ValueError("프로그램 리몰딩 카탈로그 항목은 배열이어야 합니다.")
        return
    if key == "doll_asset_aliases":
        if not isinstance(payload, dict) or any(not str(k).isdigit() for k in payload):
            raise ValueError("이미지 별칭 레퍼런스는 숫자 ID를 키로 하는 JSON 객체여야 합니다.")
        if any(not isinstance(v, list) or any(not isinstance(x, str) for x in v) for v in payload.values()):
            raise ValueError("이미지 별칭 값은 문자열 배열이어야 합니다.")
        return
    if key == "remolding_rules":
        required = {"options", "characters", "imagoforms"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise ValueError("리몰딩 규칙 파일에 options/characters/imagoforms가 필요합니다.")
        if any(not isinstance(payload.get(name), list) for name in required):
            raise ValueError("리몰딩 규칙의 핵심 항목은 배열이어야 합니다.")
        return
    if key == "cooking_permanent":
        if not isinstance(payload, (list, dict)):
            raise ValueError("요리 레퍼런스는 JSON 배열 또는 객체여야 합니다.")
        return
    if key == "tactic_equipment_catalog":
        required = {"weapons", "common_keys", "fixed_keys", "expansion_keys"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise ValueError("장비/키 카탈로그에 weapons/common_keys/fixed_keys/expansion_keys가 필요합니다.")
        if any(not isinstance(payload.get(name), list) for name in required):
            raise ValueError("장비/키 카탈로그의 각 항목은 배열이어야 합니다.")
        return
    raise KeyError(f"unknown reference dataset: {key}")


def _int_keyed(name: str) -> dict[int, Any]:
    return {int(k): v for k, v in _load(name).items()}


@lru_cache(maxsize=1)
def bundled_dolls() -> dict[int, str]:
    return _int_keyed("dolls.json")


@lru_cache(maxsize=1)
def bundled_doll_asset_aliases() -> dict[int, list[str]]:
    raw = _load("doll_asset_aliases.json")
    return {int(k): [str(x) for x in (v or []) if str(x).strip()] for k, v in raw.items()}


def _norm_reference(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


@lru_cache(maxsize=1)
def program_dolls() -> dict[int, dict[str, Any]]:
    try:
        payload = _load("program_dolls.json")
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        try:
            did = int(raw.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if did > 0:
            out[did] = dict(raw)
    return out


@lru_cache(maxsize=1)
def program_version() -> dict[str, Any]:
    try:
        payload = _load("program_version.json")
    except Exception:
        return {"schema_version": 0, "game_version": "", "data_version": "", "source_table_version": "", "bv_version": ""}
    if not isinstance(payload, dict):
        return {"schema_version": 0, "game_version": "", "data_version": "", "source_table_version": "", "bv_version": ""}
    return {
        "schema_version": int(payload.get("schema_version") or 1),
        "game_version": str(payload.get("game_version") or ""),
        "data_version": str(payload.get("data_version") or ""),
        "source_table_version": str(payload.get("source_table_version") or ""),
        "bv_version": str(payload.get("bv_version") or ""),
    }


@lru_cache(maxsize=1)
def program_remolding_catalog() -> dict[str, Any]:
    try:
        payload = _load("program_remolding_catalog.json")
        validate_dataset_payload("program_remolding_catalog", payload)
    except Exception:
        return {
            "schema_version": 0,
            "source_table_version": "",
            "property_definitions": [],
            "power_effects": [],
            "power_map": [],
            "mod_types": [],
            "ranks": [],
            "polarity_plans": [],
        }
    return dict(payload)


@lru_cache(maxsize=1)
def bundled_doll_display_names() -> dict[int, str]:
    chars = {_norm_reference(c.get("key")): c.get("nameKR") for c in remolding_rules().get("characters", [])}
    return {
        int(did): str(chars.get(_norm_reference(raw_name)) or raw_name)
        for did, raw_name in bundled_dolls().items()
    }


@lru_cache(maxsize=1)
def dolls() -> dict[int, str]:
    return dict(bundled_doll_display_names())


@lru_cache(maxsize=1)
def remolding_rules() -> dict[str, Any]:
    return _load("remolding_rules.json")


@lru_cache(maxsize=1)
def remolding_options() -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in remolding_rules()["options"]}


@lru_cache(maxsize=1)
def remolding_characters() -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in remolding_rules()["characters"]}


@lru_cache(maxsize=1)
def remolding_characters_by_normalized_key() -> dict[str, dict[str, Any]]:
    return {_norm_reference(row.get("key")): row for row in remolding_rules()["characters"]}


@lru_cache(maxsize=1)
def remolding_imagoforms() -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in remolding_rules()["imagoforms"]}


@lru_cache(maxsize=1)
def remolding_code_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for option in remolding_rules()["options"]:
        for i, code in enumerate(option.get("codes", []), 1):
            out[str(code).lower()] = {
                "option_key": option["key"],
                "name": option["nameKR"],
                "variant": i,
                "factor_type": option.get("factorType"),
                "element_type": option.get("elementType"),
            }
    return out


@lru_cache(maxsize=1)
def remoldings() -> dict[str, str]:
    index = remolding_code_index()
    return {code: f"{meta['name'].replace(' ', '')}{meta['variant']}" for code, meta in index.items()}


def clear_caches() -> None:
    for fn in (
        bundled_dolls,
        bundled_doll_asset_aliases,
        bundled_doll_display_names,
        program_dolls,
        program_version,
        program_remolding_catalog,
        remolding_rules,
        remolding_options,
        remolding_characters,
        remolding_characters_by_normalized_key,
        remolding_imagoforms,
        remolding_code_index,
        remoldings,
    ):
        fn.cache_clear()
