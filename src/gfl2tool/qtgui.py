from __future__ import annotations

import sys
from pathlib import Path


def main(db_path: str | Path = "data/gfl2.db") -> None:
    try:
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication, QMessageBox

        from .qtui import theme
        from .qtui.app_settings import AppSettings
        from .settings import recommended_worker_count
        from .repository import SchemaMismatchError
        from .services.data_backup import apply_pending_restore
    except ImportError as exc:
        raise RuntimeError(
            "PySide6가 설치되지 않았습니다. start_gfl2_tools.bat로 실행하면 전용 런타임에 자동 설치됩니다."
        ) from exc

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("GFL2 Tools")
    app.setOrganizationName("GFL2 Tools")

    settings = AppSettings()
    restore_notice = ""
    try:
        restored = apply_pending_restore(Path(db_path).parent)
        if restored is not None:
            settings.apply_snapshot(restored.get("settings"))
            source = str(restored.get("source") or "")
            restore_notice = f"백업 복원을 적용했습니다.\n{source}" if source else "백업 복원을 적용했습니다."
    except Exception as exc:
        QMessageBox.warning(None, "백업 복원 실패", f"예약된 백업을 적용하지 못했습니다.\n{exc}")

    theme.set_active_theme(settings.theme())
    theme.apply_to_application(app)
    QThreadPool.globalInstance().setMaxThreadCount(recommended_worker_count())

    from .qtui.mainwindow import MainWindow

    try:
        window = MainWindow(db_path)
    except SchemaMismatchError as exc:
        QMessageBox.critical(
            None,
            "GFL2 Tools 데이터베이스 오류",
            f"{exc}\n\n현재 릴리스에서 생성한 DB만 지원합니다. "
            "기존 data/gfl2.db를 다른 이름으로 보관하거나 삭제한 뒤 다시 실행하세요.",
        )
        raise SystemExit(1) from exc
    window.show()
    if restore_notice:
        QMessageBox.information(window, "백업 복원", restore_notice)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
