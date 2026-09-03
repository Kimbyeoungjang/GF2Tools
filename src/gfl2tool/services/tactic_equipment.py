from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import reference
from ..atomic_io import atomic_write_json

CATALOG_SCHEMA = 1
USER_EQUIPMENT_SCHEMA = 1


def _empty_catalog() -> dict[str, Any]:
    return {
        "schema": CATALOG_SCHEMA,
        "weapons": [],
        "common_keys": [],
        "fixed_keys": [],
        "expansion_keys": [],
        "verified_name_counts": {},
        "needs_refresh": False,
    }


class TacticEquipmentCatalog:
    """Offline reference catalog imported through data/reference_data.

    The main application loads this catalog only from its bundled/reference-data
    store. Updated catalogs are applied through the reference-data import UI.
    """

    def __init__(self, repo):
        self.repo = repo

    def load(self) -> dict[str, Any]:
        reference.configure_override_root(self.repo.path.parent)
        try:
            payload = reference.dataset_payload("tactic_equipment_catalog")
            reference.validate_dataset_payload("tactic_equipment_catalog", payload)
        except Exception:
            payload = _empty_catalog()
        payload = dict(payload) if isinstance(payload, dict) else _empty_catalog()
        payload.setdefault("schema", CATALOG_SCHEMA)
        for key in ("weapons", "common_keys", "fixed_keys", "expansion_keys"):
            payload.setdefault(key, [])
        payload.setdefault(
            "verified_name_counts",
            {key: len(payload.get(key) or []) for key in ("weapons", "common_keys", "fixed_keys", "expansion_keys")},
        )
        payload["needs_refresh"] = False
        try:
            payload["imported_matches"] = ImportedEquipmentStore(self.repo).resolve(payload)
        except Exception:
            payload["imported_matches"] = {
                "schema": USER_EQUIPMENT_SCHEMA,
                "dolls": {},
                "matched_dolls": 0,
                "matched_items": 0,
                "named_items": 0,
            }
        return payload


class ImportedEquipmentStore:
    """Offline equipment/key state imported from an external user-data file."""

    def __init__(self, repo):
        self.path = repo.path.parent / "master_data" / "tactic_equipment_user.json"

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"schema": USER_EQUIPMENT_SCHEMA, "dolls": {}, "weapons_by_uid": {}}
        if not isinstance(payload, dict) or int(payload.get("schema") or 0) != USER_EQUIPMENT_SCHEMA:
            return {"schema": USER_EQUIPMENT_SCHEMA, "dolls": {}, "weapons_by_uid": {}}
        return {
            "schema": USER_EQUIPMENT_SCHEMA,
            "dolls": dict(payload.get("dolls") or {}),
            "weapons_by_uid": dict(payload.get("weapons_by_uid") or {}),
        }

    def save(self, payload: dict[str, Any]) -> Path:
        clean = {
            "schema": USER_EQUIPMENT_SCHEMA,
            "dolls": dict(payload.get("dolls") or {}),
            "weapons_by_uid": dict(payload.get("weapons_by_uid") or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, clean, ensure_ascii=False, indent=2)
        return self.path

    @staticmethod
    def _category_index(catalog: dict[str, Any], category: str) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for raw in catalog.get(category) or []:
            if not isinstance(raw, dict):
                continue
            try:
                row_id = int(raw.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if row_id > 0:
                out[row_id] = dict(raw)
        return out

    @staticmethod
    def _resolved_item(value: int, index: dict[int, dict[str, Any]], *, kind: str = "") -> dict[str, Any]:
        raw = index.get(int(value), {})
        name = str(raw.get("name") or "").strip()
        item = {
            "id": int(value),
            "name": name or f"ID {int(value)}",
            "source": str(raw.get("source") or ""),
            "verified_name": bool(name),
        }
        if kind:
            item["kind"] = kind
        return item

    def resolve(self, catalog: dict[str, Any] | None) -> dict[str, Any]:
        catalog = catalog if isinstance(catalog, dict) else {}
        weapon_index = self._category_index(catalog, "weapons")
        common_index = self._category_index(catalog, "common_keys")
        fixed_index = self._category_index(catalog, "fixed_keys")
        expansion_index = self._category_index(catalog, "expansion_keys")
        imported = self.load()
        weapons_by_uid = dict(imported.get("weapons_by_uid") or {})
        resolved: dict[str, Any] = {}
        named_items = 0
        for doll_id, raw in (imported.get("dolls") or {}).items():
            if not isinstance(raw, dict):
                continue
            row: dict[str, list[dict[str, Any]]] = {
                "weapons": [], "common_keys": [], "fixed_keys": [], "expansion_keys": []
            }
            weapon_uid = int(raw.get("weapon_uid") or 0)
            if weapon_uid:
                weapon_state = dict(weapons_by_uid.get(str(weapon_uid)) or {})
                item_id = int(weapon_state.get("item_id") or 0)
                candidates = []
                for value in weapon_state.get("item_id_candidates") or []:
                    try:
                        candidate = int(value)
                    except (TypeError, ValueError):
                        continue
                    if candidate > 0 and candidate not in candidates:
                        candidates.append(candidate)
                if item_id > 0 and item_id not in candidates:
                    candidates.insert(0, item_id)
                hits = [candidate for candidate in candidates if candidate in weapon_index]
                resolved_id = item_id if item_id in weapon_index else (hits[0] if len(hits) == 1 else item_id)
                item = self._resolved_item(resolved_id or weapon_uid, weapon_index)
                item.update({
                    "uid": weapon_uid,
                    "level": int(weapon_state.get("level") or 0),
                    "rank": int(weapon_state.get("rank") or 0),
                })
                row["weapons"].append(item)
            for value in raw.get("common_key_ids") or []:
                row["common_keys"].append(self._resolved_item(int(value), common_index, kind="공용키"))
            for value in raw.get("fixed_key_ids") or []:
                row["fixed_keys"].append(self._resolved_item(int(value), fixed_index, kind="고유키"))
            for value in raw.get("expansion_key_ids") or []:
                row["expansion_keys"].append(self._resolved_item(int(value), expansion_index, kind="도약키"))
            if any(row.values()):
                resolved[str(doll_id)] = row
                named_items += sum(1 for values in row.values() for item in values if item.get("verified_name"))
        return {
            "schema": USER_EQUIPMENT_SCHEMA,
            "dolls": resolved,
            "matched_dolls": len(resolved),
            "matched_items": sum(sum(len(v) for v in row.values()) for row in resolved.values()),
            "named_items": named_items,
            "imported_dolls": len(imported.get("dolls") or {}),
            "imported_weapons": len(weapons_by_uid),
        }
