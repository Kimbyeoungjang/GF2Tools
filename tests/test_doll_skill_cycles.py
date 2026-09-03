from __future__ import annotations

import json

from gfl2tool.services.doll_skill_cycles import DollSkillCycleStore, apply_skill_cycles_to_tactic
from gfl2tool.tactics import Tactic, TacticStep, TacticUnit


def test_skill_cycle_store_keeps_one_profile_per_doll(tmp_path):
    store = DollSkillCycleStore(tmp_path)
    store.set_actions(101, ["1", "2", "3"])
    assert store.actions_for(101) == ["1", "2", "3"]
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["profiles"]["101"] == ["1", "2", "3"]


def test_v1_rank_profiles_migrate_to_longest_sequence(tmp_path):
    store = DollSkillCycleStore(tmp_path)
    store.path.write_text(json.dumps({
        "schema_id": "gfl2-doll-skill-cycles", "schema_version": 1,
        "profiles": {"101": {"ranks": {"0": ["평"], "3": ["스1", "평", "스2"]}}},
    }), encoding="utf-8")
    assert store.actions_for(101) == ["스1", "평", "스2"]


def test_skill_cycles_auto_expand_steps_and_preserve_manual_text(tmp_path):
    store = DollSkillCycleStore(tmp_path)
    store.set_actions(101, ["1", "2", "3"]); store.set_actions(202, ["A", "B"])
    tactic = Tactic(title="x", units=[TacticUnit(unit_key="a", alias="센", doll_id=101), TacticUnit(unit_key="b", alias="토", doll_id=202)], steps=[TacticStep(name="T1", cycle="수동")])
    assert apply_skill_cycles_to_tactic(tactic, store) is True
    assert len(tactic.steps) == 3
    assert tactic.steps[0].cycle == "수동"
    assert tactic.steps[1].cycle == "센 2 · 토 B"
    assert tactic.steps[2].cycle == "센 3"


def test_auto_cycle_is_refreshable_but_manual_cycle_is_not_overwritten(tmp_path):
    store = DollSkillCycleStore(tmp_path); store.set_actions(101, ["1", "2"])
    tactic = Tactic(title="x", units=[TacticUnit(unit_key="a", alias="센", doll_id=101)], steps=[])
    apply_skill_cycles_to_tactic(tactic, store)
    assert tactic.steps[0].cycle_auto is True
    store.set_actions(101, ["X", "Y"]); apply_skill_cycles_to_tactic(tactic, store)
    assert tactic.steps[0].cycle == "센 X"
    tactic.steps[0].cycle = "직접 작성"; tactic.steps[0].cycle_auto = False
    store.set_actions(101, ["Z", "Y"]); apply_skill_cycles_to_tactic(tactic, store)
    assert tactic.steps[0].cycle == "직접 작성"


def test_removed_profiles_clear_only_auto_generated_cycles(tmp_path):
    store = DollSkillCycleStore(tmp_path); store.set_actions(101, ["평", "스1"])
    tactic = Tactic(title="x", units=[TacticUnit(unit_key="a", alias="센", doll_id=101)], steps=[])
    apply_skill_cycles_to_tactic(tactic, store)
    tactic.steps[1].cycle = "수동"; tactic.steps[1].cycle_auto = False
    store.set_actions(101, []); apply_skill_cycles_to_tactic(tactic, store)
    assert tactic.steps[0].cycle == ""
    assert tactic.steps[1].cycle == "수동"
