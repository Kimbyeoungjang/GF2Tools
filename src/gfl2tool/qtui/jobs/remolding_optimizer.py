from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...repository import Repository
from ...services.optimizer import EquipmentOptimizer
from ...services.remolding_recommendation import RemoldingRecommendationService


def score_owned_remoldings(
    db_path: str,
    character_key: str,
    *,
    serial: int,
    state_token: tuple[int, int],
    snapshot: list[dict[str, Any]] | None,
    should_cancel: Callable[[], bool],
):
    """Score one character's owned pieces in a worker-owned repository."""
    with Repository(db_path) as repo:
        svc = RemoldingRecommendationService(repo)
        pieces = snapshot if snapshot is not None else svc.owned_remolding_pieces()
        rows = svc.score_remolding_pieces(character_key, pieces, should_cancel=should_cancel)
        return character_key, serial, state_token, pieces, rows, bool(should_cancel())


def best_remolding_set(
    db_path: str,
    character_key: str,
    request_token: tuple[int, int],
    character_level_override: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
):
    with Repository(db_path) as repo:
        worker_start = repo.state_token()
        optimizer = EquipmentOptimizer(repo)
        result = optimizer.best_remolding_set(character_key, should_cancel=should_cancel)
        result["character_level"] = optimizer.calculation_level_for_key(
            character_key, character_level_override
        )
        return request_token, worker_start, repo.state_token(), result


def allocate_owned_remoldings(
    db_path: str,
    character_keys: list[str],
    request_token: tuple[int, int],
    should_cancel: Callable[[], bool] | None = None,
    *,
    character_level_override: int | None = None,
):
    with Repository(db_path) as repo:
        worker_start = repo.state_token()
        optimizer = EquipmentOptimizer(repo)
        service = RemoldingRecommendationService(repo)
        targets = {key: service.get_target_profile(key) for key in character_keys}
        result = optimizer.allocate_remoldings(
            character_keys,
            targets_by_character=targets,
            priority_character_keys=set(),
            character_level_override=character_level_override,
            should_cancel=should_cancel,
        )
        return request_token, worker_start, repo.state_token(), result
