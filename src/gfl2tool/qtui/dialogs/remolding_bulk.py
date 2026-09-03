from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
)

from ..widgets import dialog_layout, page_title


class RemoldingBulkSettingsDialog(QDialog):
    """Collect a scoped bulk update without exposing internal profile storage."""

    def __init__(self, categories: list[str], factor_names: dict[str, str], selected_name: str, parent=None):
        super().__init__(parent)
        self.categories = list(categories)
        self.factor_names = dict(factor_names)
        self.selected_name = str(selected_name or "")
        self.setWindowTitle("리몰딩 설정 일괄 적용")
        self.resize(560, 430)
        root = dialog_layout(self)
        root.addWidget(page_title("리몰딩 설정 일괄 적용"))
        note = QLabel(
            "사용자 카테고리 또는 역할 단위로 여러 인형의 설정을 한 번에 바꿉니다. "
            "목표 스탯/장착칸 복사는 현재 선택 인형의 값을 기준으로 하며, 대상 인형에 사용할 수 없는 목표는 자동으로 제외합니다."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        self.scope_kind = QComboBox()
        self.scope_kind.addItem("사용자 카테고리", "category")
        self.scope_kind.addItem("인형 역할", "role")
        self.scope_value = QComboBox()
        self.scope_kind.currentIndexChanged.connect(self._refresh_scope_values)
        form.addRow("적용 단위", self.scope_kind)
        form.addRow("대상", self.scope_value)

        self.change_level = QCheckBox("계산 레벨 변경")
        self.change_level.setChecked(True)
        self.level = QSpinBox()
        self.level.setRange(0, 60)
        self.level.setSpecialValueText("개별 설정 해제")
        self.level.setValue(0)
        form.addRow(self.change_level, self.level)

        source = self.selected_name or "선택 인형 없음"
        self.copy_targets = QCheckBox(f"{source}의 목표 스탯 복사")
        self.copy_slots = QCheckBox(f"{source}의 장착칸 구성 복사")
        if not self.selected_name:
            self.copy_targets.setEnabled(False)
            self.copy_slots.setEnabled(False)
        form.addRow("선택 인형 기준", self.copy_targets)
        form.addRow("", self.copy_slots)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("일괄 적용")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_scope_values()

    def _refresh_scope_values(self) -> None:
        current = self.scope_value.currentData()
        self.scope_value.clear()
        if self.scope_kind.currentData() == "role":
            for key in ("sentinel", "vanguard", "bulwark", "support"):
                self.scope_value.addItem(str(self.factor_names.get(key, key)), key)
        else:
            for name in self.categories:
                self.scope_value.addItem(name, name)
        index = self.scope_value.findData(current)
        if index >= 0:
            self.scope_value.setCurrentIndex(index)

    def values(self) -> dict[str, object]:
        return {
            "scope_kind": str(self.scope_kind.currentData() or "category"),
            "scope_value": str(self.scope_value.currentData() or ""),
            "change_level": self.change_level.isChecked(),
            "level": int(self.level.value()),
            "copy_targets": self.copy_targets.isChecked(),
            "copy_slots": self.copy_slots.isChecked(),
        }
