from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from ..formation_widgets import PortraitLabel
from ..images import PortraitLoader
from ..widgets import dialog_layout, page_title


class DollImagePickerDialog(QDialog):
    """Pick one REST/offline image variant while previewing the real cached asset."""

    def __init__(self, portraits: PortraitLoader, options: list[tuple[str, Path]], *, title: str, current: Path | None = None, parent=None):
        super().__init__(parent)
        self.portraits = portraits
        self.options = [(str(label), Path(path)) for label, path in options if path]
        self.result_path: Path | None = None
        self._requested = ""
        self.setWindowTitle(title)
        self.resize(900, 720)
        self.setMinimumSize(720, 560)
        root = dialog_layout(self)
        root.addWidget(page_title(title))

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(270)
        for label, path in self.options:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(path.name)
            self.list.addItem(item)
        body.addWidget(self.list)

        right = QVBoxLayout()
        self.preview = PortraitLabel()
        self.preview.setMinimumSize(430, 500)
        right.addWidget(self.preview, 1)
        self.filename = QLabel("")
        self.filename.setObjectName("Muted")
        self.filename.setWordWrap(True)
        right.addWidget(self.filename)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("이 이미지 사용")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("AccentButton")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.portraits.imageReady.connect(self._image_ready)
        self.list.currentRowChanged.connect(self._selected)
        selected = 0
        if current is not None:
            for index, (_label, path) in enumerate(self.options):
                if str(path) == str(current):
                    selected = index
                    break
        if self.options:
            self.list.setCurrentRow(selected)
        else:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            self.filename.setText("선택할 수 있는 동기화 이미지가 없습니다.")

    def _selected(self, row: int) -> None:
        if not (0 <= row < len(self.options)):
            return
        _label, path = self.options[row]
        self._requested = str(path)
        self.filename.setText(path.name)
        image = self.portraits.get(path)
        self.preview.set_image(image, 520, 560)
        if image is None:
            self.portraits.request(path)

    def _image_ready(self, path: str, image) -> None:
        if path == self._requested:
            self.preview.set_image(image, 520, 560)

    def _accept(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.options):
            self.result_path = self.options[row][1]
            self.accept()
