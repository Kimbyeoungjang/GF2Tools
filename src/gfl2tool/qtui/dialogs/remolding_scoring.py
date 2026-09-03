from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
)

from ... import reference
from ...repository import Repository
from ...services.remolding_recommendation import RemoldingRecommendationService
from ..models import DataTableModel, TABLE_ROW_ROLE, TextFilterProxy
from ..widgets import configure_table_view, dialog_layout, show_error


class OptionOverrideDialog(QDialog):
    def __init__(
        self,
        repo: Repository,
        character_key: str,
        option_key: str,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.character_key = str(character_key)
        self.option_key = str(option_key)
        self.svc = RemoldingRecommendationService(repo)

        character = self.svc.get_character(self.character_key)
        option = reference.remolding_options()[self.option_key]
        current = self.svc.get_override(self.character_key, self.option_key)

        self.setWindowTitle("캐릭터별 리몰딩 보정")
        self.resize(560, 340)
        self.setMinimumSize(500, 300)

        root = dialog_layout(self)
        title = QLabel(f"{character.get('nameKR')} · {option.get('nameKR')}")
        title.setObjectName("PageTitle")
        title.setToolTip(
            "현재 캐릭터의 추천표·보유 리몰딩 점수·자동배치에 동일하게 반영됩니다."
        )
        root.addWidget(title)

        form = QFormLayout()
        self.adjust = QSpinBox()
        self.adjust.setRange(-10000, 10000)
        self.adjust.setValue(int(current.get("score_adjustment") or 0))
        self.adjust.setSingleStep(10)

        self.exclude = QCheckBox("이 캐릭터에게는 추천하지 않음")
        self.exclude.setChecked(str(current.get("state")) == "exclude")

        self.note = QLineEdit(str(current.get("note") or ""))
        self.note.setPlaceholderText("선택 사항")

        form.addRow("점수 보정", self.adjust)
        form.addRow("추천 제외", self.exclude)
        form.addRow("메모", self.note)
        root.addLayout(form)

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
            self.svc.set_override(
                self.character_key,
                self.option_key,
                score_adjustment=self.adjust.value(),
                state="exclude" if self.exclude.isChecked() else "inherit",
                note=self.note.text(),
            )
        except Exception as exc:
            show_error(self, "보정 저장 실패", exc)
            return
        self.accept()

class ScoreConfigDialog(QDialog):
    MULTIPLIER_FIELDS = (
        ("option_weight", "옵션 기본값"),
        ("base_rank", "옵션 기본 등급"),
        ("tag_rank", "캐릭터 적합도"),
    )
    PRESETS = (
        ("기본 균형", (1.0, 1.0, 1.0)),
        ("옵션 자체 중시", (1.4, 1.2, 0.8)),
        ("캐릭터 궁합 중시", (0.8, 1.0, 1.5)),
    )

    def __init__(self, repo: Repository, character_key: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.character_key = str(character_key)
        self.svc = RemoldingRecommendationService(repo)

        self.setWindowTitle("리몰딩 평가 기준")
        self.resize(1050, 760)
        self.setMinimumSize(880, 620)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)
        title = QLabel("리몰딩 평가 기준")
        title.setObjectName("PageTitle")
        title.setToolTip(
            "전역 점수식과 현재 캐릭터의 옵션별 예외를 한 곳에서 관리합니다."
        )
        root.addWidget(title)

        self._build_global_panel(root)
        self._build_option_table(root)
        self._build_actions(root)
        self._connect_signals()
        self._render_options()

    def _build_global_panel(self, root) -> None:
        config = self.svc.get_score_config()
        global_panel = QFrame()
        global_panel.setObjectName("Panel")
        grid = QGridLayout(global_panel)

        self.mult: dict[str, QDoubleSpinBox] = {}
        for column, (key, label) in enumerate(self.MULTIPLIER_FIELDS):
            label_widget = QLabel(label)
            label_widget.setObjectName("Muted")
            grid.addWidget(label_widget, 0, column)

            spin = QDoubleSpinBox()
            spin.setRange(-10, 10)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setValue(float(config["multipliers"][key]))
            self.mult[key] = spin
            grid.addWidget(spin, 1, column)

        presets = QHBoxLayout()
        presets.addWidget(QLabel("빠른 설정"))
        for label, values in self.PRESETS:
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, preset=values: self._preset(preset)
            )
            presets.addWidget(button)
        presets.addStretch(1)
        grid.addLayout(presets, 2, 0, 1, 3)

        self.grades: dict[str, QSpinBox] = {}
        for column, rank in enumerate(("S", "A", "B", "C", "D", "E", "F")):
            label_widget = QLabel(rank)
            label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label_widget, 3, column)

            spin = QSpinBox()
            spin.setRange(-1000, 1000)
            spin.setSingleStep(5)
            spin.setValue(int(config["grades"][rank]))
            self.grades[rank] = spin
            grid.addWidget(spin, 4, column)

        root.addWidget(global_panel)

    def _build_option_table(self, root) -> None:
        header = QHBoxLayout()
        character = self.svc.get_character(self.character_key)
        self.char_label = QLabel(f"{character.get('nameKR')} · 캐릭터별 예외")
        self.char_label.setObjectName("SectionTitle")
        header.addWidget(self.char_label)
        header.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("옵션 검색")
        self.search.setClearButtonEnabled(True)
        header.addWidget(self.search)
        root.addLayout(header)

        self.option_model = DataTableModel(
            [],
            [
                ("옵션", "name"),
                ("계열", "factor_label"),
                ("보정", "adjust_text"),
                ("상태", "state_text"),
                ("메모", "note_text"),
            ],
            self,
            sort_getters=[
                "name",
                "factor_label",
                "score_adjustment",
                "state_text",
                "note_text",
            ],
            search_getter="search_text",
        )
        self.option_proxy = TextFilterProxy(self)
        self.option_proxy.setSourceModel(self.option_model)

        self.table = QTableView()
        self.table.setModel(self.option_proxy)
        configure_table_view(
            self.table,
            widths={0: 170, 1: 95, 2: 75, 3: 95},
            select_rows=True,
        )
        root.addWidget(self.table, 1)

        character_actions = QHBoxLayout()
        self.edit = QPushButton("선택 옵션 수정")
        self.reset_char = QPushButton("이 캐릭터 예외 초기화")
        self.reset_char.setObjectName("DangerButton")
        character_actions.addWidget(self.edit)
        character_actions.addWidget(self.reset_char)
        character_actions.addStretch(1)
        root.addLayout(character_actions)

    def _build_actions(self, root) -> None:
        actions = QHBoxLayout()
        self.reset_global = QPushButton("전역 기준 기본값")
        self.reset_global.setObjectName("DangerButton")
        actions.addWidget(self.reset_global)
        actions.addStretch(1)

        self.save = QPushButton("전역 기준 저장")
        self.save.setObjectName("AccentButton")
        actions.addWidget(self.save)

        self.close = QPushButton("닫기")
        actions.addWidget(self.close)
        root.addLayout(actions)

    def _connect_signals(self) -> None:
        self.search.textChanged.connect(self.option_proxy.set_query)
        self.edit.clicked.connect(self._edit_selected)
        self.table.doubleClicked.connect(lambda _index: self._edit_selected())
        self.reset_char.clicked.connect(self._reset_char)
        self.reset_global.clicked.connect(self._reset_global)
        self.save.clicked.connect(self._save_global)
        self.close.clicked.connect(self.accept)

    def _preset(self, values: tuple[float, float, float]) -> None:
        for key, value in zip(
            ("option_weight", "base_rank", "tag_rank"),
            values,
        ):
            self.mult[key].setValue(value)

    def _render_options(self) -> None:
        factors = reference.remolding_rules().get("factor_names", {})
        options = reference.remolding_options()
        overrides = self.svc.list_overrides(self.character_key)
        character = self.svc.get_character(self.character_key)
        allowed = set(character.get("slotTypes") or [])

        prepared: list[dict] = []
        for key, option in sorted(
            options.items(),
            key=lambda item: (
                str(item[1].get("factorType")),
                str(item[1].get("nameKR")),
            ),
        ):
            if allowed and option.get("factorType") not in allowed:
                continue
            override = overrides.get(
                key,
                {"score_adjustment": 0, "state": "inherit", "note": ""},
            )
            adjustment = int(override.get("score_adjustment") or 0)
            row = {
                "option_key": str(key),
                "name": str(option.get("nameKR") or key),
                "factor_label": str(
                    factors.get(option.get("factorType"), option.get("factorType"))
                ),
                "score_adjustment": adjustment,
                "adjust_text": f"{adjustment:+d}" if adjustment else "—",
                "state_text": (
                    "추천 제외" if override.get("state") == "exclude" else "기본 적용"
                ),
                "note_text": str(override.get("note") or ""),
            }
            row["search_text"] = " ".join(
                str(row[field])
                for field in (
                    "name",
                    "factor_label",
                    "adjust_text",
                    "state_text",
                    "note_text",
                )
            )
            prepared.append(row)

        self.option_model.set_rows(prepared)
        self.option_proxy.set_query(self.search.text())
        if self.option_proxy.rowCount():
            self.table.selectRow(0)

    def _selected_option_key(self) -> str | None:
        index = self.table.currentIndex()
        if not index.isValid():
            return None
        row = self.table.model().index(index.row(), 0).data(TABLE_ROW_ROLE) or {}
        value = row.get("option_key")
        return str(value) if value else None

    def _edit_selected(self) -> None:
        key = self._selected_option_key()
        if not key:
            return
        if OptionOverrideDialog(self.repo, self.character_key, key, self).exec():
            self.svc = RemoldingRecommendationService(self.repo)
            self._render_options()

    def _reset_char(self) -> None:
        answer = QMessageBox.question(
            self,
            "캐릭터별 예외 초기화",
            "현재 캐릭터의 옵션별 보정을 모두 초기화할까요?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.svc.reset_character_overrides(self.character_key)
        self.svc = RemoldingRecommendationService(self.repo)
        self._render_options()

    def _reset_global(self) -> None:
        answer = QMessageBox.question(
            self,
            "전역 기준 초기화",
            "등급과 계산 비중을 기본값으로 되돌릴까요? 캐릭터별 예외는 유지됩니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        config = self.svc.reset_score_config()
        for key, spin in self.mult.items():
            spin.setValue(float(config["multipliers"][key]))
        for rank, spin in self.grades.items():
            spin.setValue(int(config["grades"][rank]))

    def _save_global(self) -> None:
        config = {
            "grades": {key: spin.value() for key, spin in self.grades.items()},
            "multipliers": {key: spin.value() for key, spin in self.mult.items()},
        }
        try:
            self.svc.save_score_config(config)
        except Exception as exc:
            show_error(self, "평가 기준 저장 실패", exc)
            return
        self.accept()
