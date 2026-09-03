from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from ... import reference
from ...repository import Repository
from ...services.remolding_recommendation import RemoldingRecommendationService
from ..theme import FACTOR_ORDER
from ..widgets import dialog_layout, show_error


class DummyCharactersDialog(QDialog):
    SHAPES = ("단일형", "광역형", "혼합형")
    SCALES = ("공격계수", "체력계수", "방어계수", "전환형체력계수", "체공계수")

    def __init__(self, repo: Repository, selected_key: str | None = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.svc = RemoldingRecommendationService(repo)
        self.selected_key = selected_key
        self.changed = False
        self.extra_tags: list[str] = []
        rules = reference.remolding_rules()
        self.factor_names = rules.get("factor_names", {})
        self.element_names = rules.get("element_names", {})

        self.setWindowTitle("캐릭터 장착칸 · 더미")
        self.resize(820, 690)
        self.setMinimumSize(720, 580)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)
        self._build_character_selector(root)
        self._build_profile_form(root)
        self._build_slot_grid(root)
        self._build_actions(root)
        self._connect_signals()
        self._reload_combo(self.selected_key)

    def _build_character_selector(self, root) -> None:
        top = QHBoxLayout()
        top.addWidget(QLabel("편집 대상"))
        self.combo = QComboBox()
        top.addWidget(self.combo, 1)
        root.addLayout(top)

    def _build_profile_form(self, root) -> None:
        form = QFormLayout()
        self.name = QLineEdit()

        self.role = QComboBox()
        self.role.addItems(
            [str(self.factor_names.get(key, key)) for key in FACTOR_ORDER]
        )

        self.element = QComboBox()
        self.element.addItems([str(value) for value in self.element_names.values()])

        self.shape = QComboBox()
        self.shape.addItems(self.SHAPES)
        self.scale = QComboBox()
        self.scale.addItems(self.SCALES)

        self.level = QSpinBox()
        self.level.setRange(0, 60)
        self.level.setSpecialValueText("전역 레벨 사용")

        form.addRow("표시 이름", self.name)
        form.addRow("주 역할", self.role)
        form.addRow("속성", self.element)
        form.addRow("공격 형태", self.shape)
        form.addRow("계수 유형", self.scale)
        form.addRow("개별 계산 레벨", self.level)
        root.addLayout(form)

    def _build_slot_grid(self, root) -> None:
        grid = QGridLayout()
        self.slots: dict[str, QSpinBox] = {}
        for index, factor in enumerate(FACTOR_ORDER):
            grid.addWidget(
                QLabel(str(self.factor_names.get(factor, factor))),
                index // 2,
                (index % 2) * 2,
            )
            spin = QSpinBox()
            spin.setRange(0, 6)
            self.slots[factor] = spin
            grid.addWidget(spin, index // 2, (index % 2) * 2 + 1)
        root.addLayout(grid)

        self.total = QLabel("")
        self.total.setObjectName("Muted")
        root.addWidget(self.total)

    def _build_actions(self, root) -> None:
        actions = QHBoxLayout()
        self.save = QPushButton("현재 설정 저장")
        self.new = QPushButton("새 더미 만들기")
        self.delete = QPushButton("더미 삭제")
        self.delete.setObjectName("DangerButton")
        actions.addWidget(self.save)
        actions.addWidget(self.new)
        actions.addWidget(self.delete)
        actions.addStretch(1)
        root.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.accept)
        root.addWidget(buttons)

    def _connect_signals(self) -> None:
        for spin in self.slots.values():
            spin.valueChanged.connect(self._update_total)
        self.combo.currentIndexChanged.connect(self._load)
        self.save.clicked.connect(self._save)
        self.new.clicked.connect(self._new)
        self.delete.clicked.connect(self._delete)

    def _reload_combo(self, preserve: str | None = None) -> None:
        self.svc = RemoldingRecommendationService(self.repo)
        dummies = {str(row["key"]) for row in self.svc.list_dummy_characters()}
        self.combo.blockSignals(True)
        self.combo.clear()

        target_index = 0
        for index, character in enumerate(self.svc.list_characters()):
            key = str(character["key"])
            suffix = " [더미]" if key in dummies else ""
            self.combo.addItem(str(character.get("nameKR") or key) + suffix, key)
            if preserve and key == preserve:
                target_index = index

        self.combo.setCurrentIndex(target_index if self.combo.count() else -1)
        self.combo.blockSignals(False)
        self._load()

    def _current_key(self) -> str | None:
        if self.combo.currentIndex() < 0:
            return None
        value = self.combo.currentData()
        return str(value) if value is not None else None

    def _dummy_keys(self) -> set[str]:
        return {str(row["key"]) for row in self.svc.list_dummy_characters()}

    def _load(self) -> None:
        key = self._current_key()
        if not key:
            return

        character = self.svc.get_character(key)
        dummy = key.startswith("dummy_") or key in self._dummy_keys()
        self.name.setText(str(character.get("nameKR") or ""))
        self.name.setEnabled(dummy)

        role_label = str(
            self.factor_names.get(character.get("dollType"), character.get("dollType") or "")
        )
        self.role.setCurrentText(role_label)

        element_label = str(
            self.element_names.get(
                character.get("elementType"),
                character.get("elementType") or "",
            )
        )
        self.element.setCurrentText(element_label)

        tags = list(character.get("tags") or [])
        self.shape.setCurrentText(
            next((value for value in self.SHAPES if value in tags), self.SHAPES[0])
        )
        self.scale.setCurrentText(
            next((value for value in self.SCALES if value in tags), self.SCALES[0])
        )
        known_tags = set(self.SHAPES) | set(self.SCALES)
        self.extra_tags = [value for value in tags if value not in known_tags]

        distribution = {
            str(row.get("factorType")): int(row.get("count") or 0)
            for row in character.get("slotDistribution", [])
        }
        for factor, spin in self.slots.items():
            spin.setValue(distribution.get(factor, 0))

        self.level.setValue(int(character.get("levelOverride") or (60 if dummy else 0)))
        self.delete.setEnabled(dummy)
        self._update_total()

    def _values(self):
        counts = {key: spin.value() for key, spin in self.slots.items()}
        total = sum(counts.values())
        if total != 6:
            raise ValueError("장착 가능 개수 합계는 정확히 6이어야 합니다.")

        role_map = {
            str(self.factor_names.get(key, key)): key
            for key in FACTOR_ORDER
        }
        element_map = {
            str(value): str(key)
            for key, value in self.element_names.items()
        }
        tags = list(
            dict.fromkeys(
                [self.shape.currentText(), self.scale.currentText(), *self.extra_tags]
            )
        )
        return (
            counts,
            role_map.get(self.role.currentText(), "sentinel"),
            element_map.get(self.element.currentText(), "physical"),
            tags,
            self.level.value(),
        )

    def _update_total(self) -> None:
        total = sum(spin.value() for spin in self.slots.values())
        suffix = "  ✓" if total == 6 else "  · 정확히 6 필요"
        self.total.setText(f"장착칸 합계 {total}/6{suffix}")

    def _save(self) -> None:
        key = self._current_key()
        if not key:
            return
        try:
            counts, role, element, tags, level = self._values()
            self.svc.save_character_profile(
                key,
                slot_counts=counts,
                display_name=self.name.text(),
                doll_type=role,
                element_type=element,
                tags=tags,
                is_dummy=key.startswith("dummy_"),
                level_override=(level or None),
            )
        except Exception as exc:
            show_error(self, "저장 실패", exc)
            return
        self.changed = True
        self._reload_combo(key)

    def _new(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "더미 캐릭터 추가",
            "표시 이름",
            text="더미 캐릭터",
        )
        if not accepted or not name.strip():
            return
        try:
            counts, role, element, tags, level = self._values()
            character = self.svc.create_dummy_character(
                name.strip(),
                slot_counts=counts,
                doll_type=role,
                element_type=element,
                tags=tags,
                level=(level or 60),
            )
        except Exception as exc:
            show_error(self, "더미 생성 실패", exc)
            return
        self.changed = True
        self._reload_combo(str(character["key"]))

    def _delete(self) -> None:
        key = self._current_key()
        if not key or not key.startswith("dummy_"):
            return
        answer = QMessageBox.question(
            self,
            "더미 삭제",
            f"{self.combo.currentText()}를 삭제할까요?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.svc.delete_dummy_character(key)
        self.changed = True
        self._reload_combo(None)


class SelectedRemoldingCharacterDialog(QDialog):
    """Focused editor for one existing Doll's remolding calculation settings."""

    def __init__(self, repo: Repository, character_key: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.character_key = str(character_key)
        self.svc = RemoldingRecommendationService(repo)
        self.changed = False
        character = self.svc.get_character(self.character_key)
        rules = reference.remolding_rules()
        factor_names = dict(rules.get("factor_names") or {})

        self.setWindowTitle(f"{character.get('nameKR') or self.character_key} · 리몰딩 설정")
        self.resize(560, 430)
        root = dialog_layout(self)

        title = QLabel(str(character.get("nameKR") or self.character_key))
        title.setObjectName("PageTitle")
        root.addWidget(title)
        note = QLabel(
            "이 인형의 리몰딩 계산 레벨과 6개 장착칸만 편집합니다. "
            "계산 레벨을 ‘전역 레벨 사용’으로 두면 리몰딩 최적화 화면의 전역값을 따릅니다."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        self.level = QSpinBox()
        self.level.setRange(0, 60)
        self.level.setSpecialValueText("전역 레벨 사용")
        self.level.setValue(int(character.get("levelOverride") or 0))
        form.addRow("개별 계산 레벨", self.level)
        root.addLayout(form)

        grid = QGridLayout()
        self.slots: dict[str, QSpinBox] = {}
        distribution = {
            str(row.get("factorType")): int(row.get("count") or 0)
            for row in character.get("slotDistribution", [])
        }
        for index, factor in enumerate(FACTOR_ORDER):
            label = QLabel(str(factor_names.get(factor, factor)))
            spin = QSpinBox()
            spin.setRange(0, 6)
            spin.setValue(int(distribution.get(factor, 0)))
            spin.valueChanged.connect(self._update_total)
            self.slots[factor] = spin
            grid.addWidget(label, index // 2, (index % 2) * 2)
            grid.addWidget(spin, index // 2, (index % 2) * 2 + 1)
        root.addLayout(grid)
        self.total = QLabel()
        self.total.setObjectName("Muted")
        root.addWidget(self.total)
        self._update_total()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _update_total(self) -> None:
        total = sum(spin.value() for spin in self.slots.values())
        suffix = "✓" if total == 6 else "정확히 6개가 필요합니다."
        self.total.setText(f"장착칸 합계 {total}/6 · {suffix}")

    def _save(self) -> None:
        counts = {factor: spin.value() for factor, spin in self.slots.items()}
        if sum(counts.values()) != 6:
            QMessageBox.information(self, "리몰딩 설정", "장착칸 합계는 정확히 6이어야 합니다.")
            return
        try:
            character = self.svc.get_character(self.character_key)
            self.svc.save_character_profile(
                self.character_key,
                slot_counts=counts,
                display_name=str(character.get("nameKR") or self.character_key),
                doll_type=str(character.get("dollType") or "sentinel"),
                element_type=str(character.get("elementType") or "physical"),
                tags=[str(tag) for tag in character.get("tags", [])],
                is_dummy=self.character_key.startswith("dummy_"),
                level_override=(self.level.value() or None),
            )
        except Exception as exc:
            show_error(self, "리몰딩 설정 저장 실패", exc)
            return
        self.changed = True
        self.accept()
