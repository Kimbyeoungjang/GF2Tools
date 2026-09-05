from __future__ import annotations

from pathlib import Path

import pytest

from gfl2tool.models import Doll
from gfl2tool.repository import Repository
from gfl2tool.services.formations import FormationService
from gfl2tool.tactics import TacticMarker
from gfl2tool.qtui import theme


ROOT = Path(__file__).resolve().parents[1]


def test_primary_pages_use_scroll_safe_content_canvas() -> None:
    source = (ROOT / "src/gfl2tool/qtui/widgets.py").read_text(encoding="utf-8")
    assert 'scroll.setObjectName("PageScroll")' in source
    assert "PAGE_MIN_CONTENT_WIDTH = 1020" in source
    assert "QLayout.SizeConstraint.SetMinimumSize" in source
    assert "ScrollBarAsNeeded" in source


def test_checklist_ui_has_roomier_rows_and_explicit_edit_button() -> None:
    page = (ROOT / "src/gfl2tool/qtui/pages/checklist.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "src/gfl2tool/qtui/pages/dashboard.py").read_text(encoding="utf-8")
    style = (ROOT / "src/gfl2tool/qtui/theme.py").read_text(encoding="utf-8")
    assert 'QPushButton("선택 편집")' in page
    assert "DoubleClicked" in page
    assert 'listing.setObjectName("ChecklistList")' in page
    assert 'checkbox.setObjectName("ChecklistCheckBox")' in dashboard
    assert "QCheckBox#ChecklistCheckBox" in style
    assert "QListWidget#ChecklistList::item" in style


def test_formation_add_members_fills_only_free_slots_and_rejects_overflow(tmp_path: Path) -> None:
    dolls = [Doll(1000 + index, f"D{index}", 60, 1) for index in range(1, 8)]
    with Repository(tmp_path / "formation.db") as repo:
        repo.replace_dolls(dolls)
        svc = FormationService(repo)
        plan_id = svc.create("RC1")
        svc.set_member(plan_id, 2, 1001)
        svc.set_member(plan_id, 5, 1002)
        plan = svc.add_members(plan_id, [1003, 1004, 1005])
        assert [(row["position"], row["doll_id"]) for row in plan["members"]] == [
            (1, 1003),
            (2, 1001),
            (3, 1004),
            (4, 1005),
            (5, 1002),
        ]
        with pytest.raises(ValueError, match="남은 제대 자리는 1칸"):
            svc.add_members(plan_id, [1006, 1007])
        assert len(svc.get(plan_id)["members"]) == 5


def test_formation_page_hides_empty_cards_and_uses_multi_picker() -> None:
    page = (ROOT / "src/gfl2tool/qtui/pages/formation.py").read_text(encoding="utf-8")
    card = (ROOT / "src/gfl2tool/qtui/formation_widgets.py").read_text(encoding="utf-8")
    picker = (ROOT / "src/gfl2tool/qtui/dialogs/doll_picker.py").read_text(encoding="utf-8")
    assert 'QPushButton("인형 추가")' in page
    assert 'QPushButton("리몰딩 자동배치")' in page
    assert "multi_select=True" in page
    assert "max_selection=remaining" in page
    assert 'QPushButton("인형 변경")' not in card
    assert "self.setVisible(False)" in card
    assert "max_selection" in picker
    assert "excluded_ids" in picker


def test_tactic_export_rc1_palette_and_layout_contract() -> None:
    source = (ROOT / "src/gfl2tool/qtui/tactic_widgets.py").read_text(encoding="utf-8")
    assert theme.EXPORT_BACKGROUND == "#FFFFFF"
    assert theme.EXPORT_SUMMON == "#000000"
    assert theme.EXPORT_SCALE == 3.0
    assert theme.EXPORT_COVER != theme.EXPORT_GRID
    assert "margin=3" in source
    assert "min(cw, ch) * 0.105" in source
    assert "min(cw, ch) * 0.030" in source
    assert "min(cw, ch) * 0.018" in source
    assert 'return "제대 배치"' in source
    assert 'f"{rows_n}×{cols_n}"' not in source


def test_overlay_rc1_lock_counter_and_restart_contract() -> None:
    overlay = (ROOT / "src/gfl2tool/qtui/tactic_overlay.py").read_text(encoding="utf-8")
    page = (ROOT / "src/gfl2tool/qtui/pages/tactics.py").read_text(encoding="utf-8")
    assert "self.controls_widget.setVisible(bool(visible))" in overlay
    assert "self.appearance_button.setVisible(bool(visible))" in overlay
    assert "root.addLayout(opacity_row)" not in overlay
    assert "def _turn_counter" in overlay
    assert 'return normalized in {"제대배치", "제대편성", "배치"}' in overlay
    assert 'f"{self.tactic.title}  ·  {step.name}  ({turn}/{total})"' in overlay
    assert "for overlay in list(self.overlays):" in page
    assert "overlay.close()" in page
    assert "self.overlays.clear()" in page
    assert "self.overlay_button.setEnabled(False)" not in page


def test_numbered_arrow_rc1_contract_and_marker_roundtrip() -> None:
    page = (ROOT / "src/gfl2tool/qtui/pages/tactics.py").read_text(encoding="utf-8")
    widgets = (ROOT / "src/gfl2tool/qtui/tactic_widgets.py").read_text(encoding="utf-8")
    assert 'QLabel("화살표 순서")' in page
    assert 'self.arrow_order_combo.addItem(f"{order}번째", str(order))' in page
    assert "self.grid.arrow_label = value" in page
    assert "painter.drawPolygon(poly)" in widgets
    assert "def _arrow_marker_label" in widgets
    assert "label=_arrow_marker_label(marker, arrow_ordinal)" in widgets
    assert "label=(self.arrow_label if self.arrow_label" in widgets

    marker = TacticMarker.from_dict(
        {"kind": "arrow", "row": 1, "col": 2, "to_row": 3, "to_col": 4, "label": "5"},
        rows=8,
        cols=8,
    )
    assert marker is not None
    assert marker.label == "5"
