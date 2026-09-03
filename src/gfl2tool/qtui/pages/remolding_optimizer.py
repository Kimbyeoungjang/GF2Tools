from __future__ import annotations

from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QTextBrowser,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import reference
from ...repository import Repository
from ...services.remolding_recommendation import (
    RemoldingRecommendationService,
)
from ...services.doll_categories import DollCategoryStore
from ...services.dolls import DollCharacterResolver
from ...services.recommendation_profiles import (
    default_recommendation_profile_name,
    export_recommendation_profiles,
    import_recommendation_profiles,
)
from ..data import OwnedDollCatalog
from ..dialogs.category_assign import DollCategoryAssignDialog
from ..dialogs.remolding_characters import DummyCharactersDialog, SelectedRemoldingCharacterDialog
from ..dialogs.remolding_scoring import OptionOverrideDialog, ScoreConfigDialog
from ..dialogs.remolding_subset import SubsetAllocationDialog
from ..dialogs.remolding_bulk import RemoldingBulkSettingsDialog
from ..dialogs.remolding_profiles import TargetProfileDialog
from ..grouped_dolls import ElementGroupedDollView
from ..images import PortraitLoader
from ..theme import ELEMENT_ORDER, FACTOR_ORDER
from ..jobs.revision import result_is_current
from ..jobs.remolding_optimizer import (
    allocate_owned_remoldings,
    best_remolding_set,
    score_owned_remoldings,
)
from ..models import (
    DataTableModel,
    DollListModel,
    TABLE_ROW_ROLE,
)
from ..widgets import (
    BusyButton,
    PortraitLabel,
    ResultDialog,
    configure_table_view,
    page_layout,
    show_error,
)
from ..workers import CancellableWorkerHandle, run_cancellable_worker
from .base import DeferredRefreshPage


