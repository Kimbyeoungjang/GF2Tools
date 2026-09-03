from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...tactics import MAX_TACTIC_UNITS, TacticUnit
from ...services.doll_skill_cycles import DollSkillCycleStore
from ...services.formation_preferences import FormationMemberPreferenceStore, formation_cycle_candidates
from ...services.formations import FormationService
from ..data import OwnedDollCatalog
from ..dialogs.doll_picker import DollPickerDialog
from ..dialogs.doll_skill_cycles import DollSkillCycleDialog
from ..images import PortraitLoader
from ..rich_text import game_markup_to_qt_html
from ..widgets import dialog_layout, help_icon, page_title, section_panel


class _TacticUnitSkillCycleAdapter:
    def __init__(self, unit: TacticUnit):
        self.unit = unit

    def actions_for(self, _doll_id: int | None) -> list[str]:
        return list(self.unit.skill_cycle)

    def set_actions(self, _doll_id: int, actions: list[str]):
        self.unit.skill_cycle = [str(value or "").strip()[:240] for value in actions[:64]]
        while self.unit.skill_cycle and not self.unit.skill_cycle[-1]:
            self.unit.skill_cycle.pop()
        if not self.unit.skill_cycle:
            self.unit.skill_cycle_source = ""
        return None


class TacticUnitsDialog(QDialog):
    """Edit tactic roster and portable equipment presets."""

    KEY_SLOT_COUNT = 3

    def __init__(
        self,
        repo,
        catalog: OwnedDollCatalog,
        portraits: PortraitLoader,
        units: list[TacticUnit],
        equipment_data: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog
        self.portraits = portraits
        self.equipment_data = dict(equipment_data or {})
        self.skill_cycle_store = DollSkillCycleStore(repo.path.parent)
        self.formation_cycle_store = FormationMemberPreferenceStore(repo.path.parent)
        self.formation_service = FormationService(repo)
        self.units = [TacticUnit.from_dict(asdict(unit)) for unit in units]
        self.result_units = [TacticUnit.from_dict(asdict(unit)) for unit in units]
        self._updating = False

        self.setWindowTitle("택틱 사용 인형 관리")
        self.resize(1180, 820)
        self.setMinimumSize(980, 700)
        root = dialog_layout(self)
        root.addWidget(page_title("택틱 사용 인형 관리"))
        self._build_editor(root)
        self._build_footer(root)
        self._connect_field_signals()
        self._rebuild_list(select=0)

    def _build_editor(self, root) -> None:
        split = QSplitter(Qt.Orientation.Horizontal)
        left, left_layout = section_panel("사용 인형")
        order_row = QHBoxLayout()
        order_label = QLabel("스킬 사이클 적용 순서")
        order_label.setObjectName("Muted")
        order_row.addWidget(order_label)
        order_row.addWidget(help_icon(
            "택틱의 자동 스킬 사이클은 이 목록의 위→아래 순서대로 한 줄에 조합됩니다. "
            "인형을 추가한 순서가 기본값이며, 아래의 위/아래 버튼으로 언제든 순서를 바꿀 수 있습니다."
        ))
        order_row.addStretch(1)
        left_layout.addLayout(order_row)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._selected)
        left_layout.addWidget(self.list, 1)

        actions = QHBoxLayout()
        add = QPushButton("+ 인형 여러 개")
        import_formation = QPushButton("제대에서 가져오기…")
        move_up = QPushButton("↑ 위로")
        move_down = QPushButton("↓ 아래로")
        remove = QPushButton("선택 제거")
        remove.setObjectName("DangerButton")
        add.clicked.connect(self._add_units)
        import_formation.clicked.connect(self._import_from_formation)
        move_up.clicked.connect(lambda: self._move_unit(-1))
        move_down.clicked.connect(lambda: self._move_unit(1))
        remove.clicked.connect(self._remove_unit)
        actions.addWidget(add)
        actions.addWidget(import_formation)
        actions.addWidget(move_up)
        actions.addWidget(move_down)
        actions.addWidget(remove)
        left_layout.addLayout(actions)
        split.addWidget(left)

        right, right_layout = section_panel("선택 인형 설정")
        self.selected_name = QLabel("인형을 선택하세요.")
        self.selected_name.setObjectName("AccentText")
        right_layout.addWidget(self.selected_name)
        right_layout.addLayout(self._build_form())

        self.imported_status = QLabel("가져온 장착값 없음")
        self.imported_status.setObjectName("Muted")
        self.imported_status.setWordWrap(True)
        right_layout.addWidget(self.imported_status)
        self.apply_imported = QPushButton("가져온 장착값 적용")
        self.apply_imported.clicked.connect(self._apply_imported_values)
        right_layout.addWidget(self.apply_imported)

        self.skill_cycle_status = QLabel("이 택틱에서는 스킬 사이클 미지정")
        self.skill_cycle_status.setObjectName("Muted")
        self.skill_cycle_status.setWordWrap(True)
        right_layout.addWidget(self.skill_cycle_status)
        cycle_row = QHBoxLayout()
        self.load_formation_cycle = QPushButton("제대 사이클 불러오기…")
        self.load_general_cycle = QPushButton("일반 사이클 불러오기")
        self.edit_skill_cycles = QPushButton("직접 편집…")
        self.clear_skill_cycles = QPushButton("비우기")
        self.load_formation_cycle.setToolTip("제대 편성에서 이 인형에 별도로 저장한 사이클을 선택해 이 택틱에 복사합니다.")
        self.load_general_cycle.setToolTip("보유 현황에서 이 인형에 저장한 일반 사이클을 이 택틱에 복사합니다.")
        self.edit_skill_cycles.setToolTip("이 택틱에서만 사용할 T1~Tn 사이클을 직접 편집합니다.")
        self.clear_skill_cycles.setToolTip("이 택틱에서 불러온 사이클만 비웁니다. 원본 일반/제대 사이클은 삭제하지 않습니다.")
        self.load_formation_cycle.clicked.connect(self._load_formation_cycle)
        self.load_general_cycle.clicked.connect(self._load_general_cycle)
        self.edit_skill_cycles.clicked.connect(self._edit_skill_cycles)
        self.clear_skill_cycles.clicked.connect(self._clear_skill_cycle)
        for button in (self.load_formation_cycle, self.load_general_cycle, self.edit_skill_cycles, self.clear_skill_cycles):
            cycle_row.addWidget(button)
        right_layout.addLayout(cycle_row)
        right_layout.addStretch(1)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([360, 640])
        root.addWidget(split, 1)

    def _build_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.alias = QComboBox()
        self.alias.setEditable(True)
        self.alias.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.alias.lineEdit().setPlaceholderText("비우면 이름 첫 글자")
        self.rank = QSpinBox()
        self.rank.setRange(0, 6)
        self.weapon = self._equipment_combo("무기 이름 또는 ID")
        self.common_keys = self._key_slot_widget("공용키 이름 또는 ID")
        self.unique_keys = self._key_slot_widget("고유키 이름 또는 ID")
        self.expansion_level = QComboBox()
        self.expansion_level.addItem("도약키 미장착", 0)
        self.expansion_level.addItem("도약키 1단계", 1)
        self.expansion_level.addItem("도약키 2단계", 2)

        form.addRow("오버레이 별칭", self.alias)
        form.addRow("돌파", self.rank)
        form.addRow("무기", self.weapon)
        common_host = self._slots_container(self.common_keys)
        common_host.setToolTip("목록의 키를 고르거나 '아무거나', '피증 키'처럼 직접 설명을 입력할 수 있습니다.")
        unique_host = self._slots_container(self.unique_keys)
        unique_host.setToolTip("이 인형에 지정된 고유키만 목록에 표시합니다. 목록에 없는 자유 설명도 직접 입력할 수 있습니다.")
        form.addRow("공용키 (최대 3개)", common_host)
        form.addRow("고유키 (최대 3개)", unique_host)
        form.addRow("도약키", self.expansion_level)
        return form

    @classmethod
    def _key_slot_widget(cls, placeholder: str) -> list[QComboBox]:
        return [cls._equipment_combo(f"{placeholder} · {index + 1}") for index in range(cls.KEY_SLOT_COUNT)]

    @staticmethod
    def _slots_container(combos: list[QComboBox]) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for combo in combos:
            layout.addWidget(combo)
        return widget

    def _build_footer(self, root) -> None:
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("취소")
        save = QPushButton("적용")
        save.setObjectName("AccentButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

    def _connect_field_signals(self) -> None:
        self.alias.currentTextChanged.connect(self._fields_changed)
        self.rank.valueChanged.connect(self._fields_changed)
        self.weapon.currentTextChanged.connect(self._fields_changed)
        self.expansion_level.currentIndexChanged.connect(self._fields_changed)
        for combo in (*self.common_keys, *self.unique_keys):
            combo.currentTextChanged.connect(self._fields_changed)

    @staticmethod
    def _equipment_combo(placeholder: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText(placeholder + " · 직접 입력 가능")
        combo.setMinimumContentsLength(26)
        combo.currentIndexChanged.connect(
            lambda index, target=combo: target.setToolTip(
                str(target.itemData(index, Qt.ItemDataRole.ToolTipRole) or "") if index >= 0 else ""
            )
        )
        return combo

    def _current(self) -> TacticUnit | None:
        row = self.list.currentRow()
        return self.units[row] if 0 <= row < len(self.units) else None

    @staticmethod
    def _unit_summary(unit: TacticUnit) -> str:
        parts = []
        if unit.weapon.strip():
            parts.append(unit.weapon.strip())
        if unit.common_keys:
            parts.append(f"공용 {len(unit.common_keys)}")
        if unit.unique_keys:
            parts.append(f"고유 {len(unit.unique_keys)}")
        if unit.expansion_level:
            parts.append(f"도약 {unit.expansion_level}단계")
        if unit.skill_cycle:
            parts.append(f"사이클 T{len(unit.skill_cycle)}")
        return " · ".join(parts)

    def _rebuild_list(self, *, select: int = -1) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for unit in self.units:
            summary = self._unit_summary(unit)
            item = QListWidgetItem(
                f"{unit.name or '인형'}  ·  {unit.display_label()}  ·  {unit.rank}돌"
                + (f"\n{summary}" if summary else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, unit.unit_key)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.units:
            self.list.setCurrentRow(max(0, min(select if select >= 0 else 0, len(self.units) - 1)))
        else:
            self._selected(-1)

    def _add_units(self) -> None:
        remaining = MAX_TACTIC_UNITS - len(self.units)
        if remaining <= 0:
            QMessageBox.information(self, "사용 인형", f"한 택틱에는 최대 {MAX_TACTIC_UNITS}명까지 등록할 수 있습니다.")
            return
        dialog = DollPickerDialog(
            self.repo,
            self.catalog,
            self.portraits,
            parent=self,
            multi_select=True,
            include_unowned=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_ids:
            return
        entries = {
            int(row.get("doll_id")): dict(row)
            for row in dialog.result_entries
            if row.get("doll_id") is not None
        }
        existing = {int(unit.doll_id) for unit in self.units if unit.doll_id is not None}
        added = 0
        for raw_id in dialog.result_ids:
            doll_id = int(raw_id)
            if doll_id in existing or added >= remaining:
                continue
            entry = entries.get(doll_id, {})
            owned = dict(entry.get("row") or {})
            self.units.append(
                TacticUnit(
                    doll_id=doll_id,
                    name=str(entry.get("name") or doll_id),
                    rank=max(0, min(6, int(owned.get("rank") or entry.get("rank") or 0))),
                )
            )
            existing.add(doll_id)
            added += 1
        self._rebuild_list(select=len(self.units) - 1)
        if added == 0:
            QMessageBox.information(self, "사용 인형", "선택한 인형은 이미 모두 등록되어 있습니다.")

    def _import_from_formation(self) -> None:
        plans = self.formation_service.list()
        if not plans:
            QMessageBox.information(self, "제대에서 가져오기", "저장된 제대가 없습니다. 먼저 제대 편성에서 제대를 만들어 주세요.")
            return
        labels = [f"{row['name']} · {int(row.get('member_count') or 0)}명" for row in plans]
        selected, ok = QInputDialog.getItem(
            self, "제대에서 가져오기", "사용 인형을 가져올 제대를 선택하세요.", labels, 0, False
        )
        if not ok or str(selected) not in labels:
            return
        plan = self.formation_service.get(int(plans[labels.index(str(selected))]['id']))
        members = sorted(plan.get("members") or [], key=lambda row: int(row.get("position") or 0))[:MAX_TACTIC_UNITS]
        if not members:
            QMessageBox.information(self, "제대에서 가져오기", "선택한 제대에 인형이 없습니다.")
            return
        if self.units:
            answer = QMessageBox.question(
                self, "제대에서 가져오기",
                "현재 택틱의 사용 인형 목록을 선택한 제대 인형으로 교체할까요?\n"
                "스킬 사이클은 자동으로 불러오지 않고 빈칸으로 유지됩니다.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        entries = {
            int(row.get("doll_id")): dict(row)
            for row in self.catalog.entries_with_portraits()
            if row.get("doll_id") is not None
        }
        imported: list[TacticUnit] = []
        plan_id = int(plan.get("id") or 0)
        for member in members:
            doll_id = int(member.get("doll_id") or 0)
            if doll_id <= 0:
                continue
            entry = entries.get(doll_id, {})
            owned = dict(entry.get("row") or {})
            imported.append(TacticUnit(
                doll_id=doll_id,
                name=str(entry.get("name") or member.get("doll_name") or doll_id),
                rank=max(0, min(6, int(owned.get("rank") or 0))),
                formation_plan_id=plan_id or None,
                formation_position=int(member.get("position") or 0),
            ))
        self.units = imported
        self._rebuild_list(select=0)

    def _remove_unit(self) -> None:
        row = self.list.currentRow()
        if not (0 <= row < len(self.units)):
            return
        self.units.pop(row)
        self._rebuild_list(select=min(row, len(self.units) - 1))

    def _move_unit(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + int(delta)
        if not (0 <= row < len(self.units)) or not (0 <= target < len(self.units)):
            return
        self._fields_changed()
        unit = self.units.pop(row)
        self.units.insert(target, unit)
        self._rebuild_list(select=target)

    def _imported_row(self, unit: TacticUnit | None) -> dict:
        if unit is None or unit.doll_id is None:
            return {}
        imported = dict(self.equipment_data.get("imported_matches") or {})
        return dict((imported.get("dolls") or {}).get(str(int(unit.doll_id))) or {})

    @staticmethod
    def _item_text(item: dict, *, weapon: bool = False) -> str:
        name = str(item.get("name") or "").strip()
        if not name:
            item_id = int(item.get("id") or 0)
            name = f"ID {item_id}" if item_id else ""
        if weapon and name:
            extras = []
            if int(item.get("level") or 0):
                extras.append(f"Lv.{int(item.get('level') or 0)}")
            if int(item.get("rank") or 0):
                extras.append(f"{int(item.get('rank') or 0)}돌")
            if extras:
                return f"{name} ({' · '.join(extras)})"
        return name

    def _catalog_values(self, category: str, *, doll_id: int | None = None) -> list[tuple[str, str]]:
        rows = [dict(item) for item in self.equipment_data.get(category) or [] if isinstance(item, dict)]
        if category == "fixed_keys" and doll_id is not None:
            # Unique keys are Doll-specific. Never fall back to the entire catalog
            # just because one Doll has no mapped key in the current data set.
            rows = [
                row for row in rows
                if int(row.get("doll_id") or row.get("owner_doll_id") or 0) == int(doll_id)
            ]
        values: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in rows:
            text = str(item.get("name") or f"ID {int(item.get('id') or 0)}").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            values.append((text, str(item.get("description") or "")))
        return values

    def _populate_equipment(
        self,
        combo: QComboBox,
        category: str,
        current: str,
        imported_rows: list[dict],
        *,
        doll_id: int | None = None,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        values: list[str] = []
        descriptions: dict[str, str] = {}
        for item in imported_rows:
            text = self._item_text(item, weapon=category == "weapons")
            if text and text not in values:
                values.append(text)
        for text, description in self._catalog_values(category, doll_id=doll_id):
            descriptions[text] = description
            if text not in values:
                values.append(text)
        for text in values:
            combo.addItem(text)
            description = descriptions.get(text, "")
            if description:
                combo.setItemData(combo.count() - 1, game_markup_to_qt_html(description), Qt.ItemDataRole.ToolTipRole)
        combo.setCurrentText(current)
        index = combo.currentIndex()
        combo.setToolTip(str(combo.itemData(index, Qt.ItemDataRole.ToolTipRole) or "") if index >= 0 else "")
        completer = QCompleter(values, combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)
        combo.blockSignals(False)

    def _populate_key_slots(
        self,
        combos: list[QComboBox],
        category: str,
        current: list[str],
        imported_rows: list[dict],
        *,
        doll_id: int | None = None,
    ) -> None:
        for index, combo in enumerate(combos):
            value = current[index] if index < len(current) else ""
            self._populate_equipment(combo, category, value, imported_rows, doll_id=doll_id)

    def _clear_editor(self) -> None:
        self.selected_name.setText("인형을 선택하세요.")
        self.alias.setCurrentText("")
        self.rank.setValue(0)
        self.weapon.clear()
        for combo in (*self.common_keys, *self.unique_keys):
            combo.clear()
        self.expansion_level.setCurrentIndex(0)
        self.imported_status.setText("가져온 장착값 없음")
        self.apply_imported.setEnabled(False)
        self.skill_cycle_status.setText("이 택틱에서는 스킬 사이클 미지정")
        for button in (self.load_formation_cycle, self.load_general_cycle, self.edit_skill_cycles, self.clear_skill_cycles):
            button.setEnabled(False)

    def _selected(self, row: int) -> None:
        self._updating = True
        try:
            unit = self.units[row] if 0 <= row < len(self.units) else None
            if unit is None:
                self._clear_editor()
                return
            self.selected_name.setText(f"{unit.name}  ·  오버레이 {unit.display_label()}")
            self.alias.setCurrentText(unit.alias)
            self.rank.setValue(unit.rank)
            imported = self._imported_row(unit)
            doll_id = int(unit.doll_id) if unit.doll_id is not None else None
            self._populate_equipment(self.weapon, "weapons", unit.weapon, list(imported.get("weapons") or []))
            self._populate_key_slots(
                self.common_keys,
                "common_keys",
                unit.common_keys,
                list(imported.get("common_keys") or []),
            )
            self._populate_key_slots(
                self.unique_keys,
                "fixed_keys",
                unit.unique_keys,
                list(imported.get("fixed_keys") or []),
                doll_id=doll_id,
            )
            self.expansion_level.setCurrentIndex(max(0, min(2, int(unit.expansion_level))))
            self._update_imported_status(imported)
            for button in (self.load_formation_cycle, self.load_general_cycle, self.edit_skill_cycles, self.clear_skill_cycles):
                button.setEnabled(unit.doll_id is not None)
            self._refresh_skill_cycle_status(unit)
        finally:
            self._updating = False

    def _update_imported_status(self, imported: dict) -> None:
        parts = []
        for key, title in (("weapons", "무기"), ("common_keys", "공용키"), ("fixed_keys", "고유키")):
            values = [
                self._item_text(dict(item), weapon=key == "weapons")
                for item in imported.get(key) or []
                if isinstance(item, dict)
            ]
            values = [value for value in values if value]
            if values:
                parts.append(f"{title}: {', '.join(values[:3])}")
        if imported.get("expansion_keys"):
            parts.append("도약키: 장착 확인")
        self.imported_status.setText("가져온 장착값 · " + " / ".join(parts) if parts else "가져온 장착값 없음")
        self.apply_imported.setEnabled(bool(parts))

    def _refresh_skill_cycle_status(self, unit: TacticUnit) -> None:
        if unit.doll_id is None:
            self.skill_cycle_status.setText("인형 ID가 없어 스킬 사이클을 사용할 수 없습니다.")
            return
        actions = list(unit.skill_cycle)
        if not actions:
            self.skill_cycle_status.setText("이 택틱에서는 스킬 사이클 미지정 · 필요할 때 아래 버튼으로 불러오세요.")
            return
        filled = sum(1 for action in actions if str(action).strip())
        source = str(unit.skill_cycle_source or "직접 편집")
        self.skill_cycle_status.setText(f"{source} · T1~T{len(actions)} · 행동 {filled}개")

    def _edit_skill_cycles(self) -> None:
        unit = self._current()
        if unit is None or unit.doll_id is None:
            return
        adapter = _TacticUnitSkillCycleAdapter(unit)
        dialog = DollSkillCycleDialog(
            adapter, doll_id=int(unit.doll_id), doll_name=f"{unit.name} · 이 택틱 전용", parent=self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if unit.skill_cycle:
                unit.skill_cycle_source = "직접 편집"
            self._refresh_skill_cycle_status(unit)

    def _load_general_cycle(self) -> None:
        unit = self._current()
        if unit is None or unit.doll_id is None:
            return
        actions = self.skill_cycle_store.actions_for(int(unit.doll_id))
        if not actions:
            QMessageBox.information(self, "일반 스킬 사이클", "보유 현황에 저장된 일반 사이클이 없습니다.")
            return
        unit.skill_cycle = list(actions)
        unit.skill_cycle_source = "일반 사이클"
        self._refresh_skill_cycle_status(unit)

    def _load_formation_cycle(self) -> None:
        unit = self._current()
        if unit is None or unit.doll_id is None:
            return
        candidates = formation_cycle_candidates(self.repo, self.formation_cycle_store, int(unit.doll_id))
        if not candidates:
            QMessageBox.information(self, "제대 스킬 사이클", "이 인형에 저장된 제대 전용 사이클이 없습니다.")
            return
        labels = [f"{row['plan_name']} · {int(row['position'])}번 슬롯 · T1~T{len(row['actions'])}" for row in candidates]
        default_index = 0
        for index, row in enumerate(candidates):
            if unit.formation_plan_id == int(row['plan_id']) and unit.formation_position == int(row['position']):
                default_index = index
                break
        selected, ok = QInputDialog.getItem(
            self, "제대 스킬 사이클", "불러올 제대 사이클을 선택하세요.", labels, default_index, False
        )
        if not ok or str(selected) not in labels:
            return
        row = candidates[labels.index(str(selected))]
        unit.skill_cycle = list(row['actions'])
        unit.skill_cycle_source = f"제대 · {row['plan_name']}"[:120]
        unit.formation_plan_id = int(row['plan_id'])
        unit.formation_position = int(row['position'])
        self._refresh_skill_cycle_status(unit)

    def _clear_skill_cycle(self) -> None:
        unit = self._current()
        if unit is None:
            return
        unit.skill_cycle = []
        unit.skill_cycle_source = ""
        self._refresh_skill_cycle_status(unit)

    @staticmethod
    def _selected_values(combos: list[QComboBox]) -> list[str]:
        values: list[str] = []
        for combo in combos:
            value = TacticUnitsDialog._clean_combo_value(combo.currentText())
            if value and value not in values:
                values.append(value)
        return values[: TacticUnitsDialog.KEY_SLOT_COUNT]

    def _fields_changed(self, *_args) -> None:
        if self._updating:
            return
        unit = self._current()
        if unit is None:
            return
        unit.alias = self.alias.currentText().strip()[:12]
        unit.rank = int(self.rank.value())
        unit.weapon = self._clean_combo_value(self.weapon.currentText())
        unit.common_keys = self._selected_values(self.common_keys)
        unit.unique_keys = self._selected_values(self.unique_keys)
        unit.expansion_level = int(self.expansion_level.currentData() or 0)
        item = self.list.currentItem()
        if item is not None:
            summary = self._unit_summary(unit)
            item.setText(
                f"{unit.name or '인형'}  ·  {unit.display_label()}  ·  {unit.rank}돌"
                + (f"\n{summary}" if summary else "")
            )
        self.selected_name.setText(f"{unit.name}  ·  오버레이 {unit.display_label()}")
        self._refresh_skill_cycle_status(unit)

    @staticmethod
    def _clean_combo_value(value: str) -> str:
        text = str(value or "").strip()
        return text.split(" (Lv.", 1)[0][:80]

    def _apply_imported_values(self) -> None:
        unit = self._current()
        imported = self._imported_row(unit)
        if unit is None or not imported:
            return
        self._updating = True
        try:
            weapon_rows = [dict(item) for item in imported.get("weapons") or [] if isinstance(item, dict)]
            if weapon_rows:
                self.weapon.setCurrentText(self._item_text(weapon_rows[0], weapon=True))
            for combos, key in ((self.common_keys, "common_keys"), (self.unique_keys, "fixed_keys")):
                rows = [dict(item) for item in imported.get(key) or [] if isinstance(item, dict)][: self.KEY_SLOT_COUNT]
                for index, combo in enumerate(combos):
                    combo.setCurrentText(self._item_text(rows[index]) if index < len(rows) else "")
            self.expansion_level.setCurrentIndex(1 if imported.get("expansion_keys") else 0)
        finally:
            self._updating = False
        self._fields_changed()

    def _accept(self) -> None:
        self._fields_changed()
        self.result_units = [TacticUnit.from_dict(asdict(unit)) for unit in self.units]
        self.accept()
