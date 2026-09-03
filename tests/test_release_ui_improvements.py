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


def test_tactic_roster_has_explicit_cycle_sources_and_formation_import():
    source = (ROOT / "src/gfl2tool/qtui/dialogs/tactic_units.py").read_text(encoding="utf-8")
    assert '제대에서 가져오기…' in source
    assert '제대 사이클 불러오기…' in source
    assert '일반 사이클 불러오기' in source
    assert '이 택틱에서는 스킬 사이클 미지정' in source
    assert 'skill_cycle_source' in source


def test_grouped_inventory_single_selection_clears_old_section_current_index():
    source = (ROOT / "src/gfl2tool/qtui/grouped_dolls.py").read_text(encoding="utf-8")
    assert 'SelectionMode.SingleSelection' in source
    assert 'selection.setCurrentIndex(' in source
    assert 'QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate' in source


def test_tactic_cover_uses_nearest_pointer_edge_instead_of_fixed_direction_combo():
    widget = (ROOT / "src/gfl2tool/qtui/tactic_widgets.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gfl2tool/qtui/pages/tactics.py").read_text(encoding="utf-8")
    assert 'def _cover_target_at' in widget
    assert 'distances = {' in widget
    assert 'self.cover_edge = QComboBox()' not in page
    assert '마우스 포인터와 가장 가까운 격자 변' in page


def test_ocr_subprocesses_are_hidden_in_windows_gui_builds():
    service = (ROOT / "src/gfl2tool/services/ocr_import.py").read_text(encoding="utf-8")
    tactic = (ROOT / "src/gfl2tool/tactic_image_import.py").read_text(encoding="utf-8")
    assert 'CREATE_NO_WINDOW' in service
    assert 'STARTF_USESHOWWINDOW' in service
    assert service.count('**ocr_subprocess_kwargs()') >= 2
    assert tactic.count('**ocr_subprocess_kwargs()') >= 3
