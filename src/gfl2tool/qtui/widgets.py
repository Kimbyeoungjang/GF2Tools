from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QToolTip,
    QTableView,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme


PAGE_MARGINS = (20, 18, 20, 18)
PAGE_SPACING = 10
DIALOG_MARGINS = (16, 16, 16, 16)
DIALOG_SPACING = 10
RESULT_DIALOG_SIZE = (980, 720)
RESULT_DIALOG_MIN_SIZE = (760, 560)


def show_error(parent: QWidget, title: str, error: object) -> None:
    """Show one consistent error dialog across synchronous and worker failures."""
    detail = str(error or "").strip() or "알 수 없는 오류가 발생했습니다."
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    summary = lines[-1] if lines else detail

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(str(title))
    box.setText(summary)
    if summary != detail:
        box.setDetailedText(detail)
    box.exec()


def replace_table_model(view: QAbstractItemView, model: Any) -> None:
    """Replace a transient item model and release the previous QObject promptly."""
    old = view.model()
    if old is model:
        return

    view.setModel(model)
    if old is not None and old is not model:
        try:
            old.deleteLater()
        except (AttributeError, RuntimeError):
            pass


def configure_table_view(
    view: QTableView,
    *,
    widths: dict[int, int] | None = None,
    hidden: set[int] | None = None,
    sorting: bool = True,
    stretch_last: bool = True,
    select_rows: bool = False,
    extended_selection: bool = False,
) -> QTableView:
    """Apply the project's common table behavior without expensive auto sizing."""
    view.setAlternatingRowColors(True)
    view.setShowGrid(False)
    view.verticalHeader().setVisible(False)
    view.setSortingEnabled(bool(sorting))

    if stretch_last:
        view.horizontalHeader().setStretchLastSection(True)
    if select_rows:
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        selection_mode = (
            QAbstractItemView.SelectionMode.ExtendedSelection
            if extended_selection
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        view.setSelectionMode(selection_mode)

    for column, width in (widths or {}).items():
        view.setColumnWidth(int(column), int(width))
    for column in hidden or set():
        view.setColumnHidden(int(column), True)
    return view


def configure_tree_widget(
    tree: QTreeWidget,
    *,
    widths: dict[int, int] | None = None,
    stretch_last: bool = True,
    stretch_column: int | None = None,
    select_rows: bool = True,
) -> QTreeWidget:
    """Apply the common compact list/tree behavior used by grouped browsers.

    ``stretch_column`` takes precedence over ``stretch_last`` and is useful for
    trees where a long description column should consume the remaining width
    while a trailing ID column stays compact.
    """
    tree.setAlternatingRowColors(True)
    tree.setUniformRowHeights(True)
    tree.verticalScrollBar().setSingleStep(24)

    header = tree.header()
    if stretch_column is not None:
        header.setStretchLastSection(False)
        header.setSectionResizeMode(int(stretch_column), QHeaderView.ResizeMode.Stretch)
    elif stretch_last:
        header.setStretchLastSection(True)
    if select_rows:
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    for column, width in (widths or {}).items():
        tree.setColumnWidth(int(column), int(width))
    return tree


class CancellableJobDialogMixin:
    """Common close/cancel policy for modal dialogs running cooperative jobs."""

    _job_handle = None

    def cancel_active_job(self) -> bool:
        handle = getattr(self, "_job_handle", None)
        if handle is None:
            return False
        try:
            handle.cancel()
            return True
        except (AttributeError, RuntimeError):
            return False

    def closeEvent(self, event):  # noqa: N802
        if getattr(self, "_job_handle", None) is not None:
            self.cancel_active_job()
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if getattr(self, "_job_handle", None) is not None:
            self.cancel_active_job()
            return
        super().reject()


class HoverHelpButton(QToolButton):
    """Compact help affordance with a predictable near-instant tooltip.

    Native platform tooltip wake-up delays are often around half a second or
    longer.  That feels sluggish when the UI intentionally hides secondary
    explanations behind ``?``/``!`` icons.  This button keeps native tooltips
    disabled and shows the same QToolTip surface after a short project-owned
    delay instead.
    """

    SHOW_DELAY_MS = 80

    def __init__(self, text: str = "", *, warning: bool = False, parent=None):
        super().__init__(parent)
        self._help_text = ""
        self._hovered = False
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(self.SHOW_DELAY_MS)
        self._show_timer.timeout.connect(self._show_help)

        self.setObjectName("WarningHelpIcon" if warning else "HelpIcon")
        self.setText("!" if warning else "?")
        self.setAccessibleName("주의" if warning else "도움말")
        self.setAutoRaise(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFixedSize(26, 26)
        self.setToolTip(text)

    def setToolTip(self, text: str) -> None:  # noqa: N802
        """Update help text while bypassing Qt's slower native wake-up delay."""
        self._help_text = str(text or "").strip()
        self.setAccessibleDescription(self._help_text)
        super().setToolTip("")
        if not self._help_text:
            self._show_timer.stop()
            if self._hovered:
                QToolTip.hideText()
            return
        if self._hovered:
            QToolTip.hideText()
            self._show_timer.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        if self._help_text:
            self._show_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._show_timer.stop()
        QToolTip.hideText()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._show_timer.stop()
        QToolTip.hideText()
        super().hideEvent(event)

    def _show_help(self) -> None:
        if not self._hovered or not self._help_text or not self.isVisible():
            return
        anchor = self.mapToGlobal(QPoint(0, self.height() + 4))
        QToolTip.showText(anchor, self._help_text, self)


def help_icon(text: str, *, warning: bool = False) -> HoverHelpButton:
    """Return a compact hover-only help affordance for secondary explanations."""
    return HoverHelpButton(text, warning=warning)


def page_title(title: str, subtitle: str = "") -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(3)

    heading = QHBoxLayout()
    heading.setSpacing(6)
    label = QLabel(title)
    label.setObjectName("PageTitle")
    heading.addWidget(label)
    if subtitle:
        label.setAccessibleDescription(subtitle)
        box.setAccessibleDescription(subtitle)
    heading.addStretch(1)
    layout.addLayout(heading)
    return box


def page_layout(parent: QWidget, title: str, subtitle: str = "") -> QVBoxLayout:
    """Create the common top-level layout used by every primary page."""
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(*PAGE_MARGINS)
    layout.setSpacing(PAGE_SPACING)
    layout.addWidget(page_title(title, subtitle))
    return layout


def dialog_layout(parent: QWidget) -> QVBoxLayout:
    """Create the common content layout used by modal dialogs."""
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(*DIALOG_MARGINS)
    layout.setSpacing(DIALOG_SPACING)
    return layout


def section_panel(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Create a consistent bordered section used by dense operational pages."""
    panel = QFrame()
    panel.setObjectName("Panel")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)

    heading_row = QHBoxLayout()
    heading_row.setSpacing(5)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    heading_row.addWidget(heading)
    if subtitle:
        heading.setAccessibleDescription(subtitle)
        panel.setAccessibleDescription(subtitle)
    heading_row.addStretch(1)
    layout.addLayout(heading_row)
    return panel, layout


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "0", parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("Muted")
        layout.addWidget(title_label)

        self.value = QLabel(value)
        self.value.setObjectName("Metric")
        layout.addWidget(self.value)


class BusyButton(QPushButton):
    def set_busy(self, busy: bool, text: str | None = None) -> None:
        if busy:
            if not hasattr(self, "_normal_text"):
                self._normal_text = self.text()
            self.setText(text or "처리 중…")
            self.setEnabled(False)
            return

        self.setText(getattr(self, "_normal_text", self.text()))
        self.setEnabled(True)


class ResultDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*RESULT_DIALOG_SIZE)
        self.setMinimumSize(*RESULT_DIALOG_MIN_SIZE)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ResultScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.body = QWidget()
        self.body.setObjectName("ResultBody")
        self.host = QVBoxLayout(self.body)
        self.host.setContentsMargins(0, 0, 0, 0)
        self.host.setSpacing(10)
        self.host.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        self.button_box.accepted.connect(self.accept)
        self.close_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Close
        )
        self.close_button.setText("닫기")
        root.addWidget(self.button_box)

    def add_title(self, text: str, muted: str = "") -> None:
        label = QLabel(text)
        label.setObjectName("PageTitle")
        self.host.addWidget(label)

        if muted:
            description = QLabel(muted)
            description.setObjectName("Muted")
            description.setWordWrap(True)
            self.host.addWidget(description)

    def add_remolding_result(
        self,
        result: dict[str, Any],
        factor_names: dict[str, str],
        option_names: dict[str, dict],
    ) -> None:
        pieces = list(result.get("pieces") or [])
        score = float(result.get("total_score") or result.get("score") or 0)

        title = QLabel(f"추천 {len(pieces)}개 · 총점 {score:,.0f}")
        title.setObjectName("SectionTitle")
        self.host.addWidget(title)

        shortages = result.get("shortages") or []
        if shortages:
            warning = QLabel(" · ".join(shortages))
            warning.setObjectName("WarningText")
            self.host.addWidget(warning)

        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for index, piece in enumerate(pieces):
            card = self._remolding_card(index, piece, factor_names, option_names)
            grid.addWidget(card, index // 3, index % 3)
        self.host.addWidget(body)

    @staticmethod
    def _remolding_card(
        index: int,
        piece: dict[str, Any],
        factor_names: dict[str, str],
        option_names: dict[str, dict],
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("Panel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 9)

        factor_key = str(piece.get("primary_factor") or "")
        factor = factor_names.get(factor_key, factor_key)
        title = QLabel(f"{index + 1}. {factor}")
        title.setObjectName("AccentText")
        layout.addWidget(title)

        for slot in piece.get("slots", []):
            meta = option_names.get(str(slot.get("option_key") or ""), {})
            name = meta.get("nameKR") or slot.get("name") or "옵션"
            level = int(slot.get("level_contribution") or slot.get("variant") or 0)
            text = f"{name}" + (f"  +{level}" if level else "")
            label = QLabel(text)
            label.setWordWrap(True)
            layout.addWidget(label)

        score = QLabel(f"{float(piece.get('score') or 0):,.0f}점")
        score.setObjectName("Muted")
        layout.addWidget(score)
        return card

    def add_stats_comparison(
        self,
        *,
        phenomenon_status: dict[str, Any] | None = None,
        aggregate_levels: dict[str, dict[str, Any]] | None = None,
        target_status: list[dict[str, Any]] | None = None,
        factor_names: dict[str, str] | None = None,
    ) -> None:
        factor_names = factor_names or {}
        phenomenon_status = phenomenon_status or {}
        aggregate_levels = aggregate_levels or {}
        target_status = target_status or []

        self._add_phenomenon_status(phenomenon_status, factor_names)
        if target_status:
            self._add_target_status(target_status)
        self._add_active_stats(aggregate_levels)

    def _add_phenomenon_status(
        self,
        phenomenon_status: dict[str, Any],
        factor_names: dict[str, str],
    ) -> None:
        title = QLabel("현상 인자 · 목표 / 현재")
        title.setObjectName("SectionTitle")
        self.host.addWidget(title)

        panel = QFrame()
        panel.setObjectName("Panel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)

        current = dict(phenomenon_status.get("factor_levels") or {})
        desired_state = phenomenon_status.get("desired") or {}
        desired = dict(desired_state.get("requirements") or {})

        for column, factor in enumerate(theme.FACTOR_ORDER):
            current_level = int(current.get(factor, 0))
            goal_level = int(desired.get(factor, 0))
            met = current_level >= goal_level if goal_level else True
            card = self._factor_status_card(
                factor,
                factor_names,
                goal_level,
                current_level,
                met,
            )
            grid.addWidget(card, 0, column)

        stage = phenomenon_status.get("desired_stage")
        level = int(phenomenon_status.get("character_level") or 60)
        footer = QLabel(
            f"목표 현상 {stage or '—'} · "
            f"{'달성' if desired_state.get('active') else '미달'} · Lv.{level}/60"
        )
        footer.setObjectName("Muted")
        grid.addWidget(footer, 1, 0, 1, len(theme.FACTOR_ORDER))
        self.host.addWidget(panel)

    @staticmethod
    def _factor_status_card(
        factor: str,
        factor_names: dict[str, str],
        goal: int,
        current: int,
        met: bool,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("PanelAlt")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)

        name = QLabel(str(factor_names.get(factor, factor)))
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Factor colors are semantic and intentionally independent from the surface theme.
        name.setStyleSheet(
            f"font-weight:700;color:{theme.FACTOR_COLORS.get(factor, theme.ACCENT)}"
        )
        layout.addWidget(name)

        value = QLabel(f"{goal} / {current}" if goal else f"— / {current}")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.setObjectName("SuccessText" if met else "DangerText")
        value.setStyleSheet("font-size:14pt;")
        layout.addWidget(value)
        return card

    def _add_target_status(self, target_status: list[dict[str, Any]]) -> None:
        title = QLabel("추천 스탯 · 목표 / 현재")
        title.setObjectName("SectionTitle")
        self.host.addWidget(title)

        panel = QFrame()
        panel.setObjectName("Panel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)

        for index, row in enumerate(target_status):
            card = QFrame()
            card.setObjectName("PanelAlt")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(8, 6, 8, 6)

            name = QLabel(str(row.get("name") or row.get("option_key") or "옵션"))
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(name)

            target_level = int(row.get("target_level") or 0)
            display_level = int(row.get("display_level") or 0)
            value = QLabel(f"{target_level} / {display_level}")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setObjectName("SuccessText" if row.get("met") else "DangerText")
            layout.addWidget(value)
            grid.addWidget(card, index // 4, index % 4)

        self.host.addWidget(panel)

    def _add_active_stats(
        self,
        aggregate_levels: dict[str, dict[str, Any]],
    ) -> None:
        active = [
            dict(value)
            for value in aggregate_levels.values()
            if int(value.get("display_level") or 0) > 0
        ]
        if not active:
            return

        active.sort(
            key=lambda row: (
                -int(row.get("display_level") or 0),
                str(row.get("name") or row.get("option_key") or ""),
            )
        )

        title = QLabel("전체 활성 스탯")
        title.setObjectName("SectionTitle")
        self.host.addWidget(title)

        panel = QFrame()
        panel.setObjectName("Panel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)

        for index, row in enumerate(active):
            text = (
                f"{row.get('name') or row.get('option_key')}  "
                f"Lv.{int(row.get('display_level') or 0)}"
            )
            if row.get("value") is not None:
                text += f"  ·  {row.get('value')}%"
            label = QLabel(text)
            label.setObjectName("Muted")
            grid.addWidget(label, index // 3, index % 3)

        self.host.addWidget(panel)


class PortraitLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(120, 100)
        self.setObjectName("PortraitSurface")

    def set_image(
        self,
        image: QImage | None,
        max_w: int = 300,
        max_h: int = 220,
    ) -> None:
        if image is None or image.isNull():
            self.setPixmap(QPixmap())
            self.setText("이미지 없음")
            return

        dpr = max(1.0, float(self.devicePixelRatioF()))
        pixmap = QPixmap.fromImage(image).scaled(
            max(1, int(round(max_w * dpr))),
            max(1, int(round(max_h * dpr))),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(dpr)
        self.setPixmap(pixmap)
        self.setText("")
