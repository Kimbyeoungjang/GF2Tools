from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_json
from .repository import Repository, SCHEMA_ID, SCHEMA_VERSION

SNAPSHOT_SCHEMA_ID = SCHEMA_ID
SNAPSHOT_VERSION = SCHEMA_VERSION

_JSON_COLUMNS = {
    "remoldings": ["slots_json"],
    "game_formations": ["members_json"],
    "formation_members": ["remolding_uids_json", "remolding_targets_json"],
    "remolding_score_settings": ["config_json"],
    "remolding_target_profiles": ["targets_json"],
    "remolding_character_profiles": ["slot_counts_json", "tags_json"],
}

_JSON_VALUE_TYPES: dict[tuple[str, str], type] = {
    ("remoldings", "slots"): list,
    ("game_formations", "members"): list,
    ("formation_members", "remolding_uids"): list,
    ("formation_members", "remolding_targets"): dict,
    ("remolding_score_settings", "config"): dict,
    ("remolding_target_profiles", "targets"): dict,
    ("remolding_character_profiles", "slot_counts"): dict,
    ("remolding_character_profiles", "tags"): list,
}

_JSON_LIST_ITEM_TYPES: dict[tuple[str, str], type] = {
    ("remoldings", "slots"): dict,
    ("game_formations", "members"): dict,
    ("formation_members", "remolding_uids"): str,
    ("remolding_character_profiles", "tags"): str,
}

_JSON_DICT_VALUE_TYPES: dict[tuple[str, str], type] = {
    ("formation_members", "remolding_targets"): dict,
    ("remolding_target_profiles", "targets"): dict,
}

TABLES = [
    "dolls",
    "remoldings",
    "game_formations",
    "formation_plans",
    "formation_members",
    "remolding_patterns",
    "remolding_pattern_slots",
    "remolding_score_settings",
    "remolding_option_overrides",
    "remolding_target_profiles",
    "remolding_character_profiles",
]


