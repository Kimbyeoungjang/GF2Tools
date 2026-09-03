from gfl2tool.models import Remolding, RemoldingSlot
from gfl2tool.repository import Repository
from gfl2tool.services.optimizer import EquipmentOptimizer


def _piece(uid: str, rid: int, factor: str, option_key: str) -> Remolding:
    return Remolding(uid, rid, "", [RemoldingSlot("x", option_key, option_key=option_key, factor_type=factor)])


def test_global_remolding_allocation_is_unique_and_respects_slot_counts(tmp_path):
    pieces = []
    # Nemesis + Daiyan together need 6 sentinel and 6 vanguard pieces.
    for i in range(6):
        pieces.append(_piece(f"s{i}", 985401, "sentinel", "sentinel_1"))
        pieces.append(_piece(f"v{i}", 985201, "vanguard", "vanguard_1"))
    with Repository(tmp_path / "opt.db") as repo:
        repo.replace_remoldings(pieces)
        result = EquipmentOptimizer(repo).allocate_remoldings(["nemesis", "daiyan"])
        assert result["missing_slots"] == 0
        all_uids = [p["uid"] for row in result["rows"] for p in row["pieces"]]
        assert len(all_uids) == 12
        assert len(set(all_uids)) == 12
        by_key = {row["character_key"]: row for row in result["rows"]}
        assert sum(p["primary_factor"] == "sentinel" for p in by_key["nemesis"]["pieces"]) == 4
        assert sum(p["primary_factor"] == "vanguard" for p in by_key["nemesis"]["pieces"]) == 2
        assert sum(p["primary_factor"] == "sentinel" for p in by_key["daiyan"]["pieces"]) == 2
        assert sum(p["primary_factor"] == "vanguard" for p in by_key["daiyan"]["pieces"]) == 4


def test_individual_remolding_result_contains_auditable_slot_components(tmp_path):
    with Repository(tmp_path / "detail.db") as repo:
        repo.replace_remoldings([_piece("s1", 985401, "sentinel", "sentinel_1")])
        result = EquipmentOptimizer(repo).best_remolding_set("nemesis")
        slot = result["pieces"][0]["slots"][0]
        assert slot["components"]
        assert any(c["source"] == "옵션 기본 가중치" for c in slot["components"])




