from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, reference
from ..repository import Repository
from ..runtime_paths import install_root
from ..services.app_update import ApplicationUpdater
from ..services.remote_catalog import RemoteCatalogBootstrap
from ..settings import recommended_worker_count
from . import theme
from .tactic_widgets import apply_visual_settings
from .app_settings import AppSettings
from .data import OwnedDollCatalog, invalidate_presentation_caches
from .images import PortraitLoader
from .icons import nav_icon, nav_icon_size
from .pages import (
    BackupPage,
    DataSyncPage,
    CookingPage,
    DashboardPage,
    FormationPage,
    InventoryPage,
    RemoldingOptimizerPage,
    SettingsPage,
    TacticsPage,
)
from .widgets import show_error
from .workers import active_worker_count, run_worker


PROJECT_ROOT = install_root()


class MainWindow(QMainWindow):
    SIDEBAR_WIDTH = 218
    DEFAULT_SIZE = (1460, 920)
    MINIMUM_SIZE = (1180, 720)

    PAGE_ORDER = [
        ("dashboard", "대시보드"),
        ("inventory", "보유 현황"),
        ("formation", "제대 편성"),
        ("remolding_optimizer", "리몰딩 최적화"),
        ("cooking", "요리 계산기"),
        ("tactics", "택틱 · 오버레이"),
        ("data_sync", "데이터 동기화"),
    ]
    UTILITY_ORDER = [
        ("backup", "백업 · 복원"),
        ("settings", "설정"),
    ]

    def __init__(self, db_path: str | Path = "data/gfl2.db"):
        super().__init__()
        self.db_path = Path(db_path)
        self.settings = AppSettings()
        self.repo = Repository(self.db_path)
        reference.configure_override_root(self.repo.path.parent)
        self.remote_catalog = RemoteCatalogBootstrap(self.repo.path.parent)
        self.application_updater = ApplicationUpdater(PROJECT_ROOT)
        self._app_update_checking = False
        self._applied_theme: str | None = None
        self._close_prepared = False
        self._repo_closed = False

        self.catalog = OwnedDollCatalog(self.repo)
        self.portraits = PortraitLoader(self)

        self.setWindowTitle(f"GFL2 Tools · v{__version__}")
        self.resize(*self.DEFAULT_SIZE)
        self.setMinimumSize(*self.MINIMUM_SIZE)

        self._build_ui()
        self._apply_runtime_settings()
        self.show_page("dashboard")
        # Program self-update is checked first. Program-data updates start only
        # after that check completes, avoiding two startup update dialogs at once.
        QTimer.singleShot(0, self._initialize_startup_updates)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(self.SIDEBAR_WIDTH)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 14)
        sidebar_layout.setSpacing(5)

        title = QLabel("GFL2 TOOLS")
        title.setObjectName("BrandTitle")
        sidebar_layout.addWidget(title)

        subtitle = QLabel(f"로컬 플래너 · v{__version__}")
        subtitle.setObjectName("BrandSubtitle")
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(12)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav: dict[str, QPushButton] = {}
        for key, label in self.PAGE_ORDER:
            sidebar_layout.addWidget(self._nav_button(key, label))

        sidebar_layout.addStretch(1)
        separator = QFrame()
        separator.setObjectName("SidebarSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        sidebar_layout.addWidget(separator)
        for key, label in self.UTILITY_ORDER:
            sidebar_layout.addWidget(self._nav_button(key, label))

        outer.addWidget(sidebar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.pages = {
            "dashboard": DashboardPage(self.repo),
            "inventory": InventoryPage(self.repo, self.catalog, self.portraits),
            "formation": FormationPage(self.repo, self.catalog, self.portraits),
            "remolding_optimizer": RemoldingOptimizerPage(self.repo, self.catalog, self.portraits),
            "cooking": CookingPage(self.repo),
            "tactics": TacticsPage(self.repo, self.catalog, self.portraits),
            "data_sync": DataSyncPage(self.repo, self.catalog, self.portraits),
            "backup": BackupPage(self.repo, self.settings),
            "settings": SettingsPage(self.repo, self.settings),
        }
        for key, _label in (*self.PAGE_ORDER, *self.UTILITY_ORDER):
            page = self.pages[key]
            self.stack.addWidget(page)
            page.refreshFailed.connect(
                lambda message, page_key=key: self._page_refresh_failed(page_key, message)
            )

        self.pages["dashboard"].navigateRequested.connect(self.show_page)
        self.pages["data_sync"].dataChanged.connect(self._shared_data_changed)
        self.pages["backup"].dataChanged.connect(self._shared_data_changed)
        self.pages["backup"].exitRequested.connect(self.close)
        self.pages["settings"].settingsChanged.connect(self._apply_runtime_settings)
        self.pages["settings"].updateCheckRequested.connect(lambda: self._initialize_application_update(manual=True))
        self.pages["settings"].dataChanged.connect(self._shared_data_changed)
        self.current_page = "dashboard"

    def _initialize_startup_updates(self) -> None:
        self._initialize_application_update(manual=False, on_done=self._initialize_remote_catalog)

    def _initialize_application_update(self, *, manual: bool, on_done=None) -> None:
        if self._app_update_checking:
            if manual:
                QMessageBox.information(self, "프로그램 업데이트", "이미 최신 버전을 확인하고 있습니다.")
            return
        release_url = self.settings.program_update_release_url().strip()
        if not release_url:
            if manual:
                QMessageBox.information(
                    self,
                    "프로그램 업데이트",
                    "프로그램 Release 주소가 아직 설정되지 않았습니다.\n설정 → 프로그램 업데이트에서 주소를 입력해 주세요.",
                )
            if callable(on_done):
                on_done()
            return
        if not manual and not self.settings.program_update_auto_check():
            if callable(on_done):
                on_done()
            return

        self._app_update_checking = True

        def finish_startup() -> None:
            self._app_update_checking = False
            if callable(on_done):
                on_done()

        def checked(result) -> None:
            if not result.reachable:
                if manual:
                    QMessageBox.warning(self, "프로그램 업데이트 확인 실패", result.message)
                finish_startup()
                return
            if not result.update_available:
                if manual:
                    QMessageBox.information(self, "프로그램 업데이트", result.message)
                finish_startup()
                return
            answer = QMessageBox.question(
                self,
                "GFL2 Tools 업데이트",
                f"새 프로그램 버전이 있습니다.\n"
                f"현재: v{result.current_version}\n"
                f"최신: v{result.latest_version}\n"
                f"Release: {result.tag or result.asset_name or result.latest_version}\n\n"
                "다운로드한 ZIP의 source manifest와 SHA-256을 검증한 뒤 프로그램을 재시작해 자동 적용합니다.\n"
                "지금 업데이트할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                finish_startup()
                return

            def staged(staged_update) -> None:
                try:
                    self.application_updater.launch_staged_update(staged_update, parent_pid=os.getpid())
                except Exception as exc:
                    show_error(self, "프로그램 업데이트 준비 실패", exc)
                    finish_startup()
                    return
                QMessageBox.information(
                    self,
                    "프로그램 업데이트",
                    f"GFL2 Tools v{staged_update.version} 업데이트를 검증했습니다.\n"
                    "프로그램을 종료한 뒤 파일을 교체하고 자동으로 다시 실행합니다.",
                )
                self._app_update_checking = False
                self.close()

            def stage_failed(message: str) -> None:
                QMessageBox.warning(
                    self,
                    "프로그램 업데이트 실패",
                    "새 버전 다운로드 또는 검증에 실패했습니다. 현재 버전은 그대로 유지됩니다.\n\n" + str(message),
                )
                finish_startup()

            run_worker(
                QThreadPool.globalInstance(),
                lambda: self.application_updater.stage_latest(release_url),
                on_result=staged,
                on_error=stage_failed,
            )

        def check_failed(message: str) -> None:
            if manual:
                QMessageBox.warning(self, "프로그램 업데이트 확인 실패", str(message))
            finish_startup()

        run_worker(
            QThreadPool.globalInstance(),
            lambda: self.application_updater.check_for_update(release_url),
            on_result=checked,
            on_error=check_failed,
        )

    def _initialize_remote_catalog(self) -> None:
        try:
            state = self.remote_catalog.startup_sync()
        except Exception as exc:
            self._page_refresh_failed("dashboard", f"프로그램 데이터 초기화 파일을 준비하지 못했습니다: {exc}")
            return
        if not state.configured:
            return

        def set_status(text: str) -> None:
            page = self.pages.get("data_sync")
            status = getattr(page, "program_data_status", None)
            if status is not None:
                status.setText(text)

        def install_completed(result) -> None:
            set_status(result.message)
            if result.changed:
                self._shared_data_changed()
                QMessageBox.information(self, "프로그램 데이터 업데이트", result.message)

        def install_failed(message: str) -> None:
            text = (
                "프로그램 데이터 자동 업데이트에 실패했습니다.\n"
                "데이터 동기화 → 오프라인 패키지 가져오기에서 수동 다운로드한 ZIP을 선택해 주세요.\n\n"
                + str(message)
            )
            set_status("자동 업데이트 실패 · 오프라인 패키지를 직접 가져와 주세요.")
            QMessageBox.warning(self, "프로그램 데이터 업데이트 실패", text)

        def checked(result) -> None:
            set_status(result.message)
            if not result.reachable:
                QMessageBox.warning(
                    self,
                    "프로그램 데이터 확인 실패",
                    result.message,
                )
                return
            if not result.update_available:
                return
            answer = QMessageBox.question(
                self,
                "새 프로그램 데이터",
                f"소녀전선2 데이터 버전이 올라갔습니다.\n"
                f"현재: {result.current_game_version or '미설치'}\n"
                f"최신: {result.latest_game_version or '버전 미상'}\n"
                f"확인 경로: {result.provider}\n\n"
                "지금 업데이트할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                set_status(f"업데이트 보류 · 현재 게임 데이터 {result.current_game_version or '미설치'}")
                return
            set_status("프로그램 데이터 업데이트 중… · GitHub Release 우선")
            run_worker(
                QThreadPool.globalInstance(),
                self.remote_catalog.sync_now,
                on_result=install_completed,
                on_error=install_failed,
            )

        def check_failed(message: str) -> None:
            install_failed(message)

        run_worker(
            QThreadPool.globalInstance(),
            self.remote_catalog.check_for_update,
            on_result=checked,
            on_error=check_failed,
        )

    def _nav_button(self, key: str, label: str) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setIcon(nav_icon(key))
        button.setIconSize(nav_icon_size())
        button.clicked.connect(lambda _checked=False, page=key: self.show_page(page))
        self.nav_group.addButton(button)
        self.nav[key] = button
        return button


    def _refresh_nav_icons(self) -> None:
        """Rebuild sidebar icons after a theme change so every icon stays legible."""
        for key, button in getattr(self, "nav", {}).items():
            button.setIcon(nav_icon(key))
            button.setIconSize(nav_icon_size())

    def _apply_runtime_settings(self) -> None:
        requested_theme = self.settings.theme()
        selected_theme = theme.set_active_theme(requested_theme)
        self._refresh_nav_icons()
        if selected_theme != requested_theme:
            self.settings.set_theme(selected_theme)
            self.settings.sync()
        QThreadPool.globalInstance().setMaxThreadCount(recommended_worker_count())
        apply_visual_settings(self.settings.tactic_visuals())

        pages = getattr(self, "pages", {})
        for page in pages.values():
            apply_settings = getattr(page, "apply_runtime_settings", None)
            if callable(apply_settings):
                apply_settings()

        if selected_theme == self._applied_theme:
            return
        self._applied_theme = selected_theme

        app = QApplication.instance()
        if app is not None:
            self.setStyleSheet("")
            theme.apply_to_application(app)

        for page in pages.values():
            refresh_theme = getattr(page, "refresh_theme", None)
            if callable(refresh_theme):
                refresh_theme()
            page.update()
            for child in page.findChildren(QWidget):
                child_refresh = getattr(child, "refresh_theme", None)
                if callable(child_refresh):
                    child_refresh()
                child.update()

    def _shared_data_changed(self) -> None:
        """Invalidate shared presentation state after import/master/image changes."""
        invalidate_presentation_caches()
        self.catalog.invalidate()
        self.portraits.invalidate()
        for page in self.pages.values():
            page.invalidate_cache()

        current = self.pages.get(getattr(self, "current_page", ""))
        if current is not None:
            current.request_refresh()

    def _page_refresh_failed(self, key: str, message: str) -> None:
        if self.current_page == key:
            show_error(self, "화면 갱신 실패", message)

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        page = self.pages[key]

        if self.current_page == key and self.stack.currentWidget() is page and page.page_active:
            self.nav[key].setChecked(True)
            page.request_refresh()
            return

        old = self.pages.get(getattr(self, "current_page", ""))
        if old is not None:
            old.set_active(False)

        self.current_page = key
        self.stack.setCurrentWidget(page)
        self.nav[key].setChecked(True)

        try:
            page.set_active(True)
        except Exception as exc:
            show_error(self, "화면 갱신 실패", exc)

    def _close_blocker(self) -> str | None:
        for key, page in self.pages.items():
            try:
                reason = page.close_block_reason()
            except Exception as exc:
                raise RuntimeError(f"{key} 화면 상태를 확인하지 못했습니다.") from exc
            if reason:
                return str(reason)
        return None

    def _prepare_pages_for_close(self) -> None:
        for key, page in self.pages.items():
            try:
                page.prepare_close()
            except Exception as exc:
                raise RuntimeError(f"{key} 화면을 정리하지 못했습니다.") from exc

    def closeEvent(self, event):  # noqa: N802
        if self._repo_closed:
            event.accept()
            return

        if not self._close_prepared:
            try:
                blocker = self._close_blocker()
            except RuntimeError as exc:
                show_error(self, "종료 준비 실패", exc)
                event.ignore()
                return
            if blocker:
                QMessageBox.information(self, "작업 진행 중", blocker)
                event.ignore()
                return

            try:
                self._prepare_pages_for_close()
            except RuntimeError as exc:
                show_error(self, "종료 준비 실패", exc)
                event.ignore()
                return
            self._close_prepared = True

        worker_count = active_worker_count()
        if worker_count > 0:
            event.ignore()
            QTimer.singleShot(100, self.close)
            return

        try:
            self.portraits.invalidate()
            self.portraits.pool.clear()
            self.portraits.pool.waitForDone(1000)
        except Exception as exc:
            show_error(self, "종료 실패", f"이미지 작업을 정리하지 못했습니다.\n{exc}")
            self._close_prepared = False
            event.ignore()
            return

        try:
            self.repo.close()
        except Exception as exc:
            show_error(self, "종료 실패", f"데이터베이스를 닫지 못했습니다.\n{exc}")
            self._close_prepared = False
            event.ignore()
            return

        self._repo_closed = True
        event.accept()
