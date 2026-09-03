from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_default_gui_entrypoint_is_qt_only():
    pyproject = _text("pyproject.toml")
    cli = _text("src/gfl2tool/cli.py")
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    assert 'gfl2gui = "gfl2tool.qtgui:main"' in pyproject
    assert 'gfl2gui-tk' not in pyproject
    assert 'from .qtgui import main as gui_main' in cli
    assert 'gui-tk' not in cli
    assert 'launch_legacy' not in main
    assert not (ROOT / "start_gfl2_tools_legacy.bat").exists()


def test_qt_uses_grouped_model_view_and_pixel_scrolling_for_doll_rosters():
    models = _text("src/gfl2tool/qtui/models.py")
    grouped = _text("src/gfl2tool/qtui/grouped_dolls.py")
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    assert "QAbstractListModel" in models
    assert "QSortFilterProxyModel" in models
    assert "QStyledItemDelegate" in models
    assert "ScrollPerPixel" in models
    assert "LayoutMode.Batched" in models
    assert "class ElementGroupedDollView" in grouped
    assert "for element in ELEMENT_ORDER" in grouped
    assert "DollListView" in grouped and "DollCardDelegate" in grouped
    assert "ElementGroupedDollView" in inventory and "ElementGroupedDollView" in remolding_recommendation


def test_qt_keeps_heavy_optimizer_work_off_ui_thread():
    worker = _text("src/gfl2tool/qtui/workers.py")
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    formation = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    remolding_recommendation_jobs = _text("src/gfl2tool/qtui/jobs/remolding_optimizer.py")
    formation_jobs = _text("src/gfl2tool/qtui/jobs/formation.py")
    assert "QRunnable" in worker
    assert "QThreadPool.globalInstance" in remolding_recommendation
    assert "QThreadPool.globalInstance" in formation
    assert "run_cancellable_worker(" in remolding_recommendation and "run_cancellable_worker(" in formation
    assert "with Repository(db_path) as repo" in remolding_recommendation_jobs
    assert "with Repository(db_path) as repo" in formation_jobs


def test_qt_has_native_pages_for_all_primary_navigation_sections():
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    for key in ("dashboard", "inventory", "formation", "remolding_optimizer", "tactics", "data_sync"):
        assert f'"{key}"' in main
    for module in ("dashboard.py", "inventory.py", "formation.py", "remolding_optimizer.py", "tactics.py", "data_sync.py"):
        assert (ROOT / "src/gfl2tool/qtui/pages" / module).is_file()


def test_launcher_exposes_only_current_gui_actions():
    launcher = _text("launcher.ps1")
    bootstrap = _text("bootstrap.py")
    assert "'legacy'" not in launcher
    assert "--legacy-gui" not in launcher
    assert "--legacy-gui" not in bootstrap


def test_inventory_does_not_eagerly_rebuild_hidden_remolding_table():
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    refresh = inventory.split("    def refresh(self) -> None:", 1)[1].split("    def _doll_selected", 1)[0]
    assert "self._refresh_remoldings(); self._filters_changed()" not in refresh
    assert "self._filters_changed()" in refresh



def test_qt_moves_advanced_remolding_recommendation_and_formation_dialogs_off_tk():
    score = _text("src/gfl2tool/qtui/dialogs/remolding_scoring.py")
    subset = _text("src/gfl2tool/qtui/dialogs/remolding_subset.py")
    dummy = _text("src/gfl2tool/qtui/dialogs/remolding_characters.py")
    page = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    formation = _text("src/gfl2tool/qtui/pages/formation.py")
    formation_card = _text("src/gfl2tool/qtui/formation_widgets.py")
    assert "class ScoreConfigDialog" in score
    assert "class SubsetAllocationDialog" in subset
    assert "class DummyCharactersDialog" in dummy
    assert "OptionOverrideDialog" in page
    assert "GameFormationImportDialog" in formation
    assert "detailRequested = Signal(int)" in formation_card


def test_qt_restores_visual_remolding_goal_cur_results():
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    formation = _text("src/gfl2tool/qtui/pages/formation.py")
    assert "def add_stats_comparison" in widgets
    assert "현상 인자 · 목표 / 현재" in widgets
    assert "추천 스탯 · 목표 / 현재" in widgets
    assert "전체 활성 스탯" in widgets
    assert "dialog.add_stats_comparison" in remolding_recommendation
    assert "_member_remolding_result" in formation
    assert "dialog.add_stats_comparison" in formation






