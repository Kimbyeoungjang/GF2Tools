import json

from gfl2tool.models import Remolding, RemoldingSlot
from gfl2tool.repository import Repository
from gfl2tool.services.remoldings import RemoldingPatternService
from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService




def test_remolding_recommendation_reference_and_recommendation(tmp_path):
    with Repository(tmp_path / "a.db") as repo:
        svc = RemoldingRecommendationService(repo)
        assert len(svc.list_characters()) == 60
        nemesis = svc.get_character("nemesis")
        assert nemesis["nameKR"] == "네메시스"
        assert nemesis["slotDistribution"] == [
            {"factorType": "sentinel", "count": 4},
            {"factorType": "vanguard", "count": 2},
            {"factorType": "bulwark", "count": 0},
        ]
        recs = svc.recommendations("nemesis", "sentinel")
        assert recs[0]["optionKey"] == "sentinel_1"
        assert recs[0]["score"] == 500
        assert any(r["optionKey"] == "sentinel_11" for r in recs)  # corrosion match
        assert not any(r["optionKey"] == "sentinel_8" for r in recs)  # burn mismatch


def test_pattern_matches_logical_option_across_code_variants(tmp_path):
    with Repository(tmp_path / "a.db") as repo:
        repo.replace_remoldings([
            Remolding("10", 1, "", [RemoldingSlot("a7 ba 55", "공격강화3", option_key="sentinel_1")])
        ])
        p = RemoldingPatternService(repo)
        pid = p.create("네메시스", character_key="nemesis")
        p.set_slot(pid, 1, option_key="sentinel_1")
        assert p.matches(pid)[0]["score"] == 1
        assert p.matches(pid)[0]["matched_option_keys"] == ["sentinel_1"]




def test_remolding_recommendation_global_and_character_overrides_are_auditable(tmp_path):
    with Repository(tmp_path / "score.db") as repo:
        svc = RemoldingRecommendationService(repo)
        base = svc.score_option("nemesis", "sentinel_1")
        assert base["score"] == 500
        svc.save_score_config({
            "grades": {"S": 100, "A": 80, "B": 40, "C": 20, "D": 10, "E": 0, "F": -10},
            "multipliers": {"option_weight": 0.5, "base_rank": 1.0, "tag_rank": 2.0},
        })
        svc.set_override("nemesis", "sentinel_1", score_adjustment=25, note="보스전 우선")
        row = svc.score_option("nemesis", "sentinel_1")
        assert row["score"] == 405  # 200*.5 + A(80) + S(100)*2 + 25
        assert row["override"]["note"] == "보스전 우선"
        assert any(c["source"] == "캐릭터별 사용자 조정" for c in row["components"])
        svc.set_override("nemesis", "sentinel_1", state="exclude")
        assert svc.score_option("nemesis", "sentinel_1")["eligible"] is False


def test_remolding_levels_follow_alpha_beta_gamma_and_cap_at_max(tmp_path):
    """WebUI rule: code1/code2/code3 contribute +1/+2/+3, capped for display."""
    with Repository(tmp_path / "levels.db") as repo:
        svc = RemoldingRecommendationService(repo)
        pieces = [
            {"uid": "a", "slots": [{"option_key": "sentinel_3", "code": "ed bb 55", "level_contribution": 1}]},
            {"uid": "b", "slots": [{"option_key": "sentinel_3", "code": "ef bb 55", "level_contribution": 3}]},
            {"uid": "c", "slots": [{"option_key": "sentinel_3", "code": "ef bb 55", "level_contribution": 3}]},
        ]
        levels = svc.aggregate_option_levels(pieces)
        row = levels["sentinel_3"]
        assert row["raw_level"] == 7
        assert row["display_level"] == 6
        assert row["overcap"] == 1
        assert row["max_level"] == 6
        assert row["value"] == 5.5
        status = svc.target_status(pieces, {"sentinel_3": {"level": 6, "weight": 100, "priority": 1}})[0]
        assert status["met"] is True
        assert status["display_level"] == 6


