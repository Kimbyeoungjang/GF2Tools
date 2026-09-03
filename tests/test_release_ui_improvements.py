from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tactic_overlay_is_single_instance_and_delete_on_close():
    page = (ROOT / "src/gfl2tool/qtui/pages/tactics.py").read_text(encoding="utf-8")
    overlay = (ROOT / "src/gfl2tool/qtui/tactic_overlay.py").read_text(encoding="utf-8")
    assert 'self.overlay_button.setEnabled(False)' in page
    assert 'for extra in visible[1:]' in page
    assert 'Qt.WidgetAttribute.WA_DeleteOnClose' in overlay
    assert 'stateSaved.emit(self.tactic.tactic_id, self._state())' in overlay


def test_remolding_calculation_method_is_top_button_not_detail_tab():
    source = (ROOT / "src/gfl2tool/qtui/pages/remolding_optimizer.py").read_text(encoding="utf-8")
    assert 'QPushButton("계산 방식")' in source
    assert 'setWindowTitle("리몰딩 최적화 · 계산 방식")' in source
    assert 'tabs.addTab(page, "계산 방식")' not in source


def test_tactic_roster_has_simple_skill_cycle_editor():
    source = (ROOT / "src/gfl2tool/qtui/dialogs/tactic_units.py").read_text(encoding="utf-8")
    assert 'T1~Tn 스킬 사이클 편집…' in source
    assert 'initial_rank=unit.rank' not in source
    assert 'T1~T{len(actions)}' in source
