from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...tactic_image_import import (
    TacticImageImportResult,
    formation_board_indexes,
    reimport_tactic_region,
    suggested_board_indexes,
)
from ...tactics import Tactic
from .. import theme
from ..tactic_widgets import TacticGridWidget
from ..widgets import dialog_layout, help_icon, section_panel, show_error


class _AspectImageLabel(QLabel):
    """Image preview that always fits the available area without cropping."""

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(placeholder, parent)
        self._image = QImage()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: QImage) -> None:
        self._image = image
        if image.isNull():
            super().clear()
            return
        self._refresh_pixmap()

    def clear(self) -> None:
        self._image = QImage()
        super().clear()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._image.isNull():
            return
        size = self.contentsRect().size()
        if size.width() <= 1 or size.height() <= 1:
            return
        self.setPixmap(
            QPixmap.fromImage(self._image).scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class _ImageRegionWidget(QWidget):
    """Display the source image and let the user drag one OCR retry rectangle."""

    def __init__(self, image: QImage, initial_box: tuple[int, int, int, int] | None = None, parent=None):
        super().__init__(parent)
        self._image = image
        self._pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self._selection = QRectF()
        self._drag_start: QPointF | None = None
        self.setMinimumSize(760, 540)
        self.setMouseTracking(True)
        if initial_box is not None:
            self.set_selection(initial_box)

    def _target_rect(self) -> QRectF:
        if self._image.isNull() or self._image.width() <= 0 or self._image.height() <= 0:
            return QRectF()
        bounds = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        scale = min(bounds.width() / self._image.width(), bounds.height() / self._image.height())
        width = self._image.width() * scale
        height = self._image.height() * scale
        left = bounds.left() + (bounds.width() - width) / 2.0
        top = bounds.top() + (bounds.height() - height) / 2.0
        return QRectF(left, top, width, height)

    def _widget_to_image(self, point: QPointF) -> QPointF:
        target = self._target_rect()
        if target.isEmpty() or self._image.isNull():
            return QPointF()
        x = (point.x() - target.left()) * self._image.width() / target.width()
        y = (point.y() - target.top()) * self._image.height() / target.height()
        return QPointF(
            max(0.0, min(float(self._image.width()), x)),
            max(0.0, min(float(self._image.height()), y)),
        )

    def _image_to_widget_rect(self, rect: QRectF) -> QRectF:
        target = self._target_rect()
        if target.isEmpty() or self._image.isNull():
            return QRectF()
        return QRectF(
            target.left() + rect.left() * target.width() / self._image.width(),
            target.top() + rect.top() * target.height() / self._image.height(),
            rect.width() * target.width() / self._image.width(),
            rect.height() * target.height() / self._image.height(),
        )

    def set_selection(self, box: tuple[int, int, int, int]) -> None:
        x, y, width, height = (int(value) for value in box)
        if self._image.isNull():
            self._selection = QRectF()
            return
        left = max(0, min(self._image.width() - 1, x))
        top = max(0, min(self._image.height() - 1, y))
        right = max(left + 1, min(self._image.width(), x + max(1, width)))
        bottom = max(top + 1, min(self._image.height(), y + max(1, height)))
        self._selection = QRectF(left, top, right - left, bottom - top)
        self.update()

    def selected_box(self) -> tuple[int, int, int, int] | None:
        rect = self._selection.normalized()
        if rect.width() < 8 or rect.height() < 8:
            return None
        left = max(0, min(self._image.width() - 1, round(rect.left())))
        top = max(0, min(self._image.height() - 1, round(rect.top())))
        right = max(left + 1, min(self._image.width(), round(rect.right())))
        bottom = max(top + 1, min(self._image.height(), round(rect.bottom())))
        return left, top, right - left, bottom - top

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = self._target_rect()
        if not self._pixmap.isNull() and not target.isEmpty():
            painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        if not self._selection.isEmpty():
            selection = self._image_to_widget_rect(self._selection.normalized())
            accent = QColor(theme.ACCENT)
            fill = QColor(accent)
            fill.setAlpha(48)
            painter.fillRect(selection, fill)
            painter.setPen(QPen(accent, 3.0))
            painter.drawRect(selection)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._image.isNull():
            return super().mousePressEvent(event)
        target = self._target_rect()
        if not target.contains(event.position()):
            return
        start = self._widget_to_image(event.position())
        self._drag_start = start
        self._selection = QRectF(start, start)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_start is None:
            return super().mouseMoveEvent(event)
        current = self._widget_to_image(event.position())
        self._selection = QRectF(self._drag_start, current).normalized()
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            current = self._widget_to_image(event.position())
            self._selection = QRectF(self._drag_start, current).normalized()
            self._drag_start = None
            self.unsetCursor()
            self.update()
            return
        super().mouseReleaseEvent(event)


class _TacticRetryRegionDialog(QDialog):
    def __init__(self, image: QImage, candidate_box: tuple[int, int, int, int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("다시 인식할 원본 영역 지정")
        self.resize(1080, 760)
        self.setMinimumSize(860, 620)
        self._candidate_box = candidate_box

        root = dialog_layout(self)
        guide = QLabel(
            "잘못 인식된 후보가 들어 있는 원본 영역을 마우스로 드래그해 지정하세요. "
            "격자 아래 스킬 사이클까지 다시 읽어야 하면 그 부분도 함께 포함하는 것이 좋습니다."
        )
        guide.setWordWrap(True)
        root.addWidget(guide)

        self.image = _ImageRegionWidget(image, candidate_box, self)
        root.addWidget(self.image, 1)

        actions = QHBoxLayout()
        reset = QPushButton("현재 후보 영역으로 되돌리기")
        reset.clicked.connect(lambda: self.image.set_selection(self._candidate_box))
        actions.addWidget(reset)
        actions.addStretch(1)
        root.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("이 영역 다시 인식")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("AccentButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_box(self) -> tuple[int, int, int, int] | None:
        return self.image.selected_box()


class TacticImageImportReviewDialog(QDialog):
    """Review locally detected boards before they become editable tactic steps."""

    def __init__(self, image_path: str | Path, result: TacticImageImportResult, parent=None):
        super().__init__(parent)
        self.image_path = Path(image_path)
        self.result = result
        self._recommended = set(suggested_board_indexes(result.boards))
        self._formation = set(formation_board_indexes(result.boards))
        self._source_image = self._load_source_image()
        self._sync_step_names()

        self.setWindowTitle("택틱 이미지 인식 결과 확인")
        self.setMinimumSize(1100, 760)
        self._resize_for_screen()

        root = dialog_layout(self)
        splitter = QSplitter()
        splitter.addWidget(self._build_board_list())
        splitter.addWidget(self._build_preview())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 760])
        root.addWidget(splitter, 1)

        warnings = "\n".join(f"• {warning}" for warning in result.warnings)
        if warnings:
            warning_row = QHBoxLayout()
            warning_row.addWidget(help_icon(warnings, warning=True))
            warning_row.addStretch(1)
            root.addLayout(warning_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("선택한 격자 가져오기")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("AccentButton")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        if self.board_list.count():
            preferred = min(self._recommended) if self._recommended else 0
            self.board_list.setCurrentRow(preferred)
        self._selection_changed()

    def _resize_for_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1440, 900)
            return
        available = screen.availableGeometry()
        width = min(max(1280, round(available.width() * 0.84)), max(1100, available.width() - 60))
        height = min(max(820, round(available.height() * 0.86)), max(760, available.height() - 60))
        self.resize(width, height)

    def _load_source_image(self) -> QImage:
        reader = QImageReader(str(self.image_path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return QImage()
        return image

    @staticmethod
    def _marker_counts(result: TacticImageImportResult, index: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        for marker in result.tactic.steps[index].markers:
            counts[marker.kind] = counts.get(marker.kind, 0) + 1
        return counts

    def _board_text(self, index: int) -> str:
        board = self.result.boards[index]
        counts = self._marker_counts(self.result, index)
        step_name = str(self.result.tactic.steps[index].name or f"후보 {index + 1}")
        parts = [
            f"{step_name} · 후보 {index + 1} · {board.rows}×{board.cols}",
            f"신뢰도 {board.confidence * 100:.0f}%",
            f"인형 {counts.get('unit', 0)}",
        ]
        if counts.get("boss"):
            parts.append(f"보스 {counts['boss']}")
        if counts.get("blocked"):
            parts.append(f"이동 불가 {counts['blocked']}")
        if counts.get("cover"):
            parts.append(f"엄폐 {counts['cover']}")
        if index in self._formation:
            parts.append("제대 배치")
        return "\n".join(parts)

    def _build_board_list(self) -> QWidget:
        panel, layout = section_panel("인식된 격자", "체크를 해제하면 최종 택틱에 포함하지 않습니다.")
        self.board_list = QListWidget()
        for index, _board in enumerate(self.result.boards):
            item = QListWidgetItem(self._board_text(index))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if index in self._recommended else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.board_list.addItem(item)
        self.board_list.currentRowChanged.connect(self._preview_board)
        self.board_list.itemChanged.connect(lambda _item: self._selection_changed())
        layout.addWidget(self.board_list, 1)

        row = QHBoxLayout()
        recommended = QPushButton("권장 선택")
        select_all = QPushButton("모두 선택")
        clear = QPushButton("모두 해제")
        recommended.clicked.connect(self._apply_recommended)
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        clear.clicked.connect(lambda: self._set_all_checked(False))
        row.addWidget(recommended)
        row.addWidget(select_all)
        row.addWidget(clear)
        layout.addLayout(row)

        self.retry_board = QPushButton("선택 후보 다시 인식…")
        self.retry_board.setToolTip("현재 후보만 원본에서 다시 영역 지정해 재인식합니다. 다른 후보는 변경하지 않습니다.")
        self.retry_board.clicked.connect(self._retry_current_board)
        layout.addWidget(self.retry_board)

        self.selection_summary = QLabel()
        self.selection_summary.setObjectName("Muted")
        layout.addWidget(self.selection_summary)
        return panel

    def _build_preview(self) -> QWidget:
        panel, layout = section_panel(
            "원본 ↔ 인식 결과",
            "원본과 인식 결과를 크게 비교합니다. 인식 결과의 엘리먼트는 직접 드래그해 위치를 수정할 수 있습니다.",
        )
        preview_row = QHBoxLayout()

        source_box = QVBoxLayout()
        source_title = QLabel("원본")
        source_title.setObjectName("SectionTitle")
        self.source_preview = _AspectImageLabel("원본 미리보기를 불러오지 못했습니다.")
        self.source_preview.setMinimumSize(460, 420)
        source_box.addWidget(source_title)
        source_box.addWidget(self.source_preview, 1)
        preview_row.addLayout(source_box, 1)

        detected_box = QVBoxLayout()
        self.detected_title = QLabel("인식 결과 · 드래그로 위치 수정")
        self.detected_title.setObjectName("SectionTitle")
        self.detected_preview = TacticGridWidget(self.result.tactic, editable=True, move_only=True)
        self.detected_preview.setMinimumSize(460, 420)
        self.detected_preview.setToolTip("인형과 기타 엘리먼트를 마우스로 잡아 원하는 격자 칸으로 옮길 수 있습니다.")
        self.detected_preview.modified.connect(self._detected_grid_modified)
        detected_box.addWidget(self.detected_title)
        detected_box.addWidget(self.detected_preview, 1)
        preview_row.addLayout(detected_box, 1)
        layout.addLayout(preview_row, 1)

        editor_heading = QHBoxLayout()
        editor_title = QLabel("OCR 결과 바로 수정")
        editor_title.setObjectName("SectionTitle")
        editor_heading.addWidget(editor_title)
        editor_heading.addStretch(1)
        self.formation_check = QCheckBox("제대 배치")
        self.formation_check.toggled.connect(self._formation_toggled)
        editor_heading.addWidget(self.formation_check)
        layout.addLayout(editor_heading)

        self.marker_table = QTableWidget(0, 3)
        self.marker_table.setHorizontalHeaderLabels(["종류", "위치", "OCR 표기"])
        self.marker_table.verticalHeader().setVisible(False)
        self.marker_table.setAlternatingRowColors(True)
        self.marker_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.marker_table.setMinimumHeight(145)
        self.marker_table.setMaximumHeight(210)
        header = self.marker_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.marker_table)

        cycle_label = QLabel("스킬 사이클 OCR · 직접 교정")
        cycle_label.setObjectName("Muted")
        layout.addWidget(cycle_label)
        self.cycle_edit = QPlainTextEdit()
        self.cycle_edit.setPlaceholderText("사이클이 있으면 OCR 결과를 직접 고치세요. 제대 배치형은 비워둬도 됩니다.")
        self.cycle_edit.setMaximumHeight(78)
        self.cycle_edit.textChanged.connect(self._cycle_edited)
        layout.addWidget(self.cycle_edit)

        self.board_detail = QLabel()
        self.board_detail.setObjectName("Muted")
        self.board_detail.setWordWrap(True)
        layout.addWidget(self.board_detail)
        return panel

    def _current_board_index(self) -> int | None:
        row = self.board_list.currentRow()
        if not (0 <= row < self.board_list.count()):
            return None
        return int(self.board_list.item(row).data(Qt.ItemDataRole.UserRole))

    def _populate_ocr_editor(self, index: int) -> None:
        step = self.result.tactic.steps[index]
        editable = [marker for marker in step.markers if marker.kind in {"unit", "summon", "custom"}]
        self.marker_table.setRowCount(0)
        for row, marker in enumerate(editable):
            self.marker_table.insertRow(row)
            kind_combo = QComboBox()
            for label, kind in (("인형", "unit"), ("소환수 · 설치물", "summon"), ("기타", "custom")):
                kind_combo.addItem(label, kind)
            selected = kind_combo.findData(marker.kind)
            kind_combo.setCurrentIndex(selected if selected >= 0 else 0)
            kind_combo.currentIndexChanged.connect(
                lambda _value, target=marker, combo=kind_combo: self._marker_kind_edited(target, combo)
            )
            self.marker_table.setCellWidget(row, 0, kind_combo)

            position = QTableWidgetItem(f"행 {marker.row + 1} · 열 {marker.col + 1}")
            position.setFlags(position.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.marker_table.setItem(row, 1, position)

            label_edit = QLineEdit(marker.label or "?")
            label_edit.setMaxLength(24)
            label_edit.setPlaceholderText("표기")
            label_edit.textEdited.connect(
                lambda text, target=marker: self._marker_label_edited(target, text)
            )
            self.marker_table.setCellWidget(row, 2, label_edit)

        self.formation_check.blockSignals(True)
        self.formation_check.setChecked(index in self._formation)
        self.formation_check.blockSignals(False)

        self.cycle_edit.blockSignals(True)
        self.cycle_edit.setPlainText(step.cycle)
        self.cycle_edit.setEnabled(index not in self._formation)
        if index in self._formation:
            self.cycle_edit.setPlaceholderText("제대 배치 격자 · 스킬 사이클을 사용하지 않습니다.")
        else:
            self.cycle_edit.setPlaceholderText("OCR 결과를 바로 수정하세요. 사이클이 없으면 비워두면 됩니다.")
        self.cycle_edit.blockSignals(False)

    def _sync_step_names(self) -> None:
        combat_index = 0
        for index, step in enumerate(self.result.tactic.steps):
            if index in self._formation:
                step.name = "제대 배치"
                step.cycle = ""
            else:
                combat_index += 1
                step.name = f"T{combat_index}"

    def _formation_toggled(self, checked: bool) -> None:
        index = self._current_board_index()
        if index is None:
            return
        if checked:
            self._formation.add(index)
        else:
            self._formation.discard(index)
        self._sync_step_names()
        self._populate_ocr_editor(index)
        self._refresh_board_item(index)
        self._render_detected_preview(index)
        self._update_board_detail(index)

    def _marker_kind_edited(self, marker, combo: QComboBox) -> None:
        marker.kind = str(combo.currentData() or "unit")
        if marker.kind != "unit":
            marker.unit_key = ""
        self._refresh_edited_preview()

    def _marker_label_edited(self, marker, text: str) -> None:
        marker.label = str(text or "")[:24] or "?"
        self._refresh_edited_preview()

    def _cycle_edited(self) -> None:
        index = self._current_board_index()
        if index is None or index in self._formation:
            return
        self.result.tactic.steps[index].cycle = self.cycle_edit.toPlainText()[:2000]
        self._update_board_detail(index)

    def _refresh_board_item(self, index: int) -> None:
        for row in range(self.board_list.count()):
            item = self.board_list.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == int(index):
                item.setText(self._board_text(index))
                break

    def _refresh_edited_preview(self) -> None:
        index = self._current_board_index()
        if index is None:
            return
        self._refresh_board_item(index)
        self._render_detected_preview(index)
        self._update_board_detail(index)

    def _detected_grid_modified(self) -> None:
        index = self._current_board_index()
        if index is None:
            return
        self._populate_ocr_editor(index)
        self._refresh_board_item(index)
        self._update_board_detail(index)

    def _render_detected_preview(self, index: int) -> None:
        step_name = str(self.result.tactic.steps[index].name or f"후보 {index + 1}")
        self.detected_title.setText(f"인식 결과 · {step_name} · 드래그로 위치 수정")
        self.detected_preview.set_step_index(index)
        self.detected_preview.update()

    def _update_board_detail(self, index: int) -> None:
        board = self.result.boards[index]
        step = self.result.tactic.steps[index]
        counts = self._marker_counts(self.result, index)
        marker_text = " · ".join(
            (
                f"인형 {counts.get('unit', 0)}",
                f"소환수 {counts.get('summon', 0)}",
                f"보스 {counts.get('boss', 0)}",
                f"이동 불가 {counts.get('blocked', 0)}",
                f"엄폐 {counts.get('cover', 0)}",
            )
        )
        unit_labels = [
            marker.label
            for marker in step.markers
            if marker.kind == "unit" and marker.label and marker.label != "?"
        ]
        label_text = " · ".join(unit_labels) if unit_labels else "인식 없음"
        recommendation = "제대 배치 · 가져오기 권장" if index in self._formation else "가져오기 권장"
        cycle = step.cycle.strip()
        has_any_cycle = any(item.cycle.strip() for item in self.result.tactic.steps)
        if index in self._formation:
            cycle_text = "제대 배치 · 스킬 사이클 OCR 대상 아님"
        else:
            cycle_text = (f"스킬 사이클 · {cycle or '이 단계 없음'}" if has_any_cycle else "제대 배치형 · 스킬 사이클 표 없음")
        self.board_detail.setText(
            f"{board.rows}×{board.cols} · 신뢰도 {board.confidence * 100:.0f}% · {marker_text}\n"
            f"{recommendation}\n인형 글자 · {label_text}\n{cycle_text}"
        )

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.board_list.blockSignals(True)
        for row in range(self.board_list.count()):
            self.board_list.item(row).setCheckState(state)
        self.board_list.blockSignals(False)
        self._selection_changed()

    def _apply_recommended(self) -> None:
        self.board_list.blockSignals(True)
        for row in range(self.board_list.count()):
            index = int(self.board_list.item(row).data(Qt.ItemDataRole.UserRole))
            state = Qt.CheckState.Checked if index in self._recommended else Qt.CheckState.Unchecked
            self.board_list.item(row).setCheckState(state)
        self.board_list.blockSignals(False)
        self._selection_changed()

    def _default_retry_box(self, index: int) -> tuple[int, int, int, int]:
        board = self.result.boards[index]
        x, y, width, height = board.box
        if self._source_image.isNull():
            return x, y, width, height
        extra_x = max(12, round(width * 0.08))
        extra_top = max(12, round(height * 0.06))
        extra_bottom = max(70, round(height * 0.28))
        left = max(0, x - extra_x)
        top = max(0, y - extra_top)
        right = min(self._source_image.width(), x + width + extra_x)
        bottom = min(self._source_image.height(), y + height + extra_bottom)
        return left, top, max(1, right - left), max(1, bottom - top)

    def _retry_current_board(self) -> None:
        index = self._current_board_index()
        if index is None or self._source_image.isNull():
            return
        old_board = self.result.boards[index]
        selector = _TacticRetryRegionDialog(
            self._source_image,
            self._default_retry_box(index),
            self,
        )
        if selector.exec() != QDialog.DialogCode.Accepted:
            return
        region = selector.selected_box()
        if region is None:
            QMessageBox.information(self, "후보 다시 인식", "다시 인식할 영역을 드래그해 지정해 주세요.")
            return

        progress = QProgressDialog("선택 영역 다시 인식 중…", "", 0, 0, self)
        progress.setWindowTitle("후보 다시 인식")
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        def update_progress(text: str) -> None:
            progress.setLabelText(str(text or "선택 영역 다시 인식 중…"))
            QApplication.processEvents()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            retried = reimport_tactic_region(
                self.image_path,
                region,
                expected_rows=old_board.rows,
                expected_cols=old_board.cols,
                progress=update_progress,
            )
        except Exception as exc:
            show_error(self, "후보 다시 인식 실패", exc)
            return
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()

        new_board = retried.boards[0]
        new_step = retried.tactic.steps[0]
        old_step = self.result.tactic.steps[index]
        new_step.name = old_step.name
        if index in self._formation:
            new_step.name = "제대 배치"
            new_step.cycle = ""
        new_step.note = old_step.note
        self.result.tactic.steps[index] = new_step
        boards = list(self.result.boards)
        boards[index] = new_board
        self.result = TacticImageImportResult(
            tactic=self.result.tactic,
            boards=tuple(boards),
            warnings=self.result.warnings,
        )
        self._refresh_board_item(index)
        self._populate_ocr_editor(index)
        self._preview_board(self.board_list.currentRow())
        QMessageBox.information(
            self,
            "후보 다시 인식 완료",
            "선택한 후보만 새 영역을 기준으로 다시 인식했습니다. 나머지 후보는 그대로 유지됩니다.",
        )

    def selected_indexes(self) -> list[int]:
        selected: list[int] = []
        for row in range(self.board_list.count()):
            item = self.board_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def selected_tactic(self) -> Tactic:
        return self.result.selected_tactic(
            self.selected_indexes(),
            formation_indexes=self._formation,
        )

    def _selection_changed(self) -> None:
        count = len(self.selected_indexes())
        self.selection_summary.setText(f"선택 {count} / {len(self.result.boards)}개")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(count > 0)
        self.retry_board.setEnabled(self._current_board_index() is not None and not self._source_image.isNull())

    def _preview_board(self, row: int) -> None:
        if not (0 <= row < len(self.result.boards)):
            self.source_preview.clear()
            self.board_detail.clear()
            self.marker_table.setRowCount(0)
            self.cycle_edit.clear()
            return
        item = self.board_list.item(row)
        index = int(item.data(Qt.ItemDataRole.UserRole))
        board = self.result.boards[index]

        if not self._source_image.isNull():
            x, y, width, height = board.box
            extra = min(90, max(0, self._source_image.height() - (y + height)))
            crop = self._source_image.copy(x, y, width, height + extra)
            self.source_preview.set_image(crop)

        self._render_detected_preview(index)
        self._populate_ocr_editor(index)
        self._update_board_detail(index)