def test_qt_sources_have_no_unresolved_global_names():
    import builtins
    import symtable

    root = ROOT / "src/gfl2tool/qtui"
    failures = []
    for path in sorted(root.rglob("*.py")):
        table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
        module_names = set(table.get_identifiers())

        def walk(scope, qual=""):
            for name in scope.get_identifiers():
                symbol = scope.lookup(name)
                if (
                    scope.get_type() != "module"
                    and symbol.is_global()
                    and symbol.is_referenced()
                    and name not in module_names
                    and not hasattr(builtins, name)
                ):
                    failures.append((str(path.relative_to(ROOT)), qual or scope.get_name(), name))
            for child in scope.get_children():
                walk(child, f"{qual}.{child.get_name()}" if qual else child.get_name())

        walk(table)
    assert failures == []


def test_qt_defers_page_refresh_and_owned_remolding_scoring():
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    base = _text("src/gfl2tool/qtui/pages/base.py")
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    jobs = _text("src/gfl2tool/qtui/jobs/remolding_optimizer.py")
    assert "page.set_active(True)" in main
    assert "QTimer.singleShot(0, self._dispatch_refresh)" in base
    assert "def _start_owned_score" in remolding_recommendation
    assert "QTimer.singleShot(" in remolding_recommendation and "70," in remolding_recommendation
    assert "score_owned_remoldings(" in remolding_recommendation
    assert "with Repository(db_path) as repo" in jobs


def test_qt_remolding_search_filters_cached_grouped_rows_without_db_probe():
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    grouped = _text("src/gfl2tool/qtui/grouped_remoldings.py")
    assert "RemoldingGroupedView" in inventory
    assert "self.remolding_groups.set_filters(" in inventory
    assert "def _filtered_rows" in grouped
    refresh = inventory.split("    def _refresh_remoldings(self) -> None:", 1)[1].split("    def _export_logger_csv", 1)[0]
    assert "self._remolding_cache_token == token" in refresh
    assert "self.remolding_groups.set_rows(self._remolding_cache)" in refresh
















def test_formation_member_cards_do_not_query_sqlite_per_card():
    page = _text("src/gfl2tool/qtui/pages/formation.py")
    card = _text("src/gfl2tool/qtui/formation_widgets.py")
    setter = card.split("    def set_member",1)[1].split("    def mouseReleaseEvent",1)[0]
    render = page.split("    def _render_members",1)[1].split("    def _member_at",1)[0]
    helper = page.split("    def _doll_info",1)[1].split("    def _render_members",1)[0]
    assert "repo.con.execute" not in setter
    assert "doll_info = self._doll_info()" in render
    assert "self.catalog.entries_with_portraits()" in helper


def test_qt_portraits_are_lazy_shared_and_decode_scaled():
    images = _text("src/gfl2tool/qtui/images.py")
    models = _text("src/gfl2tool/qtui/models.py")
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    formation = _text("src/gfl2tool/qtui/pages/formation.py")
    assert "def pixmap(" in images
    assert "reader.setScaledSize(" in images
    assert "self._pixmaps: OrderedDict" in images
    assert "dpr_key" in images and "devicePixelRatio" in images
    assert "return self._portraits.pixmap(path, size, dpr=dpr)" in models
    assert "pix.deviceIndependentSize()" in models
    assert "device.devicePixelRatioF()" in models
    # Main rosters must not eagerly decode every portrait at page refresh time.
    for source in (inventory, remolding_recommendation, formation):
        assert ".request_many(" not in source


def test_qt_doll_catalog_uses_lightweight_resolver_not_full_optimizer():
    data = _text("src/gfl2tool/qtui/data.py")
    resolver = _text("src/gfl2tool/services/dolls.py")
    optimizer = _text("src/gfl2tool/services/optimizer.py")
    assert "DollCharacterResolver" in data
    assert "EquipmentOptimizer" not in data
    assert "class DollCharacterResolver" in resolver
    assert "self._doll_resolver = DollCharacterResolver" in optimizer


def test_qt_workers_keep_python_wrappers_alive_until_finished():
    workers = _text("src/gfl2tool/qtui/workers.py")
    assert "_ACTIVE_WORKERS" in workers
    assert "def start_worker(" in workers
    assert "worker.signals.finished.connect" in workers
    qtui = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "src/gfl2tool/qtui").rglob("*.py")))
    # All normal background jobs go through the lifetime-safe starter; the only
    # direct pool.start is the helper implementation itself.
    direct = [line for line in qtui.splitlines() if ".start(worker)" in line or ".start(w)" in line]
    assert direct == ["    pool.start(worker)"]


