from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from . import reference

NORMAL_POINTS = 4
GREAT_POINTS = 5
LEVEL_THRESHOLDS = (10, 25, 50)
LEVEL_BASE_POINTS = (0, 10, 25, 50)
LEVEL_STAGE_CAPACITY = (10, 15, 25, 0)


@dataclass(frozen=True)
class CookingProgress:
    normal_successes: int
    great_successes: int
    points: int
    level: int
    next_threshold: int | None
    remaining_to_next: int
    remaining_total: int
    min_more: int
    max_more: int


def load_permanent_dishes() -> list[dict]:
    payload = reference.dataset_payload("cooking_permanent")
    rows = payload.get("dishes") if isinstance(payload, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _progress_from_points(points: int, *, normal_successes: int = 0, great_successes: int = 0) -> CookingProgress:
    capped_points = min(LEVEL_THRESHOLDS[-1], max(0, int(points)))
    level = sum(capped_points >= threshold for threshold in LEVEL_THRESHOLDS)
    next_threshold = next((value for value in LEVEL_THRESHOLDS if capped_points < value), None)
    remaining_next = max(0, int(next_threshold or capped_points) - capped_points) if next_threshold else 0
    remaining_total = max(0, LEVEL_THRESHOLDS[-1] - capped_points)
    min_more = math.ceil(remaining_total / GREAT_POINTS) if remaining_total else 0
    max_more = math.ceil(remaining_total / NORMAL_POINTS) if remaining_total else 0
    return CookingProgress(
        normal_successes=max(0, int(normal_successes)),
        great_successes=max(0, int(great_successes)),
        points=capped_points,
        level=level,
        next_threshold=next_threshold,
        remaining_to_next=remaining_next,
        remaining_total=remaining_total,
        min_more=min_more,
        max_more=max_more,
    )


def cooking_progress(normal_successes: int, great_successes: int) -> CookingProgress:
    normal = max(0, int(normal_successes))
    great = max(0, int(great_successes))
    return _progress_from_points(
        normal * NORMAL_POINTS + great * GREAT_POINTS,
        normal_successes=normal,
        great_successes=great,
    )


def points_from_level_fullness(level: int, fullness: int) -> int:
    """Convert the game's current Lv + in-level fullness into cumulative points.

    Lv.0 is 0~10, Lv.1 starts at cumulative 10 and has a 15-point segment,
    Lv.2 starts at cumulative 25 and has a 25-point segment, and Lv.3 is the
    completed cumulative 50-point state. A full segment is accepted and simply
    normalizes to the next level threshold.
    """
    stage = max(0, min(3, int(level)))
    if stage >= 3:
        return LEVEL_THRESHOLDS[-1]
    capacity = LEVEL_STAGE_CAPACITY[stage]
    current = max(0, min(capacity, int(fullness)))
    return min(LEVEL_THRESHOLDS[-1], LEVEL_BASE_POINTS[stage] + current)


def cooking_progress_from_state(
    level: int,
    fullness: int,
    normal_successes: int = 0,
    great_successes: int = 0,
) -> CookingProgress:
    """Calculate completion from the player's current game state plus new results."""
    normal = max(0, int(normal_successes))
    great = max(0, int(great_successes))
    points = points_from_level_fullness(level, fullness)
    points += cooking_progress(normal, great).points
    return _progress_from_points(points, normal_successes=normal, great_successes=great)


def ingredient_requirements(ingredients: Iterable[str], cook_count: int) -> dict[str, int]:
    count = max(0, int(cook_count))
    totals = Counter(str(value) for value in ingredients if str(value).strip())
    return {name: amount * count for name, amount in sorted(totals.items())}


def exact_completion_options(remaining_points: int, *, limit: int = 4) -> list[tuple[int, int, int]]:
    """Return compact (great, normal, excess) options sorted by fewest cooks/waste."""
    remaining = max(0, int(remaining_points))
    if not remaining:
        return [(0, 0, 0)]
    candidates: list[tuple[int, int, int, int]] = []
    max_count = math.ceil(remaining / NORMAL_POINTS) + 2
    for great in range(max_count + 1):
        for normal in range(max_count + 1):
            gained = great * GREAT_POINTS + normal * NORMAL_POINTS
            if gained < remaining:
                continue
            candidates.append((great + normal, gained - remaining, great, normal))
    candidates.sort()
    return [(great, normal, excess) for _count, excess, great, normal in candidates[: max(1, int(limit))]]
