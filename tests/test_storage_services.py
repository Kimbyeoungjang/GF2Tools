import pytest

from gfl2tool.models import Doll, Remolding, RemoldingSlot, GameFormation, FormationMember
from gfl2tool.repository import Repository
from gfl2tool.services.formations import FormationService
from gfl2tool.services.remoldings import RemoldingPatternService
from gfl2tool.snapshot import export_snapshot, import_snapshot
from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService


def test_plans_and_snapshot(tmp_path):
    db = tmp_path / "a.db"
    with Repository(db) as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1)])
        repo.replace_remoldings([Remolding("30", 1, "", [RemoldingSlot("a5 ba 55", "공격강화1")])])
        fs = FormationService(repo)
        pid = fs.create("team")
        fs.set_member(pid, 1, 1032, remolding_uids=["30"])
        rs = RemoldingPatternService(repo)
        rid = rs.create("pattern", 1032)
        rs.set_slot(rid, 1, name="공격강화1", source_uid="30")
        assert rs.matches(rid)[0]["score"] == 1
        remolding_recommendation = RemoldingRecommendationService(repo)
        remolding_recommendation.save_score_config({"grades": {"S": 210, "A": 100, "B": 50, "C": 30, "D": 15, "E": 0, "F": -10}, "multipliers": {"option_weight": 1.25, "base_rank": 1.0, "tag_rank": 1.0}})
        remolding_recommendation.set_override("nemesis", "sentinel_1", score_adjustment=17, note="스냅샷 테스트")
        snap = export_snapshot(repo, tmp_path / "s.json")
    with Repository(tmp_path / "b.db") as repo2:
        import_snapshot(repo2, snap)
        assert FormationService(repo2).get(1)["members"][0]["remolding_uids"] == ["30"]
        remolding_recommendation2 = RemoldingRecommendationService(repo2)
        assert remolding_recommendation2.get_score_config()["multipliers"]["option_weight"] == 1.25
        assert remolding_recommendation2.get_override("nemesis", "sentinel_1")["score_adjustment"] == 17


def test_game_formation_import_preserves_current_doll_layout(tmp_path):
    with Repository(tmp_path / "formation.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1), Doll(1033, "모신나강", 60, 1)])
        repo.replace_game_formations([
            GameFormation("게임 1제대", [
                FormationMember(1032, "다이옌"),
                FormationMember(1033, "모신나강"),
            ])
        ])
        svc = FormationService(repo)
        game_id = svc.list_game_formations()[0]["id"]
        plan_id = svc.import_game_formation(game_id)
        members = svc.get(plan_id)["members"]
        assert len(members) == 2
        assert all(set(m) == {"plan_id", "position", "doll_id", "doll_name", "remolding_uids", "remolding_targets"} for m in members)


def test_game_formation_empty_slots_are_not_imported(tmp_path):
    with Repository(tmp_path / "empty-slots.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1)])
        repo.replace_game_formations([
            GameFormation("게임 2제대", [
                FormationMember(1032, "다이옌"),
                FormationMember(0, None),
            ])
        ])
        svc = FormationService(repo)
        game = svc.list_game_formations()[0]
        assert game["member_names"] == ["다이옌"]
        plan_id = svc.import_game_formation(game["id"])
        plan = svc.get(plan_id)
        assert len(plan["members"]) == 1
        assert plan["members"][0]["position"] == 1


def test_formation_atomic_remolding_apply_allows_swaps_and_is_plan_local(tmp_path):
    with Repository(tmp_path / "swap.db") as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1), Doll(1002, "B", 60, 1)])
        repo.replace_remoldings([
            Remolding("r1", 985401, "", [RemoldingSlot("x", "A")]),
            Remolding("r2", 985402, "", [RemoldingSlot("x", "B")]),
        ])
        svc = FormationService(repo)
        p1 = svc.create("1제대")
        svc.set_member(p1, 1, 1001, remolding_uids=["r1"])
        svc.set_member(p1, 2, 1002, remolding_uids=["r2"])
        svc.apply_remolding_plan(p1, {
            1: {"remolding_uids": ["r2"]}, 2: {"remolding_uids": ["r1"]},
        })
        assert [m["remolding_uids"] for m in svc.get(p1)["members"]] == [["r2"], ["r1"]]

        # A second formation is an independent plan and may reuse the same UID.
        p2 = svc.create("2제대")
        svc.set_member(p2, 1, 1001, remolding_uids=["r1"])
        assert svc.get(p2)["members"][0]["remolding_uids"] == ["r1"]


