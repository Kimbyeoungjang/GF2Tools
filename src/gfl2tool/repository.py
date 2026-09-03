from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .models import Doll, GameFormation, Remolding

SCHEMA_ID = "gfl2-tools"
SCHEMA_VERSION = 2


class SchemaMismatchError(RuntimeError):
    """Raised when a database does not exactly match the current schema baseline."""


SCHEMA_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "meta": ("key", "value"),
    "dolls": ("doll_id", "name", "level", "rank", "illustration_path", "favorite", "updated_at"),
    "remoldings": ("uid", "remolding_id", "raw_contents_hex", "slots_json", "updated_at"),
    "game_formations": ("id", "name", "members_json", "updated_at"),
    "formation_plans": ("id", "name", "notes", "created_at", "updated_at"),
    "formation_members": ("plan_id", "position", "doll_id", "doll_name", "remolding_uids_json", "remolding_targets_json"),
    "remolding_patterns": ("id", "name", "doll_id", "doll_name", "character_key", "notes", "created_at", "updated_at"),
    "remolding_pattern_slots": ("pattern_id", "slot_index", "code", "name", "source_remolding_uid", "option_key"),
    "remolding_score_settings": ("id", "config_json", "updated_at"),
    "remolding_option_overrides": ("character_key", "option_key", "score_adjustment", "state", "note", "updated_at"),
    "remolding_target_profiles": ("character_key", "targets_json", "explicit_empty", "updated_at"),
    "remolding_character_profiles": ("character_key", "display_name", "is_dummy", "doll_type", "element_type", "slot_counts_json", "tags_json", "level_override", "updated_at"),
}


