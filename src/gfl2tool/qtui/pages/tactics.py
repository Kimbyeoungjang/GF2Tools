from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QThreadPool, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
)

from ...atomic_io import atomic_write_bytes, atomic_write_json
from ...repository import Repository
from ...services.tactic_equipment import TacticEquipmentCatalog
from ...services.doll_skill_cycles import replace_skill_cycles_in_tactic
from ...tactic_image_import import TacticImageImportResult, import_tactic_image
from ...tactics import (
    MAX_STEPS,
    MAX_TACTICS,
    Tactic,
    TacticMarker,
    TacticStep,
    TacticStore,
    decode_tactic_share,
    encode_tactic_share,
)
from ..data import OwnedDollCatalog
from ..dialogs.tactic_image_import import TacticImageImportReviewDialog
from ..dialogs.tactic_units import TacticUnitsDialog
from ..dialogs.tactic_visuals import TacticVisualSettingsDialog
from ..images import PortraitLoader
from ..app_settings import AppSettings
from ..tactic_overlay import TacticOverlayWindow
from ..tactic_widgets import (
    TacticGridWidget,
    TacticSheetPreviewDialog,
    render_tactic_sheet,
)
from ..widgets import BusyButton, dialog_layout, help_icon, page_layout, section_panel, show_error
from ..workers import run_progress_worker
from .base import DeferredRefreshPage


class _ShareCodeDialog(QDialog):
    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("택틱 공유 코드")
        self.resize(700, 390)
        root = dialog_layout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(code)
        root.addWidget(self.text, 1)
        row = QHBoxLayout()
        copy_button = QPushButton("코드 복사")
        close_button = QPushButton("닫기")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(code))
        close_button.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(copy_button)
        row.addWidget(close_button)
        root.addLayout(row)


class _ImportCodeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("택틱 공유 코드 불러오기")
        self.resize(700, 390)
        root = dialog_layout(self)
        self.text = QTextEdit()
        self.text.setPlaceholderText("GFL2T:1:... 공유 코드를 붙여넣으세요.")
        root.addWidget(self.text, 1)
        row = QHBoxLayout()
        cancel = QPushButton("취소")
        accept = QPushButton("불러오기")
        accept.setObjectName("AccentButton")
        cancel.clicked.connect(self.reject)
        accept.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(accept)
        root.addLayout(row)


