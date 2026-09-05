from __future__ import annotations

from pathlib import Path

from PIL import Image

from gfl2tool.tactic_image_import import (
    DetectedBoard,
    _board_ocr_crop,
    _is_export_arrow_pixel,
)
from gfl2tool.tactics import TacticMarker


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_uses_press_toggle_and_relaxed_save_debounce() -> None:
    source = (ROOT / "src/gfl2tool/qtui/pages/dashboard.py").read_text(encoding="utf-8")
    assert "class _ImmediateCheckBox(QCheckBox)" in source
    assert "def mousePressEvent" in source
    assert "self.setChecked(not self.isChecked())" in source
    assert "self._checklist_save_timer.setInterval(1200)" in source
    assert "if self._checklist_dirty and self._checklist_payload is not None" in source


def test_overlay_resize_grip_has_larger_hit_area() -> None:
    source = (ROOT / "src/gfl2tool/qtui/tactic_overlay.py").read_text(encoding="utf-8")
    assert "self.size_grip.setFixedSize(26, 26)" in source
    assert "끌어서 오버레이 크기 조절" in source


def test_export_arrow_pixels_are_removed_from_ocr_but_boss_cell_is_preserved() -> None:
    assert _is_export_arrow_pixel((242, 108, 28))
    image = Image.new("RGB", (100, 100), "white")
    px = image.load()
    for x in range(5, 95):
        px[x, 75] = (242, 108, 28)
    for y in range(0, 50):
        for x in range(0, 50):
            px[x, y] = (242, 108, 28)
    board = DetectedBoard(
        box=(0, 0, 100, 100), rows=2, cols=2, confidence=1.0,
        markers=(TacticMarker(kind="boss", row=0, col=0, width=1, height=1, label="보스"),),
    )
    masked, count = _board_ocr_crop(image, board)
    assert count > 0
    assert masked.getpixel((20, 20)) == (242, 108, 28)
    assert masked.getpixel((70, 75)) == (255, 255, 255)


def test_importer_explicitly_ignores_export_arrows_for_ocr_and_cover_votes() -> None:
    source = (ROOT / "src/gfl2tool/tactic_image_import.py").read_text(encoding="utf-8")
    assert "_mask_export_arrow_pixels" in source
    assert "_detect_cover(gray, box, rows, cols, boss, rgb=source)" in source
    assert "GF2Tools 이동 화살표는 다른 칸의 OCR을 방해하지 않도록 인식 대상에서 제외했습니다." in source
