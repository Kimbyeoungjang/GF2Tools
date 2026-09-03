import json
import sqlite3

import pytest

from gfl2tool.models import Doll
from gfl2tool.repository import Repository
from gfl2tool.services.formations import FormationService
from gfl2tool.services.remoldings import RemoldingPatternService
from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService


def _fail_plan_update(repo: Repository) -> None:
    repo.con.execute(
        "CREATE TRIGGER fail_plan_update BEFORE UPDATE ON formation_plans "
        "BEGIN SELECT RAISE(ABORT,'forced plan update failure'); END"
    )


def test_set_member_rolls_back_member_insert_when_plan_timestamp_update_fails(tmp_path):
    with Repository(tmp_path / "member.db") as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1)])
        svc = FormationService(repo); plan = svc.create("team")
        _fail_plan_update(repo)
        with pytest.raises(sqlite3.IntegrityError, match="forced plan update failure"):
            svc.set_member(plan, 1, 1001)
        assert repo.con.execute("SELECT COUNT(*) FROM formation_members WHERE plan_id=?", (plan,)).fetchone()[0] == 0
        assert not repo.con.in_transaction


def test_set_member_target_update_rolls_back_on_plan_timestamp_failure(tmp_path):
    with Repository(tmp_path / "targets.db") as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1)])
        svc = FormationService(repo); plan = svc.create("team"); svc.set_member(plan, 1, 1001)
        before = repo.con.execute(
            "SELECT remolding_targets_json FROM formation_members WHERE plan_id=? AND position=1", (plan,)
        ).fetchone()[0]
        _fail_plan_update(repo)
        with pytest.raises(sqlite3.IntegrityError):
            svc.set_member(
                plan, 1, 1001,
                remolding_targets={"sentinel_1": {"level": 1, "priority": 1}},
            )
        after = repo.con.execute(
            "SELECT remolding_targets_json FROM formation_members WHERE plan_id=? AND position=1", (plan,)
        ).fetchone()[0]
        assert after == before
        assert not repo.con.in_transaction


def test_remove_member_rolls_back_delete_when_plan_timestamp_update_fails(tmp_path):
    with Repository(tmp_path / "remove.db") as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1)])
        svc = FormationService(repo); plan = svc.create("team"); svc.set_member(plan, 1, 1001)
        _fail_plan_update(repo)
        with pytest.raises(sqlite3.IntegrityError):
            svc.remove_member(plan, 1)
        assert repo.con.execute("SELECT COUNT(*) FROM formation_members WHERE plan_id=?", (plan,)).fetchone()[0] == 1
        assert not repo.con.in_transaction


def test_remolding_pattern_slot_rolls_back_when_parent_timestamp_update_fails(tmp_path):
    with Repository(tmp_path / "pattern.db") as repo:
        svc = RemoldingPatternService(repo); pattern = svc.create("P")
        repo.con.execute(
            "CREATE TRIGGER fail_pattern_update BEFORE UPDATE ON remolding_patterns "
            "BEGIN SELECT RAISE(ABORT,'forced pattern update failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            svc.set_slot(pattern, 1, option_key="sentinel_1")
        assert repo.con.execute(
            "SELECT COUNT(*) FROM remolding_pattern_slots WHERE pattern_id=?", (pattern,)
        ).fetchone()[0] == 0
        assert not repo.con.in_transaction


def test_dummy_character_delete_is_atomic_across_profile_targets_and_overrides(tmp_path):
    with Repository(tmp_path / "dummy.db") as repo:
        svc = RemoldingRecommendationService(repo)
        dummy = svc.create_dummy_character("D", slot_counts={"sentinel": 6})
        key = dummy["key"]
        svc.save_target_profile(key, {"sentinel_1": {"level": 1, "priority": 1}})
        svc.set_override(key, "sentinel_1", score_adjustment=1, note="x")
        repo.con.execute(
            "CREATE TRIGGER fail_override_delete BEFORE DELETE ON remolding_option_overrides "
            "BEGIN SELECT RAISE(ABORT,'forced override delete failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            svc.delete_dummy_character(key)
        assert repo.con.execute("SELECT COUNT(*) FROM remolding_character_profiles WHERE character_key=?", (key,)).fetchone()[0] == 1
        assert repo.con.execute("SELECT COUNT(*) FROM remolding_target_profiles WHERE character_key=?", (key,)).fetchone()[0] == 1
        assert repo.con.execute("SELECT COUNT(*) FROM remolding_option_overrides WHERE character_key=?", (key,)).fetchone()[0] == 1
        assert not repo.con.in_transaction
