from __future__ import annotations

import hashlib
import json

from gfl2tool.models import Doll, Remolding, RemoldingSlot
from gfl2tool.repository import Repository
from gfl2tool.services.optimizer import EquipmentOptimizer

CURRENT_GOLDEN_SHA256 = "84169a985c8072d60ea4cc7d40a4ca5aeca6ce2d338aca63cade445934d1719d"


def _piece(uid: str, rid: int, specs: list[tuple[str, str, int]]) -> Remolding:
    return Remolding(
        uid,
        rid,
        "",
        [
            RemoldingSlot(
                "x",
                option,
                option_key=option,
                factor_type=factor,
                level_contribution=level,
            )
            for option, factor, level in specs
        ],
    )


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return round(value, 12)
    return value


def _case_result(repo: Repository, case: int):
    if case == 0:
        pieces = []
        for index in range(6):
            pieces.append(_piece(f"s{index}", 985401, [("sentinel_1", "sentinel", 1)]))
            pieces.append(_piece(f"v{index}", 985201, [("vanguard_1", "vanguard", 1)]))
        repo.replace_remoldings(pieces)
        return EquipmentOptimizer(repo).allocate_remoldings(["nemesis", "daiyan"])

    if case == 1:
        repo.replace_remoldings([
            _piece("p1a", 985401, [("sentinel_3", "sentinel", 3)]),
            _piece("p1b", 985401, [("sentinel_3", "sentinel", 3)]),
            _piece("p2a", 985401, [("sentinel_1", "sentinel", 3)]),
            _piece("p2b", 985401, [("sentinel_1", "sentinel", 3)]),
            _piece("p2c", 985401, [("sentinel_2", "sentinel", 3)]),
            _piece("p2d", 985401, [("sentinel_2", "sentinel", 3)]),
            _piece("v1", 985201, [("vanguard_1", "vanguard", 1)]),
            _piece("v2", 985201, [("vanguard_1", "vanguard", 1)]),
        ])
        return EquipmentOptimizer(repo).allocate_remoldings(
            ["nemesis"],
            targets_by_character={
                "nemesis": {
                    "sentinel_3": {"level": 6, "priority": 1},
                    "sentinel_1": {"level": 6, "priority": 2},
                    "sentinel_2": {"level": 6, "priority": 2},
                }
            },
        )

    if case == 2:
        repo.replace_dolls([
            Doll(1008, "네메시스", 60, 1),
            Doll(1032, "다이옌", 60, 1),
        ])
        repo.set_doll_favorite(1008, True)
        pieces = []
        for index in range(8):
            pieces.append(
                _piece(
                    f"s{index}",
                    985401,
                    [("sentinel_5", "sentinel", 3), ("bulwark_5", "bulwark", 1)],
                )
            )
            pieces.append(_piece(f"v{index}", 985201, [("vanguard_5", "vanguard", 3)]))
        repo.replace_remoldings(pieces)
        optimizer = EquipmentOptimizer(repo)
        return optimizer.allocate_remoldings(
            ["nemesis", "daiyan"],
            priority_character_keys=optimizer.favorite_character_keys(),
            priority_factors={"nemesis": {"sentinel"}},
            priority_multiplier=3.0,
        )

    repo.replace_dolls([Doll(1008, "네메시스", 45, 1)])
    pieces = [
        _piece(f"s{index}", 985401, [("sentinel_5", "sentinel", 3)])
        for index in range(4)
    ]
    pieces.extend(
        _piece(f"v{index}", 985201, [("vanguard_5", "vanguard", 3)])
        for index in range(2)
    )
    repo.replace_remoldings(pieces)
    return EquipmentOptimizer(repo).allocate_remoldings(["nemesis"])


def test_refactored_allocator_matches_golden_results(tmp_path):
    results = []
    for case in range(4):
        with Repository(tmp_path / f"golden-{case}.db") as repo:
            results.append(_canonical(_case_result(repo, case)))
    payload = json.dumps(
        results,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    assert hashlib.sha256(payload).hexdigest() == CURRENT_GOLDEN_SHA256

def _canonical_formation(value):
    if isinstance(value, dict):
        return {
            str(key): _canonical_formation(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in {"created_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_canonical_formation(item) for item in value]
    if isinstance(value, float):
        return round(value, 12)
    return value


def _formation_case(repo: Repository, case: int):
    from gfl2tool.services.formations import FormationService

    repo.replace_dolls([
        Doll(1008, "네메시스", 60, 1),
        Doll(1032, "다이옌", 60, 1),
    ])
    pieces = []
    for index in range(8):
        pieces.append(_piece(f"s{index}", 985401, [("sentinel_1", "sentinel", 1)]))
        pieces.append(_piece(f"v{index}", 985201, [("vanguard_1", "vanguard", 1)]))
    repo.replace_remoldings(pieces)
    formations = FormationService(repo)
    plan_id = formations.create("A")
    formations.set_member(plan_id, 1, 1008)
    formations.set_member(plan_id, 2, 1032)
    optimizer = EquipmentOptimizer(repo)
    if case == 0:
        return optimizer.optimize_formation(plan_id, {"remolding"})
    if case == 1:
        return optimizer.optimize_formation(
            plan_id,
            {"remolding"},
            remolding_priority_by_position={1: {"sentinel"}},
            remolding_priority_multiplier=3.0,
        )
    return optimizer.optimize_formation(
        plan_id,
        {"remolding"},
        remolding_targets_by_position={
            1: {"sentinel_1": {"level": 2, "priority": 1}}
        },
    )


def test_remolding_only_formation_optimizer_is_deterministic(tmp_path):
    for case in range(3):
        results = []
        for repeat in range(2):
            with Repository(tmp_path / f"formation-current-{case}-{repeat}.db") as repo:
                results.append(_canonical_formation(_formation_case(repo, case)))
        assert results[0] == results[1]
        assert results[0]["categories"] == ["remolding"]
        assert all(change["category"] == "remolding" for change in results[0]["changes"])
        for member in results[0]["members"].values():
            assert "common_key_uids" not in member
            assert "weapon_uid" not in member
            assert "attachment_uids" not in member

