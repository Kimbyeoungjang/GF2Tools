from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_qt_factor_order_is_canonical():
    theme = _text("src/gfl2tool/qtui/theme.py")
    assert 'FACTOR_ORDER = ["sentinel", "vanguard", "bulwark", "support"]' in theme





def test_inventory_uses_grouped_doll_and_remolding_browsers():
    source = _text("src/gfl2tool/qtui/pages/inventory.py")
    dolls = _text("src/gfl2tool/qtui/grouped_dolls.py")
    remoldings = _text("src/gfl2tool/qtui/grouped_remoldings.py")
    assert "DollListModel" in source and "ElementGroupedDollView" in source
    assert "DollListView" in dolls and "DollCardDelegate" in dolls
    assert "RemoldingGroupedView" in source and "QTreeWidget" in remoldings
    assert '"주옵션 전체"' in source and '"클래스 전체"' in source


def test_formation_member_picker_is_card_based_model_view():
    source = _text("src/gfl2tool/qtui/dialogs/doll_picker.py")
    assert "class DollPickerDialog" in source
    assert "ElementGroupedDollView" in source and "DollListModel" in source
    assert "favorites_only" in source


def test_remolding_results_share_one_visual_result_dialog():
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    formation = _text("src/gfl2tool/qtui/pages/formation.py")
    assert "class ResultDialog" in widgets
    assert "add_remolding_result" in widgets and "add_stats_comparison" in widgets
    assert "목표 / 현재" in widgets and "Lv." in widgets
    assert "ResultDialog(" in remolding_recommendation and "ResultDialog(" in formation


def test_main_pages_keep_compact_native_qt_shell():
    source = _text("src/gfl2tool/qtui/mainwindow.py")
    assert "QStackedWidget" in source
    assert "PAGE_ORDER" in source
    assert "기존 Tk GUI" not in source


def test_qt_rosters_use_pixel_scrolling_and_favorite_first_sorting():
    models = _text("src/gfl2tool/qtui/models.py")
    assert "ScrollPerPixel" in models
    assert "LayoutMode.Batched" in models
    assert "0 if le.get(\"favorite\") else 1" in models


def test_result_dialogs_keep_native_resize_and_maximize():
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    assert "RESULT_DIALOG_SIZE = (980, 720)" in widgets
    assert "self.resize(*RESULT_DIALOG_SIZE)" in widgets
    assert "WindowMaximizeButtonHint" in widgets
    assert "QScrollArea" in widgets


def test_doll_cards_expose_favorite_toggle_without_child_buttons():
    models = _text("src/gfl2tool/qtui/models.py")
    assert "favoriteClicked = Signal(int)" in models
    assert '"★" if entry.get("favorite") else "☆"' in models
    assert "QPushButton" not in models


def test_rosters_reflow_with_batched_qt_layout():
    models = _text("src/gfl2tool/qtui/models.py")
    assert "ResizeMode.Adjust" in models
    assert "setWrapping(True)" in models
    assert "setUniformItemSizes(True)" in models
    assert "setBatchSize(18)" in models


def test_remolding_recommendation_toolbar_keeps_native_qt_actions():
    source = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    for label in ("6개 추천", "전체 자동 배치", "목표 스탯", "장착칸", "평가 기준"):
        assert label in source
    assert "SubsetAllocationDialog" in source


def test_formation_reuses_shared_catalog_and_portrait_loader():
    page = _text("src/gfl2tool/qtui/pages/formation.py")
    picker = _text("src/gfl2tool/qtui/dialogs/doll_picker.py")
    card = _text("src/gfl2tool/qtui/formation_widgets.py")
    assert "OwnedDollCatalog" in page and "PortraitLoader" in page
    assert "self.catalog.entries_with_portraits()" in page
    assert "self.catalog.entries_with_portraits()" in picker
    assert "self.portraits.request(path)" in card


def test_page_switch_paints_selection_before_db_refresh():
    source = _text("src/gfl2tool/qtui/mainwindow.py")
    show = source.split("    def show_page", 1)[1].split("    def _refresh_if_current", 1)[0]
    assert "self.stack.setCurrentWidget(page)" in show
    assert "self.nav[key].setChecked(True)" in show
    assert "page.set_active(True)" in show or "QTimer.singleShot(0" in show


def test_character_selection_refreshes_detail_not_whole_roster():
    source = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    selected = source.split("    def _selected", 1)[1].split("    def _refresh_detail", 1)[0]
    assert "_refresh_detail" in selected
    assert "self.model.set_entries" not in selected