def _decode_json_columns(table: str, row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    for column in _JSON_COLUMNS.get(table, []):
        if column in decoded:
            plain = column[:-5] if column.endswith("_json") else column
            decoded[plain] = json.loads(decoded.pop(column))
    return decoded


def _table_columns(repo: Repository, table: str) -> set[str]:
    return {str(row[1]) for row in repo.con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _table_column_contract(repo: Repository, table: str) -> dict[str, tuple[str, bool]]:
    return {
        str(row[1]): (str(row[2] or "").upper(), bool(row[3]) or bool(row[5]))
        for row in repo.con.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _scalar_type_matches(declared_type: str, value: Any) -> bool:
    if "INT" in declared_type:
        return type(value) is int
    if any(token in declared_type for token in ("CHAR", "CLOB", "TEXT")):
        return isinstance(value, str)
    if any(token in declared_type for token in ("REAL", "FLOA", "DOUB")):
        return type(value) in (int, float)
    if "BLOB" in declared_type:
        return isinstance(value, (bytes, bytearray))
    return True


def _validate_snapshot_payload(repo: Repository, payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("snapshot root must be a JSON object")
    if payload.get("schema_id") != SNAPSHOT_SCHEMA_ID:
        raise ValueError(
            f"unsupported snapshot schema: {payload.get('schema_id')!r}; expected {SNAPSHOT_SCHEMA_ID!r}"
        )
    if payload.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError(
            f"unsupported snapshot version: {payload.get('snapshot_version')}; expected {SNAPSHOT_VERSION}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("snapshot data must be a JSON object")

    unknown_tables = sorted(set(data) - set(TABLES))
    if unknown_tables:
        raise ValueError(f"snapshot contains unknown tables: {', '.join(unknown_tables)}")
    missing_tables = [table for table in TABLES if table not in data]
    if missing_tables:
        raise ValueError(f"snapshot is incomplete; missing tables: {', '.join(missing_tables)}")

    validated: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        rows = data[table]
        if not isinstance(rows, list):
            raise ValueError(f"snapshot table {table!r} must be an array")
        db_columns = _table_columns(repo, table)
        column_contract = _table_column_contract(repo, table)
        json_columns = set(_JSON_COLUMNS.get(table, []))
        plain_json = {
            name[:-5] if name.endswith("_json") else name
            for name in _JSON_COLUMNS.get(table, [])
        }
        expected_columns = (db_columns - json_columns) | plain_json
        normalized_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"snapshot table {table!r} row {index} must be an object")
            actual_columns = set(row)
            if actual_columns != expected_columns:
                missing = sorted(expected_columns - actual_columns)
                extra = sorted(actual_columns - expected_columns)
                detail = []
                if missing:
                    detail.append("missing=" + ",".join(missing))
                if extra:
                    detail.append("extra=" + ",".join(extra))
                raise ValueError(
                    f"snapshot table {table!r} row {index} does not match current columns: "
                    + "; ".join(detail)
                )
            for field in db_columns - json_columns:
                value = row[field]
                declared_type, required = column_contract[field]
                if value is None:
                    if required:
                        raise ValueError(
                            f"snapshot table {table!r} row {index} field {field!r} cannot be null"
                        )
                    continue
                if not _scalar_type_matches(declared_type, value):
                    raise ValueError(
                        f"snapshot table {table!r} row {index} field {field!r} "
                        f"does not match declared type {declared_type or 'ANY'}"
                    )

            for (type_table, field), expected_type in _JSON_VALUE_TYPES.items():
                if type_table != table:
                    continue
                value = row[field]
                if not isinstance(value, expected_type):
                    raise ValueError(
                        f"snapshot table {table!r} row {index} field {field!r} "
                        f"must be {expected_type.__name__}"
                    )
                item_type = _JSON_LIST_ITEM_TYPES.get((table, field))
                if item_type is not None and any(not isinstance(item, item_type) for item in value):
                    raise ValueError(
                        f"snapshot table {table!r} row {index} field {field!r} "
                        f"contains non-{item_type.__name__} entries"
                    )
                value_type = _JSON_DICT_VALUE_TYPES.get((table, field))
                if value_type is not None and any(not isinstance(item, value_type) for item in value.values()):
                    raise ValueError(
                        f"snapshot table {table!r} row {index} field {field!r} "
                        f"contains non-{value_type.__name__} values"
                    )
            normalized_rows.append(dict(row))
        validated[table] = normalized_rows
    return validated


def export_snapshot(repo: Repository, path: str | Path) -> Path:
    with repo.read_transaction():
        data = {
            table: [_decode_json_columns(table, row) for row in repo.rows(table)]
            for table in TABLES
        }
    output = {
        "schema_id": SNAPSHOT_SCHEMA_ID,
        "snapshot_version": SNAPSHOT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    target = Path(path)
    atomic_write_json(target, output, ensure_ascii=False, indent=2)
    return target


def import_snapshot(repo: Repository, path: str | Path, *, replace: bool = True) -> None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid snapshot file: {exc}") from exc

    data = _validate_snapshot_payload(repo, payload)
    with repo.transaction():
        if replace:
            for table in [
                "formation_members",
                "remolding_pattern_slots",
                "remolding_option_overrides",
                "remolding_score_settings",
                "remolding_target_profiles",
                "remolding_character_profiles",
                "formation_plans",
                "remolding_patterns",
                "dolls",
                "remoldings",
                "game_formations",
            ]:
                repo.con.execute(f'DELETE FROM "{table}"')

        for table in TABLES:
            db_columns = _table_columns(repo, table)
            for source_row in data[table]:
                row = dict(source_row)
                for json_column in _JSON_COLUMNS.get(table, []):
                    plain = json_column[:-5] if json_column.endswith("_json") else json_column
                    if plain in row:
                        row[json_column] = json.dumps(row.pop(plain), ensure_ascii=False)
                columns = [name for name in row if name in db_columns]
                if not columns:
                    continue
                quoted = ",".join(f'"{name}"' for name in columns)
                placeholders = ",".join("?" for _ in columns)
                repo.con.execute(
                    f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})',
                    tuple(row[name] for name in columns),
                )
