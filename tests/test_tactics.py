from __future__ import annotations

import json

from gfl2tool.tactics import (
    Tactic,
    TacticMarker,
    TacticStep,
    TacticStore,
    decode_tactic_share,
    encode_tactic_share,
)


def test_tactic_share_round_trip_keeps_steps_markers_and_generates_new_id():
    tactic = Tactic(
        title="흙먼지 테스트",
        category="드앰마로흑",
        rows=11,
        cols=13,
        show_previous=True,
        steps=[
            TacticStep(
                name="T1",
                note="왼쪽으로 이동",
                markers=[
                    TacticMarker(kind="unit", row=3, col=4, label="마"),
                    TacticMarker(kind="boss", row=5, col=6, width=3, height=3, label="보스"),
                    TacticMarker(kind="arrow", row=3, col=4, to_row=4, to_col=6),
                ],
            ),
            TacticStep(name="T2", note="대기"),
        ],
    )
    code = encode_tactic_share(tactic)
    restored = decode_tactic_share(code)
    assert code.startswith("GFL2T:1:")
    assert restored.tactic_id != tactic.tactic_id
    assert restored.title == tactic.title
    assert restored.category == "드앰마로흑"
    assert restored.rows == 11 and restored.cols == 13
    assert restored.show_previous is True
    assert restored.steps[0].markers[2].kind == "arrow"
    assert restored.steps[0].markers[2].to_col == 6


def test_tactic_from_dict_clamps_untrusted_grid_and_marker_bounds():
    tactic = Tactic.from_dict({
        "schema": 1,
        "rows": 999,
        "cols": -20,
        "steps": [{
            "markers": [
                {"kind": "boss", "row": 999, "col": 999, "width": 999, "height": 999},
                {"kind": "arrow", "row": -3, "col": -4, "to_row": 999, "to_col": 999},
                {"kind": "unknown", "row": 1, "col": 1},
            ],
        }],
    })
    assert tactic.rows == 24
    assert tactic.cols == 4
    assert len(tactic.steps[0].markers) == 2
    boss, arrow = tactic.steps[0].markers
    assert (boss.row, boss.col, boss.width, boss.height) == (23, 3, 1, 1)
    assert (arrow.row, arrow.col, arrow.to_row, arrow.to_col) == (0, 0, 23, 3)


def test_tactic_store_round_trip_and_duplicate_ids_are_repaired(tmp_path):
    store = TacticStore(tmp_path)
    first = Tactic(title="A")
    second = Tactic.from_dict(first.to_dict(), preserve_id=True)
    store.save([first, second])
    loaded = store.load()
    assert [item.title for item in loaded] == ["A", "A"]
    assert loaded[0].tactic_id != loaded[1].tactic_id
    payload = json.loads(store.library_path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1


def test_tactic_share_rejects_trailing_or_truncated_compressed_stream():
    tactic = Tactic(title="strict")
    code = encode_tactic_share(tactic)
    import pytest

    with pytest.raises(ValueError):
        decode_tactic_share(code + "AAAA")
    with pytest.raises(ValueError):
        decode_tactic_share(code[:-2])


def test_empty_cover_marker_is_not_materialized():
    tactic = Tactic.from_dict({"schema": 1, "steps": [{"markers": [{"kind": "cover", "row": 1, "col": 1, "edges": ""}]}]})
    assert tactic.steps[0].markers == []


def test_tactic_store_caps_untrusted_library_count(tmp_path):
    from gfl2tool.tactics import MAX_TACTICS

    store = TacticStore(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "tactics": [Tactic(title=f"T{i}").to_dict() for i in range(MAX_TACTICS + 5)],
    }
    store.library_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = store.load()
    assert len(loaded) == MAX_TACTICS


def test_tactic_store_rejects_in_memory_data_that_would_be_truncated(tmp_path):
    import pytest

    from gfl2tool.tactics import MAX_TACTICS

    store = TacticStore(tmp_path)
    with pytest.raises(ValueError):
        store.save([Tactic(title=f"T{i}") for i in range(MAX_TACTICS + 1)])


def test_tactic_units_alias_and_cycle_survive_share_round_trip():
    from gfl2tool.tactics import TacticUnit

    unit = TacticUnit(doll_id=1001, name="마키아토")
    tactic = Tactic(
        title="인형 연동",
        units=[unit],
        steps=[
            TacticStep(
                name="T1",
                cycle="로 1(마)2 · 드 2 · 마 31",
                markers=[TacticMarker(kind="unit", row=2, col=3, label="?", unit_key=unit.unit_key)],
            )
        ],
    )
    restored = decode_tactic_share(encode_tactic_share(tactic))
    assert restored.steps[0].cycle == "로 1(마)2 · 드 2 · 마 31"
    assert restored.units[0].name == "마키아토"
    marker = restored.steps[0].markers[0]
    assert restored.marker_label(marker) == "마"
    restored.units[0].alias = "맥"
    assert restored.marker_label(marker) == "맥"


def test_tactic_store_rejects_too_many_roster_units(tmp_path):
    import pytest
    from gfl2tool.tactics import MAX_TACTIC_UNITS, TacticUnit

    tactic = Tactic(units=[TacticUnit(name=f"D{i}") for i in range(MAX_TACTIC_UNITS + 1)])
    with pytest.raises(ValueError):
        TacticStore(tmp_path).save([tactic])


def test_tactic_unit_build_fields_survive_share_round_trip():
    from gfl2tool.tactics import TacticUnit

    tactic = Tactic(
        title="세팅 공유",
        units=[
            TacticUnit(
                doll_id=1001,
                name="마키아토",
                alias="마",
                rank=6,
                weapon="비터 캐러멜",
                common_keys=["공용키 A", "공용키 B", "공용키 C"],
                unique_keys=["고유키 A", "고유키 B", "고유키 C"],
                expansion_level=2,
            )
        ],
    )
    restored = decode_tactic_share(encode_tactic_share(tactic))
    unit = restored.units[0]
    assert unit.rank == 6
    assert unit.weapon == "비터 캐러멜"
    assert unit.common_keys == ["공용키 A", "공용키 B", "공용키 C"]
    assert unit.unique_keys == ["고유키 A", "고유키 B", "고유키 C"]
    assert unit.expansion_level == 2
    assert unit.expansion_label() == "도약키 2단계"


def test_tactic_store_supports_completely_empty_library(tmp_path):
    store = TacticStore(tmp_path)
    store.save([])
    assert store.load() == []
    payload = json.loads(store.library_path.read_text(encoding="utf-8"))
    assert payload["tactics"] == []