RECOMMENDATION_TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS remolding_score_settings (
          id INTEGER PRIMARY KEY CHECK (id=1), config_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS remolding_option_overrides (
          character_key TEXT NOT NULL, option_key TEXT NOT NULL, score_adjustment INTEGER NOT NULL DEFAULT 0,
          state TEXT NOT NULL DEFAULT 'inherit', note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
          PRIMARY KEY(character_key, option_key));
        CREATE TABLE IF NOT EXISTS remolding_target_profiles (
          character_key TEXT PRIMARY KEY, targets_json TEXT NOT NULL DEFAULT '{}',
          explicit_empty INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS remolding_character_profiles (
          character_key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', is_dummy INTEGER NOT NULL DEFAULT 0,
          doll_type TEXT NOT NULL DEFAULT '', element_type TEXT NOT NULL DEFAULT '',
          slot_counts_json TEXT NOT NULL DEFAULT '{}', tags_json TEXT NOT NULL DEFAULT '[]',
          level_override INTEGER, updated_at TEXT NOT NULL);
"""


SCHEMA_DDL = """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dolls (
          doll_id INTEGER PRIMARY KEY, name TEXT, level INTEGER NOT NULL, rank INTEGER NOT NULL,
          illustration_path TEXT, favorite INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS remoldings (
          uid TEXT PRIMARY KEY, remolding_id INTEGER NOT NULL, raw_contents_hex TEXT NOT NULL,
          slots_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS game_formations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, members_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS formation_plans (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS formation_members (
          plan_id INTEGER NOT NULL REFERENCES formation_plans(id) ON DELETE CASCADE,
          position INTEGER NOT NULL, doll_id INTEGER NOT NULL, doll_name TEXT,
          remolding_uids_json TEXT NOT NULL DEFAULT '[]',
          remolding_targets_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY (plan_id, position));

        CREATE TABLE IF NOT EXISTS remolding_patterns (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, doll_id INTEGER, doll_name TEXT,
          character_key TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS remolding_pattern_slots (
          pattern_id INTEGER NOT NULL REFERENCES remolding_patterns(id) ON DELETE CASCADE,
          slot_index INTEGER NOT NULL, code TEXT NOT NULL, name TEXT, source_remolding_uid TEXT, option_key TEXT,
          PRIMARY KEY (pattern_id, slot_index));

""" + RECOMMENDATION_TABLE_DDL


def _normalize_schema_sql(value: object) -> str:
    return " ".join(str(value or "").split())


def _schema_contract(con: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    """Return the exact current table/index/trigger/view DDL contract, excluding SQLite internals."""
    rows = con.execute(
        """SELECT type,name,tbl_name,sql FROM sqlite_master
           WHERE type IN ('table','index','trigger','view')
             AND name NOT LIKE 'sqlite_%'
             AND sql IS NOT NULL
           ORDER BY type,name"""
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), _normalize_schema_sql(row[3]))
        for row in rows
    )


@lru_cache(maxsize=1)
def _expected_schema_contract() -> tuple[tuple[str, str, str, str], ...]:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(SCHEMA_DDL)
        return _schema_contract(con)
    finally:
        con.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed_before_open = self.path.is_file() and self.path.stat().st_size > 0
        self.con = sqlite3.connect(self.path, timeout=5.0)
        self.con.row_factory = sqlite3.Row
        self._savepoint_counter = 0
        try:
            if existed_before_open:
                self._migrate_supported_schema()
                self._require_current_schema()
            self._configure_connection()
            self.init_schema()
        except Exception:
            self.con.close()
            raise

    def _configure_connection(self) -> None:
        # WAL keeps GUI reads responsive while background OCR/optimization jobs
        # and file-import operations update local state. NORMAL preserves crash
        # safety without forcing an fsync for every small local write.
        self.con.execute("PRAGMA journal_mode = WAL")
        self.con.execute("PRAGMA synchronous = NORMAL")
        self.con.execute("PRAGMA busy_timeout = 5000")
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute("PRAGMA temp_store = MEMORY")

    def _migrate_supported_schema(self) -> None:
        """Upgrade the v0.82 database to the offline-only schema.

        The previous schema contained diagnostic-only tables that are no longer
        part of the main application.  This one-way migration drops any tables
        outside the current strict schema without reading or interpreting their
        contents, then advances the schema version.
        """
        try:
            schema_id_row = self.con.execute(
                "SELECT value FROM meta WHERE key='schema_id'"
            ).fetchone()
            version_row = self.con.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.Error:
            return
        schema_id = str(schema_id_row[0]) if schema_id_row is not None else ""
        try:
            version = int(version_row[0]) if version_row is not None else -1
        except (TypeError, ValueError):
            return
        if schema_id != SCHEMA_ID or version != 1:
            return
        tables = {
            str(row[0])
            for row in self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        extras = sorted(tables - set(SCHEMA_TABLE_COLUMNS))
        self.con.execute("BEGIN IMMEDIATE")
        try:
            for table in extras:
                quoted = table.replace('"', '""')
                self.con.execute(f'DROP TABLE IF EXISTS "{quoted}"')
            self.con.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    def _require_current_schema(self) -> None:
        """Require an exact match with the current database schema contract."""
        tables = {
            str(row[0])
            for row in self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not tables:
            return
        if "meta" not in tables:
            raise SchemaMismatchError(
                "현재 DB 스키마를 확인할 수 없습니다. 이 버전에서 생성한 새 DB를 사용하세요."
            )
        try:
            schema_id_row = self.con.execute(
                "SELECT value FROM meta WHERE key='schema_id'"
            ).fetchone()
            version_row = self.con.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise SchemaMismatchError("DB 스키마 정보를 읽을 수 없습니다. 새 DB로 시작하세요.") from exc
        schema_id = str(schema_id_row[0]) if schema_id_row is not None else ""
        try:
            version = int(version_row[0]) if version_row is not None else -1
        except (TypeError, ValueError):
            version = -1
        if schema_id != SCHEMA_ID or version != SCHEMA_VERSION:
            raise SchemaMismatchError(
                f"지원하지 않는 DB 스키마입니다: {schema_id or 'unidentified'} / v{version}. "
                f"현재 기준은 {SCHEMA_ID} / v{SCHEMA_VERSION}이며 다른 스키마는 열지 않습니다."
            )
        expected_tables = set(SCHEMA_TABLE_COLUMNS)
        if tables != expected_tables:
            missing = sorted(expected_tables - tables)
            extra = sorted(tables - expected_tables)
            detail = []
            if missing:
                detail.append("누락=" + ",".join(missing))
            if extra:
                detail.append("추가=" + ",".join(extra))
            raise SchemaMismatchError("현재 DB 테이블 구성이 기준 스키마와 다릅니다: " + "; ".join(detail))
        for table, expected in SCHEMA_TABLE_COLUMNS.items():
            actual = tuple(
                str(row[1]) for row in self.con.execute(f'PRAGMA table_info("{table}")').fetchall()
            )
            if actual != expected:
                raise SchemaMismatchError(
                    f"현재 DB 컬럼 구성이 기준 스키마와 다릅니다: {table}: "
                    f"expected={expected!r}, actual={actual!r}"
                )
        actual_contract = _schema_contract(self.con)
        expected_contract = _expected_schema_contract()
        if actual_contract != expected_contract:
            actual_by_name = {(kind, name): sql for kind, name, _table, sql in actual_contract}
            expected_by_name = {(kind, name): sql for kind, name, _table, sql in expected_contract}
            changed = sorted(
                f"{kind}:{name}"
                for kind, name in set(actual_by_name) | set(expected_by_name)
                if actual_by_name.get((kind, name)) != expected_by_name.get((kind, name))
            )
            detail = ", ".join(changed[:8]) or "unknown"
            raise SchemaMismatchError(
                "현재 DB 제약조건/인덱스 구성이 기준 스키마와 다릅니다: " + detail
            )

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def transaction(self):
        """Atomic write transaction with safe nesting via SQLite SAVEPOINTs."""
        if self.con.in_transaction:
            self._savepoint_counter += 1
            name = f"gfl2_sp_{self._savepoint_counter}"
            self.con.execute(f"SAVEPOINT {name}")
            try:
                yield
                self.con.execute(f"RELEASE SAVEPOINT {name}")
            except Exception:
                self.con.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self.con.execute(f"RELEASE SAVEPOINT {name}")
                raise
            return

        self.con.execute("BEGIN")
        try:
            yield
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    @contextmanager
    def read_transaction(self):
        """Hold one consistent SQLite read snapshot across multiple SELECTs."""
        if self.con.in_transaction:
            yield
            return
        self.con.execute("BEGIN")
        try:
            yield
        finally:
            self.con.rollback()

    def init_schema(self) -> None:
        self.con.executescript(SCHEMA_DDL)
        with self.transaction():
            self.con.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_id',?)",
                (SCHEMA_ID,),
            )
            self.con.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _compact_game_formation_members(members: Any) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for member in members if isinstance(members, list) else []:
            if not isinstance(member, dict):
                continue
            try:
                doll_id = int(member.get("doll_id") or 0)
            except (TypeError, ValueError):
                continue
            if doll_id <= 0:
                continue
            compact.append({
                "doll_id": doll_id,
                "doll_name": member.get("doll_name"),
            })
        return compact

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction():
            self.con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (str(key), str(value)))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (str(key),)).fetchone()
        return str(row[0]) if row is not None else default

    def state_token(self) -> tuple[int, int]:
        """Cheap revision token for both external and local SQLite writes.

        ``PRAGMA data_version`` changes when another connection commits, while
        ``Connection.total_changes`` covers writes made through this Repository.
        Keeping this logic here prevents GUI/services from subtly diverging.
        """
        try:
            row = self.con.execute("PRAGMA data_version").fetchone()
            external = int(row[0]) if row is not None else 0
        except (sqlite3.Error, TypeError, ValueError):
            external = 0
        try:
            local = int(self.con.total_changes)
        except (AttributeError, TypeError, ValueError):
            local = 0
        return external, local

    def _upsert(self, table: str, columns: list[str], key_columns: list[str], rows: Iterable[tuple[Any, ...]]) -> None:
        rows = list(rows)
        if not rows:
            return
        placeholders = ",".join("?" for _ in columns)
        update_columns = [c for c in columns if c not in key_columns]
        conflict = ",".join(key_columns)
        update = ",".join(f"{c}=excluded.{c}" for c in update_columns)
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT({conflict}) DO UPDATE SET {update}"
        with self.transaction():
            self.con.executemany(sql, rows)

    def _replace(self, table: str, columns: list[str], rows: Iterable[tuple[Any, ...]]) -> None:
        placeholders = ",".join("?" for _ in columns)
        with self.transaction():
            self.con.execute(f"DELETE FROM {table}")
            self.con.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", rows)

    def replace_dolls(self, records: list[Doll]) -> None:
        now = utc_now()
        # Illustration/favorite are local user state. Account refreshes replace
        # the owned roster, but must not erase those preferences for dolls that
        # remain in the refreshed dataset.
        local_state = {
            int(row["doll_id"]): (row["illustration_path"], int(row["favorite"] or 0))
            for row in self.con.execute("SELECT doll_id,illustration_path,favorite FROM dolls")
        }
        self._replace(
            "dolls",
            ["doll_id","name","level","rank","illustration_path","favorite","updated_at"],
            ((
                r.doll_id, r.name, r.level, r.rank,
                r.illustration_path or local_state.get(int(r.doll_id), (None, 0))[0],
                local_state.get(int(r.doll_id), (None, 0))[1], now,
            ) for r in records),
        )

    def replace_remoldings(self, records: list[Remolding]) -> None:
        now = utc_now()
        self._replace("remoldings", ["uid","remolding_id","raw_contents_hex","slots_json","updated_at"],
            ((r.uid,r.remolding_id,r.raw_contents_hex,json.dumps([asdict(s) for s in r.slots],ensure_ascii=False),now) for r in records))

    def replace_game_formations(self, records: list[GameFormation]) -> None:
        now = utc_now()
        self._replace(
            "game_formations",
            ["name", "members_json", "updated_at"],
            (
                (
                    r.name,
                    json.dumps(
                        self._compact_game_formation_members(
                            [{"doll_id": m.doll_id, "doll_name": m.doll_name} for m in r.members]
                        ),
                        ensure_ascii=False,
                    ),
                    now,
                )
                for r in records
            ),
        )

    def merge_dolls(self, records: list[Doll]) -> None:
        now = utc_now()
        existing_images = {int(row["doll_id"]): row["illustration_path"] for row in self.con.execute("SELECT doll_id,illustration_path FROM dolls") if row["illustration_path"]}
        self._upsert(
            "dolls", ["doll_id","name","level","rank","illustration_path","updated_at"], ["doll_id"],
            ((r.doll_id,r.name,r.level,r.rank,r.illustration_path or existing_images.get(int(r.doll_id)),now) for r in records),
        )

    def merge_remoldings(self, records: list[Remolding]) -> None:
        now = utc_now()
        self._upsert(
            "remoldings", ["uid","remolding_id","raw_contents_hex","slots_json","updated_at"], ["uid"],
            ((r.uid,r.remolding_id,r.raw_contents_hex,json.dumps([asdict(s) for s in r.slots],ensure_ascii=False),now) for r in records),
        )

    def merge_game_formations(self, records: list[GameFormation]) -> None:
        # Imported formation names are not unique by schema. Replace matching
        # names only so an incremental update cannot erase unrelated layouts.
        now = utc_now()
        with self.transaction():
            for r in records:
                self.con.execute("DELETE FROM game_formations WHERE name=?", (r.name,))
                compact = self._compact_game_formation_members(
                    [{"doll_id": m.doll_id, "doll_name": m.doll_name} for m in r.members]
                )
                self.con.execute(
                    "INSERT INTO game_formations(name,members_json,updated_at) VALUES(?,?,?)",
                    (r.name, json.dumps(compact, ensure_ascii=False), now),
                )

    def remolding_inventory_rows(self) -> list[dict[str, Any]]:
        """Return only columns needed by the inventory/remolding UI.

        ``raw_contents_hex`` can be much larger than the decoded slot metadata;
        pulling it into Python on every inventory refresh wastes memory bandwidth
        without contributing anything to the visible table.
        """
        return [
            dict(row)
            for row in self.con.execute(
                "SELECT uid,remolding_id,slots_json,updated_at FROM remoldings ORDER BY remolding_id,uid"
            )
        ]

    def delete_remolding(self, uid: str) -> bool:
        """Delete one owned remolding and detach local references safely.

        Manual/OCR imports can contain mistakes.  Removing the inventory row
        must not leave a formation pointing at a non-existent UID or a pattern
        slot claiming that UID as its source.  The logical pattern option is
        preserved; only the source link is cleared.
        """
        target = str(uid or "").strip()
        if not target:
            return False
        changed = False
        with self.transaction():
            rows = list(
                self.con.execute(
                    "SELECT plan_id,position,remolding_uids_json FROM formation_members"
                )
            )
            for row in rows:
                try:
                    current = [str(value) for value in json.loads(row["remolding_uids_json"] or "[]")]
                except (TypeError, ValueError, json.JSONDecodeError):
                    current = []
                if target not in current:
                    continue
                updated = [value for value in current if value != target]
                self.con.execute(
                    "UPDATE formation_members SET remolding_uids_json=? WHERE plan_id=? AND position=?",
                    (json.dumps(updated, ensure_ascii=False), int(row["plan_id"]), int(row["position"])),
                )
            self.con.execute(
                "UPDATE remolding_pattern_slots SET source_remolding_uid=NULL WHERE source_remolding_uid=?",
                (target,),
            )
            cur = self.con.execute("DELETE FROM remoldings WHERE uid=?", (target,))
            changed = bool(cur.rowcount)
        return changed

    def remoldings_by_uids(self, uids: Iterable[str]) -> list[dict[str, Any]]:
        """Return remolding rows in caller order with a bounded number of SQL queries.

        UI/result rendering frequently rehydrates the six assigned remoldings.
        The old code issued one SELECT per UID, which was harmless for one card
        but became noticeable when formation/global result panes refreshed many
        characters.  Batch the lookup while preserving input order and duplicates.
        """
        ordered = [str(uid) for uid in uids if str(uid)]
        if not ordered:
            return []
        unique = list(dict.fromkeys(ordered))
        found: dict[str, dict[str, Any]] = {}
        # Stay below SQLite's common 999-parameter limit even for imported data.
        for start in range(0, len(unique), 900):
            chunk = unique[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            for row in self.con.execute(
                f"SELECT uid,remolding_id,slots_json FROM remoldings WHERE uid IN ({placeholders})",
                chunk,
            ):
                found[str(row["uid"])] = dict(row)
        return [dict(found[uid]) for uid in ordered if uid in found]

    def set_doll_favorite(self, doll_id: int, favorite: bool) -> bool:
        """Persist the local favorite flag for an owned doll."""
        with self.transaction():
            cur = self.con.execute(
                "UPDATE dolls SET favorite=? WHERE doll_id=?",
                (1 if favorite else 0, int(doll_id)),
            )
            changed = bool(cur.rowcount)
        return changed

    def is_doll_favorite(self, doll_id: int) -> bool:
        row = self.con.execute("SELECT favorite FROM dolls WHERE doll_id=?", (int(doll_id),)).fetchone()
        return bool(row and int(row[0] or 0))

    def rows(self, table: str, *, order_by: str = "1") -> list[dict[str, Any]]:
        allowed = {
            "dolls","remoldings","game_formations","formation_plans","formation_members",
            "remolding_patterns","remolding_pattern_slots","remolding_score_settings","remolding_option_overrides",
            "remolding_target_profiles","remolding_character_profiles",
        }
        if table not in allowed:
            raise ValueError(f"unknown table: {table}")
        return [dict(r) for r in self.con.execute(f"SELECT * FROM {table} ORDER BY {order_by}")]

    def inventory_summary(self) -> dict[str, int]:
        row = self.con.execute(
            """SELECT
                 (SELECT COUNT(*) FROM dolls) AS dolls,
                 (SELECT COUNT(*) FROM remoldings) AS remoldings,
                 (SELECT COUNT(*) FROM game_formations) AS game_formations
            """
        ).fetchone()
        return {key: int(row[key]) for key in row.keys()} if row is not None else {}

    def exists(self, table: str, column: str, value: Any) -> bool:
        allowed = {"dolls": {"doll_id"}, "remoldings": {"uid"}}
        if column not in allowed.get(table,set()):
            raise ValueError("unsupported lookup")
        return self.con.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (value,)).fetchone() is not None
