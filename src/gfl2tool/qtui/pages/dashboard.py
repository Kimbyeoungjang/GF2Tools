from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ... import reference
from ...repository import Repository
from ...services.checklist import CATEGORIES, CATEGORY_LABELS, ChecklistStore
from ..widgets import MetricCard, page_layout, section_panel
from .base import DeferredRefreshPage


_METRICS = (
    ("dolls", "보유 인형"),
    ("remoldings", "보유 리몰딩"),
    ("formations", "제대 편성"),
    ("data_sync", "게임 데이터 버전"),
)

_WORKFLOWS = (
    ("보유 현황", "inventory", "보유 현황 열기"),
    ("제대 편성", "formation", "제대 편성 열기"),
    ("리몰딩 최적화", "remolding_optimizer", "최적화 열기"),
    ("요리 계산기", "cooking", "요리 계산기 열기"),
    ("택틱 · 오버레이", "tactics", "택틱 편집기 열기"),
    ("데이터 동기화", "data_sync", "데이터 동기화 열기"),
)


class _ImmediateCheckBox(QCheckBox):
    """Toggle on mouse press so fast checklist sweeps never lose a click.

    QAbstractButton normally changes state on mouse release. When users rapidly
    sweep through several checklist rows, a release can land outside the row
    after the pointer has already moved to the next item. Toggling on press
    makes the visual state immediate while keyboard interaction keeps Qt's
    normal checkbox behaviour.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._left_press_toggled = False

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._left_press_toggled = True
            self.setChecked(not self.isChecked())
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._left_press_toggled:
            self._left_press_toggled = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DashboardPage(DeferredRefreshPage):
    navigateRequested = Signal(str)

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.checklist = ChecklistStore(repo.path.parent)
        self._hero_target = "data_sync"
        self._checklist_payload: dict | None = None
        self._checklist_dirty = False
        self._checklist_save_timer = QTimer(self)
        self._checklist_save_timer.setSingleShot(True)
        self._checklist_save_timer.setInterval(1200)
        self._checklist_save_timer.timeout.connect(self._flush_checklist_changes)

        root = page_layout(self, "대시보드")
        root.addWidget(self._build_status_hero())
        root.addLayout(self._build_metrics_grid())
        root.addWidget(self._build_checklist_panel())
        root.addWidget(self._build_workflow_panel())
        root.addWidget(self._build_status_panel())
        root.addStretch(1)

    def _build_status_hero(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(16)

        text = QVBoxLayout()
        text.setSpacing(5)
        title_row = QHBoxLayout()
        self.hero_title = QLabel("보유 데이터 확인 중")
        self.hero_title.setObjectName("PageTitle")
        title_row.addWidget(self.hero_title)
        title_row.addStretch(1)
        text.addLayout(title_row)
        layout.addLayout(text, 1)

        self.hero_action = QPushButton("데이터 동기화 열기")
        self.hero_action.setObjectName("AccentButton")
        self.hero_action.clicked.connect(lambda: self.navigateRequested.emit(self._hero_target))
        layout.addWidget(self.hero_action)
        return panel

    def _build_metrics_grid(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.cards: dict[str, MetricCard] = {}
        for index, (key, label) in enumerate(_METRICS):
            card = MetricCard(label)
            self.cards[key] = card
            grid.addWidget(card, 0, index)
        return grid

    def _build_checklist_panel(self) -> QFrame:
        panel, layout = section_panel("체크리스트")
        heading = QHBoxLayout()
        self.checklist_summary = QLabel("")
        self.checklist_summary.setObjectName("Muted")
        heading.addWidget(self.checklist_summary)
        heading.addStretch(1)
        manage = QPushButton("체크리스트 관리")
        manage.clicked.connect(lambda: self.navigateRequested.emit("checklist"))
        heading.addWidget(manage)
        layout.addLayout(heading)

        self.checklist_grid = QGridLayout()
        self.checklist_grid.setHorizontalSpacing(16)
        self.checklist_grid.setVerticalSpacing(12)
        layout.addLayout(self.checklist_grid)
        return panel

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()
            nested = item.layout()
            if nested is not None:
                DashboardPage._clear_layout(nested)

    def _render_checklist(self) -> None:
        # A refresh can arrive while the user is rapidly checking rows. Never
        # force a synchronous disk write or replace the live in-memory state in
        # that case; doing so made the checkbox appear to lag or briefly ignore
        # clicks on Windows. Calendar/reset reloads still happen whenever there
        # are no unsaved local edits.
        if self._checklist_dirty and self._checklist_payload is not None:
            payload = self._checklist_payload
        else:
            payload = self.checklist.load()
            self._checklist_payload = payload
        self._clear_layout(self.checklist_grid)
        for column, category in enumerate(CATEGORIES):
            box = QFrame()
            box.setObjectName("ChecklistCategoryCard")
            box.setMinimumWidth(300)
            box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(14, 12, 14, 14)
            box_layout.setSpacing(8)
            box_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            rows = payload["items"][category]
            done = sum(1 for row in rows if row.get("checked"))
            title = QLabel(f"{CATEGORY_LABELS[category]}  {done}/{len(rows)}")
            title.setObjectName("SectionTitle")
            title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            box_layout.addWidget(title)
            for row in rows:
                checkbox = _ImmediateCheckBox(str(row.get("label") or ""))
                checkbox.setObjectName("ChecklistCheckBox")
                checkbox.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                checkbox.setChecked(bool(row.get("checked")))
                checkbox.setToolTip(str(row.get("label") or ""))
                item_id = str(row.get("id") or "")
                checkbox.toggled.connect(
                    lambda checked, c=category, i=item_id: self._checklist_toggled(c, i, checked)
                )
                box_layout.addWidget(checkbox)
            # Daily has more rows than weekly/monthly. Keep every category
            # anchored to the top and leave the remaining shared card height
            # genuinely blank instead of stretching the few rows vertically.
            box_layout.addStretch(1)
            self.checklist_grid.addWidget(box, 0, column, alignment=Qt.AlignmentFlag.AlignTop)
        for column in range(len(CATEGORIES)):
            self.checklist_grid.setColumnStretch(column, 1)
        self._update_checklist_summary(payload)

    def _update_checklist_summary(self, payload: dict) -> None:
        self.checklist_summary.setText(
            " · ".join(
                f"{CATEGORY_LABELS[category]} "
                f"{sum(1 for row in payload['items'][category] if row.get('checked'))}/{len(payload['items'][category])}"
                for category in CATEGORIES
            )
        )

    def _checklist_toggled(self, category: str, item_id: str, checked: bool) -> None:
        payload = self._checklist_payload
        if payload is None or category not in CATEGORIES:
            self.request_refresh()
            return
        for item in payload["items"][category]:
            if str(item.get("id") or "") == str(item_id):
                item["checked"] = bool(checked)
                self._checklist_dirty = True
                self._update_checklist_summary(payload)
                # Re-arm a deliberately relaxed timer for every click. Mouse
                # state changes are immediate; disk I/O happens only after the
                # user has stopped interacting for a moment.
                self._checklist_save_timer.start()
                return
        self.request_refresh()

    def _flush_checklist_changes(self, *, durable: bool = False) -> None:
        if not self._checklist_dirty or self._checklist_payload is None:
            return
        payload = self._checklist_payload
        try:
            self.checklist.save(payload, durable=durable)
        except Exception as exc:
            self._checklist_dirty = True
            self.refreshFailed.emit(f"체크리스트 저장에 실패했습니다: {exc}")
            return
        self._checklist_dirty = False

    def on_deactivated(self) -> None:
        if self._checklist_save_timer.isActive():
            self._checklist_save_timer.stop()
        self._flush_checklist_changes(durable=True)

    def _build_workflow_panel(self) -> QFrame:
        panel, layout = section_panel("주요 작업")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for index, (title, page_key, button_text) in enumerate(_WORKFLOWS):
            card = QFrame()
            card.setObjectName("PanelAlt")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(7)
            heading_row = QHBoxLayout()
            heading = QLabel(title)
            heading.setObjectName("SectionTitle")
            heading_row.addWidget(heading)
            heading_row.addStretch(1)
            card_layout.addLayout(heading_row)
            card_layout.addStretch(1)
            button = QPushButton(button_text)
            if page_key in {"formation", "remolding_optimizer"}:
                button.setObjectName("AccentButton")
            button.clicked.connect(
                lambda _checked=False, key=page_key: self.navigateRequested.emit(key)
            )
            card_layout.addWidget(button)
            grid.addWidget(card, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return panel

    def _build_status_panel(self) -> QFrame:
        panel, layout = section_panel("준비 상태")
        self.sync = QLabel("")
        self.sync.setWordWrap(True)
        layout.addWidget(self.sync)
        return panel

    def refresh(self) -> None:
        self._render_checklist()
        summary = self.repo.inventory_summary()
        doll_count = int(summary.get("dolls", 0))
        remolding_count = int(summary.get("remoldings", 0))
        data_ready = doll_count > 0 or remolding_count > 0
        formation_count = len(self.repo.rows("formation_plans", order_by="id"))

        self.cards["dolls"].value.setText(f"{doll_count:,}")
        self.cards["remoldings"].value.setText(f"{remolding_count:,}")
        self.cards["formations"].value.setText(f"{formation_count:,}")
        version = reference.program_version()
        game_version = str(version.get("game_version") or "").strip()
        data_version = str(version.get("data_version") or "").strip()
        self.cards["data_sync"].value.setText(game_version or "미설치")
        self.cards["data_sync"].value.setToolTip(
            f"게임 버전 {game_version or '미상'} · 데이터 {data_version or '미상'}"
        )

        if data_ready:
            self.hero_title.setText("사용자 데이터 준비 완료")
            self.hero_action.setText("제대 편성 열기")
            self._hero_target = "formation"
            self.sync.setText(
                f"사용자 데이터 · 수동/OCR/파일 동기화 사용 가능 · "
                f"게임 데이터 {game_version or '미설치'} ({data_version or '버전 미상'})"
            )
        else:
            self.hero_title.setText("보유 데이터를 입력해 주세요")
            self.hero_action.setText("데이터 동기화 열기")
            self._hero_target = "data_sync"
            self.sync.setText(
                "데이터 동기화에서 보조 툴 사용자 ZIP 또는 기존 GF2Tools 백업을 가져오세요. "
                f"프로그램 게임 데이터: {game_version or '미설치'} ({data_version or '버전 미상'})"
            )
