from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
)

from . import theme

ENTRY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
TABLE_ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 2
TABLE_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 3
TABLE_SEARCH_ROLE = int(Qt.ItemDataRole.UserRole) + 4


class DataTableModel(QAbstractTableModel):
    """Read-only row model without per-cell QStandardItem allocations.

    ``columns`` contains ``(header, getter)`` pairs. A getter can be a row key
    or a callable. Optional sort getters keep numeric columns numerically sorted
    even when their display text contains commas/units. The whole row is exposed
    through ``TABLE_ROW_ROLE`` so dialogs can retrieve payloads without storing
    hidden QStandardItems.
    """

    def __init__(
        self,
        rows: Sequence[dict[str, Any]] | None,
        columns: Sequence[tuple[str, str | Callable[[dict[str, Any]], Any]]],
        parent: QObject | None = None,
        *,
        sort_getters: Sequence[str | Callable[[dict[str, Any]], Any] | None] | None = None,
        search_getter: str | Callable[[dict[str, Any]], Any] | None = None,
    ):
        super().__init__(parent)
        self._rows = list(rows or [])
        self._columns = list(columns)
        self._sort_getters = list(sort_getters or [None] * len(self._columns))
        if len(self._sort_getters) < len(self._columns):
            self._sort_getters.extend([None] * (len(self._columns) - len(self._sort_getters)))
        self._search_getter = search_getter
        self._sort_column: int | None = None
        self._sort_order = Qt.SortOrder.AscendingOrder

    @staticmethod
    def _value(row: dict[str, Any], getter):
        if getter is None:
            return None
        if callable(getter):
            return getter(row)
        return row.get(str(getter))

    def set_rows(self, rows: Sequence[dict[str, Any]] | None) -> None:
        self.beginResetModel()
        self._rows = list(rows or [])
        if self._sort_column is not None:
            self._sort_rows(self._sort_column, self._sort_order)
        self.endResetModel()

    def rows(self) -> list[dict[str, Any]]:
        return self._rows

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if (
            not index.isValid()
            or not (0 <= index.row() < len(self._rows))
            or not (0 <= index.column() < len(self._columns))
        ):
            return None
        row = self._rows[index.row()]
        if role == TABLE_ROW_ROLE:
            return row
        if role == TABLE_SEARCH_ROLE:
            value = self._value(row, self._search_getter)
            return "" if value is None else str(value)
        if role == TABLE_SORT_ROLE:
            getter = self._sort_getters[index.column()]
            value = self._value(
                row,
                getter if getter is not None else self._columns[index.column()][1],
            )
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            value = self._value(row, self._columns[index.column()][1])
            return "" if value is None else str(value)
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self._columns)
        ):
            return self._columns[section][0]
        return super().headerData(section, orientation, role)

    def _sort_rows(self, column: int, order) -> None:
        getter = self._sort_getters[column]
        if getter is None:
            getter = self._columns[column][1]

        def normalized(row: dict[str, Any]):
            value = self._value(row, getter)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return (0, float(value))
            return (1, str(value).casefold())

        present = [row for row in self._rows if self._value(row, getter) is not None]
        missing = [row for row in self._rows if self._value(row, getter) is None]
        present.sort(key=normalized, reverse=order == Qt.SortOrder.DescendingOrder)
        self._rows[:] = present + missing

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        if not (0 <= column < len(self._columns)):
            return
        self._sort_column = int(column)
        self._sort_order = order
        if len(self._rows) < 2:
            return
        self.layoutAboutToBeChanged.emit()
        self._sort_rows(column, order)
        self.layoutChanged.emit()


