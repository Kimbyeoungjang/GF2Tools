from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ... import reference
from ...repository import Repository
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


class DashboardPage(DeferredRefreshPage):
    navigateRequested = Signal(str)

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self.repo = repo
        self._hero_target = "data_sync"

        root = page_layout(self, "대시보드")
        root.addWidget(self._build_status_hero())
        root.addLayout(self._build_metrics_grid())
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
