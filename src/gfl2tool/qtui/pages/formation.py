from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ... import reference
from ...repository import Repository
from ...services.formations import FormationService
from ...services.dolls import DollCharacterResolver
from ...services.doll_skill_cycles import DollSkillCycleStore
from ...services.formation_preferences import FormationMemberPreferenceStore, FormationSkillCycleAdapter
from ...services.remote_assets import site_asset_cache_path
from ...services.remolding_recommendation import RemoldingRecommendationService
from ..data import OwnedDollCatalog, remolding_meta
from ..dialogs.doll_picker import DollPickerDialog
from ..dialogs.doll_image_picker import DollImagePickerDialog
from ..dialogs.doll_skill_cycles import DollSkillCycleDialog
from ..dialogs.formation import GameFormationImportDialog
from ..dialogs.formation_optimize import FormationOptimizeDialog, _member_remolding_result
from ..dialogs.remolding_profiles import TargetProfileDialog
from ..formation_widgets import MemberArtwork, MemberCard, RemoldingPieceSummaryWidget
from ..images import PortraitLoader
from ..widgets import ResultDialog, page_layout, show_error
from .base import DeferredRefreshPage


class FormationPage(DeferredRefreshPage):
    def __init__(
        self,
        repo: Repository,
        catalog: OwnedDollCatalog,
        portraits: PortraitLoader,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog
        self.portraits = portraits
        self.svc = FormationService(repo)
        self.member_preferences = FormationMemberPreferenceStore(repo.path.parent)
        self.global_skill_cycles = DollSkillCycleStore(repo.path.parent)
        self.plan_id: int | None = None
        self._refresh_token = None
        self._applied_calculation_level = 60

        root = page_layout(self, "제대 편성")
        self._build_toolbar(root)
        self._build_content(root)
        self._connect_signals()

    def _build_toolbar(self, root) -> None:
        toolbar = QHBoxLayout()
        self.new = QPushButton("새 제대")
        self.rename = QPushButton("이름 변경")
        self.import_game = QPushButton("게임 제대 가져오기")
        self.delete = QPushButton("삭제")
        self.delete.setObjectName("DangerButton")
        self.optimize = QPushButton("자동 배치")
        self.optimize.setObjectName("AccentButton")

        for button in (
            self.new,
            self.rename,
            self.import_game,
            self.delete,
            self.optimize,
        ):
            toolbar.addWidget(button)
        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("전역 계산 레벨"))
        self.calculation_level = QSpinBox()
        self.calculation_level.setRange(1, 60)
        self.calculation_level.setPrefix("Lv.")
        self.calculation_level.setValue(60)
        self.calculation_level.setMinimumWidth(110)
        self.calculation_level.setToolTip(
            "리몰딩 계산용 전역 레벨입니다. 기본값은 Lv.60이며, 인형별 개별 계산 레벨이 항상 우선합니다. "
            "CSV의 dolls.level은 인형 자체 레벨로만 보존되고 리몰딩 계산 레벨로 사용되지 않습니다."
        )
        toolbar.addWidget(self.calculation_level)
        self.calculation_level_apply = QPushButton("적용")
        self.calculation_level_apply.setToolTip("전역 계산 레벨 변경을 확정합니다. 숫자만 바꿔서는 계산에 반영되지 않습니다.")
        self.calculation_level_apply.setEnabled(False)
        toolbar.addWidget(self.calculation_level_apply)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

    def _build_content(self, root) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        self.plans = QListWidget()
        self.plans.setMaximumWidth(260)
        splitter.addWidget(self.plans)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        heading_row = QHBoxLayout()
        heading = QLabel("제대 구성")
        heading.setObjectName("SectionTitle")
        heading_row.addWidget(heading)
        heading_row.addStretch(1)
        right_layout.addLayout(heading_row)

        scroll = QScrollArea()
        scroll.setObjectName("FormationMemberScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setObjectName("FormationMemberBody")
        cards = QVBoxLayout(body)
        cards.setContentsMargins(0, 0, 4, 0)
        cards.setSpacing(8)
        self.cards: list[MemberCard] = []
        for position in range(1, self.svc.MAX_MEMBERS + 1):
            card = MemberCard(position, self.portraits)
            card.change.clicked.connect(
                lambda _checked=False, slot=position: self._change_member(slot)
            )
            card.clear.clicked.connect(
                lambda _checked=False, slot=position: self._clear_member(slot)
            )
            card.detailRequested.connect(self._member_detail)
            card.levelRequested.connect(self._edit_member_level)
            card.portraitRequested.connect(self._change_member_portrait)
            card.skillCycleRequested.connect(self._edit_member_skill_cycle)
            cards.addWidget(card)
            self.cards.append(card)
        cards.addStretch(1)
        scroll.setWidget(body)
        right_layout.addWidget(scroll, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

    def _connect_signals(self) -> None:
        self.new.clicked.connect(self._create)
        self.rename.clicked.connect(self._rename)
        self.import_game.clicked.connect(self._import_game)
        self.delete.clicked.connect(self._delete)
        self.optimize.clicked.connect(self._optimize)
        self.calculation_level.valueChanged.connect(self._calculation_level_pending)
        self.calculation_level_apply.clicked.connect(self._apply_calculation_level)
        self.plans.currentItemChanged.connect(self._plan_selected)

    def invalidate_cache(self) -> None:
        self._refresh_token = None
        self.request_refresh()

    def refresh(self) -> None:
        token = self.repo.state_token()
        if token == self._refresh_token and self.plan_id is not None:
            return

        # Load first and only commit the revision after a successful refresh.
        # A transient DB/service error must not poison the cache and suppress
        # the next retry with the same repository state token.
        rows = list(self.svc.list())
        previous = self.plan_id
        self.plans.clear()
        for row in rows:
            item = QListWidgetItem(
                f"{row['name']}  ·  {row.get('member_count', 0)}/{self.svc.MAX_MEMBERS}"
            )
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.plans.addItem(item)
            if previous and int(row["id"]) == previous:
                self.plans.setCurrentItem(item)

        if self.plans.count() and self.plans.currentRow() < 0:
            self.plans.setCurrentRow(0)
        if not self.plans.count():
            self.plan_id = None
            self._render_members(None)
        self._refresh_token = token

    def _plan_selected(self, current, _previous) -> None:
        self.plan_id = (
            int(current.data(Qt.ItemDataRole.UserRole))
            if current is not None
            else None
        )
        plan = self.svc.get(self.plan_id) if self.plan_id else None
        self._render_members(plan)

    def _global_level_override(self) -> int:
        return max(1, min(60, int(self._applied_calculation_level)))

    def _calculation_level_pending(self) -> None:
        pending = max(1, min(60, int(self.calculation_level.value())))
        self.calculation_level_apply.setEnabled(pending != self._global_level_override())

    def _apply_calculation_level(self) -> None:
        self._applied_calculation_level = max(1, min(60, int(self.calculation_level.value())))
        self.calculation_level_apply.setEnabled(False)
        if self.plan_id:
            self._render_members(self.svc.get(self.plan_id))

    def _level_context_for_doll(self, doll_id: int) -> tuple[str, int, int, str] | None:
        resolver = DollCharacterResolver(self.repo)
        key = resolver.character_key_for_doll(int(doll_id))
        if not key:
            return None
        actual = resolver.owned_character_level_for_key(key)
        individual = resolver.character_level_override_for_key(key)
        global_level = self._global_level_override()
        resolved = resolver.calculation_level_for_key(key, global_level)
        source = "개별" if individual is not None else "전역"
        return key, actual, resolved, source

    def _member_level_text(self, member: dict) -> str:
        context = self._level_context_for_doll(int(member.get("doll_id") or 0))
        if context is None:
            return "계산 레벨 · 캐릭터 기준 연결 안 됨"
        _key, actual, resolved, source = context
        return f"인형 Lv.{actual} · 리몰딩 계산 Lv.{resolved} ({source})"

    def _edit_member_level(self, position: int) -> None:
        if not self.plan_id:
            return
        plan = self.svc.get(self.plan_id)
        member = self._member_at(plan, position)
        if not member:
            return
        context = self._level_context_for_doll(int(member.get("doll_id") or 0))
        if context is None:
            QMessageBox.information(self, "계산 레벨", "이 인형을 리몰딩 계산 기준과 연결하지 못했습니다.")
            return
        key, actual, _resolved, _source = context
        resolver = DollCharacterResolver(self.repo)
        current = resolver.character_level_override_for_key(key) or 0
        value, accepted = QInputDialog.getInt(
            self,
            "인형별 계산 레벨",
            f"{member.get('doll_name') or member.get('doll_id')}의 개별 계산 레벨을 지정하세요.\n"
            f"현재 인형 레벨: Lv.{actual} (계산값과 별개)\n0 = 전역 계산 레벨 Lv.{self._global_level_override()} 사용",
            current,
            0,
            60,
            1,
        )
        if not accepted:
            return
        try:
            RemoldingRecommendationService(self.repo).set_character_level_override(
                key, value if value > 0 else None
            )
        except Exception as exc:
            show_error(self, "계산 레벨 저장 실패", exc)
            return
        self._refresh_token = None
        self.request_refresh()

    def _doll_info(self) -> dict[int, dict]:
        return {
            int(entry["doll_id"]): dict(entry)
            for entry in self.catalog.entries_with_portraits()
        }

    def _member_piece_summary(self, member: dict) -> dict:
        doll_id = int(member.get("doll_id") or 0)
        resolver = self.catalog.resolver
        key = resolver.character_key_for_doll(doll_id)
        if not key:
            return {"groups": [], "required": 0, "assigned": 0, "missing": 0}
        pieces: list[dict] = []
        for row in self.repo.remoldings_by_uids(list(member.get("remolding_uids") or [])):
            meta = remolding_meta(row)
            pieces.append({
                "uid": str(row.get("uid") or ""),
                "remolding_id": int(row.get("remolding_id") or 0),
                "slots": list(meta.get("slots") or []),
                "primary_factor": meta.get("primary_factor"),
            })
        try:
            return resolver.recommendation.equipped_piece_display(key, pieces)
        except ValueError:
            return {"groups": [], "required": 0, "assigned": len(pieces), "missing": 0}

    def _program_doll_meta(self, doll_entry: dict, doll_id: int) -> dict:
        raw = doll_entry.get("program_meta") if isinstance(doll_entry.get("program_meta"), dict) else None
        return dict(raw or reference.program_dolls().get(int(doll_id), {}) or {})

    def _asset_options(self, doll_entry: dict, doll_id: int, *, kind: str) -> list[tuple[str, str, Path]]:
        meta = self._program_doll_meta(doll_entry, doll_id)
        assets = meta.get("assets") if isinstance(meta.get("assets"), dict) else {}
        relatives: list[tuple[str, str]] = []
        if kind == "artwork":
            fullbody = str(assets.get("fullbody") or "").strip()
            if fullbody:
                relatives.append(("기본 전신", fullbody))
            for index, value in enumerate(assets.get("skins") or [], 1):
                relative = str(value or "").strip()
                if relative:
                    relatives.append((f"스킨 {index}", relative))
        else:
            values = [str(value or "").strip() for value in assets.get("portrait_variants") or []]
            if not any(values):
                values = [str(assets.get("portrait") or "").strip()]
            for index, relative in enumerate((value for value in values if value), 1):
                label = "기본 초상화" if index == 1 else f"초상화 {index}"
                relatives.append((label, relative))
        out: list[tuple[str, str, Path]] = []
        seen: set[str] = set()
        for label, relative in relatives:
            clean = relative.replace("\\", "/").lstrip("/")
            if not clean or clean in seen:
                continue
            path = site_asset_cache_path(self.repo.path.parent, clean)
            if path is None:
                continue
            seen.add(clean)
            out.append((label, clean, path))
        return out

    def _selected_asset_path(self, doll_entry: dict, member: dict, position: int, *, kind: str) -> Path | None:
        doll_id = int(member.get("doll_id") or 0)
        pref = self.member_preferences.member(int(self.plan_id or 0), position, doll_id) if self.plan_id else {}
        key = "artwork" if kind == "artwork" else "portrait"
        preferred = str(pref.get(key) or "").strip()
        options = self._asset_options(doll_entry, doll_id, kind=kind)
        for _label, relative, path in options:
            if preferred and relative == preferred:
                return path
        if options:
            return options[0][2]
        fallback_key = "portrait_path"
        fallback = doll_entry.get(fallback_key)
        return Path(str(fallback)) if fallback else None

    def _pick_member_asset(self, position: int, *, kind: str, parent=None) -> bool:
        if not self.plan_id:
            return False
        plan = self.svc.get(self.plan_id)
        member = self._member_at(plan, position)
        if not member:
            return False
        doll_id = int(member.get("doll_id") or 0)
        doll_entry = self._doll_info().get(doll_id, {})
        options = self._asset_options(doll_entry, doll_id, kind=kind)
        if not options:
            QMessageBox.information(self, "이미지 변경", "이 인형에 사용할 동기화 이미지 변형이 없습니다.")
            return False
        current = self._selected_asset_path(doll_entry, member, position, kind=kind)
        dialog = DollImagePickerDialog(
            self.portraits,
            [(label, path) for label, _relative, path in options],
            title="전신 · 스킨 이미지 변경" if kind == "artwork" else "초상화 변경",
            current=current,
            parent=parent or self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.result_path is None:
            return False
        selected_relative = next((relative for _label, relative, path in options if str(path) == str(dialog.result_path)), "")
        if not selected_relative:
            return False
        self.member_preferences.update_member(
            self.plan_id, position, doll_id, **({"artwork": selected_relative} if kind == "artwork" else {"portrait": selected_relative})
        )
        self._render_members(self.svc.get(self.plan_id))
        return True

    def _change_member_portrait(self, position: int) -> None:
        self._pick_member_asset(position, kind="portrait")

    def _edit_member_skill_cycle(self, position: int) -> None:
        if not self.plan_id:
            return
        plan = self.svc.get(self.plan_id)
        member = self._member_at(plan, position)
        if not member:
            return
        doll_id = int(member.get("doll_id") or 0)
        name = str(self._doll_info().get(doll_id, {}).get("name") or member.get("doll_name") or doll_id)
        adapter = FormationSkillCycleAdapter(
            self.member_preferences, self.plan_id, position, doll_id, fallback_store=self.global_skill_cycles
        )
        dialog = DollSkillCycleDialog(adapter, doll_id=doll_id, doll_name=f"{name} · 이 제대 전용", parent=self)
        dialog.setToolTip("저장하면 이 제대 슬롯에서만 사용할 사이클로 보관됩니다. 처음에는 인형 전역 사이클을 불러옵니다.")
        dialog.exec()

    def _render_members(self, plan: dict | None) -> None:
        members = {
            int(member["position"]): member
            for member in (plan or {}).get("members", [])
        }
        doll_info = self._doll_info()
        for card in self.cards:
            member = members.get(card.position)
            info = doll_info.get(int(member["doll_id"]), {}) if member else {}
            piece_summary = self._member_piece_summary(member) if member else None
            portrait_path = self._selected_asset_path(info, member, card.position, kind="portrait") if member else None
            card.set_member(
                member,
                str(info.get("name") or ""),
                portrait_path,
                piece_summary=piece_summary,
                level_text=self._member_level_text(member) if member else "",
            )

    def _member_at(self, plan: dict, position: int) -> dict | None:
        return next(
            (
                member
                for member in plan.get("members", [])
                if int(member.get("position") or 0) == int(position)
            ),
            None,
        )

    def _member_overview(self, doll_entry: dict, visual: dict, member: dict, position: int, detail_dialog=None) -> QFrame:
        overview = QFrame()
        overview.setObjectName("Panel")
        overview_layout = QHBoxLayout(overview)
        overview_layout.setContentsMargins(10, 10, 10, 10)
        overview_layout.setSpacing(12)
        artwork = MemberArtwork(
            self.portraits,
            self._selected_asset_path(doll_entry, member, position, kind="artwork"),
            self._selected_asset_path(doll_entry, member, position, kind="portrait"),
            overview,
        )
        def change_artwork() -> None:
            if self._pick_member_asset(position, kind="artwork", parent=detail_dialog or self):
                if detail_dialog is not None:
                    detail_dialog.accept()
                    QTimer.singleShot(0, lambda: self._member_detail(position))
        artwork.changeRequested.connect(change_artwork)
        overview_layout.addWidget(artwork)

        summary_host = QFrame()
        summary_host.setObjectName("PanelAlt")
        summary_layout = QVBoxLayout(summary_host)
        summary_layout.setContentsMargins(14, 14, 14, 14)
        summary_layout.setSpacing(8)

        summary_heading = QHBoxLayout()
        summary_title = QLabel("장착할 리몰딩 6개")
        summary_title.setObjectName("SectionTitle")
        summary_heading.addWidget(summary_title)
        summary_heading.addStretch(1)
        summary_layout.addLayout(summary_heading)

        phenomenon = dict(visual.get("phenomenon_status") or {})
        desired = dict(phenomenon.get("desired") or {})
        targets = list(visual.get("target_status") or [])
        met_targets = sum(1 for row in targets if row.get("met"))
        quick = QLabel(
            f"현상 {phenomenon.get('desired_stage') or '—'} · "
            f"{'달성' if desired.get('active') else '미달'} · "
            f"추천 목표 {met_targets}/{len(targets)} · "
            f"Lv.{int(phenomenon.get('character_level') or 60)}/60"
        )
        quick.setObjectName(
            "SuccessText" if desired.get("active") else "WarningText"
        )
        quick.setWordWrap(True)
        summary_layout.addWidget(quick)

        piece_summary = RemoldingPieceSummaryWidget(columns=1)
        piece_summary.set_summary(dict(visual.get("piece_summary") or {}))
        summary_layout.addWidget(piece_summary, 1)

        overview_layout.addWidget(summary_host, 1)
        overview_layout.setStretch(0, 2)
        overview_layout.setStretch(1, 3)
        return overview

    def _edit_member_targets(
        self,
        dialog: ResultDialog,
        position: int,
        member: dict,
        visual: dict,
        name: str,
    ) -> None:
        editor = TargetProfileDialog(
            self.repo,
            str(visual.get("character_key") or ""),
            dialog,
            initial_targets=dict(visual.get("target_specs") or {}),
            save_global=False,
            title=f"{name}의 목표 스탯 편집",
        )
        if not editor.exec() or editor.result_targets is None:
            return
        try:
            self.svc.set_member(
                self.plan_id,
                position,
                int(member["doll_id"]),
                remolding_uids=list(member.get("remolding_uids") or []),
                remolding_targets=dict(editor.result_targets),
            )
        except Exception as exc:
            show_error(dialog, "목표 스탯 저장 실패", exc)
            return

        dialog.accept()
        self._refresh_token = None
        self.request_refresh()
        QTimer.singleShot(0, lambda: self._member_detail(position))

    def _member_detail(self, position: int) -> None:
        if not self.plan_id:
            return
        plan = self.svc.get(self.plan_id)
        member = self._member_at(plan, position)
        if not member:
            return

        visual = _member_remolding_result(
            self.repo,
            int(member["doll_id"]),
            list(member.get("remolding_uids") or []),
            dict(member.get("remolding_targets") or {}),
            global_level_override=self._global_level_override(),
        )
        if not visual:
            QMessageBox.information(
                self,
                "리몰딩 결과",
                "이 인형의 리몰딩 추천 기준을 연결하지 못했습니다.",
            )
            return

        factors = reference.remolding_rules().get("factor_names", {})
        name = str(
            (visual.get("character") or {}).get("nameKR")
            or member.get("doll_name")
            or member.get("doll_id")
        )
        doll_entry = self._doll_info().get(int(member["doll_id"]), {})

        dialog = ResultDialog(f"{name} · 장착 결과", self)
        header = QFrame()
        header.setObjectName("PanelAlt")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        title_host = QVBoxLayout()
        title_label = QLabel(name)
        title_label.setObjectName("PageTitle")
        title_host.addWidget(title_label)
        subtitle = QLabel(f"제대 슬롯 {position} · 추천 패턴과 상세 결과")
        subtitle.setObjectName("Muted")
        title_host.addWidget(subtitle)
        header_layout.addLayout(title_host, 1)

        level_context = self._level_context_for_doll(int(member.get("doll_id") or 0))
        if level_context is not None:
            character_key, actual_level, _resolved_level, _source = level_context
            current_override = DollCharacterResolver(self.repo).character_level_override_for_key(character_key)
            header_layout.addWidget(QLabel(f"인형 Lv.{actual_level}  ·  리몰딩 계산 Lv."))
            individual_level = QSpinBox()
            individual_level.setRange(0, 60)
            individual_level.setSpecialValueText("전역")
            individual_level.setValue(int(current_override or 0))
            individual_level.setToolTip(
                f"0(전역) = 현재 전역 Lv.{self._global_level_override()} 사용 · 1~60 = 이 인형만 개별 적용"
            )
            header_layout.addWidget(individual_level)
            apply_individual = QPushButton("적용")
            header_layout.addWidget(apply_individual)

            def apply_detail_level() -> None:
                try:
                    value = int(individual_level.value())
                    RemoldingRecommendationService(self.repo).set_character_level_override(
                        character_key, value if value > 0 else None
                    )
                except Exception as exc:
                    show_error(dialog, "계산 레벨 저장 실패", exc)
                    return
                dialog.accept()
                self._refresh_token = None
                self.request_refresh()
                QTimer.singleShot(0, lambda: self._member_detail(position))

            apply_individual.clicked.connect(apply_detail_level)

        dialog.host.addWidget(header)
        dialog.host.addWidget(self._member_overview(doll_entry, visual, member, position, dialog))
        dialog.add_remolding_result(visual, factors, reference.remolding_options())
        dialog.add_stats_comparison(
            phenomenon_status=visual.get("phenomenon_status"),
            aggregate_levels=visual.get("aggregate_levels"),
            target_status=visual.get("target_status"),
            factor_names=factors,
        )

        edit_targets = QPushButton("목표 스탯 편집")
        edit_targets.setObjectName("AccentButton")
        dialog.button_box.addButton(
            edit_targets,
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        edit_targets.clicked.connect(
            lambda: self._edit_member_targets(dialog, position, member, visual, name)
        )
        dialog.exec()

    def _create(self) -> None:
        name, accepted = QInputDialog.getText(self, "새 제대", "제대 이름")
        if not accepted or not name.strip():
            return
        try:
            self.plan_id = self.svc.create(name.strip())
        except Exception as exc:
            show_error(self, "생성 실패", exc)
            return
        self.request_refresh()

    def _rename(self) -> None:
        if not self.plan_id:
            return
        plan = self.svc.get(self.plan_id)
        name, accepted = QInputDialog.getText(
            self,
            "제대 이름 변경",
            "새 이름",
            text=str(plan.get("name") or ""),
        )
        if not accepted or not name.strip():
            return
        try:
            self.svc.rename(self.plan_id, name.strip())
        except Exception as exc:
            show_error(self, "이름 변경 실패", exc)
            return
        self._refresh_token = None
        self.request_refresh()

    def _import_game(self) -> None:
        dialog = GameFormationImportDialog(self.repo, self)
        if not dialog.exec() or dialog.imported_plan_id is None:
            return
        self.plan_id = int(dialog.imported_plan_id)
        self._refresh_token = None
        self.request_refresh()

    def _delete(self) -> None:
        if not self.plan_id:
            return
        answer = QMessageBox.question(
            self,
            "제대 삭제",
            "선택한 제대 계획을 삭제할까요?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.svc.delete(self.plan_id)
        except Exception as exc:
            show_error(self, "제대 삭제 실패", exc)
            return
        self.plan_id = None
        self._refresh_token = None
        self.request_refresh()

    def _change_member(self, position: int) -> None:
        if not self.plan_id:
            return
        plan = self.svc.get(self.plan_id)
        current = self._member_at(plan, position)
        current_doll_id = int(current["doll_id"]) if current else None

        dialog = DollPickerDialog(
            self.repo,
            self.catalog,
            self.portraits,
            current_doll_id,
            self,
        )
        dialog.exec()
        if not dialog.result_id:
            return

        previous = current or {}
        same_doll = bool(
            current
            and int(current.get("doll_id") or 0) == int(dialog.result_id)
        )
        if not same_doll:
            self.member_preferences.clear_member(self.plan_id, position)
        try:
            self.svc.set_member(
                self.plan_id,
                position,
                dialog.result_id,
                remolding_uids=(
                    list(previous.get("remolding_uids", [])) if same_doll else []
                ),
                remolding_targets=(
                    dict(previous.get("remolding_targets", {})) if same_doll else {}
                ),
            )
        except Exception as exc:
            show_error(self, "슬롯 저장 실패", exc)
            return
        self.request_refresh()

    def _clear_member(self, position: int) -> None:
        if not self.plan_id:
            return
        answer = QMessageBox.question(
            self,
            "슬롯 비우기",
            f"제대 슬롯 {position}의 인형을 비울까요?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.svc.remove_member(self.plan_id, position)
            self.member_preferences.clear_member(self.plan_id, position)
        except Exception as exc:
            show_error(self, "슬롯 비우기 실패", exc)
            return
        self._refresh_token = None
        self.request_refresh()

    def _optimize(self) -> None:
        if not self.plan_id:
            return
        dialog = FormationOptimizeDialog(
            self.repo,
            self.plan_id,
            self,
            global_level_override=self._global_level_override(),
        )
        if dialog.exec():
            self.request_refresh()
