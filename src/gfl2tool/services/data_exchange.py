from __future__ import annotations

import csv
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import reference
from ..atomic_io import atomic_write_json
from ..models import FormationMember, GameFormation, Remolding
from ..repository import Repository, utc_now
from .remolding_csv import decode_remolding_contents, export_remoldings_csv

REFERENCE_EXCHANGE_SCHEMA = "gfl2-reference-exchange"
REFERENCE_EXCHANGE_VERSION = 1
USER_EXCHANGE_SCHEMA = "gfl2-user-exchange"
USER_EXCHANGE_VERSION = 1

REFERENCE_DATASET_LABELS = {
    key: meta["label"] for key, meta in reference.REFERENCE_DATASETS.items()
}

USER_DATASET_LABELS = {
    "dolls": "보유 인형",
    "remoldings": "보유 리몰딩",
    "game_formations": "게임 제대",
    "formation_plans": "제대 계획",
    "remolding_patterns": "리몰딩 장착 패턴",
    "remolding_preferences": "리몰딩 추천/점수 설정",
    "tactics": "택틱 라이브러리",
    "doll_categories": "인형 사용자 분류",
    "doll_skill_cycles": "인형 스킬 사이클",
    "formation_member_preferences": "제대 인형 이미지·스킬 사이클",
    "equipment_setup": "장비·키 사용자 데이터",
    "app_settings": "프로그램 설정",
}

_DB_GROUPS: dict[str, tuple[str, ...]] = {
    "dolls": ("dolls",),
    "remoldings": ("remoldings",),
    "game_formations": ("game_formations",),
    "formation_plans": ("formation_plans", "formation_members"),
    "remolding_patterns": ("remolding_patterns", "remolding_pattern_slots"),
    "remolding_preferences": (
        "remolding_score_settings",
        "remolding_option_overrides",
        "remolding_target_profiles",
        "remolding_character_profiles",
    ),
}

_JSON_COLUMNS = {
    "remoldings": {"slots_json": "slots"},
    "game_formations": {"members_json": "members"},
    "formation_members": {
        "remolding_uids_json": "remolding_uids",
        "remolding_targets_json": "remolding_targets",
    },
    "remolding_score_settings": {"config_json": "config"},
    "remolding_target_profiles": {"targets_json": "targets"},
    "remolding_character_profiles": {
        "slot_counts_json": "slot_counts",
        "tags_json": "tags",
    },
}

_FILE_GROUPS: dict[str, tuple[str, ...]] = {
    "tactics": ("tactics/library.json", "tactics/overlay_state.json"),
    "doll_categories": ("doll_categories.json",),
    "doll_skill_cycles": ("doll_skill_cycles.json",),
    "formation_member_preferences": ("formation_member_preferences.json",),
    "equipment_setup": ("master_data/tactic_equipment_user.json",),
}

