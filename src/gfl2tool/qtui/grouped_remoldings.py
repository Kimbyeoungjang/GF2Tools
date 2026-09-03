from __future__ import annotations

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from . import theme
from .widgets import configure_tree_widget


class RemoldingGroupedView(QWidget):
    """Compact remolding browser grouped by main option.

    Top-level rows are only summaries; every physical remolding stays visible as
    a child row with its class and complete option string. ``rowSelected`` is
    emitted for the selected physical item so Inventory can show a persistent
    detail panel instead of making the user infer details from a group count.
    """

    rowSelected = Signal(dict)

    def __init__(
        self,
        main_option_names: dict[str, str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.main_option_names = dict(main_option_names)
        self._main_order = {key: index for index, key in enumerate(main_option_names)}
        self._rows: list[dict[str, Any]] = []
        self._query = ""
        self._main_option = ""
        self._factor = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setObjectName("RemoldingGroupedTree")
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["주옵션 / 리몰딩", "클래스", "전체 옵션", "UID"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(18)
        self.tree.setAnimated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        configure_tree_widget(self.tree, widths={0: 210, 1: 90, 2: 440, 3: 150})
        self.tree.currentItemChanged.connect(self._current_item_changed)
        root.addWidget(self.tree, 1)

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]
        self._rebuild()

    def set_filters(
        self,
        *,
        query: str = "",
        main_option: str = "",
        factor: str = "",
    ) -> None:
        state = (query.casefold().strip(), str(main_option or ""), str(factor or ""))
        if state == (self._query, self._main_option, self._factor):
            return
        self._query, self._main_option, self._factor = state
        self._rebuild()

    def _filtered_rows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self._rows:
            if self._query and self._query not in str(row.get("search_text") or "").casefold():
                continue
            if self._main_option and str(row.get("main_option_key") or "") != self._main_option:
                continue
            if self._factor and str(row.get("primary_factor") or "") != self._factor:
                continue
            out.append(row)
        return out

    def _current_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            self.rowSelected.emit({})
            return
        payload = current.data(0, Qt.ItemDataRole.UserRole)
        self.rowSelected.emit(dict(payload) if isinstance(payload, dict) else {})

    def _rebuild(self) -> None:
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in self._filtered_rows():
                groups[str(row.get("main_option_key") or "__unknown__")].append(row)

            keys = sorted(
                groups,
                key=lambda key: (
                    self._main_order.get(key, len(self._main_order) + 1),
                    self.main_option_names.get(key, "주옵션 미확인").casefold(),
                ),
            )
            if not keys:
                empty = QTreeWidgetItem(["조건에 맞는 리몰딩이 없습니다.", "", "", ""])
                empty.setForeground(0, QColor(theme.MUTED))
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.tree.addTopLevelItem(empty)
                self.rowSelected.emit({})
                return

            first_child: QTreeWidgetItem | None = None
            for key in keys:
                rows = groups[key]
                label = self.main_option_names.get(key, "주옵션 미확인")
                group = QTreeWidgetItem([f"{label}  ·  {len(rows):,}개", "", "", ""])
                font = QFont(group.font(0))
                font.setBold(True)
                group.setFont(0, font)
                factor = str(rows[0].get("primary_factor") or "") if rows else ""
                group.setForeground(0, QColor(theme.FACTOR_COLORS.get(factor, theme.ACCENT)))
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.tree.addTopLevelItem(group)
                group.setFirstColumnSpanned(True)

                for row in sorted(
                    rows,
                    key=lambda item: (
                        str(item.get("class_label") or ""),
                        str(item.get("attributes") or ""),
                        str(item.get("uid") or ""),
                    ),
                ):
                    child = QTreeWidgetItem(
                        [
                            str(row.get("name") or label),
                            str(row.get("class_label") or "—"),
                            str(row.get("attributes") or row.get("sub_attributes") or "—"),
                            str(row.get("uid") or ""),
                        ]
                    )
                    child.setToolTip(2, str(row.get("attributes") or "—"))
                    child.setData(0, Qt.ItemDataRole.UserRole, dict(row))
                    group.addChild(child)
                    if first_child is None:
                        first_child = child
                group.setExpanded(True)

            # Make the first physical row immediately visible/selected. This
            # avoids the old impression that only "공격 강화 56개" existed.
            if first_child is not None:
                self.tree.setCurrentItem(first_child)
        finally:
            self.tree.setUpdatesEnabled(True)