def test_qt_remolding_score_cancelled_jobs_are_not_cached():
    remolding_recommendation = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    jobs = _text("src/gfl2tool/qtui/jobs/remolding_optimizer.py")
    assert "bool(should_cancel())" in jobs
    assert "key, serial, state_token, pieces, rows, cancelled = payload" in remolding_recommendation
    assert "if cancelled or tuple(state_token) != self.repo.state_token():" in remolding_recommendation


def test_qt_hot_tables_do_not_auto_measure_every_refresh():
    for path in sorted((ROOT / "src/gfl2tool/qtui").rglob("*.py")):
        assert "resizeColumnsToContents" not in path.read_text(encoding="utf-8")


def test_inventory_prepares_stale_remolding_table_off_ui_thread():
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    assert "self._remolding_loading_token" in inventory
    assert "QTimer.singleShot(0, self._refresh_remoldings)" in inventory
    assert "with Repository(db) as repo:" in inventory
    assert "run_worker(" in inventory
    assert "def _remoldings_ready" in inventory


def test_qt_roster_favorite_updates_use_doll_id_index():
    models = _text("src/gfl2tool/qtui/models.py")
    data = _text("src/gfl2tool/qtui/data.py")
    assert "self._doll_rows: dict[int, int]" in models
    assert "row = self._doll_rows.get(did)" in models
    assert "self._entry_by_doll_id" in data
    assert "cached = self._entry_by_doll_id.get(did)" in data


def test_remolding_typing_filters_cached_group_view_without_sqlite_revision_probe():
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    section = inventory.split("    def _filters_changed(self) -> None:",1)[1].split("    def _prepare_entries",1)[0]
    assert "self.remolding_groups.set_filters(" in section
    assert "query=self.search.text()" in section
    assert 'main_option=str(self.remolding_major.currentData() or \"\")' in section
    assert 'factor=str(self.remolding_factor.currentData() or \"\")' in section
    assert "state_token" not in section
    assert "_refresh_remoldings()" not in section


def test_formation_member_detail_uses_lightweight_doll_resolver():
    formation = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    helper = formation.split("def _member_remolding_result",1)[1].split("class FormationOptimizeDialog",1)[0]
    assert "DollCharacterResolver(repo)" in helper
    assert "EquipmentOptimizer(repo)" not in helper
    assert "resolver.calculation_level_for_key(" in helper




def test_portrait_loader_rejects_stale_decode_revision():
    images = _text("src/gfl2tool/qtui/images.py")
    ready = images.split("    def _ready(self, payload) -> None:",1)[1]
    assert "self._file_revision(key) != tuple(revision)" in ready
    assert "self.request(key)" in ready


def test_subset_picker_uses_user_categories_instead_of_favorite_priority():
    dialog = _text("src/gfl2tool/qtui/dialogs/remolding_subset.py")
    jobs = _text("src/gfl2tool/qtui/jobs/remolding_optimizer.py")
    assert "DollCategoryStore" in dialog
    assert "allowed_character_keys=self.category_store.keys(category)" in dialog
    assert "priority_character_keys=set()" in jobs
    assert "_favorite_character_keys" not in dialog


def test_all_main_pages_use_deferred_refresh_lifecycle():
    base = _text("src/gfl2tool/qtui/pages/base.py")
    assert "class DeferredRefreshPage(QWidget)" in base
    assert "if not self._page_active or self._refresh_queued" in base
    for rel, cls in (
        ("dashboard.py", "DashboardPage"),
        ("inventory.py", "InventoryPage"),
        ("formation.py", "FormationPage"),
        ("remolding_optimizer.py", "RemoldingOptimizerPage"),
        ("tactics.py", "TacticsPage"),
        ("data_sync.py", "DataSyncPage"),
    ):
        source = _text(f"src/gfl2tool/qtui/pages/{rel}")
        assert f"class {cls}(DeferredRefreshPage)" in source


def test_remolding_recommendation_cancels_hidden_owned_score_work():
    source = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    section = source.split("    def on_deactivated(self) -> None:", 1)[1].split("    def _filter", 1)[0]
    assert "handle.cancel()" in section
    assert "self._detail_serial += 1" in section
    assert "self._detail_render_token = None" in section


