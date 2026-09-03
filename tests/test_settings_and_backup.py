from __future__ import annotations

import json
import zipfile
from pathlib import Path

from gfl2tool.settings import (
    OverlayAppearance,
    OverlayHotkeys,
    TacticVisualSettings,
    normalize_optional_color,
    validate_overlay_hotkeys,
    windows_hotkey_keys,
)
from gfl2tool.repository import Repository
from gfl2tool.services.data_backup import (
    BackupFormatError,
    apply_pending_restore,
    cancel_pending_restore,
    create_data_backup,
    pending_restore_info,
    prepare_data_restore,
)


def test_app_settings_round_trip_with_isolated_qsettings(tmp_path):
    import pytest

    qtcore = pytest.importorskip("PySide6.QtCore")
    from gfl2tool.qtui.app_settings import AppSettings

    ini = qtcore.QSettings(str(tmp_path / "settings.ini"), qtcore.QSettings.Format.IniFormat)
    settings = AppSettings(ini)
    settings.set_theme("midnight")
    settings.set_program_update_release_url("https://github.com/example/gfl2-tools/releases")
    settings.set_program_update_auto_check(False)
    settings.set_overlay_hotkeys(
        OverlayHotkeys("Ctrl+Shift+Left", "Ctrl+Shift+Right", "Ctrl+Shift+F8")
    )
    settings.set_overlay_appearance(
        OverlayAppearance(width=720, height=820, background="#111111", accent="#FF7A20")
    )
    settings.set_tactic_visuals(
        TacticVisualSettings(boss="#AA2200", cover="#556677")
    )
    settings.sync()

    restored = AppSettings(
        qtcore.QSettings(str(tmp_path / "settings.ini"), qtcore.QSettings.Format.IniFormat)
    )
    assert restored.theme() == "midnight"
    assert restored.program_update_release_url() == "https://github.com/example/gfl2-tools/releases"
    assert restored.program_update_auto_check() is False
    assert restored.overlay_hotkeys() == OverlayHotkeys(
        "Ctrl+Shift+Left", "Ctrl+Shift+Right", "Ctrl+Shift+F8"
    )
    assert restored.overlay_appearance() == OverlayAppearance(
        width=720, height=820, background="#111111", accent="#FF7A20"
    )
    assert restored.tactic_visuals() == TacticVisualSettings(
        boss="#AA2200", cover="#556677"
    )
    assert "worker_count" not in restored.snapshot()


def test_app_settings_ignores_legacy_worker_count_snapshot(tmp_path):
    import pytest

    qtcore = pytest.importorskip("PySide6.QtCore")
    from gfl2tool.qtui.app_settings import AppSettings

    settings = AppSettings(
        qtcore.QSettings(str(tmp_path / "legacy.ini"), qtcore.QSettings.Format.IniFormat)
    )
    settings.apply_snapshot({"schema": 1, "theme": "dark", "worker_count": 31})
    assert settings.theme() == "dark"
    assert "worker_count" not in settings.snapshot()


def test_windows_hotkey_parser_supports_current_ui_choices():
    assert windows_hotkey_keys("Ctrl+Alt+Left") == (0x11, 0x12, 0x25)
    assert windows_hotkey_keys("Ctrl+Shift+F12") == (0x11, 0x10, 0x7B)
    assert windows_hotkey_keys("Alt+9") == (0x12, ord("9"))


