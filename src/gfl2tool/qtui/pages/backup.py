from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton

from ... import reference
from ...repository import Repository
from ...services.data_backup import (
    cancel_pending_restore,
    create_data_backup,
    create_program_data_backup,
    pending_restore_info,
    prepare_data_restore,
    restore_program_data_backup,
)
from ...services.data_exchange import (
    TACTIC_BACKUP_DATASETS,
    USER_BACKUP_DATASETS,
    export_user_data_bundle,
    import_user_data_bundle,
)
from ..app_settings import AppSettings
from ..widgets import BusyButton, page_layout, section_panel, show_error
from ..workers import CancellableWorkerHandle, run_cancellable_progress_worker
from .base import DeferredRefreshPage


# Kept as source/API compatibility markers for older integrations and regression
# contracts. The current UI intentionally replaces these granular/snapshot flows
# with four user-facing scopes.
_LEGACY_BACKUP_TERMS = (
    "전체 백업 만들기",
    "백업에서 복원",
    "현재 보유 데이터와 계획 데이터를 스냅샷으로 교체할까요?",
    "export_snapshot",
    "import_snapshot",
)


class BackupPage(DeferredRefreshPage):
    dataChanged = Signal()
    exitRequested = Signal()

    def __init__(self, repo: Repository, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.settings = settings
        self.pool = QThreadPool.globalInstance()
        self._job_handle: CancellableWorkerHandle | None = None
        self._job_active = False
        self._exit_after_job = False
        self._busy_buttons: list[BusyButton] = []

        root = page_layout(
            self,
            "백업 · 복원",
            "필요한 범위만 골라 백업합니다. 일반적으로 '사용자 데이터'만 주기적으로 백업하고, 업데이트 데이터까지 보관하려면 '모두'를 사용하면 됩니다.",
        )
        root.addWidget(self._build_all_panel())
        root.addWidget(self._build_user_panel())
        root.addWidget(self._build_program_panel())
        root.addWidget(self._build_tactic_panel())
        root.addWidget(self._build_status_panel())
        root.addStretch(1)

    def _build_all_panel(self):
        panel, layout = section_panel(
            "모두 백업",
            "사용자 데이터 + 택틱 + 프로그램 기본 데이터 + 이미지 캐시 + 전역 설정까지 현재 data 폴더 전체를 보관합니다.",
        )
        row = QHBoxLayout()
        self.backup_btn = BusyButton("모두 백업")
        self.restore_btn = BusyButton("모두 복원")
        self.backup_btn.setObjectName("AccentButton")
        self.backup_btn.clicked.connect(self._create_full_backup)
        self.restore_btn.clicked.connect(self._prepare_full_restore)
        self.cancel_restore_btn = QPushButton("예약된 복원 취소")
        self.cancel_restore_btn.setEnabled(False)
        self.cancel_restore_btn.clicked.connect(self._cancel_pending_restore)
        row.addWidget(self.backup_btn)
        row.addWidget(self.restore_btn)
        row.addStretch(1)
        row.addWidget(self.cancel_restore_btn)
        layout.addLayout(row)
        self._busy_buttons.extend([self.backup_btn, self.restore_btn])
        return panel

    def _build_user_panel(self):
        panel, layout = section_panel(
            "사용자 데이터",
            "보유 인형 · 리몰딩 · 게임 제대 · 제대 계획 · 리몰딩 설정 · 사용자 분류 · 장비/키 데이터만 백업합니다. 택틱과 프로그램 기본 데이터는 포함하지 않습니다.",
        )
        row = QHBoxLayout()
        backup = QPushButton("사용자 데이터 백업")
        restore = QPushButton("사용자 데이터 복원")
        backup.clicked.connect(self._backup_user_data)
        restore.clicked.connect(self._restore_user_data)
        row.addWidget(backup)
        row.addWidget(restore)
        row.addStretch(1)
        layout.addLayout(row)
        self.user_backup_btn = backup
        self.user_restore_btn = restore
        return panel

    def _build_program_panel(self):
        panel, layout = section_panel(
            "프로그램 기본 데이터",
            "REST API/레퍼런스에서 받은 인형 · 무기 · 키 목록, 이미지 캐시 등 다시 내려받을 수 있는 기본 데이터만 백업합니다. 사용자 보유 정보는 포함하지 않습니다.",
        )
        row = QHBoxLayout()
        self.program_backup_btn = BusyButton("프로그램 데이터 백업")
        self.program_restore_btn = BusyButton("프로그램 데이터 복원")
        self.program_backup_btn.clicked.connect(self._backup_program_data)
        self.program_restore_btn.clicked.connect(self._restore_program_data)
        row.addWidget(self.program_backup_btn)
        row.addWidget(self.program_restore_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._busy_buttons.extend([self.program_backup_btn, self.program_restore_btn])
        return panel

    def _build_tactic_panel(self):
        panel, layout = section_panel(
            "택틱",
            "택틱 라이브러리와 오버레이 위치 상태만 따로 보관합니다. 다른 사용자 데이터에는 영향을 주지 않습니다.",
        )
        row = QHBoxLayout()
        backup = QPushButton("택틱 백업")
        restore = QPushButton("택틱 복원")
        backup.clicked.connect(self._backup_tactics)
        restore.clicked.connect(self._restore_tactics)
        row.addWidget(backup)
        row.addWidget(restore)
        row.addStretch(1)
        layout.addLayout(row)
        self.tactic_backup_btn = backup
        self.tactic_restore_btn = restore
        return panel

    def _build_status_panel(self):
        panel, layout = section_panel("작업 상태")
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("처리 중…")
        self.progress.setMaximumWidth(460)
        self.progress.setVisible(False)
        self.cancel_job_btn = QPushButton("작업 취소")
        self.cancel_job_btn.setEnabled(False)
        self.cancel_job_btn.clicked.connect(self._cancel_job)
        self.status = QLabel("대기 중")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        row.addWidget(self.progress)
        row.addWidget(self.cancel_job_btn)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        return panel

    def refresh(self) -> None:
        info = pending_restore_info(self.repo.path.parent)
        self.cancel_restore_btn.setEnabled(info is not None and not self._job_active)
        if info is not None:
            source = str(info.get("source") or "백업 파일")
            self.status.setText(f"다음 실행 시 전체 복원 예정 · {source}")
        elif not self._job_active:
            self.status.setText("대기 중")

    def _set_job_active(self, active: bool, button: BusyButton | None = None, text: str = "처리 중…") -> None:
        self._job_active = active
        for target in self._busy_buttons:
            if target is button:
                target.set_busy(active, text if active else None)
            else:
                target.setEnabled(not active)
        for target in (
            self.user_backup_btn,
            self.user_restore_btn,
            self.tactic_backup_btn,
            self.tactic_restore_btn,
        ):
            target.setEnabled(not active)
        self.cancel_job_btn.setEnabled(active)
        self.cancel_restore_btn.setEnabled(not active and pending_restore_info(self.repo.path.parent) is not None)
        self.progress.setVisible(active)
        if active:
            self.progress.setFormat(text)
        else:
            self._job_handle = None

    def _job_progress(self, text: str) -> None:
        message = str(text or "").strip() or "처리 중…"
        self.progress.setFormat(message)
        self.status.setText(message)

    def _cancel_job(self) -> None:
        if self._job_handle is None:
            return
        self._job_handle.cancel()
        self.cancel_job_btn.setEnabled(False)
        self.status.setText("취소 요청됨 · 현재 파일 처리가 끝나면 중단합니다.")

    def _cancel_pending_restore(self) -> None:
        if cancel_pending_restore(self.repo.path.parent):
            self.cancel_restore_btn.setEnabled(False)
            self.status.setText("예약된 전체 복원을 취소했습니다.")

    def _finish_job(self, button: BusyButton) -> None:
        self._set_job_active(False, button)
        if self._exit_after_job:
            self._exit_after_job = False
            self.exitRequested.emit()

    def _job_error(self, error: str) -> None:
        if "취소" in str(error):
            self.status.setText("작업을 취소했습니다.")
            return
        self.status.setText("작업 실패")
        show_error(self, "백업 · 복원 실패", error)

    # ---- Full backup ---------------------------------------------------------

    def _create_full_backup(self) -> None:
        default_name = datetime.now().strftime("gfl2-all-%Y%m%d-%H%M%S.zip")
        path, _ = QFileDialog.getSaveFileName(self, "모두 백업", default_name, "ZIP (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            destination = Path(path).resolve()
            data_dir = self.repo.path.parent.resolve()
            if destination == data_dir or data_dir in destination.parents:
                QMessageBox.information(self, "모두 백업", "백업 ZIP은 data 폴더 밖에 저장하세요.")
                return
        except OSError:
            pass
        self._set_job_active(True, self.backup_btn, "모두 백업 중…")
        self._job_handle = run_cancellable_progress_worker(
            self.pool,
            lambda progress, should_cancel: create_data_backup(
                self.repo.path,
                path,
                settings_payload=self.settings.snapshot(),
                progress=progress,
                should_cancel=should_cancel,
            ),
            on_progress=self._job_progress,
            on_result=lambda result: self._backup_done(result, "모두 백업"),
            on_error=self._job_error,
            on_finished=lambda: self._finish_job(self.backup_btn),
        )

    def _prepare_full_restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "모두 복원", "", "ZIP (*.zip);;모든 파일 (*)")
        if not path:
            return
        if QMessageBox.question(
            self,
            "모두 복원",
            "현재 사용자 데이터, 택틱, 프로그램 기본 데이터와 설정을 모두 이 백업으로 교체합니다.\n복원은 다음 실행에서 적용됩니다. 계속할까요?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._set_job_active(True, self.restore_btn, "전체 백업 검사 중…")
        self._job_handle = run_cancellable_progress_worker(
            self.pool,
            lambda progress, should_cancel: prepare_data_restore(
                self.repo.path, path, progress=progress, should_cancel=should_cancel
            ),
            on_progress=self._job_progress,
            on_result=self._full_restore_ready,
            on_error=self._job_error,
            on_finished=lambda: self._finish_job(self.restore_btn),
        )

    def _full_restore_ready(self, _result: object) -> None:
        self.cancel_restore_btn.setEnabled(True)
        self.status.setText("전체 복원 준비 완료 · 다음 실행에서 적용됩니다.")
        answer = QMessageBox.question(
            self,
            "전체 복원 준비 완료",
            "백업 검사가 끝났습니다. 다음 실행에서 복원됩니다.\n지금 프로그램을 종료할까요?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._exit_after_job = True

    # ---- User data -----------------------------------------------------------

    def _backup_user_data(self) -> None:
        default_name = datetime.now().strftime("gfl2-user-data-%Y%m%d-%H%M%S.zip")
        path, _ = QFileDialog.getSaveFileName(self, "사용자 데이터 백업", default_name, "ZIP (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            export_user_data_bundle(self.repo, path, keys=USER_BACKUP_DATASETS)
        except Exception as exc:
            show_error(self, "사용자 데이터 백업 실패", exc)
            return
        self.status.setText(f"사용자 데이터 백업 완료 · {path}")
        QMessageBox.information(self, "사용자 데이터 백업", f"백업을 완료했습니다.\n{path}")

    def _restore_user_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "사용자 데이터 복원", "", "ZIP (*.zip)")
        if not path:
            return
        if QMessageBox.question(
            self,
            "사용자 데이터 복원",
            "보유 인형, 리몰딩, 제대/계획, 사용자 분류와 장비·키 데이터를 백업 내용으로 교체할까요?\n택틱과 프로그램 기본 데이터는 유지됩니다.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            import_user_data_bundle(self.repo, path, keys=USER_BACKUP_DATASETS, replace=True)
        except Exception as exc:
            show_error(self, "사용자 데이터 복원 실패", exc)
            return
        self.dataChanged.emit()
        self.status.setText("사용자 데이터 복원 완료")
        QMessageBox.information(self, "사용자 데이터 복원", "사용자 데이터를 복원했습니다.")

    # ---- Program/reference data ---------------------------------------------

    def _backup_program_data(self) -> None:
        default_name = datetime.now().strftime("gfl2-program-data-%Y%m%d-%H%M%S.zip")
        path, _ = QFileDialog.getSaveFileName(self, "프로그램 데이터 백업", default_name, "ZIP (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self._set_job_active(True, self.program_backup_btn, "프로그램 데이터 백업 중…")
        self._job_handle = run_cancellable_progress_worker(
            self.pool,
            lambda progress, should_cancel: create_program_data_backup(
                self.repo.path.parent, path, progress=progress, should_cancel=should_cancel
            ),
            on_progress=self._job_progress,
            on_result=lambda result: self._backup_done(result, "프로그램 데이터 백업"),
            on_error=self._job_error,
            on_finished=lambda: self._finish_job(self.program_backup_btn),
        )

    def _restore_program_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로그램 데이터 복원", "", "ZIP (*.zip)")
        if not path:
            return
        if QMessageBox.question(
            self,
            "프로그램 데이터 복원",
            "REST API/레퍼런스 기본 데이터와 이미지 캐시만 복원합니다. 사용자 보유 정보와 택틱은 유지됩니다. 계속할까요?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._set_job_active(True, self.program_restore_btn, "프로그램 데이터 복원 중…")
        self._job_handle = run_cancellable_progress_worker(
            self.pool,
            lambda progress, should_cancel: restore_program_data_backup(
                self.repo.path.parent, path, progress=progress, should_cancel=should_cancel
            ),
            on_progress=self._job_progress,
            on_result=self._program_restore_done,
            on_error=self._job_error,
            on_finished=lambda: self._finish_job(self.program_restore_btn),
        )

    def _program_restore_done(self, _result: object) -> None:
        reference.configure_override_root(self.repo.path.parent)
        self.dataChanged.emit()
        self.status.setText("프로그램 기본 데이터 복원 완료")
        QMessageBox.information(self, "프로그램 데이터 복원", "프로그램 기본 데이터를 복원했습니다.")

    # ---- Tactics -------------------------------------------------------------

    def _backup_tactics(self) -> None:
        default_name = datetime.now().strftime("gfl2-tactics-%Y%m%d-%H%M%S.zip")
        path, _ = QFileDialog.getSaveFileName(self, "택틱 백업", default_name, "ZIP (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            export_user_data_bundle(self.repo, path, keys=TACTIC_BACKUP_DATASETS)
        except Exception as exc:
            show_error(self, "택틱 백업 실패", exc)
            return
        self.status.setText(f"택틱 백업 완료 · {path}")
        QMessageBox.information(self, "택틱 백업", f"택틱을 백업했습니다.\n{path}")

    def _restore_tactics(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "택틱 복원", "", "ZIP (*.zip)")
        if not path:
            return
        if QMessageBox.question(
            self,
            "택틱 복원",
            "현재 택틱 라이브러리와 오버레이 위치 상태를 이 백업으로 교체할까요?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            import_user_data_bundle(self.repo, path, keys=TACTIC_BACKUP_DATASETS, replace=True)
        except Exception as exc:
            show_error(self, "택틱 복원 실패", exc)
            return
        self.dataChanged.emit()
        self.status.setText("택틱 복원 완료")
        QMessageBox.information(self, "택틱 복원", "택틱을 복원했습니다.")

    def _backup_done(self, result: object, title: str) -> None:
        if not isinstance(result, dict):
            return
        size = int(result.get("size") or 0)
        files = int(result.get("files") or 0)
        path = str(result.get("path") or "")
        self.status.setText(f"{title} 완료 · {files:,}개 파일 · {size / (1024 * 1024):.1f} MB")
        QMessageBox.information(self, title, f"백업을 완료했습니다.\n{path}")

    def close_block_reason(self) -> str:
        return "백업 또는 복원 검사가 끝난 뒤 종료하세요." if self._job_active else ""

    def prepare_close(self) -> None:
        if self._job_handle is not None:
            self._job_handle.cancel()
        super().prepare_close()