def test_target_profile_defaults_persist_weight_and_explicit_empty(tmp_path):
    with Repository(tmp_path / "targets.db") as repo:
        svc = RemoldingRecommendationService(repo)
        defaults = svc.default_target_profile("nemesis")
        assert defaults["sentinel_1"] == {"level": 6, "weight": 1200, "priority": 1}
        assert defaults["sentinel_3"]["level"] == 3  # non-attack major is deliberately split
        assert defaults["vanguard_1"]["level"] == 3
        assert defaults["support_5"]["level"] == 5  # cross-family minor target

        svc.save_target_profile("nemesis", {"sentinel_1": {"level": 4, "weight": 150}})
        saved = svc.get_target_profile("nemesis")
        assert saved["sentinel_1"]["level"] == 4
        assert saved["sentinel_1"]["weight"] == 150

        svc.save_target_profile("nemesis", {})
        assert svc.get_target_profile("nemesis") == {}


def test_cross_family_minor_roll_is_eligible_and_major_still_respects_slots(tmp_path):
    with Repository(tmp_path / "cross.db") as repo:
        svc = RemoldingRecommendationService(repo)
        # Nemesis has no Support major slot, but support_5 is a real cross-family minor roll.
        assert svc.score_option("nemesis", "support_5")["eligible"] is True
        assert svc.score_option("nemesis", "support_1")["eligible"] is False


def test_character_slot_override_and_dummy_character(tmp_path):
    with Repository(tmp_path / "dummy.db") as repo:
        svc = RemoldingRecommendationService(repo)
        svc.save_character_profile("nemesis", slot_counts={"sentinel":3,"vanguard":2,"bulwark":1,"support":0})
        assert svc.get_character("nemesis")["slotDistribution"] == [
            {"factorType":"sentinel","count":3},{"factorType":"vanguard","count":2},{"factorType":"bulwark","count":1}
        ]
        dummy = svc.create_dummy_character(
            "테스트 더미", slot_counts={"sentinel":2,"vanguard":2,"bulwark":1,"support":1},
            doll_type="sentinel", element_type="physical", tags=["단일형","공격계수"],
        )
        assert dummy["key"].startswith("dummy_")
        assert sum(x["count"] for x in dummy["slotDistribution"]) == 6
        assert any(c["key"] == dummy["key"] for c in svc.list_dummy_characters())
        svc.delete_dummy_character(dummy["key"])
        assert not svc.has_character(dummy["key"])


def test_practical_default_targets_cover_support_bulwark_and_klukai(tmp_path):
    with Repository(tmp_path / "policy.db") as repo:
        svc = RemoldingRecommendationService(repo)

        colphne = svc.default_target_profile("colphne")
        assert colphne["support_1"] == {"level": 6, "weight": 1200, "priority": 1}
        assert colphne["support_4"]["level"] == 6
        assert colphne["bulwark_1"]["level"] == 4
        assert colphne["support_5"]["level"] == 5

        springfield = svc.default_target_profile("springfield")
        assert springfield["support_6"]["level"] == 5
        assert "support_5" not in springfield

        for key in ("andoris", "peri"):
            profile = svc.default_target_profile(key)
            assert profile["bulwark_1"] == {"level": 6, "weight": 1050, "priority": 1}
            assert profile["support_5"] == {"level": 5, "weight": 1100, "priority": 1}

        klukai = svc.default_target_profile("klukai")
        assert klukai["sentinel_1"]["level"] == 6
        assert klukai["sentinel_4"]["level"] == 3
        assert klukai["support_5"] == {"level": 5, "weight": 820, "priority": 2}


def test_exact_character_phenomenon_threshold_exceptions(tmp_path):
    """Planner snapshot has real per-character exceptions beyond generic imagoforms."""
    with Repository(tmp_path / "pheno-exact.db") as repo:
        svc = RemoldingRecommendationService(repo)
        assert svc.phenomenon_requirements("basti")["배아"] == {"support": 6, "sentinel": 2}
        assert svc.phenomenon_requirements("basti")["꽃"] == {"bulwark": 3, "support": 18, "sentinel": 8}
        assert svc.phenomenon_requirements("colphne")["꽃눈"] == {"bulwark": 4, "support": 8, "sentinel": 1}
        assert svc.phenomenon_requirements("colphne")["꽃"] == {"bulwark": 8, "support": 18, "sentinel": 3}
        assert svc.phenomenon_requirements("dushevnaya")["꽃"] == {"bulwark": 5, "support": 18, "sentinel": 6}


