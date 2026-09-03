from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from ...repository import Repository
from ...services.app_update import ApplicationUpdater
from ...settings import OverlayHotkeys, validate_overlay_hotkeys
from .. import theme
from ..app_settings import AppSettings
from ..widgets import page_layout, section_panel
from .base import DeferredRefreshPage


class SettingsPage(DeferredRefreshPage):
    settingsChanged = Signal()
    dataChanged = Signal()
    updateCheckRequested = Signal()

    def __init__(self, repo: Repository, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.settings = settings
        root = page_layout(self, "설정")
        root.addWidget(self._build_general_panel())
        root.addWidget(self._build_program_update_panel())
        root.addWidget(self._build_overlay_panel())
        root.addStretch(1)
        root.addLayout(self._build_actions())

    def _build_general_panel(self):
        panel, layout = section_panel(
            "전역 설정",
            "프로그램 전체에 적용되는 항목만 관리합니다. 성능 관련 작업 수는 PC 환경에 맞춰 자동으로 조절됩니다.",
        )
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.theme_combo = QComboBox()
        for key, label in theme.theme_choices():
            self.theme_combo.addItem(label, key)
        form.addRow("테마", self.theme_combo)

        layout.addLayout(form)
        return panel

    def _build_program_update_panel(self):
        panel, layout = section_panel(
            "프로그램 업데이트",
            "GitHub Release의 최신 GFL2 Tools 버전을 확인합니다. 주소는 배포 저장소가 확정될 때까지 비워둘 수 있습니다.",
        )
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.program_update_url = QLineEdit()
        self.program_update_url.setPlaceholderText("예: https://github.com/owner/repository/releases · 현재 기본값 없음")
        self.program_update_url.setClearButtonEnabled(True)
        form.addRow("Release 주소", self.program_update_url)

        self.program_update_auto = QCheckBox("프로그램 시작 시 최신 Release 확인")
        form.addRow("자동 확인", self.program_update_auto)
        layout.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        check = QPushButton("지금 업데이트 확인")
        check.clicked.connect(self._request_update_check)
        row.addWidget(check)
        layout.addLayout(row)
        return panel


    def _validated_update_url(self) -> str | None:
        try:
            return ApplicationUpdater.normalize_release_url(self.program_update_url.text())
        except ValueError as exc:
            QMessageBox.information(self, "프로그램 업데이트", str(exc))
            return None

    def _request_update_check(self) -> None:
        release_url = self._validated_update_url()
        if release_url is None:
            return
        self.settings.set_program_update_release_url(release_url)
        self.settings.set_program_update_auto_check(self.program_update_auto.isChecked())
        self.settings.sync()
        self.updateCheckRequested.emit()

    def _build_overlay_panel(self):
        panel, layout = section_panel("오버레이 전역 단축키")
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.hotkey_previous = self._hotkey_editor()
        self.hotkey_next = self._hotkey_editor()
        self.hotkey_lock = self._hotkey_editor()
        form.addRow("이전 단계", self.hotkey_previous)
        form.addRow("다음 단계", self.hotkey_next)
        form.addRow("클릭 통과 전환", self.hotkey_lock)
        layout.addLayout(form)
        return panel

    @staticmethod
    def _hotkey_editor() -> QKeySequenceEdit:
        editor = QKeySequenceEdit()
        editor.setMaximumSequenceLength(1)
        editor.setClearButtonEnabled(True)
        return editor

    def _build_actions(self):
        row = QHBoxLayout()
        row.addStretch(1)
        reset = QPushButton("전역 기본값 복원")
        save = QPushButton("설정 저장")
        save.setObjectName("AccentButton")
        reset.clicked.connect(self._reset_defaults)
        save.clicked.connect(self._save)
        row.addWidget(reset)
        row.addWidget(save)
        return row

    def refresh(self) -> None:
        selected = theme.active_theme()
        index = self.theme_combo.findData(selected)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.program_update_url.setText(self.settings.program_update_release_url())
        self.program_update_auto.setChecked(self.settings.program_update_auto_check())
        hotkeys = self.settings.overlay_hotkeys()
        self.hotkey_previous.setKeySequence(QKeySequence(hotkeys.previous))
        self.hotkey_next.setKeySequence(QKeySequence(hotkeys.next))
        self.hotkey_lock.setKeySequence(QKeySequence(hotkeys.toggle_lock))

    @staticmethod
    def _portable(editor: QKeySequenceEdit) -> str:
        return editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def _validated_hotkeys(self) -> OverlayHotkeys | None:
        hotkeys = OverlayHotkeys(
            previous=self._portable(self.hotkey_previous),
            next=self._portable(self.hotkey_next),
            toggle_lock=self._portable(self.hotkey_lock),
        )
        try:
            return validate_overlay_hotkeys(hotkeys)
        except ValueError as exc:
            QMessageBox.information(self, "오버레이 단축키", str(exc))
            return None

    def _save(self) -> None:
        hotkeys = self._validated_hotkeys()
        if hotkeys is None:
            return
        release_url = self._validated_update_url()
        if release_url is None:
            return
        self.settings.set_theme(str(self.theme_combo.currentData() or AppSettings.DEFAULT_THEME))
        self.settings.set_program_update_release_url(release_url)
        self.settings.set_program_update_auto_check(self.program_update_auto.isChecked())
        self.settings.set_overlay_hotkeys(hotkeys)
        self.settings.sync()
        self.settingsChanged.emit()
        QMessageBox.information(self, "설정", "전역 설정을 저장했습니다.")

    def _reset_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "전역 기본값 복원",
            "테마, 프로그램 업데이트 설정과 오버레이 단축키를 기본값으로 되돌릴까요?\n택틱/오버레이 표시 색상과 크기는 유지됩니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.settings.set_theme(AppSettings.DEFAULT_THEME)
        self.settings.set_program_update_release_url("")
        self.settings.set_program_update_auto_check(True)
        self.settings.set_overlay_hotkeys(AppSettings.DEFAULT_HOTKEYS)
        self.settings.sync()
        self.refresh()
        self.settingsChanged.emit()