def test_deferred_refresh_reports_errors_and_coalesces_requests():
    base = _text("src/gfl2tool/qtui/pages/base.py")
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    assert "refreshFailed = Signal(str)" in base
    assert "if not self._page_active or self._refresh_queued" in base
    assert "self.refreshFailed.emit(str(exc))" in base
    assert "page.refreshFailed.connect" in main


def test_same_page_navigation_requests_refresh_without_lifecycle_churn():
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    section = main.split("    def show_page(self, key: str) -> None:", 1)[1].split("    def closeEvent", 1)[0]
    assert "and page.page_active" in section
    assert "page.request_refresh()" in section
    assert section.index("page.request_refresh()") < section.index("old.set_active(False)")






def test_remolding_recommendation_worker_jobs_are_qt_independent():
    jobs = _text("src/gfl2tool/qtui/jobs/remolding_optimizer.py")
    page = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    assert "PySide6" not in jobs
    assert "EquipmentOptimizer" in jobs and "RemoldingRecommendationService" in jobs
    assert "best_remolding_set" in page and "allocate_owned_remoldings" in page
    assert "EquipmentOptimizer" not in page




def test_recommendation_profile_dialogs_are_split_from_page():
    page = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    profiles = _text("src/gfl2tool/qtui/dialogs/remolding_profiles.py")
    assert "from ..dialogs.remolding_profiles import TargetProfileDialog" in page
    assert "RemoldingBulkSettingsDialog" in page
    assert "class TargetProfileDialog" not in page and "class SlotProfileDialog" not in page
    assert "class TargetProfileDialog" in profiles and "class SlotProfileDialog" in profiles




def test_snapshot_confirmation_uses_current_user_facing_terminology():
    source = (ROOT / "src/gfl2tool/qtui/pages/backup.py").read_text(encoding="utf-8")
    assert "현재 보유 데이터와 계획 데이터를 스냅샷으로 교체할까요?" in source
    assert "현재 인벤토리/계획" not in source


def test_remolding_meta_uses_major_option_for_physical_family():
    import json
    from gfl2tool.qtui.data import remolding_meta

    row = {
        "slots_json": json.dumps([
            {"option_key": "sentinel_1", "level_contribution": 3},
            {"option_key": "vanguard_11", "level_contribution": 2},
            {"option_key": "vanguard_14", "level_contribution": 1},
        ], ensure_ascii=False)
    }
    meta = remolding_meta(row)
    assert meta["primary_factor"] == "sentinel"
    assert meta["main_option_name"] == "공격 강화"


def test_runtime_theme_replaces_full_application_palette_not_window_snapshot():
    theme = _text("src/gfl2tool/qtui/theme.py")
    main = _text("src/gfl2tool/qtui/mainwindow.py")
    gui = _text("src/gfl2tool/qtgui.py")
    assert "def qt_palette" in theme
    assert 'QStyleFactory.create("Fusion")' in theme
    assert "SH_ToolTip_WakeUpDelay" in theme
    assert "app.setPalette(qt_palette())" in theme
    assert "theme.apply_to_application(app)" in main
    assert "theme.apply_to_application(app)" in gui
    constructor = main.split("    def __init__", 1)[1].split("    def _build_menu", 1)[0]
    assert "setStyleSheet(theme.stylesheet())" not in constructor


def test_tactic_move_tool_uses_direct_mouse_drag_and_keeps_fallback():
    source = _text("src/gfl2tool/qtui/tactic_widgets.py")
    assert "def _begin_move_drag" in source
    assert "def _drag_selected_to" in source
    assert "event.buttons() & Qt.MouseButton.LeftButton" in source
    assert "self.grabMouse()" in source and "self.releaseMouse()" in source
    assert "accessibility fallback" in source


def test_tactic_doll_picker_supports_persistent_multi_selection_feedback():
    grouped = _text("src/gfl2tool/qtui/grouped_dolls.py")
    picker = _text("src/gfl2tool/qtui/dialogs/doll_picker.py")
    tactic_units = _text("src/gfl2tool/qtui/dialogs/tactic_units.py")
    assert "SelectionMode.MultiSelection" in grouped
    assert "def visible_entries" in grouped
    assert "self._multi_selected_ids" in picker
    assert 'setObjectName("SelectionChip")' in picker
    assert "multi_select=True" in tactic_units


