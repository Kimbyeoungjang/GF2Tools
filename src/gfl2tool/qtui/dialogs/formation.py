from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QTableView,
)

from ...repository import Repository
from ...services.formations import FormationService
from ..models import DataTableModel, TABLE_ROW_ROLE
from ..widgets import configure_table_view, dialog_layout, show_error


class GameFormationImportDialog(QDialog):
    """Copy one imported game formation into the editable planner."""

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.service = FormationService(repo)
        self.imported_plan_id: int | None = None
        self.setWindowTitle("게임 제대 가져오기")
        self.resize(820, 560)
        self.setMinimumSize(680, 440)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)
        title = QLabel("게임 제대 선택")
        title.setObjectName("PageTitle")
        title.setToolTip("선택한 게임 편성을 새 제대 계획으로 복사합니다. 게임 안의 원본 편성은 변경하지 않습니다.")
        root.addWidget(title)

        self.table = QTableView()
        configure_table_view(self.table, widths={0:180}, select_rows=True)
        root.addWidget(self.table, 1)

        rename = QHBoxLayout()
        rename.addWidget(QLabel("새 계획 이름"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("비우면 원래 이름 + 복사본")
        rename.addWidget(self.name, 1)
        root.addLayout(rename)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("가져오기")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.table.doubleClicked.connect(lambda _idx: self._import())
        self._load()

    def _load(self) -> None:
        prepared = []
        for row in self.service.list_game_formations():
            names = list(row.get("member_names") or [])
            prepared.append({
                "formation_id": int(row["id"]),
                "name": str(row.get("name") or f"제대 {row['id']}"),
                "members": " · ".join(names) or "—",
                "member_count": len(names),
            })
        model = DataTableModel(
            prepared, [("게임 제대", "name"), ("구성 인형", "members"), ("인원", "member_count")], self,
            sort_getters=["name", "members", "member_count"],
        )
        self.table.setModel(model)
        configure_table_view(self.table, widths={0: 180}, select_rows=True)
        if model.rowCount():
            self.table.selectRow(0)

    def _import(self) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "게임 제대 가져오기", "가져올 게임 제대를 선택하세요.")
            return
        row = self.table.model().index(idx.row(), 0).data(TABLE_ROW_ROLE) or {}
        formation_id = row.get("formation_id")
        if formation_id is None:
            return
        try:
            self.imported_plan_id = self.service.import_game_formation(
                int(formation_id),
                self.name.text().strip() or None,
            )
        except Exception as exc:
            show_error(self, "가져오기 실패", str(exc))
            return
        self.accept()
