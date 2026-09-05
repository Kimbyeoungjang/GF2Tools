from __future__ import annotations

from pathlib import Path

from gfl2tool.services.app_update import ApplicationUpdater


ROOT = Path(__file__).resolve().parents[1]


def test_stable_version_is_newer_than_rc1() -> None:
    assert ApplicationUpdater.version_is_newer("1.0.4", "1.0.4-RC1") is True
    assert ApplicationUpdater.version_is_newer("1.0.4-RC1", "1.0.4") is False


def test_dashboard_checklist_is_top_aligned_and_debounced() -> None:
    source = (ROOT / "src/gfl2tool/qtui/pages/dashboard.py").read_text(encoding="utf-8")
    assert "box_layout.addStretch(1)" in source
    assert "Qt.AlignmentFlag.AlignTop" in source
    assert "self._checklist_save_timer.setInterval(1200)" in source
    assert "self._checklist_save_timer.start()" in source
    assert "self.checklist.set_checked(" not in source


def test_update_check_exposes_github_release_notes() -> None:
    source = (ROOT / "src/gfl2tool/services/app_update.py").read_text(encoding="utf-8")
    ui = (ROOT / "src/gfl2tool/qtui/mainwindow.py").read_text(encoding="utf-8")
    assert "release_notes: str = """ in source
    assert 'str(raw.get("body") or "").strip()' in source
    assert "업데이트 내용" in ui
    assert "release_notes" in ui


def test_tactic_export_cover_hugs_border_and_uses_bundled_noto_font() -> None:
    source = (ROOT / "src/gfl2tool/qtui/tactic_widgets.py").read_text(encoding="utf-8")
    assert "edge_gap = max(1.2, min(cw, ch) * 0.030)" in source
    assert "end_gap = max(0.8, min(cw, ch) * 0.018)" in source
    assert "min(cw, ch) * 0.105" in source
    assert "QFontDatabase.addApplicationFont" in source
    assert '"NotoSansKR-VF.ttf"' in source
    assert "QFont.Weight.Bold" in source
    assert "font.setPixelSize" in source
    assert "min(cw, ch) * 0.46" in source
