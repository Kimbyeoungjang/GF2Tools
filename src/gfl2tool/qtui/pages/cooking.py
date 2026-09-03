from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from ...cooking import (
    LEVEL_STAGE_CAPACITY,
    cooking_progress_from_state,
    exact_completion_options,
    ingredient_requirements,
    load_permanent_dishes,
)
from ..widgets import MetricCard, page_layout, section_panel
from .base import DeferredRefreshPage


class CookingPage(DeferredRefreshPage):
    def __init__(self, repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.dishes = load_permanent_dishes()

        root = page_layout(self, "활동층 요리 계산기")
        formula = QLabel("Lv.1 10 · Lv.2 누적 25 · Lv.3 누적 50 · 일반 성공 +4 · 대성공 +5")
        formula.setObjectName("Muted")
        root.addWidget(formula)

        root.addWidget(self._build_recipe_panel())
        root.addWidget(self._build_progress_panel())
        root.addWidget(self._build_ingredient_panel(), 1)

        self.category.currentTextChanged.connect(self._reload_dishes)
        self.dish.currentIndexChanged.connect(self._reload_recipes)
        self.recipe.currentIndexChanged.connect(self._recalculate)
        self.cook_count.valueChanged.connect(self._recalculate)
        self.character_count.valueChanged.connect(self._recalculate)
        self.current_level.currentIndexChanged.connect(self._level_changed)
        self.current_fullness.valueChanged.connect(self._recalculate)
        self.normal.valueChanged.connect(self._recalculate)
        self.great.valueChanged.connect(self._recalculate)
        self.use_minimum.clicked.connect(self._apply_minimum_count)
        self.use_safe.clicked.connect(self._apply_safe_count)
        self._reload_dishes()
        self._level_changed()

    def _build_recipe_panel(self):
        panel, layout = section_panel("인형 영구 스탯 요리")
        select_row = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(["전체", "기본 능력", "위상 속성", "범용 위상"])
        self.dish = QComboBox()
        self.recipe = QComboBox()
        select_row.addWidget(QLabel("스탯 분류"))
        select_row.addWidget(self.category)
        select_row.addSpacing(8)
        select_row.addWidget(QLabel("대상 스탯"))
        select_row.addWidget(self.dish, 1)
        select_row.addSpacing(8)
        select_row.addWidget(QLabel("레시피"))
        select_row.addWidget(self.recipe, 2)
        layout.addLayout(select_row)

        count_row = QHBoxLayout()
        self.character_count = QSpinBox()
        self.character_count.setRange(1, 100)
        self.character_count.setValue(1)
        self.character_count.setSuffix("명")
        self.character_count.setToolTip("같은 조건으로 요리를 먹일 인형 수입니다. 필요 재료와 전체 조리 횟수에 반영됩니다.")
        self.cook_count = QSpinBox()
        self.cook_count.setRange(0, 9999)
        self.cook_count.setValue(10)
        self.use_minimum = QPushButton("최소 횟수 적용")
        self.use_safe = QPushButton("안전 횟수 적용")
        count_row.addWidget(QLabel("먹일 인형 수"))
        count_row.addWidget(self.character_count)
        count_row.addSpacing(12)
        count_row.addWidget(QLabel("인형 1명당 조리 횟수"))
        count_row.addWidget(self.cook_count)
        count_row.addWidget(self.use_minimum)
        count_row.addWidget(self.use_safe)
        count_row.addStretch(1)
        layout.addLayout(count_row)
        return panel

    def _build_progress_panel(self):
        panel, layout = section_panel("현재 포만감")

        current_row = QHBoxLayout()
        self.current_level = QComboBox()
        for level in range(4):
            self.current_level.addItem(f"Lv.{level}", level)
        self.current_fullness = QSpinBox()
        self.current_fullness.setRange(0, LEVEL_STAGE_CAPACITY[0])
        current_row.addWidget(QLabel("현재 단계"))
        current_row.addWidget(self.current_level)
        current_row.addSpacing(12)
        current_row.addWidget(QLabel("현재 단계 포만감"))
        current_row.addWidget(self.current_fullness)
        current_row.addStretch(1)
        layout.addLayout(current_row)

        result_row = QHBoxLayout()
        self.normal = QSpinBox()
        self.normal.setRange(0, 9999)
        self.great = QSpinBox()
        self.great.setRange(0, 9999)
        result_row.addWidget(QLabel("추가 일반 성공"))
        result_row.addWidget(self.normal)
        result_row.addSpacing(12)
        result_row.addWidget(QLabel("추가 대성공"))
        result_row.addWidget(self.great)
        result_row.addStretch(1)
        layout.addLayout(result_row)

        metrics = QGridLayout()
        self.points = MetricCard("누적 포만감", "0 / 50")
        self.level = MetricCard("현재 단계", "Lv.0")
        self.next_level = MetricCard("다음 단계까지", "10")
        self.more = MetricCard("Lv.3까지 추가 요리", "1명 기준 10~13회")
        for index, card in enumerate((self.points, self.level, self.next_level, self.more)):
            metrics.addWidget(card, 0, index)
        layout.addLayout(metrics)

        self.completion = QLabel("")
        self.completion.setObjectName("Muted")
        self.completion.setWordWrap(True)
        layout.addWidget(self.completion)
        return panel

    def _build_ingredient_panel(self):
        panel, layout = section_panel("필요 재료")
        self.ingredients = QTableWidget(0, 2)
        self.ingredients.setHorizontalHeaderLabels(["재료", "필요 개수"])
        self.ingredients.verticalHeader().setVisible(False)
        self.ingredients.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.ingredients.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.ingredients)
        return panel

    def refresh(self) -> None:
        self._recalculate()

    def _current_dish(self) -> dict:
        value = self.dish.currentData()
        return dict(value) if isinstance(value, dict) else {}

    def _reload_dishes(self) -> None:
        current = self.dish.currentText()
        category = self.category.currentText()
        self.dish.blockSignals(True)
        self.dish.clear()
        for row in self.dishes:
            if category != "전체" and row.get("category") != category:
                continue
            label = str(row.get("effect") or row.get("source_name") or "스탯 요리")
            self.dish.addItem(label, row)
        index = self.dish.findText(current)
        self.dish.setCurrentIndex(index if index >= 0 else 0)
        self.dish.blockSignals(False)
        self._reload_recipes()

    def _reload_recipes(self) -> None:
        row = self._current_dish()
        recipes = list(row.get("recipes") or [])
        best = int(row.get("recommended_recipe_index") or 0)
        self.recipe.blockSignals(True)
        self.recipe.clear()
        for index, recipe in enumerate(recipes):
            ingredients = [str(value) for value in recipe.get("ingredients") or []]
            marker = "추천 · " if index == best else ""
            self.recipe.addItem(marker + " + ".join(ingredients), recipe)
        if recipes:
            self.recipe.setCurrentIndex(max(0, min(best, len(recipes) - 1)))
        self.recipe.blockSignals(False)
        self._recalculate()

    def _level_changed(self) -> None:
        level = int(self.current_level.currentData() or 0)
        capacity = LEVEL_STAGE_CAPACITY[level]
        current = self.current_fullness.value()
        self.current_fullness.blockSignals(True)
        self.current_fullness.setRange(0, capacity)
        self.current_fullness.setValue(min(current, capacity))
        self.current_fullness.setSuffix(f" / {capacity}" if capacity else "")
        self.current_fullness.setEnabled(level < 3)
        self.current_fullness.blockSignals(False)
        self._recalculate()

    def _progress(self):
        return cooking_progress_from_state(
            int(self.current_level.currentData() or 0),
            self.current_fullness.value(),
            self.normal.value(),
            self.great.value(),
        )

    def _apply_minimum_count(self) -> None:
        self.cook_count.setValue(self._progress().min_more)

    def _apply_safe_count(self) -> None:
        self.cook_count.setValue(self._progress().max_more)

    def _recalculate(self) -> None:
        progress = self._progress()
        self.points.value.setText(f"{progress.points} / 50")
        self.level.value.setText(f"Lv.{progress.level}")
        if progress.next_threshold is None:
            self.next_level.value.setText("완료")
        else:
            self.next_level.value.setText(f"{progress.remaining_to_next} 포인트")
        count = max(1, int(self.character_count.value()))
        if not progress.remaining_total:
            self.more.value.setText("완료")
        elif count == 1:
            self.more.value.setText(f"1명 기준 {progress.min_more}~{progress.max_more}회")
        else:
            self.more.value.setText(
                f"1명 {progress.min_more}~{progress.max_more}회 · 총 {progress.min_more * count}~{progress.max_more * count}회"
            )

        options = exact_completion_options(progress.remaining_total)
        if progress.remaining_total:
            examples = [
                f"대성공 {great} + 일반 {normal}" + (f" (초과 {excess})" if excess else "")
                for great, normal, excess in options[:3]
            ]
            total_min = progress.min_more * count
            total_max = progress.max_more * count
            total_text = (
                f" · {count}명 전체 기준 최소 {total_min}회 / 안전 {total_max}회" if count > 1 else ""
            )
            self.completion.setText(
                f"1명 기준 Lv.3까지 {progress.remaining_total}포인트 남음 · 최소 {progress.min_more}회(전부 대성공) / "
                f"안전 {progress.max_more}회(전부 일반 성공){total_text} · 예: " + " / ".join(examples)
            )
        else:
            self.completion.setText("Lv.3 포만감 50을 모두 채웠습니다.")

        recipe = self.recipe.currentData()
        ingredients = list(recipe.get("ingredients") or []) if isinstance(recipe, dict) else []
        totals = ingredient_requirements(ingredients, self.cook_count.value() * max(1, int(self.character_count.value())))
        self.ingredients.setRowCount(len(totals))
        for row_index, (name, amount) in enumerate(totals.items()):
            self.ingredients.setItem(row_index, 0, QTableWidgetItem(name))
            self.ingredients.setItem(row_index, 1, QTableWidgetItem(f"{amount:,}"))
