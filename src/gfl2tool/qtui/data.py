from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import reference
from ..repository import Repository
from ..services.remote_assets import remote_asset_cache_path
from ..services.dolls import DollCharacterResolver, expand_linked_owned_doll_rows


def _normalized(value: str | None) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())



@lru_cache(maxsize=1)
def _recommendation_character_name_index() -> dict[str, str]:
    out: dict[str, str] = {}
    for char in reference.remolding_characters().values():
        key = _normalized(char.get("key"))
        if key:
            out[key] = str(char.get("nameKR") or char.get("key") or "")
    return out


def invalidate_presentation_caches() -> None:
    _recommendation_character_name_index.cache_clear()


def doll_display_name(doll_id: int, raw_name: str | None = None) -> str:
    raw = str(raw_name or "").strip()
    if raw and any("가" <= ch <= "힣" for ch in raw):
        return raw
    bundled = reference.bundled_doll_display_names().get(int(doll_id))
    if bundled:
        return str(bundled)
    normalized = _normalized(raw)
    if normalized:
        matched = _recommendation_character_name_index().get(normalized)
        if matched:
            return matched
    return raw or reference.bundled_dolls().get(int(doll_id)) or f"인형 {doll_id}"


class OwnedDollCatalog:
    """Small, revision-aware presentation cache shared by Qt pages."""

    def __init__(self, repo: Repository):
        self.repo = repo
        self._token: tuple[Any, ...] | None = None
        self._entries: list[dict[str, Any]] = []
        self._entry_by_doll_id: dict[int, dict[str, Any]] = {}
        self._resolver: DollCharacterResolver | None = None
        self._portrait_cache: dict[tuple[int, str], Path | None] = {}

    def invalidate(self) -> None:
        self._token = None
        self._entries = []
        self._entry_by_doll_id = {}
        self._resolver = None
        self._portrait_cache.clear()

    def _presentation_signature(self) -> tuple[Any, ...]:
        dolls = tuple(
            (int(r.get("doll_id") or 0), str(r.get("name") or ""), int(r.get("level") or 0),
             int(r.get("rank") or 0), str(r.get("illustration_path") or ""), int(r.get("favorite") or 0),
             str(r.get("updated_at") or ""))
            for r in self.repo.rows("dolls", order_by="doll_id")
        )
        profiles = tuple(
            (str(r.get("character_key") or ""), str(r.get("display_name") or ""),
             str(r.get("doll_type") or ""), str(r.get("element_type") or ""),
             str(r.get("slot_counts_json") or ""), str(r.get("tags_json") or ""),
             r.get("level_override"), str(r.get("updated_at") or ""))
            for r in self.repo.rows("remolding_character_profiles", order_by="character_key")
        )
        return dolls, profiles

    @property
    def resolver(self) -> DollCharacterResolver:
        token = self._presentation_signature()
        if self._resolver is None or self._token != token:
            self.entries()
        if self._resolver is None:
            raise RuntimeError("인형 표시 데이터 resolver를 초기화하지 못했습니다.")
        return self._resolver

    def character_level_for_key(self, character_key: str) -> int:
        return self.resolver.character_level_for_key(str(character_key))

    def entries(self) -> list[dict[str, Any]]:
        token = self._presentation_signature()
        if self._token == token and self._resolver is not None:
            return self._entries
        stored_rows = self.repo.rows("dolls", order_by="favorite DESC, COALESCE(name,'~'), doll_id")
        raw_rows = expand_linked_owned_doll_rows(stored_rows)
        raw_rows.sort(
            key=lambda row: (
                0 if bool(int(row.get("favorite") or 0)) else 1,
                str(row.get("name") or "").casefold(),
                int(row.get("doll_id") or 0),
            )
        )
        owned_rows = {int(row["doll_id"]): dict(row) for row in raw_rows}
        resolver = DollCharacterResolver(self.repo, owned_doll_rows=owned_rows)
        factor_names = reference.remolding_rules().get("factor_names", {})
        element_names = reference.remolding_rules().get("element_names", {})
        out: list[dict[str, Any]] = []
        for raw in raw_rows:
            row = dict(raw)
            did = int(row["doll_id"])
            key = resolver.character_key_for_doll(did)
            try:
                char = resolver.recommendation.get_character(key) if key else None
            except ValueError:
                char = None
            program_meta = reference.program_dolls().get(did, {})
            duty_to_factor = {
                "센티널": "sentinel", "센티넬": "sentinel", "뱅가드": "vanguard",
                "불워크": "bulwark", "서포트": "support",
            }
            factor = str((char or {}).get("dollType") or duty_to_factor.get(str(program_meta.get("duty_ko") or ""), ""))
            element = str((char or {}).get("elementType") or "")
            out.append({
                "row": row,
                "doll_id": did,
                "name": doll_display_name(did, row.get("name")),
                "character_key": key,
                "character": char,
                "factor_type": factor,
                "factor_label": str(factor_names.get(factor, factor)) if factor else "분류 미확인",
                "element_type": element,
                "element_label": str(element_names.get(element, element)) if element else "속성 미확인",
                "favorite": bool(int(row.get("favorite") or 0)),
                "ownership_source_doll_id": int(row.get("_ownership_source_doll_id") or did),
                "linked_ownership": bool(row.get("_linked_ownership")),
                "program_meta": program_meta,
                "weapon_type_label": str(program_meta.get("weapon_type_ko") or ""),
                "rarity": int(program_meta.get("rarity") or 0),
            })
            out[-1]["search_text"] = " ".join((
                str(out[-1]["name"]), str(did), str(out[-1]["factor_label"]), str(out[-1]["element_label"]),
            )).casefold()
            out[-1]["sort_key"] = (0 if out[-1]["favorite"] else 1, str(out[-1]["name"]).casefold(), did)
        self._token = token
        self._entries = out
        self._entry_by_doll_id = {int(entry["doll_id"]): entry for entry in out}
        self._resolver = resolver
        return out

    def toggle_favorite(self, doll_id: int) -> bool:
        did = int(doll_id)
        cached = self._entry_by_doll_id.get(did)
        source_did = int((cached or {}).get("ownership_source_doll_id") or did)
        current = bool(cached.get("favorite")) if cached is not None else self.repo.is_doll_favorite(source_did)
        new_value = not current
        self.repo.set_doll_favorite(source_did, new_value)
        self.invalidate()
        return new_value

    def entries_with_portraits(self) -> list[dict[str, Any]]:
        entries = self.entries()
        for entry in entries:
            row = entry.get("row") or {}
            raw = str(row.get("illustration_path") or "")
            key = (int(entry.get("doll_id") or 0), raw)
            if key not in self._portrait_cache:
                self._portrait_cache[key] = resolve_portrait_path(self.repo, row)
            entry["portrait_path"] = self._portrait_cache[key]
            entry["gacha_art_path"] = resolve_gacha_art_path(self.repo, int(entry.get("doll_id") or 0))
        return entries

    def all_reference_entries_with_portraits(self) -> list[dict[str, Any]]:
        """Return owned + program-catalog Dolls for manual/tactic pickers.

        Fresh installs can have a complete REST/offline program catalog while
        the user-owned ``dolls`` table is still empty.  Building the unowned
        rows here keeps those pickers on the same resolver and asset-cache path
        as the normal inventory instead of falling back to retired illustration
        locations.
        """
        owned = {int(row.get("doll_id") or 0): dict(row) for row in self.entries_with_portraits()}
        factor_names = reference.remolding_rules().get("factor_names", {})
        element_names = reference.remolding_rules().get("element_names", {})
        display_names = reference.bundled_doll_display_names()
        program = reference.program_dolls()
        doll_ids = set(display_names) | set(program) | set(owned)
        duty_to_factor = {
            "센티널": "sentinel", "센티넬": "sentinel", "뱅가드": "vanguard",
            "불워크": "bulwark", "서포트": "support",
        }
        rows: list[dict[str, Any]] = []
        for did in doll_ids:
            if did <= 0:
                continue
            existing = owned.get(int(did))
            if existing is not None:
                entry = dict(existing)
                entry["portrait_path"] = str(entry.get("portrait_path") or "")
                entry["owned"] = True
            else:
                key = self.resolver.character_key_for_doll(int(did))
                try:
                    char = self.resolver.recommendation.get_character(key) if key else None
                except ValueError:
                    char = None
                program_meta = dict(program.get(int(did), {}) or {})
                factor = str(
                    (char or {}).get("dollType")
                    or duty_to_factor.get(str(program_meta.get("duty_ko") or ""), "")
                )
                element = str((char or {}).get("elementType") or program_meta.get("element_type") or "")
                name = str(display_names.get(int(did)) or program_meta.get("name_ko") or f"인형 {did}")
                portrait = remote_asset_cache_path(self.repo.path.parent, int(did), kind="portrait")
                entry = {
                    "row": {"doll_id": int(did), "name": name, "level": 60, "rank": 0},
                    "doll_id": int(did),
                    "name": name,
                    "character_key": key,
                    "character": char,
                    "factor_type": factor,
                    "factor_label": str(factor_names.get(factor, factor)) if factor else "분류 미확인",
                    "element_type": element,
                    "element_label": str(element_names.get(element, element)) if element else "속성 미확인",
                    "favorite": False,
                    "program_meta": program_meta,
                    "weapon_type_label": str(program_meta.get("weapon_type_ko") or ""),
                    "rarity": int(program_meta.get("rarity") or 0),
                    "portrait_path": str(portrait),
                    "gacha_art_path": str(resolve_gacha_art_path(self.repo, int(did)) or ""),
                    "owned": False,
                }
            entry["search_text"] = " ".join((
                str(entry.get("name") or ""), str(did),
                str(entry.get("factor_label") or ""), str(entry.get("element_label") or ""),
                "보유" if entry.get("owned") else "미보유",
            )).casefold()
            entry["sort_key"] = (
                0 if entry.get("owned") else 1,
                str(entry.get("name") or "").casefold(),
                int(did),
            )
            rows.append(entry)
        rows.sort(key=lambda row: row.get("sort_key") or (1, "", 0))
        return rows


