from __future__ import annotations

import json

from gfl2tool.services.doll_skill_cycles import DollSkillCycleStore, replace_skill_cycles_in_tactic
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


def test_tactic_cycles_start_blank_until_explicit_replace():
    tactic = Tactic(title="x", units=[TacticUnit(unit_key="a", alias="센", doll_id=101)], steps=[TacticStep(name="T1")])
    assert replace_skill_cycles_in_tactic(tactic) is False
    assert tactic.steps[0].cycle == ""


def test_explicit_skill_cycle_replace_expands_steps_and_overwrites_old_text():
    tactic = Tactic(
        title="x",
        units=[
            TacticUnit(unit_key="a", alias="센", doll_id=101, skill_cycle=["1", "2", "3"], skill_cycle_source="일반 사이클"),
            TacticUnit(unit_key="b", alias="토", doll_id=202, skill_cycle=["A", "B"], skill_cycle_source="제대 · 테스트"),
        ],
        steps=[TacticStep(name="T1", cycle="OCR 기존")],
    )
    assert replace_skill_cycles_in_tactic(tactic) is True
    assert len(tactic.steps) == 3
    assert tactic.steps[0].cycle == "센 1 · 토 A"
    assert tactic.steps[1].cycle == "센 2 · 토 B"
    assert tactic.steps[2].cycle == "센 3"
    assert all(step.cycle_auto for step in tactic.steps)


def test_explicit_skill_cycle_replace_refreshes_from_current_roster():
    unit = TacticUnit(unit_key="a", alias="센", doll_id=101, skill_cycle=["1", "2"])
    tactic = Tactic(title="x", units=[unit], steps=[TacticStep(name="T1", cycle="old"), TacticStep(name="T2")])
    replace_skill_cycles_in_tactic(tactic)
    assert tactic.steps[0].cycle == "센 1"
    unit.skill_cycle = ["X", "Y"]
    replace_skill_cycles_in_tactic(tactic)
    assert tactic.steps[0].cycle == "센 X"
    assert tactic.steps[1].cycle == "센 Y"


def test_explicit_replace_clears_stale_extra_turn_cycle_text():
    unit = TacticUnit(unit_key="a", alias="센", doll_id=101, skill_cycle=["평"])
    tactic = Tactic(
        title="x", units=[unit],
        steps=[TacticStep(name="T1", cycle="OCR 1"), TacticStep(name="T2", cycle="OCR 2")],
    )
    replace_skill_cycles_in_tactic(tactic)
    assert tactic.steps[0].cycle == "센 평"
    assert tactic.steps[1].cycle == ""
    assert tactic.steps[1].cycle_auto is False
