from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from ..atomic_io import atomic_write_json
from .data_backup import create_data_backup

AUTO_BACKUP_STATE = "auto_backup_state.json"
AUTO_BACKUP_DIR = "automatic_backups"
STATE_SCHEMA = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def state_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / AUTO_BACKUP_STATE


def read_auto_backup_state(data_dir: str | Path) -> dict[str, Any]:
    path = state_path(data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict) or int(payload.get("schema") or 0) != STATE_SCHEMA:
        payload = {}
    return {
        "schema": STATE_SCHEMA,
        "last_success_at": str(payload.get("last_success_at") or ""),
        "last_path": str(payload.get("last_path") or ""),
        "last_reason": str(payload.get("last_reason") or ""),
    }


def last_backup_time(data_dir: str | Path) -> datetime | None:
    text = read_auto_backup_state(data_dir).get("last_success_at") or ""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def auto_backup_due(data_dir: str | Path, interval_days: int, *, now: datetime | None = None) -> bool:
    last = last_backup_time(data_dir)
    if last is None:
        return True
    now = now or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now.astimezone(timezone.utc) - last).total_seconds() >= max(1, int(interval_days)) * 86400


def automatic_backup_directory(db_path: str | Path) -> Path:
    db_path = Path(db_path)
    return db_path.parent.parent / AUTO_BACKUP_DIR


def create_automatic_backup(
    db_path: str | Path,
    *,
    settings_payload: dict[str, object] | None = None,
    reason: str = "scheduled",
    progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, object]:
    db_path = Path(db_path)
    target_dir = automatic_backup_directory(db_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = target_dir / f"gfl2-auto-all-{stamp}.zip"
    suffix = 1
    while destination.exists():
        destination = target_dir / f"gfl2-auto-all-{stamp}-{suffix:02d}.zip"
        suffix += 1
    result = create_data_backup(
        db_path,
        destination,
        settings_payload=settings_payload,
        progress=progress,
        should_cancel=should_cancel,
    )
    state = {
        "schema": STATE_SCHEMA,
        "last_success_at": str(result.get("created_at") or _utc_now().isoformat()),
        "last_path": str(result.get("path") or destination),
        "last_reason": str(reason or "scheduled"),
    }
    atomic_write_json(state_path(db_path.parent), state, ensure_ascii=False, indent=2)
    return result
