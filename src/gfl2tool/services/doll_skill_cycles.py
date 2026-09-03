from __future__ import annotations

import json
from pathlib import Path

from ..atomic_io import atomic_write_json

SCHEMA_ID = "gfl2-doll-skill-cycles"
SCHEMA_VERSION = 2
MAX_TURNS = 64
MAX_ACTION_LENGTH = 240


def _normalize_actions(value: object) -> list[str]:
    rows = value if isinstance(value, list) else []
    out = [str(item or "").strip()[:MAX_ACTION_LENGTH] for item in rows[:MAX_TURNS]]
    while out and not out[-1]:
        out.pop()
    return out


def _migrate_v1_profiles(raw_profiles: object) -> dict[int, list[str]]:
    """Collapse the old per-breakthrough profiles into one cycle per Doll.

    The old format could contain multiple profiles for one Doll.  The new UI no
    longer distinguishes breakthrough ranks, so preserve the richest profile:
    choose the longest non-empty sequence, breaking ties by lower rank.
    """
    if not isinstance(raw_profiles, dict):
        return {}
    out: dict[int, list[str]] = {}
    for raw_doll_id, raw_profile in raw_profiles.items():
        try:
            doll_id = int(raw_doll_id)
        except (TypeError, ValueError):
            continue
        if doll_id <= 0 or not isinstance(raw_profile, dict):
            continue
        ranks = raw_profile.get("ranks")
        if not isinstance(ranks, dict):
            continue
        candidates: list[tuple[int, int, list[str]]] = []
        for raw_rank, raw_actions in ranks.items():
            actions = _normalize_actions(raw_actions)
            if not actions:
                continue
            try:
                rank = int(raw_rank)
            except (TypeError, ValueError):
                rank = 99
            candidates.append((-len(actions), rank, actions))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            out[doll_id] = candidates[0][2]
    return out


class DollSkillCycleStore:
    """User-defined per-Doll T1..Tn skill-cycle presets."""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "doll_skill_cycles.json"

    def load(self) -> dict[int, list[str]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict) or raw.get("schema_id") != SCHEMA_ID:
            return {}
        version = int(raw.get("schema_version") or 0)
        profiles = raw.get("profiles")
        if version == 1:
            return _migrate_v1_profiles(profiles)
        if version != SCHEMA_VERSION or not isinstance(profiles, dict):
            return {}
        out: dict[int, list[str]] = {}
        for raw_doll_id, raw_actions in profiles.items():
            try:
                doll_id = int(raw_doll_id)
            except (TypeError, ValueError):
                continue
            actions = _normalize_actions(raw_actions)
            if doll_id > 0 and actions:
                out[doll_id] = actions
        return out

    def save(self, profiles: dict[int, list[str]]) -> Path:
        normalized: dict[str, list[str]] = {}
        for raw_doll_id, raw_actions in profiles.items():
            try:
                doll_id = int(raw_doll_id)
            except (TypeError, ValueError):
                continue
            actions = _normalize_actions(raw_actions)
            if doll_id > 0 and actions:
                normalized[str(doll_id)] = actions
        payload = {
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "profiles": normalized,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write_json(self.path, payload, ensure_ascii=False, indent=2)

    def actions_for(self, doll_id: int | None) -> list[str]:
        if doll_id is None:
            return []
        try:
            did = int(doll_id)
        except (TypeError, ValueError):
            return []
        return list(self.load().get(did, []))

    def set_actions(self, doll_id: int, actions: list[str]) -> Path:
        profiles = self.load()
        did = int(doll_id)
        normalized = _normalize_actions(actions)
        if normalized:
            profiles[did] = normalized
        else:
            profiles.pop(did, None)
        return self.save(profiles)


def apply_skill_cycles_to_tactic(tactic, store: DollSkillCycleStore) -> bool:
    """Fill blank/previously-auto cycle fields from the tactic roster presets.

    Manual cycle text is never overwritten. Missing T steps are appended up to
    the longest applicable Doll profile. Breakthrough rank is intentionally not
    part of this mapping: each Doll owns one user-authored repeating sequence.
    """
    from ..tactics import MAX_STEPS, TacticStep

    profiles = store.load()
    by_unit: list[tuple[object, list[str]]] = []
    max_turns = 0
    for unit in tactic.units:
        if unit.doll_id is None:
            continue
        actions = list(profiles.get(int(unit.doll_id), []))
        if not actions:
            continue
        by_unit.append((unit, actions))
        max_turns = max(max_turns, len(actions))
    max_turns = min(MAX_STEPS, max_turns)
    changed = False
    if max_turns <= 0:
        for step in tactic.steps:
            if step.cycle_auto and (step.cycle or step.cycle_auto):
                step.cycle = ""
                step.cycle_auto = False
                changed = True
        return changed

    while len(tactic.steps) < max_turns:
        index = len(tactic.steps)
        previous = tactic.steps[-1] if tactic.steps else None
        tactic.steps.append(
            TacticStep(
                name=f"T{index + 1}",
                rows=int(previous.rows or tactic.rows) if previous is not None else tactic.rows,
                cols=int(previous.cols or tactic.cols) if previous is not None else tactic.cols,
            )
        )
        changed = True

    for index, step in enumerate(tactic.steps):
        fragments: list[str] = []
        if index < max_turns:
            for unit, actions in by_unit:
                action = actions[index].strip() if index < len(actions) else ""
                if not action:
                    continue
                label = unit.display_label().strip() or "?"
                fragments.append(action if action.startswith(label) else f"{label} {action}")
        composed = " · ".join(fragments)[:2000]
        if step.cycle_auto or not step.cycle.strip():
            if step.cycle != composed or step.cycle_auto != bool(composed):
                step.cycle = composed
                step.cycle_auto = bool(composed)
                changed = True
    return changed
