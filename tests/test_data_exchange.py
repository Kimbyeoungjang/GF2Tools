from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from gfl2tool import reference
from gfl2tool.models import Doll, FormationMember, GameFormation
from gfl2tool.repository import Repository
from gfl2tool.services.data_exchange import (
    REFERENCE_DATASET_LABELS,
    USER_DATASET_LABELS,
    export_all_reference_data,
    export_all_user_data,
    export_reference_dataset,
    export_user_dataset,
    import_all_reference_data,
    import_all_user_data,
    import_user_csv_bundle,
    import_reference_dataset,
    import_user_dataset,
)


def test_each_reference_dataset_can_export_and_import_as_validated_override(tmp_path):
    data_dir = tmp_path / "data"
    reference.configure_override_root(data_dir)
    try:
        for key in REFERENCE_DATASET_LABELS:
            exported = tmp_path / f"{key}.json"
            export_reference_dataset(data_dir, key, exported)
            wrapper = json.loads(exported.read_text(encoding="utf-8"))
            assert wrapper["dataset"] == key
            assert "payload" in wrapper
            target = import_reference_dataset(data_dir, key, exported)
            assert target == data_dir / "reference_data" / reference.REFERENCE_DATASETS[key]["filename"]
            reference.validate_dataset_payload(key, json.loads(target.read_text(encoding="utf-8")))
    finally:
        reference.configure_override_root(None)


def test_all_reference_data_round_trip_requires_complete_valid_bundle(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    bundle = tmp_path / "references.zip"
    reference.configure_override_root(source_dir)
    try:
        export_all_reference_data(source_dir, bundle)
        imported = import_all_reference_data(target_dir, bundle)
        assert set(imported) == set(REFERENCE_DATASET_LABELS)
        for key, path in imported.items():
            reference.validate_dataset_payload(key, json.loads(path.read_text(encoding="utf-8")))
    finally:
        reference.configure_override_root(None)


def test_each_user_dataset_can_export_and_import_individually(tmp_path):
    db = tmp_path / "data" / "gfl2.db"
    with Repository(db) as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 6)])
        repo.set_doll_favorite(1008, True)
        repo.replace_game_formations([GameFormation("제대 A", [FormationMember(1008, "네메시스")])])
        data_dir = db.parent
        (data_dir / "tactics").mkdir(parents=True, exist_ok=True)
        (data_dir / "tactics" / "library.json").write_text('{"schema":1,"tactics":[]}', encoding="utf-8")
        (data_dir / "doll_categories.json").write_text('{"schema":1,"categories":{}}', encoding="utf-8")
        (data_dir / "master_data").mkdir(parents=True, exist_ok=True)
        (data_dir / "master_data" / "tactic_equipment_user.json").write_text(
            '{"schema":1,"dolls":{},"weapons_by_uid":{}}', encoding="utf-8"
        )

        for key in USER_DATASET_LABELS:
            out = tmp_path / f"user-{key}.json"
            settings = {"theme": "dark", "worker_count": 3} if key == "app_settings" else None
            export_user_dataset(repo, key, out, settings_payload=settings)
            payload = import_user_dataset(repo, key, out, replace=True)
            if key == "app_settings":
                assert payload["settings"] == settings

        assert repo.inventory_summary()["dolls"] == 1
        assert repo.inventory_summary()["game_formations"] == 1


def test_all_user_data_round_trip_restores_database_files_and_returns_settings(tmp_path):
    src_db = tmp_path / "src-data" / "gfl2.db"
    with Repository(src_db) as src:
        src.replace_dolls([Doll(1008, "네메시스", 60, 6)])
        src.set_doll_favorite(1008, True)
        src.replace_game_formations([GameFormation("백업 제대", [FormationMember(1008, "네메시스")])])
        root = src_db.parent
        (root / "tactics").mkdir(parents=True, exist_ok=True)
        (root / "tactics" / "library.json").write_text('{"schema":1,"tactics":[]}', encoding="utf-8")
        (root / "tactics" / "overlay_state.json").write_text('{"schema":1,"states":{}}', encoding="utf-8")
        (root / "doll_categories.json").write_text('{"schema":1,"categories":{"raid":[1008]}}', encoding="utf-8")
        (root / "master_data").mkdir(parents=True, exist_ok=True)
        (root / "master_data" / "tactic_equipment_user.json").write_text(
            '{"schema":1,"dolls":{"1008":{"weapon_uid":39}},"weapons_by_uid":{}}', encoding="utf-8"
        )
        bundle = tmp_path / "user-all.zip"
        export_all_user_data(src, bundle, settings_payload={"theme": "dark"})

    dst_db = tmp_path / "dst-data" / "gfl2.db"
    with Repository(dst_db) as dst:
        dst.replace_dolls([Doll(9999, "임시", 1, 1)])
        staged = import_all_user_data(dst, bundle, replace=True)
        dolls = dst.rows("dolls")
        assert [row["doll_id"] for row in dolls] == [1008]
        assert dst.rows("game_formations")[0]["name"] == "백업 제대"
        assert staged["app_settings"]["settings"] == {"theme": "dark"}
        assert json.loads((dst_db.parent / "doll_categories.json").read_text(encoding="utf-8"))["categories"] == {"raid": [1008]}
        assert (dst_db.parent / "master_data" / "tactic_equipment_user.json").is_file()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_user_csv_bundle_import_rebuilds_equipment_sidecar_from_csv_only(tmp_path):
    staging = tmp_path / "external"
    staging.mkdir()
    _write_csv(staging / "dolls.csv", ["doll_id", "level", "rank"], [{"doll_id": 1008, "level": 60, "rank": 6}])
    _write_csv(staging / "remoldings.csv", ["uid", "stat1", "stat2", "stat3"], [])
    _write_csv(staging / "formations.csv", ["formation_name", "position", "doll_id", "doll_name"], [])
    _write_csv(
        staging / "equipment_dolls.csv",
        ["doll_id", "weapon_uid", "fixed_key_ids", "common_key_ids", "common_key_uids", "expansion_key_ids"],
        [{"doll_id": 1008, "weapon_uid": 39, "fixed_key_ids": "202501", "common_key_ids": "93029", "common_key_uids": "", "expansion_key_ids": "921025"}],
    )
    _write_csv(
        staging / "weapons.csv",
        ["uid", "item_id", "item_id_candidates", "level", "rank", "equipped_doll_id"],
        [{"uid": 39, "item_id": 10743, "item_id_candidates": "10743 10744", "level": 60, "rank": 6, "equipped_doll_id": 1008}],
    )
    (staging / "manifest.json").write_text(
        json.dumps({"schema_id": "gfl2-user-csv-backup", "schema_version": 1}),
        encoding="utf-8",
    )
    bundle = tmp_path / "user-data.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in staging.iterdir():
            zf.write(path, path.name)

    db = tmp_path / "main-data" / "gfl2.db"
    with Repository(db) as repo:
        result = import_user_csv_bundle(repo, bundle)
        assert result["dolls"] == 1
        sidecar = json.loads((db.parent / "master_data" / "tactic_equipment_user.json").read_text(encoding="utf-8"))
        assert sidecar["dolls"]["1008"]["weapon_uid"] == 39
        assert sidecar["dolls"]["1008"]["fixed_key_ids"] == [202501]
        assert sidecar["weapons_by_uid"]["39"]["item_id_candidates"] == [10743, 10744]