def test_snapshot_preserves_formation_remoldings(tmp_path):
    from gfl2tool.models import Doll
    db = tmp_path / "r.db"
    with Repository(db) as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1)])
        repo.replace_remoldings([Remolding("r1", 985401, "", [RemoldingSlot("a5 ba 55", "공격강화1", option_key="sentinel_1", factor_type="sentinel")])])
        pid = FormationService(repo).create("R")
        FormationService(repo).set_member(pid, 1, 1001, remolding_uids=["r1"])
        snap = export_snapshot(repo, tmp_path / "r.json")
    with Repository(tmp_path / "r2.db") as repo:
        import_snapshot(repo, snap)
        assert FormationService(repo).get(1)["members"][0]["remolding_uids"] == ["r1"]










def test_doll_favorite_survives_account_replace_and_snapshot(tmp_path):
    import json
    from gfl2tool.models import Doll
    from gfl2tool.snapshot import SNAPSHOT_VERSION

    with Repository(tmp_path / "favorite.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1), Doll(1032, "다이옌", 60, 1)])
        assert repo.set_doll_favorite(1008, True) is True
        assert repo.is_doll_favorite(1008) is True
        assert repo.is_doll_favorite(1032) is False

        # A fresh account sync replaces owned rows but local UI preferences stay.
        repo.replace_dolls([Doll(1008, "네메시스", 60, 2), Doll(1032, "다이옌", 60, 1)])
        assert repo.is_doll_favorite(1008) is True
        assert repo.is_doll_favorite(1032) is False
        snap = export_snapshot(repo, tmp_path / "favorite.json")
        payload = json.loads(snap.read_text(encoding="utf-8"))
        assert payload["snapshot_version"] == SNAPSHOT_VERSION

    with Repository(tmp_path / "favorite-import.db") as restored:
        import_snapshot(restored, snap)
        assert restored.is_doll_favorite(1008) is True
        assert restored.is_doll_favorite(1032) is False


def test_snapshot_preserves_remolding_recommendation_targets_and_character_profiles(tmp_path):
    source = tmp_path / "profiles.db"
    with Repository(source) as repo:
        svc = RemoldingRecommendationService(repo)
        saved_target = svc.save_target_profile("nemesis", {"sentinel_1": {"level": 3, "weight": 175, "priority": 1}})
        assert saved_target
        svc.save_character_profile(
            "nemesis",
            slot_counts={"sentinel": 3, "vanguard": 3, "bulwark": 0, "support": 0},
            level_override=55,
        )
        snap = export_snapshot(repo, tmp_path / "profiles.json")

    with Repository(tmp_path / "profiles-restored.db") as restored:
        import_snapshot(restored, snap)
        svc = RemoldingRecommendationService(restored)
        assert svc.get_target_profile("nemesis", with_default=False)["sentinel_1"]["level"] == 3
        char = svc.get_character("nemesis")
        assert char["levelOverride"] == 55
        assert {x["factorType"]: x["count"] for x in char["slotDistribution"]} == {"sentinel": 3, "vanguard": 3}


def test_remolding_batch_lookup_preserves_requested_order_and_duplicates(tmp_path):
    from gfl2tool.models import Remolding, RemoldingSlot

    with Repository(tmp_path / "batch-remoldings.db") as repo:
        repo.replace_remoldings([
            Remolding("a", 985401, "", [RemoldingSlot("x", "공격 강화", option_key="sentinel_1", factor_type="sentinel")]),
            Remolding("b", 985201, "", [RemoldingSlot("x", "강공 태세", option_key="vanguard_1", factor_type="vanguard")]),
        ])
        rows = repo.remoldings_by_uids(["b", "missing", "a", "b"])
        assert [row["uid"] for row in rows] == ["b", "a", "b"]


def test_formation_list_includes_member_count_without_n_plus_one(tmp_path):
    from gfl2tool.models import Doll
    from gfl2tool.services.formations import FormationService

    with Repository(tmp_path / "formation-count.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        svc = FormationService(repo)
        pid = svc.create("카운트 테스트")
        svc.set_member(pid, 1, 1008)
        rows = svc.list()
        row = next(item for item in rows if int(item["id"]) == pid)
        assert int(row["member_count"]) == 1


def test_remolding_inventory_projection_omits_raw_encoded_hex(tmp_path):
    with Repository(tmp_path / "projection.db") as repo:
        repo.replace_remoldings([
            Remolding("compact-r", 985401, "ab" * 4096, [RemoldingSlot("x", "공격 강화")])
        ])
        rows = repo.remolding_inventory_rows()
        assert len(rows) == 1
        assert rows[0]["uid"] == "compact-r"
        assert "slots_json" in rows[0]
        assert "raw_contents_hex" not in rows[0]


def test_game_formation_listing_prefetches_doll_names(tmp_path):
    from gfl2tool.models import Doll
    with Repository(tmp_path / "game-formation-prefetch.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1), Doll(1033, "모신나강", 60, 1)])
        repo.replace_game_formations([
            GameFormation("A", [FormationMember(1032, None), FormationMember(1033, None)]),
            GameFormation("B", [FormationMember(1033, None)]),
        ])
        statements = []
        repo.con.set_trace_callback(statements.append)
        rows = FormationService(repo).list_game_formations()
        repo.con.set_trace_callback(None)
        assert [r["member_names"] for r in rows] == [["다이옌", "모신나강"], ["모신나강"]]
        doll_selects = [q for q in statements if "SELECT doll_id,name FROM dolls" in q]
        assert len(doll_selects) == 1
        assert not any("WHERE doll_id=" in q for q in statements)


def test_formation_apply_rejects_incomplete_optimizer_result_without_mutation(tmp_path):
    from gfl2tool.models import Doll
    import pytest

    with Repository(tmp_path / "incomplete-optimizer.db") as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1), Doll(1002, "B", 60, 1)])
        repo.replace_remoldings([
            Remolding("r1", 985401, "", [RemoldingSlot("a5 ba 55", "공격강화1")]),
            Remolding("r2", 985402, "", [RemoldingSlot("a5 ba 56", "공격강화2")]),
        ])
        svc = FormationService(repo)
        pid = svc.create("incomplete")
        svc.set_member(pid, 1, 1001, remolding_uids=["r1"])
        svc.set_member(pid, 2, 1002, remolding_uids=["r2"])
        before = svc.get(pid)

        with pytest.raises(ValueError, match="2번 슬롯의 자동 배치 결과가 없습니다"):
            svc.apply_remolding_plan(
                pid,
                {1: {"remolding_uids": ["r2"]}},
            )

        assert svc.get(pid) == before


