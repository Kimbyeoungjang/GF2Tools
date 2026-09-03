from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ...services.doll_skill_cycles import DollSkillCycleStore, MAX_TURNS
from ..widgets import configure_table_view, dialog_layout, page_title


class DollSkillCycleDialog(QDialog):
    """Edit one Doll's simple T1..Tn repeating skill cycle."""

    def __init__(
        self,
        store: DollSkillCycleStore,
        *,
        doll_id: int,
        doll_name: str,
        parent=None,
        sync_from_label: str = "",
        sync_from: Callable[[], list[str] | None] | None = None,
        sync_to_label: str = "",
        sync_to: Callable[[list[str]], object] | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.doll_id = int(doll_id)
        self.doll_name = str(doll_name or f"인형 {doll_id}")
        self._sync_from = sync_from
        self._sync_to = sync_to

        self.setWindowTitle(f"{self.doll_name} · 스킬 사이클")
        self.resize(760, 660)
        self.setMinimumSize(620, 500)
        root = dialog_layout(self)
        root.addWidget(page_title(f"{self.doll_name} · T1~Tn 스킬 사이클"))

        intro = QLabel(
            "이 인형의 반복 행동을 T1부터 원하는 만큼 적습니다. 돌파 수와는 구분하지 않습니다. "
            "여러 T를 Shift/Ctrl로 선택한 뒤 ‘선택 구간 복제’를 누르면 선택한 주기를 표 아래에 그대로 이어 붙입니다. "
            "택틱의 수동 작성 사이클은 자동 적용이 덮어쓰지 않습니다."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["단계", "이 인형의 행동"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        configure_table_view(self.table, widths={0: 78}, sorting=False, stretch_last=True)
        root.addWidget(self.table, 1)

        edit_row = QHBoxLayout()
        add = QPushButton("+ 다음 T 추가")
        duplicate = QPushButton("선택 구간 복제")
        remove = QPushButton("선택 T 삭제")
        clear = QPushButton("전체 비우기")
        clear.setObjectName("DangerButton")
        add.clicked.connect(self._add_turn)
        duplicate.clicked.connect(self._duplicate_selected)
        remove.clicked.connect(self._remove_selected)
        clear.clicked.connect(self._clear)
        edit_row.addWidget(add)
        edit_row.addWidget(duplicate)
        edit_row.addWidget(remove)
        edit_row.addStretch(1)
        edit_row.addWidget(clear)
        root.addLayout(edit_row)

        if sync_from is not None or sync_to is not None:
            sync_row = QHBoxLayout()
            sync_hint = QLabel("필요할 때만 다른 사이클과 값을 복사합니다. 두 설정은 기본적으로 서로 독립적입니다.")
            sync_hint.setObjectName("Muted")
            sync_row.addWidget(sync_hint, 1)
            if sync_from is not None:
                button = QPushButton(sync_from_label or "다른 사이클 불러오기")
                button.clicked.connect(self._sync_from_actions)
                sync_row.addWidget(button)
            if sync_to is not None:
                button = QPushButton(sync_to_label or "다른 사이클에 저장")
                button.clicked.connect(self._sync_to_actions)
                sync_row.addWidget(button)
            root.addLayout(sync_row)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("취소")
        save = QPushButton("저장")
        save.setObjectName("AccentButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._load(self.store.actions_for(self.doll_id))

    def _actions(self) -> list[str]:
        actions: list[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            actions.append(str(item.text() if item is not None else "").strip()[:240])
        while actions and not actions[-1]:
            actions.pop()
        return actions

    def _load(self, actions: list[str]) -> None:
        count = max(1, len(actions))
        self.table.setRowCount(count)
        for row in range(count):
            turn = QTableWidgetItem(f"T{row + 1}")
            turn.setFlags(turn.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, turn)
            self.table.setItem(row, 1, QTableWidgetItem(actions[row] if row < len(actions) else ""))

    def _renumber(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, 0, item)
            item.setText(f"T{row + 1}")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def _add_turn(self) -> None:
        if self.table.rowCount() >= MAX_TURNS:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._renumber()
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setCurrentCell(row, 1)
        self.table.editItem(self.table.item(row, 1))

    def _selected_rows(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        return rows

    def _duplicate_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        room = MAX_TURNS - self.table.rowCount()
        if room <= 0:
            QMessageBox.information(self, "스킬 사이클", f"최대 T{MAX_TURNS}까지 저장할 수 있습니다.")
            return
        values = [
            str(self.table.item(row, 1).text() if self.table.item(row, 1) else "")
            for row in rows
        ][:room]
        start = self.table.rowCount()
        for value in values:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 1, QTableWidgetItem(value))
        self._renumber()
        self.table.clearSelection()
        for row in range(start, self.table.rowCount()):
            self.table.selectRow(row)

    def _remove_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for row in reversed(rows):
            if 0 <= row < self.table.rowCount():
                self.table.removeRow(row)
        if self.table.rowCount() == 0:
            self.table.setRowCount(1)
            self.table.setItem(0, 1, QTableWidgetItem(""))
        self._renumber()

    def _clear(self) -> None:
        self.table.setRowCount(1)
        self.table.setItem(0, 1, QTableWidgetItem(""))
        self._renumber()

    def _sync_from_actions(self) -> None:
        if self._sync_from is None:
            return
        try:
            actions = self._sync_from()
        except Exception as exc:
            QMessageBox.warning(self, "스킬 사이클 동기화", str(exc))
            return
        if actions is None:
            return
        self._load(list(actions))

    def _sync_to_actions(self) -> None:
        if self._sync_to is None:
            return
        try:
            result = self._sync_to(self._actions())
        except Exception as exc:
            QMessageBox.warning(self, "스킬 사이클 동기화", str(exc))
            return
        if result is False:
            return
        QMessageBox.information(self, "스킬 사이클 동기화", "현재 사이클을 선택한 대상에 복사했습니다.")

    def _accept(self) -> None:
        self.store.set_actions(self.doll_id, self._actions())
        self.accept()