def test_final_flower_requires_factor_thresholds_and_level_60(tmp_path):
    with Repository(tmp_path / "pheno-level.db") as repo:
        svc = RemoldingRecommendationService(repo)
        # OTs-14 final flower: bulwark 3 / vanguard 8 / sentinel 18.
        pieces = [
            {"slots": [
                {"option_key": "sentinel_1", "level_contribution": 3},
                {"option_key": "vanguard_1", "level_contribution": 3 if i < 2 else (2 if i == 2 else 0)},
                {"option_key": "bulwark_1", "level_contribution": 3 if i == 0 else 0},
            ]}
            for i in range(6)
        ]
        at_45 = svc.phenomenon_status("ots_14", pieces, character_level=45)
        assert at_45["factor_levels"]["sentinel"] == 18
        assert at_45["factor_levels"]["vanguard"] == 8
        assert at_45["factor_levels"]["bulwark"] == 3
        assert at_45["flower"]["factor_met"] is True
        assert at_45["flower"]["level_met"] is False
        assert at_45["flower"]["active"] is False
        assert at_45["desired_stage"] == "꽃망울"
        assert at_45["desired"]["active"] is True

        at_60 = svc.phenomenon_status("ots_14", pieces, character_level=60)
        assert at_60["flower"]["factor_met"] is True
        assert at_60["flower"]["level_met"] is True
        assert at_60["flower"]["active"] is True
        assert at_60["highest_active"] == "꽃"
        assert at_60["desired_stage"] == "꽃"