class RemoldingOptimizerPage(DeferredRefreshPage):
    OWNED_SCORE_LIMIT = 300
    OWNED_SCORE_CACHE_SIZE = 8

    def __init__(
        self,
        repo: Repository,
        catalog: OwnedDollCatalog,
        portraits: PortraitLoader,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog
        self.portraits = portraits
        self.pool = QThreadPool.globalInstance()
        self.category_store = DollCategoryStore(repo.path.parent)

        self._selected_key: str | None = None
        self._selected_entry: dict[str, Any] | None = None
        self._selected_portrait_path = ""
        self._refresh_token: tuple[int, int] | None = None
        self._svc_token: tuple[int, int] | None = None
        self._svc_cache: RemoldingRecommendationService | None = None
        self._detail_serial = 0
        self._detail_render_token: tuple[str, tuple[int, int], int | None] | None = None
        self._owned_piece_token: tuple[int, int] | None = None
        self._owned_piece_snapshot: list[dict[str, Any]] | None = None
        self._owned_score_cache: OrderedDict[
            tuple[tuple[int, int], str], list[dict[str, Any]]
        ] = OrderedDict()
        self._owned_job: CancellableWorkerHandle | None = None
        self._best_job: CancellableWorkerHandle | None = None
        self._all_job: CancellableWorkerHandle | None = None
        self._applied_calculation_level = 60

        root = page_layout(self, "리몰딩 최적화")
        self._build_filters(root)
        self._build_actions(root)
        self._build_content(root)
        self._connect_signals()
        self.portraits.imageReady.connect(self._selected_image_ready)

    def _build_filters(self, root) -> None:
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("인형 검색")
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 1)

        rules = reference.remolding_rules()
        factor_names = dict(rules.get("factor_names") or {})
        element_names = dict(rules.get("element_names") or {})

        self.factor_filter = QComboBox()
        self.factor_filter.addItem("계열 전체", "")
        for key in FACTOR_ORDER:
            self.factor_filter.addItem(str(factor_names.get(key, key)), key)
        filters.addWidget(self.factor_filter)

        self.element_filter = QComboBox()
        self.element_filter.addItem("속성 전체", "")
        for key in ELEMENT_ORDER:
            self.element_filter.addItem(str(element_names.get(key, key)), key)
        filters.addWidget(self.element_filter)

        self.category_filter = QComboBox()
        self.category_filter.addItem("카테고리 전체", "")
        filters.addWidget(self.category_filter)

        filters.addSpacing(8)
        filters.addWidget(QLabel("전역 계산 레벨"))
        self.calculation_level = QSpinBox()
        self.calculation_level.setRange(1, 60)
        self.calculation_level.setPrefix("Lv.")
        self.calculation_level.setValue(60)
        self.calculation_level.setMinimumWidth(110)
        self.calculation_level.setToolTip(
            "리몰딩 계산용 전역 레벨입니다. 기본값은 Lv.60이며 인형별 개별 계산 레벨이 우선합니다. "
            "dolls.csv의 level은 인형 자체 레벨이므로 리몰딩 계산 기준으로 사용하지 않습니다."
        )
        filters.addWidget(self.calculation_level)
        self.calculation_level_apply = QPushButton("적용")
        self.calculation_level_apply.setEnabled(False)
        self.calculation_level_apply.setToolTip("전역 계산 레벨 변경을 확정합니다. 숫자를 변경한 것만으로는 계산 결과가 바뀌지 않습니다.")
        filters.addWidget(self.calculation_level_apply)
        root.addLayout(filters)

        category_actions = QHBoxLayout()
        category_label = QLabel("선택 인형 카테고리")
        category_label.setObjectName("Muted")
        category_actions.addWidget(category_label)
        self.category_add = QPushButton("카테고리 지정")
        self.category_remove = QPushButton("카테고리에서 제거")
        self.category_add.setToolTip(
            "현재 선택한 인형을 기존 또는 새 사용자 카테고리에 추가합니다."
        )
        self.category_remove.setToolTip(
            "선택한 인형을 현재 카테고리에서 제거합니다. 빈 카테고리는 자동으로 삭제됩니다."
        )
        category_actions.addWidget(self.category_add)
        category_actions.addWidget(self.category_remove)
        category_actions.addStretch(1)
        root.addLayout(category_actions)

    def _build_actions(self, root) -> None:
        primary = QHBoxLayout()
        self.best_btn = BusyButton("6개 추천")
        self.best_btn.setObjectName("AccentButton")
        self.best_btn.setToolTip("선택 인형의 장착칸에 맞춰 현재 보유 리몰딩 중 최고 조합을 계산합니다.")
        self.subset_btn = QPushButton("선택 자동 배치")
        self.subset_btn.setToolTip("여러 캐릭터를 골라 리몰딩을 중복 없이 배치합니다.")
        self.all_btn = BusyButton("전체 자동 배치")
        self.all_btn.setToolTip("보유 인형 전체에 리몰딩을 중복 없이 배분합니다.")
        self.calculation_method_btn = QPushButton("계산 방식")
        self.calculation_method_btn.setToolTip("추천 점수, 현상 요구치와 자동 배치 과정을 새 창에서 자세히 설명합니다.")
        for button in (self.best_btn, self.subset_btn, self.all_btn):
            primary.addWidget(button)
        primary.addSpacing(12)
        primary.addWidget(self.calculation_method_btn)
        primary.addStretch(1)
        root.addLayout(primary)

        panel = QFrame()
        panel.setObjectName("PanelAlt")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("선택 인형 설정")
        title.setObjectName("SectionTitle")
        heading.addWidget(title)
        hint = QLabel("개별 설정과 그룹 일괄 설정을 여기서 관리합니다.")
        hint.setObjectName("Muted")
        heading.addWidget(hint)
        heading.addStretch(1)
        panel_layout.addLayout(heading)

        settings = QHBoxLayout()
        self.target_btn = QPushButton("목표 스탯")
        self.character_btn = QPushButton("계산 레벨 · 장착칸")
        self.score_btn = QPushButton("평가 기준")
        self.bulk_settings_btn = QPushButton("카테고리 / 역할 일괄 설정…")
        self.score_btn.setToolTip("전역 평가식과 선택 인형의 옵션별 보정을 편집합니다.")
        self.character_btn.setToolTip("선택 인형의 개별 계산 레벨과 6개 장착칸 구성을 한 창에서 설정합니다.")
        self.bulk_settings_btn.setToolTip("사용자 카테고리 또는 센티넬·뱅가드·불워크·서포트 단위로 설정을 일괄 적용합니다.")
        for button in (self.target_btn, self.character_btn, self.score_btn, self.bulk_settings_btn):
            settings.addWidget(button)
        settings.addStretch(1)
        panel_layout.addLayout(settings)

        share = QHBoxLayout()
        share_title = QLabel("추천값 공유")
        share_title.setObjectName("Muted")
        share.addWidget(share_title)
        self.import_targets_btn = QPushButton("불러오기")
        self.export_targets_btn = QPushButton("내보내기")
        self.advanced_character_btn = QPushButton("더미 · 고급 관리…")
        self.import_targets_btn.setToolTip("다른 사용자가 내보낸 전체 캐릭터 목표 스탯을 불러옵니다.")
        self.export_targets_btn.setToolTip("전체 기본 캐릭터의 현재 목표 스탯을 공유용 JSON으로 저장합니다.")
        self.advanced_character_btn.setToolTip("가상 조건 시험용 더미 인형과 고급 캐릭터 프로필을 관리합니다.")
        share.addWidget(self.import_targets_btn)
        share.addWidget(self.export_targets_btn)
        share.addStretch(1)
        share.addWidget(self.advanced_character_btn)
        panel_layout.addLayout(share)
        root.addWidget(panel)

    def _build_content(self, root) -> None:
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        self.model = DollListModel(portraits=self.portraits)
        element_names = reference.remolding_rules().get("element_names", {})
        self.groups = ElementGroupedDollView(
            self.model,
            element_names,
            self,
            card_size=QSize(138, 166),
            grid_size=QSize(142, 170),
            show_favorite=False,
            text_scale=1.10,
        )
        split.addWidget(self.groups)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self._build_detail_header(detail_layout)
        self.detail_tabs = QTabWidget()
        detail_layout.addWidget(self.detail_tabs, 1)
        self._build_recommendations(self.detail_tabs)
        self._build_owned_scores(self.detail_tabs)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 500])

    def _build_detail_header(self, layout) -> None:
        # Make the current selection impossible to miss: the unused right-side
        # square is now a large portrait preview instead of relying on a thin
        # selection border in the roster.
        self.selection_panel = QFrame()
        self.selection_panel.setObjectName("SelectedDollPanel")
        header = QHBoxLayout(self.selection_panel)
        header.setContentsMargins(12, 10, 12, 10)
        header.setSpacing(14)

        text = QVBoxLayout()
        badge = QLabel("현재 선택")
        badge.setObjectName("AccentText")
        text.addWidget(badge)
        self.info = QLabel("인형을 선택하세요.")
        self.info.setObjectName("SectionTitle")
        self.info.setWordWrap(True)
        text.addWidget(self.info)
        self.target_summary = QLabel("")
        self.target_summary.setObjectName("Muted")
        self.target_summary.setWordWrap(True)
        text.addWidget(self.target_summary)
        text.addStretch(1)
        header.addLayout(text, 1)

        self.selected_portrait = PortraitLabel()
        self.selected_portrait.setFixedSize(168, 168)
        self.selected_portrait.setToolTip("현재 선택된 인형")
        header.addWidget(self.selected_portrait, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.selection_panel)

    def _build_recommendations(self, tabs: QTabWidget) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.recommend_model = DataTableModel(
            [],
            [
                ("추천 스탯", "name"),
                ("계열", "factor_label"),
                ("점수", "score_text"),
                ("보정", "override_text"),
            ],
            self,
            sort_getters=["name", "factor_label", "score", "override_text"],
        )
        self.recommend = QTableView()
        self.recommend.setModel(self.recommend_model)
        self.recommend.setToolTip("행을 더블클릭하면 선택 스탯의 평가 보정을 편집합니다.")
        configure_table_view(self.recommend, widths={0: 150, 1: 90, 2: 82})
        layout.addWidget(self.recommend, 1)
        tabs.addTab(page, "추천 스탯")

    def _build_owned_scores(self, tabs: QTabWidget) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.owned_title = QLabel("보유 리몰딩 점수")
        self.owned_title.setObjectName("Muted")
        layout.addWidget(self.owned_title)

        self.owned_model = DataTableModel(
            [],
            [("UID", "uid"), ("점수", "score_text"), ("속성", "attributes")],
            self,
            sort_getters=["uid", "score", "attributes"],
        )
        self.owned = QTableView()
        self.owned.setModel(self.owned_model)
        configure_table_view(self.owned, widths={0: 170, 1: 90})
        layout.addWidget(self.owned, 1)
        tabs.addTab(page, "보유 리몰딩 점수")

    def _calculation_level(self) -> int:
        return max(1, min(60, int(self._applied_calculation_level)))

    def _calculation_level_pending(self) -> None:
        pending = max(1, min(60, int(self.calculation_level.value())))
        self.calculation_level_apply.setEnabled(pending != self._calculation_level())

    def _apply_calculation_level(self) -> None:
        self._applied_calculation_level = max(1, min(60, int(self.calculation_level.value())))
        self.calculation_level_apply.setEnabled(False)
        self._level_changed()

    def _resolved_level(self, svc: RemoldingRecommendationService, key: str) -> int:
        resolver = DollCharacterResolver(self.repo, recommendation=svc)
        return resolver.calculation_level_for_key(key, self._calculation_level())

    def _level_changed(self) -> None:
        self._detail_render_token = None
        if self._selected_key:
            self._refresh_detail()

    def _calculation_method_html(
        self,
        svc: RemoldingRecommendationService,
        key: str,
        factor_names: dict[str, Any],
    ) -> str:
        character = svc.get_character(key)
        requirements = svc.phenomenon_requirements(key)
        rules = reference.remolding_rules()
        stage_order = list(rules.get("phenomenon_stage_order") or [])
        level_requirements = {
            str(stage): int(level)
            for stage, level in (rules.get("phenomenon_level_requirements") or {}).items()
        }
        selected_level = self._resolved_level(svc, key)
        selected_stage = svc.phenomenon_stage_for_level(selected_level)
        score_config = svc.get_score_config()
        grades = score_config.get("grades", {})
        multipliers = score_config.get("multipliers", {})
        grade_text = " · ".join(f"{rank}={int(value)}" for rank, value in grades.items())
        multiplier_text = " · ".join(
            f"{label}×{float(multipliers.get(key, 1.0)):g}"
            for key, label in (
                ("option_weight", "옵션 가중치"),
                ("base_rank", "기본 등급"),
                ("tag_rank", "태그 등급"),
            )
        )
        rows = []
        for stage in stage_order:
            req = requirements.get(stage, {})
            detail = " · ".join(
                f"{factor_names.get(factor, factor)} {int(value)}"
                for factor, value in req.items()
            ) or "요구치 없음"
            marker = " ← 현재 계산" if stage == selected_stage else ""
            rows.append(
                f"<tr><td><b>{stage}</b>{marker}</td>"
                f"<td>Lv.{int(level_requirements.get(stage, 0))}</td>"
                f"<td>{detail}</td></tr>"
            )
        name = str(character.get("nameKR") or key)
        return f"""
            <h2>리몰딩 계산 방식</h2>
            <p><b>현재 계산:</b> {name} · Lv.{selected_level} · 현상 목표 <b>{selected_stage}</b></p>
            <h3>1. 리몰딩 옵션 레벨</h3>
            <p>각 옵션 코드는 +1 / +2 / +3 레벨을 기여합니다. 같은 논리 옵션의 기여를 6개 장비에서 합산합니다.</p>
            <pre>raw level = Σ(각 장착 옵션의 레벨 기여)
표시 level = min(raw level, 옵션 maxLevel)
overcap = max(0, raw level - maxLevel)</pre>
            <h3>2. 추천 점수</h3>
            <p>이 점수는 게임 내부의 공식 전투력 수치가 아니라, 추천 reference의 weight·등급·캐릭터 태그를 한 값으로 비교하기 위한 <b>설명 가능한 플래너 점수</b>입니다.</p>
            <pre>옵션 점수 =
  weight × 옵션 가중치 배수
+ baseRank 등급점수 × 기본 등급 배수
+ Σ(캐릭터 tag에서 이 옵션이 받은 등급점수 × 태그 등급 배수)
+ 캐릭터별 사용자 조정값

리몰딩 1개의 점수 = Σ(장착 가능한 슬롯 옵션 점수)</pre>
            <p><b>현재 등급점수:</b> {grade_text}</p>
            <p><b>현재 배수:</b> {multiplier_text}</p>
            <p>주옵션 계열이 해당 인형의 장착 계열에 없거나, 속성 전용 옵션의 속성이 인형과 다르거나, 사용자가 제외한 옵션은 추천 후보에서 제외합니다. 부옵션은 실제 보유 리몰딩에서 다른 계열 조합이 가능하므로 주옵션처럼 계열만으로 버리지 않습니다.</p>
            <h3>3. 레벨별 현상 요구치</h3>
            <p>계산 레벨은 Lv.0 / 10 / 20 / 30 / 45 / 60 여섯 구간을 사용합니다. 각 구간에서 해당 현상 단계의 센티널·뱅가드·불워크·서포트 요구치를 목표로 계산합니다.</p>
            <table cellspacing="0" cellpadding="6" border="1">
              <tr><th>현상</th><th>레벨</th><th>{name} 요구치</th></tr>
              {''.join(rows)}
            </table>
            <p>현상 계열 수치는 6개 리몰딩의 모든 옵션에서 센티널·뱅가드·불워크·서포트 기여 Lv.를 각각 합산합니다.</p>
            <pre>계열 누적 Lv = Σ(해당 계열 옵션의 +1/+2/+3 기여)
단계 충족 = 캐릭터 레벨 ≥ 단계 최소 레벨 AND 모든 계열 누적 Lv ≥ 단계 요구치</pre>
            <h3>4. 자동 배치 우선순위</h3>
            <ol>
              <li><b>현상:</b> 선택한 Lv.0/10/20/30/45/60에 해당하는 단계의 부족 수치를 먼저 줄입니다.</li>
              <li><b>목표 옵션:</b> 사용자가 지정한 목표 Lv.를 우선순위 그룹 순서대로 최대한 충족합니다.</li>
              <li><b>추천 점수:</b> 위 조건이 같은 후보끼리는 합산 추천 점수가 높은 배치를 선택합니다.</li>
              <li><b>물리 제약:</b> 같은 리몰딩 UID는 한 번만 사용할 수 있고, 캐릭터별 계열 장착칸 수를 넘지 않습니다.</li>
            </ol>
            <p>즉 추천 점수가 조금 더 높더라도 현상 단계나 더 높은 우선순위 목표를 깨뜨리는 조합은 선택하지 않습니다.</p>
            """


    def _open_calculation_method(self) -> None:
        key = str(self._selected_key or "")
        if not key:
            QMessageBox.information(self, "계산 방식", "인형을 선택하면 현재 인형의 레벨별 요구치까지 함께 설명합니다.")
            return
        svc = self._service(self.repo.state_token())
        factor_names = reference.remolding_rules().get("factor_names", {})
        dialog = QDialog(self)
        dialog.setWindowTitle("리몰딩 최적화 · 계산 방식")
        dialog.resize(920, 760)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(self._calculation_method_html(svc, key, factor_names))
        layout.addWidget(browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("닫기")
        close.clicked.connect(dialog.accept)
        row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()

    def _connect_signals(self) -> None:
        self.search.textChanged.connect(self._filter)
        self.factor_filter.currentIndexChanged.connect(self._filter)
        self.element_filter.currentIndexChanged.connect(self._filter)
        self.category_filter.currentIndexChanged.connect(self._filter)
        self.calculation_level.valueChanged.connect(self._calculation_level_pending)
        self.calculation_level_apply.clicked.connect(self._apply_calculation_level)
        self.category_add.clicked.connect(self._assign_selected_category)
        self.category_remove.clicked.connect(self._remove_selected_from_category)
        self.groups.entrySelected.connect(self._selected)
        self.best_btn.clicked.connect(self._best)
        self.all_btn.clicked.connect(self._all)
        self.calculation_method_btn.clicked.connect(self._open_calculation_method)
        self.target_btn.clicked.connect(self._edit_targets)
        self.score_btn.clicked.connect(self._edit_scoring)
        self.subset_btn.clicked.connect(self._subset)
        self.character_btn.clicked.connect(self._character_profiles)
        self.bulk_settings_btn.clicked.connect(self._bulk_settings)
        self.import_targets_btn.clicked.connect(self._import_target_profiles)
        self.export_targets_btn.clicked.connect(self._export_target_profiles)
        self.advanced_character_btn.clicked.connect(self._advanced_character_profiles)
        self.recommend.doubleClicked.connect(
            lambda _index: self._edit_selected_override()
        )

    def on_deactivated(self) -> None:
        # Owned-piece scoring is useful only while this detail is visible.
        for attr in ("_owned_job", "_best_job", "_all_job"):
            handle = getattr(self, attr, None)
            if handle is not None:
                handle.cancel()
                setattr(self, attr, None)
        self._detail_serial += 1
        self._detail_render_token = None

    def invalidate_cache(self) -> None:
        self._refresh_token = None
        self._svc_token = None
        self._svc_cache = None
        self._detail_render_token = None
        self._owned_piece_token = None
        self._owned_piece_snapshot = None
        self._owned_score_cache.clear()
        self.request_refresh()

    def _filter(self) -> None:
        category = str(self.category_filter.currentData() or "").strip()
        allowed = self.category_store.keys(category) if category else None
        self.groups.set_filters(
            query=self.search.text(),
            factor=str(self.factor_filter.currentData() or ""),
            visible_element=str(self.element_filter.currentData() or ""),
            favorites_only=False,
            allowed_character_keys=allowed,
        )
        self.category_remove.setEnabled(bool(category))

    def _refresh_category_filter(self) -> None:
        if not hasattr(self, "category_filter"):
            return
        current = str(self.category_filter.currentData() or "")
        names = self.category_store.names()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("카테고리 전체", "")
        for name in names:
            self.category_filter.addItem(f"{name} ({len(self.category_store.keys(name))})", name)
        index = self.category_filter.findData(current)
        self.category_filter.setCurrentIndex(index if index >= 0 else 0)
        self.category_filter.blockSignals(False)
        self.category_remove.setEnabled(bool(self.category_filter.currentData()))

    def _assign_selected_category(self) -> None:
        key = str(self._selected_key or "")
        if not key:
            QMessageBox.information(self, "인형 카테고리", "먼저 인형을 선택하세요.")
            return
        dialog = DollCategoryAssignDialog(self.category_store, count=1, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.result_name
        try:
            self.category_store.assign(name, [key])
        except Exception as exc:
            show_error(self, "인형 카테고리 저장 실패", exc)
            return
        self._refresh_category_filter()
        self._filter()

    def _remove_selected_from_category(self) -> None:
        key = str(self._selected_key or "")
        category = str(self.category_filter.currentData() or "").strip()
        if not key or not category:
            return
        try:
            self.category_store.remove(category, [key])
        except Exception as exc:
            show_error(self, "인형 카테고리 저장 실패", exc)
            return
        self._refresh_category_filter()
        self._filter()

    def _prepare_entries(self):
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in self.catalog.entries_with_portraits():
            if not raw.get("character_key"):
                continue
            entry = dict(raw)
            portrait = entry.get("portrait_path")
            entry["portrait_path"] = str(portrait) if portrait else ""
            entry["sort_key"] = (0, str(entry.get("name") or "").casefold(), int(entry.get("doll_id") or 0))
            entries.append(entry)
            seen.add(str(entry.get("character_key")))

        # Calculator-only dummy characters share the same roster, but do not
        # have a physical doll id, portrait, or favorite state.
        svc = self._service()
        factor_names = reference.remolding_rules().get("factor_names", {})
        element_names = reference.remolding_rules().get("element_names", {})
        for character in svc.list_dummy_characters():
            key = str(character.get("key") or "")
            if not key or key in seen:
                continue
            name = str(character.get("nameKR") or key) + " [더미]"
            factor_label = str(
                factor_names.get(character.get("dollType"), character.get("dollType") or "")
            )
            element_label = str(
                element_names.get(
                    character.get("elementType"), character.get("elementType") or ""
                )
            )
            entries.append(
                {
                    "row": {},
                    "doll_id": None,
                    "name": name,
                    "character_key": key,
                    "character": character,
                    "factor_type": str(character.get("dollType") or ""),
                    "factor_label": factor_label,
                    "element_type": str(character.get("elementType") or ""),
                    "element_label": element_label,
                    "favorite": False,
                    "portrait_path": "",
                    "search_text": f"{name} {factor_label} {element_label} {key}".casefold(),
                    "sort_key": (1, name.casefold(), 0),
                }
            )
        return entries

    def _service(self, token: tuple[int, int] | None = None) -> RemoldingRecommendationService:
        state_token = self.repo.state_token() if token is None else tuple(token)
        if self._svc_cache is None or self._svc_token != state_token:
            self._svc_cache = RemoldingRecommendationService(self.repo)
            self._svc_token = state_token
        return self._svc_cache

    def refresh(self) -> None:
        self._refresh_category_filter()
        token = self.repo.state_token()
        previous_key = self._selected_key
        if token != self._refresh_token:
            self.model.set_entries(self._prepare_entries())
            self._filter()
            self._refresh_token = token
            if previous_key:
                self._restore_character_selection(previous_key)

        if self._selected_key:
            self._refresh_detail()
        else:
            self.groups.select_first()

    def _restore_character_selection(self, character_key: str) -> None:
        self.groups.select_by("character_key", character_key)

    def _selected(self, entry: dict[str, Any]) -> None:
        key = str(entry.get("character_key") or "")
        if not key:
            return
        self._selected_key = key
        self._selected_entry = dict(entry)
        self._render_selected_portrait()
        self._refresh_detail()

    def _render_selected_portrait(self) -> None:
        entry = self._selected_entry or {}
        path = str(entry.get("portrait_path") or "")
        self._selected_portrait_path = path
        image = self.portraits.get(path) if path else None
        self.selected_portrait.set_image(image, 164, 164)
        if path and image is None:
            self.portraits.request(path)

    def _selected_image_ready(self, path: str, image) -> None:
        if str(path) == self._selected_portrait_path:
            self.selected_portrait.set_image(image, 164, 164)

    def _refresh_detail(self):
        key = self._selected_key
        if not key:
            return
        detail_token = (key, self.repo.state_token(), self._calculation_level())
        if detail_token == self._detail_render_token:
            return

        svc = self._service(detail_token[1])
        character = svc.get_character(key)
        factor_names = reference.remolding_rules().get("factor_names", {})
        element_names = reference.remolding_rules().get("element_names", {})
        self._render_character_header(character, factor_names, element_names)
        self._render_target_summary(svc, key)
        self._render_recommendations(svc, key, factor_names)

        # Commit only after all synchronous detail queries succeeded. A failed
        # read must remain retryable for the same DB revision.
        self._detail_render_token = detail_token
        self._schedule_owned_scores(key, detail_token[1])

    def _render_character_header(
        self,
        character: dict[str, Any],
        factor_names: dict[str, Any],
        element_names: dict[str, Any],
    ) -> None:
        distribution = " · ".join(
            f"{factor_names.get(item['factorType'], item['factorType'])} {item['count']}"
            for item in character.get("slotDistribution", [])
        )
        self.info.setText(
            f"{character.get('nameKR')} · "
            f"{factor_names.get(character.get('dollType'), character.get('dollType'))} · "
            f"{element_names.get(character.get('elementType'), character.get('elementType'))} · "
            f"{distribution}"
        )

    def _render_target_summary(self, svc: RemoldingRecommendationService, key: str) -> None:
        targets = svc.get_target_profile(key)
        options = reference.remolding_options()
        chunks = [
            f"{options.get(option_key, {}).get('nameKR', option_key)} "
            f"Lv.{int(spec.get('level') or 0)}"
            for option_key, spec in targets.items()
        ]
        self.target_summary.setText(
            "추천 목표 · " + (" · ".join(chunks) if chunks else "사용자 목표 없음")
        )

    def _render_recommendations(
        self,
        svc: RemoldingRecommendationService,
        key: str,
        factor_names: dict[str, Any],
    ) -> None:
        prepared: list[dict[str, Any]] = []
        for row in svc.recommendations(key):
            override = row.get("override") or {}
            adjustment = int(override.get("score_adjustment") or 0)
            state = override.get("state")
            note = "제외" if state == "exclude" else (f"{adjustment:+d}" if adjustment else "—")
            prepared.append(
                {
                    "name": str(row.get("name") or ""),
                    "factor_label": str(
                        factor_names.get(row.get("factorType"), row.get("factorType"))
                    ),
                    "score": float(row.get("score") or 0),
                    "score_text": str(row.get("score")),
                    "override_text": note,
                    "option_key": str(row.get("optionKey") or ""),
                    "payload": dict(row),
                }
            )
        self.recommend_model.set_rows(prepared)

    def _schedule_owned_scores(
        self,
        key: str,
        state_token: tuple[int, int],
    ) -> None:
        if self._owned_piece_token is not None and self._owned_piece_token != state_token:
            self._owned_piece_token = None
            self._owned_piece_snapshot = None
            self._owned_score_cache.clear()

        if self._owned_job is not None:
            self._owned_job.cancel()
            self._owned_job = None

        self._detail_serial += 1
        serial = self._detail_serial
        cache_key = (state_token, key)
        cached = self._owned_score_cache.get(cache_key)
        if cached is not None:
            self._owned_score_cache.move_to_end(cache_key)
            self._render_owned_scores(cached)
            return

        self.owned_title.setText("보유 리몰딩 점수 · 계산 중…")
        self.owned_model.set_rows([])
        QTimer.singleShot(
            70,
            lambda k=key, s=serial, t=state_token: self._start_owned_score(k, s, t),
        )

    def _start_owned_score(
        self,
        key: str,
        serial: int,
        state_token: tuple[int, int],
    ) -> None:
        if serial != self._detail_serial or key != self._selected_key:
            return
        db = str(self.repo.path)
        snapshot = (
            self._owned_piece_snapshot
            if self._owned_piece_token == state_token
            else None
        )
        self._owned_job = run_cancellable_worker(
            self.pool,
            lambda should_cancel: score_owned_remoldings(
                db,
                key,
                serial=serial,
                state_token=state_token,
                snapshot=snapshot,
                should_cancel=should_cancel,
            ),
            on_result=self._owned_score_ready,
            on_error=lambda error, k=key, s=serial: self._owned_score_failed(
                k, s, error
            ),
        )

    def _owned_score_ready(self, payload) -> None:
        key, serial, state_token, pieces, rows, cancelled = payload
        if int(serial) == self._detail_serial:
            self._owned_job = None

        # Superseded jobs may contain partial rows. Never cache them.
        if cancelled or tuple(state_token) != self.repo.state_token():
            return

        if self._owned_piece_token != state_token:
            self._owned_piece_token = state_token
            self._owned_piece_snapshot = list(pieces or [])
            self._owned_score_cache = OrderedDict(
                (cache_key, value)
                for cache_key, value in self._owned_score_cache.items()
                if cache_key[0] == state_token
            )

        cache_key = (state_token, str(key))
        self._owned_score_cache[cache_key] = list(rows or [])
        self._owned_score_cache.move_to_end(cache_key)
        while len(self._owned_score_cache) > self.OWNED_SCORE_CACHE_SIZE:
            self._owned_score_cache.popitem(last=False)

        if int(serial) != self._detail_serial or str(key) != self._selected_key:
            return
        self._render_owned_scores(rows)

    def _render_owned_scores(self, rows) -> None:
        options = reference.remolding_options()
        prepared: list[dict[str, Any]] = []
        for row in list(rows or [])[: self.OWNED_SCORE_LIMIT]:
            attributes: list[str] = []
            for slot in row.get("slots", []):
                meta = options.get(str(slot.get("option_key") or ""), {})
                name = meta.get("nameKR") or slot.get("name") or "옵션"
                level = int(
                    slot.get("level_contribution") or slot.get("variant") or 0
                )
                attributes.append(str(name) + (f" +{level}" if level else ""))
            score = float(row.get("score") or 0)
            prepared.append(
                {
                    "uid": str(row.get("uid") or ""),
                    "score": score,
                    "score_text": f"{score:,.0f}",
                    "attributes": " / ".join(attributes),
                    "payload": dict(row),
                }
            )

        total = len(rows or [])
        self.owned_model.set_rows(prepared)
        if total <= self.OWNED_SCORE_LIMIT:
            label = f"보유 리몰딩 점수 · {total:,}개"
        else:
            label = (
                f"보유 리몰딩 점수 · {total:,}개 중 "
                f"{self.OWNED_SCORE_LIMIT}개 표시"
            )
        self.owned_title.setText(label)

    def _owned_score_failed(self, key: str, serial: int, _error: str) -> None:
        if serial == self._detail_serial:
            self._owned_job = None
        if serial == self._detail_serial and key == self._selected_key:
            self.owned_title.setText("보유 리몰딩 점수 · 계산 실패")

    def _best(self) -> None:
        key = self._selected_key
        if not key:
            return
        self.best_btn.set_busy(True, "계산 중…")
        db = str(self.repo.path)
        request_token = self.repo.state_token()
        self._best_job = run_cancellable_worker(
            self.pool,
            lambda should_cancel: best_remolding_set(
                db,
                key,
                request_token,
                character_level_override=self._calculation_level(),
                should_cancel=should_cancel,
            ),
            on_result=self._best_ready,
            on_error=lambda error: show_error(self, "계산 실패", error),
            on_finished=self._best_finished,
        )

    def _best_finished(self) -> None:
        self._best_job = None
        self.best_btn.set_busy(False)

    def _best_ready(self, payload) -> None:
        request_token, worker_start, worker_end, result = payload
        if not result_is_current(
            request_token,
            worker_start,
            worker_end,
            self.repo.state_token(),
        ):
            if self.page_active:
                QMessageBox.information(
                    self,
                    "계산 결과 갱신",
                    "계산 중 보유 데이터가 변경되어 결과를 폐기했습니다. 다시 계산하세요.",
                )
            return
        self._show_best(result)

    def _show_best(self, result) -> None:
        dialog = ResultDialog("리몰딩 6개 추천", self)
        factor_names = reference.remolding_rules().get("factor_names", {})
        key = str(result.get("character_key") or self._selected_key or "")
        pieces = list(result.get("pieces") or [])
        svc = self._service()
        level = int(
            result.get("character_level")
            if result.get("character_level") is not None
            else self._resolved_level(svc, key)
        )
        phenomenon = (
            svc.phenomenon_status(key, pieces, character_level=level) if key else {}
        )
        targets = svc.get_target_profile(key) if key else {}
        target_status = svc.target_status(pieces, targets) if key else []

        dialog.add_title(
            str((result.get("character") or {}).get("nameKR") or "추천 결과")
        )
        dialog.add_remolding_result(result, factor_names, reference.remolding_options())
        dialog.add_stats_comparison(
            phenomenon_status=phenomenon,
            aggregate_levels=result.get("aggregate_levels")
            or svc.aggregate_option_levels(pieces),
            target_status=target_status,
            factor_names=factor_names,
        )
        dialog.exec()

    def _all(self) -> None:
        keys = self._owned_character_keys()
        if not keys:
            return
        self.all_btn.set_busy(True, "계산 중…")
        db = str(self.repo.path)
        request_token = self.repo.state_token()
        self._all_job = run_cancellable_worker(
            self.pool,
            lambda should_cancel: allocate_owned_remoldings(
                db,
                keys,
                request_token,
                should_cancel,
                character_level_override=self._calculation_level(),
            ),
            on_result=self._all_ready,
            on_error=lambda error: show_error(self, "계산 실패", error),
            on_finished=self._all_finished,
        )

    def _owned_character_keys(self) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()
        for entry in self.catalog.entries():
            key = str(entry.get("character_key") or "")
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    def _all_finished(self) -> None:
        self._all_job = None
        self.all_btn.set_busy(False)

    def _all_ready(self, payload) -> None:
        request_token, worker_start, worker_end, result = payload
        if not result_is_current(
            request_token,
            worker_start,
            worker_end,
            self.repo.state_token(),
        ):
            if self.page_active:
                QMessageBox.information(
                    self,
                    "자동 배치 결과 갱신",
                    "계산 중 보유 데이터가 변경되어 결과를 폐기했습니다. 다시 계산하세요.",
                )
            return
        self._show_all(result)

    def _show_all(self, result) -> None:
        dialog = ResultDialog("전체 리몰딩 자동 배치", self)
        dialog.add_title(
            f"{result.get('characters', 0)}명 · "
            f"총점 {float(result.get('total_score') or 0):,.0f}",
            f"미배치 슬롯 {result.get('missing_slots', 0)}",
        )

        table = QTableView()
        table.setToolTip(
            "행을 더블클릭하면 장착 리몰딩과 목표 / 현재 전체 스탯을 확인합니다."
        )
        rows = list(result.get("rows") or [])
        prepared = [self._allocation_summary_row(index, row) for index, row in enumerate(rows)]
        model = DataTableModel(
            prepared,
            [
                ("인형", "name"),
                ("배치", "allocation"),
                ("점수", "score_text"),
                ("목표", "target"),
            ],
            dialog,
            sort_getters=["name", "allocation", "score", "target"],
        )
        table.setModel(model)
        configure_table_view(table)
        table.doubleClicked.connect(
            lambda index: self._show_all_row(result, index)
        )
        dialog.host.addWidget(table)
        dialog.exec()

    @staticmethod
    def _allocation_summary_row(index: int, row: dict[str, Any]) -> dict[str, Any]:
        pieces = list(row.get("pieces") or [])
        missing = int(row.get("missing") or 0)
        score = float(row.get("score") or 0)
        return {
            "row_index": index,
            "name": str(
                (row.get("character") or {}).get("nameKR")
                or row.get("character_key")
                or ""
            ),
            "allocation": f"{len(pieces)}/{len(pieces) + missing}",
            "score": score,
            "score_text": f"{score:,.0f}",
            "target": (
                f"{int(row.get('targets_met') or 0)}/"
                f"{len(row.get('target_status') or [])}"
            ),
        }

    def _show_all_row(self, result, index) -> None:
        if not index.isValid():
            return
        payload = index.model().index(index.row(), 0).data(TABLE_ROW_ROLE) or {}
        row_index = payload.get("row_index")
        rows = list(result.get("rows") or [])
        if row_index is None or int(row_index) >= len(rows):
            return

        row = rows[int(row_index)]
        factor_names = reference.remolding_rules().get("factor_names", {})
        name = str(
            (row.get("character") or {}).get("nameKR")
            or row.get("character_key")
            or "배치 결과"
        )
        dialog = ResultDialog(f"{name} · 리몰딩 결과", self)
        dialog.add_title(name)
        dialog.add_remolding_result(row, factor_names, reference.remolding_options())
        dialog.add_stats_comparison(
            phenomenon_status=row.get("phenomenon_status"),
            aggregate_levels=row.get("aggregate_levels"),
            target_status=row.get("target_status"),
            factor_names=factor_names,
        )
        dialog.exec()

    def _edit_targets(self) -> None:
        if not self._selected_key:
            return
        if TargetProfileDialog(self.repo, self._selected_key, self).exec():
            self._invalidate_after_settings_change()

    def _edit_selected_override(self) -> None:
        if not self._selected_key:
            return
        index = self.recommend.currentIndex()
        if not index.isValid():
            return
        row = self.recommend.model().index(index.row(), 0).data(TABLE_ROW_ROLE) or {}
        option_key = row.get("option_key")
        if option_key and OptionOverrideDialog(
            self.repo,
            self._selected_key,
            str(option_key),
            self,
        ).exec():
            self._invalidate_after_settings_change()

    def _edit_scoring(self) -> None:
        if not self._selected_key:
            return
        if ScoreConfigDialog(self.repo, self._selected_key, self).exec():
            self._invalidate_after_settings_change()

    def _bulk_settings(self) -> None:
        svc = self._service(self.repo.state_token())
        selected_key = str(self._selected_key or "")
        selected_name = ""
        if selected_key:
            try:
                selected_name = str(svc.get_character(selected_key).get("nameKR") or selected_key)
            except ValueError:
                selected_name = selected_key
        factor_names = dict(reference.remolding_rules().get("factor_names") or {})
        dialog = RemoldingBulkSettingsDialog(
            self.category_store.names(), factor_names, selected_name, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        scope_value = str(values.get("scope_value") or "")
        if not scope_value:
            QMessageBox.information(self, "일괄 설정", "적용할 대상 그룹이 없습니다.")
            return
        if values.get("scope_kind") == "role":
            keys = [
                str(entry.get("character_key") or "")
                for entry in self._prepare_entries()
                if str(entry.get("factor_type") or "") == scope_value
            ]
        else:
            keys = sorted(self.category_store.keys(scope_value))
        keys = [key for key in keys if key and svc.has_character(key)]
        if not keys:
            QMessageBox.information(self, "일괄 설정", "현재 그룹에 적용 가능한 인형이 없습니다.")
            return

        copy_targets = bool(values.get("copy_targets")) and bool(selected_key)
        copy_slots = bool(values.get("copy_slots")) and bool(selected_key)
        change_level = bool(values.get("change_level"))
        if not any((copy_targets, copy_slots, change_level)):
            QMessageBox.information(self, "일괄 설정", "변경할 항목을 하나 이상 선택하세요.")
            return
        source_targets = svc.get_target_profile(selected_key) if copy_targets else {}
        source_slots: dict[str, int] = {}
        if copy_slots:
            source_character = svc.get_character(selected_key)
            source_slots = {
                str(row.get("factorType")): int(row.get("count") or 0)
                for row in source_character.get("slotDistribution", [])
            }

        applied = 0
        skipped_targets = 0
        try:
            for key in keys:
                if change_level:
                    level = int(values.get("level") or 0)
                    svc.set_character_level_override(key, level if level > 0 else None)
                if copy_slots:
                    character = svc.get_character(key)
                    svc.save_character_profile(
                        key,
                        slot_counts=source_slots,
                        display_name=str(character.get("nameKR") or key),
                        doll_type=str(character.get("dollType") or "sentinel"),
                        element_type=str(character.get("elementType") or "physical"),
                        tags=[str(tag) for tag in character.get("tags", [])],
                        is_dummy=key.startswith("dummy_"),
                        level_override=character.get("levelOverride"),
                    )
                if copy_targets:
                    compatible = {
                        option_key: spec for option_key, spec in source_targets.items()
                        if svc.score_option(key, option_key).get("eligible")
                    }
                    skipped_targets += len(source_targets) - len(compatible)
                    svc.save_target_profile(key, compatible)
                applied += 1
        except Exception as exc:
            show_error(self, "일괄 설정 실패", exc)
            return
        self.catalog.invalidate()
        self._invalidate_after_settings_change()
        suffix = f"\n호환되지 않은 목표 항목 {skipped_targets:,}개는 제외했습니다." if skipped_targets else ""
        QMessageBox.information(self, "일괄 설정 완료", f"인형 {applied:,}명에 설정을 적용했습니다.{suffix}")

    def _subset(self) -> None:
        entries = self._prepare_entries()
        SubsetAllocationDialog(
            self.repo,
            entries,
            self.portraits,
            self,
            character_level_override=self._calculation_level(),
        ).exec()
        # The subset dialog writes the shared category file through its own
        # store instance. Refresh immediately so newly-created categories are
        # visible on this page without navigating away and back.
        self._refresh_category_filter()
        self._filter()

    def _import_target_profiles(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "리몰딩 추천값 불러오기",
            "",
            "GFL2 추천 설정 (*.json)",
        )
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "추천값 불러오기",
            "파일에 포함된 캐릭터의 현재 추천 스탯 목표를 덮어쓸까요?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = import_recommendation_profiles(self.repo, path)
        except Exception as exc:
            show_error(self, "추천값 불러오기 실패", exc)
            return
        self._invalidate_after_settings_change()
        QMessageBox.information(
            self,
            "추천값 불러오기 완료",
            f"캐릭터 {int(result.get('imported') or 0):,}명의 추천값을 적용했습니다."
            + (
                f"\n현재 버전에 없는 캐릭터 {int(result.get('skipped') or 0):,}명은 건너뛰었습니다."
                if result.get("skipped")
                else ""
            ),
        )

    def _export_target_profiles(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "리몰딩 추천값 내보내기",
            default_recommendation_profile_name(),
            "GFL2 추천 설정 (*.json)",
        )
        if not path:
            return
        try:
            count = export_recommendation_profiles(self.repo, path)
        except Exception as exc:
            show_error(self, "추천값 내보내기 실패", exc)
            return
        QMessageBox.information(
            self,
            "추천값 내보내기 완료",
            f"캐릭터 {count:,}명의 현재 추천값을 저장했습니다.",
        )

    def _character_profiles(self) -> None:
        if not self._selected_key:
            QMessageBox.information(self, "선택 인형 설정", "먼저 인형을 선택하세요.")
            return
        dialog = SelectedRemoldingCharacterDialog(self.repo, self._selected_key, self)
        dialog.exec()
        if dialog.changed:
            self.catalog.invalidate()
            self._invalidate_after_settings_change()

    def _advanced_character_profiles(self) -> None:
        dialog = DummyCharactersDialog(self.repo, self._selected_key, self)
        dialog.exec()
        if dialog.changed:
            self.catalog.invalidate()
            self._invalidate_after_settings_change()

    def _invalidate_after_settings_change(self) -> None:
        self._svc_token = None
        self._refresh_token = None
        self._detail_render_token = None
        self.refresh()
