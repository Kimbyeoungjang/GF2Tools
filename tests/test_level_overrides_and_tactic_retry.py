from __future__ import annotations

from pathlib import Path

from PIL import Image

from gfl2tool.services.dolls import DollCharacterResolver
from gfl2tool.tactic_image_import import DetectedBoard, TacticImageImportResult, reimport_tactic_region
from gfl2tool.tactics import Tactic, TacticMarker, TacticStep


class _FakeRecommendation:
    def __init__(self):
        self.base_characters = {"char": {"nameKR": "테스트"}}
        self.characters = {"char": {"nameKR": "테스트"}}

    def get_character(self, key: str):
        if key not in self.characters:
            raise ValueError(key)
        return dict(self.characters[key])


def test_calculation_level_priority_individual_then_global_default_60():
    recommendation = _FakeRecommendation()
    resolver = DollCharacterResolver(
        object(),
        recommendation=recommendation,
        owned_doll_rows={1: {"doll_id": 1, "name": "테스트", "level": 37, "favorite": 0}},
        master={},
        master_loaded=True,
    )
    resolver.character_key_for_doll = lambda _doll_id: "char"  # type: ignore[method-assign]

    assert resolver.owned_character_level_for_key("char") == 37
    assert resolver.calculation_level_for_key("char") == 60
    assert resolver.calculation_level_for_key("char", 45) == 45

    recommendation.characters["char"]["levelOverride"] = 52
    assert resolver.calculation_level_for_key("char") == 52
    assert resolver.calculation_level_for_key("char", 45) == 52


def test_reimport_tactic_region_translates_selected_candidate(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (500, 400), "white").save(source)

    board_a = DetectedBoard(
        box=(12, 16, 180, 180), rows=6, cols=6, confidence=0.93,
        markers=(TacticMarker(kind="unit", row=1, col=2, label="가"),),
    )
    board_b = DetectedBoard(
        box=(40, 40, 220, 220), rows=9, cols=9, confidence=0.70,
        markers=(TacticMarker(kind="unit", row=3, col=4, label="나"),),
    )
    local_tactic = Tactic(
        title="retry",
        rows=6,
        cols=6,
        steps=[
            TacticStep(name="T1", rows=6, cols=6, markers=[TacticMarker(kind="unit", row=1, col=2, label="가")]),
            TacticStep(name="T2", rows=9, cols=9, markers=[TacticMarker(kind="unit", row=3, col=4, label="나")]),
        ],
    )

    def fake_import(_path, *, title=None, progress=None):
        if progress:
            progress("테스트 OCR")
        return TacticImageImportResult(local_tactic, (board_a, board_b), ())

    monkeypatch.setattr("gfl2tool.tactic_image_import.import_tactic_image", fake_import)
    result = reimport_tactic_region(
        source,
        (100, 80, 300, 280),
        expected_rows=9,
        expected_cols=9,
    )

    assert len(result.boards) == 1
    selected = result.boards[0]
    assert (selected.rows, selected.cols) == (9, 9)
    assert selected.box == (140, 120, 220, 220)
    assert result.tactic.steps[0].markers[0].label == "나"