def test_full_data_backup_and_pending_restore_round_trip(tmp_path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "gfl2.db"
    with Repository(db_path) as repo:
        repo.set_meta("backup_test", "original")
        repo.con.commit()
        (data_dir / "portraits").mkdir(parents=True)
        (data_dir / "portraits" / "sample.png").write_bytes(b"image-v1")
        (data_dir / "tactics").mkdir(parents=True)
        (data_dir / "tactics" / "library.json").write_text('{"version":1}', encoding="utf-8")

        backup_path = tmp_path / "backup.zip"
        result = create_data_backup(
            db_path,
            backup_path,
            settings_payload={"schema": 1, "theme": "forest", "worker_count": 5},
        )
        assert result["files"] >= 3

    (data_dir / "portraits" / "sample.png").write_bytes(b"image-v2")
    with Repository(db_path) as repo:
        repo.set_meta("backup_test", "modified")

    prepared = prepare_data_restore(db_path, backup_path)
    assert Path(str(prepared["marker"])).is_file()
    restored = apply_pending_restore(data_dir)
    assert restored is not None
    assert restored["settings"]["theme"] == "forest"
    assert (data_dir / "portraits" / "sample.png").read_bytes() == b"image-v1"
    assert (data_dir / "tactics" / "library.json").read_text(encoding="utf-8") == '{"version":1}'
    with Repository(db_path) as repo:
        assert repo.get_meta("backup_test") == "original"



def test_pending_restore_can_be_cancelled_before_restart(tmp_path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "gfl2.db"
    with Repository(db_path):
        pass
    backup_path = tmp_path / "backup.zip"
    create_data_backup(db_path, backup_path)
    prepare_data_restore(db_path, backup_path)

    info = pending_restore_info(data_dir)
    assert info is not None
    staging = Path(str(info["staging_root"]))
    assert staging.is_dir()
    assert cancel_pending_restore(data_dir) is True
    assert pending_restore_info(data_dir) is None
    assert not staging.exists()

def test_restore_rejects_archive_path_traversal(tmp_path):
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "backup_manifest.json",
            json.dumps({"schema": 1, "application": "GFL2 Tools"}),
        )
        archive.writestr("../escape.txt", "bad")

    try:
        prepare_data_restore(tmp_path / "data" / "gfl2.db", archive_path)
    except BackupFormatError:
        pass
    else:
        raise AssertionError("path traversal backup must be rejected")


def test_overlay_hotkey_validation_rejects_semantic_duplicates():
    try:
        validate_overlay_hotkeys(
            OverlayHotkeys(
                previous="Ctrl+Alt+Left",
                next="Alt+Ctrl+Left",
                toggle_lock="Ctrl+Alt+Space",
            )
        )
    except ValueError as exc:
        assert "서로 다르게" in str(exc)
    else:
        raise AssertionError("semantic duplicate hotkeys must be rejected")


def test_full_backup_rejects_destination_inside_data_directory(tmp_path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "gfl2.db"
    with Repository(db_path):
        pass
    try:
        create_data_backup(db_path, data_dir / "backup.zip")
    except ValueError as exc:
        assert "data 폴더 밖" in str(exc)
    else:
        raise AssertionError("backup destination inside data must be rejected")


def test_restore_rejects_backup_from_another_application(tmp_path):
    archive_path = tmp_path / "wrong-app.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "backup_manifest.json",
            json.dumps({"schema": 1, "application": "Other Tool"}),
        )
        archive.writestr("data/example.txt", "x")

    try:
        prepare_data_restore(tmp_path / "data" / "gfl2.db", archive_path)
    except BackupFormatError as exc:
        assert "GFL2 Tools" in str(exc)
    else:
        raise AssertionError("backup from another application must be rejected")


def test_restore_rejects_unrelated_sqlite_database(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    unrelated = source_dir / "gfl2.db"
    import sqlite3

    con = sqlite3.connect(unrelated)
    try:
        con.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        con.commit()
    finally:
        con.close()

    archive_path = tmp_path / "unrelated-db.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "backup_manifest.json",
            json.dumps({"schema": 1, "application": "GFL2 Tools"}),
        )
        archive.write(unrelated, "data/gfl2.db")

    try:
        prepare_data_restore(tmp_path / "data" / "gfl2.db", archive_path)
    except BackupFormatError as exc:
        assert "데이터베이스" in str(exc)
    else:
        raise AssertionError("unrelated sqlite database must be rejected")


def test_restore_rejects_empty_sqlite_database(tmp_path):
    empty_db = tmp_path / "empty.db"
    import sqlite3

    con = sqlite3.connect(empty_db)
    con.close()
    archive_path = tmp_path / "empty-db.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "backup_manifest.json",
            json.dumps({"schema": 1, "application": "GFL2 Tools"}),
        )
        archive.write(empty_db, "data/gfl2.db")

    try:
        prepare_data_restore(tmp_path / "data" / "gfl2.db", archive_path)
    except BackupFormatError as exc:
        assert "데이터베이스" in str(exc)
    else:
        raise AssertionError("empty sqlite database must be rejected")



def test_visual_settings_normalize_theme_following_colors_and_overlay_size():
    assert normalize_optional_color("") == ""
    assert normalize_optional_color("#a1b2c3") == "#A1B2C3"
    appearance = OverlayAppearance(width=1, height=9999, background="#112233").normalized()
    assert (appearance.width, appearance.height) == (420, 1600)
    assert appearance.background == "#112233"
    assert appearance.text == ""

    visuals = TacticVisualSettings(boss="#abcdef", grid="").normalized()
    assert visuals.boss == "#ABCDEF"
    assert visuals.grid == ""


def test_visual_settings_reject_invalid_color():
    try:
        normalize_optional_color("red")
    except ValueError as exc:
        assert "#RRGGBB" in str(exc)
    else:
        raise AssertionError("named colors must not bypass the portable color format")
