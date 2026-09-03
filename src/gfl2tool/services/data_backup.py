from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import __version__
from ..atomic_io import atomic_write_json
from ..repository import SCHEMA_ID, SCHEMA_VERSION, Repository, SchemaMismatchError

BACKUP_SCHEMA = 1
MANIFEST_NAME = "backup_manifest.json"
SETTINGS_NAME = "app_settings.json"
PENDING_RESTORE_NAME = ".gfl2_pending_restore.json"
STAGING_PREFIX = ".gfl2_restore_staging_"
ROLLBACK_PREFIX = ".gfl2_restore_previous_"

Progress = Callable[[str], None]
CancelCheck = Callable[[], bool]


class BackupCancelled(RuntimeError):
    pass


class BackupFormatError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_progress(progress: Progress | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _check_cancel(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise BackupCancelled("작업이 취소되었습니다.")


_ALREADY_COMPRESSED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".dds",
    ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".zst",
    ".mp3", ".ogg", ".mp4", ".mkv", ".webm",
}


def _zip_compression_for(path: Path) -> int:
    return (
        zipfile.ZIP_STORED
        if path.suffix.lower() in _ALREADY_COMPRESSED_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )


def _iter_data_files(data_dir: Path, db_path: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    ignored = {
        db_path.resolve(),
        Path(str(db_path) + "-wal").resolve(),
        Path(str(db_path) + "-shm").resolve(),
    }
    files: list[Path] = []
    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in ignored or path.name.endswith(".tmp"):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix().lower())


def _backup_sqlite(source_path: Path, destination_path: Path, should_cancel: CancelCheck | None) -> None:
    if not source_path.is_file():
        return
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path), timeout=5.0)
    target = sqlite3.connect(str(destination_path))
    try:
        source.execute("PRAGMA query_only = ON")
        def on_progress(_status: int, _remaining: int, _total: int) -> None:
            _check_cancel(should_cancel)

        source.backup(target, pages=256, progress=on_progress, sleep=0.01)
        target.commit()
    finally:
        target.close()
        source.close()


