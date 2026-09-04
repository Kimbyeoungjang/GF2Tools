from __future__ import annotations

import json

from gfl2tool import reference
from gfl2tool.qtui.data import OwnedDollCatalog
from gfl2tool.repository import Repository
from gfl2tool.services.doll_skill_cycles import replace_skill_cycles_in_tactic
from gfl2tool.services.ocr_import import ContinuousOcrGate, parse_inventory_ocr
from gfl2tool.tactics import Tactic, TacticMarker, TacticStep, TacticUnit


def _sig(value: int, count: int = 64 * 36) -> bytes:
    return bytes([max(0, min(255, value))]) * count


def test_continuous_ocr_gate_forces_progress_when_animation_never_settles():
    gate = ContinuousOcrGate(stable_seconds=0.45, max_settle_seconds=2.0)
    state, _ = gate.observe(_sig(10), 0.0)
    assert state == "stabilizing"
    state, _ = gate.observe(_sig(30), 0.5)
    assert state == "stabilizing"
    state, _ = gate.observe(_sig(50), 1.0)
    assert state == "stabilizing"
    state, _ = gate.observe(_sig(70), 2.1)
    assert state == "ready"
    gate.mark_ocr(_sig(70))
    state, _ = gate.observe(_sig(70), 2.6)
    assert state == "unchanged"


def test_resistance_wording_is_preserved_in_ocr_candidate():
    _dolls, options = parse_inventory_ocr("Lv.3 탁류 저항\n")
    assert [(row.name, row.level) for row in options] == [("탁류 저항", 3)]