# User-facing backup presets. Tactics have their own dedicated backup button and
# app settings are intentionally excluded from "사용자 데이터" so restoring
# owned/planner data does not unexpectedly change theme/hotkeys.
USER_BACKUP_DATASETS = tuple(
    key for key in USER_DATASET_LABELS if key not in {"tactics", "app_settings"}
)
TACTIC_BACKUP_DATASETS = ("tactics",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON 파일을 읽을 수 없습니다: {path}: {exc}") from exc


def export_reference_dataset(data_dir: str | Path, key: str, path: str | Path) -> Path:
    if key not in REFERENCE_DATASET_LABELS:
        raise KeyError(key)
    reference.configure_override_root(data_dir)
    payload = reference.dataset_payload(key)
    reference.validate_dataset_payload(key, payload)
    output = {
        "schema_id": REFERENCE_EXCHANGE_SCHEMA,
        "schema_version": REFERENCE_EXCHANGE_VERSION,
        "dataset": key,
        "label": REFERENCE_DATASET_LABELS[key],
        "exported_at": _now(),
        "payload": payload,
    }
    target = Path(path)
    atomic_write_json(target, output, ensure_ascii=False, indent=2)
    return target


def import_reference_dataset(data_dir: str | Path, key: str, path: str | Path) -> Path:
    if key not in REFERENCE_DATASET_LABELS:
        raise KeyError(key)
    raw = _read_json(Path(path))
    if isinstance(raw, dict) and raw.get("schema_id") == REFERENCE_EXCHANGE_SCHEMA:
        if raw.get("dataset") != key:
            raise ValueError(f"선택한 데이터셋({key})과 파일 데이터셋({raw.get('dataset')})이 다릅니다.")
        if int(raw.get("schema_version") or 0) != REFERENCE_EXCHANGE_VERSION:
            raise ValueError("지원하지 않는 레퍼런스 교환 파일 버전입니다.")
        payload = raw.get("payload")
    else:
        # Maintainer convenience: allow a raw canonical JSON dataset as input.
        payload = raw
    reference.validate_dataset_payload(key, payload)
    meta = reference.REFERENCE_DATASETS[key]
    target = Path(data_dir) / "reference_data" / meta["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, payload, ensure_ascii=False, indent=2)
    reference.configure_override_root(data_dir)
    return target


def export_all_reference_data(data_dir: str | Path, path: str | Path) -> Path:
    reference.configure_override_root(data_dir)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gfl2-reference-export-") as td:
        root = Path(td)
        datasets: list[dict[str, str]] = []
        for key, meta in reference.REFERENCE_DATASETS.items():
            payload = reference.dataset_payload(key)
            reference.validate_dataset_payload(key, payload)
            name = f"datasets/{key}.json"
            out = root / name
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(out, payload, ensure_ascii=False, indent=2)
            datasets.append({"key": key, "label": meta["label"], "file": name})
        manifest = {
            "schema_id": REFERENCE_EXCHANGE_SCHEMA,
            "schema_version": REFERENCE_EXCHANGE_VERSION,
            "exported_at": _now(),
            "datasets": datasets,
        }
        atomic_write_json(root / "manifest.json", manifest, ensure_ascii=False, indent=2)
        tmp = target.with_name(f".{target.name}.tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for file in sorted(root.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(root).as_posix())
        tmp.replace(target)
    return target


def import_all_reference_data(data_dir: str | Path, path: str | Path) -> dict[str, Path]:
    source = Path(path)
    with tempfile.TemporaryDirectory(prefix="gfl2-reference-import-") as td:
        root = Path(td)
        try:
            with zipfile.ZipFile(source, "r") as zf:
                bad = [n for n in zf.namelist() if Path(n).is_absolute() or ".." in Path(n).parts]
                if bad:
                    raise ValueError("ZIP에 안전하지 않은 경로가 포함되어 있습니다.")
                zf.extractall(root)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"레퍼런스 ZIP을 읽을 수 없습니다: {exc}") from exc
        manifest = _read_json(root / "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("schema_id") != REFERENCE_EXCHANGE_SCHEMA:
            raise ValueError("GFL2 레퍼런스 묶음 파일이 아닙니다.")
        if int(manifest.get("schema_version") or 0) != REFERENCE_EXCHANGE_VERSION:
            raise ValueError("지원하지 않는 레퍼런스 묶음 버전입니다.")
        staged: dict[str, tuple[Any, Path]] = {}
        for entry in manifest.get("datasets") or []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            rel = str(entry.get("file") or "")
            if key not in REFERENCE_DATASET_LABELS or not rel:
                continue
            file = root / rel
            payload = _read_json(file)
            reference.validate_dataset_payload(key, payload)
            staged[key] = (payload, file)
        missing = sorted(set(REFERENCE_DATASET_LABELS) - set(staged))
        if missing:
            raise ValueError("레퍼런스 묶음에 데이터셋이 누락되었습니다: " + ", ".join(missing))
        results: dict[str, Path] = {}
        out_root = Path(data_dir) / "reference_data"
        out_root.mkdir(parents=True, exist_ok=True)
        for key, (payload, _file) in staged.items():
            target = out_root / reference.REFERENCE_DATASETS[key]["filename"]
            atomic_write_json(target, payload, ensure_ascii=False, indent=2)
            results[key] = target
    reference.configure_override_root(data_dir)
    return results


def _export_table_rows(repo: Repository, table: str) -> list[dict[str, Any]]:
    rows = repo.rows(table)
    mapping = _JSON_COLUMNS.get(table, {})
    out: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        for db_name, public_name in mapping.items():
            if db_name in row:
                try:
                    row[public_name] = json.loads(row.pop(db_name))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"{table}.{db_name} JSON 데이터가 손상되었습니다.") from exc
        out.append(row)
    return out


def _table_columns(repo: Repository, table: str) -> set[str]:
    return {str(row[1]) for row in repo.con.execute(f'PRAGMA table_info("{table}")')}


def _validate_table_rows(repo: Repository, table: str, rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{table} 데이터는 객체 배열이어야 합니다.")
    db_columns = _table_columns(repo, table)
    mapping = _JSON_COLUMNS.get(table, {})
    public_columns = (db_columns - set(mapping)) | set(mapping.values())
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(rows):
        if set(source) != public_columns:
            missing = sorted(public_columns - set(source))
            extra = sorted(set(source) - public_columns)
            raise ValueError(f"{table} {index + 1}행 컬럼 불일치: 누락={missing}, 추가={extra}")
        normalized.append(dict(source))
    return normalized


def _insert_table_rows(repo: Repository, table: str, rows: list[dict[str, Any]]) -> None:
    db_columns = _table_columns(repo, table)
    mapping = _JSON_COLUMNS.get(table, {})
    for source in rows:
        row = dict(source)
        for db_name, public_name in mapping.items():
            if public_name in row:
                row[db_name] = json.dumps(row.pop(public_name), ensure_ascii=False)
        columns = [name for name in row if name in db_columns]
        quoted = ",".join(f'"{name}"' for name in columns)
        placeholders = ",".join("?" for _ in columns)
        repo.con.execute(
            f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})',
            tuple(row[name] for name in columns),
        )


def _file_group_payload(repo: Repository, key: str) -> dict[str, Any]:
    data_dir = repo.path.parent
    files: dict[str, Any] = {}
    for rel in _FILE_GROUPS[key]:
        path = data_dir / rel
        files[rel] = _read_json(path) if path.is_file() else None
    return {"files": files}


def _validate_file_group_payload(key: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise ValueError(f"{key} 파일 데이터 형식이 올바르지 않습니다.")
    expected = set(_FILE_GROUPS[key])
    actual = set(payload["files"])
    if actual != expected:
        raise ValueError(f"{key} 파일 목록 불일치: expected={sorted(expected)}, actual={sorted(actual)}")
    return dict(payload)


def user_dataset_payload(repo: Repository, key: str, *, settings_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if key in _DB_GROUPS:
        return {"tables": {table: _export_table_rows(repo, table) for table in _DB_GROUPS[key]}}
    if key in _FILE_GROUPS:
        return _file_group_payload(repo, key)
    if key == "app_settings":
        return {"settings": dict(settings_payload or {})}
    raise KeyError(key)


def export_user_dataset(
    repo: Repository,
    key: str,
    path: str | Path,
    *,
    settings_payload: dict[str, Any] | None = None,
) -> Path:
    if key not in USER_DATASET_LABELS:
        raise KeyError(key)
    output = {
        "schema_id": USER_EXCHANGE_SCHEMA,
        "schema_version": USER_EXCHANGE_VERSION,
        "dataset": key,
        "label": USER_DATASET_LABELS[key],
        "exported_at": _now(),
        "payload": user_dataset_payload(repo, key, settings_payload=settings_payload),
    }
    target = Path(path)
    atomic_write_json(target, output, ensure_ascii=False, indent=2)
    return target


def _read_user_dataset_file(repo: Repository, expected_key: str, path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    if not isinstance(raw, dict) or raw.get("schema_id") != USER_EXCHANGE_SCHEMA:
        raise ValueError("GFL2 사용자 데이터 교환 파일이 아닙니다.")
    if int(raw.get("schema_version") or 0) != USER_EXCHANGE_VERSION:
        raise ValueError("지원하지 않는 사용자 데이터 교환 파일 버전입니다.")
    if raw.get("dataset") != expected_key:
        raise ValueError(f"선택한 데이터셋({expected_key})과 파일 데이터셋({raw.get('dataset')})이 다릅니다.")
    payload = raw.get("payload")
    return _validate_user_payload(repo, expected_key, payload)


def _validate_user_payload(repo: Repository, key: str, payload: Any) -> dict[str, Any]:
    if key in _DB_GROUPS:
        if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
            raise ValueError(f"{key} 테이블 데이터 형식이 올바르지 않습니다.")
        tables = payload["tables"]
        if set(tables) != set(_DB_GROUPS[key]):
            raise ValueError(f"{key} 테이블 목록이 올바르지 않습니다.")
        return {"tables": {table: _validate_table_rows(repo, table, tables[table]) for table in _DB_GROUPS[key]}}
    if key in _FILE_GROUPS:
        return _validate_file_group_payload(key, payload)
    if key == "app_settings":
        if not isinstance(payload, dict) or not isinstance(payload.get("settings"), dict):
            raise ValueError("프로그램 설정 데이터 형식이 올바르지 않습니다.")
        return {"settings": dict(payload["settings"])}
    raise KeyError(key)


def _apply_user_payload(repo: Repository, key: str, payload: dict[str, Any], *, replace: bool) -> None:
    if key in _DB_GROUPS:
        tables = _DB_GROUPS[key]
        with repo.transaction():
            if replace:
                for table in reversed(tables):
                    repo.con.execute(f'DELETE FROM "{table}"')
            for table in tables:
                _insert_table_rows(repo, table, payload["tables"][table])
        return
    if key in _FILE_GROUPS:
        data_dir = repo.path.parent
        for rel, content in payload["files"].items():
            target = data_dir / rel
            if content is None:
                if replace:
                    target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target, content, ensure_ascii=False, indent=2)
        return
    if key == "app_settings":
        return
    raise KeyError(key)


def import_user_dataset(
    repo: Repository,
    key: str,
    path: str | Path,
    *,
    replace: bool = True,
) -> dict[str, Any]:
    payload = _read_user_dataset_file(repo, key, Path(path))
    _apply_user_payload(repo, key, payload, replace=replace)
    return payload


def _normalized_user_bundle_keys(keys: Any) -> tuple[str, ...]:
    if keys is None:
        return tuple(USER_DATASET_LABELS)
    result: list[str] = []
    for raw in keys:
        key = str(raw)
        if key not in USER_DATASET_LABELS:
            raise KeyError(key)
        if key not in result:
            result.append(key)
    if not result:
        raise ValueError("백업할 사용자 데이터가 없습니다.")
    return tuple(result)


def export_user_data_bundle(
    repo: Repository,
    path: str | Path,
    *,
    keys: Any = None,
    settings_payload: dict[str, Any] | None = None,
) -> Path:
    selected = _normalized_user_bundle_keys(keys)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gfl2-user-export-") as td:
        root = Path(td)
        datasets = []
        with repo.read_transaction():
            for key in selected:
                label = USER_DATASET_LABELS[key]
                payload = user_dataset_payload(repo, key, settings_payload=settings_payload)
                rel = f"datasets/{key}.json"
                file = root / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(file, payload, ensure_ascii=False, indent=2)
                datasets.append({"key": key, "label": label, "file": rel})
        manifest = {
            "schema_id": USER_EXCHANGE_SCHEMA,
            "schema_version": USER_EXCHANGE_VERSION,
            "exported_at": _now(),
            "datasets": datasets,
        }
        atomic_write_json(root / "manifest.json", manifest, ensure_ascii=False, indent=2)
        tmp = target.with_name(f".{target.name}.tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for file in sorted(root.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(root).as_posix())
        tmp.replace(target)
    return target


def import_user_data_bundle(
    repo: Repository,
    path: str | Path,
    *,
    keys: Any = None,
    replace: bool = True,
) -> dict[str, dict[str, Any]]:
    selected = _normalized_user_bundle_keys(keys)
    selected_set = set(selected)
    source = Path(path)
    with tempfile.TemporaryDirectory(prefix="gfl2-user-import-") as td:
        root = Path(td)
        try:
            with zipfile.ZipFile(source, "r") as zf:
                if any(Path(n).is_absolute() or ".." in Path(n).parts for n in zf.namelist()):
                    raise ValueError("ZIP에 안전하지 않은 경로가 포함되어 있습니다.")
                zf.extractall(root)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"사용자 데이터 ZIP을 읽을 수 없습니다: {exc}") from exc
        manifest = _read_json(root / "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("schema_id") != USER_EXCHANGE_SCHEMA:
            raise ValueError("GFL2 사용자 데이터 묶음이 아닙니다.")
        if int(manifest.get("schema_version") or 0) != USER_EXCHANGE_VERSION:
            raise ValueError("지원하지 않는 사용자 데이터 묶음 버전입니다.")
        staged: dict[str, dict[str, Any]] = {}
        for entry in manifest.get("datasets") or []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            rel = str(entry.get("file") or "")
            if key not in selected_set or not rel:
                continue
            staged[key] = _validate_user_payload(repo, key, _read_json(root / rel))
        missing = sorted(selected_set - set(staged))
        if missing:
            raise ValueError("선택한 백업 데이터가 ZIP에 누락되었습니다: " + ", ".join(missing))

        db_keys = [key for key in selected if key in _DB_GROUPS]
        if db_keys:
            target_tables = {table for key in db_keys for table in _DB_GROUPS[key]}
            delete_order = [
                "formation_members", "remolding_pattern_slots",
                "remolding_option_overrides", "remolding_score_settings",
                "remolding_target_profiles", "remolding_character_profiles",
                "formation_plans", "remolding_patterns", "dolls", "remoldings", "game_formations",
            ]
            with repo.transaction():
                if replace:
                    for table in delete_order:
                        if table in target_tables:
                            repo.con.execute(f'DELETE FROM "{table}"')
                for key in db_keys:
                    for table in _DB_GROUPS[key]:
                        _insert_table_rows(repo, table, staged[key]["tables"][table])
        for key in selected:
            if key in _FILE_GROUPS:
                _apply_user_payload(repo, key, staged[key], replace=replace)
        return staged


def export_all_user_data(
    repo: Repository,
    path: str | Path,
    *,
    settings_payload: dict[str, Any] | None = None,
) -> Path:
    return export_user_data_bundle(
        repo, path, keys=tuple(USER_DATASET_LABELS), settings_payload=settings_payload
    )


def import_all_user_data(
    repo: Repository,
    path: str | Path,
    *,
    replace: bool = True,
) -> dict[str, dict[str, Any]]:
    return import_user_data_bundle(repo, path, keys=tuple(USER_DATASET_LABELS), replace=replace)


# ---- External CSV interchange ----------------------------------------------------


def export_dolls_csv(repo: Repository, path: str | Path) -> int:
    rows = repo.con.execute(
        "SELECT doll_id,name,level,rank,favorite FROM dolls ORDER BY doll_id"
    ).fetchall()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["doll_id", "name", "level", "rank", "favorite"])
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return len(rows)


def import_dolls_csv(repo: Repository, path: str | Path, *, replace: bool = True) -> int:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"doll_id", "level", "rank"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("인형 CSV에는 doll_id, level, rank 컬럼이 필요합니다.")
        rows = list(reader)
    names = reference.dolls()
    now = utc_now()
    with repo.transaction():
        if replace:
            repo.con.execute("DELETE FROM dolls")
        for row in rows:
            did = int(row["doll_id"])
            repo.con.execute(
                """INSERT OR REPLACE INTO dolls(doll_id,name,level,rank,illustration_path,favorite,updated_at)
                   VALUES(?,?,?,?,COALESCE((SELECT illustration_path FROM dolls WHERE doll_id=?),NULL),?,?)""",
                (did, str(row.get("name") or names.get(did) or did), int(row["level"]), int(row["rank"]), did,
                 1 if str(row.get("favorite") or "0").strip() in {"1", "true", "True"} else 0, now),
            )
    return len(rows)


def import_remoldings_csv(repo: Repository, path: str | Path, *, replace: bool = True) -> int:
    from .remolding_csv import REMOLDING_CSV_FIELDS
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not set(REMOLDING_CSV_FIELDS).issubset(set(reader.fieldnames or [])):
            raise ValueError("리몰딩 CSV에는 uid, stat1, stat2, stat3 컬럼이 필요합니다.")
        rows = list(reader)
    records: list[Remolding] = []
    for index, row in enumerate(rows, 1):
        uid = str(row.get("uid") or "").strip()
        if uid.startswith("U"):
            uid = uid[1:]
        if not uid:
            raise ValueError(f"리몰딩 CSV {index}행의 uid가 비어 있습니다.")
        tokens = []
        for field in ("stat1", "stat2", "stat3"):
            tokens.extend(str(row.get(field) or "").split())
        try:
            raw = bytes(int(token, 16) for token in tokens)
        except ValueError as exc:
            raise ValueError(f"리몰딩 CSV {index}행의 옵션 코드가 16진수가 아닙니다.") from exc
        try:
            rid = int(uid)
        except ValueError:
            rid = 900_000_000 + index
        records.append(Remolding(uid=uid, remolding_id=rid, raw_contents_hex=raw.hex(" "), slots=decode_remolding_contents(raw)))
    if replace:
        repo.replace_remoldings(records)
    else:
        repo.merge_remoldings(records)
    return len(records)


def export_formations_csv(repo: Repository, path: str | Path) -> int:
    rows = repo.rows("game_formations")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["formation_name", "position", "doll_id", "doll_name"])
        writer.writeheader()
        count = 0
        for row in rows:
            members = json.loads(str(row.get("members_json") or "[]"))
            if not members:
                writer.writerow({"formation_name": row.get("name"), "position": 0, "doll_id": "", "doll_name": ""})
                count += 1
                continue
            for pos, member in enumerate(members, 1):
                writer.writerow({
                    "formation_name": row.get("name"), "position": pos,
                    "doll_id": member.get("doll_id"), "doll_name": member.get("doll_name") or "",
                })
                count += 1
    return count


def import_formations_csv(repo: Repository, path: str | Path, *, replace: bool = True) -> int:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"formation_name", "position", "doll_id"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("제대 CSV에는 formation_name, position, doll_id 컬럼이 필요합니다.")
        rows = list(reader)
    grouped: dict[str, list[tuple[int, FormationMember]]] = {}
    names = reference.dolls()
    for row in rows:
        name = str(row.get("formation_name") or "").strip() or "이름 없는 제대"
        did_text = str(row.get("doll_id") or "").strip()
        if not did_text:
            grouped.setdefault(name, [])
            continue
        did = int(did_text)
        pos = int(row.get("position") or 0)
        grouped.setdefault(name, []).append((pos, FormationMember(did, str(row.get("doll_name") or names.get(did) or did))))
    records = [GameFormation(name, [m for _, m in sorted(items, key=lambda x: x[0])]) for name, items in grouped.items()]
    if replace:
        repo.replace_game_formations(records)
    else:
        repo.merge_game_formations(records)
    return len(records)


def _parse_id_list(value: Any) -> list[int]:
    out: list[int] = []
    for token in str(value or "").replace(",", " ").split():
        try:
            number = int(token)
        except ValueError:
            continue
        if number > 0 and number not in out:
            out.append(number)
    return out


def _equipment_sidecar_from_csv_files(equipment_path: Path, weapons_path: Path) -> dict[str, Any]:
    dolls: dict[str, dict[str, Any]] = {}
    weapons: dict[str, dict[str, Any]] = {}
    if equipment_path.is_file():
        with equipment_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"doll_id", "weapon_uid", "fixed_key_ids", "common_key_ids", "expansion_key_ids"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError("장비 CSV에는 doll_id/weapon_uid/fixed_key_ids/common_key_ids/expansion_key_ids 컬럼이 필요합니다.")
            for index, row in enumerate(reader, 2):
                try:
                    doll_id = int(row.get("doll_id") or 0)
                    weapon_uid = int(row.get("weapon_uid") or 0)
                except ValueError as exc:
                    raise ValueError(f"equipment_dolls.csv {index}행의 ID가 숫자가 아닙니다.") from exc
                if doll_id <= 0:
                    continue
                dolls[str(doll_id)] = {
                    "weapon_uid": max(0, weapon_uid),
                    "fixed_key_ids": _parse_id_list(row.get("fixed_key_ids")),
                    "common_key_ids": _parse_id_list(row.get("common_key_ids")),
                    "common_key_uids": _parse_id_list(row.get("common_key_uids")),
                    "expansion_key_ids": _parse_id_list(row.get("expansion_key_ids")),
                }
    if weapons_path.is_file():
        with weapons_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"uid", "item_id", "level", "rank"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError("무기 CSV에는 uid/item_id/level/rank 컬럼이 필요합니다.")
            for index, row in enumerate(reader, 2):
                try:
                    uid = int(row.get("uid") or 0)
                    item_id = int(row.get("item_id") or 0)
                    level = int(row.get("level") or 0)
                    rank = int(row.get("rank") or 0)
                    equipped_doll_id = int(row.get("equipped_doll_id") or 0)
                except ValueError as exc:
                    raise ValueError(f"weapons.csv {index}행의 숫자 필드가 올바르지 않습니다.") from exc
                if uid <= 0:
                    continue
                candidates = _parse_id_list(row.get("item_id_candidates"))
                if item_id > 0 and item_id not in candidates:
                    candidates.insert(0, item_id)
                weapons[str(uid)] = {
                    "uid": uid, "item_id": max(0, item_id), "item_id_candidates": candidates,
                    "level": max(0, level), "rank": max(0, rank),
                    "equipped_doll_id": max(0, equipped_doll_id),
                }
    return {"schema": 1, "dolls": dolls, "weapons_by_uid": weapons}


def import_equipment_sidecar_csvs(
    repo: Repository,
    *,
    equipment_path: str | Path | None = None,
    weapons_path: str | Path | None = None,
    replace: bool = True,
) -> dict[str, int]:
    """Import equipment/key and weapon CSV sidecars produced by auxiliary tools.

    Only the components explicitly supplied are replaced. This prevents a user
    from accidentally wiping weapon data when importing only the doll/key CSV,
    or vice versa. In merge mode, imported rows are merged by their stable IDs.
    """
    equipment_file = Path(equipment_path) if equipment_path else None
    weapons_file = Path(weapons_path) if weapons_path else None
    if equipment_file is None and weapons_file is None:
        raise ValueError("가져올 장비/키 또는 무기 CSV가 없습니다.")

    missing = Path("__gfl2_missing_sidecar__.csv")
    parsed = _equipment_sidecar_from_csv_files(
        equipment_file if equipment_file is not None else missing,
        weapons_file if weapons_file is not None else missing,
    )

    target = repo.path.parent / "master_data" / "tactic_equipment_user.json"
    current: dict[str, Any] = {"schema": 1, "dolls": {}, "weapons_by_uid": {}}
    if target.is_file():
        raw = _read_json(target)
        if isinstance(raw, dict) and int(raw.get("schema") or 0) == 1:
            current = raw

    current_dolls = dict(current.get("dolls") or {})
    current_weapons = dict(current.get("weapons_by_uid") or {})
    imported_dolls = dict(parsed.get("dolls") or {})
    imported_weapons = dict(parsed.get("weapons_by_uid") or {})

    if equipment_file is not None:
        dolls = imported_dolls if replace else {**current_dolls, **imported_dolls}
    else:
        dolls = current_dolls
    if weapons_file is not None:
        weapons = imported_weapons if replace else {**current_weapons, **imported_weapons}
    else:
        weapons = current_weapons

    payload = {"schema": 1, "dolls": dolls, "weapons_by_uid": weapons}
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, payload, ensure_ascii=False, indent=2)
    return {"equipment_dolls": len(imported_dolls), "weapons": len(imported_weapons)}


def _export_equipment_sidecar_csv(payload: dict[str, Any], root: Path) -> None:
    dolls = dict(payload.get("dolls") or {})
    with (root / "equipment_dolls.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["doll_id", "weapon_uid", "fixed_key_ids", "common_key_ids", "common_key_uids", "expansion_key_ids"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for doll_id, raw in sorted(dolls.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else 0):
            row = dict(raw) if isinstance(raw, dict) else {}
            writer.writerow({
                "doll_id": doll_id,
                "weapon_uid": int(row.get("weapon_uid") or 0),
                "fixed_key_ids": " ".join(map(str, row.get("fixed_key_ids") or [])),
                "common_key_ids": " ".join(map(str, row.get("common_key_ids") or [])),
                "common_key_uids": " ".join(map(str, row.get("common_key_uids") or [])),
                "expansion_key_ids": " ".join(map(str, row.get("expansion_key_ids") or [])),
            })
    weapons = dict(payload.get("weapons_by_uid") or {})
    with (root / "weapons.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["uid", "item_id", "item_id_candidates", "level", "rank", "equipped_doll_id"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for uid, raw in sorted(weapons.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else 0):
            row = dict(raw) if isinstance(raw, dict) else {}
            writer.writerow({
                "uid": int(row.get("uid") or uid or 0),
                "item_id": int(row.get("item_id") or 0),
                "item_id_candidates": " ".join(map(str, row.get("item_id_candidates") or [])),
                "level": int(row.get("level") or 0),
                "rank": int(row.get("rank") or 0),
                "equipped_doll_id": int(row.get("equipped_doll_id") or 0),
            })


def export_user_csv_bundle(repo: Repository, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gfl2-user-csv-") as td:
        root = Path(td)
        export_dolls_csv(repo, root / "dolls.csv")
        export_remoldings_csv(repo, root / "remoldings.csv")
        export_formations_csv(repo, root / "formations.csv")
        equipment = repo.path.parent / "master_data" / "tactic_equipment_user.json"
        if equipment.is_file():
            payload = _read_json(equipment)
            shutil.copy2(equipment, root / "equipment_user.json")
            _export_equipment_sidecar_csv(payload, root)
        manifest = {
            "schema_id": "gfl2-user-csv-backup", "schema_version": 1, "exported_at": _now(),
            "files": [p.name for p in root.iterdir() if p.is_file()],
        }
        atomic_write_json(root / "manifest.json", manifest, ensure_ascii=False, indent=2)
        tmp = target.with_name(f".{target.name}.tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(root.iterdir()):
                if file.is_file():
                    zf.write(file, file.name)
        tmp.replace(target)
    return target


def import_user_csv_bundle(repo: Repository, path: str | Path, *, replace: bool = True) -> dict[str, int]:
    """Import the official auxiliary-tool user ZIP.

    Supported schema: ``gfl2-user-csv-backup`` v1. The older
    ``gfl2-user-csv-bundle`` v1 produced by early GF2Tools builds remains
    readable so existing exports do not become stranded. Extra raw CSV files
    such as attachments/common_keys/items are validated by the manifest but the
    current main program consumes their normalized ``equipment_user.json``
    projection.
    """
    with tempfile.TemporaryDirectory(prefix="gfl2-user-csv-import-") as td:
        root = Path(td)
        try:
            with zipfile.ZipFile(path, "r") as zf:
                if any(Path(n).is_absolute() or ".." in Path(n).parts for n in zf.namelist()):
                    raise ValueError("ZIP에 안전하지 않은 경로가 포함되어 있습니다.")
                zf.extractall(root)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"사용자 CSV 묶음을 읽을 수 없습니다: {exc}") from exc

        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("manifest.json이 없는 사용자 CSV 묶음입니다.")
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("사용자 CSV 묶음 manifest 형식이 올바르지 않습니다.")
        schema_id = str(manifest.get("schema_id") or "")
        if schema_id not in {"gfl2-user-csv-backup", "gfl2-user-csv-bundle"}:
            raise ValueError("지원하는 보조 툴 사용자 CSV 묶음이 아닙니다.")
        if int(manifest.get("schema_version") or 0) != 1:
            raise ValueError("지원하지 않는 사용자 CSV 묶음 버전입니다.")

        required = ("dolls.csv", "remoldings.csv", "formations.csv")
        missing = [name for name in required if not (root / name).is_file()]
        if missing:
            raise ValueError("사용자 CSV 묶음에 필수 파일이 없습니다: " + ", ".join(missing))

        results = {"dolls": 0, "remoldings": 0, "formations": 0, "equipment_dolls": 0, "weapons": 0}
        results["dolls"] = import_dolls_csv(repo, root / "dolls.csv", replace=replace)
        results["remoldings"] = import_remoldings_csv(repo, root / "remoldings.csv", replace=replace)
        results["formations"] = import_formations_csv(repo, root / "formations.csv", replace=replace)

        eq = root / "equipment_user.json"
        equipment_csv = root / "equipment_dolls.csv"
        weapons_csv = root / "weapons.csv"
        payload = None
        if eq.is_file():
            payload = _read_json(eq)
            if not isinstance(payload, dict) or int(payload.get("schema") or 0) != 1:
                raise ValueError("장비/키 sidecar 형식이 올바르지 않습니다.")
            results["equipment_dolls"] = len(dict(payload.get("dolls") or {}))
            results["weapons"] = len(dict(payload.get("weapons_by_uid") or {}))
        elif equipment_csv.is_file() or weapons_csv.is_file():
            payload = _equipment_sidecar_from_csv_files(equipment_csv, weapons_csv)
            results["equipment_dolls"] = len(dict(payload.get("dolls") or {}))
            results["weapons"] = len(dict(payload.get("weapons_by_uid") or {}))
        if payload is not None:
            target = repo.path.parent / "master_data" / "tactic_equipment_user.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target, payload, ensure_ascii=False, indent=2)
        return results


# These granular helpers remain part of the import/export service API for CLI/tests
# and older integrations, even though the main GUI now exposes only four backup
# scopes. Keeping explicit references prevents them from being mistaken for dead
# code by the release audit.
_GRANULAR_COMPAT_API = (
    export_reference_dataset,
    import_reference_dataset,
    export_user_dataset,
    import_user_dataset,
    import_equipment_sidecar_csvs,
)