def test_formation_auto_assignment_is_remolding_only_and_applies_atomically(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.formations import FormationService

    pieces = [
        _piece("s1", 985401, "sentinel", "sentinel_1"),
        _piece("s2", 985401, "sentinel", "sentinel_1"),
        _piece("s3", 985401, "sentinel", "sentinel_1"),
        _piece("s4", 985401, "sentinel", "sentinel_1"),
        _piece("v1", 985201, "vanguard", "vanguard_1"),
        _piece("v2", 985201, "vanguard", "vanguard_1"),
    ]
    with Repository(tmp_path / "formation-auto.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        repo.replace_remoldings(pieces)
        formation = FormationService(repo)
        plan_id = formation.create("테스트 제대")
        formation.set_member(plan_id, 1, 1008)
        optimizer = EquipmentOptimizer(repo)
        result = optimizer.optimize_formation(plan_id, {"remolding"})
        member = result["members"][1]
        assert set(member) == {"doll_id", "remolding_uids", "scores", "notes", "remolding_target_status"}
        assert len(member["remolding_uids"]) == 6

        optimizer.apply_formation_result(plan_id, result)
        saved = formation.get(plan_id)["members"][0]
        assert len(saved["remolding_uids"]) == 6


def test_formation_optimizer_rejects_weapon_and_attachment_auto_categories(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.formations import FormationService

    with Repository(tmp_path / "unsupported-auto.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        plan_id = FormationService(repo).create("테스트 제대")
        FormationService(repo).set_member(plan_id, 1, 1008)
        optimizer = EquipmentOptimizer(repo)
        for category in ("weapon", "attachment"):
            try:
                optimizer.optimize_formation(plan_id, {category})
            except ValueError as exc:
                assert "리몰딩만 지원합니다" in str(exc)
            else:
                raise AssertionError(f"{category} auto assignment must be rejected")


def test_remolding_priority_factor_biases_global_assignment(tmp_path):
    """Formation priority is an allocation objective; raw 리몰딩 추천 scores stay auditable."""
    # Two sentinel pieces: s-high scores higher than s-low for Nemesis. Without
    # priority either character may win it depending on score. Mark Nemesis'
    # sentinel pool as priority and verify the high piece is routed to Nemesis.
    pieces = [
        Remolding("s-high", 985401, "", [
            RemoldingSlot("x", "공격 강화", option_key="sentinel_1", factor_type="sentinel"),
            RemoldingSlot("x", "단일 특화", option_key="sentinel_3", factor_type="sentinel"),
        ]),
        _piece("s-low", 985401, "sentinel", "sentinel_14"),
    ]
    # Add enough neutral pieces to satisfy required slots for two characters.
    for i in range(8):
        pieces.append(_piece(f"s{i}", 985401, "sentinel", "sentinel_2"))
        pieces.append(_piece(f"v{i}", 985201, "vanguard", "vanguard_2"))
    with Repository(tmp_path / "priority.db") as repo:
        repo.replace_remoldings(pieces)
        opt = EquipmentOptimizer(repo)
        result = opt.allocate_remoldings(
            ["nemesis", "daiyan"],
            priority_factors={"nemesis": {"sentinel"}},
            priority_multiplier=3.0,
        )
        by_key = {row["character_key"]: row for row in result["rows"]}
        assert "sentinel" in by_key["nemesis"]["priority_factors"]
        assert any(p["priority_applied"] for p in by_key["nemesis"]["pieces"] if p["primary_factor"] == "sentinel")
        # Displayed score remains the raw 리몰딩 추천 score, not the boosted objective.
        for p in by_key["nemesis"]["pieces"]:
            if p.get("priority_applied"):
                assert float(p["allocation_objective"]) >= float(p["score"])


def test_remolding_target_level_beats_plain_planner_score(tmp_path):
    """Explicit Lv targets are allocation goals, ahead of ordinary 리몰딩 추천 score."""
    def rp(uid, option, factor, level):
        return Remolding(uid, 985401 if factor == "sentinel" else 985201, "", [
            RemoldingSlot("x", option, option_key=option, factor_type=factor, level_contribution=level)
        ])

    pieces = [
        # High normal-score Sentinel alternatives.
        rp("high1", "sentinel_1", "sentinel", 3),
        rp("high2", "sentinel_1", "sentinel", 3),
        rp("high3", "sentinel_1", "sentinel", 3),
        # Target pieces. Together they make 단일 특화 Lv.6.
        rp("target1", "sentinel_3", "sentinel", 3),
        rp("target2", "sentinel_3", "sentinel", 3),
        # Required Vanguard pool for Nemesis.
        rp("v1", "vanguard_1", "vanguard", 1),
        rp("v2", "vanguard_1", "vanguard", 1),
    ]
    with Repository(tmp_path / "target.db") as repo:
        repo.replace_remoldings(pieces)
        result = EquipmentOptimizer(repo).allocate_remoldings(
            ["nemesis"], targets_by_character={"nemesis": {"sentinel_3": {"level": 6, "weight": 100, "priority": 1}}}
        )
        row = result["rows"][0]
        assert row["target_status"][0]["met"] is True
        assert row["target_status"][0]["display_level"] == 6
        uids = {piece["uid"] for piece in row["pieces"]}
        assert {"target1", "target2"} <= uids
        assert len(uids) == 6



def test_remolding_priority_groups_are_unlimited_and_lexicographic(tmp_path):
    """Priority 1 targets must win over lower groups even when lower targets score well."""
    def rp(uid, option, factor="sentinel", level=3):
        rid = 985401 if factor == "sentinel" else 985201
        return Remolding(uid, rid, "", [
            RemoldingSlot("x", option, option_key=option, factor_type=factor, level_contribution=level)
        ])

    # Nemesis has four sentinel slots. Only two priority-1 pieces exist; lower
    # priority options are plentiful. The optimizer must preserve both P1 pieces.
    pieces = [
        rp("p1a", "sentinel_3", level=3),
        rp("p1b", "sentinel_3", level=3),
        rp("p2a", "sentinel_1", level=3),
        rp("p2b", "sentinel_1", level=3),
        rp("p2c", "sentinel_2", level=3),
        rp("p2d", "sentinel_2", level=3),
        rp("v1", "vanguard_1", "vanguard", 1),
        rp("v2", "vanguard_1", "vanguard", 1),
    ]
    targets = {
        # More than the former fixed UI limit is legal at the API/storage layer.
        "sentinel_3": {"level": 6, "priority": 1},
        "sentinel_1": {"level": 6, "priority": 2},
        "sentinel_2": {"level": 6, "priority": 2},
        "sentinel_4": {"level": 1, "priority": 3},
        "sentinel_5": {"level": 1, "priority": 3},
        "sentinel_6": {"level": 1, "priority": 3},
        "sentinel_11": {"level": 1, "priority": 3},
    }
    with Repository(tmp_path / "priority-target.db") as repo:
        repo.replace_remoldings(pieces)
        result = EquipmentOptimizer(repo).allocate_remoldings(
            ["nemesis"], targets_by_character={"nemesis": targets}
        )
        row = result["rows"][0]
        status = {x["option_key"]: x for x in row["target_status"]}
        assert status["sentinel_3"]["priority"] == 1
        assert status["sentinel_3"]["met"] is True
        assert {"p1a", "p1b"} <= {p["uid"] for p in row["pieces"]}
        assert len(row["targets"]) == len(targets)


def test_formation_remolding_target_storage_uses_current_shape(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.formations import FormationService
    with Repository(tmp_path / "priority-store.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        fs = FormationService(repo)
        pid = fs.create("우선순위 저장")
        fs.set_member(pid, 1, 1008, remolding_targets={
            "sentinel_3": {"level": 6, "weight": 700, "priority": 1},
            "sentinel_2": {"level": 4, "weight": 300, "priority": 2},
        })
        targets = fs.get(pid)["members"][0]["remolding_targets"]
        assert targets["sentinel_3"] == {"level": 6, "weight": 700, "priority": 1}
        assert targets["sentinel_2"] == {"level": 4, "weight": 300, "priority": 2}


def test_same_priority_targets_are_saturated_before_overstacking_one_option(tmp_path):
    def rp(uid, option, level=3):
        return Remolding(uid, 985401, "", [
            RemoldingSlot("x", option, option_key=option, factor_type="sentinel", level_contribution=level)
        ])
    pieces = [
        rp("s3a", "sentinel_3"), rp("s3b", "sentinel_3"),
        rp("s2a", "sentinel_2"), rp("neutral", "sentinel_14"),
        _piece("v1", 985201, "vanguard", "vanguard_1"),
        _piece("v2", 985201, "vanguard", "vanguard_1"),
    ]
    with Repository(tmp_path / "saturate.db") as repo:
        repo.replace_remoldings(pieces)
        result = EquipmentOptimizer(repo).allocate_remoldings(
            ["nemesis"],
            targets_by_character={"nemesis": {
                "sentinel_3": {"level":3,"weight":600,"priority":1},
                "sentinel_2": {"level":3,"weight":400,"priority":1},
            }},
        )
        status = {x["option_key"]: x for x in result["rows"][0]["target_status"]}
        assert status["sentinel_3"]["met"] is True
        assert status["sentinel_2"]["met"] is True
        assert "s2a" in {p["uid"] for p in result["rows"][0]["pieces"]}


def test_phenomenon_flower_is_structural_objective_before_plain_piece_score(tmp_path):
    """A higher raw planner score must not beat the exact Lv.60 flower thresholds."""
    from gfl2tool.models import Doll
    from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService

    def rem(uid: str, rid: int, specs):
        return Remolding(uid, rid, "", [
            RemoldingSlot("x", opt, option_key=opt, factor_type=factor, level_contribution=level)
            for opt, factor, level in specs
        ])

    pieces = [
        rem("sg0", 985401, [("sentinel_5", "sentinel", 3), ("vanguard_5", "vanguard", 2), ("bulwark_5", "bulwark", 3)]),
        rem("sg1", 985401, [("sentinel_5", "sentinel", 3)]),
        rem("sg2", 985401, [("sentinel_5", "sentinel", 3)]),
        rem("sg3", 985401, [("sentinel_5", "sentinel", 3)]),
        rem("vg0", 985201, [("sentinel_5", "sentinel", 3), ("vanguard_5", "vanguard", 3)]),
        rem("vg1", 985201, [("sentinel_5", "sentinel", 3), ("vanguard_5", "vanguard", 3)]),
    ]
    # Deliberately high-score alternatives that add only Support phenomenon points.
    for i in range(4):
        pieces.append(rem(f"sb{i}", 985401, [("support_5", "support", 3)]))
    for i in range(2):
        pieces.append(rem(f"vb{i}", 985201, [("support_5", "support", 3)]))

    with Repository(tmp_path / "pheno-objective.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        repo.replace_remoldings(pieces)
        svc = RemoldingRecommendationService(repo)
        svc.set_override("nemesis", "support_5", score_adjustment=10_000)
        result = EquipmentOptimizer(repo).allocate_remoldings(["nemesis"])
        row = result["rows"][0]
        assert row["phenomenon_status"]["flower"]["active"] is True
        assert row["desired_phenomenon_stage"] == "꽃"
        assert {p["uid"] for p in row["pieces"]} == {"sg0", "sg1", "sg2", "sg3", "vg0", "vg1"}


def test_imported_doll_level_does_not_lower_default_remolding_calculation_level(tmp_path):
    from gfl2tool.models import Doll
    with Repository(tmp_path / "pheno-lv45.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 45, 1)])
        # Enough neutral inventory to produce a result; the key assertion is the
        # level-aware objective and status exposed by the optimizer.
        pieces = []
        for i in range(4):
            pieces.append(Remolding(f"s{i}", 985401, "", [
                RemoldingSlot("x", "급습 태세", option_key="sentinel_5", factor_type="sentinel", level_contribution=3)
            ]))
        for i in range(2):
            pieces.append(Remolding(f"v{i}", 985201, "", [
                RemoldingSlot("x", "급습 마스터", option_key="vanguard_5", factor_type="vanguard", level_contribution=3)
            ]))
        repo.replace_remoldings(pieces)
        row = EquipmentOptimizer(repo).allocate_remoldings(["nemesis"])["rows"][0]
        assert row["character_level"] == 60
        assert row["desired_phenomenon_stage"] == "꽃"
        assert row["phenomenon_status"]["flower"]["level_met"] is True


def test_favorite_character_keys_resolve_from_owned_dolls(tmp_path):
    from gfl2tool.models import Doll

    with Repository(tmp_path / "favorite-optimizer.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1), Doll(1032, "다이옌", 60, 1)])
        repo.set_doll_favorite(1008, True)
        optimizer = EquipmentOptimizer(repo)
        assert optimizer.favorite_character_keys() == {"nemesis"}


def test_favorite_priority_is_visible_in_allocation_reason(tmp_path):
    from gfl2tool.models import Doll

    pieces = [
        _piece(f"s{i}", 985401, "sentinel", "sentinel_1") for i in range(4)
    ] + [
        _piece(f"v{i}", 985201, "vanguard", "vanguard_1") for i in range(2)
    ]
    with Repository(tmp_path / "favorite-allocation.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        repo.set_doll_favorite(1008, True)
        repo.replace_remoldings(pieces)
        optimizer = EquipmentOptimizer(repo)
        result = optimizer.allocate_remoldings(
            ["nemesis"], priority_character_keys=optimizer.favorite_character_keys()
        )
        assert len(result["rows"][0]["pieces"]) == 6
        assert any("즐겨찾기 우선" in str(piece.get("allocation_note") or "") for piece in result["rows"][0]["pieces"])


def test_global_allocation_reads_physical_remolding_inventory_once(tmp_path):
    pieces = []
    for i in range(6):
        pieces.append(_piece(f"s{i}", 985401, "sentinel", "sentinel_1"))
        pieces.append(_piece(f"v{i}", 985201, "vanguard", "vanguard_1"))
    with Repository(tmp_path / "single-read.db") as repo:
        repo.replace_remoldings(pieces)
        optimizer = EquipmentOptimizer(repo)
        statements = []
        repo.con.set_trace_callback(statements.append)
        try:
            optimizer.allocate_remoldings(["nemesis", "daiyan"])
        finally:
            repo.con.set_trace_callback(None)
        remolding_selects = [
            sql for sql in statements
            if sql.lstrip().upper().startswith("SELECT") and " FROM REMOLDINGS" in sql.upper()
        ]
        assert len(remolding_selects) == 1, remolding_selects


def test_optimizer_does_not_scan_imported_formations(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.formations import FormationService
    with Repository(tmp_path / "no-key-scan.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        pieces = [
            _piece(f"s{i}", 985401, "sentinel", "sentinel_1") for i in range(4)
        ] + [
            _piece(f"v{i}", 985201, "vanguard", "vanguard_1") for i in range(2)
        ]
        repo.replace_remoldings(pieces)
        pid = FormationService(repo).create("A")
        FormationService(repo).set_member(pid, 1, 1008)
        statements = []
        repo.con.set_trace_callback(statements.append)
        EquipmentOptimizer(repo).optimize_formation(pid, {"remolding"})
        repo.con.set_trace_callback(None)
        assert not any("game_formations" in q.lower() for q in statements)
        assert not any("common_keys" in q.lower() for q in statements)


def test_allocator_facade_is_thin_and_planner_owns_hot_path_caches():
    import inspect
    from gfl2tool.services.optimizer import _RemoldingAllocationPlanner

    facade = inspect.getsource(EquipmentOptimizer.allocate_remoldings)
    planner = inspect.getsource(_RemoldingAllocationPlanner)
    assert len(facade.splitlines()) < 35
    assert "_RemoldingAllocationPlanner(" in facade
    assert "pieces_by_factor" in planner
    assert "metric_cache" in planner
    assert "signature = (" in planner
    assert "_search_best_allocation" in planner
    assert "_saturation_rebalance" in planner


def test_explicit_calculation_level_override_selects_requested_phenomenon_stage(tmp_path):
    from gfl2tool.models import Doll

    with Repository(tmp_path / "pheno-level-overrides.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        pieces = []
        for i in range(4):
            pieces.append(Remolding(f"s{i}", 985401, "", [
                RemoldingSlot(
                    "x",
                    "급습 태세",
                    option_key="sentinel_5",
                    factor_type="sentinel",
                    level_contribution=3,
                )
            ]))
        for i in range(2):
            pieces.append(Remolding(f"v{i}", 985201, "", [
                RemoldingSlot(
                    "x",
                    "급습 마스터",
                    option_key="vanguard_5",
                    factor_type="vanguard",
                    level_contribution=3,
                )
            ]))
        repo.replace_remoldings(pieces)

        expected = {
            0: "배아",
            10: "떡잎",
            20: "꽃눈",
            30: "꽃봉오리",
            45: "꽃망울",
            60: "꽃",
        }
        optimizer = EquipmentOptimizer(repo)
        for level, stage in expected.items():
            row = optimizer.allocate_remoldings(
                ["nemesis"], character_level_override=level
            )["rows"][0]
            assert row["character_level"] == level
            assert row["desired_phenomenon_stage"] == stage