def create_data_backup(
    db_path: str | Path,
    destination: str | Path,
    *,
    settings_payload: dict[str, object] | None = None,
    progress: Progress | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Create one portable backup containing the complete user data directory.

    SQLite is copied with its online backup API instead of copying a live DB/WAL
    pair. Other project data is written directly to the archive; project writes
    use atomic replacement, so readers see either the old or new complete file.
    """

    db_path = Path(db_path)
    data_dir = db_path.parent
    destination = Path(destination)
    try:
        resolved_data_dir = data_dir.resolve()
        resolved_destination = destination.resolve()
    except OSError:
        resolved_data_dir = data_dir.absolute()
        resolved_destination = destination.absolute()
    if resolved_destination == resolved_data_dir or resolved_data_dir in resolved_destination.parents:
        raise ValueError("백업 파일은 data 폴더 밖에 저장하세요.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
    temporary_db = destination.with_name(destination.name + f".{uuid.uuid4().hex}.sqlite.tmp")
    data_files = _iter_data_files(data_dir, db_path)
    archived_count = 0

    try:
        _check_cancel(should_cancel)
        _safe_progress(progress, "데이터베이스의 일관된 백업본을 생성하는 중…")
        _backup_sqlite(db_path, temporary_db, should_cancel)

        manifest = {
            "schema": BACKUP_SCHEMA,
            "application": "GFL2 Tools",
            "application_version": __version__,
            "created_at": _utc_now(),
            "data_directory": "data",
        }
        _safe_progress(progress, f"data 폴더의 파일 {len(data_files):,}개를 백업하는 중…")
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            if settings_payload is not None:
                archive.writestr(
                    SETTINGS_NAME,
                    json.dumps(settings_payload, ensure_ascii=False, indent=2),
                )
            if temporary_db.is_file():
                archive.write(temporary_db, arcname=f"data/{db_path.name}")
                archived_count += 1
            for path in data_files:
                _check_cancel(should_cancel)
                relative = path.relative_to(data_dir)
                archive.write(
                    path,
                    arcname=(Path("data") / relative).as_posix(),
                    compress_type=_zip_compression_for(path),
                )
                archived_count += 1
                if archived_count % 50 == 0:
                    _safe_progress(progress, f"백업 중… {archived_count:,}개 파일")

        _check_cancel(should_cancel)
        os.replace(temporary_zip, destination)
        _safe_progress(progress, "전체 데이터 백업을 완료했습니다.")
        return {
            "path": str(destination),
            "files": archived_count,
            "size": destination.stat().st_size if destination.is_file() else 0,
            "created_at": manifest["created_at"],
        }
    finally:
        for path in (temporary_zip, temporary_db):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _validated_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    names = {member.filename for member in members}
    if MANIFEST_NAME not in names:
        raise BackupFormatError("GFL2 Tools 백업 파일이 아닙니다. 백업 정보가 없습니다.")

    try:
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupFormatError("백업 정보를 읽을 수 없습니다.") from exc
    if not isinstance(manifest, dict) or int(manifest.get("schema") or 0) != BACKUP_SCHEMA:
        raise BackupFormatError("지원하지 않는 백업 형식입니다.")
    if str(manifest.get("application") or "") != "GFL2 Tools":
        raise BackupFormatError("GFL2 Tools에서 만든 백업 파일이 아닙니다.")

    for member in members:
        raw = member.filename.replace("\\", "/")
        parts = Path(raw).parts
        if raw.startswith("/") or ".." in parts:
            raise BackupFormatError("백업 파일에 안전하지 않은 경로가 포함되어 있습니다.")
        if raw not in {MANIFEST_NAME, SETTINGS_NAME} and not raw.startswith("data/"):
            raise BackupFormatError("백업 파일에 알 수 없는 항목이 포함되어 있습니다.")
    return members


def prepare_data_restore(
    db_path: str | Path,
    source: str | Path,
    *,
    progress: Progress | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Validate/extract a backup and schedule an atomic restore for next launch."""

    db_path = Path(db_path)
    data_dir = db_path.parent
    project_root = data_dir.parent
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)

    staging_root = project_root / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
    staged_data = staging_root / "data"
    marker_path = project_root / PENDING_RESTORE_NAME
    settings_payload: dict[str, object] | None = None

    try:
        _check_cancel(should_cancel)
        _safe_progress(progress, "백업 파일을 검사하는 중…")
        with zipfile.ZipFile(source, "r") as archive:
            members = _validated_archive_members(archive)
            staging_root.mkdir(parents=True, exist_ok=False)
            for index, member in enumerate(members, start=1):
                _check_cancel(should_cancel)
                archive.extract(member, staging_root)
                if index % 50 == 0:
                    _safe_progress(progress, f"복원 파일 확인 중… {index:,}/{len(members):,}")
            if SETTINGS_NAME in {member.filename for member in members}:
                try:
                    raw = json.loads((staging_root / SETTINGS_NAME).read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        settings_payload = raw
                except (OSError, json.JSONDecodeError):
                    settings_payload = None

        if not staged_data.is_dir():
            raise BackupFormatError("백업에 data 폴더가 없습니다.")
        staged_db = staged_data / db_path.name
        if staged_db.is_file():
            connection = sqlite3.connect(str(staged_db), timeout=5.0)
            try:
                connection.execute("PRAGMA query_only = ON")
                row = connection.execute("PRAGMA quick_check").fetchone()
                if row is None or str(row[0]).lower() != "ok":
                    raise BackupFormatError("백업 데이터베이스 무결성 검사에 실패했습니다.")
                tables = {
                    str(item[0])
                    for item in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if "meta" not in tables:
                    raise BackupFormatError("GFL2 Tools 데이터베이스가 포함된 백업이 아닙니다.")
                schema_id_row = connection.execute(
                    "SELECT value FROM meta WHERE key='schema_id'"
                ).fetchone()
                schema_version_row = connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                schema_id = str(schema_id_row[0]) if schema_id_row is not None else ""
                try:
                    schema_version = int(schema_version_row[0]) if schema_version_row is not None else -1
                except (TypeError, ValueError):
                    schema_version = -1
                if schema_id != SCHEMA_ID or schema_version != SCHEMA_VERSION:
                    raise BackupFormatError(
                        "현재 버전에서 사용할 수 없는 데이터베이스가 포함된 백업입니다."
                    )
            finally:
                connection.close()
            try:
                with Repository(staged_db):
                    pass
            except (SchemaMismatchError, sqlite3.Error) as exc:
                raise BackupFormatError(
                    "현재 버전에서 사용할 수 없는 데이터베이스가 포함된 백업입니다."
                ) from exc

        marker = {
            "schema": 1,
            "created_at": _utc_now(),
            "source": str(source),
            "staging_root": str(staging_root),
            "settings": settings_payload,
        }
        previous = pending_restore_info(data_dir)
        atomic_write_json(marker_path, marker, ensure_ascii=False, indent=2)
        if previous is not None:
            _remove_pending_staging(data_dir, previous)
        _safe_progress(progress, "복원 준비가 완료되었습니다. 다음 실행에서 적용됩니다.")
        return {
            "source": str(source),
            "marker": str(marker_path),
            "staging_root": str(staging_root),
        }
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise



def pending_restore_info(data_dir: str | Path) -> dict[str, object] | None:
    data_dir = Path(data_dir)
    marker_path = data_dir.parent / PENDING_RESTORE_NAME
    if not marker_path.is_file():
        return None
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"marker": str(marker_path), "invalid": True}
    if not isinstance(payload, dict):
        return {"marker": str(marker_path), "invalid": True}
    return {**payload, "marker": str(marker_path)}


def _remove_pending_staging(data_dir: Path, info: dict[str, object]) -> None:
    staging_root = Path(str(info.get("staging_root") or ""))
    project_root = data_dir.parent.resolve()
    try:
        resolved = staging_root.resolve()
    except OSError:
        resolved = staging_root
    if staging_root.name.startswith(STAGING_PREFIX) and resolved.parent == project_root:
        shutil.rmtree(staging_root, ignore_errors=True)


def cancel_pending_restore(data_dir: str | Path) -> bool:
    data_dir = Path(data_dir)
    marker_path = data_dir.parent / PENDING_RESTORE_NAME
    info = pending_restore_info(data_dir)
    if info is None:
        return False
    _remove_pending_staging(data_dir, info)
    marker_path.unlink(missing_ok=True)
    return True


def apply_pending_restore(data_dir: str | Path) -> dict[str, object] | None:
    """Apply a staged restore before Repository opens any SQLite handle."""

    data_dir = Path(data_dir)
    project_root = data_dir.parent
    marker_path = project_root / PENDING_RESTORE_NAME
    if not marker_path.is_file():
        return None

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupFormatError("대기 중인 복원 정보를 읽을 수 없습니다.") from exc
    if not isinstance(marker, dict) or int(marker.get("schema") or 0) != 1:
        raise BackupFormatError("대기 중인 복원 정보 형식이 올바르지 않습니다.")

    staging_root = Path(str(marker.get("staging_root") or ""))
    try:
        resolved_staging = staging_root.resolve()
        resolved_project = project_root.resolve()
    except OSError as exc:
        raise BackupFormatError("복원 대기 경로를 확인할 수 없습니다.") from exc
    if not staging_root.name.startswith(STAGING_PREFIX) or resolved_staging.parent != resolved_project:
        raise BackupFormatError("복원 대기 경로가 안전하지 않습니다.")
    staged_data = staging_root / "data"
    if not staged_data.is_dir():
        raise BackupFormatError("복원 대기 폴더를 찾을 수 없습니다.")

    rollback = project_root / f"{ROLLBACK_PREFIX}{uuid.uuid4().hex}"
    moved_current = False
    try:
        if data_dir.exists():
            data_dir.rename(rollback)
            moved_current = True
        staged_data.rename(data_dir)
    except Exception:
        if not data_dir.exists() and moved_current and rollback.exists():
            rollback.rename(data_dir)
        raise
    else:
        shutil.rmtree(rollback, ignore_errors=True)
        shutil.rmtree(staging_root, ignore_errors=True)
        marker_path.unlink(missing_ok=True)

    settings_payload = marker.get("settings") if isinstance(marker.get("settings"), dict) else None
    return {
        "source": str(marker.get("source") or ""),
        "settings": settings_payload,
    }

PROGRAM_BACKUP_SCHEMA = 1
PROGRAM_MANIFEST_NAME = "program_data_manifest.json"
PROGRAM_DATA_PATHS = (
    "reference_data",
    "remote-api.json",
    "remote-api-cache",
    "master_data/illustrations",
)


def _iter_program_data_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for rel in PROGRAM_DATA_PATHS:
        root = data_dir / rel
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(set(files), key=lambda item: item.as_posix().lower())


def create_program_data_backup(
    data_dir: str | Path,
    destination: str | Path,
    *,
    progress: Progress | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Back up remotely supplied/base catalog data without user-owned state."""

    data_dir = Path(data_dir)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = _iter_program_data_files(data_dir)
    temporary_zip = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
    archived = 0
    try:
        _safe_progress(progress, f"프로그램 기본 데이터 {len(files):,}개 파일을 백업하는 중…")
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(
                PROGRAM_MANIFEST_NAME,
                json.dumps(
                    {
                        "schema": PROGRAM_BACKUP_SCHEMA,
                        "application": "GFL2 Tools",
                        "application_version": __version__,
                        "created_at": _utc_now(),
                        "paths": list(PROGRAM_DATA_PATHS),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for index, path in enumerate(files, start=1):
                _check_cancel(should_cancel)
                archive.write(
                    path,
                    arcname=(Path("data") / path.relative_to(data_dir)).as_posix(),
                    compress_type=_zip_compression_for(path),
                )
                archived += 1
                if index % 50 == 0:
                    _safe_progress(progress, f"프로그램 데이터 백업 중… {index:,}/{len(files):,}")
        _check_cancel(should_cancel)
        os.replace(temporary_zip, destination)
        _safe_progress(progress, "프로그램 기본 데이터 백업을 완료했습니다.")
        return {
            "path": str(destination),
            "files": archived,
            "size": destination.stat().st_size if destination.is_file() else 0,
        }
    finally:
        try:
            temporary_zip.unlink(missing_ok=True)
        except OSError:
            pass


def restore_program_data_backup(
    data_dir: str | Path,
    source: str | Path,
    *,
    progress: Progress | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Restore only program/reference/API data from a dedicated backup ZIP."""

    data_dir = Path(data_dir)
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    staging = data_dir.parent / f".gfl2_program_restore_{uuid.uuid4().hex}"
    try:
        _safe_progress(progress, "프로그램 데이터 백업을 검사하는 중…")
        with zipfile.ZipFile(source, "r") as archive:
            names = archive.namelist()
            if PROGRAM_MANIFEST_NAME not in names:
                raise BackupFormatError("GFL2 Tools 프로그램 데이터 백업이 아닙니다.")
            try:
                manifest = json.loads(archive.read(PROGRAM_MANIFEST_NAME).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupFormatError("프로그램 데이터 백업 정보를 읽을 수 없습니다.") from exc
            if not isinstance(manifest, dict) or int(manifest.get("schema") or 0) != PROGRAM_BACKUP_SCHEMA:
                raise BackupFormatError("지원하지 않는 프로그램 데이터 백업 형식입니다.")
            for name in names:
                normalized = name.replace("\\", "/")
                parts = Path(normalized).parts
                if normalized.startswith("/") or ".." in parts:
                    raise BackupFormatError("백업 파일에 안전하지 않은 경로가 포함되어 있습니다.")
                if normalized == PROGRAM_MANIFEST_NAME:
                    continue
                if not normalized.startswith("data/"):
                    raise BackupFormatError("프로그램 데이터 백업에 알 수 없는 항목이 포함되어 있습니다.")
                rel = Path(*parts[1:]).as_posix() if len(parts) > 1 else ""
                if not any(rel == allowed or rel.startswith(allowed.rstrip("/") + "/") for allowed in PROGRAM_DATA_PATHS):
                    raise BackupFormatError("프로그램 데이터 범위를 벗어난 파일이 포함되어 있습니다.")
            staging.mkdir(parents=True, exist_ok=False)
            archive.extractall(staging)

        staged_data = staging / "data"
        _safe_progress(progress, "프로그램 기본 데이터를 적용하는 중…")
        for rel in PROGRAM_DATA_PATHS:
            _check_cancel(should_cancel)
            target = data_dir / rel
            staged = staged_data / rel
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink(missing_ok=True)
            if not staged.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if staged.is_dir():
                shutil.copytree(staged, target)
            else:
                shutil.copy2(staged, target)
        _safe_progress(progress, "프로그램 기본 데이터 복원을 완료했습니다.")
        return {"source": str(source)}
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupFormatError(f"프로그램 데이터 백업을 읽을 수 없습니다: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
