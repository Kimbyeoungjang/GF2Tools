from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, QThreadPool, QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import reference
from ...repository import Repository
from ...services.doll_categories import DollCategoryStore
from ...services.doll_skill_cycles import DollSkillCycleStore
from ...services.formation_preferences import FormationMemberPreferenceStore, formation_cycle_candidates
from ...services.tactic_equipment import ImportedEquipmentStore, TacticEquipmentCatalog
from ...services.remolding_csv import (
    default_remoldings_csv_name,
    export_remoldings_csv,
)
from ..data import OwnedDollCatalog, remolding_meta
from ..dialogs.category_assign import DollCategoryAssignDialog
from ..dialogs.doll_skill_cycles import DollSkillCycleDialog
from ..grouped_dolls import ElementGroupedDollView
from ..grouped_remoldings import RemoldingGroupedView
from ..images import PortraitLoader
from ..models import DollListModel
from ..rich_text import game_markup_to_plain_text, game_markup_to_qt_html, set_game_rich_text
from ..theme import ELEMENT_ORDER, FACTOR_ORDER
from ..widgets import configure_tree_widget, page_layout, show_error
from ..workers import run_worker
from .base import DeferredRefreshPage


class InventoryPage(DeferredRefreshPage):
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
        self.pool = QThreadPool.globalInstance()
        self.category_store = DollCategoryStore(repo.path.parent)
        self.skill_cycle_store = DollSkillCycleStore(repo.path.parent)
        self.formation_cycle_store = FormationMemberPreferenceStore(repo.path.parent)

        self._selected_doll_id: int | None = None
        self._selected_remolding_uid: str = ""
        self._refresh_token: tuple[int, int] | None = None
        self._remolding_cache_token: tuple[int, int] | None = None
        self._remolding_cache: list[dict[str, Any]] = []
        self._remolding_loading_token: tuple[int, int] | None = None
        self._equipment_source_cache: tuple[dict[str, Any], dict[str, Any], dict[int, str]] | None = None
        self._pending_equipment_kind = ""
        self._equipment_filter_timer = QTimer(self)
        self._equipment_filter_timer.setSingleShot(True)
        self._equipment_filter_timer.setInterval(90)
        self._equipment_filter_timer.timeout.connect(self._flush_equipment_filter)

        root = page_layout(self, "보유 현황")
        self._build_toolbar(root)
        self._build_stack(root, portraits)
        self._connect_signals()

    def _build_toolbar(self, root) -> None:
        primary = QHBoxLayout()

        self.kind = QComboBox()
        self.kind.addItem("인형", "dolls")
        self.kind.addItem("리몰딩", "remoldings")
        self.kind.addItem("무기", "weapons")
        self.kind.addItem("공용키", "common_keys")
        self.kind.addItem("고유키", "fixed_keys")
        self.kind.addItem("도약키", "expansion_keys")
        primary.addWidget(self.kind)

        self.search = QLineEdit()
        self.search.setPlaceholderText("인형 이름 또는 ID 검색")
        self.search.setClearButtonEnabled(True)
        primary.addWidget(self.search, 1)

        self.refresh_btn = QPushButton("새로고침")
        primary.addWidget(self.refresh_btn)
        root.addLayout(primary)

        filters = QHBoxLayout()
        factor_names = reference.remolding_rules().get("factor_names", {})
        element_names = reference.remolding_rules().get("element_names", {})

        self.factor = QComboBox()
        self.factor.addItem("직업 전체", "")
        for key in FACTOR_ORDER:
            self.factor.addItem(str(factor_names.get(key, key)), key)

        self.element = QComboBox()
        self.element.addItem("속성 전체", "")
        for key in ELEMENT_ORDER:
            self.element.addItem(str(element_names.get(key, key)), key)

        self.category = QComboBox()
        self.category.addItem("카테고리 전체", "")
        self.category_add = QPushButton("카테고리 지정")
        self.category_remove = QPushButton("카테고리에서 제거")
        self.skill_cycles = QPushButton("스킬 사이클")
        self.skill_cycles.setEnabled(False)
        self.skill_cycles.setToolTip("선택 인형의 T1~Tn 반복 스킬 사이클을 편집합니다.")
        for control in (
            self.factor,
            self.element,
            self.category,
            self.category_add,
            self.category_remove,
            self.skill_cycles,
        ):
            filters.addWidget(control)

        self.remolding_major = QComboBox()
        self.remolding_major.addItem("주옵션 전체", "")
        for option in reference.remolding_rules().get("options", []):
            if option.get("isMajor"):
                self.remolding_major.addItem(
                    str(option.get("nameKR") or option.get("key") or "주옵션"),
                    str(option.get("key") or ""),
                )
        self.remolding_major.setVisible(False)
        filters.addWidget(self.remolding_major)

        self.remolding_factor = QComboBox()
        self.remolding_factor.addItem("클래스 전체", "")
        for key in FACTOR_ORDER:
            self.remolding_factor.addItem(str(factor_names.get(key, key)), key)
        self.remolding_factor.setVisible(False)
        filters.addWidget(self.remolding_factor)

        self.export_logger = QPushButton("리몰딩 CSV 내보내기")
        self.export_logger.setToolTip(
            "보유 리몰딩을 gfl2logger 호환 uid/stat1/stat2/stat3 CSV로 저장합니다."
        )
        filters.addWidget(self.export_logger)
        filters.addStretch(1)
        root.addLayout(filters)

    def _build_stack(self, root, portraits: PortraitLoader) -> None:
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self._build_dolls(portraits)
        self._build_remoldings()
        self._build_equipment_pages()

    def _connect_signals(self) -> None:
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.search.textChanged.connect(self._filters_changed)
        self.factor.currentIndexChanged.connect(self._filters_changed)
        self.element.currentIndexChanged.connect(self._filters_changed)
        self.category.currentIndexChanged.connect(self._filters_changed)
        self.category_add.clicked.connect(self._assign_category)
        self.category_remove.clicked.connect(self._remove_category)
        self.skill_cycles.clicked.connect(self._edit_selected_skill_cycles)
        self.remolding_major.currentIndexChanged.connect(self._filters_changed)
        self.remolding_factor.currentIndexChanged.connect(self._filters_changed)
        self.refresh_btn.clicked.connect(self._manual_refresh)
        self.export_logger.clicked.connect(self._export_logger_csv)

    def _build_dolls(self, portraits: PortraitLoader) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.doll_reference_status = QLabel()
        self.doll_reference_status.setObjectName("Muted")
        self.doll_reference_status.setWordWrap(True)
        layout.addWidget(self.doll_reference_status)

        self.doll_model = DollListModel(portraits=portraits)
        element_names = reference.remolding_rules().get("element_names", {})
        self.doll_groups = ElementGroupedDollView(
            self.doll_model,
            element_names,
            self,
            card_size=QSize(130, 140),
            grid_size=QSize(134, 144),
            show_favorite=False,
        )
        layout.addWidget(self.doll_groups, 1)
        self.stack.addWidget(page)

        self.doll_groups.entrySelected.connect(self._doll_selected)

    def _build_remoldings(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        rules = reference.remolding_rules()
        main_options = {
            str(row.get("key") or ""): str(row.get("nameKR") or row.get("key") or "")
            for row in rules.get("options", [])
            if row.get("isMajor") and row.get("key")
        }
        split = QSplitter(Qt.Orientation.Horizontal)
        self.remolding_groups = RemoldingGroupedView(main_options, self)
        self.remolding_groups.rowSelected.connect(self._remolding_selected)
        split.addWidget(self.remolding_groups)

        detail = QFrame()
        detail.setObjectName("Panel")
        detail.setMinimumWidth(300)
        detail.setMaximumWidth(430)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(9)
        title = QLabel("선택 리몰딩 상세")
        title.setObjectName("SectionTitle")
        detail_layout.addWidget(title)
        self.rem_detail_name = QLabel("왼쪽에서 리몰딩을 선택하세요.")
        self.rem_detail_name.setObjectName("AccentText")
        self.rem_detail_name.setWordWrap(True)
        detail_layout.addWidget(self.rem_detail_name)
        self.rem_detail_class = QLabel("클래스 · —")
        self.rem_detail_class.setObjectName("Muted")
        detail_layout.addWidget(self.rem_detail_class)
        self.rem_detail_options = QLabel("전체 옵션\n—")
        self.rem_detail_options.setWordWrap(True)
        self.rem_detail_options.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.rem_detail_options)
        self.rem_detail_uid = QLabel("UID · —")
        self.rem_detail_uid.setObjectName("Muted")
        self.rem_detail_uid.setWordWrap(True)
        self.rem_detail_uid.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.rem_detail_uid)
        self.rem_delete = QPushButton("선택 리몰딩 삭제")
        self.rem_delete.setObjectName("DangerButton")
        self.rem_delete.setEnabled(False)
        self.rem_delete.setToolTip("OCR/수동 입력 실수로 추가된 리몰딩을 보유 데이터에서 제거합니다.")
        self.rem_delete.clicked.connect(self._delete_selected_remolding)
        detail_layout.addWidget(self.rem_delete)
        detail_layout.addStretch(1)
        split.addWidget(detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([900, 340])
        layout.addWidget(split, 1)
        self.stack.addWidget(page)


    def _build_equipment_pages(self) -> None:
        self.equipment_pages: dict[str, QWidget] = {}
        self.equipment_summaries: dict[str, QLabel] = {}
        self.equipment_trees: dict[str, QTreeWidget] = {}
        self.equipment_descriptions: dict[str, QLabel] = {}
        definitions = {
            "weapons": (
                "무기",
                "로그인에서 확인한 무기 인스턴스를 ID/UID와 함께 표시합니다.",
                ["무기", "ID", "UID", "Lv", "돌파", "장착 인형", "이름 출처"],
                {0: 260, 1: 100, 2: 150, 3: 70, 4: 70, 5: 160, 6: 220},
            ),
            "common_keys": (
                "공용키",
                "현재 로그인에서 인형과 연결된 공용키를 표시합니다.",
                ["공용키", "ID", "장착 인형", "이름 확인", "출처"],
                {0: 300, 1: 110, 2: 300, 3: 100, 4: 220},
            ),
            "fixed_keys": (
                "고유키",
                "현재 로그인에서 인형과 연결된 고유키를 표시합니다.",
                ["고유키", "ID", "장착 인형", "이름 확인", "출처"],
                {0: 300, 1: 110, 2: 300, 3: 100, 4: 220},
            ),
            "expansion_keys": (
                "도약키",
                "현재 로그인에서 인형과 연결된 도약키를 표시합니다.",
                ["도약키", "ID", "장착 인형", "이름 확인", "출처"],
                {0: 300, 1: 110, 2: 300, 3: 100, 4: 220},
            ),
        }
        for kind, (title, description, headers, widths) in definitions.items():
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)

            heading = QHBoxLayout()
            title_label = QLabel(title)
            title_label.setObjectName("SectionTitle")
            heading.addWidget(title_label)
            summary = QLabel("데이터를 불러오는 중…")
            summary.setObjectName("Muted")
            heading.addWidget(summary)
            heading.addStretch(1)
            layout.addLayout(heading)

            tree = QTreeWidget()
            tree.setObjectName(f"InventoryEquipmentTree_{kind}")
            tree.setColumnCount(len(headers))
            tree.setHeaderLabels(headers)
            tree.setRootIsDecorated(False)
            configure_tree_widget(tree, widths=widths)
            layout.addWidget(tree, 1)

            description = QLabel("항목을 선택하면 게임 설명을 표시합니다.")
            description.setObjectName("Muted")
            description.setWordWrap(True)
            description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            description.setMinimumHeight(58)
            description.setMaximumHeight(150)
            tree.currentItemChanged.connect(
                lambda current, _previous, current_kind=kind: self._equipment_item_selected(current_kind, current)
            )
            layout.addWidget(description)

            self.equipment_pages[kind] = page
            self.equipment_summaries[kind] = summary
            self.equipment_trees[kind] = tree
            self.equipment_descriptions[kind] = description
            self.stack.addWidget(page)

    @staticmethod
    def _equipment_catalog_index(data: dict[str, Any], category: str) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for raw in data.get(category) or []:
            if not isinstance(raw, dict):
                continue
            try:
                item_id = int(raw.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if item_id > 0:
                out[item_id] = dict(raw)
        return out

    @staticmethod
    def _doll_names() -> dict[int, str]:
        return {int(key): str(value) for key, value in reference.bundled_doll_display_names().items()}

    @staticmethod
    def _display_equipment_name(item_id: int, catalog_row: dict[str, Any] | None, prefix: str) -> tuple[str, str, str]:
        row = dict(catalog_row or {})
        name = str(row.get("name") or "").strip()
        source = str(row.get("source") or "").strip()
        if name:
            return name, "확인", source or "Table 이름"
        return f"{prefix} ID {item_id}", "ID만", source or "—"

    @staticmethod
    def _weapon_owner_index(doll_rows: dict[str, Any]) -> dict[int, int]:
        owners: dict[int, int] = {}
        for raw_doll_id, raw in doll_rows.items():
            if not isinstance(raw, dict):
                continue
            try:
                doll_id = int(raw_doll_id)
                weapon_uid = int(raw.get("weapon_uid") or 0)
            except (TypeError, ValueError):
                continue
            if weapon_uid > 0:
                owners[weapon_uid] = doll_id
        return owners

    @staticmethod
    def _key_owner_index(doll_rows: dict[str, Any], field_name: str) -> dict[int, set[int]]:
        owners: dict[int, set[int]] = {}
        for raw_doll_id, raw in doll_rows.items():
            if not isinstance(raw, dict):
                continue
            try:
                doll_id = int(raw_doll_id)
            except (TypeError, ValueError):
                continue
            for value in raw.get(field_name) or []:
                try:
                    item_id = int(value)
                except (TypeError, ValueError):
                    continue
                if item_id > 0:
                    owners.setdefault(item_id, set()).add(doll_id)
        return owners

    @staticmethod
    def _populate_equipment_tree(
        tree: QTreeWidget, prepared: list[tuple[str, list[str], str]]
    ) -> int:
        for _sort, values, description in sorted(prepared, key=lambda row: (row[0], row[1][1])):
            item = QTreeWidgetItem(values)
            item.setData(0, Qt.ItemDataRole.UserRole, description)
            rich_tooltip = game_markup_to_qt_html(description) if description.strip() else ""
            for column, value in enumerate(values):
                item.setToolTip(column, rich_tooltip or value)
            tree.addTopLevelItem(item)
        return len(prepared)

    def _equipment_item_selected(self, kind: str, item: QTreeWidgetItem | None) -> None:
        label = self.equipment_descriptions.get(str(kind))
        if label is None:
            return
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            label.setObjectName("Muted")
            set_game_rich_text(label, "", empty="항목을 선택하면 게임 설명을 표시합니다.")
            return
        description = str(item.data(0, Qt.ItemDataRole.UserRole) or "").strip()
        label.setObjectName("" if description else "Muted")
        set_game_rich_text(label, description, empty="이 항목에는 별도 설명이 없습니다.")

    def _refresh_weapon_equipment(
        self,
        tree: QTreeWidget,
        summary: QLabel,
        catalog: dict[str, Any],
        doll_rows: dict[str, Any],
        weapon_rows: dict[str, Any],
        names: dict[int, str],
        query: str,
    ) -> int:
        index = self._equipment_catalog_index(catalog, "weapons")
        owner_index = self._weapon_owner_index(doll_rows)
        prepared: list[tuple[str, list[str], str]] = []
        equipped_count = 0

        for raw_uid, raw in weapon_rows.items():
            if not isinstance(raw, dict):
                continue
            try:
                uid = int(raw.get("uid") or raw_uid or 0)
                primary_id = int(raw.get("item_id") or 0)
            except (TypeError, ValueError):
                continue

            candidates: list[int] = []
            for value in raw.get("item_id_candidates") or []:
                try:
                    candidate = int(value)
                except (TypeError, ValueError):
                    continue
                if candidate > 0 and candidate not in candidates:
                    candidates.append(candidate)
            if primary_id > 0 and primary_id not in candidates:
                candidates.insert(0, primary_id)

            catalog_hits = [candidate for candidate in candidates if candidate in index]
            resolved_id = (
                primary_id
                if primary_id in index
                else catalog_hits[0]
                if len(catalog_hits) == 1
                else primary_id
            )
            label, verified, source = self._display_equipment_name(
                resolved_id, index.get(resolved_id), "무기"
            )
            owner = int(raw.get("equipped_doll_id") or owner_index.get(uid) or 0)
            if owner > 0:
                equipped_count += 1
            owner_name = str(names.get(owner) or (f"인형 ID {owner}" if owner else "—"))
            values = [
                label,
                str(resolved_id or "—"),
                str(uid or "—"),
                str(int(raw.get("level") or 0) or "—"),
                str(int(raw.get("rank") or 0) or "—"),
                owner_name,
                f"{verified} · {source}",
            ]
            catalog_row = dict(index.get(resolved_id) or {})
            description = str(catalog_row.get("description") or "")
            search_haystack = " ".join(values + [game_markup_to_plain_text(description)]).casefold()
            if query and query not in search_haystack:
                continue
            prepared.append((label.casefold(), values, description))

        shown = self._populate_equipment_tree(tree, prepared)
        refresh_note = " · 이름 데이터 갱신 필요" if catalog.get("needs_refresh") else ""
        summary.setText(
            f"무기 {len(weapon_rows):,}개 · 장착 {equipped_count:,}개 · 표시 {shown:,}개"
            f"{refresh_note}"
        )
        return shown

    def _refresh_key_equipment(
        self,
        kind: str,
        tree: QTreeWidget,
        summary: QLabel,
        catalog: dict[str, Any],
        doll_rows: dict[str, Any],
        names: dict[int, str],
        query: str,
    ) -> tuple[int, str]:
        category_info = {
            "common_keys": ("common_key_ids", "공용키"),
            "fixed_keys": ("fixed_key_ids", "고유키"),
            "expansion_keys": ("expansion_key_ids", "도약키"),
        }
        field_name, label_prefix = category_info[kind]
        index = self._equipment_catalog_index(catalog, kind)
        owners = self._key_owner_index(doll_rows, field_name)
        prepared: list[tuple[str, list[str], str]] = []

        for item_id, doll_ids in owners.items():
            label, verified, source = self._display_equipment_name(
                item_id, index.get(item_id), label_prefix
            )
            owner_text = " · ".join(
                str(names.get(doll_id) or f"인형 ID {doll_id}")
                for doll_id in sorted(doll_ids)
            )
            values = [label, str(item_id), owner_text or "—", verified, source or "—"]
            catalog_row = dict(index.get(item_id) or {})
            description = str(catalog_row.get("description") or "")
            search_haystack = " ".join(values + [game_markup_to_plain_text(description)]).casefold()
            if query and query not in search_haystack:
                continue
            prepared.append((label.casefold(), values, description))

        shown = self._populate_equipment_tree(tree, prepared)
        refresh_note = " · 이름 데이터 갱신 필요" if catalog.get("needs_refresh") else ""
        summary.setText(
            f"{label_prefix} {len(owners):,}종 · 표시 {shown:,}종 · 현재 인형 장착값 기준"
            f"{refresh_note}"
        )
        return shown, label_prefix

    def _equipment_sources(self) -> tuple[dict[str, Any], dict[str, Any], dict[int, str]]:
        """Load equipment sidecars once per shared-data revision.

        Search filtering used to read both JSON sidecars on every keystroke.
        Shared-data changes already invalidate this page, so a page-local cache
        keeps filtering entirely in memory without risking stale imported data.
        """
        if self._equipment_source_cache is None:
            self._equipment_source_cache = (
                TacticEquipmentCatalog(self.repo).load(),
                ImportedEquipmentStore(self.repo).load(),
                self._doll_names(),
            )
        return self._equipment_source_cache

    def _schedule_equipment_filter(self, kind: str) -> None:
        self._pending_equipment_kind = str(kind or "")
        self._equipment_filter_timer.start()

    def _flush_equipment_filter(self) -> None:
        kind = self._pending_equipment_kind
        self._pending_equipment_kind = ""
        if not self.page_active or str(self.kind.currentData() or "") != kind:
            return
        self._refresh_equipment_view(kind)

    def _refresh_equipment_view(self, kind: str | None = None) -> None:
        current_kind = str(kind or self.kind.currentData() or "")
        if current_kind not in self.equipment_trees:
            return

        tree = self.equipment_trees[current_kind]
        summary = self.equipment_summaries[current_kind]
        catalog, imported, names = self._equipment_sources()
        doll_rows = dict(imported.get("dolls") or {})
        weapon_rows = dict(imported.get("weapons_by_uid") or {})
        query = self.search.text().casefold().strip()
        empty_label = "무기"

        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            detail = self.equipment_descriptions.get(current_kind)
            if detail is not None:
                detail.setObjectName("Muted")
                set_game_rich_text(detail, "", empty="항목을 선택하면 게임 설명을 표시합니다.")
            if current_kind == "weapons":
                shown = self._refresh_weapon_equipment(
                    tree, summary, catalog, doll_rows, weapon_rows, names, query
                )
            else:
                shown, empty_label = self._refresh_key_equipment(
                    current_kind, tree, summary, catalog, doll_rows, names, query
                )

            if shown == 0:
                empty = QTreeWidgetItem(
                    [f"표시할 {empty_label} 데이터가 없습니다."]
                    + [""] * (tree.columnCount() - 1)
                )
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                tree.addTopLevelItem(empty)
                empty.setFirstColumnSpanned(True)
        finally:
            tree.setUpdatesEnabled(True)

    def _remolding_selected(self, row: dict[str, Any]) -> None:
        if not row:
            self._selected_remolding_uid = ""
            self.rem_delete.setEnabled(False)
            self.rem_detail_name.setText("왼쪽에서 리몰딩을 선택하세요.")
            self.rem_detail_class.setText("클래스 · —")
            self.rem_detail_options.setText("전체 옵션\n—")
            self.rem_detail_uid.setText("UID · —")
            return
        self._selected_remolding_uid = str(row.get("uid") or "").strip()
        self.rem_delete.setEnabled(bool(self._selected_remolding_uid))
        self.rem_detail_name.setText(str(row.get("name") or "리몰딩"))
        self.rem_detail_class.setText(f"클래스 · {row.get('class_label') or '—'}")
        attrs = str(row.get("attributes") or "—")
        # One option per line is much faster to scan than a long slash-delimited string.
        parts = [part.strip() for part in attrs.split("/") if part.strip()]
        self.rem_detail_options.setText("전체 옵션\n" + "\n".join(f"• {part}" for part in parts))
        self.rem_detail_uid.setText(f"UID · {row.get('uid') or '—'}")

    def _delete_selected_remolding(self) -> None:
        uid = self._selected_remolding_uid
        if not uid:
            return
        answer = QMessageBox.question(
            self,
            "리몰딩 삭제",
            "선택한 리몰딩을 보유 데이터에서 삭제할까요?\n\n"
            "제대 계획에서 이 UID를 사용 중이면 해당 장착 연결도 함께 해제됩니다. "
            "추천 패턴의 옵션 자체는 유지되고 원본 UID 연결만 제거됩니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            changed = self.repo.delete_remolding(uid)
        except Exception as exc:
            show_error(self, "리몰딩 삭제 실패", exc)
            return
        if not changed:
            QMessageBox.information(self, "리몰딩 삭제", "이미 삭제되었거나 해당 UID를 찾지 못했습니다.")
        self._selected_remolding_uid = ""
        self.rem_delete.setEnabled(False)
        self._remolding_cache_token = None
        self._remolding_loading_token = None
        self._remolding_cache = []
        self._remolding_selected({})
        QTimer.singleShot(0, self._refresh_remoldings)

    def _kind_changed(self) -> None:
        kind = str(self.kind.currentData() or "dolls")
        is_dolls = kind == "dolls"
        is_remoldings = kind == "remoldings"
        stack_indexes = {"dolls": 0, "remoldings": 1}
        for offset, equipment_kind in enumerate(("weapons", "common_keys", "fixed_keys", "expansion_keys"), start=2):
            stack_indexes[equipment_kind] = offset
        self.stack.setCurrentIndex(stack_indexes.get(kind, 0))
        self.factor.setVisible(is_dolls)
        self.element.setVisible(is_dolls)
        self.category.setVisible(is_dolls)
        self.category_add.setVisible(is_dolls)
        self.category_remove.setVisible(is_dolls)
        self.skill_cycles.setVisible(is_dolls)
        self.remolding_major.setVisible(is_remoldings)
        self.remolding_factor.setVisible(is_remoldings)
        self.export_logger.setVisible(is_remoldings)

        placeholders = {
            "dolls": "인형 이름 또는 ID 검색",
            "remoldings": "리몰딩 옵션 또는 UID 검색",
            "weapons": "무기 이름 · ID · UID 검색",
            "common_keys": "공용키 이름 또는 ID 검색",
            "fixed_keys": "고유키 이름 또는 ID 검색",
            "expansion_keys": "도약키 이름 또는 ID 검색",
        }
        self.search.setPlaceholderText(placeholders.get(kind, "검색"))

        self._filters_changed()
        if is_remoldings:
            QTimer.singleShot(0, self._refresh_remoldings)
        elif kind in self.equipment_trees:
            self._equipment_filter_timer.stop()
            self._refresh_equipment_view(kind)

    def _filters_changed(self) -> None:
        if self.kind.currentData() == "dolls":
            self.doll_groups.set_filters(
                query=self.search.text(),
                factor=str(self.factor.currentData() or ""),
                visible_element=str(self.element.currentData() or ""),
                favorites_only=False,
                allowed_character_keys=(
                    self.category_store.keys(str(self.category.currentData() or ""))
                    if self.category.currentData() else None
                ),
            )
            self.category_remove.setEnabled(bool(self.category.currentData()) and self._selected_doll_id is not None)
            self.skill_cycles.setEnabled(self._selected_doll_id is not None)
            return

        if self.kind.currentData() == "remoldings":
            self.remolding_groups.set_filters(
                query=self.search.text(),
                main_option=str(self.remolding_major.currentData() or ""),
                factor=str(self.remolding_factor.currentData() or ""),
            )
            return
        kind = str(self.kind.currentData() or "")
        if kind in self.equipment_trees:
            self._schedule_equipment_filter(kind)

    def _prepare_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for raw in self.catalog.entries_with_portraits():
            entry = dict(raw)
            portrait = entry.get("portrait_path")
            entry["portrait_path"] = str(portrait) if portrait else ""
            entry["sort_key"] = (0, str(entry.get("name") or "").casefold(), int(entry.get("doll_id") or 0))
            entries.append(entry)
        return entries

    def invalidate_cache(self) -> None:
        self._refresh_token = None
        self._remolding_cache_token = None
        self._remolding_loading_token = None
        self._remolding_cache = []
        self._equipment_source_cache = None
        self._equipment_filter_timer.stop()
        self._pending_equipment_kind = ""
        self.request_refresh()

    def _manual_refresh(self) -> None:
        self.invalidate_cache()

    def on_deactivated(self) -> None:
        self._equipment_filter_timer.stop()
        self._pending_equipment_kind = ""

    def refresh(self) -> None:
        self._refresh_categories()
        token = self.repo.state_token()
        if token != self._refresh_token:
            entries = self._prepare_entries()
            basic_count = len(reference.bundled_doll_display_names())
            owned_count = len(entries)
            if owned_count:
                self.doll_reference_status.setText(
                    f"보유 인형 {owned_count:,}명 · 기본 인형 레퍼런스 {basic_count:,}명 로드됨. "
                    "이 화면은 보유로 등록된 인형만 표시합니다. 단, 동일 소체의 연동 개조형은 사용 가능한 별도 형태로 함께 표시합니다."
                )
                self.doll_groups.empty.setText("조건에 맞는 보유 인형이 없습니다.")
            else:
                self.doll_reference_status.setText(
                    f"기본 인형 레퍼런스 {basic_count:,}명은 정상 로드되어 있습니다. "
                    "아직 보유 인형이 등록되지 않았습니다. 데이터 동기화에서 보조 툴 사용자 ZIP 또는 기존 GF2Tools 백업을 가져오세요."
                )
                self.doll_groups.empty.setText(
                    "보유 인형 데이터가 없습니다. 데이터 동기화에서 사용자 데이터 ZIP/백업을 가져오세요."
                )
            self.doll_model.set_entries(entries)
            self.doll_groups.refresh_sections()
            self._refresh_token = token
            self._remolding_cache_token = None

        self._filters_changed()
        if self.kind.currentData() == "remoldings":
            QTimer.singleShot(0, self._refresh_remoldings)
        elif str(self.kind.currentData() or "") in self.equipment_trees:
            self._equipment_filter_timer.stop()
            self._pending_equipment_kind = ""
            self._refresh_equipment_view(str(self.kind.currentData() or ""))
        self._restore_doll_selection()

    def _restore_doll_selection(self) -> None:
        if self._selected_doll_id is None:
            return
        self.doll_groups.select_by("doll_id", self._selected_doll_id)

    def _doll_selected(self, entry: dict[str, Any]) -> None:
        # The inventory page intentionally keeps doll browsing card-first.
        # Selection is retained only so refreshes can restore the highlighted card.
        self._selected_doll_id = int(entry.get("doll_id") or 0)
        self.skill_cycles.setEnabled(self._selected_doll_id > 0)

    def _selected_doll_entry(self) -> dict[str, Any]:
        if self._selected_doll_id is None:
            return {}
        for row in range(self.doll_model.rowCount()):
            entry = self.doll_model.entry(row) or {}
            if int(entry.get("doll_id") or -1) == int(self._selected_doll_id):
                return dict(entry)
        return {}

    def _edit_selected_skill_cycles(self) -> None:
        entry = self._selected_doll_entry()
        if not entry:
            QMessageBox.information(self, "스킬 사이클", "먼저 인형을 선택하세요.")
            return
        row = dict(entry.get("row") or {})
        doll_id = int(entry.get("doll_id") or 0)
        DollSkillCycleDialog(
            self.skill_cycle_store,
            doll_id=doll_id,
            doll_name=str(entry.get("name") or "인형"),
            parent=self,
            sync_from_label="제대 사이클 불러오기…",
            sync_from=lambda did=doll_id: self._choose_formation_cycle(did),
            sync_to_label="제대 사이클에 저장…",
            sync_to=lambda actions, did=doll_id: self._save_to_formation_cycle(did, actions),
        ).exec()

    def _formation_cycle_choice(self, doll_id: int, *, require_existing: bool) -> dict[str, Any] | None:
        candidates = formation_cycle_candidates(
            self.repo, self.formation_cycle_store, doll_id, include_empty=not require_existing
        )
        if not candidates:
            QMessageBox.information(
                self, "제대 스킬 사이클",
                "이 인형에 저장된 제대 전용 사이클이 없습니다." if require_existing
                else "이 인형이 배치된 저장 제대를 찾지 못했습니다.",
            )
            return None
        labels = [
            f"{row['plan_name']} · {int(row['position'])}번 슬롯 · "
            + (f"T1~T{len(row['actions'])}" if row["actions"] else "사이클 미설정")
            for row in candidates
        ]
        selected, ok = QInputDialog.getItem(
            self, "제대 스킬 사이클", "대상 제대를 선택하세요.", labels, 0, False
        )
        if not ok:
            return None
        index = labels.index(str(selected)) if str(selected) in labels else -1
        return candidates[index] if index >= 0 else None

    def _choose_formation_cycle(self, doll_id: int) -> list[str] | None:
        choice = self._formation_cycle_choice(doll_id, require_existing=True)
        return list(choice.get("actions") or []) if choice else None

    def _save_to_formation_cycle(self, doll_id: int, actions: list[str]) -> bool:
        choice = self._formation_cycle_choice(doll_id, require_existing=False)
        if not choice:
            return False
        self.formation_cycle_store.set_skill_actions(
            int(choice["plan_id"]), int(choice["position"]), int(doll_id), actions
        )
        return True

    def _selected_character_key(self) -> str:
        if self._selected_doll_id is None:
            return ""
        for row in range(self.doll_model.rowCount()):
            entry = self.doll_model.entry(row) or {}
            if int(entry.get("doll_id") or -1) == int(self._selected_doll_id):
                return str(entry.get("character_key") or "")
        return ""

    def _refresh_categories(self) -> None:
        current = str(self.category.currentData() or "")
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("카테고리 전체", "")
        for name in self.category_store.names():
            self.category.addItem(f"{name} ({len(self.category_store.keys(name))})", name)
        index = self.category.findData(current)
        self.category.setCurrentIndex(index if index >= 0 else 0)
        self.category.blockSignals(False)
        self.category_remove.setEnabled(bool(self.category.currentData()) and self._selected_doll_id is not None)

    def _assign_category(self) -> None:
        key = self._selected_character_key()
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
        self._refresh_categories()
        self._filters_changed()

    def _remove_category(self) -> None:
        key = self._selected_character_key()
        category = str(self.category.currentData() or "").strip()
        if not key or not category:
            return
        try:
            self.category_store.remove(category, [key])
        except Exception as exc:
            show_error(self, "인형 카테고리 저장 실패", exc)
            return
        self._refresh_categories()
        self._filters_changed()

    def _refresh_remoldings(self) -> None:
        token = self.repo.state_token()
        if (
            self._remolding_cache_token == token
            or self._remolding_loading_token == token
        ):
            return

        self._remolding_loading_token = token
        db = str(self.repo.path)

        def work():
            with Repository(db) as repo:
                prepared: list[dict[str, Any]] = []
                for raw in repo.remolding_inventory_rows():
                    row = dict(raw)
                    meta = remolding_meta(row)
                    name = str(meta["name"])
                    attributes = str(meta["attributes"])
                    uid = str(row.get("uid") or "")
                    primary_factor = str(meta.get("primary_factor") or "")
                    class_label = str(
                        reference.remolding_rules().get("factor_names", {}).get(
                            primary_factor, primary_factor or "—"
                        )
                    )
                    main_option_key = str(meta.get("main_option_key") or "")
                    main_option_name = str(meta.get("main_option_name") or name)
                    sub_attributes = str(meta.get("sub_attributes") or "—")
                    prepared.append(
                        {
                            "name": main_option_name,
                            "attributes": attributes,
                            "sub_attributes": sub_attributes,
                            "main_option_key": main_option_key,
                            "primary_factor": primary_factor,
                            "class_label": class_label,
                            "uid": uid,
                            "search_text": (
                                f"{main_option_name} {attributes} {class_label} {uid}"
                            ),
                            "payload": row,
                        }
                    )
                return token, prepared

        run_worker(
            self.pool,
            work,
            on_result=self._remoldings_ready,
            on_error=lambda error, current=token: self._remoldings_failed(current, error),
        )

    def _remoldings_ready(self, payload) -> None:
        token, prepared = payload
        if self._remolding_loading_token == token:
            self._remolding_loading_token = None

        if tuple(token) != self.repo.state_token():
            if self.kind.currentData() == "remoldings":
                QTimer.singleShot(0, self._refresh_remoldings)
            return

        self._remolding_cache = list(prepared or [])
        self.remolding_groups.set_rows(self._remolding_cache)
        self._remolding_cache_token = token
        self._filters_changed()

    def _remoldings_failed(self, token, error: str) -> None:
        if self._remolding_loading_token != token:
            return
        self._remolding_loading_token = None
        if self.page_active and self.kind.currentData() == "remoldings":
            show_error(self, "리몰딩 목록 갱신 실패", error)

    def _export_logger_csv(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "리몰딩 CSV 저장",
            default_remoldings_csv_name(),
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            count = export_remoldings_csv(self.repo, path)
        except Exception as exc:
            show_error(self, "리몰딩 CSV 내보내기 실패", str(exc))
            return
        QMessageBox.information(
            self,
            "리몰딩 CSV 내보내기 완료",
            f"리몰딩 {count:,}개를 gfl2logger 호환 형식으로 저장했습니다.",
        )
