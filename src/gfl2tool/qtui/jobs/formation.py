from __future__ import annotations

from ...repository import Repository
from ...services.optimizer import EquipmentOptimizer


def optimize_formation(
    db_path: str,
    plan_id: int,
    categories: set[str],
    request_token: tuple[int, int],
    should_cancel=None,
    *,
    character_level_override: int | None = None,
):
    with Repository(db_path) as repo:
        worker_start = repo.state_token()
        result = EquipmentOptimizer(repo).optimize_formation(
            plan_id,
            categories,
            character_level_override=character_level_override,
            should_cancel=should_cancel,
        )
        return request_token, worker_start, repo.state_token(), result
