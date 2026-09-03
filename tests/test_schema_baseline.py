from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gfl2tool.cli import build_parser
from gfl2tool.models import FormationMember, GameFormation
from gfl2tool.repository import Repository, SCHEMA_ID, SCHEMA_VERSION, SchemaMismatchError
from gfl2tool.snapshot import SNAPSHOT_SCHEMA_ID, SNAPSHOT_VERSION, export_snapshot, import_snapshot


EXPECTED_TABLES = {
    "meta",
    "dolls",
    "remoldings",
    "game_formations",
    "formation_plans",
    "formation_members",
    "remolding_patterns",
    "remolding_pattern_slots",
    "remolding_score_settings",
    "remolding_option_overrides",
    "remolding_target_profiles",
    "remolding_character_profiles",
}


def _columns(repo: Repository, table: str) -> set[str]:
    return {str(row[1]) for row in repo.con.execute(f"PRAGMA table_info({table})")}


def test_new_database_uses_only_current_schema():
    # The schema contract itself is asserted against a real temporary DB in the
    # next test. These constants define the current schema family explicitly.
    assert SCHEMA_ID == SNAPSHOT_SCHEMA_ID == "gfl2-tools"
    assert SCHEMA_VERSION == SNAPSHOT_VERSION == 2


def test_current_database_contains_no_removed_inventory_tables_or_columns(tmp_path):
    with Repository(tmp_path / "current.db") as repo:
        tables = {
            str(row[0])
            for row in repo.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == EXPECTED_TABLES
        assert _columns(repo, "dolls") == {
            "doll_id", "name", "level", "rank", "illustration_path", "favorite", "updated_at"
        }
        assert _columns(repo, "formation_members") == {
            "plan_id", "position", "doll_id", "doll_name", "remolding_uids_json", "remolding_targets_json"
        }
        assert repo.get_meta("schema_id") == SCHEMA_ID
        assert repo.get_meta("schema_version") == str(SCHEMA_VERSION)





def test_v1_database_migrates_by_dropping_only_noncurrent_diagnostic_tables(tmp_path):
    path = tmp_path / "v1.db"
    with Repository(path) as repo:
        repo.replace_game_formations([GameFormation("keep", [FormationMember(1008, "네메시스")])])
        repo.con.execute("CREATE TABLE diagnostic_a(id INTEGER PRIMARY KEY, note TEXT)")
        repo.con.execute("CREATE TABLE diagnostic_b(id INTEGER PRIMARY KEY, blob BLOB)")
        repo.con.execute("INSERT INTO diagnostic_a(note) VALUES('old')")
        repo.con.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        repo.con.commit()

    with Repository(path) as migrated:
        tables = {
            str(row[0])
            for row in migrated.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == EXPECTED_TABLES
        assert migrated.get_meta("schema_version") == "2"
        assert migrated.rows("game_formations")[0]["name"] == "keep"

def test_wrong_schema_version_is_rejected_without_automatic_migration(tmp_path):
    path = tmp_path / "wrong-version.db"
    with Repository(path) as repo:
        repo.con.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        repo.con.commit()

    with pytest.raises(SchemaMismatchError):
        Repository(path)


def test_foreign_database_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "foreign.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT INTO meta(key,value) VALUES('schema_id','foreign-schema')")
    con.execute("INSERT INTO meta(key,value) VALUES('schema_version','999')")
    con.execute("CREATE TABLE foreign_data(uid TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    before = path.read_bytes()

    with pytest.raises(SchemaMismatchError, match="지원하지 않는 DB 스키마|다른 스키마는 열지 않습니다"):
        Repository(path)

    assert path.read_bytes() == before



def test_current_schema_identity_does_not_allow_extra_tables(tmp_path):
    path = tmp_path / "tampered.db"
    with Repository(path) as repo:
        repo.con.execute("CREATE TABLE obsolete_inventory(uid TEXT PRIMARY KEY)")
        repo.con.commit()
    with pytest.raises(SchemaMismatchError, match="테이블 구성이 기준 스키마와 다릅니다"):
        Repository(path)



def test_current_schema_identity_rejects_unexpected_trigger(tmp_path):
    path = tmp_path / "tampered-trigger.db"
    with Repository(path) as repo:
        repo.con.execute(
            """CREATE TRIGGER unexpected_doll_write AFTER INSERT ON dolls
               BEGIN UPDATE meta SET value=value WHERE key='schema_version'; END"""
        )
        repo.con.commit()
    with pytest.raises(SchemaMismatchError, match="제약조건/인덱스 구성이 기준 스키마와 다릅니다"):
        Repository(path)


def test_current_schema_identity_rejects_changed_column_constraint(tmp_path):
    path = tmp_path / "tampered-default.db"
    with Repository(path):
        pass
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE dolls RENAME TO dolls_original")
    con.execute(
        """CREATE TABLE dolls (
             doll_id INTEGER PRIMARY KEY, name TEXT, level INTEGER NOT NULL, rank INTEGER NOT NULL,
             illustration_path TEXT, favorite INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)"""
    )
    con.execute(
        """INSERT INTO dolls(doll_id,name,level,rank,illustration_path,favorite,updated_at)
           SELECT doll_id,name,level,rank,illustration_path,favorite,updated_at FROM dolls_original"""
    )
    con.execute("DROP TABLE dolls_original")
    con.commit()
    con.close()
    with pytest.raises(SchemaMismatchError, match="제약조건/인덱스 구성이 기준 스키마와 다릅니다"):
        Repository(path)

def test_current_snapshot_requires_exact_schema_identity(tmp_path):
    source_path = tmp_path / "source.db"
    with Repository(source_path) as source:
        snapshot = export_snapshot(source, tmp_path / "snapshot.json")
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["schema_id"] == SCHEMA_ID
    assert payload["snapshot_version"] == SCHEMA_VERSION

    payload.pop("schema_id")
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    with Repository(tmp_path / "target.db") as target:
        with pytest.raises(ValueError, match="unsupported snapshot schema"):
            import_snapshot(target, snapshot)


def test_snapshot_rejects_removed_tables_instead_of_sanitizing_them(tmp_path):
    with Repository(tmp_path / "source.db") as source:
        snapshot = export_snapshot(source, tmp_path / "snapshot.json")
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["data"]["weapons"] = []
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    with Repository(tmp_path / "target.db") as target:
        with pytest.raises(ValueError, match="unknown tables: weapons"):
            import_snapshot(target, snapshot)


def test_game_formation_storage_has_only_doll_identity(tmp_path):
    with Repository(tmp_path / "formations.db") as repo:
        repo.replace_game_formations([GameFormation("A", [FormationMember(1008, "네메시스")])])
        members = json.loads(repo.con.execute("SELECT members_json FROM game_formations").fetchone()[0])
        assert members == [{"doll_id": 1008, "doll_name": "네메시스"}]



def test_current_schema_identity_does_not_allow_extra_columns(tmp_path):
    path = tmp_path / "tampered-column.db"
    with Repository(path) as repo:
        repo.con.execute("ALTER TABLE dolls ADD COLUMN obsolete_field TEXT")
        repo.con.commit()
    with pytest.raises(SchemaMismatchError, match="컬럼 구성이 기준 스키마와 다릅니다"):
        Repository(path)


def test_gui_entrypoint_reports_only_schema_mismatch_as_database_error():
    source = (Path(__file__).resolve().parents[1] / "src/gfl2tool/qtgui.py").read_text(encoding="utf-8")
    assert "except SchemaMismatchError as exc:" in source
    assert "except RuntimeError as exc:" not in source
    assert "현재 릴리스에서 생성한 DB만 지원합니다" in source
    assert "기존 data/gfl2.db를 다른 이름으로 보관하거나 삭제" in source




def test_cli_exposes_only_current_planner_inventory_and_formation_fields():
    parser = build_parser()
    assert parser.parse_args(["list", "remoldings"]).kind == "remoldings"
    args = parser.parse_args(["formation", "set-member", "1", "1", "1008", "--remoldings", "r1,r2"])
    assert args.remoldings == "r1,r2"

    with pytest.raises(SystemExit):
        parser.parse_args(["list", "weapons"])
    for removed in ("--weapon", "--attachments", "--common-keys", "--fixed-keys", "--expansion-keys"):
        with pytest.raises(SystemExit):
            parser.parse_args(["formation", "set-member", "1", "1", "1008", removed, "x"])


def test_user_facing_qt_has_no_removed_inventory_controls():
    root = Path(__file__).resolve().parents[1]
    main = (root / "src/gfl2tool/qtui/mainwindow.py").read_text(encoding="utf-8")
    dashboard = (root / "src/gfl2tool/qtui/pages/dashboard.py").read_text(encoding="utf-8")
    formation = (root / "src/gfl2tool/qtui/dialogs/formation_optimize.py").read_text(encoding="utf-8")
    inventory = (root / "src/gfl2tool/qtui/pages/inventory.py").read_text(encoding="utf-8")

    assert "공용키" not in formation
    assert "common_key" not in formation
    assert '"무기"' not in dashboard and '"부착물"' not in dashboard and '"공용키"' not in dashboard
    assert 'page_layout(self, "보유 현황")' in inventory


def test_current_schema_assets_are_exactly_the_supported_sets():
    root = Path(__file__).resolve().parents[1]
    schema_root = root / "schemas"
    schema_files = {path.name for path in schema_root.glob("*.proto")} if schema_root.is_dir() else set()
    reference_files = {path.name for path in (root / "src/gfl2tool/reference_data").iterdir() if path.is_file()}
    assert schema_files == set()
    assert reference_files == {"doll_asset_aliases.json", "dolls.json", "program_dolls.json", "program_remolding_catalog.json", "program_version.json", "remolding_rules.json", "tactic_equipment_catalog.json"}


