from __future__ import annotations

from PIL import Image, ImageDraw

from gfl2tool.tactic_image_import import import_tactic_image


def _sample_board(path):
    image = Image.new("RGB", (360, 360), "white")
    draw = ImageDraw.Draw(image)
    left = top = 30
    size = 300
    cells = 6
    step = size // cells
    grid = (145, 145, 145)
    for index in range(cells + 1):
        x = left + index * step
        y = top + index * step
        draw.line((x, top, x, top + size), fill=grid, width=2)
        draw.line((left, y, left + size, y), fill=grid, width=2)

    # 2x2 boss area.
    draw.rectangle((left + step * 2 + 2, top + step * 2 + 2, left + step * 4 - 2, top + step * 4 - 2), fill=(244, 125, 42))
    # One impassable cell.
    draw.rectangle((left + step + 8, top + step * 4 + 8, left + step * 2 - 8, top + step * 5 - 8), fill=(10, 10, 10))
    # North cover edge in one cell.
    y = top + step
    draw.rectangle((left + step * 4 + 4, y + 2, left + step * 5 - 4, y + 10), fill=(150, 150, 150))
    image.save(path)


def test_image_import_detects_grid_boss_and_terrain(tmp_path):
    path = tmp_path / "tactic.png"
    _sample_board(path)
    result = import_tactic_image(path)

    assert len(result.boards) == 1
    board = result.boards[0]
    assert (board.rows, board.cols) == (6, 6)
    assert board.confidence > 0.8
    kinds = [marker.kind for marker in board.markers]
    assert "boss" in kinds
    assert "blocked" in kinds
    assert "cover" in kinds
    assert result.tactic.grid_size(0) == (6, 6)


def test_tactic_step_grid_size_and_cover_survive_share_round_trip():
    from gfl2tool.tactics import Tactic, TacticMarker, TacticStep, decode_tactic_share, encode_tactic_share

    tactic = Tactic(
        rows=12,
        cols=12,
        steps=[
            TacticStep(
                name="T1",
                rows=9,
                cols=10,
                markers=[TacticMarker(kind="cover", row=3, col=4, edges="NE")],
            )
        ],
    )
    restored = decode_tactic_share(encode_tactic_share(tactic))
    assert restored.grid_size(0) == (9, 10)
    assert restored.steps[0].markers[0].kind == "cover"
    assert restored.steps[0].markers[0].edges == "NE"


def test_image_import_keeps_large_leading_formation_board_and_renumbers_combat_steps():
    from gfl2tool.tactic_image_import import (
        DetectedBoard, TacticImageImportResult, formation_board_indexes, suggested_board_indexes,
    )
    from gfl2tool.tactics import Tactic, TacticStep

    boards = (
        DetectedBoard((0, 0, 400, 400), 14, 14, 0.95),
        DetectedBoard((410, 0, 400, 400), 11, 11, 0.96),
        DetectedBoard((820, 0, 400, 400), 9, 9, 0.97),
        DetectedBoard((0, 410, 400, 400), 9, 9, 0.98),
    )
    result = TacticImageImportResult(
        Tactic(steps=[
            TacticStep(name="제대 배치", cycle="", rows=14, cols=14),
            TacticStep(name="T1", cycle="cycle-1", rows=11, cols=11),
            TacticStep(name="T2", cycle="cycle-2", rows=9, cols=9),
            TacticStep(name="T3", cycle="cycle-3", rows=9, cols=9),
        ]),
        boards,
    )
    assert formation_board_indexes(boards) == (0,)
    assert suggested_board_indexes(boards) == (0, 1, 2, 3)
    selected = result.selected_tactic((0, 1, 2, 3))
    assert [step.name for step in selected.steps] == ["제대 배치", "T1", "T2", "T3"]
    assert [selected.grid_size(i) for i in range(4)] == [(14, 14), (11, 11), (9, 9), (9, 9)]
    assert [step.cycle for step in selected.steps] == ["", "cycle-1", "cycle-2", "cycle-3"]


def test_detection_box_mapping_keeps_original_image_coordinates():
    from gfl2tool.tactic_image_import import _box_to_original

    assert _box_to_original((110, 220, 550, 660), (1100, 2200), (2200, 4400)) == (220, 440, 1100, 1320)


def test_skill_cycle_detection_uses_detected_band_and_segmented_cells(tmp_path, monkeypatch):
    from gfl2tool import tactic_image_import as module
    from gfl2tool.tactic_image_import import DetectedBoard, detect_skill_cycles

    image = Image.new("RGB", (240, 220), "white")
    path = tmp_path / "sheet.png"
    image.save(path)
    boards = (DetectedBoard((20, 20, 160, 120), 6, 6, 1.0),)
    band = Image.new("RGB", (160, 38), "white")
    seen = []

    monkeypatch.setattr(module, "_tesseract_executable", lambda: "/fake/tesseract")
    monkeypatch.setattr(module, "_cycle_band", lambda _source, _board: band)
    monkeypatch.setattr(
        module,
        "_segmented_cycle_text",
        lambda _exe, crop, **_kwargs: seen.append(crop.size) or "마 31 · 엘 23",
    )
    cycles, available = detect_skill_cycles(path, boards)

    assert available is True
    assert cycles == ("마 31 · 엘 23",)
    assert seen == [(160, 38)]