class TextFilterProxy(QSortFilterProxyModel):
    """Cheap single-string filter over a precomputed row search field."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._query = ""
        self.setDynamicSortFilter(True)
        self.setSortRole(TABLE_SORT_ROLE)

    def set_query(self, query: str) -> None:
        normalized = str(query or "").strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._query:
            return True
        model = self.sourceModel()
        if model is None:
            return False
        text = model.data(model.index(source_row, 0, source_parent), TABLE_SEARCH_ROLE)
        return self._query in str(text or "").casefold()



def _contiguous_ranges(rows: Sequence[int]):
    if not rows:
        return
    start = previous = int(rows[0])
    for value in rows[1:]:
        current = int(value)
        if current != previous + 1:
            yield start, previous
            start = current
        previous = current
    yield start, previous


class DollListModel(QAbstractListModel):
    favoriteRequested = Signal(int)

    def __init__(
        self,
        entries: list[dict[str, Any]] | None = None,
        parent: QObject | None = None,
        *,
        portraits=None,
    ):
        super().__init__(parent)
        self._entries = list(entries or [])
        self._portraits = portraits
        self._path_rows: dict[str, list[int]] = {}
        self._doll_rows: dict[int, int] = {}
        self._fallback_images: dict[str, QImage] = {}
        self._fallback_pixmaps: OrderedDict[tuple[str, int, int, int], QPixmap] = OrderedDict()
        self._reindex_paths()
        if self._portraits is not None:
            self._portraits.imageReady.connect(self.set_image)

    def _reindex_paths(self) -> None:
        path_rows: dict[str, list[int]] = {}
        doll_rows: dict[int, int] = {}
        for row, entry in enumerate(self._entries):
            path = str(entry.get("portrait_path") or "")
            if path:
                path_rows.setdefault(path, []).append(row)
            doll_id = entry.get("doll_id")
            if doll_id is not None:
                try:
                    doll_rows[int(doll_id)] = row
                except (TypeError, ValueError):
                    pass
        self._path_rows = path_rows
        self._doll_rows = doll_rows

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self._reindex_paths()
        active_paths = set(self._path_rows)
        self._fallback_images = {k: v for k, v in self._fallback_images.items() if k in active_paths}
        self._fallback_pixmaps = OrderedDict(
            (k, v) for k, v in self._fallback_pixmaps.items() if k[0] in active_paths
        )
        self.endResetModel()

    def entry(self, row: int) -> dict[str, Any] | None:
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.get("name", "")
        if role == ENTRY_ROLE:
            return entry
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{entry.get('name','')}\n{entry.get('factor_label','')} · {entry.get('element_label','')}"
        return None

    def set_image(self, path: str, image: QImage) -> None:
        key = str(path)
        rows = self._path_rows.get(key)
        if not rows:
            return
        if self._portraits is None:
            self._fallback_images[key] = image
        # One contiguous signal is substantially cheaper than scanning the whole
        # roster and emitting one dataChanged per matching row. Duplicate portrait
        # paths are rare but still handled correctly.
        for first, last in _contiguous_ranges(rows):
            self.dataChanged.emit(self.index(first, 0), self.index(last, 0), [Qt.ItemDataRole.DecorationRole])

    def set_favorite(self, doll_id: int, favorite: bool) -> bool:
        did = int(doll_id)
        row = self._doll_rows.get(did)
        if row is None:
            return False
        entry = self._entries[row]
        entry["favorite"] = bool(favorite)
        entry["sort_key"] = (0 if favorite else 1, str(entry.get("name") or "").casefold(), did)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [ENTRY_ROLE, Qt.ItemDataRole.DisplayRole])
        return True

    def pixmap_for(self, entry: dict[str, Any], size: QSize, *, dpr: float = 1.0) -> QPixmap | None:
        path = str(entry.get("portrait_path") or "")
        if not path:
            return None
        if self._portraits is not None:
            return self._portraits.pixmap(path, size, dpr=dpr)
        dpr = max(1.0, float(dpr))
        physical_width = max(1, int(round(size.width() * dpr)))
        physical_height = max(1, int(round(size.height() * dpr)))
        key = (path, physical_width, physical_height, max(100, int(round(dpr * 100))))
        cached = self._fallback_pixmaps.get(key)
        if cached is not None:
            self._fallback_pixmaps.move_to_end(key)
            return cached
        image = self._fallback_images.get(path)
        if image is None:
            return None
        pix = QPixmap.fromImage(image).scaled(
            physical_width,
            physical_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix.setDevicePixelRatio(dpr)
        self._fallback_pixmaps[key] = pix
        self._fallback_pixmaps.move_to_end(key)
        while len(self._fallback_pixmaps) > 64:
            self._fallback_pixmaps.popitem(last=False)
        return pix


class DollFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.query = ""
        self.factor = ""
        self.element = ""
        self.favorites_only = False
        self.allowed_character_keys: frozenset[str] | None = None
        self.setDynamicSortFilter(True)

    def set_filters(
        self,
        *,
        query: str | None = None,
        factor: str | None = None,
        element: str | None = None,
        favorites_only: bool | None = None,
        allowed_character_keys: set[str] | frozenset[str] | None = None,
    ) -> None:
        changed = False
        if query is not None:
            value = query.strip().casefold()
            if value != self.query:
                self.query = value
                changed = True
        if factor is not None and factor != self.factor:
            self.factor = factor
            changed = True
        if element is not None and element != self.element:
            self.element = element
            changed = True
        if favorites_only is not None and bool(favorites_only) != self.favorites_only:
            self.favorites_only = bool(favorites_only)
            changed = True
        normalized_keys = (
            None
            if allowed_character_keys is None
            else frozenset(str(key) for key in allowed_character_keys if str(key))
        )
        if normalized_keys != self.allowed_character_keys:
            self.allowed_character_keys = normalized_keys
            changed = True
        if changed:
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None:
            return False
        idx = model.index(source_row, 0, source_parent)
        entry = model.data(idx, ENTRY_ROLE) or {}
        if (
            self.allowed_character_keys is not None
            and str(entry.get("character_key") or "") not in self.allowed_character_keys
        ):
            return False
        if self.favorites_only and not bool(entry.get("favorite")):
            return False
        if self.factor and str(entry.get("factor_type") or "") != self.factor:
            return False
        if self.element and str(entry.get("element_type") or "") != self.element:
            return False
        if self.query and self.query not in str(entry.get("search_text") or "").casefold():
            return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        le = self.sourceModel().data(left, ENTRY_ROLE) or {}
        re = self.sourceModel().data(right, ENTRY_ROLE) or {}
        lk = le.get("sort_key") or (
            0 if le.get("favorite") else 1,
            str(le.get("name") or "").casefold(),
            int(le.get("doll_id") or 0),
        )
        rk = re.get("sort_key") or (
            0 if re.get("favorite") else 1,
            str(re.get("name") or "").casefold(),
            int(re.get("doll_id") or 0),
        )
        return lk < rk


class DollCardDelegate(QStyledItemDelegate):
    favoriteClicked = Signal(int)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        card_size: QSize | None = None,
        show_favorite: bool = True,
        text_scale: float = 1.0,
    ):
        super().__init__(parent)
        self.card_size = card_size or QSize(128, 138)
        self.show_favorite = bool(show_favorite)
        self.text_scale = max(0.85, min(1.3, float(text_scale)))

    def sizeHint(self, option, index):  # noqa: N802
        return self.card_size

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        entry = index.data(ENTRY_ROLE) or {}
        rect = option.rect.adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.BORDER), 4 if selected else 1))
        painter.setBrush(QColor(theme.SELECT if selected else theme.PANEL_ALT))
        painter.drawRoundedRect(rect, 7, 7)

        strip_y = rect.bottom() - 4
        sub_height = max(18, int(round(19 * self.text_scale)))
        strip_gap = max(7, int(round(10 * self.text_scale)))
        sub_y = strip_y - sub_height - strip_gap
        name_height = max(22, int(round(23 * self.text_scale)))
        name_gap = max(3, int(round(5 * self.text_scale)))
        name_y = sub_y - name_height - name_gap
        image_height = max(64, name_y - rect.top() - 12)
        image_rect = QRect(rect.left() + 9, rect.top() + 6, rect.width() - 18, image_height)
        model = index.model()
        source_model = model.sourceModel() if hasattr(model, "sourceModel") else model
        device = painter.device()
        dpr = (
            float(device.devicePixelRatioF())
            if device is not None and hasattr(device, "devicePixelRatioF")
            else 1.0
        )
        pix = (
            source_model.pixmap_for(entry, image_rect.size(), dpr=dpr)
            if hasattr(source_model, "pixmap_for")
            else None
        )
        if pix and not pix.isNull():
            logical = pix.deviceIndependentSize()
            target = QRect(
                0,
                0,
                max(1, int(round(logical.width()))),
                max(1, int(round(logical.height()))),
            )
            target.moveCenter(image_rect.center())
            painter.drawPixmap(target, pix)
        else:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, "이미지 없음")

        painter.setPen(QColor(theme.TEXT))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(9.4 * self.text_scale)
        painter.setFont(font)
        name_rect = QRect(rect.left() + 6, name_y, rect.width() - 12, name_height)
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            str(entry.get("name") or ""),
        )
        painter.setPen(QColor(theme.MUTED))
        font.setBold(False)
        font.setPointSizeF(8.2 * self.text_scale)
        painter.setFont(font)
        sub = f"{entry.get('factor_label','')} · {entry.get('element_label','')}"
        painter.drawText(
            QRect(rect.left() + 6, sub_y, rect.width() - 12, sub_height),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            sub,
        )

        if self.show_favorite:
            painter.setPen(QColor(theme.ACCENT if entry.get("favorite") else theme.MUTED))
            font.setBold(True)
            font.setPointSizeF(12)
            painter.setFont(font)
            painter.drawText(
                QRect(rect.right() - 25, rect.top() + 3, 20, 20),
                Qt.AlignmentFlag.AlignCenter,
                "★" if entry.get("favorite") else "☆",
            )

        accent = theme.ELEMENT_COLORS.get(str(entry.get("element_type") or ""), theme.BORDER)
        painter.fillRect(QRect(rect.left() + 1, strip_y, rect.width() - 2, 3), QColor(accent))
        painter.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if self.show_favorite and event.type() == QEvent.Type.MouseButtonRelease:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            rect = option.rect.adjusted(4, 4, -4, -4)
            if QRect(rect.right()-30, rect.top(), 30, 30).contains(pos):
                entry = index.data(ENTRY_ROLE) or {}
                if entry.get("doll_id") is not None:
                    self.favoriteClicked.emit(int(entry["doll_id"]))
                    return True
        return super().editorEvent(event, model, option, index)


class DollListView(QListView):
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(18)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(18)
        self.setGridSize(QSize(132, 142))
