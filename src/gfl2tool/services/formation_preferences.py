from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_json

SCHEMA_ID = "gfl2-formation-member-preferences"
SCHEMA_VERSION = 1
MAX_TURNS = 64
MAX_ACTION_LENGTH = 240


def _clean_actions(value: object) -> list[str]:
    rows = value if isinstance(value, list) else []
    out = [str(item or "").strip()[:MAX_ACTION_LENGTH] for item in rows[:MAX_TURNS]]
    while out and not out[-1]:
        out.pop()
    return out


class FormationMemberPreferenceStore:
    """Formation-local display and skill-cycle overrides.

    Preferences deliberately live outside the strict SQLite schema. They are
    user presentation/planning choices, not captured game state, and are backed
    up with the other user-side JSON files.
    """

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "formation_member_preferences.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "plans": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict) or raw.get("schema_id") != SCHEMA_ID or int(raw.get("schema_version") or 0) != SCHEMA_VERSION:
            return {"schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "plans": {}}
        plans = raw.get("plans") if isinstance(raw.get("plans"), dict) else {}
        return {"schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "plans": dict(plans)}

    def save(self, payload: dict[str, Any]) -> Path:
        clean = {
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "plans": dict(payload.get("plans") or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write_json(self.path, clean, ensure_ascii=False, indent=2)

    @staticmethod
    def _keys(plan_id: int, position: int) -> tuple[str, str]:
        return str(int(plan_id)), str(int(position))

    def member(self, plan_id: int, position: int, doll_id: int | None = None) -> dict[str, Any]:
        payload = self.load()
        plan_key, pos_key = self._keys(plan_id, position)
        raw = dict((payload.get("plans") or {}).get(plan_key, {}).get(pos_key, {}) or {})
        if doll_id is not None and int(raw.get("doll_id") or 0) != int(doll_id):
            return {}
        return raw

    def update_member(self, plan_id: int, position: int, doll_id: int, **changes: object) -> Path:
        payload = self.load()
        plans = payload.setdefault("plans", {})
        plan_key, pos_key = self._keys(plan_id, position)
        plan = plans.setdefault(plan_key, {})
        current = dict(plan.get(pos_key) or {})
        if int(current.get("doll_id") or 0) != int(doll_id):
            current = {"doll_id": int(doll_id)}
        for key, value in changes.items():
            if key == "skill_cycle":
                value = _clean_actions(value)
            if value in (None, "", []):
                current.pop(key, None)
            else:
                current[key] = value
        current["doll_id"] = int(doll_id)
        if set(current) == {"doll_id"}:
            plan.pop(pos_key, None)
        else:
            plan[pos_key] = current
        if not plan:
            plans.pop(plan_key, None)
        return self.save(payload)

    def clear_member(self, plan_id: int, position: int) -> Path:
        payload = self.load()
        plans = payload.get("plans") or {}
        plan_key, pos_key = self._keys(plan_id, position)
        plan = plans.get(plan_key)
        if isinstance(plan, dict):
            plan.pop(pos_key, None)
            if not plan:
                plans.pop(plan_key, None)
        return self.save(payload)

    def skill_actions(self, plan_id: int, position: int, doll_id: int) -> list[str]:
        return _clean_actions(self.member(plan_id, position, doll_id).get("skill_cycle"))

    def set_skill_actions(self, plan_id: int, position: int, doll_id: int, actions: list[str]) -> Path:
        return self.update_member(plan_id, position, doll_id, skill_cycle=actions)


class FormationSkillCycleAdapter:
    """Adapter exposing the DollSkillCycleDialog store interface for one slot."""

    def __init__(
        self, store: FormationMemberPreferenceStore, plan_id: int, position: int, doll_id: int,
        *, fallback_store=None,
    ):
        self.store = store
        self.plan_id = int(plan_id)
        self.position = int(position)
        self.doll_id = int(doll_id)
        self.fallback_store = fallback_store

    def actions_for(self, _doll_id: int | None) -> list[str]:
        local = self.store.skill_actions(self.plan_id, self.position, self.doll_id)
        if local:
            return local
        if self.fallback_store is not None:
            return list(self.fallback_store.actions_for(self.doll_id))
        return []

    def set_actions(self, _doll_id: int, actions: list[str]) -> Path:
        return self.store.set_skill_actions(self.plan_id, self.position, self.doll_id, actions)
