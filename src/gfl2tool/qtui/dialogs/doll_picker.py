from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ... import reference
from ...repository import Repository
from ...services.doll_categories import DollCategoryStore
from ..data import OwnedDollCatalog
from ..grouped_dolls import ElementGroupedDollView
from ..images import PortraitLoader
from ..models import DollListModel
from ..theme import ELEMENT_ORDER, FACTOR_ORDER
from ..widgets import dialog_layout, page_title


class DollPickerDialog(QDialog):
    """Owned-doll picker using the same element-grouped roster as Inventory."""

    def __init__(
        self,
        repo: Repository,
        catalog: OwnedDollCatalog,
        portraits: PortraitLoader,
        selected_id: int | None = None,
        parent=None,
        *,
        multi_select: bool = False,
        selected_ids: set[int] | None = None,
        include_unowned: bool = False,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog
        self.portraits = portraits
        self.category_store = DollCategoryStore(repo.path.parent)
        self.selected_id = selected_id
        self.multi_select = bool(multi_select)
        self.include_unowned = bool(include_unowned)
        self.initial_selected_ids = {int(value) for value in (selected_ids or set())}
        self._multi_selected_ids = set(self.initial_selected_ids)
        self._syncing_multi_selection = False
        self.result_id: int | None = None
        self.result_ids: list[int] = []
        self.result_entries: list[dict] = []
        self._selected_entry: dict | None = None

        self.setWindowTitle("사용 인형 선택" if self.multi_select else "인형 선택")
        self.resize(1080, 780)
        self.setMinimumSize(860, 640)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)
        root.addWidget(
            page_title(
                "사용 인형 선택" if self.multi_select else "인형 선택",
                (
                    "여러 인형을 한 번에 선택할 수 있습니다. 선택된 인형은 아래에 바로 표시됩니다."
                    if self.multi_select
                    else (
                        "전체 인형을 속성별로 확인하고 검색·직업·속성으로 좁혀 선택합니다."
                        if self.include_unowned
                        else "보유 인형을 속성별로 확인하고 검색·직업·속성·사용자 카테고리로 좁혀 선택합니다."
                    )
                ),
            )
        )

        rules = reference.remolding_rules()
        factor_names = dict(rules.get("factor_names") or {})
        element_names = dict(rules.get("element_names") or {})
        self._build_toolbar(root, factor_names, element_names)
        self._build_selection_controls(root)
        self._build_roster(root, element_names)
        self._connect_signals()
        self._restore_initial_selection(selected_id)
        self._build_buttons(root)

    def _build_toolbar(self, root, factor_names: dict, element_names: dict) -> None:
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("인형 검색")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, 1)

        self.factor = QComboBox()
        self.factor.addItem("직업 전체", "")
        for key in FACTOR_ORDER:
            self.factor.addItem(str(factor_names.get(key, key)), key)
        toolbar.addWidget(self.factor)

        self.element = QComboBox()
        self.element.addItem("속성 전체", "")
        for key in ELEMENT_ORDER:
            self.element.addItem(str(element_names.get(key, key)), key)
        toolbar.addWidget(self.element)

        self.category = QComboBox()
        self.category.addItem("카테고리 전체", "")
        for name in self.category_store.names():
            self.category.addItem(f"{name} ({len(self.category_store.keys(name))})", name)
        toolbar.addWidget(self.category)
        root.addLayout(toolbar)

    def _build_selection_controls(self, root) -> None:
        self.selection_bar = QWidget()
        self.selection_bar_layout = QHBoxLayout(self.selection_bar)
        self.selection_bar_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_bar_layout.setSpacing(5)
        self.selection_bar.setVisible(self.multi_select)
        root.addWidget(self.selection_bar)
        if not self.multi_select:
            return

        select_row = QHBoxLayout()
        self.select_visible = QPushButton("현재 결과 전체 선택")
        self.select_all = QPushButton("전체 인형 선택")
        self.clear_selection = QPushButton("선택 해제")
        self.select_visible.clicked.connect(self._select_visible)
        self.select_all.clicked.connect(self._select_all)
        self.clear_selection.clicked.connect(self._clear_multi_selection)
        select_row.addWidget(self.select_visible)
        select_row.addWidget(self.select_all)
        select_row.addWidget(self.clear_selection)
        select_row.addStretch(1)
        root.addLayout(select_row)

    def _build_roster(self, root, element_names: dict) -> None:
        self.model = DollListModel(portraits=self.portraits)
        self._rebuild_entries()
        self.groups = ElementGroupedDollView(
            self.model,
            element_names,
            self,
            card_size=QSize(128, 138),
            grid_size=QSize(132, 142),
            multi_select=self.multi_select,
            show_favorite=False,
        )
        root.addWidget(self.groups, 1)

    def _connect_signals(self) -> None:
        self.search.textChanged.connect(self._apply_filters)
        self.factor.currentIndexChanged.connect(self._apply_filters)
        self.element.currentIndexChanged.connect(self._apply_filters)
        self.category.currentIndexChanged.connect(self._apply_filters)
        self.groups.entrySelected.connect(self._entry_selected)
        if self.multi_select:
            self.groups.selectionChanged.connect(self._multi_selection_changed)

    def _restore_initial_selection(self, selected_id: int | None) -> None:
        self._apply_filters()
        if self.multi_select and self.initial_selected_ids:
            self._syncing_multi_selection = True
            try:
                self.groups.select_many_by("doll_id", self._multi_selected_ids)
            finally:
                self._syncing_multi_selection = False
            self._render_multi_selection()
            return
        self._select_doll(selected_id)

    def _build_buttons(self, root) -> None:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("선택")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply_filters(self) -> None:
        if self.multi_select:
            self._syncing_multi_selection = True
        try:
            self.groups.set_filters(
                query=self.search.text(),
                factor=str(self.factor.currentData() or ""),
                visible_element=str(self.element.currentData() or ""),
                favorites_only=False,
                allowed_character_keys=(
                    self.category_store.keys(str(self.category.currentData() or ""))
                    if self.category.currentData() else None
                ),
            )
            if self.multi_select:
                self.groups.select_many_by("doll_id", self._multi_selected_ids)
        finally:
            self._syncing_multi_selection = False
        if self.multi_select:
            self._render_multi_selection()

    def _entry_selected(self, entry: dict) -> None:
        self._selected_entry = dict(entry)

    def _multi_selection_changed(self, entries: object) -> None:
        if self._syncing_multi_selection:
            return
        selected = list(entries) if isinstance(entries, list) else self.groups.selected_entries()
        selected_ids = {
            int(entry["doll_id"])
            for entry in selected
            if entry.get("doll_id") is not None
        }
        # Filtering a proxy can remove its visible selection indexes. Preserve
        # choices that are currently hidden by search/element/category filters
        # and update only the IDs represented by the current visible result set.
        visible_ids = {
            int(entry["doll_id"])
            for entry in self.groups.visible_entries()
            if entry.get("doll_id") is not None
        }
        self._multi_selected_ids.difference_update(visible_ids)
        self._multi_selected_ids.update(selected_ids)
        self._render_multi_selection()

    def _render_multi_selection(self) -> None:
        entries_by_id: dict[int, dict] = {}
        for row in range(self.model.rowCount()):
            entry = dict(self.model.entry(row) or {})
            if entry.get("doll_id") is not None:
                entries_by_id[int(entry["doll_id"])] = entry
        selected = [entries_by_id[value] for value in sorted(self._multi_selected_ids) if value in entries_by_id]
        selected.sort(key=lambda row: tuple(row.get("sort_key") or (str(row.get("name") or ""),)))
        while self.selection_bar_layout.count():
            item = self.selection_bar_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not selected:
            empty = QLabel("선택된 인형 없음")
            empty.setObjectName("Muted")
            self.selection_bar_layout.addWidget(empty)
        else:
            for entry in selected[:8]:
                chip = QLabel(str(entry.get("name") or entry.get("doll_id") or "인형"))
                chip.setObjectName("SelectionChip")
                self.selection_bar_layout.addWidget(chip)
            if len(selected) > 8:
                extra = QLabel(f"+{len(selected) - 8}")
                extra.setObjectName("SelectionChip")
                self.selection_bar_layout.addWidget(extra)
        self.selection_bar_layout.addStretch(1)

    def _select_doll(self, doll_id: int | None) -> bool:
        if doll_id is None:
            return False
        selected = self.groups.select_by("doll_id", int(doll_id))
        if selected:
            for row in range(self.model.rowCount()):
                entry = self.model.entry(row) or {}
                if int(entry.get("doll_id") or -1) == int(doll_id):
                    self._selected_entry = dict(entry)
                    break
        return selected

    def _rebuild_entries(self, preserve_id: int | None = None) -> None:
        if self.include_unowned:
            entries = [dict(row) for row in self.catalog.all_reference_entries_with_portraits()]
        else:
            entries = []
            for raw in self.catalog.entries_with_portraits():
                entry = dict(raw)
                path = entry.get("portrait_path")
                entry["portrait_path"] = str(path) if path else ""
                entry["owned"] = True
                entry["sort_key"] = (
                    0, str(entry.get("name") or "").casefold(), int(entry.get("doll_id") or 0)
                )
                entries.append(entry)
        self.model.set_entries(entries)
        if preserve_id is not None and hasattr(self, "groups"):
            self.groups.refresh_sections()
            self._select_doll(preserve_id)

    def _select_visible(self) -> None:
        if not self.multi_select:
            return
        self._multi_selected_ids.update(
            int(entry["doll_id"]) for entry in self.groups.visible_entries() if entry.get("doll_id") is not None
        )
        self._apply_filters()

    def _select_all(self) -> None:
        if not self.multi_select:
            return
        self._multi_selected_ids = {
            int((self.model.entry(row) or {}).get("doll_id"))
            for row in range(self.model.rowCount())
            if (self.model.entry(row) or {}).get("doll_id") is not None
        }
        self._apply_filters()

    def _clear_multi_selection(self) -> None:
        if not self.multi_select:
            return
        self._multi_selected_ids.clear()
        self._apply_filters()

    def _accept(self) -> None:
        if self.multi_select:
            self.result_ids = sorted(self._multi_selected_ids)
            if not self.result_ids:
                return
            wanted = set(self.result_ids)
            self.result_entries = [
                dict(self.model.entry(row) or {})
                for row in range(self.model.rowCount())
                if (self.model.entry(row) or {}).get("doll_id") in wanted
            ]
            self.accept()
            return
        entry = self._selected_entry or {}
        if not entry:
            return
        self.result_id = int(entry["doll_id"])
        self.result_ids = [self.result_id]
        self.result_entries = [dict(entry)]
        self.accept()
