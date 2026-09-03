from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from ... import reference
from ...repository import Repository
from ...services.dolls import DollCharacterResolver
from ...services.formations import FormationService
from ...services.optimizer import EquipmentOptimizer
from ..data import remolding_meta
from ..jobs.formation import optimize_formation
from ..jobs.revision import result_is_current
from ..formation_widgets import RemoldingPieceSummaryWidget
from ..models import DataTableModel, TABLE_ROW_ROLE
from ..widgets import (
    BusyButton,
    CancellableJobDialogMixin,
    MetricCard,
    ResultDialog,
    configure_table_view,
    dialog_layout,
    page_title,
    replace_table_model,
    show_error,
)
from ..workers import run_cancellable_worker


def _member_remolding_result(
    repo: Repository,
    doll_id: int,
    uids: list[str],
    targets: dict[str, Any] | None = None,
    *,
    global_level_override: int | None = None,
) -> dict[str, Any] | None:
    """Rehydrate one member result without constructing the global allocator.

    The detail popup only needs doll identity/level plus 리몰딩 추천 scoring. Building
    the formation-wide optimizer on this GUI click path is unnecessary, so the
    lightweight resolver is used instead.
    """
    resolver = DollCharacterResolver(repo)
    character_key = resolver.character_key_for_doll(int(doll_id))
    if not character_key:
        return None

    service = resolver.recommendation
    base_pieces: list[dict[str, Any]] = []
    for row in repo.remoldings_by_uids([str(uid) for uid in uids]):
        meta = remolding_meta(row)
        base_pieces.append(
            {
                "uid": str(row.get("uid") or ""),
                "remolding_id": int(row.get("remolding_id") or 0),
                "slots": list(meta.get("slots") or []),
                "primary_factor": meta.get("primary_factor"),
            }
        )

    pieces = service.score_remolding_pieces(
        character_key,
        base_pieces,
        sort_results=False,
    )
    character = service.get_character(character_key)
    required = sum(
        int(row.get("count") or 0)
        for row in character.get("slotDistribution", [])
    )
    level = resolver.calculation_level_for_key(
        character_key, 60 if global_level_override is None else global_level_override
    )
    target_specs = dict(targets or {}) or service.get_target_profile(character_key)

    return {
        "character_key": character_key,
        "character": character,
        "pieces": pieces,
        "score": sum(float(piece.get("score") or 0) for piece in pieces),
        "missing": max(0, required - len(pieces)),
        "character_level": level,
        "phenomenon_status": service.phenomenon_status(
            character_key,
            pieces,
            character_level=level,
        ),
        "aggregate_levels": service.aggregate_option_levels(pieces),
        "target_status": service.target_status(pieces, target_specs),
        "target_specs": target_specs,
        "piece_summary": service.equipped_piece_display(character_key, pieces),
    }