def test_unit_label_consensus_repairs_single_outlier_and_masks_extra_annotation():
    from gfl2tool.tactic_image_import import DetectedBoard, _refine_unit_label_consensus
    from gfl2tool.tactics import TacticMarker

    def board(labels):
        return DetectedBoard(
            (0, 0, 180, 180),
            9,
            9,
            1.0,
            tuple(TacticMarker(kind="unit", row=1, col=i, label=label) for i, label in enumerate(labels)),
        )

    boards = (
        board(["마", "로", "앨", "드", "흑"]),
        board(["마", "로", "앨", "드", "흑"]),
        board(["마", "로", "앨", "드", "흑"]),
        board(["마", "로", "앨", "드", "별", "흑"]),
        board(["마", "로", "앨", "흑", "X"]),
    )
    refined = _refine_unit_label_consensus(boards)
    assert [m.label for m in refined[3].markers] == ["마", "로", "앨", "드", "*", "흑"]
    assert refined[3].markers[4].kind == "summon"
    assert [m.label for m in refined[4].markers] == ["마", "로", "앨", "흑", "드"]


def test_cycle_alias_normalization_uses_repeated_roster_conservatively():
    from gfl2tool.tactic_image_import import _normalize_cycle_aliases

    roster = ("마", "로", "앨", "드", "흑")
    assert _normalize_cycle_aliases("도 2 · 마 31 · 1(흑) / 도 평3", roster) == "로 2 · 마 31 · 1(흑) / 로 평3"
    assert _normalize_cycle_aliases("스킬 사용", roster) == "스킬 사용"


def test_cycle_cell_cleanup_repairs_wrapped_parenthetical_and_numeric_suffix():
    from gfl2tool.tactic_image_import import _clean_cycle_cell_value

    assert _clean_cycle_cell_value("1(흑)\n로 평3") == "로 1(흑)평3"
    assert _clean_cycle_cell_value(". 2344\nㄱ- 4") == "23444"
    assert _clean_cycle_cell_value("ㅠ 2344") == "2344"


def test_summon_and_custom_markers_survive_share_round_trip():
    from gfl2tool.tactics import Tactic, TacticMarker, TacticStep, decode_tactic_share, encode_tactic_share

    tactic = Tactic(
        rows=9,
        cols=9,
        steps=[TacticStep(name="T1", markers=[
            TacticMarker(kind="summon", row=2, col=3, label="*"),
            TacticMarker(kind="custom", row=4, col=5, label="기믹A"),
        ])],
    )
    restored = decode_tactic_share(encode_tactic_share(tactic))
    assert [(m.kind, m.label, m.row, m.col) for m in restored.steps[0].markers] == [
        ("summon", "*", 2, 3),
        ("custom", "기믹A", 4, 5),
    ]


def test_cycle_cell_cleanup_repairs_parenthetical_star_misread():
    from gfl2tool.tactic_image_import import _clean_cycle_cell_value

    assert _clean_cycle_cell_value("로 3(0)1") == "로 3(*)1"
    assert _clean_cycle_cell_value("로 3(O)1") == "로 3(*)1"
    assert _clean_cycle_cell_value("로 3(°)1") == "로 3(*)1"


def test_cycle_cell_cleanup_preserves_noop_x_and_wrapped_skill_digits():
    from gfl2tool.tactic_image_import import _clean_cycle_cell_value

    assert _clean_cycle_cell_value("미 X") == "미X"
    assert _clean_cycle_cell_value("미×") == "미X"
    assert _clean_cycle_cell_value("린 고(미)\n32") == "린 고(미)32"


def test_selected_tactic_respects_reviewed_formation_flags():
    from gfl2tool.tactic_image_import import DetectedBoard, TacticImageImportResult
    from gfl2tool.tactics import Tactic, TacticStep

    result = TacticImageImportResult(
        tactic=Tactic(steps=[
            TacticStep(name="T1", cycle="a", rows=9, cols=9),
            TacticStep(name="T2", cycle="b", rows=9, cols=9),
            TacticStep(name="T3", cycle="c", rows=9, cols=9),
        ]),
        boards=(
            DetectedBoard((0, 0, 100, 100), 9, 9, 1.0),
            DetectedBoard((110, 0, 100, 100), 9, 9, 1.0),
            DetectedBoard((220, 0, 100, 100), 9, 9, 1.0),
        ),
    )
    tactic = result.selected_tactic((0, 1, 2), formation_indexes={1})
    assert [step.name for step in tactic.steps] == ["T1", "제대 배치", "T2"]
    assert [step.cycle for step in tactic.steps] == ["a", "", "c"]


