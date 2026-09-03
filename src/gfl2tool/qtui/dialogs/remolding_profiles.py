from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import reference
from ...repository import Repository
from ...services.remolding_recommendation import RemoldingRecommendationService
from ..theme import FACTOR_ORDER
from ..widgets import dialog_layout, help_icon, page_title, show_error


class StepperBox(QWidget):
    """Dark-theme-safe numeric input with explicit text step buttons."""

    def __init__(
        self,
        minimum: int,
        maximum: int,
        value: int,
        *,
        step: int = 1,
        prefix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.spin = QSpinBox()
        self.spin.setRange(int(minimum), int(maximum))
        self.spin.setSingleStep(max(1, int(step)))
        self.spin.setValue(int(value))
        self.spin.setPrefix(prefix)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setMinimumWidth(84)
        layout.addWidget(self.spin, 1)

        self.down = QToolButton()
        self.down.setObjectName("StepButton")
        self.down.setText("−")
        self.down.setToolTip("값 줄이기")
        self.down.clicked.connect(self.spin.stepDown)
        layout.addWidget(self.down)

        self.up = QToolButton()
        self.up.setObjectName("StepButton")
        self.up.setText("+")
        self.up.setToolTip("값 늘리기")
        self.up.clicked.connect(self.spin.stepUp)
        layout.addWidget(self.up)

    def value(self) -> int:
        return int(self.spin.value())


class TargetProfileDialog(QDialog):
    def __init__(
        self,
        repo: Repository,
        character_key: str,
        parent=None,
        *,
        initial_targets: dict[str, dict] | None = None,
        save_global: bool = True,
        title: str = "추천 스탯 목표",
    ):
        super().__init__(parent)
        self.repo = repo
        self.key = character_key
        self.svc = RemoldingRecommendationService(repo)
        self.save_global = bool(save_global)
        self.result_targets: dict[str, dict[str, int]] | None = None
        self.values: dict[str, tuple[StepperBox, StepperBox, StepperBox, QWidget]] = {}
        self.option_meta = reference.remolding_options()

        self.setWindowTitle(title)
        self.resize(820, 690)
        self.setMinimumSize(720, 560)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)
        root.addWidget(
            page_title(
                title,
                "자동 배치에서 맞출 목표를 직접 지정합니다. "
                "가중치는 클수록 같은 우선순위 안에서 더 큰 비중을 두며, 우선순위는 숫자가 작을수록 높습니다.",
            )
        )

        self._build_header(root)
        self._build_rows_area(root)
        self._load_initial_rows(initial_targets)
        self._build_add_panel(root)
        self._build_buttons(root)

    def _build_header(self, root) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(10, 0, 10, 0)
        stat_header = QLabel("스탯")
        stat_header.setObjectName("Muted")
        stat_header.setMinimumWidth(190)
        header.addWidget(stat_header, 1)

        header_help = {
            "가중치": "100이 기본값이며 값이 클수록 같은 우선순위에서 해당 목표의 배치 점수 비중이 커집니다.",
            "우선순위": "1이 가장 높습니다. 1번 목표를 먼저 맞춘 뒤 2, 3… 순으로 고려합니다.",
        }
        for text, width in (("목표 레벨", 150), ("가중치", 150), ("우선순위", 150)):
            cell = QHBoxLayout()
            cell.setSpacing(3)
            label = QLabel(text)
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumWidth(width - (24 if text in header_help else 0))
            cell.addWidget(label, 1)
            if text in header_help:
                cell.addWidget(help_icon(header_help[text]))
            header.addLayout(cell)
        header.addSpacing(38)
        root.addLayout(header)

    def _build_rows_area(self, root) -> None:
        self.row_host = QWidget()
        self.rows = QVBoxLayout(self.row_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(7)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setObjectName("TargetProfileScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.row_host)
        root.addWidget(scroll, 1)

    def _load_initial_rows(self, initial_targets: dict[str, dict] | None) -> None:
        current = (
            self.svc.get_target_profile(self.key)
            if initial_targets is None
            else dict(initial_targets)
        )
        eligible = [
            row
            for row in self.svc.recommendations(self.key)
            if row.get("eligible")
        ]
        self.option_rows = {str(row["optionKey"]): dict(row) for row in eligible}
        self.factor_names = reference.remolding_rules().get("factor_names", {})
        for key, spec in current.items():
            self._add_row(
                str(key),
                int(spec.get("level") or 0),
                int(spec.get("weight") or 100),
                int(spec.get("priority") or 1),
            )

    def _build_add_panel(self, root) -> None:
        add_panel = QFrame()
        add_panel.setObjectName("Panel")
        add_layout = QGridLayout(add_panel)
        add_layout.setContentsMargins(12, 10, 12, 10)
        add_layout.setHorizontalSpacing(8)

        add_layout.addWidget(QLabel("카테고리"), 0, 0)
        self.add_category = QComboBox()
        self.add_category.addItem("전체 카테고리", "")
        factors_present = {
            str((self.option_meta.get(key) or {}).get("factorType") or "")
            for key in self.option_rows
        }
        for factor in FACTOR_ORDER:
            if factor in factors_present:
                self.add_category.addItem(str(self.factor_names.get(factor, factor)), factor)
        self.add_category.currentIndexChanged.connect(self._refresh_add_options)
        add_layout.addWidget(self.add_category, 0, 1)

        add_layout.addWidget(QLabel("스탯"), 0, 2)
        self.add_combo = QComboBox()
        add_layout.addWidget(self.add_combo, 0, 3, 1, 2)

        add_button = QPushButton("선택 스탯 추가")
        add_button.setObjectName("AccentButton")
        add_button.clicked.connect(self._add_selected)
        add_layout.addWidget(add_button, 0, 5)
        add_layout.setColumnStretch(3, 1)
        root.addWidget(add_panel)
        self._refresh_add_options()

    def _build_buttons(self, root) -> None:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _option_sort_key(self, key: str) -> tuple[int, str]:
        meta = self.option_meta.get(key, {})
        factor = str(meta.get("factorType") or "")
        try:
            order = FACTOR_ORDER.index(factor)
        except ValueError:
            order = len(FACTOR_ORDER)
        return order, str(meta.get("nameKR") or key)

    def _refresh_add_options(self) -> None:
        selected_factor = str(self.add_category.currentData() or "")
        self.add_combo.clear()
        for key in sorted(self.option_rows, key=self._option_sort_key):
            meta = self.option_meta.get(key, {})
            factor = str(meta.get("factorType") or "")
            if selected_factor and factor != selected_factor:
                continue
            label = str(meta.get("nameKR") or key)
            self.add_combo.addItem(label, key)

    def _add_row(self, key: str, level: int, weight: int, priority: int = 1) -> None:
        if key in self.values:
            return

        meta = self.option_meta.get(key, {})
        row = QFrame()
        row.setObjectName("Panel")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)

        factor = str(meta.get("factorType") or "")
        factor_label = str(self.factor_names.get(factor, factor)) if factor else "분류 없음"
        name = QLabel(f"{meta.get('nameKR') or key}  ·  {factor_label}")
        name.setMinimumWidth(190)
        name.setWordWrap(True)
        layout.addWidget(name, 1)

        level_spin = StepperBox(1, max(1, int(meta.get("maxLevel") or 1)), max(1, level), prefix="Lv.")
        level_spin.setMinimumWidth(150)
        layout.addWidget(level_spin)

        weight_spin = StepperBox(0, 10000, max(0, weight), step=10)
        weight_spin.setToolTip("값이 클수록 같은 우선순위 안에서 이 목표의 점수 비중이 커집니다. 100이 기본값입니다.")
        weight_spin.setMinimumWidth(150)
        layout.addWidget(weight_spin)

        priority_spin = StepperBox(1, 99, max(1, priority))
        priority_spin.setToolTip("숫자가 작을수록 높은 우선순위입니다. 1이 가장 먼저 달성할 목표입니다.")
        priority_spin.setMinimumWidth(150)
        layout.addWidget(priority_spin)

        remove = QPushButton("삭제")
        remove.setObjectName("DangerButton")
        remove.setFixedWidth(54)
        remove.clicked.connect(lambda: self._remove_row(key))
        layout.addWidget(remove)

        self.values[key] = (level_spin, weight_spin, priority_spin, row)
        self.rows.addWidget(row)

    def _remove_row(self, key: str) -> None:
        item = self.values.pop(key, None)
        if item is not None:
            item[3].deleteLater()

    def _add_selected(self) -> None:
        key = str(self.add_combo.currentData() or "")
        if not key:
            return
        meta = self.option_meta.get(key, {})
        self._add_row(key, 1, int(meta.get("weight") or 100))

    def _save(self) -> None:
        targets = {
            key: {
                "level": level_spin.value(),
                "weight": weight_spin.value(),
                "priority": priority_spin.value(),
            }
            for key, (level_spin, weight_spin, priority_spin, _row) in self.values.items()
        }
        try:
            normalized = self.svc.normalize_target_profile(self.key, targets)
            if self.save_global:
                self.svc.save_target_profile(self.key, normalized)
        except Exception as exc:
            show_error(self, "저장 실패", exc)
            return
        self.result_targets = normalized
        self.accept()


class SlotProfileDialog(QDialog):
    def __init__(self, repo: Repository, character_key: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.key = character_key
        self.svc = RemoldingRecommendationService(repo)
        character = self.svc.get_character(character_key)

        self.setWindowTitle("리몰딩 장착칸")
        self.resize(480, 330)
        self.setMinimumSize(440, 300)

        root = dialog_layout(self)
        root.addWidget(
            page_title(
                "리몰딩 장착칸",
                "캐릭터가 장착할 6개 리몰딩의 계열별 개수를 지정합니다.",
            )
        )

        form = QFormLayout()
        root.addLayout(form)
        self.spins: dict[str, QSpinBox] = {}

        counts = {
            str(row.get("factorType")): int(row.get("count") or 0)
            for row in character.get("slotDistribution", [])
        }
        names = reference.remolding_rules().get("factor_names", {})
        for factor in ("sentinel", "vanguard", "bulwark", "support"):
            spin = QSpinBox()
            spin.setRange(0, 6)
            spin.setValue(counts.get(factor, 0))
            self.spins[factor] = spin
            form.addRow(str(names.get(factor, factor)), spin)

        self.level = QSpinBox()
        self.level.setRange(0, 60)
        self.level.setSpecialValueText("전역 레벨 사용")
        self.level.setValue(int(character.get("levelOverride") or 0))
        form.addRow("계산 레벨", self.level)

        note_row = QHBoxLayout()
        note_row.addWidget(help_icon("장착칸 합계는 정확히 6이어야 합니다.", warning=True))
        note_row.addStretch(1)
        root.addLayout(note_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        try:
            self.svc.save_character_profile(
                self.key,
                slot_counts={key: spin.value() for key, spin in self.spins.items()},
                level_override=self.level.value(),
            )
        except Exception as exc:
            show_error(self, "저장 실패", exc)
            return
        self.accept()