def test_manual_picker_catalog_can_resolve_rest_portrait_without_owned_csv(tmp_path):
    data_root = tmp_path
    reference_dir = data_root / "reference_data"
    reference_dir.mkdir(parents=True)
    # Only program data is present; the owned dolls table intentionally stays empty.
    reference_dir.joinpath("program_dolls.json").write_text(
        json.dumps({
            "schema_version": 1,
            "items": [{
                "id": 1001,
                "name_ko": "샤크리",
                "duty_ko": "센티넬",
                "element_type": "burn",
                "assets": {"portrait": "assets/portraits/manual-picker.webp"},
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    portrait = data_root / "remote-api-cache" / "site" / "assets" / "portraits" / "manual-picker.webp"
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b"not-an-image-but-present")

    reference.configure_override_root(data_root)
    try:
        with Repository(data_root / "owned.db") as repo:
            assert repo.rows("dolls") == []
            rows = OwnedDollCatalog(repo).all_reference_entries_with_portraits()
            row = next(item for item in rows if int(item.get("doll_id") or 0) == 1001)
            assert row["owned"] is False
            assert row["portrait_path"] == str(portrait)
    finally:
        reference.configure_override_root(None)


def test_explicit_tactic_cycle_replace_overwrites_ocr_text_only():
    first = TacticUnit(doll_id=1001, name="샤크리", alias="샤", skill_cycle=["평", "스1"])
    second = TacticUnit(doll_id=1002, name="그로자", alias="그", skill_cycle=["스2"])
    marker = TacticMarker(kind="unit", row=1, col=2, label="X")
    tactic = Tactic(
        title="OCR import",
        units=[first, second],
        steps=[
            TacticStep(name="T1", cycle="OCR 기존 1", note="유지", markers=[marker]),
            TacticStep(name="T2", cycle="OCR 기존 2"),
            TacticStep(name="T3", cycle="OCR 기존 3"),
        ],
    )
    assert replace_skill_cycles_in_tactic(tactic) is True
    assert tactic.steps[0].cycle == "샤 평 · 그 스2"
    assert tactic.steps[1].cycle == "샤 스1"
    assert tactic.steps[2].cycle == ""
    assert tactic.steps[0].note == "유지"
    assert tactic.steps[0].markers[0] is marker


def test_tactic_page_does_not_auto_apply_cycle_when_roster_dialog_closes():
    source = open("src/gfl2tool/qtui/pages/tactics.py", encoding="utf-8").read()
    assert "cycle_changed = apply_skill_cycles_to_tactic(tactic)" not in source
    assert "내 인형 사이클로 교체…" in source
    assert "replace_skill_cycles_in_tactic(tactic)" in source


def test_ocr_apply_ui_uses_grouped_queue_only():
    source = open("src/gfl2tool/qtui/data_entry.py", encoding="utf-8").read()
    assert "인식된 리몰딩 옵션" not in source
    assert "선택 옵션 모두 개별 추가" not in source
    assert "선택 옵션을 1개 리몰딩으로 묶기" not in source
    assert "pieces = [_manual_remolding(group) for group in self._live_groups]" in source


def test_continuous_ocr_gate_keeps_transition_active_if_animation_returns_to_previous_frame():
    old = _sig(20)
    moving = _sig(60)
    gate = ContinuousOcrGate(stable_seconds=0.45, max_settle_seconds=2.0)
    gate.mark_ocr(old)

    state, _ = gate.observe(moving, 10.0)
    assert state == "changed"
    # A transition animation can briefly render pixels nearly identical to the
    # previous item. It must not cancel/reset the already-observed transition.
    state, _ = gate.observe(old, 10.5)
    assert state == "stabilizing"
    state, _ = gate.observe(old, 11.0)
    assert state == "ready"
    assert gate.settling_elapsed(11.0) == 1.0


def test_continuous_ocr_queue_allows_equal_value_items_after_separate_transitions():
    source = open("src/gfl2tool/qtui/data_entry.py", encoding="utf-8").read()
    assert "Two distinct remolding pieces can" in source
    assert "same option/level combination" not in source.lower()
    assert "any(self._live_group_signature" not in source


def test_corrected_raw_text_replaces_latest_live_candidate():
    source = open("src/gfl2tool/qtui/data_entry.py", encoding="utf-8").read()
    assert "self._live_groups[index] = [" in source
    assert "수정 OCR 원문 적용 · 대기열" in source
    assert "self._parse_text(sync_live_queue=False)" in source


def test_continuous_ocr_waits_before_advertising_next_item_and_polls_fast():
    source = open("src/gfl2tool/qtui/data_entry.py", encoding="utf-8").read()
    assert "self._live_timer.setInterval(150)" in source
    assert "self._live_next_ready_at = time.monotonic() + 1.0" in source
    assert "다음 항목 준비 중 1.0초" in source
    assert "다음 항목으로 넘겨도 됩니다." in source
    assert "if self._live_enabled:" in source


def test_continuous_ocr_uses_queue_as_the_only_option_apply_surface():
    source = open("src/gfl2tool/qtui/data_entry.py", encoding="utf-8").read()
    assert "인식된 리몰딩 옵션" not in source
    assert "self.option_panel" not in source
    assert "대기열 전체 반영" in source
    assert "pieces = [_manual_remolding(group) for group in self._live_groups]" in source


def test_tactic_export_preserves_layout_while_scaling_pixels_without_forced_dpi():
    source = open("src/gfl2tool/qtui/tactic_widgets.py", encoding="utf-8").read()
    theme_source = open("src/gfl2tool/qtui/theme.py", encoding="utf-8").read()
    assert "cell_panel: QSize = QSize(322, 388)" in source
    assert "export_scale: float = theme.EXPORT_SCALE" in source
    assert "painter.scale(scale, scale)" in source
    assert "setDotsPerMeterX" not in source
    assert "setDotsPerMeterY" not in source
    assert "EXPORT_DPI" not in theme_source
    assert 'EXPORT_GRID = "#8295a2"' in theme_source
    assert 'EXPORT_BOSS = "#F26C1C"' in theme_source
    assert '_export_step_label(step.name, index)' in source
    assert 'return "제대\\n배치"' in source
    assert 'ratios = (0.15, 0.18, 0.24, 0.14, 0.20, 0.09)' in source


def test_one_shot_ocr_also_enters_the_same_remolding_queue():
    source = open("src/gfl2tool/qtui/data_entry.py", encoding="utf-8").read()
    assert 'self._append_live_group(group, suffix="단발 OCR")' in source
    assert "단발 OCR 완료 · 리몰딩 후보" in source
    assert "수정 OCR 원문 적용 · 대기열" in source