def test_snapshot_rejects_incomplete_current_payload_before_replace(tmp_path):
    import json
    from gfl2tool.models import Doll

    bad = tmp_path / "bad-snapshot.json"
    from gfl2tool.snapshot import SNAPSHOT_SCHEMA_ID, SNAPSHOT_VERSION
    bad.write_text(
        json.dumps({"schema_id": SNAPSHOT_SCHEMA_ID, "snapshot_version": SNAPSHOT_VERSION, "data": {}}),
        encoding="utf-8",
    )
    with Repository(tmp_path / "snapshot-guard.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        try:
            import_snapshot(repo, bad, replace=True)
        except ValueError as exc:
            assert "incomplete" in str(exc)
        else:
            raise AssertionError("incomplete current snapshot was accepted")
        assert repo.con.execute("SELECT name FROM dolls WHERE doll_id=1008").fetchone()[0] == "네메시스"


def test_snapshot_rejects_unknown_columns_before_replace(tmp_path):
    import json
    from gfl2tool.models import Doll

    with Repository(tmp_path / "snapshot-source.db") as source:
        source.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        snap = export_snapshot(source, tmp_path / "snapshot.json")
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["data"]["dolls"][0]["name) VALUES ('corrupt'); --"] = "x"
    snap.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with Repository(tmp_path / "snapshot-target.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1)])
        try:
            import_snapshot(repo, snap, replace=True)
        except ValueError as exc:
            assert "does not match current columns" in str(exc)
        else:
            raise AssertionError("snapshot with unknown identifiers was accepted")
        rows = repo.con.execute("SELECT doll_id,name FROM dolls ORDER BY doll_id").fetchall()
        assert [(row[0], row[1]) for row in rows] == [(1032, "다이옌")]


def test_snapshot_rejects_non_array_table_before_replace(tmp_path):
    import json
    from gfl2tool.models import Doll

    with Repository(tmp_path / "snapshot-shape-source.db") as source:
        snap = export_snapshot(source, tmp_path / "shape.json")
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["data"]["dolls"] = {"doll_id": 1008}
    snap.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with Repository(tmp_path / "snapshot-shape-target.db") as repo:
        repo.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        try:
            import_snapshot(repo, snap, replace=True)
        except ValueError as exc:
            assert "must be an array" in str(exc)
        else:
            raise AssertionError("snapshot table object was accepted")
        assert repo.con.execute("SELECT COUNT(*) FROM dolls").fetchone()[0] == 1


def test_snapshot_rejects_missing_current_columns_before_replace(tmp_path):
    import json
    from gfl2tool.models import Doll

    with Repository(tmp_path / "snapshot-source-missing.db") as source:
        source.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        snap = export_snapshot(source, tmp_path / "snapshot-missing.json")
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["data"]["dolls"][0].pop("rank")
    snap.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with Repository(tmp_path / "snapshot-target-missing.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1)])
        try:
            import_snapshot(repo, snap, replace=True)
        except ValueError as exc:
            assert "missing=rank" in str(exc)
        else:
            raise AssertionError("snapshot missing a current column was accepted")
        assert repo.con.execute("SELECT name FROM dolls WHERE doll_id=1032").fetchone()[0] == "다이옌"


def test_snapshot_rejects_invalid_current_nested_json_shape_before_replace(tmp_path):
    import json
    from gfl2tool.models import Doll

    with Repository(tmp_path / "nested-source.db") as source:
        source.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        snap = export_snapshot(source, tmp_path / "nested.json")
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["data"]["formation_members"] = [{
        "plan_id": 1,
        "position": 1,
        "doll_id": 1008,
        "doll_name": "네메시스",
        "remolding_uids": "r1,r2",
        "remolding_targets": {},
    }]
    snap.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with Repository(tmp_path / "nested-target.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1)])
        with pytest.raises(ValueError, match="remolding_uids.*must be list"):
            import_snapshot(repo, snap, replace=True)
        assert repo.con.execute("SELECT name FROM dolls WHERE doll_id=1032").fetchone()[0] == "다이옌"


def test_snapshot_rejects_invalid_current_scalar_type_before_replace(tmp_path):
    import json

    with Repository(tmp_path / "scalar-source.db") as source:
        source.replace_dolls([Doll(1008, "네메시스", 60, 1)])
        snap = export_snapshot(source, tmp_path / "scalar.json")
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["data"]["dolls"][0]["level"] = "sixty"
    snap.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with Repository(tmp_path / "scalar-target.db") as repo:
        repo.replace_dolls([Doll(1032, "다이옌", 60, 1)])
        with pytest.raises(ValueError, match="level.*declared type INTEGER"):
            import_snapshot(repo, snap, replace=True)
        assert repo.con.execute("SELECT name FROM dolls WHERE doll_id=1032").fetchone()[0] == "다이옌"


def test_formation_supports_six_slots_and_rejects_seventh(tmp_path):
    repo = Repository(tmp_path / "six-member.db")
    try:
        repo.replace_dolls([Doll(1000 + i, f"Doll {i}", 60, 1) for i in range(1, 8)])
        service = FormationService(repo)
        plan_id = service.create("6인 제대")
        for position in range(1, 7):
            service.set_member(plan_id, position, 1000 + position)
        plan = service.get(plan_id)
        assert service.MAX_MEMBERS == 6
        assert [member["position"] for member in plan["members"]] == [1, 2, 3, 4, 5, 6]
        with pytest.raises(ValueError, match="1~6"):
            service.set_member(plan_id, 7, 1007)
    finally:
        repo.close()


def test_delete_remolding_detaches_formation_and_pattern_source(tmp_path):
    from gfl2tool.models import Doll, Remolding, RemoldingSlot
    from gfl2tool.repository import Repository
    from gfl2tool.services.formations import FormationService
    from gfl2tool.services.remoldings import RemoldingPatternService

    with Repository(tmp_path / "delete-remolding.db") as repo:
        repo.replace_dolls([Doll(1001, "A", 60, 1)])
        repo.replace_remoldings([
            Remolding("r1", 985401, "", [RemoldingSlot("a5 ba 55", "공격강화1", option_key="sentinel_1", factor_type="sentinel")]),
            Remolding("r2", 985402, "", [RemoldingSlot("a5 ba 55", "공격강화1", option_key="sentinel_1", factor_type="sentinel")]),
        ])
        formations = FormationService(repo)
        plan = formations.create("team")
        formations.set_member(plan, 1, 1001, remolding_uids=["r1", "r2"])
        patterns = RemoldingPatternService(repo)
        pattern = patterns.create("P")
        patterns.set_slot(pattern, 1, option_key="sentinel_1", source_uid="r1")

        assert repo.delete_remolding("r1") is True
        assert repo.delete_remolding("r1") is False
        assert repo.con.execute("SELECT COUNT(*) FROM remoldings WHERE uid='r1'").fetchone()[0] == 0
        member = repo.con.execute(
            "SELECT remolding_uids_json FROM formation_members WHERE plan_id=? AND position=1", (plan,)
        ).fetchone()[0]
        assert __import__("json").loads(member) == ["r2"]
        source = repo.con.execute(
            "SELECT source_remolding_uid FROM remolding_pattern_slots WHERE pattern_id=? AND slot_index=1", (pattern,)
        ).fetchone()[0]
        assert source is None