def test_character_profile_level_override_and_dummy_level(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.optimizer import EquipmentOptimizer
    with Repository(tmp_path / "level-override.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 45, 1)])
        svc = RemoldingRecommendationService(repo)
        opt = EquipmentOptimizer(repo)
        assert opt.character_level_for_key("nemesis") == 45
        svc.save_character_profile(
            "nemesis", slot_counts={"sentinel": 4, "vanguard": 2, "bulwark": 0, "support": 0},
            level_override=60,
        )
        updated = EquipmentOptimizer(repo)
        assert updated.character_level_for_key("nemesis") == 45
        assert updated.calculation_level_for_key("nemesis") == 60
        dummy = svc.create_dummy_character(
            "꽃 계산 더미", slot_counts={"sentinel": 4, "vanguard": 2, "bulwark": 0, "support": 0}, level=55,
        )
        assert RemoldingRecommendationService(repo).get_character(dummy["key"])["levelOverride"] == 55


def test_screenshot_flower_thresholds_match_ots14_springfield_qiuhua_and_andoris(tmp_path):
    with Repository(tmp_path / "screenshot-pheno.db") as repo:
        svc = RemoldingRecommendationService(repo)
        assert svc.phenomenon_requirements("ots_14")["꽃"] == {"bulwark": 3, "vanguard": 8, "sentinel": 18}
        assert svc.phenomenon_requirements("springfield")["꽃"] == {"bulwark": 8, "support": 18, "sentinel": 3}
        assert svc.phenomenon_requirements("qiuhua")["꽃"] == {"bulwark": 3, "vanguard": 18, "sentinel": 8}
        assert svc.phenomenon_requirements("andoris")["꽃망울"] == {"bulwark": 15, "support": 4, "sentinel": 4}
        assert svc.phenomenon_requirements("andoris")["꽃"] == {"bulwark": 18, "support": 6, "sentinel": 5}


def test_predecoded_remolding_scoring_matches_repository_scoring(tmp_path):
    import json
    from gfl2tool.models import Remolding, RemoldingSlot
    from gfl2tool.repository import Repository
    from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService

    pieces = [
        Remolding("r2", 985401, "", [
            RemoldingSlot("x", "공격 강화", option_key="sentinel_1", factor_type="sentinel", level_contribution=3),
            RemoldingSlot("y", "단일 특화", option_key="sentinel_3", factor_type="sentinel", level_contribution=2),
        ]),
        Remolding("r1", 985201, "", [
            RemoldingSlot("x", "강공 태세", option_key="vanguard_1", factor_type="vanguard", level_contribution=1),
        ]),
    ]
    with Repository(tmp_path / "predecoded-score.db") as repo:
        repo.replace_remoldings(pieces)
        svc = RemoldingRecommendationService(repo)
        owned = svc.score_owned_remoldings("nemesis")
        decoded = []
        for row in repo.con.execute("SELECT uid,remolding_id,slots_json FROM remoldings"):
            decoded.append({"uid": row["uid"], "remolding_id": row["remolding_id"], "slots": json.loads(row["slots_json"])})
        direct = svc.score_remolding_pieces("nemesis", decoded)
        assert [(x["uid"], x["score"], x["eligible_slots"], x["slots"]) for x in direct] == [
            (x["uid"], x["score"], x["eligible_slots"], x["slots"]) for x in owned
        ]


def test_owned_remolding_scores_are_reused_until_sqlite_changes(tmp_path):
    pieces = [
        Remolding("cache-r1", 985401, "", [
            RemoldingSlot("x", "공격 강화", option_key="sentinel_1", factor_type="sentinel", level_contribution=3),
        ])
    ]
    with Repository(tmp_path / "score-cache.db") as repo:
        repo.replace_remoldings(pieces)
        svc = RemoldingRecommendationService(repo)
        calls = {"count": 0}
        original = svc.score_remolding_pieces

        def counted(character_key, decoded, *, sort_results=True):
            calls["count"] += 1
            return original(character_key, decoded, sort_results=sort_results)

        svc.score_remolding_pieces = counted  # type: ignore[method-assign]
        first = svc.score_owned_remoldings("nemesis")
        second = svc.score_owned_remoldings("nemesis")
        assert first == second
        assert calls["count"] == 1

        # A local scoring write changes Connection.total_changes, invalidating
        # both the per-character score cache and the decoded inventory cache.
        svc.set_override("nemesis", "sentinel_1", score_adjustment=1)
        third = svc.score_owned_remoldings("nemesis")
        assert calls["count"] == 2
        assert third[0]["score"] != first[0]["score"]


def test_remolding_recommendation_small_configuration_reads_are_cached_per_revision(tmp_path):
    with Repository(tmp_path / "remolding-hot-cache.db") as repo:
        svc = RemoldingRecommendationService(repo)
        statements = []
        repo.con.set_trace_callback(statements.append)
        svc.get_score_config(); svc.get_score_config()
        svc.list_overrides("nemesis"); svc.list_overrides("nemesis")
        svc.get_target_profile("nemesis"); svc.get_target_profile("nemesis")
        repo.con.set_trace_callback(None)
        assert sum("SELECT config_json FROM remolding_score_settings" in q for q in statements) == 1
        assert sum("SELECT option_key,score_adjustment,state,note FROM remolding_option_overrides" in q for q in statements) == 1
        assert sum("SELECT targets_json,explicit_empty FROM remolding_target_profiles" in q for q in statements) == 1

        # Any local write advances total_changes and invalidates all three caches.
        svc.set_override("nemesis", "sentinel_1", score_adjustment=2)
        statements = []
        repo.con.set_trace_callback(statements.append)
        svc.get_score_config()
        svc.list_overrides("nemesis")
        svc.get_target_profile("nemesis")
        repo.con.set_trace_callback(None)
        assert any("SELECT config_json FROM remolding_score_settings" in q for q in statements)
        assert any("SELECT option_key,score_adjustment,state,note FROM remolding_option_overrides" in q for q in statements)
        assert any("SELECT targets_json,explicit_empty FROM remolding_target_profiles" in q for q in statements)


def test_lightweight_doll_resolver_maps_favorites_and_level(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.dolls import DollCharacterResolver

    with Repository(tmp_path / "doll-resolver.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 47, 1), Doll(1032, "다이옌", 52, 1)])
        repo.set_doll_favorite(1008, True)
        resolver = DollCharacterResolver(repo)
        assert resolver.character_key_for_doll(1008) == "nemesis"
        assert resolver.character_level_for_key("nemesis") == 47
        assert "nemesis" in resolver.favorite_character_keys()
        repo.set_doll_favorite(1008, False)
        refreshed = DollCharacterResolver(repo)
        assert "nemesis" not in refreshed.favorite_character_keys()


def test_predecoded_scoring_can_cancel_without_changing_default_semantics(tmp_path):
    with Repository(tmp_path / "score-cancel.db") as repo:
        svc = RemoldingRecommendationService(repo)
        pieces = [
            {"uid": str(i), "remolding_id": i, "slots": [{"option_key": "sentinel_1", "level_contribution": 3}]}
            for i in range(40)
        ]
        full = svc.score_remolding_pieces("nemesis", pieces)
        assert len(full) == 40
        cancelled = svc.score_remolding_pieces("nemesis", pieces, should_cancel=lambda: True)
        assert cancelled == []


def _display_piece(uid: str, factor: str, slots: list[tuple[str, int]]):
    return {
        "uid": uid,
        "primary_factor": factor,
        "slots": [
            {"option_key": option_key, "level_contribution": level}
            for option_key, level in slots
        ],
    }


def test_equipped_piece_display_lists_each_physical_remolding_and_roll():
    svc = RemoldingRecommendationService()
    pieces = [
        _display_piece("s1", "sentinel", [("sentinel_1", 3), ("sentinel_11", 2)]),
        _display_piece("s2", "sentinel", [("sentinel_1", 3), ("sentinel_11", 3)]),
        _display_piece("s3", "sentinel", [("sentinel_2", 3), ("sentinel_11", 2)]),
        _display_piece("s4", "sentinel", [("sentinel_3", 3), ("sentinel_11", 2)]),
        _display_piece("v1", "vanguard", [("vanguard_2", 3), ("vanguard_11", 2), ("vanguard_14", 1)]),
        _display_piece("v2", "vanguard", [("vanguard_1", 3), ("vanguard_11", 2)]),
    ]
    summary = svc.equipped_piece_display("nemesis", pieces)
    assert [(group["factor"], group["required_count"]) for group in summary["groups"]] == [
        ("sentinel", 4), ("vanguard", 2)
    ]
    first = summary["groups"][0]["pieces"][0]
    assert [(row["name"], row["level"]) for row in first["stats"]] == [
        ("공격 강화", 3), ("산성 강화", 2)
    ]
    vanguard = summary["groups"][1]["pieces"][0]
    assert [(row["name"], row["level"]) for row in vanguard["stats"]] == [
        ("참수 칼날", 3), ("산성 강타", 2), ("피에 굶주린 칼날", 1)
    ]
    assert summary["assigned"] == 6 and summary["missing"] == 0


def test_equipped_piece_display_keeps_missing_slots_visible_until_auto_assignment():
    svc = RemoldingRecommendationService()
    summary = svc.equipped_piece_display("nemesis", [])
    assert summary["required"] == 6
    assert summary["assigned"] == 0 and summary["missing"] == 6
    assert sum(len(group["pieces"]) for group in summary["groups"]) == 6
    assert all(piece["missing"] for group in summary["groups"] for piece in group["pieces"])


def test_equipped_piece_display_uses_in_game_bulwark_support_sentinel_order():
    svc = RemoldingRecommendationService()
    summary = svc.equipped_piece_display("peri", [])
    assert [group["factor"] for group in summary["groups"]] == ["bulwark", "support", "sentinel"]



def test_phenomenon_level_checkpoints_cover_all_six_stages(tmp_path):
    with Repository(tmp_path / "pheno-checkpoints.db") as repo:
        svc = RemoldingRecommendationService(repo)
        expected = {
            0: "배아",
            10: "떡잎",
            20: "꽃눈",
            30: "꽃봉오리",
            45: "꽃망울",
            60: "꽃",
        }
        assert {level: svc.phenomenon_stage_for_level(level) for level in expected} == expected

        rules = __import__("gfl2tool.reference", fromlist=["remolding_rules"]).remolding_rules()
        stages = tuple(expected.values())
        for character in rules["characters"]:
            key = str(character["key"])
            requirements = svc.phenomenon_requirements(key)
            assert all(stage in requirements for stage in stages), key
