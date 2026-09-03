from __future__ import annotations

import json
import uuid
from copy import deepcopy
from collections.abc import Callable
from typing import Any, Iterable

from .. import reference
from ..repository import Repository, utc_now


PHENOMENON_LEVEL_CHECKPOINTS = (0, 10, 20, 30, 45, 60)


class RemoldingRecommendationService:
    """Auditable remolding recommendation service with user-editable scoring.

    The source 리몰딩 추천 reference provides weights, ranks and character tag rules,
    but does not define one authoritative final arithmetic formula. The planner
    therefore stores its scoring policy separately and exposes every component.
    Global score parameters and per-character/per-option adjustments are kept in
    SQLite so one change applies consistently throughout recommendation,
    pattern design and owned-remolding ranking.
    """

    DEFAULT_MULTIPLIERS = {
        "option_weight": 1.0,
        "base_rank": 1.0,
        "tag_rank": 1.0,
    }

    def __init__(self, repo: Repository | None = None):
        self.repo = repo
        self.rules = reference.remolding_rules()
        self.options = reference.remolding_options()
        self.base_characters = reference.remolding_characters()
        self.characters = deepcopy(self.base_characters)
        self._cache_token: tuple[int, int] | None = None
        self._recommendation_cache: dict[tuple[str, str | None, bool], list[dict[str, Any]]] = {}
        self._owned_piece_cache: list[dict[str, Any]] | None = None
        self._owned_score_cache: dict[str, list[dict[str, Any]]] = {}
        # Hot-path configuration is tiny but used by almost every score call.
        # Cache it per SQLite revision instead of issuing the same SELECT while
        # evaluating hundreds of pieces or several allocation candidates.
        self._score_config_cache: dict[str, Any] | None = None
        self._override_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._target_profile_cache: dict[tuple[str, bool], dict[str, dict[str, int]]] = {}
        self._phenomenon_requirements_cache: dict[str, dict[str, dict[str, int]]] = {}
        self._load_character_profiles()

    def _repo_state_token(self) -> tuple[int, int]:
        return self.repo.state_token() if self.repo is not None else (0, 0)

    def _ensure_cache_fresh(self) -> None:
        token = self._repo_state_token()
        if token == self._cache_token:
            return
        self._cache_token = token
        self._recommendation_cache.clear()
        self._owned_piece_cache = None
        self._owned_score_cache.clear()
        self._score_config_cache = None
        self._override_cache.clear()
        self._target_profile_cache.clear()
        self._phenomenon_requirements_cache.clear()

    def _load_character_profiles(self) -> None:
        if self.repo is None:
            return
        rows = self.repo.con.execute(
            "SELECT character_key,display_name,is_dummy,doll_type,element_type,slot_counts_json,tags_json,level_override FROM remolding_character_profiles"
        ).fetchall()
        for row in rows:
            key = str(row["character_key"] or "")
            if not key:
                continue
            try:
                slot_counts = {
                    str(key): max(0, int(value))
                    for key, value in json.loads(row["slot_counts_json"] or "{}").items()
                }
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError):
                slot_counts = {}
            try:
                tags = [
                    str(value)
                    for value in json.loads(row["tags_json"] or "[]")
                    if str(value)
                ]
            except (json.JSONDecodeError, TypeError):
                tags = []
            if key in self.characters:
                char = deepcopy(self.characters[key])
                if row["display_name"]:
                    char["nameKR"] = str(row["display_name"])
                if row["doll_type"]:
                    char["dollType"] = str(row["doll_type"])
                if row["element_type"]:
                    char["elementType"] = str(row["element_type"])
                if tags:
                    char["tags"] = tags
            elif int(row["is_dummy"] or 0):
                char = {
                    "id": 1_000_000 + len(self.characters), "key": key,
                    "nameKR": str(row["display_name"] or "더미 캐릭터"),
                    "dollType": str(row["doll_type"] or "sentinel"),
                    "elementType": str(row["element_type"] or "physical"),
                    "imagoformType": None, "tags": tags or ["단일형", "공격계수"],
                }
            else:
                continue
            if slot_counts:
                primary = str(char.get("dollType") or "")
                slot_total = sum(int(slot_counts.get(factor, 0)) for factor in ("sentinel", "vanguard", "bulwark", "support"))
                if slot_total == 6:
                    factor_order = [primary] + [
                        factor for factor in ("sentinel", "vanguard", "bulwark", "support") if factor != primary
                    ]
                    char["customSlotDistribution"] = [
                        {"factorType": factor, "count": int(slot_counts.get(factor, 0))}
                        for factor in factor_order
                        if factor and int(slot_counts.get(factor, 0)) > 0
                    ]
                    char["slotTypes"] = [x["factorType"] for x in char["customSlotDistribution"]]
            try:
                level_override = int(row["level_override"] or 0)
            except (TypeError, ValueError):
                level_override = 0
            if level_override > 0:
                char["levelOverride"] = max(1, min(60, level_override))
            elif int(row["is_dummy"] or 0):
                char["levelOverride"] = 60
            self.characters[key] = char

    def has_character(self, character_key: str) -> bool:
        return str(character_key) in self.characters

    def list_dummy_characters(self) -> list[dict[str, Any]]:
        if self.repo is None:
            return []
        keys = {str(r[0]) for r in self.repo.con.execute("SELECT character_key FROM remolding_character_profiles WHERE is_dummy=1")}
        return [self.get_character(k) for k in sorted(keys) if k in self.characters]

    def save_character_profile(
        self, character_key: str, *, slot_counts: dict[str, int], display_name: str | None = None,
        doll_type: str | None = None, element_type: str | None = None, tags: list[str] | None = None, is_dummy: bool | None = None,
        level_override: int | None = None,
    ) -> dict[str, Any]:
        if self.repo is None:
            raise RuntimeError("repository is required")
        key = str(character_key)
        base = self.characters.get(key) or self.base_characters.get(key)
        dummy = bool(is_dummy) if is_dummy is not None else key.startswith("dummy_")
        if base is None and not dummy:
            raise ValueError(f"unknown 리몰딩 추천 character: {key}")
        counts = {factor: max(0, min(6, int(slot_counts.get(factor, 0)))) for factor in ("sentinel", "vanguard", "bulwark", "support")}
        total = sum(counts.values())
        if total != 6:
            raise ValueError("리몰딩 장착 칸 수 합계는 정확히 6이어야 합니다.")
        current = base or {}
        name = str(display_name or current.get("nameKR") or "더미 캐릭터").strip() or "더미 캐릭터"
        role = str(doll_type or current.get("dollType") or next((f for f,v in counts.items() if v), "sentinel"))
        element = str(element_type or current.get("elementType") or "physical")
        tag_values = [str(x) for x in (tags if tags is not None else current.get("tags", [])) if str(x)]
        if level_override is None:
            normalized_level = 60 if dummy else None
        else:
            try:
                raw_level = int(level_override)
            except (TypeError, ValueError):
                raw_level = 0
            normalized_level = max(1, min(60, raw_level)) if raw_level > 0 else (60 if dummy else None)
        self.repo.con.execute(
            """INSERT INTO remolding_character_profiles(character_key,display_name,is_dummy,doll_type,element_type,slot_counts_json,tags_json,level_override,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(character_key) DO UPDATE SET
               display_name=excluded.display_name,is_dummy=excluded.is_dummy,doll_type=excluded.doll_type,element_type=excluded.element_type,
               slot_counts_json=excluded.slot_counts_json,tags_json=excluded.tags_json,level_override=excluded.level_override,updated_at=excluded.updated_at""",
            (key, name, int(dummy), role, element, json.dumps(counts, ensure_ascii=False), json.dumps(tag_values, ensure_ascii=False), normalized_level, utc_now()),
        )
        self.repo.con.commit()
        self.characters = deepcopy(self.base_characters)
        self._load_character_profiles()
        return self.get_character(key)

    def set_character_level_override(
        self,
        character_key: str,
        level_override: int | None,
    ) -> dict[str, Any]:
        """Set only the per-character calculation level while preserving profile fields."""
        key = str(character_key)
        character = self.get_character(key)
        distribution = {
            str(row.get("factorType")): int(row.get("count") or 0)
            for row in character.get("slotDistribution", [])
        }
        if sum(distribution.values()) != 6:
            distribution = {
                str(row.get("factorType")): int(row.get("count") or 0)
                for row in character.get("customSlotDistribution", [])
            }
        return self.save_character_profile(
            key,
            slot_counts=distribution,
            display_name=str(character.get("nameKR") or key),
            doll_type=str(character.get("dollType") or "sentinel"),
            element_type=str(character.get("elementType") or "physical"),
            tags=[str(value) for value in character.get("tags", [])],
            is_dummy=key.startswith("dummy_"),
            level_override=level_override,
        )

    def create_dummy_character(
        self, name: str, *, slot_counts: dict[str, int], doll_type: str = "sentinel", element_type: str = "physical", tags: list[str] | None = None,
        level: int = 60,
    ) -> dict[str, Any]:
        key = "dummy_" + uuid.uuid4().hex[:12]
        return self.save_character_profile(
            key, slot_counts=slot_counts, display_name=name, doll_type=doll_type, element_type=element_type,
            tags=tags or ["단일형", "공격계수"], is_dummy=True, level_override=level,
        )

    def delete_dummy_character(self, character_key: str) -> None:
        if self.repo is None:
            return
        key = str(character_key)
        with self.repo.transaction():
            self.repo.con.execute("DELETE FROM remolding_target_profiles WHERE character_key=?", (key,))
            self.repo.con.execute("DELETE FROM remolding_option_overrides WHERE character_key=?", (key,))
            self.repo.con.execute("DELETE FROM remolding_character_profiles WHERE character_key=?", (key,))
        self.characters = deepcopy(self.base_characters)
        self._load_character_profiles()

    def default_score_config(self) -> dict[str, Any]:
        return {
            "grades": {k: int(v) for k, v in self.rules["grades"].items()},
            "multipliers": dict(self.DEFAULT_MULTIPLIERS),
        }

    def get_score_config(self) -> dict[str, Any]:
        self._ensure_cache_fresh()
        if self._score_config_cache is not None:
            return deepcopy(self._score_config_cache)
        config = self.default_score_config()
        if self.repo is not None:
            row = self.repo.con.execute("SELECT config_json FROM remolding_score_settings WHERE id=1").fetchone()
            if row:
                try:
                    saved = json.loads(row["config_json"])
                except (json.JSONDecodeError, TypeError):
                    saved = {}
                grades = saved.get("grades", {}) if isinstance(saved, dict) else {}
                mult = saved.get("multipliers", {}) if isinstance(saved, dict) else {}
                for key in config["grades"]:
                    if key in grades:
                        try:
                            config["grades"][key] = int(grades[key])
                        except (TypeError, ValueError):
                            pass
                for key in config["multipliers"]:
                    if key in mult:
                        try:
                            config["multipliers"][key] = float(mult[key])
                        except (TypeError, ValueError):
                            pass
        self._score_config_cache = deepcopy(config)
        return config

    def save_score_config(self, config: dict[str, Any]) -> dict[str, Any]:
        if self.repo is None:
            raise RuntimeError("repository is required")
        normalized = self.default_score_config()
        for rank in normalized["grades"]:
            normalized["grades"][rank] = int(config.get("grades", {}).get(rank, normalized["grades"][rank]))
        for key in normalized["multipliers"]:
            value = float(config.get("multipliers", {}).get(key, normalized["multipliers"][key]))
            if value < -10 or value > 10:
                raise ValueError(f"배수는 -10~10 범위에서 입력하세요: {key}")
            normalized["multipliers"][key] = value
        self.repo.con.execute(
            "INSERT OR REPLACE INTO remolding_score_settings(id,config_json,updated_at) VALUES(1,?,?)",
            (json.dumps(normalized, ensure_ascii=False), utc_now()),
        )
        self.repo.con.commit()
        return normalized

    def reset_score_config(self) -> dict[str, Any]:
        if self.repo is not None:
            self.repo.con.execute("DELETE FROM remolding_score_settings WHERE id=1")
            self.repo.con.commit()
        return self.default_score_config()

    def get_override(self, character_key: str, option_key: str) -> dict[str, Any]:
        default = {"score_adjustment": 0, "state": "inherit", "note": ""}
        if self.repo is None:
            return default
        row = self.repo.con.execute(
            "SELECT score_adjustment,state,note FROM remolding_option_overrides WHERE character_key=? AND option_key=?",
            (character_key, option_key),
        ).fetchone()
        return {**default, **dict(row)} if row else default

    def list_overrides(self, character_key: str) -> dict[str, dict[str, Any]]:
        self._ensure_cache_fresh()
        key = str(character_key)
        cached = self._override_cache.get(key)
        if cached is not None:
            return {k: dict(v) for k, v in cached.items()}
        if self.repo is None:
            return {}
        result = {
            str(row["option_key"]): {
                "score_adjustment": int(row["score_adjustment"]),
                "state": str(row["state"]),
                "note": str(row["note"] or ""),
            }
            for row in self.repo.con.execute(
                "SELECT option_key,score_adjustment,state,note FROM remolding_option_overrides WHERE character_key=?",
                (key,),
            )
        }
        self._override_cache[key] = result
        return {k: dict(v) for k, v in result.items()}

    def set_override(
        self,
        character_key: str,
        option_key: str,
        *,
        score_adjustment: int = 0,
        state: str = "inherit",
        note: str = "",
    ) -> None:
        if self.repo is None:
            raise RuntimeError("repository is required")
        if character_key not in self.characters:
            raise ValueError(f"unknown 리몰딩 추천 character: {character_key}")
        if option_key not in self.options:
            raise ValueError(f"unknown 리몰딩 추천 option: {option_key}")
        if state not in {"inherit", "exclude"}:
            raise ValueError("state must be inherit or exclude")
        adjustment = int(score_adjustment)
        if adjustment < -10000 or adjustment > 10000:
            raise ValueError("사용자 점수 조정은 -10000~10000 범위에서 입력하세요.")
        if adjustment == 0 and state == "inherit" and not note.strip():
            self.repo.con.execute(
                "DELETE FROM remolding_option_overrides WHERE character_key=? AND option_key=?",
                (character_key, option_key),
            )
        else:
            self.repo.con.execute(
                """INSERT INTO remolding_option_overrides(character_key,option_key,score_adjustment,state,note,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(character_key,option_key) DO UPDATE SET
                     score_adjustment=excluded.score_adjustment,state=excluded.state,note=excluded.note,updated_at=excluded.updated_at""",
                (character_key, option_key, adjustment, state, note.strip(), utc_now()),
            )
        self.repo.con.commit()

    def reset_character_overrides(self, character_key: str) -> None:
        if self.repo is not None:
            self.repo.con.execute("DELETE FROM remolding_option_overrides WHERE character_key=?", (character_key,))
            self.repo.con.commit()

    def list_characters(self) -> list[dict[str, Any]]:
        return sorted(self.characters.values(), key=lambda x: (x["nameKR"], x["id"]))

    def get_character(self, character_key: str) -> dict[str, Any]:
        try:
            c = deepcopy(self.characters[character_key])
        except KeyError as exc:
            raise ValueError(f"unknown 리몰딩 추천 character: {character_key}") from exc
        custom = c.get("customSlotDistribution")
        imago = reference.remolding_imagoforms().get(c.get("imagoformType")) if c.get("imagoformType") else None
        c["imagoform"] = imago
        if custom:
            c["slotDistribution"] = [dict(x) for x in custom]
        elif imago:
            c["slotDistribution"] = [
                {"factorType": factor, "count": count}
                for factor, count in zip(c["slotTypes"], imago["slotCounts"])
            ]
        else:
            c["slotDistribution"] = []
        return c

    def equipped_piece_display(
        self,
        character_key: str,
        pieces: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a six-piece, slot-level formation display.

        This describes the concrete remolding pieces the player should equip,
        rather than collapsing six pieces into aggregate target levels.
        Every colored card corresponds to one physical remolding and lists the
        rolled +1/+2/+3 option contributions on that piece. Missing assignments
        remain visible as placeholders in the character's required family order.
        """
        character = self.get_character(character_key)
        factor_names = self.rules.get("factor_names", {})
        option_index = reference.remolding_code_index()

        required_rows: list[dict[str, Any]] = []
        for source_index, row in enumerate(character.get("slotDistribution", [])):
            factor = str(row.get("factorType") or "")
            count = max(0, int(row.get("count") or 0))
            if factor and count:
                required_rows.append({
                    "factor": factor,
                    "count": count,
                    "source_index": source_index,
                })
        if str(character.get("dollType") or "") == "bulwark":
            order = {"bulwark": 0, "support": 1, "sentinel": 2}
            required_rows.sort(key=lambda row: (order.get(row["factor"], 99), -row["count"], row["source_index"]))
        else:
            required_rows.sort(key=lambda row: (-row["count"], row["source_index"]))

        factor_order = {row["factor"]: index for index, row in enumerate(required_rows)}
        normalized: list[dict[str, Any]] = []
        for source_index, raw_piece in enumerate(pieces or []):
            piece = dict(raw_piece)
            slots = [dict(slot) for slot in list(piece.get("slots") or [])]
            primary_factor = str(piece.get("primary_factor") or "")
            stats: list[dict[str, Any]] = []
            inferred_major = ""
            inferred_any = ""
            for slot_index, slot in enumerate(slots):
                option_key = str(slot.get("option_key") or "")
                if not option_key:
                    option_key = str(option_index.get(str(slot.get("code") or "").lower(), {}).get("option_key") or "")
                option = self.options.get(option_key) or {}
                factor = str(option.get("factorType") or slot.get("factor_type") or "")
                if factor and not inferred_any:
                    inferred_any = factor
                is_major = bool(option.get("isMajor"))
                if is_major and factor and not inferred_major:
                    inferred_major = factor
                name = str(option.get("nameKR") or slot.get("name") or "옵션 미확인")
                level = self.slot_level_contribution(slot)
                stats.append({
                    "option_key": option_key,
                    "name": name,
                    "level": level,
                    "factor": factor,
                    "is_major": is_major,
                    "slot_index": slot_index,
                })
            primary_factor = primary_factor or inferred_major or inferred_any
            stats.sort(key=lambda row: (not bool(row["is_major"]), int(row["slot_index"])))
            normalized.append({
                "uid": str(piece.get("uid") or ""),
                "factor": primary_factor,
                "label": str(factor_names.get(primary_factor, primary_factor or "분류 미확인")),
                "stats": stats,
                "score": float(piece.get("score") or 0),
                "factor_slot": int(piece.get("factor_slot") or 0),
                "source_index": source_index,
                "missing": False,
            })

        buckets: dict[str, list[dict[str, Any]]] = {}
        extras: list[dict[str, Any]] = []
        for row in normalized:
            factor = str(row.get("factor") or "")
            if factor in factor_order:
                buckets.setdefault(factor, []).append(row)
            else:
                extras.append(row)
        for rows in buckets.values():
            rows.sort(key=lambda row: (
                int(row.get("factor_slot") or 999),
                -float(row.get("score") or 0),
                int(row.get("source_index") or 0),
                str(row.get("uid") or ""),
            ))

        display_groups: list[dict[str, Any]] = []
        assigned = 0
        for required in required_rows:
            factor = str(required["factor"])
            wanted = int(required["count"])
            rows = list(buckets.get(factor, []))
            visible: list[dict[str, Any]] = []
            for index in range(wanted):
                if index < len(rows):
                    piece = dict(rows[index])
                    assigned += 1
                else:
                    piece = {
                        "uid": "",
                        "factor": factor,
                        "label": str(factor_names.get(factor, factor)),
                        "stats": [],
                        "score": 0.0,
                        "factor_slot": index + 1,
                        "source_index": index,
                        "missing": True,
                    }
                piece["display_index"] = index + 1
                visible.append(piece)
            for index, piece in enumerate(rows[wanted:], wanted + 1):
                extra = dict(piece)
                extra["display_index"] = index
                visible.append(extra)
                assigned += 1
            display_groups.append({
                "factor": factor,
                "label": str(factor_names.get(factor, factor)),
                "required_count": wanted,
                "pieces": visible,
            })

        if extras:
            extras.sort(key=lambda row: (str(row.get("label") or ""), int(row.get("source_index") or 0)))
            for index, piece in enumerate(extras, 1):
                piece["display_index"] = index
            display_groups.append({
                "factor": "",
                "label": "기타",
                "required_count": 0,
                "pieces": extras,
            })
            assigned += len(extras)

        required_total = sum(int(row["count"]) for row in required_rows)
        return {
            "groups": display_groups,
            "required": required_total,
            "assigned": assigned,
            "missing": max(0, required_total - assigned),
        }

    def phenomenon_requirements(self, character_key: str) -> dict[str, dict[str, int]]:
        """Return exact per-character phenomenon-image factor thresholds.

        The bundled source contains character-specific overrides extracted from
        the supplied gfl2-remold planner snapshot.  Older generic imagoform
        families remain a fallback for future characters whose exact table has
        not yet been added.
        """
        self._ensure_cache_fresh()
        cache_key = str(character_key)
        cached = self._phenomenon_requirements_cache.get(cache_key)
        if cached is not None:
            return {stage: dict(req) for stage, req in cached.items()}
        char = self.get_character(cache_key)
        explicit = char.get("phenomenonStages")
        if isinstance(explicit, dict) and explicit:
            out: dict[str, dict[str, int]] = {}
            for stage in self.rules.get("phenomenon_stage_order", ["배아", "떡잎", "꽃눈", "꽃봉오리", "꽃망울", "꽃"]):
                req = explicit.get(stage)
                if isinstance(req, dict):
                    out[str(stage)] = {str(k): max(0, int(v)) for k, v in req.items() if int(v) > 0}
            if out:
                self._phenomenon_requirements_cache[cache_key] = {stage: dict(req) for stage, req in out.items()}
                return out
        imago = char.get("imagoform")
        if not imago and char.get("slotDistribution"):
            wanted = [int(x.get("count") or 0) for x in char.get("slotDistribution", [])]
            for candidate in reference.remolding_imagoforms().values():
                if [int(x) for x in candidate.get("slotCounts", [])] == wanted:
                    imago = candidate
                    break
        if not imago:
            self._phenomenon_requirements_cache[cache_key] = {}
            return {}
        stages = list(self.rules.get("phenomenon_stage_order", ["배아", "떡잎", "꽃눈", "꽃봉오리", "꽃망울", "꽃"]))
        out: dict[str, dict[str, int]] = {}
        for stage, values in zip(stages, imago.get("values", [])):
            out[str(stage)] = {
                str(factor): int(value)
                for factor, value in zip(char.get("slotTypes", []), values)
                if int(value or 0) > 0
            }
        self._phenomenon_requirements_cache[cache_key] = {stage: dict(req) for stage, req in out.items()}
        return out

    def aggregate_factor_levels(self, pieces: list[dict[str, Any]] | list[str]) -> dict[str, int]:
        """Sum phenomenon-factor levels from every rolled option on six pieces."""
        if self.repo is None and pieces and isinstance(pieces[0], str):
            raise RuntimeError("repository is required when pieces are UID strings")
        rows: list[dict[str, Any]] = []
        if pieces and isinstance(pieces[0], str):
            for row in self.repo.remoldings_by_uids(pieces):  # type: ignore[arg-type]
                try:
                    slots = json.loads(row.get("slots_json") or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    slots = []
                rows.append({"uid": str(row["uid"]), "remolding_id": int(row["remolding_id"]), "slots": slots})
        else:
            rows = [dict(x) for x in pieces]  # type: ignore[arg-type]
        totals = {factor: 0 for factor in ("sentinel", "vanguard", "bulwark", "support")}
        code_index = reference.remolding_code_index()
        for piece in rows:
            for slot in piece.get("slots", []):
                option_key = slot.get("option_key") or code_index.get(str(slot.get("code", "")).lower(), {}).get("option_key")
                option = self.options.get(str(option_key)) if option_key else None
                if not option:
                    continue
                factor = str(option.get("factorType") or "")
                if factor in totals:
                    totals[factor] += self.slot_level_contribution(slot)
        return totals


    def phenomenon_stage_for_level(self, character_level: int) -> str:
        """Return the phenomenon stage corresponding to the planner level checkpoints."""
        level = max(0, min(60, int(character_level or 0)))
        stage_order = list(
            self.rules.get(
                "phenomenon_stage_order",
                ["배아", "떡잎", "꽃눈", "꽃봉오리", "꽃망울", "꽃"],
            )
        )
        requirements = {
            str(key): int(value)
            for key, value in self.rules.get("phenomenon_level_requirements", {}).items()
        }
        selected = stage_order[0] if stage_order else "배아"
        for stage in stage_order:
            if level >= int(requirements.get(stage, 0)):
                selected = str(stage)
            else:
                break
        return selected

    def phenomenon_status(
        self, character_key: str, pieces: list[dict[str, Any]] | list[str], *, character_level: int = 60,
    ) -> dict[str, Any]:
        """Evaluate all six phenomenon stages, including the Lv.60 flower gate."""
        level = max(0, min(60, int(character_level or 0)))
        factor_levels = self.aggregate_factor_levels(pieces)
        requirements = self.phenomenon_requirements(character_key)
        stage_order = list(self.rules.get("phenomenon_stage_order", ["배아", "떡잎", "꽃눈", "꽃봉오리", "꽃망울", "꽃"]))
        level_requirements = {str(k): int(v) for k, v in self.rules.get("phenomenon_level_requirements", {"꽃": 60}).items()}
        stages: list[dict[str, Any]] = []
        highest_active = None
        highest_index = -1
        for idx, stage in enumerate(stage_order):
            req = {str(k): int(v) for k, v in requirements.get(stage, {}).items() if int(v) > 0}
            missing = {factor: max(0, need - int(factor_levels.get(factor, 0))) for factor, need in req.items()}
            factor_met = bool(req) and all(v <= 0 for v in missing.values())
            min_level = int(level_requirements.get(stage, 1))
            level_met = level >= min_level
            active = bool(factor_met and level_met)
            if active:
                highest_active = stage
                highest_index = idx
            stages.append({
                "stage": stage, "requirements": req, "missing": missing, "factor_met": factor_met,
                "min_level": min_level, "level_met": level_met, "active": active, "index": idx,
            })
        desired_stage = self.phenomenon_stage_for_level(level)
        desired = next((x for x in stages if x["stage"] == desired_stage), None)
        flower = next((x for x in stages if x["stage"] == "꽃"), None)
        return {
            "character_key": character_key, "character_level": level, "factor_levels": factor_levels,
            "stages": stages, "highest_active": highest_active, "highest_index": highest_index,
            "desired_stage": desired_stage, "desired": desired, "flower": flower,
        }

    @staticmethod
    def _component(source: str, base_score: int | float, multiplier: float, *, rank: str | None = None) -> dict[str, Any]:
        score = float(base_score) * float(multiplier)
        rounded: int | float = int(score) if score.is_integer() else round(score, 2)
        return {
            "source": source,
            "rank": rank,
            "base_score": base_score,
            "multiplier": multiplier,
            "score": rounded,
        }

    def _tag_components(
        self,
        character: dict[str, Any],
        option_key: str,
        grades: dict[str, int],
        multiplier: float,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        rules = self.rules["tag_rules"]
        for tag in character.get("tags", []):
            rule = rules.get(tag)
            if not rule:
                continue
            for rank, option_keys in rule.get("ranks", {}).items():
                if option_key in option_keys:
                    out.append(self._component(f"캐릭터 태그 · {tag}", grades[rank], multiplier, rank=rank))
        return out

    def _score_option_with_context(
        self, character_key: str, character: dict[str, Any], option_key: str,
        config: dict[str, Any], overrides: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            option = dict(self.options[option_key])
        except KeyError as exc:
            raise ValueError(f"unknown 리몰딩 추천 option: {option_key}") from exc
        grades: dict[str, int] = config["grades"]
        multipliers: dict[str, float] = config["multipliers"]
        override = {"score_adjustment": 0, "state": "inherit", "note": "", **overrides.get(option_key, {})}
        # Only the *major* option determines the physical remolding family.
        # Minor rolls can cross families in real inventory (for example
        # 피와 칼의 전환 on Sentinel/Vanguard/Bulwark pieces), so rejecting a
        # minor option merely because its reference factor is not in slotTypes
        # incorrectly discards some of the strongest real combinations.
        eligible = (not bool(option.get("isMajor"))) or option["factorType"] in character["slotTypes"]
        reason = "" if eligible else "이 캐릭터가 장착할 수 없는 주옵션 계열"
        option_element = option.get("elementType")
        if eligible and option_element and option_element != character["elementType"]:
            eligible = False
            reason = "캐릭터 속성과 옵션 속성이 일치하지 않음"
        if eligible and override["state"] == "exclude":
            eligible = False
            reason = "사용자가 이 캐릭터 추천에서 제외함"
        components: list[dict[str, Any]] = [
            self._component("옵션 기본 가중치", int(option.get("weight", 0)), multipliers["option_weight"])
        ]
        base_rank = option.get("baseRank")
        if base_rank:
            components.append(self._component("옵션 기본 등급", grades[base_rank], multipliers["base_rank"], rank=base_rank))
        components.extend(self._tag_components(character, option_key, grades, multipliers["tag_rank"]))
        adjustment = int(override["score_adjustment"])
        if adjustment:
            components.append(self._component("캐릭터별 사용자 조정", adjustment, 1.0))
        total_value = sum(float(c["score"]) for c in components) if eligible else -10_000
        total: int | float = int(total_value) if float(total_value).is_integer() else round(total_value, 2)
        return {
            "characterKey": character_key, "optionKey": option_key, "name": option["nameKR"],
            "factorType": option["factorType"], "elementType": option.get("elementType"),
            "isMajor": bool(option.get("isMajor")), "maxLevel": option["maxLevel"], "values": option["values"],
            "description": option.get("description"), "nickname": option.get("nickname"),
            "eligible": eligible, "ineligibleReason": reason, "score": total, "components": components,
            "override": override, "scoreConfig": config,
        }

    def score_option(self, character_key: str, option_key: str) -> dict[str, Any]:
        character = self.get_character(character_key)
        config = self.get_score_config()
        overrides = self.list_overrides(character_key)
        return self._score_option_with_context(character_key, character, option_key, config, overrides)

    def score_options(self, character_key: str, option_keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Score several options with one config/override lookup."""
        character = self.get_character(character_key)
        config = self.get_score_config()
        overrides = self.list_overrides(character_key)
        out: dict[str, dict[str, Any]] = {}
        for option_key in dict.fromkeys(str(key) for key in option_keys):
            if option_key not in self.options:
                continue
            out[option_key] = self._score_option_with_context(character_key, character, option_key, config, overrides)
        return out

    def recommendations(
        self, character_key: str, factor_type: str | None = None, *, include_ineligible: bool = False,
    ) -> list[dict[str, Any]]:
        self._ensure_cache_fresh()
        cache_key = (str(character_key), str(factor_type) if factor_type is not None else None, bool(include_ineligible))
        cached = self._recommendation_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        character = self.get_character(character_key)
        factors = {factor_type} if factor_type else set(character["slotTypes"])
        config = self.get_score_config()
        overrides = self.list_overrides(character_key)
        rows: list[dict[str, Any]] = []
        for option_key, option in self.options.items():
            # With no explicit factor filter, include cross-family minor rolls.
            # With an explicit filter, the user is intentionally browsing that
            # option family, so keep the familiar factor-only view.
            if factor_type:
                if option["factorType"] not in factors:
                    continue
            elif bool(option.get("isMajor")) and option["factorType"] not in factors:
                continue
            scored = self._score_option_with_context(character_key, character, option_key, config, overrides)
            if scored["eligible"] or include_ineligible:
                rows.append(scored)
        result = sorted(rows, key=lambda x: (not x["eligible"], -float(x["score"]), x["factorType"], x["name"]))
        self._recommendation_cache[cache_key] = result
        return list(result)

    def score_remolding_pieces(
        self, character_key: str, pieces: list[dict[str, Any]], *, sort_results: bool = True,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Score already-decoded remolding pieces without re-reading SQLite.

        Global allocation evaluates the same inventory for many characters.  The
        former path reparsed every ``slots_json`` row once per character.  This
        helper keeps scoring semantics identical while letting callers decode the
        physical inventory once and reuse it across all character evaluations.
        """
        index = reference.remolding_code_index()
        character = self.get_character(character_key)
        config = self.get_score_config()
        overrides = self.list_overrides(character_key)
        # Score option definitions lazily as they are encountered in the physical
        # inventory.  The reference table contains options that may not appear in
        # the player's current remoldings; eager scoring multiplied that unused
        # work by every character during global allocation.
        option_scores: dict[str, dict[str, Any]] = {}
        out: list[dict[str, Any]] = []
        for piece_index, source in enumerate(pieces):
            if should_cancel is not None and piece_index % 16 == 0 and should_cancel():
                break
            row = dict(source)
            slots = row.get("slots")
            if slots is None:
                try:
                    slots = json.loads(row.get("slots_json") or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    slots = []
            slots = list(slots or [])
            slot_results: list[dict[str, Any]] = []
            total = 0.0
            eligible_count = 0
            for slot in slots:
                slot = dict(slot)
                option_key = slot.get("option_key") or index.get(str(slot.get("code", "")).lower(), {}).get("option_key")
                if not option_key or str(option_key) not in self.options:
                    slot_results.append({**slot, "score": 0, "eligible": False, "reason": "미확인 코드"})
                    continue
                option_key = str(option_key)
                scored = option_scores.get(option_key)
                if scored is None:
                    scored = self._score_option_with_context(character_key, character, option_key, config, overrides)
                    option_scores[option_key] = scored
                visible_score = float(scored["score"]) if scored["eligible"] else 0.0
                total += visible_score
                eligible_count += int(scored["eligible"])
                slot_results.append({
                    **slot, "option_key": str(option_key),
                    "score": int(visible_score) if visible_score.is_integer() else round(visible_score, 2),
                    "eligible": scored["eligible"], "reason": scored["ineligibleReason"],
                    "components": scored.get("components", []), "override": scored.get("override", {}),
                })
            final = int(total) if total.is_integer() else round(total, 2)
            result = {
                "uid": str(row.get("uid") or ""),
                "remolding_id": int(row.get("remolding_id") or 0),
                "score": final, "eligible_slots": eligible_count, "slots": slot_results,
            }
            # Preserve precomputed physical metadata used by the allocator.
            for key in ("primary_factor", "level_contributions", "factor_contributions"):
                if key in row:
                    result[key] = row[key]
            out.append(result)
        if sort_results:
            return sorted(out, key=lambda x: (-float(x["score"]), -x["eligible_slots"], x["uid"]))
        return out

    def owned_remolding_pieces(self) -> list[dict[str, Any]]:
        """Return the decoded physical remolding inventory for this DB revision.

        Qt and batch allocators can reuse this immutable-by-convention snapshot
        across several character score jobs instead of reparsing ``slots_json``
        once per character.  ``score_remolding_pieces`` copies every source row
        before decorating it with scores, so callers may safely share the snapshot
        between read-only worker jobs.
        """
        if self.repo is None:
            raise RuntimeError("repository is required to load owned remoldings")
        self._ensure_cache_fresh()
        if self._owned_piece_cache is None:
            pieces: list[dict[str, Any]] = []
            for row in self.repo.con.execute("SELECT uid,remolding_id,slots_json FROM remoldings ORDER BY uid"):
                try:
                    slots = json.loads(row["slots_json"] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    slots = []
                pieces.append({"uid": str(row["uid"]), "remolding_id": int(row["remolding_id"]), "slots": slots})
            self._owned_piece_cache = pieces
        return list(self._owned_piece_cache)

    def score_owned_remoldings(self, character_key: str) -> list[dict[str, Any]]:
        if self.repo is None:
            raise RuntimeError("repository is required to score owned remoldings")
        self._ensure_cache_fresh()
        key = str(character_key)
        cached = self._owned_score_cache.get(key)
        if cached is not None:
            return list(cached)
        result = self.score_remolding_pieces(key, self.owned_remolding_pieces())
        self._owned_score_cache[key] = result
        return list(result)

    @staticmethod
    def slot_level_contribution(slot: dict[str, Any]) -> int:
        """Return the rolled option's +1/+2/+3 level contribution."""
        value = slot.get("level_contribution")
        if value is None:
            value = slot.get("variant")
        try:
            return max(0, min(3, int(value or 0)))
        except (TypeError, ValueError):
            return 0

    def aggregate_option_levels(self, pieces: list[dict[str, Any]] | list[str]) -> dict[str, dict[str, Any]]:
        """Sum logical option levels across equipped remolding pieces.

        The reference WebUI treats code1/code2/code3 as +1/+2/+3 and sums the
        same logical option across all six pieces. Display level is capped at the
        option's configured maxLevel while raw/overcap are preserved.
        """
        if self.repo is None and pieces and isinstance(pieces[0], str):
            raise RuntimeError("repository is required when pieces are UID strings")
        rows: list[dict[str, Any]] = []
        if pieces and isinstance(pieces[0], str):
            for row in self.repo.remoldings_by_uids(pieces):  # type: ignore[arg-type]
                try:
                    slots = json.loads(row.get("slots_json") or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    slots = []
                rows.append({"uid": str(row["uid"]), "remolding_id": int(row["remolding_id"]), "slots": slots})
        else:
            rows = [dict(x) for x in pieces]  # type: ignore[arg-type]

        aggregate: dict[str, dict[str, Any]] = {}
        index = reference.remolding_code_index()
        for piece in rows:
            for slot in piece.get("slots", []):
                option_key = slot.get("option_key") or index.get(str(slot.get("code", "")).lower(), {}).get("option_key")
                option = self.options.get(str(option_key)) if option_key else None
                if not option:
                    continue
                contribution = self.slot_level_contribution(slot)
                if contribution <= 0:
                    continue
                entry = aggregate.setdefault(str(option_key), {
                    "option_key": str(option_key),
                    "name": option.get("nameKR") or str(option_key),
                    "factor_type": option.get("factorType"),
                    "max_level": int(option.get("maxLevel") or 0),
                    "raw_level": 0,
                    "display_level": 0,
                    "overcap": 0,
                    "value": None,
                    "pieces": [],
                })
                entry["raw_level"] += contribution
                entry["pieces"].append({
                    "uid": str(piece.get("uid") or ""),
                    "code": slot.get("code"),
                    "contribution": contribution,
                })
        for entry in aggregate.values():
            max_level = max(0, int(entry["max_level"] or 0))
            raw_level = max(0, int(entry["raw_level"] or 0))
            display = min(raw_level, max_level) if max_level else raw_level
            entry["display_level"] = display
            entry["overcap"] = max(0, raw_level - display)
            values = self.options[entry["option_key"]].get("values", [])
            if display > 0 and display <= len(values):
                entry["value"] = values[display - 1]
        return aggregate

    @staticmethod
    def normalize_target_spec(requested: Any, max_level: int = 0, default_weight: int = 100) -> dict[str, int] | None:
        """Validate the current remolding target shape.

        Targets are JSON objects with ``level`` and optional ``weight`` /
        ``priority``. Historical scalar and renamed-key forms are intentionally
        rejected by the current target-profile contract.
        """
        if not isinstance(requested, dict):
            return None
        try:
            level = int(requested.get("level") or 0)
            weight = int(requested.get("weight", default_weight) or 0)
            priority = int(requested.get("priority", 1) or 1)
        except (TypeError, ValueError):
            return None
        if level <= 0:
            return None
        priority = max(1, min(priority, 99))
        weight = max(0, min(weight, 10000))
        if max_level:
            level = min(level, int(max_level))
        return {"level": max(1, level), "weight": weight, "priority": priority}


    def default_target_profile(self, character_key: str) -> dict[str, dict[str, int]]:
        """Gameplay-policy defaults that avoid blindly maxing every option."""
        char = self.get_character(character_key)
        role = str(char.get("dollType") or "")
        tags = set(char.get("tags") or [])
        counts = {str(x.get("factorType")): int(x.get("count") or 0) for x in char.get("slotDistribution", [])}
        out: dict[str, dict[str, int]] = {}

        def put(key: str, level: int, weight: int, priority: int) -> None:
            opt = self.options.get(key)
            if not opt:
                return
            scored = self.score_option(character_key, key)
            if not scored.get("eligible"):
                return
            level = max(1, min(int(level), int(opt.get("maxLevel") or level)))
            existing = out.get(key)
            spec = {"level": level, "weight": max(0, int(weight)), "priority": max(1, int(priority))}
            if existing is None or (spec["priority"], -spec["weight"]) < (existing["priority"], -existing["weight"]):
                out[key] = spec

        shape_single = "단일형" in tags
        shape_area = "광역형" in tags
        mixed = "혼합형" in tags or (shape_single and shape_area)

        # Sentinel/Vanguard damage dealers: attack buff is the exception that
        # should be maxed; other majors deliberately stop around Lv.3.
        if role in {"sentinel", "vanguard"}:
            if counts.get("sentinel", 0):
                put("sentinel_1", 6, 1200, 1)  # attack buff: always max
                if counts.get("sentinel", 0) >= 4:
                    put("sentinel_2", 3, 520, 2)
                if mixed:
                    put("sentinel_3", 2, 360, 3)
                    put("sentinel_4", 2, 360, 3)
                elif shape_area:
                    put("sentinel_4", 3, 560, 2)
                else:
                    put("sentinel_3", 3, 560, 2)
            if counts.get("vanguard", 0):
                put("vanguard_1", 3, 680, 2)  # crit damage: next multiplicative bucket
                if counts.get("vanguard", 0) >= 2:
                    put("vanguard_2", 3, 480, 3)
                if mixed:
                    put("vanguard_3", 2, 360, 3)
                    put("vanguard_4", 2, 360, 3)
                elif shape_area:
                    put("vanguard_4", 3, 560, 2)
                else:
                    put("vanguard_3", 3, 560, 2)
            if "공격계수" in tags:
                put("support_5", 5, 520 if character_key != "klukai" else 820, 4 if character_key != "klukai" else 2)

        if role == "support":
            # Team attack buff sits in the smallest additive bucket and is the
            # first mandatory major target.
            put("support_1", 6, 1200, 1)
            put("support_4", 6, 820, 2)
            if counts.get("bulwark", 0):
                put("bulwark_1", 4, 720, 2)
            if "체력계수" in tags or "전환형체력계수" in tags:
                put("support_6", 5, 900, 2)
            else:
                put("support_5", 5, 900, 2)

        if role == "bulwark":
            if counts.get("bulwark", 0):
                put("bulwark_1", 6, 1050, 1)
            # Real pieces can roll this minor option on any major family.
            put("support_5", 5, 1100, 1)

        # Non-support attack scalers still value at least some initial-attack
        # conversion once their mandatory primary goals are satisfied.
        if role not in {"support", "bulwark", "sentinel", "vanguard"} and "공격계수" in tags:
            put("support_5", 5, 450, 4)

        return out

    def get_target_profile(self, character_key: str, *, with_default: bool = True) -> dict[str, dict[str, int]]:
        if character_key not in self.characters:
            raise ValueError(f"unknown 리몰딩 추천 character: {character_key}")
        self._ensure_cache_fresh()
        cache_key = (str(character_key), bool(with_default))
        cached = self._target_profile_cache.get(cache_key)
        if cached is not None:
            return {k: dict(v) for k, v in cached.items()}
        if self.repo is None:
            result = self.default_target_profile(character_key) if with_default else {}
            self._target_profile_cache[cache_key] = {k: dict(v) for k, v in result.items()}
            return result
        row = self.repo.con.execute(
            "SELECT targets_json,explicit_empty FROM remolding_target_profiles WHERE character_key=?",
            (character_key,),
        ).fetchone()
        if row is None:
            result = self.default_target_profile(character_key) if with_default else {}
            self._target_profile_cache[cache_key] = {k: dict(v) for k, v in result.items()}
            return result
        try:
            raw = json.loads(row["targets_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            raw = {}
        normalized: dict[str, dict[str, int]] = {}
        for option_key, requested in (raw or {}).items():
            option = self.options.get(str(option_key))
            if not option:
                continue
            spec = self.normalize_target_spec(requested, int(option.get("maxLevel") or 0), int(option.get("weight") or 100))
            if spec:
                normalized[str(option_key)] = spec
        # Presence of a row is meaningful: an explicitly empty profile means
        # the user deliberately disabled every target and must not be replaced
        # by defaults on the next launch.
        if normalized or bool(row["explicit_empty"]):
            result = normalized
        else:
            result = self.default_target_profile(character_key) if with_default else {}
        self._target_profile_cache[cache_key] = {k: dict(v) for k, v in result.items()}
        return result

    def _normalize_target_profile(
        self, character_key: str, targets: dict[str, Any]
    ) -> dict[str, dict[str, int]]:
        if character_key not in self.characters:
            raise ValueError(f"unknown 리몰딩 추천 character: {character_key}")
        if not isinstance(targets, dict):
            raise ValueError(f"target profile must be an object: {character_key}")
        normalized: dict[str, dict[str, int]] = {}
        for option_key, requested in targets.items():
            option = self.options.get(str(option_key))
            if not option:
                raise ValueError(f"unknown 리몰딩 추천 option for {character_key}: {option_key}")
            scored = self.score_option(character_key, str(option_key))
            if not scored.get("eligible"):
                raise ValueError(
                    f"ineligible 리몰딩 추천 option for {character_key}: {option_key}"
                )
            spec = self.normalize_target_spec(
                requested,
                int(option.get("maxLevel") or 0),
                int(option.get("weight") or 100),
            )
            if not spec:
                raise ValueError(
                    f"invalid 리몰딩 추천 target specification for {character_key}: {option_key}"
                )
            normalized[str(option_key)] = spec
        return normalized

    def _write_target_profile(
        self, character_key: str, normalized: dict[str, dict[str, int]]
    ) -> None:
        if self.repo is None:
            raise RuntimeError("repository is required")
        self.repo.con.execute(
            """INSERT INTO remolding_target_profiles(character_key,targets_json,explicit_empty,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(character_key) DO UPDATE SET targets_json=excluded.targets_json,
                 explicit_empty=excluded.explicit_empty,updated_at=excluded.updated_at""",
            (
                character_key,
                json.dumps(normalized, ensure_ascii=False),
                int(not normalized),
                utc_now(),
            ),
        )

    def normalize_target_profile(
        self, character_key: str, targets: dict[str, Any]
    ) -> dict[str, dict[str, int]]:
        """Normalize one current recommendation target profile without persisting it."""
        return self._normalize_target_profile(character_key, targets)

    def save_target_profile(
        self, character_key: str, targets: dict[str, Any]
    ) -> dict[str, dict[str, int]]:
        if self.repo is None:
            raise RuntimeError("repository is required")
        normalized = self._normalize_target_profile(character_key, targets)
        with self.repo.transaction():
            self._write_target_profile(character_key, normalized)
        self._target_profile_cache.clear()
        return normalized

    def save_target_profiles(
        self, profiles: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, dict[str, int]]]:
        """Persist several current target profiles as one atomic settings update."""
        if self.repo is None:
            raise RuntimeError("repository is required")
        if not isinstance(profiles, dict):
            raise ValueError("target profiles must be an object")
        normalized = {
            str(character_key): self._normalize_target_profile(
                str(character_key), targets
            )
            for character_key, targets in profiles.items()
        }
        with self.repo.transaction():
            for character_key, targets in normalized.items():
                self._write_target_profile(character_key, targets)
        self._target_profile_cache.clear()
        return normalized

    def target_status(self, pieces: list[dict[str, Any]] | list[str], targets: dict[str, Any] | None) -> list[dict[str, Any]]:
        levels = self.aggregate_option_levels(pieces)
        rows: list[dict[str, Any]] = []
        for option_key, requested in (targets or {}).items():
            option = self.options.get(str(option_key))
            if not option:
                continue
            max_level = int(option.get("maxLevel") or 0)
            spec = self.normalize_target_spec(requested, max_level, int(option.get("weight") or 100))
            if not spec:
                continue
            target = int(spec["level"])
            priority = int(spec["priority"])
            current = levels.get(str(option_key), {})
            display = int(current.get("display_level") or 0)
            rows.append({
                "option_key": str(option_key),
                "name": option.get("nameKR") or str(option_key),
                "factor_type": option.get("factorType"),
                "target_level": target,
                "priority": priority,
                "display_level": display,
                "raw_level": int(current.get("raw_level") or 0),
                "max_level": max_level,
                "met": display >= target,
                "value": current.get("value"),
            })
        return sorted(rows, key=lambda x: (int(x.get("priority") or 1), str(x.get("name") or ""), str(x.get("option_key") or "")))
