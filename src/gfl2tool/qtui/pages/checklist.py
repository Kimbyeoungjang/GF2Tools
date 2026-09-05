from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...repository import Repository
from ...services.checklist import CATEGORIES, CATEGORY_LABELS, ChecklistStore, new_item
from ..widgets import page_layout, section_panel
from .base import DeferredRefreshPage


class ChecklistPage(DeferredRefreshPage):
    dataChanged = Signal()

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.store = ChecklistStore(repo.path.parent)
        self.lists: dict[str, QListWidget] = {}

        root = page_layout(
            self,
            "체크리스트",
            "일간은 매일, 주간은 매주 월요일, 월간은 매월 1일에 자동으로 체크가 초기화됩니다. 항목 이름과 순서는 자유롭게 수정할 수 있습니다.",
        )
        panel, layout = section_panel("체크리스트 항목 관리")
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ChecklistTabs")
        for category in CATEGORIES:
            self.tabs.addTab(self._build_category_tab(category), CATEGORY_LABELS[category])
        layout.addWidget(self.tabs)
        root.addWidget(panel, 1)

        actions = QHBoxLayout()
        reset = QPushButton("기본값으로 복원")
        save = QPushButton("체크리스트 저장")
        save.setObjectName("AccentButton")
        reset.clicked.connect(self._reset_defaults)
        save.clicked.connect(self._save)
        actions.addStretch(1)
        actions.addWidget(reset)
        actions.addWidget(save)
        root.addLayout(actions)

    def _build_category_tab(self, category: str) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(10)

        hint = QLabel("항목을 더블클릭하거나 아래의 ‘선택 편집’ 버튼으로 이름을 수정할 수 있습니다.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        listing = QListWidget()
        listing.setObjectName("ChecklistList")
        listing.setAlternatingRowColors(True)
        listing.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        listing.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.lists[category] = listing
        layout.addWidget(listing, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        add = QPushButton("항목 추가")
        edit = QPushButton("선택 편집")
        edit.setObjectName("AccentButton")
        remove = QPushButton("선택 삭제")
        up = QPushButton("위로")
        down = QPushButton("아래로")
        add.clicked.connect(lambda _checked=False, c=category: self._add_item(c))
        edit.clicked.connect(lambda _checked=False, c=category: self._edit_item(c))
        remove.clicked.connect(lambda _checked=False, c=category: self._remove_item(c))
        up.clicked.connect(lambda _checked=False, c=category: self._move_item(c, -1))
        down.clicked.connect(lambda _checked=False, c=category: self._move_item(c, 1))
        row.addWidget(add)
        row.addWidget(edit)
        row.addWidget(remove)
        row.addStretch(1)
        row.addWidget(up)
        row.addWidget(down)
        layout.addLayout(row)
        return tab

    def _edit_item(self, category: str) -> None:
        listing = self.lists[category]
        item = listing.currentItem()
        if item is None:
            QMessageBox.information(self, "체크리스트 편집", "먼저 수정할 항목을 선택해 주세요.")
            return
        listing.editItem(item)

    @staticmethod
    def _make_item(payload: dict) -> QListWidgetItem:
        item = QListWidgetItem(str(payload.get("label") or "새 항목"))
        item.setData(Qt.ItemDataRole.UserRole, str(payload.get("id") or new_item()["id"]))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if payload.get("checked") else Qt.CheckState.Unchecked)
        return item

    def refresh(self) -> None:
        payload = self.store.load()
        for category in CATEGORIES:
            listing = self.lists[category]
            listing.blockSignals(True)
            listing.clear()
            for row in payload["items"][category]:
                listing.addItem(self._make_item(row))
            listing.blockSignals(False)

    def _add_item(self, category: str) -> None:
        listing = self.lists[category]
        item = self._make_item(new_item())
        listing.addItem(item)
        listing.setCurrentItem(item)
        listing.editItem(item)

    def _remove_item(self, category: str) -> None:
        listing = self.lists[category]
        row = listing.currentRow()
        if row >= 0:
            listing.takeItem(row)

    def _move_item(self, category: str, direction: int) -> None:
        listing = self.lists[category]
        row = listing.currentRow()
        target = row + int(direction)
        if row < 0 or target < 0 or target >= listing.count():
            return
        item = listing.takeItem(row)
        listing.insertItem(target, item)
        listing.setCurrentRow(target)

    def _collect(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for category in CATEGORIES:
            listing = self.lists[category]
            rows: list[dict] = []
            for index in range(listing.count()):
                item = listing.item(index)
                label = item.text().strip()
                if not label:
                    continue
                rows.append({
                    "id": str(item.data(Qt.ItemDataRole.UserRole) or new_item()["id"]),
                    "label": label,
                    "checked": item.checkState() == Qt.CheckState.Checked,
                })
            out[category] = rows
        return out

    def _save(self) -> None:
        self.store.replace_items(self._collect())
        self.dataChanged.emit()
        QMessageBox.information(self, "체크리스트", "체크리스트를 저장했습니다.")

    def _reset_defaults(self) -> None:
        if QMessageBox.question(
            self,
            "체크리스트 기본값 복원",
            "일간/주간/월간 항목을 1.0.4 기본 체크리스트로 되돌릴까요? 현재 체크 상태도 초기화됩니다.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.reset_defaults()
        self.refresh()
        self.dataChanged.emit()