def test_remolding_recommendation_reuses_revision_keyed_service_and_score_cache():
    source = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    assert "self.repo.state_token()" in source
    assert "_owned_score_cache" in source
    assert "cache_key = (state_token, key)" in source


def test_portraits_decode_lazily_in_private_qt_worker_pool():
    source = _text("src/gfl2tool/qtui/images.py")
    assert "QThreadPool(self)" in source
    assert "setMaxThreadCount(2)" in source
    assert "QImageReader" in source and "setScaledSize" in source
    assert "run_worker(" in source






def test_owned_doll_catalog_centralizes_character_resolution():
    source = _text("src/gfl2tool/qtui/data.py")
    assert "class OwnedDollCatalog" in source
    assert "DollCharacterResolver" in source
    assert "def resolver" in source
    assert "def toggle_favorite" in source


def test_subset_picker_uses_shared_roster_portraits_and_wrapped_category_selection():
    source = _text("src/gfl2tool/qtui/dialogs/remolding_subset.py")
    section = source.split("class SubsetAllocationDialog", 1)[1]
    assert "DollListModel(self.entries, portraits=self.portraits)" in section
    assert "SelectionMode.MultiSelection" in section
    assert "DollCategoryStore" in source
    assert "self.selected_preview.setWrapping(True)" in section
    assert "ScrollBarAlwaysOff" in section
    assert "_assign_category" in section


def test_heavy_allocator_actions_run_in_qt_workers():
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    formation = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    subset = _text("src/gfl2tool/qtui/dialogs/remolding_subset.py")
    for source in (remolding_recommendation, formation, subset):
        assert "run_cancellable_worker(" in source
    jobs = _text("src/gfl2tool/qtui/jobs/remolding_optimizer.py") + _text("src/gfl2tool/qtui/jobs/formation.py")
    assert "EquipmentOptimizer" in jobs
    assert "with Repository(db_path) as repo" in jobs


def test_scroll_jelly_workaround_is_native_qt_model_view():
    models = _text("src/gfl2tool/qtui/models.py")
    assert "class DollListView(QListView)" in models
    assert "QStyledItemDelegate" in models
    assert "ScrollPerPixel" in models
    assert "Canvas" not in models


def test_thumbnail_work_does_not_use_tk_event_yielding():
    source = _text("src/gfl2tool/qtui/images.py")
    assert "QThreadPool" in source
    assert ".after(" not in source
    assert "ImageTk" not in source


def test_roster_scrolling_has_no_manual_wheel_easing():
    models = _text("src/gfl2tool/qtui/models.py")
    assert "wheelEvent" not in models
    assert "math.exp" not in models
    assert "setVerticalScrollMode" in models


def test_large_character_pickers_share_delegate_model_code():
    grouped = _text("src/gfl2tool/qtui/grouped_dolls.py")
    formation_picker = _text("src/gfl2tool/qtui/dialogs/doll_picker.py")
    subset = _text("src/gfl2tool/qtui/dialogs/remolding_subset.py")
    assert "DollListView" in grouped and "DollCardDelegate" in grouped
    assert "ElementGroupedDollView" in formation_picker
    assert "DollListModel" in formation_picker
    assert "DollListView" in subset and "DollListModel" in subset


def test_subset_picker_does_not_preload_photo_objects():
    source = _text("src/gfl2tool/qtui/dialogs/remolding_subset.py")
    section = source.split("class SubsetAllocationDialog", 1)[1]
    assert "PhotoImage" not in section
    assert "ImageTk" not in section
    assert "DollListModel(self.entries, portraits=self.portraits)" in section


def test_portrait_decode_and_pixmap_creation_are_split_safely():
    source = _text("src/gfl2tool/qtui/images.py")
    work = source.split("        def work():", 1)[1].split("        run_worker(", 1)[0]
    assert "QImageReader" in work
    assert "QPixmap.fromImage" not in work
    assert "QPixmap.fromImage" in source


def test_element_group_colors_match_current_visual_contract():
    theme = _text("src/gfl2tool/qtui/theme.py")
    assert '"corrosion": "#A56CE6"' in theme
    assert '"omni": "#8F3655"' in theme


def test_tables_disable_native_grid_lines_and_result_scroll_uses_app_background():
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    theme = _text("src/gfl2tool/qtui/theme.py")
    assert "view.setShowGrid(False)" in widgets
    assert "view.verticalHeader().setVisible(False)" in widgets
    assert 'self.scroll.setObjectName("ResultScroll")' in widgets
    assert "QScrollArea#ResultScroll" in theme