def test_data_import_workspace_keeps_bundle_backup_rest_and_manual_ocr_paths():
    data_sync = _text("src/gfl2tool/qtui/pages/data_sync.py")
    assert '"사용자 CSV 묶음 가져오기…"' in data_sync
    assert '"GF2Tools 백업에서 복원…"' in data_sync
    assert '"프로그램 데이터 자동 다운로드"' in data_sync
    assert '"오프라인 패키지 가져오기…"' in data_sync
    assert '"GitHub Release"' in data_sync and '정적 API' in data_sync
    assert 'self.tabs.addTab(manual_tab, "수동으로 입력")' in data_sync
    assert 'manual_tabs.addTab(self.manual_entry, "직접 입력")' in data_sync
    assert 'manual_tabs.addTab(self.ocr_entry, "리몰딩 OCR")' in data_sync
    assert data_sync.count('QLineEdit()') >= 2
    assert "mitmproxy" not in data_sync



def test_remolding_optimizer_has_large_selected_portrait_panel():
    source = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")
    assert 'setObjectName("SelectedDollPanel")' in source
    assert "self.selected_portrait.setFixedSize(168, 168)" in source
    assert 'badge = QLabel("현재 선택")' in source


def test_v068_inventory_and_formation_detail_workflows_are_immediate():
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    grouped = _text("src/gfl2tool/qtui/grouped_remoldings.py")
    formation = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    remolding = _text("src/gfl2tool/qtui/pages/remolding_optimizer.py")

    assert 'QLabel("선택 리몰딩 상세")' in inventory
    assert 'self.remolding_groups.rowSelected.connect(self._remolding_selected)' in inventory
    assert 'self.tree.setHeaderLabels(["주옵션 / 리몰딩", "클래스", "전체 옵션", "UID"])' in grouped
    assert "self.tree.setCurrentItem(first_child)" in grouped
    assert "self.table.doubleClicked.connect(self._open_member_editor)" in formation
    assert 'QPushButton("인형 자세히 보기")' in formation
    subset = remolding.split("    def _subset(self) -> None:", 1)[1].split("    def _import_target_profiles", 1)[0]
    assert "self._refresh_category_filter()" in subset
    assert "self._filter()" in subset




def test_v069_remolding_tree_uses_qtreewidgetitem_span_api_and_inventory_exposes_equipment():
    grouped = _text("src/gfl2tool/qtui/grouped_remoldings.py")
    inventory = _text("src/gfl2tool/qtui/pages/inventory.py")
    assert "setFirstItemColumnSpanned" not in grouped
    assert "group.setFirstColumnSpanned(True)" in grouped
    for label, key in (("무기", "weapons"), ("공용키", "common_keys"), ("고유키", "fixed_keys"), ("도약키", "expansion_keys")):
        assert f'self.kind.addItem("{label}", "{key}")' in inventory
    assert '"weapons": (' in inventory
    assert '"common_keys": (' in inventory
    assert '"fixed_keys": (' in inventory
    assert '"expansion_keys": (' in inventory
    assert 'ImportedEquipmentStore(self.repo).load()' in inventory
    assert 'QPushButton("선택 리몰딩 삭제")' in inventory


def test_v071_tactic_image_review_supports_inline_ocr_correction():
    review = _text("src/gfl2tool/qtui/dialogs/tactic_image_import.py")
    assert 'QLabel("OCR 결과 바로 수정")' in review
    assert 'self.marker_table = QTableWidget(0, 3)' in review
    assert '["종류", "위치", "OCR 표기"]' in review
    assert 'self.cycle_edit = QPlainTextEdit()' in review
    assert 'self.result.tactic.steps[index].cycle = self.cycle_edit.toPlainText()[:2000]' in review
    assert 'marker.label = str(text or "")[:24] or "?"' in review


def test_v071_formation_preview_is_visible_before_save_and_warns_on_close():
    source = _text("src/gfl2tool/qtui/dialogs/formation_optimize.py")
    assert 'QPushButton("이 배치 적용 · 저장")' in source
    assert '("추천 패턴", "pattern")' in source
    assert 'self.inline_pattern = RemoldingPieceSummaryWidget(columns=2)' in source
    assert 'self._preview_dirty = bool(prepared)' in source
    assert '"미저장 추천 배치"' in source
    assert 'def closeEvent(self, event: QCloseEvent)' in source