class FormationOptimizeDialog(CancellableJobDialogMixin, QDialog):
    """Stable, non-nested table dialog for formation remolding allocation."""

    def __init__(
        self,
        repo: Repository,
        plan_id: int,
        parent=None,
        *,
        global_level_override: int | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("제대 자동 배치")
        self.resize(980, 680)
        self.setMinimumSize(820, 560)

        self.repo = repo
        self.repo_path = repo.path
        self.plan_id = plan_id
        self.global_level_override = global_level_override
        self.result: dict | None = None
        self._result_request_token = None
        self._result_visuals: dict[int, dict[str, Any]] = {}
        self._preview_dirty = False
        self._close_confirmed = False
        self.pool = QThreadPool.globalInstance()
        self._job_active = False
        self._job_handle = None

        root = dialog_layout(self)
        root.addWidget(
            page_title(
                "제대 자동 배치",
                "현재 제대의 인형에게 보유 리몰딩을 중복 없이 배치합니다. "
                "리몰딩 계산은 기본 Lv.60 전역값을 사용하며 인형별 계산 레벨이 전역값보다 우선합니다. "
                "행을 더블클릭하면 기존 인형 상세/목표 편집을 열고, 계산 후 결과 상세 버튼에서 추천 배치를 확인합니다.",
            )
        )

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        self.members_metric = MetricCard("제대 인원", "0 / 6")
        self.remoldings_metric = MetricCard("배치 리몰딩", "0개")
        self.score_metric = MetricCard("추천 총점", "—")
        metrics.addWidget(self.members_metric, 0, 0)
        metrics.addWidget(self.remoldings_metric, 0, 1)
        metrics.addWidget(self.score_metric, 0, 2)
        root.addLayout(metrics)

        preview = QFrame()
        preview.setObjectName("Panel")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        preview_layout.setSpacing(8)

        preview_title = QLabel("배치 미리보기")
        preview_title.setObjectName("SectionTitle")
        preview_layout.addWidget(preview_title)

        self.summary = QLabel("현재 제대 구성을 확인한 뒤 배치 계산을 실행하세요.")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        preview_layout.addWidget(self.summary)

        preview_body = QHBoxLayout()
        preview_body.setSpacing(10)

        self.table = QTableView()
        self.table.setMinimumHeight(300)
        self.table.setToolTip(
            "계산하면 추천 리몰딩 패턴이 즉시 표와 오른쪽 미리보기에 표시됩니다. "
            "행을 더블클릭하면 인형 상세/목표 편집을 엽니다."
        )
        self.table.doubleClicked.connect(self._open_member_editor)
        preview_body.addWidget(self.table, 3)

        inline_panel = QFrame()
        inline_panel.setObjectName("PanelAlt")
        inline_panel.setMinimumWidth(360)
        inline_layout = QVBoxLayout(inline_panel)
        inline_layout.setContentsMargins(12, 12, 12, 12)
        inline_layout.setSpacing(7)
        inline_title = QLabel("선택 인형 · 추천 배치 미리보기")
        inline_title.setObjectName("SectionTitle")
        inline_layout.addWidget(inline_title)
        self.inline_pattern_name = QLabel("추천 배치를 계산하면 실제 6개 리몰딩이 바로 표시됩니다.")
        self.inline_pattern_name.setObjectName("Muted")
        self.inline_pattern_name.setWordWrap(True)
        inline_layout.addWidget(self.inline_pattern_name)
        self.inline_pattern = RemoldingPieceSummaryWidget(columns=2)
        self.inline_pattern.set_summary(None)
        inline_layout.addWidget(self.inline_pattern, 1)
        preview_body.addWidget(inline_panel, 2)

        preview_layout.addLayout(preview_body, 1)
        root.addWidget(preview, 1)

        action_row = QHBoxLayout()
        self.member_detail = QPushButton("인형 자세히 보기")
        self.member_detail.setToolTip("현재 행의 인형 상세 팝업을 열어 리몰딩 목표도 바로 수정합니다.")
        self.result_detail = QPushButton("계산 결과 상세")
        self.result_detail.setEnabled(False)
        action_row.addWidget(self.member_detail)
        action_row.addWidget(self.result_detail)
        action_row.addStretch(1)
        self.calc = BusyButton("추천 배치 계산")
        self.calc.setObjectName("AccentButton")
        self.apply = QPushButton("이 배치 적용 · 저장")
        self.apply.setObjectName("AccentButton")
        self.apply.setEnabled(False)
        action_row.addWidget(self.calc)
        action_row.addWidget(self.apply)
        root.addLayout(action_row)
        self._show_initial_members()

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        self.close_button = self.button_box.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText("닫기")
        root.addWidget(self.button_box)

        self.member_detail.clicked.connect(self._open_selected_member_editor)
        self.result_detail.clicked.connect(self._open_selected_result)
        self.calc.clicked.connect(self.calculate)
        self.apply.clicked.connect(self.apply_result)

    @staticmethod
    def _selected_categories() -> set[str]:
        return {"remolding"}

    def _plan(self) -> dict:
        with Repository(str(self.repo_path)) as repo:
            return FormationService(repo).get(self.plan_id)

    def _member_names(self) -> dict[int, str]:
        return {
            int(member["position"]): str(member.get("doll_name") or member.get("doll_id"))
            for member in self._plan().get("members", [])
        }

    def _show_initial_members(self) -> None:
        plan = self._plan()
        rows: list[dict[str, Any]] = []
        resolver = DollCharacterResolver(self.repo)
        for member in sorted(plan.get("members", []), key=lambda row: int(row.get("position") or 0)):
            key = resolver.character_key_for_doll(int(member.get("doll_id") or 0))
            level = (
                resolver.calculation_level_for_key(key, self.global_level_override)
                if key
                else 60
            )
            rows.append(
                {
                    "position": int(member.get("position") or 0),
                    "name": str(member.get("doll_name") or member.get("doll_id") or ""),
                    "level": f"Lv.{level}",
                    "remoldings": len(member.get("remolding_uids") or []),
                    "score": None,
                    "score_text": "—",
                    "pattern": "—",
                }
            )
        self._set_table_rows(rows)
        assigned = sum(int(row.get("remoldings") or 0) for row in rows)
        self.members_metric.value.setText(f"{len(rows)} / 6")
        self.remoldings_metric.value.setText(f"{assigned}개")
        self.score_metric.value.setText("—")
        if not rows:
            self.summary.setText("제대에 인형이 없습니다. 먼저 제대 슬롯을 구성하세요.")
            self.calc.setEnabled(False)

    def _set_table_rows(self, rows: list[dict[str, Any]]) -> None:
        model = DataTableModel(
            rows,
            [
                ("슬롯", "position"),
                ("인형", "name"),
                ("계산 레벨", "level"),
                ("리몰딩", "remoldings"),
                ("점수", "score_text"),
                ("추천 패턴", "pattern"),
            ],
            self,
            sort_getters=["position", "name", "level", "remoldings", "score", "pattern"],
        )
        replace_table_model(self.table, model)
        configure_table_view(
            self.table,
            widths={0: 64, 1: 150, 2: 92, 3: 78, 4: 90, 5: 480},
            select_rows=True,
        )
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        selection = self.table.selectionModel()
        if selection is not None:
            selection.currentRowChanged.connect(self._inline_selection_changed)


    @staticmethod
    def _piece_summary_text(summary: dict[str, Any] | None) -> str:
        parts: list[str] = []
        for group in list(dict(summary or {}).get("groups") or []):
            for raw_piece in list(group.get("pieces") or []):
                piece = dict(raw_piece)
                label = str(piece.get("label") or piece.get("factor") or "리몰딩")
                index = max(1, int(piece.get("display_index") or 1))
                stats = [
                    f"{row.get('name') or row.get('option_key') or '스탯'} +{int(row.get('level') or 0)}"
                    for row in list(piece.get("stats") or [])
                    if int(row.get("level") or 0) > 0
                ]
                tail = " ".join(stats) if stats else "옵션 없음"
                parts.append(f"{label}{index} · {tail}")
        return " | ".join(parts) if parts else "장착 정보 없음"

    def _inline_selection_changed(self, current, _previous=None) -> None:
        position = self._selected_position(current)
        if position is None:
            self.inline_pattern_name.setText("추천 배치를 계산하면 실제 6개 리몰딩이 바로 표시됩니다.")
            self.inline_pattern.set_summary(None)
            return
        visual = self._result_visuals.get(int(position))
        if not visual:
            self.inline_pattern_name.setText("아직 계산된 추천 배치가 없습니다.")
            self.inline_pattern.set_summary(None)
            return
        name = str((visual.get("character") or {}).get("nameKR") or f"슬롯 {position}")
        self.inline_pattern_name.setText(f"{name} · 계산된 미저장 추천 배치")
        self.inline_pattern.set_summary(dict(visual.get("piece_summary") or {}))

    def _confirm_discard_preview(self) -> bool:
        if self._close_confirmed or not self._preview_dirty or not self.result:
            return True
        answer = QMessageBox.question(
            self,
            "미저장 추천 배치",
            "계산한 추천 배치가 아직 제대 계획에 저장되지 않았습니다.\n\n"
            "'이 배치 적용 · 저장'을 누르지 않고 닫으면 이번 계산 결과는 사라집니다. 정말 닫을까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        confirmed = answer == QMessageBox.StandardButton.Yes
        if confirmed:
            self._close_confirmed = True
        return confirmed

    def reject(self) -> None:
        if not self._confirm_discard_preview():
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._confirm_discard_preview():
            event.ignore()
            return
        super().closeEvent(event)


    def _selected_position(self, index=None) -> int | None:
        target = index if index is not None and index.isValid() else self.table.currentIndex()
        if not target.isValid():
            return None
        payload = target.model().index(target.row(), 0).data(TABLE_ROW_ROLE) or {}
        try:
            return int(payload.get("position"))
        except (TypeError, ValueError):
            return None

    def _open_member_editor(self, index) -> None:
        position = self._selected_position(index)
        if position is None:
            return
        parent = self.parent()
        opener = getattr(parent, "_member_detail", None)
        if callable(opener):
            opener(position)
            # Target edits can change scoring while this dialog is open. Any
            # prior calculation is therefore stale and must be recomputed.
            self.result = None
            self._result_request_token = None
            self._result_visuals.clear()
            self._preview_dirty = False
            self.inline_pattern.set_summary(None)
            self.apply.setEnabled(False)
            self.result_detail.setEnabled(False)
            self.summary.setText("인형 상세/목표를 확인했습니다. 변경했다면 추천 배치를 다시 계산하세요.")
            return
        QMessageBox.information(self, "인형 자세히 보기", "제대 계획 화면에서 열었을 때 상세 편집을 사용할 수 있습니다.")

    def _open_selected_member_editor(self) -> None:
        self._open_member_editor(self.table.currentIndex())

    def _open_selected_result(self) -> None:
        self._open_member_result(self.table.currentIndex())

    def calculate(self) -> None:
        categories = self._selected_categories()
        self._job_active = True
        self.calc.set_busy(True, "계산 중…")
        self.close_button.setEnabled(False)
        self.apply.setEnabled(False)
        self._result_request_token = None
        self.summary.setText("보유 리몰딩을 기준으로 제대 전체 배치를 계산하고 있습니다…")

        database = str(self.repo_path)
        plan_id = self.plan_id
        request_token = self.repo.state_token()
        self._job_handle = run_cancellable_worker(
            self.pool,
            lambda should_cancel: optimize_formation(
                database,
                plan_id,
                categories,
                request_token,
                should_cancel,
                character_level_override=self.global_level_override,
            ),
            on_result=self._optimization_ready,
            on_error=self._optimization_failed,
            on_finished=self._finish_job,
        )

    def _optimization_failed(self, error: str) -> None:
        self.summary.setText("자동 배치 계산에 실패했습니다.")
        show_error(self, "계산 실패", error)

    def _finish_job(self) -> None:
        self._job_handle = None
        self._job_active = False
        self.calc.set_busy(False)
        self.close_button.setEnabled(True)

    def _optimization_ready(self, payload) -> None:
        request_token, worker_start, worker_end, result = payload
        if not result_is_current(
            request_token,
            worker_start,
            worker_end,
            self.repo.state_token(),
        ):
            self.summary.setText("계산 중 데이터가 변경되어 결과를 폐기했습니다.")
            QMessageBox.information(
                self,
                "자동 배치",
                "계산 중 보유 데이터가 변경되어 결과를 폐기했습니다. 다시 계산하세요.",
            )
            return
        self._result_request_token = tuple(request_token)
        self._show(result)

    def _show(self, result: dict) -> None:
        self.result = result
        names = self._member_names()
        self._result_visuals = {}
        prepared: list[dict[str, Any]] = []

        with Repository(str(self.repo_path)) as repo:
            plan = FormationService(repo).get(self.plan_id)
            members = {int(row.get("position") or 0): row for row in plan.get("members", [])}
            for position, row in sorted(
                (result.get("members") or {}).items(),
                key=lambda item: int(item[0]),
            ):
                slot = int(position)
                score = sum(float(value) for value in (row.get("scores") or {}).values())
                member = members.get(slot)
                visual = None
                if member:
                    visual = _member_remolding_result(
                        repo,
                        int(member["doll_id"]),
                        list(row.get("remolding_uids") or []),
                        dict(member.get("remolding_targets") or {}),
                        global_level_override=self.global_level_override,
                    )
                if visual:
                    self._result_visuals[slot] = visual
                prepared.append(
                    {
                        "position": slot,
                        "name": names.get(slot, ""),
                        "level": f"Lv.{int((visual or {}).get('character_level') or 60)}",
                        "remoldings": len(row.get("remolding_uids") or []),
                        "score": score,
                        "score_text": f"{score:,.0f}",
                        "pattern": self._piece_summary_text((visual or {}).get("piece_summary")),
                    }
                )

        self._set_table_rows(prepared)
        assigned = sum(int(row.get("remoldings") or 0) for row in prepared)
        total_score = sum(float(row.get("score") or 0) for row in prepared)
        self.members_metric.value.setText(f"{len(prepared)} / 6")
        self.remoldings_metric.value.setText(f"{assigned}개")
        self.score_metric.value.setText(f"{total_score:,.0f}")
        self.summary.setText(
            "계산 완료 · 추천 패턴을 즉시 미리보기에 반영했습니다. 아직 DB에는 저장되지 않았으며 "
            "'이 배치 적용 · 저장'을 눌러야 제대 계획에 확정됩니다."
        )
        self._preview_dirty = bool(prepared)
        self.apply.setEnabled(bool(prepared))
        self.result_detail.setEnabled(bool(prepared))
        if prepared:
            self.table.selectRow(0)
            self._inline_selection_changed(self.table.currentIndex())

    def _result_row(self, position: int) -> dict | None:
        if not self.result:
            return None
        members = self.result.get("members") or {}
        return members.get(position) or members.get(str(position))

    def _open_member_result(self, index) -> None:
        if not self.result or not index.isValid():
            return
        payload = index.model().index(index.row(), 0).data(TABLE_ROW_ROLE) or {}
        position = payload.get("position")
        if position is None:
            return
        row = self._result_row(int(position))
        if not row:
            return

        with Repository(str(self.repo_path)) as repo:
            plan = FormationService(repo).get(self.plan_id)
            member = next(
                (
                    item
                    for item in plan.get("members", [])
                    if int(item.get("position") or 0) == int(position)
                ),
                None,
            )
            if not member:
                return
            visual = _member_remolding_result(
                repo,
                int(member["doll_id"]),
                list(row.get("remolding_uids") or []),
                dict(member.get("remolding_targets") or {}),
                global_level_override=self.global_level_override,
            )
            piece_summary = dict((visual or {}).get("piece_summary") or {})
        if not visual:
            QMessageBox.information(
                self,
                "자동 배치 상세",
                "이 인형의 리몰딩 추천 결과를 구성할 수 없습니다.",
            )
            return

        factors = reference.remolding_rules().get("factor_names", {})
        name = str(
            (visual.get("character") or {}).get("nameKR")
            or member.get("doll_name")
            or member.get("doll_id")
        )
        dialog = ResultDialog(f"{name} · 자동 배치", self)
        dialog.add_title(name, f"제대 슬롯 {int(position)} · 자동 배치 상세")
        pattern_title = QLabel("장착할 리몰딩 6개")
        pattern_title.setObjectName("SectionTitle")
        dialog.host.addWidget(pattern_title)
        pattern_widget = RemoldingPieceSummaryWidget(columns=3)
        pattern_widget.set_summary(piece_summary)
        dialog.host.addWidget(pattern_widget)
        dialog.add_remolding_result(visual, factors, reference.remolding_options())
        dialog.add_stats_comparison(
            phenomenon_status=visual.get("phenomenon_status"),
            aggregate_levels=visual.get("aggregate_levels"),
            target_status=visual.get("target_status"),
            factor_names=factors,
        )
        dialog.exec()

    def apply_result(self) -> None:
        if not self.result:
            return
        if (
            self._result_request_token is None
            or tuple(self._result_request_token) != self.repo.state_token()
        ):
            self.apply.setEnabled(False)
            QMessageBox.information(
                self,
                "자동 배치",
                "결과를 계산한 뒤 제대/보유 데이터가 변경되었습니다. 다시 계산하세요.",
            )
            return
        try:
            EquipmentOptimizer(self.repo).apply_formation_result(
                self.plan_id,
                self.result,
            )
        except Exception as exc:
            show_error(self, "적용 실패", exc)
            return
        self._preview_dirty = False
        QMessageBox.information(
            self,
            "자동 배치",
            "자동 배치 결과를 제대 계획에 적용하고 저장했습니다.",
        )
        self.accept()

