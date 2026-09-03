from __future__ import annotations

import json
from pathlib import Path

import pytest

from gfl2tool.atomic_io import atomic_text_writer, atomic_write_json
from gfl2tool.models import Doll, GameFormation, FormationMember
from gfl2tool.repository import Repository
from gfl2tool.services.formations import FormationService


def test_atomic_text_writer_preserves_existing_destination_on_failure(tmp_path):
    target = tmp_path / "important.json"
    target.write_text("known-good", encoding="utf-8")

    with pytest.raises(RuntimeError):
        with atomic_text_writer(target) as handle:
            handle.write("partial-new-data")
            raise RuntimeError("simulated disk/write pipeline failure")

    assert target.read_text(encoding="utf-8") == "known-good"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_json_replaces_complete_document_and_leaves_no_temp(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")
    atomic_write_json(target, {"한글": [1, 2, 3]}, ensure_ascii=False, indent=2)
    assert json.loads(target.read_text(encoding="utf-8")) == {"한글": [1, 2, 3]}
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []



def test_game_formation_import_rolls_back_plan_and_first_member_when_second_member_fails(tmp_path):
    db = tmp_path / "import-atomic.db"
    with Repository(db) as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1), Doll(1002, "B", 60, 1)])
        repo.replace_game_formations([
            GameFormation("게임 제대", [FormationMember(1001, "A"), FormationMember(1002, "B")])
        ])
        game_id = FormationService(repo).list_game_formations()[0]["id"]
        repo.con.execute(
            """CREATE TRIGGER fail_second_import_member BEFORE INSERT ON formation_members
               WHEN NEW.position=2 BEGIN SELECT RAISE(ABORT, 'forced second member failure'); END"""
        )
        repo.con.commit()

        with pytest.raises(Exception, match="forced second member failure"):
            FormationService(repo).import_game_formation(game_id)

        assert repo.con.execute("SELECT COUNT(*) FROM formation_plans").fetchone()[0] == 0
        assert repo.con.execute("SELECT COUNT(*) FROM formation_members").fetchone()[0] == 0
        assert not repo.con.in_transaction


def test_game_formation_import_commits_whole_plan_once_on_success(tmp_path):
    db = tmp_path / "import-success.db"
    with Repository(db) as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1), Doll(1002, "B", 60, 1)])
        repo.replace_game_formations([
            GameFormation("게임 제대", [FormationMember(1001, "A"), FormationMember(1002, "B")])
        ])
        game_id = FormationService(repo).list_game_formations()[0]["id"]
        plan_id = FormationService(repo).import_game_formation(game_id)
        plan = FormationService(repo).get(plan_id)
        assert [member["doll_id"] for member in plan["members"]] == [1001, 1002]
        assert not repo.con.in_transaction






def test_nested_repository_transactions_roll_back_inner_writes_with_outer_failure(tmp_path):
    with Repository(tmp_path / "nested.db") as repo:
        with pytest.raises(RuntimeError):
            with repo.transaction():
                repo.con.execute("INSERT INTO meta(key,value) VALUES('outer','1')")
                with repo.transaction():
                    repo.con.execute("INSERT INTO meta(key,value) VALUES('inner','1')")
                raise RuntimeError("abort outer")
        assert repo.get_meta("outer") is None
        assert repo.get_meta("inner") is None
        assert not repo.con.in_transaction






def _current_master_payload(tmp_path, *, version: str, marker: str) -> dict:
    from gfl2tool.services.game_master import MASTER_SCHEMA_ID, MASTER_SCHEMA_VERSION
    return {
        "schema_id": MASTER_SCHEMA_ID,
        "schema_version": MASTER_SCHEMA_VERSION,
        "generated_at": "2026-08-31T00:00:00+00:00",
        "game_root": str(tmp_path),
        "game_version": version,
        "counts": {"dolls": 0},
        "dolls": {},
        "marker": marker,
    }






