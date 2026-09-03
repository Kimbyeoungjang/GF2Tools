from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
)

from ...services.doll_categories import DollCategoryStore
from ..widgets import dialog_layout


class DollCategoryAssignDialog(QDialog):
    """Choose an existing doll category or create a new one explicitly."""

    def __init__(self, store: DollCategoryStore, *, count: int = 1, parent=None):
        super().__init__(parent)
        self.store = store
        self.result_name = ""
        self.setWindowTitle("인형 카테고리 지정")
        self.setMinimumWidth(440)
        root = dialog_layout(self)
        root.addWidget(QLabel(f"선택한 인형 {max(1, int(count))}명을 어디에 추가할지 선택하세요."))

        self.existing_radio = QRadioButton("기존 카테고리에 추가")
        self.new_radio = QRadioButton("새 카테고리 만들기")
        names = self.store.names()
        self.existing_radio.setChecked(bool(names))
        self.new_radio.setChecked(not names)
        root.addWidget(self.existing_radio)

        existing_row = QHBoxLayout()
        existing_row.addSpacing(24)
        self.existing = QComboBox()
        for name in names:
            self.existing.addItem(f"{name} ({len(self.store.keys(name))})", name)
        self.existing.setEnabled(bool(names))
        existing_row.addWidget(self.existing, 1)
        root.addLayout(existing_row)

        root.addWidget(self.new_radio)
        new_row = QHBoxLayout()
        new_row.addSpacing(24)
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("새 카테고리 이름")
        self.new_name.setEnabled(not names)
        self.new_name.setMaxLength(48)
        new_row.addWidget(self.new_name, 1)
        root.addLayout(new_row)

        self.existing_radio.toggled.connect(self._mode_changed)
        self.new_radio.toggled.connect(self._mode_changed)
        self.new_name.returnPressed.connect(self._accept)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("추가")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _mode_changed(self, *_args) -> None:
        existing = self.existing_radio.isChecked() and self.existing.count() > 0
        self.existing.setEnabled(existing)
        self.new_name.setEnabled(not existing)
        if not existing:
            self.new_name.setFocus()

    def _accept(self) -> None:
        if self.existing_radio.isChecked() and self.existing.count() > 0:
            name = str(self.existing.currentData() or "").strip()
        else:
            name = " ".join(self.new_name.text().strip().split())
        if not name:
            self.new_name.setFocus()
            return
        self.result_name = name
        self.accept()