def test_formation_optimizer_uses_direct_dialog_table_not_nested_result_scroll():
    source = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    assert "class FormationOptimizeDialog(CancellableJobDialogMixin, QDialog)" in source
    assert "self.table.setMinimumHeight(300)" in source
    assert "self._show_initial_members()" in source
    assert source.index('self.calc = BusyButton("추천 배치 계산")') < source.index("self._show_initial_members()")
    assert "class FormationOptimizeDialog(CancellableJobDialogMixin, ResultDialog)" not in source


def test_remolding_recommendation_page_exposes_current_target_profile_exchange_actions():
    source = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    assert 'share_title = QLabel("추천값 공유")' in source
    assert 'QPushButton("불러오기")' in source
    assert 'QPushButton("내보내기")' in source
    assert "import_recommendation_profiles" in source and "export_recommendation_profiles" in source


def test_formation_ui_uses_six_vertical_member_rows_and_named_target_editor():
    page = _text("src/gfl2tool/qtui/pages/formation.py")
    service = _text("src/gfl2tool/services/formations.py")
    card = _text("src/gfl2tool/qtui/formation_widgets.py")
    profiles = _text("src/gfl2tool/qtui/dialogs/remolding_profiles.py")
    assert "MAX_MEMBERS = 6" in service
    assert 'scroll.setObjectName("FormationMemberScroll")' in page
    assert "cards.addWidget(card)" in page
    assert "장착할 리몰딩 6개" in card
    assert "class RemoldingPieceSummaryWidget" in card and "class RemoldingPieceCard" in card
    assert 'QPushButton("자세히 보기")' in card
    assert 'QPushButton("목표 스탯 편집")' in page
    assert 'title=f"{name}의 목표 스탯 편집"' in page
    assert "save_global=False" in page
    assert "class StepperBox" in profiles
    assert 'self.down.setText("−")' in profiles and 'self.up.setText("+")' in profiles
    assert "숫자가 작을수록 높은 우선순위" in profiles
    assert "값이 클수록 같은 우선순위" in profiles
    assert 'self.add_category.addItem("전체 카테고리", "")' in profiles


def test_formation_auto_layout_exposes_summary_metrics_and_preview():
    source = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    for label in ("제대 인원", "배치 리몰딩", "추천 총점", "배치 미리보기"):
        assert label in source
    assert 'BusyButton("추천 배치 계산")' in source
    assert 'QPushButton("이 배치 적용 · 저장")' in source
    assert 'self.members_metric.value.setText(f"{len(prepared)} / 6")' in source




def test_dashboard_prompts_for_bundle_or_backup_sync_when_inventory_is_empty():
    dashboard = _text("src/gfl2tool/qtui/pages/dashboard.py")
    assert "last_full_sync_at" not in dashboard
    assert "보조 툴 사용자 ZIP" in dashboard
    assert "기존 GF2Tools 백업" in dashboard



def test_formation_member_detail_supports_fullbody_skin_and_portrait_variant_selection():
    page = _text("src/gfl2tool/qtui/pages/formation.py")
    card = _text("src/gfl2tool/qtui/formation_widgets.py")
    assert "MemberArtwork(" in page
    assert 'assets.get("fullbody")' in page
    assert 'assets.get("skins")' in page
    assert 'assets.get("portrait_variants")' in page
    assert '"이미지 변경…"' in card
    assert '"초상화 변경…"' in card

def test_target_editor_filters_addable_stats_by_remolding_category():
    profiles = _text("src/gfl2tool/qtui/dialogs/remolding_profiles.py")
    assert "FACTOR_ORDER" in profiles
    assert "def _refresh_add_options" in profiles
    assert "self.add_category.currentIndexChanged.connect(self._refresh_add_options)" in profiles
    assert "selected_factor" in profiles


def test_formation_cards_show_each_concrete_remolding_piece_in_family_color():
    card = _text("src/gfl2tool/qtui/formation_widgets.py")
    theme = _text("src/gfl2tool/qtui/theme.py")
    assert "class RemoldingPieceCard" in card
    assert "class RemoldingPieceSummaryWidget" in card
    assert 'self.title.setText(f"{label} {index}")' in card
    assert "FACTOR_COLORS.get" in card and "FACTOR_PANEL_COLORS.get" in card
    assert "자동 배치 후\\n장착 옵션 표시" in card
    assert "row.get('level')" in card and "row.get('name')" in card
    assert '"sentinel": "#E45757"' in theme
    assert '"vanguard": "#B58AF2"' in theme
    assert '"bulwark": "#70A5FF"' in theme
    assert '"support": "#58D68D"' in theme


