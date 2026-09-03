from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QThreadPool
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ... import reference
from ...atomic_io import atomic_write_json
from ...repository import Repository
from ...services.doll_categories import DollCategoryStore
from ..images import PortraitLoader
from ..jobs.revision import result_is_current
from ..jobs.remolding_optimizer import allocate_owned_remoldings
from .category_assign import DollCategoryAssignDialog
from ..models import (
    DataTableModel,
    DollCardDelegate,
    DollFilterProxy,
    DollListModel,
    DollListView,
    ENTRY_ROLE,
    TABLE_ROW_ROLE,
)
from ..widgets import (
    BusyButton,
    CancellableJobDialogMixin,
    ResultDialog,
    configure_table_view,
    dialog_layout,
    replace_table_model,
    show_error,
)
from ..workers import run_cancellable_worker


class SubsetAllocationDialog(CancellableJobDialogMixin, QDialog):
    """Allocate owned remoldings across a visually selected character subset."""

    def __init__(
        self,
        repo: Repository,
        entries: list[dict[str, Any]],
        portraits: PortraitLoader,
        parent=None,
        *,
        character_level_override: int = 60,
    ):
        super().__init__(parent)
        self.repo = repo
        self.repo_path = Path(repo.path)
        self.entries = [dict(entry) for entry in entries]
        self.portraits = portraits
        self.pool = QThreadPool.globalInstance()
        self.category_store = DollCategoryStore(repo.path.parent)
        self.result: dict | None = None
        self.character_level_override = max(0, min(60, int(character_level_override)))
        self._job_active = False
        self._job_handle = None

        self.setWindowTitle("선택 캐릭터 리몰딩 자동 배치")
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        root = dialog_layout(self)
        self._build_toolbar(root)
        self._build_content(root)
        self._build_actions(root)
        self._connect_signals()
        self._refresh_categories()
        self._filter()

    def _build_toolbar(self, root) -> None:
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("인형 검색")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, 1)

        self.category_filter = QComboBox()
        self.category_filter.addItem("카테고리 전체", "")
        toolbar.addWidget(self.category_filter)
        self.assign_category = QPushButton("선택 → 카테고리")
        self.remove_category = QPushButton("카테고리에서 제거")
        toolbar.addWidget(self.assign_category)
        toolbar.addWidget(self.remove_category)
        self.select_all = QPushButton("현재 목록 전체 선택")
        self.clear = QPushButton("선택 해제")
        toolbar.addWidget(self.select_all)
        toolbar.addWidget(self.clear)
        root.addLayout(toolbar)

    def _build_content(self, root) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        self.model = DollListModel(self.entries, portraits=self.portraits)
        self.proxy = DollFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(0)

        self.view = DollListView()
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.view.setGridSize(QSize(132, 142))
        self.view.setModel(self.proxy)
        self.delegate = DollCardDelegate(self.view, show_favorite=False)
        self.view.setItemDelegate(self.delegate)
        splitter.addWidget(self.view)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.status = QLabel("계산할 캐릭터를 카드에서 선택하세요.")
        self.status.setObjectName("Muted")
        right_layout.addWidget(self.status)

        selected_title = QLabel("선택 인형")
        selected_title.setObjectName("SectionTitle")
        right_layout.addWidget(selected_title)
        self.selected_preview = QListWidget()
        self.selected_preview.setViewMode(QListView.ViewMode.IconMode)
        self.selected_preview.setFlow(QListView.Flow.LeftToRight)
        self.selected_preview.setWrapping(True)
        self.selected_preview.setResizeMode(QListView.ResizeMode.Adjust)
        self.selected_preview.setMovement(QListView.Movement.Static)
        self.selected_preview.setIconSize(QSize(82, 82))
        self.selected_preview.setGridSize(QSize(112, 118))
        self.selected_preview.setMinimumHeight(128)
        self.selected_preview.setMaximumHeight(270)
        self.selected_preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.selected_preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.selected_preview.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.selected_preview.setToolTip("선택 인형은 창 폭에 맞춰 자동 줄바꿈되며 세로로만 스크롤됩니다.")
        right_layout.addWidget(self.selected_preview)

        self.table = QTableView()
        self.table.setToolTip(
            "행을 더블클릭하면 장착 리몰딩과 목표 / 현재 전체 스탯을 엽니다."
        )
        right_layout.addWidget(self.table, 2)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        right_layout.addWidget(self.detail, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

    def _build_actions(self, root) -> None:
        actions = QHBoxLayout()
        self.calc = BusyButton("선택 대상 계산")
        self.calc.setObjectName("AccentButton")
        self.export = QPushButton("결과 JSON 저장")
        self.export.setEnabled(False)
        actions.addWidget(self.calc)
        actions.addWidget(self.export)
        actions.addStretch(1)

        self.close_btn = QPushButton("닫기")
        actions.addWidget(self.close_btn)
        root.addLayout(actions)

    def _connect_signals(self) -> None:
        self.search.textChanged.connect(self._filter)
        self.category_filter.currentIndexChanged.connect(self._filter)
        self.assign_category.clicked.connect(self._assign_category)
        self.remove_category.clicked.connect(self._remove_from_category)
        self.select_all.clicked.connect(self.view.selectAll)
        self.clear.clicked.connect(self.view.clearSelection)
        self.calc.clicked.connect(self._calculate)
        self.export.clicked.connect(self._export)
        self.table.clicked.connect(self._show_detail)
        self.table.doubleClicked.connect(self._open_detail)
        self.close_btn.clicked.connect(self.reject)
        self.view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.portraits.imageReady.connect(self._selected_portrait_ready)
        self._update_selected_preview()

    def _refresh_categories(self) -> None:
        current = str(self.category_filter.currentData() or "")
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("카테고리 전체", "")
        for name in self.category_store.names():
            self.category_filter.addItem(f"{name} ({len(self.category_store.keys(name))})", name)
        index = self.category_filter.findData(current)
        self.category_filter.setCurrentIndex(index if index >= 0 else 0)
        self.category_filter.blockSignals(False)
        self.remove_category.setEnabled(bool(self.category_filter.currentData()))

    def _filter(self) -> None:
        category = str(self.category_filter.currentData() or "").strip()
        self.proxy.set_filters(
            query=self.search.text(),
            favorites_only=False,
            allowed_character_keys=self.category_store.keys(category) if category else None,
        )
        self.remove_category.setEnabled(bool(category))

    def _assign_category(self) -> None:
        keys = self._selected_keys()
        if not keys:
            QMessageBox.information(self, "인형 카테고리", "먼저 카테고리에 넣을 인형을 선택하세요.")
            return
        dialog = DollCategoryAssignDialog(self.category_store, count=len(keys), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.result_name
        try:
            self.category_store.assign(name, keys)
        except Exception as exc:
            show_error(self, "인형 카테고리 저장 실패", exc)
            return
        self._refresh_categories()
        self._filter()
        self.status.setText(f"선택 {len(keys)}명을 '{str(name).strip()}' 카테고리에 추가했습니다.")

    def _remove_from_category(self) -> None:
        keys = self._selected_keys()
        category = str(self.category_filter.currentData() or "").strip()
        if not keys or not category:
            return
        try:
            self.category_store.remove(category, keys)
        except Exception as exc:
            show_error(self, "인형 카테고리 저장 실패", exc)
            return
        self._refresh_categories()
        self._filter()

    def _selected_keys(self) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for index in self.view.selectionModel().selectedIndexes():
            entry = index.data(ENTRY_ROLE) or {}
            key = str(entry.get("character_key") or "")
            if key and key not in seen:
                seen.add(key)
                selected.append(key)
        return selected

    def _selection_changed(self, _selected=None, _deselected=None) -> None:
        keys = self._selected_keys()
        if keys:
            self.status.setText(f"선택 {len(keys)}명 · 계산 버튼을 누르면 이 인형들만 중복 없이 자동 배치합니다.")
            self.status.setObjectName("AccentText")
        else:
            self.status.setText("계산할 캐릭터를 카드에서 선택하세요.")
            self.status.setObjectName("Muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self._update_selected_preview()

    def _entry_for_key(self, character_key: str) -> dict[str, Any]:
        return next(
            (entry for entry in self.entries if str(entry.get("character_key") or "") == str(character_key)),
            {},
        )

    def _update_selected_preview(self) -> None:
        keys = self._selected_keys()
        self.selected_preview.clear()
        if not keys:
            item = QListWidgetItem("선택 없음")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.selected_preview.addItem(item)
            return
        for key in keys:
            entry = self._entry_for_key(key)
            item = QListWidgetItem(str(entry.get("name") or key))
            item.setData(Qt.ItemDataRole.UserRole, str(entry.get("portrait_path") or ""))
            path = str(entry.get("portrait_path") or "")
            image = self.portraits.get(path) if path else None
            if image is not None and not image.isNull():
                pixmap = QPixmap.fromImage(image).scaled(
                    self.selected_preview.iconSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item.setIcon(QIcon(pixmap))
            elif path:
                self.portraits.request(path)
            self.selected_preview.addItem(item)

    def _selected_portrait_ready(self, path: str, _image) -> None:
        if any(
            str(self.selected_preview.item(row).data(Qt.ItemDataRole.UserRole) or "") == str(path)
            for row in range(self.selected_preview.count())
        ):
            self._update_selected_preview()

    def _calculate(self) -> None:
        keys = self._selected_keys()
        if not keys:
            QMessageBox.information(
                self,
                "선택 자동 배치",
                "한 명 이상 선택하세요.",
            )
            return

        self._job_active = True
        self.calc.set_busy(True, "계산 중…")
        self.close_btn.setEnabled(False)
        self.export.setEnabled(False)
        self.status.setText(f"{len(keys)}명 계산 중…")

        database = str(self.repo_path)
        request_token = self.repo.state_token()
        self._job_handle = run_cancellable_worker(
            self.pool,
            lambda should_cancel: allocate_owned_remoldings(
                database,
                keys,
                request_token,
                should_cancel,
                character_level_override=self.character_level_override,
            ),
            on_result=self._allocation_ready,
            on_error=lambda error: show_error(self, "배치 계산 실패", error),
            on_finished=self._finish_job,
        )

    def _finish_job(self) -> None:
        self._job_handle = None
        self._job_active = False
        self.calc.set_busy(False)
        self.close_btn.setEnabled(True)

    def _allocation_ready(self, payload) -> None:
        request_token, worker_start, worker_end, result = payload
        if not result_is_current(
            request_token,
            worker_start,
            worker_end,
            self.repo.state_token(),
        ):
            self.status.setText(
                "계산 중 보유 데이터가 변경되었습니다. 다시 계산하세요."
            )
            return
        self._show_result(result)

    def _show_result(self, result: dict) -> None:
        self.result = result
        prepared: list[dict] = []
        for row_index, row in enumerate(result.get("rows", [])):
            phenomenon = row.get("phenomenon_status") or {}
            desired = phenomenon.get("desired") or {}
            target = row.get("target_status") or []
            piece_count = len(row.get("pieces") or [])
            missing = int(row.get("missing") or 0)
            score = float(row.get("score") or 0)
            name = str(
                (row.get("character") or {}).get("nameKR")
                or row.get("character_key")
            )
            prepared.append(
                {
                    "row_index": row_index,
                    "name": name,
                    "allocation": f"{piece_count}/{piece_count + missing}",
                    "score": score,
                    "score_text": f"{score:,.0f}",
                    "phenomenon": (
                        f"{phenomenon.get('desired_stage', '—')} "
                        f"{'✓' if desired.get('active') else '미달'}"
                    ),
                    "target": f"{sum(1 for item in target if item.get('met'))}/{len(target)}",
                }
            )

        model = DataTableModel(
            prepared,
            [
                ("인형", "name"),
                ("배치", "allocation"),
                ("점수", "score_text"),
                ("현상", "phenomenon"),
                ("목표", "target"),
            ],
            self,
            sort_getters=["name", "allocation", "score", "phenomenon", "target"],
        )
        replace_table_model(self.table, model)
        configure_table_view(self.table, select_rows=True)
        self.status.setText(
            f"{result.get('characters', 0)}명 · "
            f"총점 {float(result.get('total_score') or 0):,.0f} · "
            f"미배치 슬롯 {int(result.get('missing_slots') or 0)}"
        )
        self.export.setEnabled(True)
        if model.rowCount():
            self.table.selectRow(0)
            self._show_detail(model.index(0, 0))

    def _result_row(self, index) -> dict | None:
        if not self.result or not index.isValid():
            return None
        payload = self.table.model().index(index.row(), 0).data(TABLE_ROW_ROLE) or {}
        row_index = payload.get("row_index")
        rows = self.result.get("rows") or []
        if row_index is None or int(row_index) >= len(rows):
            return None
        return rows[int(row_index)]

    def _show_detail(self, index) -> None:
        row = self._result_row(index)
        if row is None:
            return

        factors = reference.remolding_rules().get("factor_names", {})
        options = reference.remolding_options()
        lines = [
            f"{(row.get('character') or {}).get('nameKR', '')} · "
            f"{float(row.get('score') or 0):,.0f}점",
            f"배치 {len(row.get('pieces') or [])}개 · "
            f"부족 {int(row.get('missing') or 0)}개",
            "",
        ]
        for index_value, piece in enumerate(row.get("pieces") or [], 1):
            attributes: list[str] = []
            for slot in piece.get("slots") or []:
                meta = options.get(str(slot.get("option_key") or ""), {})
                name = meta.get("nameKR") or slot.get("name") or "옵션"
                level = int(
                    slot.get("level_contribution") or slot.get("variant") or 0
                )
                attributes.append(str(name) + (f" +{level}" if level else ""))
            factor = piece.get("primary_factor")
            lines.append(
                f"{index_value}. {factors.get(factor, factor)} · "
                f"{' / '.join(attributes)}"
            )
        self.detail.setText("\n".join(lines))

    def _open_detail(self, index) -> None:
        row = self._result_row(index)
        if row is None:
            return
        factors = reference.remolding_rules().get("factor_names", {})
        name = str(
            (row.get("character") or {}).get("nameKR")
            or row.get("character_key")
            or "배치 결과"
        )
        dialog = ResultDialog(f"{name} · 리몰딩 결과", self)
        dialog.add_title(name)
        dialog.add_remolding_result(row, factors, reference.remolding_options())
        dialog.add_stats_comparison(
            phenomenon_status=row.get("phenomenon_status"),
            aggregate_levels=row.get("aggregate_levels"),
            target_status=row.get("target_status"),
            factor_names=factors,
        )
        dialog.exec()

    def _export(self) -> None:
        if not self.result:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "선택 캐릭터 리몰딩 결과 저장",
            "gfl2-remolding-selected-allocation.json",
            "JSON (*.json)",
        )
        if path:
            atomic_write_json(path, self.result, ensure_ascii=False, indent=2)
