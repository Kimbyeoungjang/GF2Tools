from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gfl2tool.qtui import theme
from gfl2tool.services.app_update import ApplicationUpdater
from gfl2tool.services.automatic_backup import (
    auto_backup_due,
    automatic_backup_directory,
    create_automatic_backup,
    read_auto_backup_state,
)
from gfl2tool.services.checklist import ChecklistStore
from gfl2tool.repository import Repository


def test_checklist_defaults_and_calendar_resets(tmp_path: Path) -> None:
    store = ChecklistStore(tmp_path / "data")
    start = date(2026, 9, 5)  # Saturday
    payload = store.load(today=start)
    assert [row["label"] for row in payload["items"]["daily"]] == [
        "교역실 일일 보급 상자",
        "활동층 요리 / 드링크바",
        "변경추진 - 결정채집",
        "정보조각 소모",
        "서클 과업",
        "이벤트 티켓 소모",
        "의뢰 / 이벤트 / 일지 보상 수령",
        "흙먼지 / 개척원정",
    ]
    assert [row["label"] for row in payload["items"]["weekly"]] == [
        "교역실 주간 깜짝 보급 상자",
        "변경추진 - 한정 현상수배",
        "위상충돌",
    ]
    assert [row["label"] for row in payload["items"]["monthly"]] == [
        "교역실 / 위시리스트 월간 물품 구매"
    ]

    for category in ("daily", "weekly", "monthly"):
        store.set_checked(category, payload["items"][category][0]["id"], True, today=start)

    sunday = store.load(today=date(2026, 9, 6))
    assert sunday["items"]["daily"][0]["checked"] is False
    assert sunday["items"]["weekly"][0]["checked"] is True
    assert sunday["items"]["monthly"][0]["checked"] is True

    monday = store.load(today=date(2026, 9, 7))
    assert monday["items"]["weekly"][0]["checked"] is False
    assert monday["items"]["monthly"][0]["checked"] is True

    store.set_checked("monthly", monday["items"]["monthly"][0]["id"], True, today=date(2026, 9, 7))
    october = store.load(today=date(2026, 10, 1))
    assert october["items"]["monthly"][0]["checked"] is False


def test_checklist_user_edits_persist(tmp_path: Path) -> None:
    store = ChecklistStore(tmp_path / "data")
    payload = store.load(today=date(2026, 9, 5))
    payload["items"]["daily"][0]["label"] = "수정한 일간 항목"
    payload["items"]["weekly"] = []
    store.replace_items(payload["items"], today=date(2026, 9, 5))
    loaded = store.load(today=date(2026, 9, 5))
    assert loaded["items"]["daily"][0]["label"] == "수정한 일간 항목"
    assert loaded["items"]["weekly"] == []


def test_automatic_full_backup_is_cumulative_and_due_from_last_success(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "gfl2.db"
    with Repository(db_path) as repo:
        repo.con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('test_marker', 'v104')")
        repo.con.commit()
        first = create_automatic_backup(repo.path, settings_payload={"schema": 1}, reason="test")
        second = create_automatic_backup(repo.path, settings_payload={"schema": 1}, reason="test")

    first_path = Path(str(first["path"]))
    second_path = Path(str(second["path"]))
    assert first_path.is_file()
    assert second_path.is_file()
    assert first_path != second_path
    assert first_path.parent == automatic_backup_directory(db_path)
    assert db_path.parent not in first_path.parents

    state = read_auto_backup_state(db_path.parent)
    assert state["last_path"] == str(second_path)
    last = datetime.fromisoformat(state["last_success_at"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    assert auto_backup_due(db_path.parent, 14, now=last + timedelta(days=13, hours=23)) is False
    assert auto_backup_due(db_path.parent, 14, now=last + timedelta(days=14)) is True


def test_rc_versions_upgrade_in_expected_order() -> None:
    assert ApplicationUpdater.version_is_newer("1.0.4-RC1", "1.0.4-RC0") is True
    assert ApplicationUpdater.version_is_newer("1.0.4", "1.0.4-RC0") is True
    assert ApplicationUpdater.version_is_newer("1.0.4-RC0", "1.0.4-RC0") is False
    assert ApplicationUpdater.version_is_newer("1.0.4-RC0", "1.0.4") is False
    assert ApplicationUpdater.version_is_newer("1.0.5-RC0", "1.0.4") is True


def test_tactic_export_cover_is_visibly_darker_than_grid() -> None:
    def luminance(hex_color: str) -> float:
        text = hex_color.lstrip("#")
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert luminance(theme.EXPORT_COVER) + 45 < luminance(theme.EXPORT_GRID)