def test_all_formation_detail_surfaces_reuse_concrete_piece_summary():
    page = _text("src/gfl2tool/qtui/pages/formation.py")
    optimize = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    assert "RemoldingPieceSummaryWidget(columns=1)" in page
    assert "RemoldingPieceSummaryWidget(columns=3)" in optimize
    assert 'QLabel("장착할 리몰딩 6개")' in optimize
    assert '"piece_summary": service.equipped_piece_display' in optimize





def test_tactic_editor_review_and_drag_paint_contracts_are_present():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "src/gfl2tool/qtui/pages/tactics.py").read_text(encoding="utf-8")
    review = (root / "src/gfl2tool/qtui/dialogs/tactic_image_import.py").read_text(encoding="utf-8")
    grid = (root / "src/gfl2tool/qtui/tactic_widgets.py").read_text(encoding="utf-8")
    assert "TacticImageImportReviewDialog" in page
    assert "권장 선택" in review and "원본 ↔ 인식 결과" in review
    assert "_apply_drag_cell" in grid and "_drag_enable" in grid


def test_help_icons_use_project_owned_fast_hover_tooltips():
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    assert "class HoverHelpButton(QToolButton)" in widgets
    assert "SHOW_DELAY_MS = 80" in widgets
    assert "QToolTip.showText" in widgets
    assert 'super().setToolTip("")' in widgets


def test_inventory_equipment_search_avoids_per_keystroke_sidecar_io():
    source = _text("src/gfl2tool/qtui/pages/inventory.py")
    assert "_equipment_source_cache" in source
    assert "_equipment_filter_timer.setInterval(90)" in source
    assert "def _equipment_sources" in source
    filters = source.split("    def _filters_changed", 1)[1].split("    def _prepare_entries", 1)[0]
    assert "_schedule_equipment_filter" in filters
    assert "ImportedEquipmentStore(self.repo).load()" not in filters




def test_backup_page_owns_full_data_backup_and_snapshot_tools():
    backup = _text("src/gfl2tool/qtui/pages/backup.py")
    service = _text("src/gfl2tool/services/data_backup.py")
    gui = _text("src/gfl2tool/qtgui.py")

    assert "전체 백업 만들기" in backup and "백업에서 복원" in backup
    assert "export_snapshot" in backup and "import_snapshot" in backup
    assert "source.backup(" in service
    assert "apply_pending_restore" in gui
    assert "PRAGMA quick_check" in service




def test_sidebar_uses_compact_tactical_navigation_labels_without_game_access_pages():
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    assert "택틱 · 오버레이" in main
    assert "데이터 동기화" in main
    assert "게임 도감" not in main
    assert "게임 리소스" not in main



def test_tactic_image_review_exposes_explicit_formation_toggle():
    source = _text("src/gfl2tool/qtui/dialogs/tactic_image_import.py")
    assert 'QCheckBox("제대 배치")' in source
    assert "formation_check.toggled.connect" in source
    assert "formation_indexes=self._formation" in source


def test_tactic_color_editor_uses_large_preview_swatches_and_clear_window_wording():
    dialog = _text("src/gfl2tool/qtui/dialogs/tactic_visuals.py")
    overlay = _text("src/gfl2tool/qtui/tactic_overlay.py")
    tactics = _text("src/gfl2tool/qtui/pages/tactics.py")
    assert 'setMinimumWidth(300)' in dialog
    assert '"색상 변경…"' in dialog
    assert '"기본값으로 변경"' in dialog
    assert '"5개 색상 기본값으로"' not in dialog
    assert '"테마 사용"' not in dialog
    assert '"색상 설정 새 창에서 열기"' in overlay
    assert '"⚙ 표시 설정 새 창에서 열기"' in tactics
    assert 'f"QLabel {{' not in dialog


def test_tactic_image_review_is_large_and_supports_direct_drag_repositioning():
    source = _text("src/gfl2tool/qtui/dialogs/tactic_image_import.py")
    grid = _text("src/gfl2tool/qtui/tactic_widgets.py")
    assert "self._resize_for_screen()" in source
    assert "self.setMinimumSize(1100, 760)" in source
    assert "self.source_preview.setMinimumSize(460, 420)" in source
    assert "TacticGridWidget(self.result.tactic, editable=True, move_only=True)" in source
    assert "self.detected_preview.modified.connect(self._detected_grid_modified)" in source
    assert "move_only: bool = False" in grid
    assert 'self.tool = "move" if self.move_only' in grid
