from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...repository import Repository
from ...services.data_backup import (
    MANIFEST_NAME as FULL_BACKUP_MANIFEST,
    PROGRAM_MANIFEST_NAME,
    prepare_data_restore,
    restore_program_data_backup,
)
from ...services.data_exchange import (
    USER_DATASET_LABELS,
    USER_EXCHANGE_SCHEMA,
    import_user_csv_bundle,
    import_user_data_bundle,
)
from ...services.remote_catalog import RemoteCatalogBootstrap
from ..data import OwnedDollCatalog
from ..data_entry import ManualInventoryWidget, OcrInventoryWidget
from ..images import PortraitLoader
from ..widgets import BusyButton, page_layout, section_panel, show_error
from ..workers import run_worker
from .base import DeferredRefreshPage


class DataSyncPage(DeferredRefreshPage):
    """Three explicit synchronization paths: user bundle, GF2Tools backup, REST catalog."""

    dataChanged = Signal()

    def __init__(
        self, repo: Repository, catalog: OwnedDollCatalog | None = None,
        portraits: PortraitLoader | None = None, parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog or OwnedDollCatalog(repo)
        self.portraits = portraits or PortraitLoader(self)
        self.remote_catalog = RemoteCatalogBootstrap(self.repo.path.parent)
        self.pool = QThreadPool.globalInstance()

        root = page_layout(
            self,
            "데이터 동기화",
            "사용자 데이터는 보조 툴 ZIP/기존 백업/수동 입력으로 관리하고, 프로그램 기본 데이터는 GitHub Release → GitHub raw 정적 API → 오프라인 패키지 순으로 설치합니다.",
        )
        self.tabs = QTabWidget()

        import_tab = QWidget()
        import_layout = QVBoxLayout(import_tab)
        import_layout.setContentsMargins(10, 10, 10, 10)
        import_layout.setSpacing(10)
        import_layout.addWidget(self._build_user_bundle_panel())
        import_layout.addWidget(self._build_backup_panel())
        import_layout.addWidget(self._build_rest_panel())
        import_layout.addStretch(1)
        self.tabs.addTab(import_tab, "데이터 가져오기")

        manual_tab = QWidget()
        manual_layout = QVBoxLayout(manual_tab)
        manual_layout.setContentsMargins(10, 10, 10, 10)
        manual_layout.setSpacing(8)
        manual_tabs = QTabWidget()
        self.manual_entry = ManualInventoryWidget(self.repo, self.catalog, self.portraits)
        self.manual_entry.dataChanged.connect(self._changed)
        manual_tabs.addTab(self.manual_entry, "직접 입력")
        self.ocr_entry = OcrInventoryWidget(self.repo, self.catalog, self.portraits)
        self.ocr_entry.dataChanged.connect(self._changed)
        manual_tabs.addTab(self.ocr_entry, "리몰딩 OCR")
        manual_layout.addWidget(manual_tabs, 1)
        self.tabs.addTab(manual_tab, "수동으로 입력")

        root.addWidget(self.tabs, 1)

    def _build_user_bundle_panel(self):
        panel, layout = section_panel(
            "보조 툴 사용자 데이터",
            "gfl2_user_csv_bundle.zip처럼 manifest.json의 schema_id가 gfl2-user-csv-backup인 ZIP만 가져옵니다.",
        )
        row = QHBoxLayout()
        button = QPushButton("사용자 CSV 묶음 가져오기…")
        button.setObjectName("AccentButton")
        button.clicked.connect(self._import_user_bundle)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        note = QLabel("보유 인형 · 리몰딩 · 게임 제대 · 장비/키 · 무기 데이터를 한 번에 교체합니다. 개별 CSV 가져오기는 제공하지 않습니다.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return panel

    def _build_backup_panel(self):
        panel, layout = section_panel(
            "기존 GF2Tools 백업",
            "과거에 이 프로그램의 백업 · 복원 기능으로 만든 ZIP을 자동 판별해 복원합니다.",
        )
        row = QHBoxLayout()
        button = QPushButton("GF2Tools 백업에서 복원…")
        button.clicked.connect(self._restore_gf2tools_backup)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        note = QLabel("전체 백업은 안전하게 다음 실행에서 적용되며, 사용자/택틱/프로그램 데이터 백업은 해당 범위만 복원합니다.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return panel

    def _build_rest_panel(self):
        panel, layout = section_panel(
            "프로그램 기본 데이터",
            "자동 다운로드는 GitHub Release를 먼저 사용하고 실패하면 GitHub raw/Pages 호환 정적 API로 전환합니다. "
            "둘 다 사용할 수 없을 때는 동일 형식의 오프라인 ZIP을 직접 가져올 수 있습니다.",
        )
        github_row = QHBoxLayout()
        github_row.addWidget(QLabel("GitHub Release"))
        self.github_release_url = QLineEdit()
        self.github_release_url.setPlaceholderText("GitHub 저장소의 /releases 주소 또는 직접 ZIP 주소")
        self.github_release_url.setClearButtonEnabled(True)
        self.github_release_url.editingFinished.connect(self._save_program_data_urls)
        github_row.addWidget(self.github_release_url, 1)
        layout.addLayout(github_row)

        pages_row = QHBoxLayout()
        pages_row.addWidget(QLabel("정적 API (raw / Pages)"))
        self.pages_url = QLineEdit()
        self.pages_url.setPlaceholderText("exported.zip 구조를 그대로 제공하는 GitHub raw 또는 Pages 루트")
        self.pages_url.setClearButtonEnabled(True)
        self.pages_url.editingFinished.connect(self._save_program_data_urls)
        pages_row.addWidget(self.pages_url, 1)
        layout.addLayout(pages_row)

        row = QHBoxLayout()
        self.program_sync_btn = BusyButton("프로그램 데이터 자동 다운로드")
        self.program_sync_btn.setObjectName("AccentButton")
        self.program_sync_btn.setToolTip("1순위 GitHub Release → 실패 시 2순위 GitHub raw/Pages 정적 API로 동기화합니다.")
        self.program_sync_btn.clicked.connect(self._sync_program_catalog)
        self.offline_import_btn = BusyButton("오프라인 패키지 가져오기…")
        self.offline_import_btn.setToolTip("gfl2-gf2tools-offline-table.*.zip 형식의 수동 배포 파일을 설치합니다.")
        self.offline_import_btn.clicked.connect(self._import_offline_program_package)
        row.addWidget(self.program_sync_btn)
        row.addWidget(self.offline_import_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.program_data_status = QLabel("다운로드 주소 미설정 · 오프라인 패키지는 언제든 직접 가져올 수 있습니다.")
        self.program_data_status.setObjectName("Muted")
        self.program_data_status.setWordWrap(True)
        layout.addWidget(self.program_data_status)
        return panel

    def refresh(self) -> None:
        state = self.remote_catalog.ensure_placeholder()
        self.github_release_url.blockSignals(True)
        self.pages_url.blockSignals(True)
        self.github_release_url.setText(self.remote_catalog.github_release_url())
        self.pages_url.setText(self.remote_catalog.pages_base_url())
        self.github_release_url.blockSignals(False)
        self.pages_url.blockSignals(False)
        config = self.remote_catalog.load_config()
        provider = str(config.get("active_provider") or "").strip()
        version = str(config.get("data_version") or "").strip()
        game_version = str(config.get("game_version") or "").strip()
        if provider or version or game_version:
            self.program_data_status.setText(
                f"현재 게임 데이터: {game_version or '버전 미상'} · {version or '데이터 버전 미상'} · {provider or '설치 경로 미상'}"
            )
        else:
            self.program_data_status.setText(
                state.message + " · 오프라인 패키지는 언제든 직접 가져올 수 있습니다."
            )

    def on_deactivated(self) -> None:
        self.ocr_entry.stop_continuous()

    def _save_program_data_urls(self) -> bool:
        try:
            github, pages = self.remote_catalog.set_provider_urls(
                self.github_release_url.text(), self.pages_url.text()
            )
        except Exception as exc:
            show_error(self, "프로그램 데이터 주소 저장 실패", exc)
            return False
        self.github_release_url.setText(github)
        self.pages_url.setText(pages)
        if github or pages:
            self.program_data_status.setText(
                "자동 다운로드 주소 저장됨 · GitHub Release를 우선하고 실패하면 정적 API를 사용합니다."
            )
        else:
            self.program_data_status.setText(
                "다운로드 주소 미설정 · 오프라인 패키지는 언제든 직접 가져올 수 있습니다."
            )
        return True

    def _changed(self) -> None:
        self.catalog.invalidate()
        self.portraits.invalidate()
        self.dataChanged.emit()

    def _import_user_bundle(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "사용자 CSV 묶음 가져오기", "", "GFL2 사용자 ZIP (*.zip);;ZIP (*.zip)"
        )
        if not path:
            return
        if QMessageBox.question(
            self, "사용자 데이터 가져오기",
            "현재 보유 인형, 리몰딩, 게임 제대와 장비/키/무기 데이터를 선택한 묶음으로 교체할까요?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            counts = import_user_csv_bundle(self.repo, path, replace=True)
        except Exception as exc:
            show_error(self, "사용자 CSV 묶음 가져오기 실패", exc)
            return
        self._changed()
        summary = " · ".join(
            f"{label} {counts.get(key, 0):,}" for key, label in (
                ("dolls", "인형"), ("remoldings", "리몰딩"), ("formations", "제대"),
                ("equipment_dolls", "장비 인형"), ("weapons", "무기"),
            )
        )
        QMessageBox.information(self, "사용자 데이터 가져오기", f"가져오기를 완료했습니다.\n{summary}")

    @staticmethod
    def _zip_manifest(path: str | Path) -> tuple[str, dict]:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = set(zf.namelist())
                if FULL_BACKUP_MANIFEST in names:
                    return "full", json.loads(zf.read(FULL_BACKUP_MANIFEST).decode("utf-8"))
                if PROGRAM_MANIFEST_NAME in names:
                    return "program", json.loads(zf.read(PROGRAM_MANIFEST_NAME).decode("utf-8"))
                if "manifest.json" in names:
                    payload = json.loads(zf.read("manifest.json").decode("utf-8"))
                    return "manifest", payload if isinstance(payload, dict) else {}
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"백업 ZIP을 읽을 수 없습니다: {exc}") from exc
        raise ValueError("지원하는 GF2Tools 백업 형식을 찾지 못했습니다.")

    def _restore_gf2tools_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "GF2Tools 백업에서 복원", "", "ZIP (*.zip)")
        if not path:
            return
        try:
            kind, manifest = self._zip_manifest(path)
            if kind == "full":
                if QMessageBox.question(
                    self, "전체 백업 복원",
                    "전체 백업입니다. 현재 data 폴더 전체를 교체하며 다음 실행에서 적용됩니다. 계속할까요?",
                ) != QMessageBox.StandardButton.Yes:
                    return
                prepare_data_restore(self.repo.path, path)
                QMessageBox.information(self, "전체 백업 복원", "복원 준비가 완료되었습니다. 프로그램을 다시 시작하면 적용됩니다.")
                return
            if kind == "program":
                restore_program_data_backup(self.repo.path.parent, path)
                self._changed()
                QMessageBox.information(self, "프로그램 데이터 복원", "REST/레퍼런스/이미지 캐시 데이터를 복원했습니다.")
                return

            schema_id = str(manifest.get("schema_id") or "")
            if schema_id in {"gfl2-user-csv-backup", "gfl2-user-csv-bundle"}:
                counts = import_user_csv_bundle(self.repo, path, replace=True)
                self._changed()
                QMessageBox.information(self, "사용자 데이터 복원", f"사용자 CSV 백업을 복원했습니다. 인형 {counts.get('dolls', 0):,}명")
                return
            if schema_id != USER_EXCHANGE_SCHEMA:
                raise ValueError("지원하는 GF2Tools 사용자/택틱 백업이 아닙니다.")
            entries = [entry for entry in manifest.get("datasets") or [] if isinstance(entry, dict)]
            keys = tuple(
                str(entry.get("key") or "") for entry in entries
                if str(entry.get("key") or "") in USER_DATASET_LABELS and str(entry.get("key") or "") != "app_settings"
            )
            if not keys:
                raise ValueError("복원할 사용자 데이터가 백업에 없습니다.")
            import_user_data_bundle(self.repo, path, keys=keys, replace=True)
            self._changed()
            labels = ", ".join(USER_DATASET_LABELS[key] for key in keys)
            QMessageBox.information(self, "GF2Tools 백업 복원", f"복원을 완료했습니다.\n{labels}")
        except Exception as exc:
            show_error(self, "GF2Tools 백업 복원 실패", exc)

    def _program_job_busy(self, busy: bool, *, offline: bool = False) -> None:
        if offline:
            self.offline_import_btn.set_busy(busy, "오프라인 패키지 확인 중…")
            self.program_sync_btn.setEnabled(not busy)
        else:
            self.program_sync_btn.set_busy(busy, "최신 프로그램 데이터 확인 중…")
            self.offline_import_btn.setEnabled(not busy)

    def _program_sync_result(self, result) -> None:
        self.program_data_status.setText(result.message)
        if result.changed:
            self._changed()
        QMessageBox.information(self, "프로그램 데이터 다운로드", result.message)

    def _program_sync_error(self, error: str) -> None:
        show_error(self, "프로그램 데이터 다운로드 실패", error)

    def _sync_program_catalog(self) -> None:
        if not self._save_program_data_urls():
            return
        self._program_job_busy(True)
        run_worker(
            self.pool,
            self.remote_catalog.sync_now,
            on_result=self._program_sync_result,
            on_error=self._program_sync_error,
            on_finished=lambda: self._program_job_busy(False),
        )

    def _offline_sync_result(self, result) -> None:
        self.program_data_status.setText(result.message)
        if result.changed:
            self._changed()
        QMessageBox.information(self, "오프라인 프로그램 데이터", result.message)

    def _import_offline_program_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "GF2Tools 오프라인 데이터 패키지 가져오기",
            "",
            "GF2Tools 오프라인 패키지 (gfl2-gf2tools-offline-table*.zip);;ZIP (*.zip)",
        )
        if not path:
            return
        self._program_job_busy(True, offline=True)
        run_worker(
            self.pool,
            lambda selected=path: self.remote_catalog.install_offline_package(selected),
            on_result=self._offline_sync_result,
            on_error=lambda error: show_error(self, "오프라인 프로그램 데이터 가져오기 실패", error),
            on_finished=lambda: self._program_job_busy(False, offline=True),
        )

