from __future__ import annotations

from math import ceil
from typing import Any

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .models import DollCardDelegate, DollFilterProxy, DollListModel, DollListView, ENTRY_ROLE
from . import theme
from .theme import ELEMENT_COLORS, ELEMENT_ORDER


class _ElementSectionProxy(DollFilterProxy):
    def __init__(self, section_element: str | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.section_element = section_element

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not super().filterAcceptsRow(source_row, source_parent):
            return False
        model = self.sourceModel()
        if model is None:
            return False
        entry = model.data(model.index(source_row, 0, source_parent), ENTRY_ROLE) or {}
        element = str(entry.get("element_type") or "")
        if self.section_element is None:
            return element not in ELEMENT_ORDER
        return element == self.section_element


class _AutoHeightDollListView(DollListView):
    """Icon list that delegates vertical scrolling to its containing section view."""

    def __init__(self, parent: QWidget | None = None, *, grid_size: QSize | None = None):
        super().__init__(parent)
        if grid_size is not None:
            self.setGridSize(grid_size)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(0)

    def setModel(self, model) -> None:  # noqa: N802
        previous = self.model()
        if previous is not None:
            for signal in (
                previous.modelReset,
                previous.rowsInserted,
                previous.rowsRemoved,
                previous.layoutChanged,
            ):
                try:
                    signal.disconnect(self.schedule_height_update)
                except (RuntimeError, TypeError):
                    pass
        super().setModel(model)
        if model is not None:
            model.modelReset.connect(self.schedule_height_update)
            model.rowsInserted.connect(self.schedule_height_update)
            model.rowsRemoved.connect(self.schedule_height_update)
            model.layoutChanged.connect(self.schedule_height_update)
        self.schedule_height_update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_height_update()

    def schedule_height_update(self, *_args) -> None:
        QTimer.singleShot(0, self._sync_height)

    def _sync_height(self) -> None:
        model = self.model()
        count = int(model.rowCount()) if model is not None else 0
        if count <= 0:
            self.setFixedHeight(0)
            return
        grid = self.gridSize()
        grid_width = max(1, int(grid.width()))
        grid_height = max(1, int(grid.height()))
        available = max(grid_width, int(self.viewport().width()))
        columns = max(1, available // grid_width)
        rows = max(1, ceil(count / columns))
        self.setFixedHeight(rows * grid_height + 4)


class ElementGroupedDollView(QWidget):
    """Reusable character-card browser grouped by phase element.

    Every section owns a lightweight proxy over one shared ``DollListModel``.
    The outer scroll area handles scrolling so section headers and card rows stay
    visually stable instead of nesting several independently scrolling lists.
    """

    entrySelected = Signal(object)
    selectionChanged = Signal(object)
    favoriteClicked = Signal(int)

    def __init__(
        self,
        model: DollListModel,
        element_names: dict[str, str],
        parent: QWidget | None = None,
        *,
        card_size: QSize | None = None,
        grid_size: QSize | None = None,
        multi_select: bool = False,
        show_favorite: bool = True,
        text_scale: float = 1.0,
    ):
        super().__init__(parent)
        self.model = model
        self.multi_select = bool(multi_select)
        self.show_favorite = bool(show_favorite)
        self.text_scale = max(0.85, min(1.3, float(text_scale)))
        self.element_names = dict(element_names or {})
        self._visible_element = ""
        self._sections: dict[str, QWidget] = {}
        self._proxies: dict[str, DollFilterProxy] = {}
        self._views: dict[str, _AutoHeightDollListView] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("GroupedDollScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        body = QWidget()
        body.setObjectName("GroupedDollBody")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(14)
        self.body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(body)

        size = card_size or QSize(128, 138)
        grid = grid_size or QSize(size.width() + 4, size.height() + 4)
        for element in ELEMENT_ORDER:
            self._add_section(element, size, grid)
        self._add_section("__unknown__", size, grid)

        self.empty = QLabel("조건에 맞는 인형이 없습니다.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(72)
        self.body_layout.addWidget(self.empty)
        self.body_layout.addStretch(1)
        self.refresh_sections()

    def _add_section(self, element: str, card_size: QSize, grid_size: QSize) -> None:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        section_element = None if element == "__unknown__" else element
        label_text = (
            "속성 미확인"
            if section_element is None
            else str(self.element_names.get(element, element))
        )
        label = QLabel(label_text)
        label.setObjectName("ElementGroupTitle")
        label.setStyleSheet(
            f"font-size:11pt;font-weight:750;color:{ELEMENT_COLORS.get(element, theme.BORDER)}"
        )
        layout.addWidget(label)

        proxy = _ElementSectionProxy(section_element, section)
        proxy.setSourceModel(self.model)
        proxy.sort(0)

        view = _AutoHeightDollListView(section, grid_size=grid_size)
        view.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
            if self.multi_select
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        delegate = DollCardDelegate(
            view,
            card_size=card_size,
            show_favorite=self.show_favorite,
            text_scale=self.text_scale,
        )
        view.setItemDelegate(delegate)
        view.setModel(proxy)
        layout.addWidget(view)

        view.selectionModel().currentChanged.connect(
            lambda current, _previous, owner=view: self._selection_changed(owner, current)
        )
        if self.multi_select:
            view.selectionModel().selectionChanged.connect(
                lambda _selected, _deselected: self.selectionChanged.emit(self.selected_entries())
            )
        delegate.favoriteClicked.connect(self.favoriteClicked)
        for signal in (proxy.modelReset, proxy.rowsInserted, proxy.rowsRemoved, proxy.layoutChanged):
            signal.connect(self.refresh_sections)

        self.body_layout.addWidget(section)
        self._sections[element] = section
        self._proxies[element] = proxy
        self._views[element] = view

    def set_filters(
        self,
        *,
        query: str = "",
        factor: str = "",
        visible_element: str = "",
        favorites_only: bool = False,
        allowed_character_keys: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._visible_element = str(visible_element or "")
        for proxy in self._proxies.values():
            proxy.set_filters(
                query=query,
                factor=factor,
                element="",
                favorites_only=favorites_only,
                allowed_character_keys=allowed_character_keys,
            )
        self.refresh_sections()

    def refresh_sections(self, *_args) -> None:
        visible_count = 0
        for element, section in self._sections.items():
            proxy = self._proxies[element]
            visible = (
                (not self._visible_element or self._visible_element == element)
                and proxy.rowCount() > 0
            )
            section.setVisible(visible)
            if visible:
                visible_count += int(proxy.rowCount())
                self._views[element].schedule_height_update()
        self.empty.setVisible(visible_count == 0)

    def _selection_changed(self, owner: _AutoHeightDollListView, current: QModelIndex) -> None:
        if not current.isValid():
            return
        if not self.multi_select:
            # Every element section owns an independent QListView.  Clearing only
            # the selection leaves the old view's current index alive, which can
            # make two cards look selected after returning from a modal dialog and
            # then choosing a Doll in another element section.  Clear both the
            # selection and current index so the grouped browser behaves like one
            # logical single-selection view.
            for view in self._views.values():
                if view is owner:
                    continue
                selection = view.selectionModel()
                if selection is not None:
                    selection.clearSelection()
                    selection.setCurrentIndex(
                        QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate
                    )
        entry = current.data(ENTRY_ROLE) or {}
        if entry:
            self.entrySelected.emit(dict(entry))

    def visible_entries(self) -> list[dict[str, Any]]:
        """Return entries currently visible through all section proxies."""
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for element in (*ELEMENT_ORDER, "__unknown__"):
            section = self._sections[element]
            proxy = self._proxies[element]
            if not section.isVisible():
                continue
            for row in range(proxy.rowCount()):
                index = proxy.index(row, 0)
                entry = dict(index.data(ENTRY_ROLE) or {})
                if not entry:
                    continue
                key = (str(entry.get("doll_id") or ""), str(entry.get("character_key") or ""))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(entry)
        return entries

    def selected_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for element in (*ELEMENT_ORDER, "__unknown__"):
            view = self._views[element]
            selection = view.selectionModel()
            if selection is None:
                continue
            for index in selection.selectedIndexes():
                entry = dict(index.data(ENTRY_ROLE) or {})
                if not entry:
                    continue
                key = (str(entry.get("doll_id") or ""), str(entry.get("character_key") or ""))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(entry)
        entries.sort(key=lambda row: tuple(row.get("sort_key") or (str(row.get("name") or ""),)))
        return entries

    def clear_selection(self) -> None:
        for view in self._views.values():
            selection = view.selectionModel()
            if selection is None:
                continue
            selection.clearSelection()
            if not self.multi_select:
                selection.setCurrentIndex(
                    QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate
                )

    def select_many_by(self, field: str, values: set[Any]) -> int:
        wanted = set(values)
        self.clear_selection()
        count = 0
        for element in (*ELEMENT_ORDER, "__unknown__"):
            section = self._sections[element]
            proxy = self._proxies[element]
            view = self._views[element]
            if not section.isVisible():
                continue
            selection = view.selectionModel()
            if selection is None:
                continue
            for row in range(proxy.rowCount()):
                index = proxy.index(row, 0)
                entry = index.data(ENTRY_ROLE) or {}
                if entry.get(field) in wanted:
                    selection.select(index, QItemSelectionModel.SelectionFlag.Select)
                    count += 1
        if self.multi_select:
            self.selectionChanged.emit(self.selected_entries())
        return count

    def select_by(self, field: str, value: Any) -> bool:
        if not self.multi_select:
            self.clear_selection()
        for element in (*ELEMENT_ORDER, "__unknown__"):
            section = self._sections[element]
            proxy = self._proxies[element]
            view = self._views[element]
            if not section.isVisible():
                continue
            for row in range(proxy.rowCount()):
                index = proxy.index(row, 0)
                entry = index.data(ENTRY_ROLE) or {}
                if entry.get(field) == value:
                    view.setCurrentIndex(index)
                    view.scrollTo(index)
                    return True
        return False

    def select_first(self) -> bool:
        for element in (*ELEMENT_ORDER, "__unknown__"):
            section = self._sections[element]
            proxy = self._proxies[element]
            if section.isVisible() and proxy.rowCount() > 0:
                self._views[element].setCurrentIndex(proxy.index(0, 0))
                return True
        return False