class TacticsPage(DeferredRefreshPage):
    def __init__(self, repo: Repository, catalog: OwnedDollCatalog, portraits: PortraitLoader, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog
        self.portraits = portraits
        self.store = TacticStore(repo.path.parent)
        self.equipment_catalog = TacticEquipmentCatalog(repo)
        self.settings = AppSettings()
        self._equipment_data = self.equipment_catalog.load()
        self.pool = QThreadPool.globalInstance()
        self.tactics: list[Tactic] = []
        self.overlay_states: dict[str, dict] = {}
        self.overlays: list[TacticOverlayWindow] = []
        self._loaded = False
        self._updating = False
        self._dirty = False
        self._image_importing = False
        self._image_import_path: str | None = None

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self._flush_save)

        root = page_layout(self, "택틱 · 오버레이")
        root.addLayout(self._build_actions())
        root.addWidget(self._build_editor(), 1)

    def _build_actions(self) -> QVBoxLayout:
        root = QVBoxLayout()
        root.setSpacing(6)

        edit_row = QHBoxLayout()
        edit_label = QLabel("택틱")
        edit_label.setObjectName("Muted")
        edit_row.addWidget(edit_label)
        for text, handler in (
            ("새 택틱", self._new_tactic),
            ("복제", self._duplicate_tactic),
            ("삭제", self._delete_tactic),
        ):
            button = QPushButton(text)
            if handler == self._delete_tactic:
                button.setObjectName("DangerButton")
            button.clicked.connect(handler)
            edit_row.addWidget(button)
        self.image_import_button = BusyButton("이미지 인식")
        self.image_import_button.clicked.connect(self._import_image)
        edit_row.addWidget(self.image_import_button)
        visual_settings = QPushButton("⚙ 표시 설정 새 창에서 열기")
        visual_settings.setToolTip("택틱과 오버레이의 현재 색상을 확인할 수 있는 별도 설정 창을 엽니다.")
        visual_settings.clicked.connect(self._open_visual_settings)
        edit_row.addWidget(visual_settings)
        edit_row.addStretch(1)
        self.overlay_button = QPushButton("오버레이 실행")
        self.overlay_button.setObjectName("AccentButton")
        self.overlay_button.clicked.connect(self._show_overlay)
        edit_row.addWidget(self.overlay_button)
        root.addLayout(edit_row)

        self.image_import_progress = QProgressBar()
        self.image_import_progress.setRange(0, 0)
        self.image_import_progress.setTextVisible(True)
        self.image_import_progress.setFormat("인식 중…")
        self.image_import_progress.setMaximumWidth(220)
        self.image_import_progress.setFixedHeight(22)
        self.image_import_progress.hide()
        edit_row.insertWidget(edit_row.count() - 2, self.image_import_progress)

        share_row = QHBoxLayout()
        share_label = QLabel("공유 · 출력")
        share_label.setObjectName("Muted")
        share_row.addWidget(share_label)
        for text, handler in (
            ("전체 미리보기", self._preview_sheet),
            ("이미지 내보내기", self._export_sheet),
            ("공유 코드", self._share_code),
            ("공유 코드 불러오기", self._import_share_code),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            share_row.addWidget(button)
        share_row.addStretch(1)
        file_label = QLabel("파일")
        file_label.setObjectName("Muted")
        share_row.addWidget(file_label)
        for text, handler in (("JSON 불러오기", self._import_json), ("JSON 내보내기", self._export_json)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            share_row.addWidget(button)
        root.addLayout(share_row)
        return root

    def _build_library_panel(self):
        panel, layout = section_panel("택틱 라이브러리")
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText("택틱 이름 검색")
        self.library_search.setClearButtonEnabled(True)
        self.library_search.textChanged.connect(self._apply_library_filter)
        layout.addWidget(self.library_search)

        self.category_filter = QComboBox()
        self.category_filter.addItem("카테고리 전체", "")
        self.category_filter.currentIndexChanged.connect(self._apply_library_filter)
        layout.addWidget(self.category_filter)

        self.tactic_list = QListWidget()
        self.tactic_list.currentRowChanged.connect(self._tactic_selected)
        layout.addWidget(self.tactic_list, 1)

        roster_title = QLabel("사용 인형")
        roster_title.setObjectName("SectionTitle")
        layout.addWidget(roster_title)
        self.roster_summary = QLabel("등록된 인형 없음")
        self.roster_summary.setObjectName("Muted")
        self.roster_summary.setWordWrap(True)
        layout.addWidget(self.roster_summary)

        self.manage_roster = QPushButton("사용 인형 · 장비 관리")
        self.manage_roster.setObjectName("AccentButton")
        self.manage_roster.clicked.connect(self._manage_roster)
        layout.addWidget(self.manage_roster)

        self.replace_skill_cycle = QPushButton("내 인형 사이클로 교체…")
        self.replace_skill_cycle.setToolTip(
            "사용 인형 관리에서 각 인형에 불러온 일반/제대/직접 사이클을 기준으로 "
            "현재 택틱의 T1~Tn 스킬 사이클 문구만 교체합니다. OCR/불러오기 직후에도 자동 실행하지 않습니다."
        )
        self.replace_skill_cycle.clicked.connect(self._replace_skill_cycle_from_roster)
        layout.addWidget(self.replace_skill_cycle)
        return panel

    def _build_editor_form(self, layout) -> None:
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.category_edit = QComboBox()
        self.category_edit.setEditable(True)
        self.category_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.category_edit.setToolTip(
            "보스나 콘텐츠 이름을 입력하면 택틱 카테고리로 사용됩니다."
        )
        self.rows_spin = QSpinBox()
        self.cols_spin = QSpinBox()
        self.rows_spin.setRange(4, 24)
        self.cols_spin.setRange(4, 24)
        self.title_edit.textEdited.connect(self._title_changed)
        self.category_edit.editTextChanged.connect(self._category_changed)
        self.rows_spin.valueChanged.connect(self._grid_size_changed)
        self.cols_spin.valueChanged.connect(self._grid_size_changed)
        form.addRow("택틱 이름", self.title_edit)
        form.addRow("카테고리", self.category_edit)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("행"))
        size_row.addWidget(self.rows_spin)
        size_row.addSpacing(8)
        size_row.addWidget(QLabel("열"))
        size_row.addWidget(self.cols_spin)
        form.addRow("현재 단계 격자", size_row)
        layout.addLayout(form)

        self.previous_ghost = QCheckBox("이전 단계 인형 위치를 잔상으로 표시")
        self.previous_ghost.toggled.connect(self._previous_ghost_changed)
        layout.addWidget(self.previous_ghost)

    def _build_tool_row(self, layout) -> None:
        tool_row = QHBoxLayout()
        self.tool_combo = QComboBox()
        for label, value in (
            ("선택 · 이동", "move"),
            ("인형", "unit"),
            ("소환수 · 설치물", "summon"),
            ("기타 텍스트", "custom"),
            ("보스", "boss"),
            ("이동 불가", "blocked"),
            ("엄폐물", "cover"),
            ("이동 화살표", "arrow"),
            ("지우기", "clear"),
        ):
            self.tool_combo.addItem(label, value)
        self.tool_combo.currentIndexChanged.connect(self._tool_changed)

        self.unit_label_title = QLabel("배치 인형")
        self.unit_combo = QComboBox()
        self.unit_combo.setMinimumWidth(150)
        self.unit_combo.currentIndexChanged.connect(self._unit_combo_changed)

        self.summon_label_title = QLabel("소환수 표기")
        self.summon_label = QLineEdit("*")
        self.summon_label.setMaximumWidth(110)
        self.summon_label.setMaxLength(12)
        self.summon_label.setPlaceholderText("* / 드론 / 지뢰")
        self.summon_label.textEdited.connect(self._summon_label_changed)

        self.custom_label_title = QLabel("기타 문구")
        self.custom_label = QLineEdit()
        self.custom_label.setMaximumWidth(150)
        self.custom_label.setMaxLength(24)
        self.custom_label.setPlaceholderText("임의 문구")
        self.custom_label.textEdited.connect(self._custom_label_changed)

        self.boss_size_title = QLabel("보스 W/H")
        self.boss_w = QSpinBox()
        self.boss_h = QSpinBox()
        self.boss_w.setRange(1, 8)
        self.boss_h.setRange(1, 8)
        self.boss_w.setValue(3)
        self.boss_h.setValue(3)
        self.boss_w.valueChanged.connect(self._boss_size_changed)
        self.boss_h.valueChanged.connect(self._boss_size_changed)

        for widget in (
            QLabel("도구"),
            self.tool_combo,
            self.unit_label_title,
            self.unit_combo,
            self.summon_label_title,
            self.summon_label,
            self.custom_label_title,
            self.custom_label,
            self.boss_size_title,
            self.boss_w,
            self.boss_h,
        ):
            tool_row.addWidget(widget)
        tool_row.addStretch(1)
        layout.addLayout(tool_row)

        hint_row = QHBoxLayout()
        self.tool_help = help_icon("")
        self.marker_summary = QLabel()
        self.marker_summary.setObjectName("Muted")
        self.grid_position = QLabel()
        self.grid_position.setObjectName("Muted")
        hint_row.addWidget(self.tool_help)
        hint_row.addStretch(1)
        hint_row.addWidget(self.marker_summary)
        hint_row.addSpacing(8)
        hint_row.addWidget(self.grid_position)
        layout.addLayout(hint_row)

    def _build_grid_panel(self):
        panel, layout = section_panel(
            "격자 편집",
            "단계마다 격자 크기를 지정할 수 있습니다. 기존 오브젝트는 '선택 · 이동'에서 마우스로 잡아 원하는 칸까지 드래그할 수 있고, 우클릭 드래그로 연속 삭제할 수 있습니다.",
        )
        self._build_editor_form(layout)
        self._build_tool_row(layout)

        self.grid = TacticGridWidget(Tactic())
        self.grid.modified.connect(self._content_modified)
        self.grid.hoverChanged.connect(self.grid_position.setText)
        layout.addWidget(self.grid, 1)
        self._tool_changed()
        return panel

    def _build_step_panel(self):
        panel, layout = section_panel(
            "단계",
            "T1 → T2 순서로 오버레이에서 넘겨봅니다. Windows 전역 단축키는 좌측 하단 설정에서 "
            "변경할 수 있으며, 오버레이 창이 선택된 상태에서는 ← / → / Space도 사용할 수 있습니다.",
        )
        self.step_list = QListWidget()
        self.step_list.currentRowChanged.connect(self._step_selected)
        layout.addWidget(self.step_list, 1)

        step_buttons = QHBoxLayout()
        for text, handler in (
            ("+", self._add_step),
            ("복제", self._duplicate_step),
            ("↑", self._move_step_up),
            ("↓", self._move_step_down),
            ("삭제", self._delete_step),
        ):
            button = QPushButton(text)
            if handler == self._delete_step:
                button.setObjectName("DangerButton")
            button.clicked.connect(handler)
            step_buttons.addWidget(button)
        layout.addLayout(step_buttons)

        layout.addWidget(QLabel("단계 이름"))
        self.step_name = QLineEdit()
        self.step_name.textEdited.connect(self._step_name_changed)
        layout.addWidget(self.step_name)

        layout.addWidget(QLabel("스킬 사이클"))
        self.cycle = QTextEdit()
        self.cycle.setMaximumHeight(96)
        self.cycle.setPlaceholderText("예: 로 1(마)2 · 드 2 · 마 31 · 엘 23 · 흑 21")
        self.cycle.textChanged.connect(self._cycle_changed)
        layout.addWidget(self.cycle)

        layout.addWidget(QLabel("행동 · 주의사항"))
        self.note = QTextEdit()
        self.note.textChanged.connect(self._note_changed)
        layout.addWidget(self.note, 1)

        return panel

    def _build_editor(self) -> QSplitter:
        splitter = QSplitter()
        library_panel = self._build_library_panel()
        editor_panel = self._build_grid_panel()
        step_panel = self._build_step_panel()
        for panel in (library_panel, editor_panel, step_panel):
            splitter.addWidget(panel)

        library_panel.setMinimumWidth(230)
        editor_panel.setMinimumWidth(440)
        step_panel.setMinimumWidth(220)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([250, 620, 260])
        return splitter

    def refresh(self) -> None:
        # Equipment/key suggestions come from the local reference catalog and
        # imported user-data file. Reload them whenever this page refreshes.
        self._equipment_data = self.equipment_catalog.load()
        if self._loaded:
            return
        self.tactics = self.store.load()
        self.overlay_states = self.store.load_overlay_states()
        self._loaded = True
        self._rebuild_tactic_list(select=0)

    def _current_tactic(self) -> Tactic | None:
        row = self.tactic_list.currentRow()
        return self.tactics[row] if 0 <= row < len(self.tactics) else None

    def _current_step(self) -> TacticStep | None:
        tactic = self._current_tactic()
        if tactic is None:
            return None
        row = self.step_list.currentRow()
        return tactic.steps[row] if 0 <= row < len(tactic.steps) else None

    def _queue_save(self) -> None:
        if not self._loaded:
            return
        self._dirty = True
        self.save_timer.start()

    def _flush_save(self) -> None:
        if not self._dirty:
            return
        try:
            self.store.save(self.tactics)
        except Exception as exc:
            self.refreshFailed.emit(f"택틱 라이브러리를 저장하지 못했습니다: {exc}")
            return
        self._dirty = False

    def _tactic_item_text(self, tactic: Tactic) -> str:
        category = tactic.category.strip()
        return f"[{category}] {tactic.title}" if category else tactic.title

    def _category_values(self) -> list[str]:
        return sorted(
            {tactic.category.strip() for tactic in self.tactics if tactic.category.strip()},
            key=str.casefold,
        )

    def _refresh_category_controls(self) -> None:
        values = self._category_values()
        selected_filter = str(self.category_filter.currentData() or "")
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem(f"카테고리 전체 ({len(self.tactics)})", "")
        for value in values:
            count = sum(1 for tactic in self.tactics if tactic.category.strip() == value)
            self.category_filter.addItem(f"{value} ({count})", value)
        index = self.category_filter.findData(selected_filter)
        self.category_filter.setCurrentIndex(index if index >= 0 else 0)
        self.category_filter.blockSignals(False)

        current_text = self.category_edit.currentText()
        self.category_edit.blockSignals(True)
        self.category_edit.clear()
        self.category_edit.addItem("")
        for value in values:
            self.category_edit.addItem(value)
        self.category_edit.setEditText(current_text)
        self.category_edit.blockSignals(False)

    def _apply_library_filter(self, *_args) -> None:
        if not hasattr(self, "tactic_list"):
            return
        query = self.library_search.text().strip().casefold()
        category = str(self.category_filter.currentData() or "").strip()
        first_visible = -1
        for row, tactic in enumerate(self.tactics):
            text = f"{tactic.title} {tactic.category}".casefold()
            visible = (not query or query in text) and (not category or tactic.category.strip() == category)
            self.tactic_list.item(row).setHidden(not visible)
            if visible and first_visible < 0:
                first_visible = row
        current = self.tactic_list.currentRow()
        if first_visible >= 0 and (current < 0 or self.tactic_list.item(current).isHidden()):
            self.tactic_list.setCurrentRow(first_visible)

    def _rebuild_tactic_list(self, *, select: int) -> None:
        self._updating = True
        self.tactic_list.clear()
        for tactic in self.tactics:
            item = QListWidgetItem(self._tactic_item_text(tactic))
            item.setData(Qt.ItemDataRole.UserRole, tactic.tactic_id)
            self.tactic_list.addItem(item)
        self._refresh_category_controls()
        self._updating = False
        self._apply_library_filter()
        if self.tactics:
            target = max(0, min(select, len(self.tactics) - 1))
            if not self.tactic_list.item(target).isHidden():
                self.tactic_list.setCurrentRow(target)
        else:
            self._show_empty_tactic_state()

    def _show_empty_tactic_state(self) -> None:
        self._updating = True
        try:
            self.title_edit.clear()
            self.category_edit.setEditText("")
            self.previous_ghost.setChecked(False)
            self.roster_summary.setText("택틱이 없습니다 · '새 택틱' 또는 '이미지 인식'으로 시작하세요.")
            self.step_list.clear()
            self.step_name.clear()
            self.cycle.clear()
            self.note.clear()
            self.marker_summary.clear()
            self.grid.set_tactic(Tactic(title="", steps=[TacticStep()]))
        finally:
            self._updating = False

    def _tactic_selected(self, row: int) -> None:
        if self._updating or not (0 <= row < len(self.tactics)):
            return
        tactic = self.tactics[row]
        self._updating = True
        self.title_edit.setText(tactic.title)
        self.category_edit.setEditText(tactic.category)
        self.previous_ghost.setChecked(tactic.show_previous)
        self.grid.set_tactic(tactic)
        self._refresh_roster_summary()
        self.step_list.clear()
        for index, step in enumerate(tactic.steps):
            rows, cols = tactic.grid_size(index)
            self.step_list.addItem(f"{step.name} · {rows}×{cols}")
        self._updating = False
        self.step_list.setCurrentRow(0)
        self._step_selected(0)

    def _step_selected(self, row: int) -> None:
        tactic = self._current_tactic()
        if tactic is None or not (0 <= row < len(tactic.steps)):
            return
        step = tactic.steps[row]
        self._updating = True
        self.step_name.setText(step.name)
        self.cycle.setPlainText(step.cycle)
        self.note.setPlainText(step.note)
        rows, cols = tactic.grid_size(row)
        self.rows_spin.setValue(rows)
        self.cols_spin.setValue(cols)
        self._updating = False
        self.grid.set_step_index(row)
        self._update_marker_summary()

    def _title_changed(self, text: str) -> None:
        if self._updating:
            return
        tactic = self._current_tactic()
        if tactic is None:
            return
        tactic.title = (text or "새 택틱")[:100]
        item = self.tactic_list.currentItem()
        if item is not None:
            item.setText(self._tactic_item_text(tactic))
        self._apply_library_filter()
        self._queue_save()

    def _category_changed(self, text: str) -> None:
        if self._updating:
            return
        tactic = self._current_tactic()
        if tactic is None:
            return
        tactic.category = str(text or "").strip()[:80]
        item = self.tactic_list.currentItem()
        if item is not None:
            item.setText(self._tactic_item_text(tactic))
        self._refresh_category_controls()
        self._apply_library_filter()
        self._queue_save()

    def _grid_size_changed(self, *_args) -> None:
        if self._updating:
            return
        tactic = self._current_tactic()
        step = self._current_step()
        if tactic is None or step is None:
            return
        rows = self.rows_spin.value()
        cols = self.cols_spin.value()
        step.rows = rows
        step.cols = cols
        kept: list[TacticMarker] = []
        for marker in step.markers:
            if marker.row >= rows or marker.col >= cols:
                continue
            marker.width = min(marker.width, cols - marker.col)
            marker.height = min(marker.height, rows - marker.row)
            if marker.to_row is not None:
                marker.to_row = min(marker.to_row, rows - 1)
            if marker.to_col is not None:
                marker.to_col = min(marker.to_col, cols - 1)
            kept.append(marker)
        step.markers = kept
        self._update_current_step_item()
        self.grid.update()
        self._queue_save()

    def _previous_ghost_changed(self, checked: bool) -> None:
        if self._updating:
            return
        tactic = self._current_tactic()
        if tactic is None:
            return
        tactic.show_previous = bool(checked)
        self.grid.update()
        self._queue_save()

    def _tool_changed(self, *_args) -> None:
        tool = str(self.tool_combo.currentData() or "unit")
        self.grid.set_tool(tool)
        is_unit = tool == "unit"
        is_summon = tool == "summon"
        is_custom = tool == "custom"
        is_boss = tool == "boss"
        is_cover = tool == "cover"
        self.unit_label_title.setVisible(is_unit)
        self.unit_combo.setVisible(is_unit)
        self.summon_label_title.setVisible(is_summon)
        self.summon_label.setVisible(is_summon)
        self.custom_label_title.setVisible(is_custom)
        self.custom_label.setVisible(is_custom)
        self.boss_size_title.setVisible(is_boss)
        self.boss_w.setVisible(is_boss)
        self.boss_h.setVisible(is_boss)
        help_text = {
            "move": "배치된 오브젝트를 마우스로 잡아 원하는 칸까지 드래그합니다. 클릭 후 다른 칸을 누르는 방식도 보조 조작으로 지원합니다.",
            "unit": "사용 인형에서 선택한 인형을 칸에 배치합니다. 보유하지 않은 인형도 사용 인형 목록에 등록할 수 있습니다.",
            "summon": "드론·지뢰·소환수·설치물을 배치합니다. 공략 이미지의 * 표기도 이 종류로 가져옵니다.",
            "custom": "사용자가 지정한 짧은 문구를 한 칸에 배치합니다. 기믹, 방향, 순서 같은 자유 표기에 사용할 수 있습니다.",
            "boss": "시작 칸을 클릭해 보스 점유 영역을 배치합니다.",
            "blocked": "클릭 또는 드래그로 이동 불가 칸을 칠하거나 지웁니다. 첫 칸 상태에 맞춰 같은 동작이 이어집니다.",
            "cover": "마우스 포인터와 가장 가까운 격자 변에 엄폐선을 그립니다. 클릭 또는 드래그로 칠하고, 이미 칠해진 변에서 시작하면 같은 방식으로 지웁니다.",
            "arrow": "출발 칸과 도착 칸을 차례로 클릭합니다.",
            "clear": "클릭 또는 드래그로 칸의 요소를 지웁니다. 어떤 도구에서도 우클릭 드래그로 빠르게 지울 수 있습니다.",
        }.get(tool, "")
        self.tool_help.setToolTip(help_text)

    def _unit_combo_changed(self, *_args) -> None:
        unit_key = str(self.unit_combo.currentData() or "")
        tactic = self._current_tactic()
        unit = tactic.unit_by_key(unit_key) if tactic is not None and unit_key else None
        self.grid.unit_key = unit_key
        self.grid.unit_label = unit.display_label() if unit is not None else "?"

    def _summon_label_changed(self, text: str) -> None:
        self.grid.summon_label = (str(text or "").strip() or "*")[:12]

    def _custom_label_changed(self, text: str) -> None:
        self.grid.custom_label = (str(text or "").strip() or "기타")[:24]

    def _refresh_roster_summary(self) -> None:
        tactic = self._current_tactic()
        if tactic is not None and tactic.units:
            labels = [f"{unit.name}({unit.display_label()})" for unit in tactic.units]
            self.roster_summary.setText(
                f"{len(labels)}명 · " + " · ".join(labels[:6])
                + (f" · +{len(labels) - 6}" if len(labels) > 6 else "")
            )
        else:
            self.roster_summary.setText("등록된 인형 없음")
        self._rebuild_unit_combo()

    def _rebuild_unit_combo(self) -> None:
        tactic = self._current_tactic()
        previous = str(self.unit_combo.currentData() or "") if hasattr(self, "unit_combo") else ""
        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        self.unit_combo.addItem("미지정 (?)", "")
        if tactic is not None:
            for unit in tactic.units:
                self.unit_combo.addItem(f"{unit.name} · {unit.display_label()}", unit.unit_key)
        index = self.unit_combo.findData(previous)
        self.unit_combo.setCurrentIndex(index if index >= 0 else (1 if self.unit_combo.count() > 1 else 0))
        self.unit_combo.blockSignals(False)
        self._unit_combo_changed()

    def _manage_roster(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            QMessageBox.information(self, "사용 인형", "먼저 택틱을 만들거나 선택해 주세요.")
            return
        # Reload so a login sync/static-data refresh performed while this page
        # was open is immediately available in the roster dialog.
        self._equipment_data = self.equipment_catalog.load()
        dialog = TacticUnitsDialog(
            self.repo, self.catalog, self.portraits, tactic.units, self._equipment_data, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        removed_keys = {unit.unit_key for unit in tactic.units} - {unit.unit_key for unit in dialog.result_units}
        if removed_keys:
            for step in tactic.steps:
                for marker in step.markers:
                    if marker.kind == "unit" and marker.unit_key in removed_keys:
                        old = tactic.unit_by_key(marker.unit_key)
                        marker.label = old.display_label() if old is not None else (marker.label or "?")
                        marker.unit_key = ""
        tactic.units = list(dialog.result_units)
        self._refresh_roster_summary()
        self.grid.update()
        self._queue_save()

    def _replace_skill_cycle_from_roster(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            QMessageBox.information(self, "스킬 사이클 교체", "먼저 택틱을 만들거나 선택해 주세요.")
            return
        if not tactic.units:
            QMessageBox.information(
                self,
                "스킬 사이클 교체",
                "먼저 '사용 인형 · 장비 관리'에서 이 택틱을 내 인형과 연결해 주세요.",
            )
            return
        linked = [unit for unit in tactic.units if any(str(value or "").strip() for value in unit.skill_cycle)]
        if not linked:
            QMessageBox.information(
                self,
                "스킬 사이클 교체",
                "현재 택틱 사용 인형에 불러온 사이클이 없습니다.\n"
                "인형별로 '제대 사이클 불러오기', '일반 사이클 불러오기' 또는 '직접 편집'을 먼저 사용해 주세요.",
            )
            return
        answer = QMessageBox.question(
            self,
            "내 인형 사이클로 교체",
            f"{len(linked)}명의 현재 택틱용 사이클로 T1~Tn 문구를 교체할까요?\n\n"
            "OCR/JSON에서 불러온 기존 스킬 사이클 문구는 대체되지만 격자, 배치, 엄폐물, 메모는 그대로 유지됩니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not replace_skill_cycles_in_tactic(tactic):
            QMessageBox.information(self, "스킬 사이클 교체", "교체할 스킬 사이클 변경사항이 없습니다.")
            return
        self._refresh_steps(select=max(0, self.step_list.currentRow()))
        self._queue_save()
        QMessageBox.information(
            self,
            "스킬 사이클 교체",
            f"현재 택틱의 스킬 사이클을 내 인형 {len(linked)}명의 설정으로 교체했습니다.",
        )

    def _open_visual_settings(self) -> None:
        TacticVisualSettingsDialog(self.settings, on_changed=self._visuals_changed, parent=self).exec()

    def _visuals_changed(self) -> None:
        self.grid.refresh_theme()
        for overlay in list(self.overlays):
            overlay.grid.refresh_theme()
        self.update()

    def _boss_size_changed(self, *_args) -> None:
        self.grid.boss_size = (self.boss_w.value(), self.boss_h.value())

    def _content_modified(self) -> None:
        self._update_marker_summary()
        self._queue_save()

    def _update_marker_summary(self) -> None:
        step = self._current_step()
        if step is None:
            self.marker_summary.clear()
            return
        counts: dict[str, int] = {}
        for marker in step.markers:
            counts[marker.kind] = counts.get(marker.kind, 0) + 1
        self.marker_summary.setText(
            " · ".join(
                (
                    f"인형 {counts.get('unit', 0)}",
                    f"소환 {counts.get('summon', 0)}",
                    f"기타 {counts.get('custom', 0)}",
                    f"보스 {counts.get('boss', 0)}",
                    f"불가 {counts.get('blocked', 0)}",
                    f"엄폐 {counts.get('cover', 0)}",
                    f"화살표 {counts.get('arrow', 0)}",
                )
            )
        )

    def _step_name_changed(self, text: str) -> None:
        if self._updating:
            return
        step = self._current_step()
        if step is None:
            return
        step.name = (text or "단계")[:32]
        self._update_current_step_item()
        self._queue_save()

    def _update_current_step_item(self) -> None:
        tactic = self._current_tactic()
        row = self.step_list.currentRow()
        item = self.step_list.currentItem()
        if tactic is None or item is None or not (0 <= row < len(tactic.steps)):
            return
        rows, cols = tactic.grid_size(row)
        item.setText(f"{tactic.steps[row].name} · {rows}×{cols}")

    def _cycle_changed(self) -> None:
        if self._updating:
            return
        step = self._current_step()
        if step is None:
            return
        step.cycle = self.cycle.toPlainText()[:2000]
        step.cycle_auto = False
        self._queue_save()

    def _note_changed(self) -> None:
        if self._updating:
            return
        step = self._current_step()
        if step is None:
            return
        step.note = self.note.toPlainText()[:4000]
        self._queue_save()

    def _new_tactic(self) -> None:
        if len(self.tactics) >= MAX_TACTICS:
            QMessageBox.information(self, "택틱", f"택틱은 최대 {MAX_TACTICS}개까지 만들 수 있습니다.")
            return
        tactic = Tactic(
            title=f"새 택틱 {len(self.tactics) + 1}",
            category=str(self.category_filter.currentData() or "")[:80],
        )
        self.tactics.append(tactic)
        self._rebuild_tactic_list(select=len(self.tactics) - 1)
        self._queue_save()

    def _duplicate_tactic(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        if len(self.tactics) >= MAX_TACTICS:
            QMessageBox.information(self, "택틱", f"택틱은 최대 {MAX_TACTICS}개까지 만들 수 있습니다.")
            return
        clone = tactic.clone()
        row = self.tactic_list.currentRow() + 1
        self.tactics.insert(row, clone)
        self._rebuild_tactic_list(select=row)
        self._queue_save()

    def _delete_tactic(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        answer = QMessageBox.question(self, "택틱 삭제", f"'{tactic.title}' 택틱을 삭제할까요?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        row = self.tactic_list.currentRow()
        self.tactics.pop(row)
        self.overlay_states.pop(tactic.tactic_id, None)
        try:
            self.store.save_overlay_states(self.overlay_states)
        except Exception as exc:
            self.refreshFailed.emit(f"오버레이 위치 설정을 저장하지 못했습니다: {exc}")
        self._rebuild_tactic_list(select=min(row, len(self.tactics) - 1) if self.tactics else -1)
        self._queue_save()

    def _add_step(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        if len(tactic.steps) >= MAX_STEPS:
            QMessageBox.information(self, "택틱", f"단계는 최대 {MAX_STEPS}개까지 만들 수 있습니다.")
            return
        current = self._current_step()
        rows = int(current.rows or tactic.rows) if current is not None else tactic.rows
        cols = int(current.cols or tactic.cols) if current is not None else tactic.cols
        tactic.steps.append(TacticStep(name=f"T{len(tactic.steps) + 1}", rows=rows, cols=cols))
        self._refresh_steps(select=len(tactic.steps) - 1)
        self._queue_save()

    def _duplicate_step(self) -> None:
        tactic = self._current_tactic()
        step = self._current_step()
        if tactic is None or step is None:
            return
        if len(tactic.steps) >= MAX_STEPS:
            QMessageBox.information(self, "택틱", f"단계는 최대 {MAX_STEPS}개까지 만들 수 있습니다.")
            return
        rows, cols = tactic.grid_size(self.step_list.currentRow())
        copied = TacticStep.from_dict(
            {
                "name": f"{step.name} 복사",
                "note": step.note,
                "cycle": step.cycle,
                "markers": [marker.__dict__ for marker in step.markers],
                "rows": rows,
                "cols": cols,
            },
            rows=rows,
            cols=cols,
            index=0,
        )
        row = self.step_list.currentRow() + 1
        tactic.steps.insert(row, copied)
        self._refresh_steps(select=row)
        self._queue_save()

    def _move_step_up(self) -> None:
        tactic = self._current_tactic()
        row = self.step_list.currentRow()
        if tactic is None or row <= 0:
            return
        tactic.steps[row - 1], tactic.steps[row] = tactic.steps[row], tactic.steps[row - 1]
        self._refresh_steps(select=row - 1)
        self._queue_save()

    def _move_step_down(self) -> None:
        tactic = self._current_tactic()
        row = self.step_list.currentRow()
        if tactic is None or row < 0 or row >= len(tactic.steps) - 1:
            return
        tactic.steps[row + 1], tactic.steps[row] = tactic.steps[row], tactic.steps[row + 1]
        self._refresh_steps(select=row + 1)
        self._queue_save()

    def _delete_step(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        if len(tactic.steps) <= 1:
            QMessageBox.information(self, "택틱", "단계는 최소 1개가 필요합니다.")
            return
        row = self.step_list.currentRow()
        tactic.steps.pop(max(0, row))
        self._refresh_steps(select=min(max(0, row), len(tactic.steps) - 1))
        self._queue_save()

    def _refresh_steps(self, *, select: int) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        self._updating = True
        self.step_list.clear()
        for index, step in enumerate(tactic.steps):
            rows, cols = tactic.grid_size(index)
            self.step_list.addItem(f"{step.name} · {rows}×{cols}")
        self._updating = False
        self.step_list.setCurrentRow(max(0, min(select, len(tactic.steps) - 1)))
        self._step_selected(self.step_list.currentRow())
        self.grid.update()

    def _import_image(self) -> None:
        if self._image_importing:
            return
        if len(self.tactics) >= MAX_TACTICS:
            QMessageBox.information(self, "택틱", f"택틱은 최대 {MAX_TACTICS}개까지 저장할 수 있습니다.")
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "택틱 이미지 인식",
            "",
            "이미지 (*.png *.jpg *.jpeg *.webp *.bmp);;모든 파일 (*)",
        )
        if not path:
            return
        self._image_importing = True
        self._image_import_path = path
        self.image_import_button.set_busy(True, "이미지 인식")
        self.image_import_progress.setFormat("인식 중…")
        self.image_import_progress.show()
        run_progress_worker(
            self.pool,
            lambda progress: import_tactic_image(path, progress=progress),
            on_progress=self._image_import_progress,
            on_result=self._image_import_ready,
            on_error=self._image_import_failed,
            on_finished=self._image_import_finished,
        )

    def _image_import_progress(self, text: str) -> None:
        message = str(text or "").strip() or "인식 중…"
        self.image_import_progress.setFormat(message)

    def _image_import_ready(self, result: object) -> None:
        self.image_import_progress.setFormat("인식 완료")
        if not isinstance(result, TacticImageImportResult):
            self._image_import_failed("이미지 인식 결과 형식이 올바르지 않습니다.")
            return
        if not self._image_import_path:
            self._image_import_failed("이미지 인식 원본 경로를 확인하지 못했습니다.")
            return
        dialog = TacticImageImportReviewDialog(self._image_import_path, result, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tactic = dialog.selected_tactic()
        except ValueError as exc:
            show_error(self, "택틱 이미지 인식 결과", exc)
            return
        if len(self.tactics) >= MAX_TACTICS:
            QMessageBox.information(self, "택틱", f"택틱은 최대 {MAX_TACTICS}개까지 저장할 수 있습니다.")
            return
        self.tactics.append(tactic)
        self._rebuild_tactic_list(select=len(self.tactics) - 1)
        self._queue_save()

    def _image_import_failed(self, error: str) -> None:
        self.image_import_progress.setFormat("인식 실패")
        show_error(self, "택틱 이미지 인식 실패", error)

    def _image_import_finished(self) -> None:
        self._image_importing = False
        self._image_import_path = None
        self.image_import_button.set_busy(False)
        QTimer.singleShot(1600, self.image_import_progress.hide)

    def _import_json(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "택틱 JSON 불러오기", "", "GFL2 Tactic (*.json);;JSON (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON 최상위 형식은 객체여야 합니다.")
            tactic = Tactic.from_dict(payload, preserve_id=False)
        except Exception as exc:
            show_error(self, "택틱 불러오기 실패", exc)
            return
        if len(self.tactics) >= MAX_TACTICS:
            QMessageBox.information(self, "택틱", f"택틱은 최대 {MAX_TACTICS}개까지 저장할 수 있습니다.")
            return
        self.tactics.append(tactic)
        self._rebuild_tactic_list(select=len(self.tactics) - 1)
        self._queue_save()

    def _export_json(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "택틱 JSON 내보내기",
            f"{tactic.title}.json",
            "GFL2 Tactic (*.json)",
        )
        if not path:
            return
        try:
            atomic_write_json(path, tactic.to_dict(include_id=False), ensure_ascii=False, indent=2)
        except Exception as exc:
            show_error(self, "택틱 내보내기 실패", exc)

    def _share_code(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        try:
            code = encode_tactic_share(tactic)
        except ValueError as exc:
            show_error(self, "택틱 공유 코드", exc)
            return
        _ShareCodeDialog(code, self).exec()

    def _import_share_code(self) -> None:
        dialog = _ImportCodeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tactic = decode_tactic_share(dialog.text.toPlainText())
        except Exception as exc:
            show_error(self, "공유 코드 오류", exc)
            return
        if len(self.tactics) >= MAX_TACTICS:
            QMessageBox.information(self, "택틱", f"택틱은 최대 {MAX_TACTICS}개까지 저장할 수 있습니다.")
            return
        self.tactics.append(tactic)
        self._rebuild_tactic_list(select=len(self.tactics) - 1)
        self._queue_save()

    def _preview_sheet(self) -> None:
        tactic = self._current_tactic()
        if tactic is not None:
            TacticSheetPreviewDialog(tactic, self).exec()

    def _export_sheet(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return
        path, _filter = QFileDialog.getSaveFileName(self, "택틱 이미지 내보내기", f"{tactic.title}.png", "PNG 이미지 (*.png)")
        if not path:
            return
        try:
            image = render_tactic_sheet(tactic)
            buffer = QBuffer()
            if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
                raise RuntimeError("PNG 이미지를 생성하지 못했습니다.")
            atomic_write_bytes(path, bytes(buffer.data()))
        except Exception as exc:
            show_error(self, "택틱 이미지 내보내기 실패", exc)

    def _show_overlay(self) -> None:
        tactic = self._current_tactic()
        if tactic is None:
            return

        # One overlay per application page. Older builds could create several
        # windows through repeated clicks, so normalize any survivors before
        # creating a new one and bring the existing window forward instead.
        visible: list[TacticOverlayWindow] = []
        for overlay in list(self.overlays):
            try:
                if overlay.isVisible():
                    visible.append(overlay)
            except RuntimeError:
                self._forget_overlay(overlay)
        if visible:
            keeper = visible[0]
            for extra in visible[1:]:
                extra.close()
            self.overlay_button.setEnabled(False)
            keeper.raise_()
            keeper.activateWindow()
            return

        self.overlay_button.setEnabled(False)
        self._flush_save()
        overlay = TacticOverlayWindow(
            tactic,
            start_index=max(0, self.step_list.currentRow()),
            saved_state=self.overlay_states.get(tactic.tactic_id),
        )
        overlay.stateSaved.connect(self._overlay_state_saved)
        overlay.destroyed.connect(lambda _obj=None, target=overlay: self._forget_overlay(target))
        self.overlays.append(overlay)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()

    def _overlay_state_saved(self, tactic_id: str, state: object) -> None:
        if isinstance(state, dict):
            self.overlay_states[str(tactic_id)] = dict(state)
            try:
                self.store.save_overlay_states(self.overlay_states)
            except Exception as exc:
                self.refreshFailed.emit(f"오버레이 위치 설정을 저장하지 못했습니다: {exc}")

    def _forget_overlay(self, overlay: TacticOverlayWindow) -> None:
        try:
            self.overlays.remove(overlay)
        except ValueError:
            pass
        if hasattr(self, "overlay_button"):
            self.overlay_button.setEnabled(not any(self._overlay_is_visible(item) for item in self.overlays))

    @staticmethod
    def _overlay_is_visible(overlay: TacticOverlayWindow) -> bool:
        try:
            return bool(overlay.isVisible())
        except RuntimeError:
            return False

    def apply_runtime_settings(self) -> None:
        self.grid.refresh_theme()
        for overlay in list(self.overlays):
            overlay.apply_runtime_settings()

    def prepare_close(self) -> None:
        self.save_timer.stop()
        if self._dirty:
            self._flush_save()
        for overlay in list(self.overlays):
            overlay.close()
        self.overlays.clear()
        super().prepare_close()