def resolve_gacha_art_path(repo: Repository, doll_id: int) -> Path | None:
    if int(doll_id) <= 0:
        return None
    # The REST catalog sync owns network I/O. UI code only points at the
    # deterministic local cache path and renders it when synchronization has
    # already populated the file.
    return remote_asset_cache_path(repo.path.parent, int(doll_id), kind="gacha")


def resolve_portrait_path(repo: Repository, row: dict[str, Any] | None) -> Path | None:
    if not row:
        return None
    raw = str(row.get("illustration_path") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = repo.path.parent / path
        if path.is_file():
            return path
    doll_id = int(row.get("doll_id") or 0)
    if doll_id <= 0:
        return None
    return remote_asset_cache_path(repo.path.parent, doll_id, kind="portrait")


def remolding_meta(row: dict[str, Any]) -> dict[str, Any]:
    try:
        slots = json.loads(row.get("slots_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        slots = []
    options = reference.remolding_options()
    factor_names = reference.remolding_rules().get("factor_names", {})
    major_names: list[str] = []
    major_keys: list[str] = []
    attrs: list[str] = []
    sub_attrs: list[str] = []
    factors: list[str] = []
    sub_factor_labels: list[str] = []
    levels: set[int] = set()
    for slot in slots if isinstance(slots, list) else []:
        option = options.get(str(slot.get("option_key") or ""), {})
        name = str(option.get("nameKR") or slot.get("name") or "옵션 미확인")
        contribution = int(slot.get("level_contribution") or slot.get("variant") or 0)
        suffix = f" +{contribution}" if contribution else ""
        attrs.append(name + suffix)
        factor = str(option.get("factorType") or slot.get("factor_type") or "")
        if factor:
            factors.append(factor)
        if bool(option.get("isMajor")):
            major_names.append(name)
            major_keys.append(str(slot.get("option_key") or ""))
        else:
            sub_attrs.append(name + suffix)
            if contribution:
                levels.add(contribution)
            if factor:
                sub_factor_labels.append(str(factor_names.get(factor, factor)))
    # A remolding's physical family is defined by its major option. Minor
    # options may legally come from other families, so using the most frequent
    # factor can misclassify (major Sentinel + two Vanguard minors) as Vanguard.
    major_factor = None
    for slot in slots if isinstance(slots, list) else []:
        option = options.get(str(slot.get("option_key") or ""), {})
        if bool(option.get("isMajor")):
            major_factor = str(option.get("factorType") or slot.get("factor_type") or "") or None
            if major_factor:
                break
    primary_factor = major_factor or (max(set(factors), key=factors.count) if factors else None)
    fallback = f"{factor_names.get(primary_factor, primary_factor)} 리몰딩" if primary_factor else "리몰딩"
    return {
        "name": major_names[0] if major_names else fallback,
        "attributes": " / ".join(attrs) or "옵션 미확인",
        "major_names": major_names,
        "major_keys": major_keys,
        "main_option_key": major_keys[0] if major_keys else "",
        "main_option_name": major_names[0] if major_names else fallback,
        "sub_attributes": " / ".join(sub_attrs) or "—",
        "sub_factor_labels": sub_factor_labels,
        "levels": levels,
        "slots": slots,
        "primary_factor": primary_factor,
    }
