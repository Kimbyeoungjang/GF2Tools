from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .. import reference
from ..repository import Repository
from .dolls import DollCharacterResolver
from .remolding_recommendation import RemoldingRecommendationService

# The current planner owns one physical equipment domain: remoldings.
FORMATION_AUTO_CATEGORIES = frozenset({"remolding"})


def _hungarian_max(weights: list[list[float]], should_cancel: Callable[[], bool] | None = None) -> list[int | None]:
    """Maximum-weight rectangular assignment. Returns column per row.

    Dummy zero-score columns are appended so every row can remain unassigned.
    Invalid choices should use a large negative score.
    """
    if not weights:
        return []
    if should_cancel and should_cancel():
        raise InterruptedError("작업이 취소되었습니다.")
    n = len(weights)
    real_m = max((len(r) for r in weights), default=0)
    m = max(real_m + n, n)
    padded = [list(r) + [-1e12] * (real_m - len(r)) + [0.0] * n for r in weights]
    max_w = max(0.0, max(max(r) for r in padded))
    # Hungarian implementation for min-cost assignment, n <= m.
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        if should_cancel and should_cancel():
            raise InterruptedError("작업이 취소되었습니다.")
        p[0] = i
        j0 = 0
        minv = [math.inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            if should_cancel and should_cancel():
                raise InterruptedError("작업이 취소되었습니다.")
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cost = max_w - padded[i0 - 1][j - 1]
                cur = cost - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    ans: list[int | None] = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            row = p[j] - 1
            col = j - 1
            if col < real_m and padded[row][col] > 0:
                ans[row] = col
    return ans


def _piece_factor(remolding_id: int, slots: list[dict[str, Any]]) -> str | None:
    # Standard families are stable in imported inventory. Unknown/special IDs
    # fall back to the dominant factor of their rolled options.
    rid = int(remolding_id)
    prefix4 = str(rid)[:4]
    standard = {
        "9841": "bulwark", "9851": "bulwark",
        "9842": "vanguard", "9852": "vanguard",
        "9843": "support", "9853": "support",
        "9844": "sentinel", "9854": "sentinel",
    }
    if prefix4 in standard:
        return standard[prefix4]
    # Unknown/special IDs still expose the physical family through their major
    # rolled option. Minor rolls can cross families, so never infer the piece
    # family from a majority of minor-option factors when a major is known.
    options = reference.remolding_options()
    code_index = reference.remolding_code_index()
    for slot in slots:
        option_key = str(slot.get("option_key") or "")
        if not option_key:
            option_key = str(code_index.get(str(slot.get("code") or "").lower(), {}).get("option_key") or "")
        option = options.get(option_key) or {}
        if bool(option.get("isMajor")):
            factor = str(option.get("factorType") or slot.get("factor_type") or "")
            if factor:
                return factor
    factors = [s.get("factor_type") for s in slots if s.get("factor_type")]
    if not factors:
        return None
    counts = Counter(factors)
    best_count = max(counts.values())
    tied = {k for k, v in counts.items() if v == best_count}
    return next((x for x in factors if x in tied), None)


@dataclass(slots=True)
class OptimizedMember:
    doll_id: int
    remolding_uids: list[str]
    scores: dict[str, float]
    notes: dict[str, str]
    remolding_target_status: list[dict[str, Any]]


_REMOLDING_FACTORS = ("sentinel", "vanguard", "bulwark", "support")


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise InterruptedError("작업이 취소되었습니다.")


class _RemoldingAllocationPlanner:
    """Internal staged planner for global physical-remolding allocation.

    The public contract remains :meth:`EquipmentOptimizer.allocate_remoldings`.
    This class deliberately separates input normalization, inventory decoding,
    assignment search, saturation repair and result rendering so policy changes
    can be tested one stage at a time without changing the external API.
    """

    def __init__(
        self,
        optimizer: "EquipmentOptimizer",
        character_keys: Iterable[str],
        *,
        priority_factors: dict[str, set[str]] | None,
        priority_multiplier: float,
        targets_by_character: dict[str, dict[str, Any]] | None,
        priority_character_keys: set[str] | None,
        character_level_override: int | None,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        self.optimizer = optimizer
        self.repo = optimizer.repo
        self.recommendation = optimizer.recommendation
        self.should_cancel = should_cancel
        self.keys = [str(k) for k in character_keys if self.recommendation.has_character(str(k))]
        self.characters = {key: self.recommendation.get_character(key) for key in self.keys}
        self.priority_character_keys = {
            str(k) for k in (priority_character_keys or set()) if str(k) in self.keys
        }
        self.priority_factors = {str(k): set(v) for k, v in (priority_factors or {}).items()}
        self.priority_multiplier = max(1.0, min(float(priority_multiplier), 10.0))
        self.raw_targets = {str(k): dict(v) for k, v in (targets_by_character or {}).items()}
        self.character_level_override = (
            max(0, min(60, int(character_level_override)))
            if character_level_override is not None
            else None
        )

        self.targets: dict[str, dict[str, dict[str, int]]] = {}
        self.priorities: list[int] = []
        self.importance: dict[int, float] = {}
        self.piece_rows: list[dict[str, Any]] = []
        self.pieces_by_factor: dict[str, list[dict[str, Any]]] = {factor: [] for factor in _REMOLDING_FACTORS}
        self.character_levels: dict[str, int] = {}
        self.phenomenon_goal_stage: dict[str, str] = {}
        self.phenomenon_goal: dict[str, dict[str, int]] = {}
        self.score_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def run(self) -> dict[str, Any]:
        self._prepare_targets()
        self._prepare_inventory()
        self._prepare_phenomenon_goals()
        self._prepare_scores()
        allocations = self._search_best_allocation()
        allocations = self._saturation_rebalance(allocations)
        return self._build_result(allocations)

    def _prepare_targets(self) -> None:
        targets: dict[str, dict[str, dict[str, int]]] = {}
        for key in self.keys:
            _raise_if_cancelled(self.should_cancel)
            normalized: dict[str, dict[str, int]] = {}
            requested_targets = self.raw_targets.get(key, {})
            scored_targets = self.recommendation.score_options(key, requested_targets)
            for option_key, requested in requested_targets.items():
                option_key = str(option_key)
                option = self.recommendation.options.get(option_key)
                if not option:
                    continue
                scored = scored_targets.get(option_key)
                if not scored or not scored.get("eligible"):
                    continue
                spec = self.recommendation.normalize_target_spec(
                    requested,
                    int(option.get("maxLevel") or 0),
                    int(option.get("weight") or 100),
                )
                if spec:
                    normalized[option_key] = spec
            targets[key] = normalized
        self.targets = targets

        priority_capacity: Counter[int] = Counter()
        for char_targets in targets.values():
            for spec in char_targets.values():
                priority_capacity[int(spec["priority"])] += int(spec["level"])
        self.priorities = sorted(priority_capacity)
        integer_importance: dict[int, int] = {}
        radix = 1
        for priority in reversed(self.priorities):
            integer_importance[priority] = radix
            radix *= max(2, int(priority_capacity[priority]) + 1)
        importance: dict[int, float] = {}
        if integer_importance:
            max_log = max(math.log(max(1, value)) for value in integer_importance.values())
            for priority, value in integer_importance.items():
                exponent = max(-250.0, math.log(max(1, value)) - max_log)
                importance[priority] = math.exp(exponent)
        self.importance = importance

    def _prepare_inventory(self) -> None:
        piece_rows: list[dict[str, Any]] = []
        code_index = reference.remolding_code_index()
        for row in self.repo.con.execute("SELECT uid,remolding_id,slots_json FROM remoldings"):
            try:
                slots = json.loads(row["slots_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                slots = []
            factor = _piece_factor(int(row["remolding_id"]), slots)
            contrib: dict[str, int] = defaultdict(int)
            factor_contrib: dict[str, int] = defaultdict(int)
            for slot in slots:
                option_key = slot.get("option_key")
                if not option_key:
                    option_key = code_index.get(str(slot.get("code", "")).lower(), {}).get("option_key")
                if option_key:
                    level = self.recommendation.slot_level_contribution(slot)
                    contrib[str(option_key)] += level
                    option = self.recommendation.options.get(str(option_key)) or {}
                    option_factor = str(option.get("factorType") or "")
                    if option_factor in _REMOLDING_FACTORS:
                        factor_contrib[option_factor] += level
            piece_rows.append({
                "uid": str(row["uid"]),
                "remolding_id": int(row["remolding_id"]),
                "slots": slots,
                "primary_factor": factor,
                "level_contributions": dict(contrib),
                "factor_contributions": dict(factor_contrib),
            })
        self.piece_rows = piece_rows
        pieces_by_factor = {factor: [] for factor in _REMOLDING_FACTORS}
        for piece in piece_rows:
            factor = str(piece.get("primary_factor") or "")
            if factor in pieces_by_factor:
                pieces_by_factor[factor].append(piece)
        self.pieces_by_factor = pieces_by_factor

    def _prepare_phenomenon_goals(self) -> None:
        self.character_levels = {
            key: self.optimizer.calculation_level_for_key(
                key, self.character_level_override
            )
            for key in self.keys
        }
        phenomenon_requirements = {
            key: self.recommendation.phenomenon_requirements(key) for key in self.keys
        }
        for key in self.keys:
            stage = self.recommendation.phenomenon_stage_for_level(self.character_levels[key])
            reqs = phenomenon_requirements.get(key, {})
            if stage not in reqs and reqs:
                stage = next(reversed(reqs))
            self.phenomenon_goal_stage[key] = stage
            self.phenomenon_goal[key] = {
                str(k): int(v) for k, v in reqs.get(stage, {}).items() if int(v) > 0
            }

    def _prepare_scores(self) -> None:
        score_cache: dict[str, dict[str, dict[str, Any]]] = {}
        for key in self.keys:
            _raise_if_cancelled(self.should_cancel)
            scored_rows = self.recommendation.score_remolding_pieces(
                key,
                self.piece_rows,
                sort_results=False,
                should_cancel=self.should_cancel,
            )
            score_cache[key] = {str(r["uid"]): r for r in scored_rows}
        self.score_cache = score_cache

    def _evaluate_resource(
        self,
        node: tuple[str, int],
        resource: dict[str, Any],
        factor: str,
        goal_weights: dict[str, dict[str, float]],
        phenomenon_weights: dict[str, dict[str, float]],
        *,
        explain: bool,
    ) -> float | tuple[float, str]:
        key, _slot = node
        scored = self.score_cache[key].get(resource["uid"])
        if not scored:
            return (-1e12, "점수 없음") if explain else -1e12
        planner = float(scored["score"])
        objective = planner
        notes: list[str] | None = ["리몰딩 추천 캐릭터별 점수"] if explain else None

        pheno_goal = self.phenomenon_goal.get(key, {})
        if pheno_goal:
            pheno_score = 0.0
            pheno_labels: list[str] | None = [] if explain else None
            for factor_key, target_level in pheno_goal.items():
                contribution = int(resource.get("factor_contributions", {}).get(factor_key, 0))
                if contribution <= 0:
                    continue
                dynamic = float(phenomenon_weights.get(key, {}).get(factor_key, 1.0))
                useful = min(contribution, max(1, int(target_level)))
                pheno_score += useful * dynamic
                if pheno_labels is not None:
                    pheno_labels.append(f"{factor_key} +{useful}")
            objective += pheno_score * 1e12
            if notes is not None and pheno_labels:
                notes.append(
                    f"현상 {self.phenomenon_goal_stage.get(key)} 기여 " + ", ".join(pheno_labels)
                )

        char_targets = self.targets.get(key, {})
        if char_targets:
            goal = 0.0
            labels: list[str] | None = [] if explain else None
            for option_key, spec in char_targets.items():
                contribution = int(resource["level_contributions"].get(option_key, 0))
                if contribution <= 0:
                    continue
                target = int(spec["level"])
                priority = int(spec["priority"])
                dynamic = float(goal_weights.get(key, {}).get(option_key, 1.0))
                group_weight = float(self.importance.get(priority, 1.0))
                user_weight = max(0.0, float(spec.get("weight", 100))) / 100.0
                useful = min(contribution, target)
                goal += useful * dynamic * group_weight * user_weight
                if labels is not None:
                    labels.append(
                        f"{option_key} +{useful} · 가중치 {int(spec.get('weight', 100))}"
                    )
            objective += goal * 1e3
            if notes is not None and labels:
                notes.append("목표 기여 " + ", ".join(labels[:4]))
        elif factor in self.priority_factors.get(key, set()) and planner > 0:
            objective = planner * self.priority_multiplier
            if notes is not None:
                notes.append(f"{factor} 우선 배분 ×{self.priority_multiplier:g}")

        if key in self.priority_character_keys and objective > 0:
            objective += 1e-3
            if notes is not None:
                notes.append("즐겨찾기 우선")
        if explain:
            return objective, " · ".join(notes or [])
        return objective

    def _run_assignment(
        self,
        goal_weights: dict[str, dict[str, float]],
        phenomenon_weights: dict[str, dict[str, float]],
    ) -> dict[str, list[dict[str, Any]]]:
        allocations: dict[str, list[dict[str, Any]]] = {k: [] for k in self.keys}
        for factor in _REMOLDING_FACTORS:
            _raise_if_cancelled(self.should_cancel)
            nodes: list[tuple[str, int]] = []
            for key in self.keys:
                char = self.characters[key]
                count = next(
                    (
                        int(x["count"])
                        for x in char.get("slotDistribution", [])
                        if x["factorType"] == factor
                    ),
                    0,
                )
                nodes.extend((key, slot) for slot in range(count))
            resources = self.pieces_by_factor.get(factor, [])
            if not nodes or not resources:
                continue

            chosen, meta = self.optimizer._assign_unique(
                nodes,
                resources,
                lambda node, resource: self._evaluate_resource(
                    node, resource, factor, goal_weights, phenomenon_weights, explain=False
                ),
                allow_nonpositive=True,
                detail_scorer=lambda node, resource: self._evaluate_resource(
                    node, resource, factor, goal_weights, phenomenon_weights, explain=True
                ),
                should_cancel=self.should_cancel,
            )
            for i, resource in chosen.items():
                key, slot = nodes[i]
                scored = self.score_cache[key][resource["uid"]]
                objective_score, objective_note = meta[i]
                allocations[key].append({
                    **resource,
                    "score": scored["score"],
                    "score_detail": scored,
                    "factor_slot": slot + 1,
                    "priority_applied": factor in self.priority_factors.get(key, set()),
                    "allocation_objective": objective_score,
                    "allocation_note": objective_note,
                })
        return allocations

    def _status_summaries(
        self, allocations: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        pheno_status_by_key = {
            key: self.recommendation.phenomenon_status(
                key, allocations[key], character_level=self.character_levels[key]
            )
            for key in self.keys
        }
        target_status_by_key = {
            key: self.recommendation.target_status(allocations[key], self.targets.get(key, {}))
            for key in self.keys
        }
        return pheno_status_by_key, target_status_by_key

    def _allocation_metric(
        self,
        allocations: dict[str, list[dict[str, Any]]],
        pheno_status_by_key: dict[str, dict[str, Any]],
        target_status_by_key: dict[str, list[dict[str, Any]]],
    ) -> tuple[Any, ...]:
        pheno_desired_met = 0
        pheno_stage_sum = 0
        pheno_progress = 0
        for key in self.keys:
            status = pheno_status_by_key[key]
            desired = status.get("desired") or {}
            pheno_desired_met += int(bool(desired.get("active")))
            pheno_stage_sum += int(status.get("highest_index", -1)) + 1
            factor_levels = status.get("factor_levels") or {}
            for factor_key, need in self.phenomenon_goal.get(key, {}).items():
                pheno_progress += min(int(factor_levels.get(factor_key, 0)), int(need))

        priority_metric: list[int] = []
        for priority in self.priorities:
            met = 0
            capped = 0
            for key in self.keys:
                for row in target_status_by_key[key]:
                    if int(row.get("priority") or 1) != priority:
                        continue
                    met += int(bool(row["met"]))
                    capped += min(int(row["display_level"]), int(row["target_level"]))
            priority_metric.extend([met, capped])

        favorite_pheno_met = 0
        favorite_stage_sum = 0
        favorite_progress = 0
        favorite_target_metric: list[int] = []
        if self.priority_character_keys:
            for key in self.keys:
                if key not in self.priority_character_keys:
                    continue
                status = pheno_status_by_key[key]
                desired = status.get("desired") or {}
                favorite_pheno_met += int(bool(desired.get("active")))
                favorite_stage_sum += int(status.get("highest_index", -1)) + 1
                factor_levels = status.get("factor_levels") or {}
                for factor_key, need in self.phenomenon_goal.get(key, {}).items():
                    favorite_progress += min(int(factor_levels.get(factor_key, 0)), int(need))
            for priority in self.priorities:
                met = 0
                capped = 0
                for key in self.priority_character_keys:
                    if key not in allocations:
                        continue
                    for row in target_status_by_key[key]:
                        if int(row.get("priority") or 1) != priority:
                            continue
                        met += int(bool(row["met"]))
                        capped += min(int(row["display_level"]), int(row["target_level"]))
                favorite_target_metric.extend([met, capped])

        planner_total = sum(
            sum(float(x["score"]) for x in allocations[key]) for key in self.keys
        )
        return (
            pheno_desired_met,
            pheno_stage_sum,
            pheno_progress,
            *priority_metric,
            favorite_pheno_met,
            favorite_stage_sum,
            favorite_progress,
            *favorite_target_metric,
            planner_total,
        )

    def _update_dynamic_weights(
        self,
        weights: dict[str, dict[str, float]],
        pheno_weights: dict[str, dict[str, float]],
        pheno_status_by_key: dict[str, dict[str, Any]],
        target_status_by_key: dict[str, list[dict[str, Any]]],
    ) -> None:
        for key in self.keys:
            status = pheno_status_by_key[key]
            factor_levels = status.get("factor_levels") or {}
            for factor_key, target in self.phenomenon_goal.get(key, {}).items():
                target = max(1, int(target))
                achieved = int(factor_levels.get(factor_key, 0))
                current = pheno_weights[key].get(factor_key, 1.0)
                if achieved < target:
                    current *= 1.0 + (target - achieved) / target
                elif achieved > target:
                    current *= max(0.25, target / max(1, achieved))
                else:
                    current *= 0.80
                pheno_weights[key][factor_key] = max(0.05, min(current, 256.0))
            for row in target_status_by_key[key]:
                target = max(1, int(row["target_level"]))
                achieved = int(row["display_level"])
                current = weights[key].get(row["option_key"], 1.0)
                if achieved < target:
                    current *= 1.0 + (target - achieved) / target
                elif achieved > target:
                    current *= max(0.30, target / achieved)
                else:
                    current *= 0.82
                weights[key][row["option_key"]] = max(0.05, min(current, 128.0))

    def _search_best_allocation(self) -> dict[str, list[dict[str, Any]]]:
        weights: dict[str, dict[str, float]] = {
            k: {o: 1.0 for o in self.targets.get(k, {})} for k in self.keys
        }
        pheno_weights: dict[str, dict[str, float]] = {
            key: {factor: 1.0 for factor in self.phenomenon_goal.get(key, {})}
            for key in self.keys
        }
        best_alloc: dict[str, list[dict[str, Any]]] | None = None
        best_metric: tuple[Any, ...] | None = None
        rounds = 16 if any(self.targets.values()) or any(self.phenomenon_goal.values()) else 1
        for _round in range(rounds):
            _raise_if_cancelled(self.should_cancel)
            allocations = self._run_assignment(weights, pheno_weights)
            pheno_status, target_status = self._status_summaries(allocations)
            metric = self._allocation_metric(allocations, pheno_status, target_status)
            if best_metric is None or metric > best_metric:
                best_metric, best_alloc = metric, allocations
            self._update_dynamic_weights(weights, pheno_weights, pheno_status, target_status)
        return best_alloc or {k: [] for k in self.keys}

    def _local_metric(
        self,
        char_key: str,
        pieces: list[dict[str, Any]],
        cache: dict[tuple[str, tuple[str, ...]], tuple[Any, ...]],
    ) -> tuple[Any, ...]:
        signature = (
            str(char_key),
            tuple(sorted(str(x.get("uid") or "") for x in pieces)),
        )
        cached_metric = cache.get(signature)
        if cached_metric is not None:
            return cached_metric
        pheno = self.recommendation.phenomenon_status(
            char_key,
            pieces,
            character_level=self.character_levels[char_key],
        )
        desired = pheno.get("desired") or {}
        factor_levels = pheno.get("factor_levels") or {}
        pheno_capped = sum(
            min(int(factor_levels.get(factor_key, 0)), int(need))
            for factor_key, need in self.phenomenon_goal.get(char_key, {}).items()
        )
        status = self.recommendation.target_status(pieces, self.targets.get(char_key, {}))
        local_prios = sorted({int(x.get("priority") or 1) for x in status})
        parts: list[Any] = [
            int(bool(desired.get("active"))),
            int(pheno.get("highest_index", -1)) + 1,
            pheno_capped,
        ]
        for priority in local_prios:
            rows = [x for x in status if int(x.get("priority") or 1) == priority]
            met = sum(1 for x in rows if x.get("met"))
            capped = sum(
                min(int(x.get("display_level") or 0), int(x.get("target_level") or 0))
                for x in rows
            )
            excess = sum(
                max(0, int(x.get("display_level") or 0) - int(x.get("target_level") or 0))
                for x in rows
            )
            parts.extend((met, capped, -excess))
        planner = sum(float(x.get("score") or 0) for x in pieces)
        parts.append(planner)
        result = tuple(parts)
        cache[signature] = result
        return result

    def _scored_resource(
        self, char_key: str, resource: dict[str, Any], factor_slot: int
    ) -> dict[str, Any] | None:
        scored = self.score_cache.get(char_key, {}).get(str(resource.get("uid") or ""))
        if not scored:
            return None
        return {
            **resource,
            "score": scored["score"],
            "score_detail": scored,
            "factor_slot": factor_slot,
            "priority_applied": False,
            "allocation_objective": float(scored["score"]),
            "allocation_note": "목표 포화도 재조정",
        }

    def _saturation_rebalance(
        self, allocations: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        if not any(self.targets.values()) and not any(self.phenomenon_goal.values()):
            return allocations

        owner: dict[str, str] = {}
        for owner_key, pieces in allocations.items():
            for piece in pieces:
                owner[str(piece.get("uid") or "")] = owner_key
        metric_cache: dict[tuple[str, tuple[str, ...]], tuple[Any, ...]] = {}

        for _pass in range(2):
            _raise_if_cancelled(self.should_cancel)
            changed = False
            for char_key in self.keys:
                _raise_if_cancelled(self.should_cancel)
                if not self.targets.get(char_key) and not self.phenomenon_goal.get(char_key):
                    continue
                current = list(allocations.get(char_key, []))
                if not current:
                    continue
                for factor in _REMOLDING_FACTORS:
                    indexes = [
                        i for i, piece in enumerate(current)
                        if piece.get("primary_factor") == factor
                    ]
                    if not indexes:
                        continue
                    candidates = [
                        resource
                        for resource in self.pieces_by_factor.get(factor, [])
                        if (
                            not owner.get(str(resource.get("uid") or ""))
                            or owner.get(str(resource.get("uid") or "")) == char_key
                        )
                        and str(resource.get("uid") or "") in self.score_cache.get(char_key, {})
                    ]
                    base_metric = self._local_metric(char_key, current, metric_cache)
                    improved = True
                    while improved:
                        improved = False
                        selected_uids = {str(x.get("uid") or "") for x in current}
                        best_trial = None
                        best_metric = base_metric
                        best_old_uid = best_new_uid = None
                        for factor_slot_index, idx in enumerate(indexes, 1):
                            old = current[idx]
                            slot = int(old.get("factor_slot") or factor_slot_index)
                            for resource in candidates:
                                uid = str(resource.get("uid") or "")
                                if (
                                    not uid
                                    or uid == str(old.get("uid") or "")
                                    or uid in selected_uids
                                ):
                                    continue
                                replacement = self._scored_resource(char_key, resource, slot)
                                if replacement is None:
                                    continue
                                trial = list(current)
                                trial[idx] = replacement
                                trial_metric = self._local_metric(char_key, trial, metric_cache)
                                if trial_metric > best_metric:
                                    best_metric = trial_metric
                                    best_trial = trial
                                    best_old_uid = str(old.get("uid") or "")
                                    best_new_uid = uid
                        if best_trial is not None:
                            if best_old_uid:
                                owner.pop(best_old_uid, None)
                            if best_new_uid:
                                owner[best_new_uid] = char_key
                            current = best_trial
                            base_metric = best_metric
                            improved = changed = True
                    allocations[char_key] = current
            if not changed:
                break
        return allocations

    def _build_result(
        self, allocations: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        total = 0.0
        shortage_count = 0
        result_rows = []
        for key in self.keys:
            _raise_if_cancelled(self.should_cancel)
            char = self.characters[key]
            need = sum(int(x["count"]) for x in char.get("slotDistribution", []))
            pieces = sorted(
                allocations[key],
                key=lambda x: (str(x["primary_factor"]), int(x["factor_slot"])),
            )
            score = sum(float(x["score"]) for x in pieces)
            total += score
            missing = max(0, need - len(pieces))
            shortage_count += missing
            target_status = self.recommendation.target_status(pieces, self.targets.get(key, {}))
            aggregate_levels = self.recommendation.aggregate_option_levels(pieces)
            character_level = int(self.character_levels.get(key, 60))
            phenomenon_status = self.recommendation.phenomenon_status(
                key, pieces, character_level=character_level
            )
            result_rows.append({
                "character_key": key,
                "character": char,
                "pieces": pieces,
                "score": score,
                "missing": missing,
                "character_level": character_level,
                "factor_levels": phenomenon_status.get("factor_levels", {}),
                "phenomenon_status": phenomenon_status,
                "desired_phenomenon_stage": phenomenon_status.get("desired_stage"),
                "priority_factors": sorted(self.priority_factors.get(key, set())),
                "targets": self.targets.get(key, {}),
                "target_status": target_status,
                "aggregate_levels": aggregate_levels,
                "targets_met": sum(1 for x in target_status if x["met"]),
                "priority_summary": {
                    str(priority): {
                        "total": sum(
                            1
                            for x in target_status
                            if int(x.get("priority") or 1) == priority
                        ),
                        "met": sum(
                            1
                            for x in target_status
                            if int(x.get("priority") or 1) == priority and x.get("met")
                        ),
                    }
                    for priority in self.priorities
                },
            })
        return {
            "rows": result_rows,
            "total_score": total,
            "missing_slots": shortage_count,
            "characters": len(self.keys),
            "priority_multiplier": self.priority_multiplier,
            "targets_by_character": self.targets,
            "target_priorities": self.priorities,
        }


class EquipmentOptimizer:
    """Formation-wide unique allocator for remolding patterns.

    The planner assigns only owned remoldings. Remolding UIDs are unique only
    inside the currently optimized formation; separate plans may reuse them.
    """

    def __init__(self, repo: Repository):
        self.repo = repo
        self.master: dict[str, Any] = {}
        self.recommendation = RemoldingRecommendationService(repo)
        self._owned_doll_rows = {
            int(row["doll_id"]): dict(row)
            for row in self.repo.con.execute(
                "SELECT doll_id,name,level,favorite FROM dolls"
            )
        }
        self._doll_resolver = DollCharacterResolver(
            repo,
            recommendation=self.recommendation,
            owned_doll_rows=self._owned_doll_rows,
            master=self.master,
            master_loaded=True,
        )

    def character_key_for_doll(self, doll_id: int) -> str | None:
        return self._doll_resolver.character_key_for_doll(int(doll_id))

    def favorite_character_keys(self) -> set[str]:
        return self._doll_resolver.favorite_character_keys()

    def character_level_for_key(self, character_key: str) -> int:
        return self._doll_resolver.character_level_for_key(str(character_key))

    def calculation_level_for_key(
        self, character_key: str, global_override: int | None = None
    ) -> int:
        return self._doll_resolver.calculation_level_for_key(
            str(character_key), global_override
        )

    def _assign_unique(
        self, nodes: list[Any], resources: list[dict[str, Any]], scorer, *,
        allow_nonpositive: bool = False, detail_scorer=None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, tuple[float, str]]]:
        """Assign unique resources while avoiding unused explanation work.

        ``scorer`` may return either a numeric score or ``(score, reason)``.
        Expensive optimizers can pass a numeric scorer plus ``detail_scorer``;
        detailed text is then generated only for the final selected pairs.
        """
        if not nodes or not resources:
            return {}, {}
        matrix: list[list[float]] = []
        basis: dict[tuple[int, int], str] = {}
        for i, node in enumerate(nodes):
            if should_cancel and should_cancel():
                raise InterruptedError("작업이 취소되었습니다.")
            row_weights: list[float] = []
            for j, resource in enumerate(resources):
                value = scorer(node, resource)
                if isinstance(value, tuple):
                    score, why = value
                    if detail_scorer is None:
                        basis[(i, j)] = str(why)
                else:
                    score = value
                row_weights.append(float(score))
            matrix.append(row_weights)
        if allow_nonpositive:
            finite = [x for row in matrix for x in row if x > -1e11]
            floor = (min(finite) - max(1.0, abs(min(finite)) * 0.05)) if finite else -1.0
            # Shift every valid resource score above zero while keeping the same
            # ordering. Dummy columns remain zero, so a real piece is preferred
            # whenever any eligible remoldings exist.
            shifted = [[(x - floor + 1.0) if x > -1e11 else -1e12 for x in row] for row in matrix]
            assignment = _hungarian_max(shifted, should_cancel)
        else:
            assignment = _hungarian_max(matrix, should_cancel)
        chosen: dict[int, dict[str, Any]] = {}
        meta: dict[int, tuple[float, str]] = {}
        for i, col in enumerate(assignment):
            if col is None:
                continue
            score = matrix[i][col]
            if (not allow_nonpositive and score <= 0) or score <= -1e11:
                continue
            chosen[i] = resources[col]
            if detail_scorer is not None:
                detail = detail_scorer(nodes[i], resources[col])
                why = str(detail[1]) if isinstance(detail, tuple) else ""
            else:
                why = basis.get((i, col), "")
            meta[i] = (score, why)
        return chosen, meta

    def best_remolding_set(
        self, character_key: str, *, excluded_uids: set[str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        char = self.recommendation.get_character(character_key)
        excluded_uids = excluded_uids or set()
        rows = self.recommendation.score_owned_remoldings(character_key)
        by_factor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if should_cancel and should_cancel():
                raise InterruptedError("작업이 취소되었습니다.")
            if str(row["uid"]) in excluded_uids:
                continue
            factor = _piece_factor(int(row["remolding_id"]), row["slots"])
            if factor:
                row = dict(row)
                row["primary_factor"] = factor
                by_factor[factor].append(row)
        selected: list[dict[str, Any]] = []
        shortages: list[str] = []
        for req in char.get("slotDistribution", []):
            factor, count = req["factorType"], int(req["count"])
            candidates = sorted(by_factor.get(factor, []), key=lambda r: (-float(r["score"]), str(r["uid"])))
            take = candidates[:count]
            selected.extend(take)
            if len(take) < count:
                shortages.append(f"{reference.remolding_rules()['factor_names'].get(factor, factor)} {count-len(take)}개 부족")
        return {
            "character_key": character_key,
            "character": char,
            "pieces": selected,
            "total_score": sum(float(x["score"]) for x in selected),
            "shortages": shortages,
            "aggregate_levels": self.recommendation.aggregate_option_levels(selected),
        }

    def allocate_remoldings(
        self,
        character_keys: Iterable[str],
        *,
        priority_factors: dict[str, set[str]] | None = None,
        priority_multiplier: float = 2.0,
        targets_by_character: dict[str, dict[str, Any]] | None = None,
        priority_character_keys: set[str] | None = None,
        character_level_override: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Allocate physical remolding pieces uniquely across characters.

        Targets are unlimited and can be grouped by priority. Each target uses
        the current ``{"level": 6, "weight": 100, "priority": 1}`` shape.
        Priority 1 remains lexicographically more important than lower groups;
        favorites are only a soft tie-break after phenomenon/target correctness.
        """
        return _RemoldingAllocationPlanner(
            self,
            character_keys,
            priority_factors=priority_factors,
            priority_multiplier=priority_multiplier,
            targets_by_character=targets_by_character,
            priority_character_keys=priority_character_keys,
            character_level_override=character_level_override,
            should_cancel=should_cancel,
        ).run()

    def apply_formation_result(self, plan_id: int, result: dict[str, Any]) -> dict[str, Any]:
        """Apply a remolding preview generated by :meth:`optimize_formation` atomically."""
        from .formations import FormationService
        members = {int(k): dict(v) for k, v in (result.get("members") or {}).items()}
        return FormationService(self.repo).apply_remolding_plan(plan_id, members)

    @staticmethod
    def _validate_formation_categories(categories: set[str]) -> set[str]:
        requested = set(categories)
        unsupported = requested - FORMATION_AUTO_CATEGORIES
        if unsupported:
            raise ValueError("제대 자동 배치는 현재 리몰딩만 지원합니다.")
        if "remolding" not in requested:
            raise ValueError("자동 배치할 리몰딩을 선택하세요.")
        return {"remolding"}

    @staticmethod
    def _initial_optimized_members(members: list[dict[str, Any]]) -> dict[int, OptimizedMember]:
        return {
            int(member["position"]): OptimizedMember(
                doll_id=int(member["doll_id"]),
                remolding_uids=list(member.get("remolding_uids", [])),
                scores={},
                notes={},
                remolding_target_status=[],
            )
            for member in members
        }


    def _formation_remolding_inputs(
        self,
        members: list[dict[str, Any]],
        remolding_priority_by_position: dict[int, set[str]] | None,
        remolding_targets_by_position: dict[int, dict[str, Any]] | None,
    ) -> tuple[dict[int, str], list[str], dict[str, set[str]], dict[str, dict[str, Any]]]:
        keys_by_pos: dict[int, str] = {}
        valid_keys: list[str] = []
        for member in members:
            key = self.character_key_for_doll(int(member["doll_id"]))
            if key:
                keys_by_pos[int(member["position"])] = key
                valid_keys.append(key)

        member_by_pos = {int(member["position"]): member for member in members}
        priority_by_key: dict[str, set[str]] = {}
        targets_by_key: dict[str, dict[str, Any]] = {}
        for pos, key in keys_by_pos.items():
            selected = set((remolding_priority_by_position or {}).get(pos, set()))
            if selected:
                priority_by_key[key] = selected
            explicit_targets = (remolding_targets_by_position or {}).get(pos)
            if explicit_targets is None:
                explicit_targets = member_by_pos.get(pos, {}).get("remolding_targets", {})
            if not explicit_targets:
                continue
            normalized_targets: dict[str, Any] = {}
            for option_key, requested in explicit_targets.items():
                option = self.recommendation.options.get(str(option_key))
                if not option:
                    continue
                spec = self.recommendation.normalize_target_spec(
                    requested,
                    int(option.get("maxLevel") or 0),
                    int(option.get("weight") or 100),
                )
                if spec:
                    normalized_targets[str(option_key)] = spec
            if normalized_targets:
                targets_by_key[key] = normalized_targets
        return keys_by_pos, valid_keys, priority_by_key, targets_by_key

    def _optimize_formation_remoldings(
        self,
        members: list[dict[str, Any]],
        result: dict[int, OptimizedMember],
        *,
        remolding_priority_by_position: dict[int, set[str]] | None,
        remolding_priority_multiplier: float,
        remolding_targets_by_position: dict[int, dict[str, Any]] | None,
        character_level_override: int | None,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        keys_by_pos, valid_keys, priority_by_key, targets_by_key = self._formation_remolding_inputs(
            members,
            remolding_priority_by_position,
            remolding_targets_by_position,
        )
        allocation = self.allocate_remoldings(
            valid_keys,
            priority_factors=priority_by_key,
            priority_multiplier=remolding_priority_multiplier,
            targets_by_character=targets_by_key,
            priority_character_keys=self.favorite_character_keys(),
            character_level_override=character_level_override,
            should_cancel=should_cancel,
        )
        by_key = {row["character_key"]: row for row in allocation["rows"]}
        factor_names = reference.remolding_rules().get("factor_names", {})
        for pos, key in keys_by_pos.items():
            row = by_key.get(key)
            if not row:
                continue
            result[pos].remolding_uids = [str(x["uid"]) for x in row["pieces"]]
            result[pos].scores["remolding"] = float(row["score"])
            target_status = list(row.get("target_status", []))
            met = sum(1 for x in target_status if x.get("met"))
            target_tail = f" · 목표 {met}/{len(target_status)}개 충족" if target_status else ""
            priority_labels = [factor_names.get(factor, factor) for factor in row.get("priority_factors", [])]
            priority_tail = (
                f" · 우선 계열: {', '.join(priority_labels)}"
                if priority_labels and not target_status
                else ""
            )
            pheno = row.get("phenomenon_status") or {}
            desired = pheno.get("desired") or {}
            pheno_tail = (
                f" · 현상 {pheno.get('desired_stage', '—')} "
                f"{'달성' if desired.get('active') else '미달'} "
                f"(Lv.{int(row.get('character_level') or 60)})"
                if pheno
                else ""
            )
            result[pos].notes["remolding"] = (
                f"캐릭터별 현상 요구치를 최우선으로 {len(row['pieces'])}/6개를 "
                f"제대 내부에서 중복 없이 배치{pheno_tail}{target_tail}{priority_tail}"
            )
            result[pos].remolding_target_status = target_status

        for member in members:
            pos = int(member["position"])
            if pos not in keys_by_pos:
                result[pos].notes["remolding"] = "캐릭터와 리몰딩 추천 기준을 연결하지 못해 현재 리몰딩 유지"

    @staticmethod
    def _formation_changes(
        members: list[dict[str, Any]],
        result: dict[int, OptimizedMember],
        should_cancel: Callable[[], bool] | None,
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for member in members:
            _raise_if_cancelled(should_cancel)
            pos = int(member["position"])
            new = result[pos]
            changes.append({
                "position": pos,
                "doll_id": new.doll_id,
                "category": "remolding",
                "before": list(member.get("remolding_uids", [])),
                "after": new.remolding_uids,
                "score": new.scores.get("remolding"),
                "note": new.notes.get("remolding", ""),
            })
        return changes

    def optimize_formation(
        self,
        plan_id: int,
        categories: set[str],
        *,
        remolding_priority_by_position: dict[int, set[str]] | None = None,
        remolding_priority_multiplier: float = 2.0,
        remolding_targets_by_position: dict[int, dict[str, Any]] | None = None,
        character_level_override: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Preview physical remolding assignment for one formation."""
        from .formations import FormationService

        _raise_if_cancelled(should_cancel)
        categories = self._validate_formation_categories(categories)
        plan = FormationService(self.repo).get(plan_id)
        members = sorted(plan["members"], key=lambda member: int(member["position"]))
        if not members:
            raise ValueError("자동 배치할 인형이 없습니다. 먼저 제대에 인형을 배치하거나 게임 제대를 가져오세요.")
        result = self._initial_optimized_members(members)

        self._optimize_formation_remoldings(
            members,
            result,
            remolding_priority_by_position=remolding_priority_by_position,
            remolding_priority_multiplier=remolding_priority_multiplier,
            remolding_targets_by_position=remolding_targets_by_position,
            character_level_override=character_level_override,
            should_cancel=should_cancel,
        )

        changes = self._formation_changes(members, result, should_cancel)
        members_out = {
            position: {key: getattr(member, key) for key in member.__slots__}
            for position, member in result.items()
        }
        return {
            "plan": plan,
            "categories": ["remolding"],
            "members": members_out,
            "changes": changes,
            "remolding_priority_by_position": {
                str(key): sorted(value)
                for key, value in (remolding_priority_by_position or {}).items()
                if value
            },
            "remolding_priority_multiplier": float(remolding_priority_multiplier),
            "remolding_targets_by_position": {
                str(key): {
                    str(option_key): dict(spec)
                    for option_key, spec in value.items()
                    if isinstance(spec, dict)
                }
                for key, value in (remolding_targets_by_position or {}).items()
                if value
            },
        }

