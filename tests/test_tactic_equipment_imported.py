from __future__ import annotations

import json
from types import SimpleNamespace

from gfl2tool import reference
from gfl2tool.services.tactic_equipment import ImportedEquipmentStore, TacticEquipmentCatalog


def _repo(tmp_path):
    return SimpleNamespace(path=tmp_path / "gfl2.db")


def test_imported_equipment_store_resolves_user_sidecar_against_reference(tmp_path):
    repo = _repo(tmp_path)
    store = ImportedEquipmentStore(repo)
    store.save({
        "schema": 1,
        "dolls": {
            "1025": {
                "weapon_uid": 39,
                "common_key_ids": [93029],
                "fixed_key_ids": [202501],
                "expansion_key_ids": [921025],
            }
        },
        "weapons_by_uid": {
            "39": {"uid": 39, "item_id": 10743, "level": 60, "rank": 6}
        },
    })
    resolved = store.resolve({
        "weapons": [{"id": 10743, "name": "검증 무기", "source": "reference"}],
        "common_keys": [{"id": 93029, "name": "공용 A", "source": "reference"}],
        "fixed_keys": [{"id": 202501, "name": "고유 A", "source": "reference"}],
        "expansion_keys": [{"id": 921025, "name": "도약 A", "source": "reference"}],
    })
    row = resolved["dolls"]["1025"]
    assert row["weapons"][0]["name"] == "검증 무기"
    assert row["weapons"][0]["level"] == 60
    assert row["weapons"][0]["rank"] == 6
    assert row["common_keys"][0]["name"] == "공용 A"
    assert row["fixed_keys"][0]["name"] == "고유 A"
    assert row["expansion_keys"][0]["name"] == "도약 A"


def test_imported_equipment_store_rejects_incompatible_sidecar_schema(tmp_path):
    repo = _repo(tmp_path)
    store = ImportedEquipmentStore(repo)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"schema": 999, "dolls": {"1": {}}}), encoding="utf-8")
    assert store.load() == {"schema": 1, "dolls": {}, "weapons_by_uid": {}}


def test_tactic_equipment_catalog_loads_only_reference_override_and_never_game_directory(tmp_path):
    repo = _repo(tmp_path)
    reference.configure_override_root(tmp_path)
    target = tmp_path / "reference_data" / "tactic_equipment_catalog.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "schema": 1,
        "weapons": [{"id": 10001, "name": "오프라인 무기"}],
        "common_keys": [], "fixed_keys": [], "expansion_keys": [],
        "verified_name_counts": {"weapons": 1},
    }, ensure_ascii=False), encoding="utf-8")
    reference.configure_override_root(tmp_path)
    loaded = TacticEquipmentCatalog(repo).load()
    assert loaded["weapons"][0]["name"] == "오프라인 무기"
    assert loaded["needs_refresh"] is False
    assert "imported_matches" in loaded