def test_unit_cell_ocr_stops_after_matching_primary_votes(monkeypatch):
    from gfl2tool import tactic_image_import as module
    from gfl2tool.tactic_image_import import DetectedBoard

    calls = []
    monkeypatch.setattr(
        module,
        "_run_single_cell_ocr",
        lambda _exe, _image, *, psm: calls.append(psm) or "마",
    )
    source = Image.new("RGB", (120, 120), "white")
    board = DetectedBoard((0, 0, 120, 120), 1, 1, 1.0)
    assert module._ocr_unit_cell("fake", source, board, 0, 0) == "마"
    assert calls == [10, 13]


def test_unit_cell_ocr_uses_threshold_votes_when_primary_votes_disagree(monkeypatch):
    from gfl2tool import tactic_image_import as module
    from gfl2tool.tactic_image_import import DetectedBoard

    answers = iter(["마", "로", "마", "마", "로", "마"])
    calls = []

    def fake(_exe, _image, *, psm):
        calls.append(psm)
        return next(answers)

    monkeypatch.setattr(module, "_run_single_cell_ocr", fake)
    monkeypatch.setattr(module, "_known_unit_initials", lambda: frozenset({"마", "로"}))
    source = Image.new("RGB", (120, 120), "white")
    board = DetectedBoard((0, 0, 120, 120), 1, 1, 1.0)
    assert module._ocr_unit_cell("fake", source, board, 0, 0) == "마"
    assert calls == [10, 13, 10, 13, 10, 13]


def test_tactic_ocr_reuses_shared_tesseract_locator(monkeypatch):
    from gfl2tool import tactic_image_import as module

    monkeypatch.setattr(module, "find_tesseract", lambda: "/runtime/ocr/tesseract.exe")
    assert module._tesseract_executable() == "/runtime/ocr/tesseract.exe"


def test_confident_board_ocr_skips_noisier_cell_fallback(tmp_path, monkeypatch):
    from gfl2tool import tactic_image_import as module
    from gfl2tool.tactic_image_import import DetectedBoard
    from gfl2tool.tactics import TacticMarker

    path = tmp_path / "board.png"
    Image.new("RGB", (120, 120), "white").save(path)
    board = DetectedBoard(
        (0, 0, 120, 120), 1, 1, 1.0,
        (TacticMarker(kind="unit", row=0, col=0, label="?"),),
    )
    monkeypatch.setattr(module, "_tesseract_executable", lambda: "/fake/tesseract")
    monkeypatch.setattr(module, "_known_unit_initials", lambda: frozenset({"마"}))
    monkeypatch.setattr(module, "_ocr_unit_board_labels", lambda *_args: {(0, 0): ("마", 90.0)})
    monkeypatch.setattr(module, "_ocr_unit_cell", lambda *_args: (_ for _ in ()).throw(AssertionError("cell fallback should not run")))

    rows, available = module.detect_unit_labels(path, (board,))
    assert available is True
    assert rows[0].markers[0].label == "마"


def test_low_confidence_board_ocr_gets_conservative_second_vote(monkeypatch):
    from PIL import Image
    import gfl2tool.tactic_image_import as mod

    board = mod.DetectedBoard(box=(0, 0, 100, 100), rows=1, cols=1, confidence=1.0, markers=())
    calls = []

    def fake_rows(_exe, _image, *, psm=11):
        calls.append(psm)
        if psm == 11:
            return [("센", 40.0, 20, 20, 20, 20)]
        return [("토", 80.0, 20, 20, 20, 20)]

    monkeypatch.setattr(mod, "_known_unit_initials", lambda: frozenset({"센", "토"}))
    monkeypatch.setattr(mod, "_run_unit_board_tsv", fake_rows)
    result = mod._ocr_unit_board_labels("tesseract", Image.new("RGB", (100, 100), "white"), board)
    assert calls == [11, 6]
    assert result[(0, 0)][0] == "토"


def test_high_confidence_board_ocr_skips_secondary_vote(monkeypatch):
    from PIL import Image
    import gfl2tool.tactic_image_import as mod

    board = mod.DetectedBoard(box=(0, 0, 100, 100), rows=2, cols=2, confidence=1.0, markers=())
    rows = [
        ("센", 90.0, 120, 120, 40, 40),
        ("토", 90.0, 420, 120, 40, 40),
        ("마", 90.0, 120, 420, 40, 40),
        ("린", 90.0, 420, 420, 40, 40),
    ]
    calls = []

    def fake_rows(_exe, _image, *, psm=11):
        calls.append(psm)
        return rows

    monkeypatch.setattr(mod, "_known_unit_initials", lambda: frozenset({"센", "토", "마", "린"}))
    monkeypatch.setattr(mod, "_run_unit_board_tsv", fake_rows)
    result = mod._ocr_unit_board_labels("tesseract", Image.new("RGB", (100, 100), "white"), board)
    assert calls == [11]
    assert len(result) == 4
