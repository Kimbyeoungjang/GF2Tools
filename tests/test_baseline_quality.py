from __future__ import annotations

from pathlib import Path

import pytest

from gfl2tool.repository import Repository
from gfl2tool.services.remolding_recommendation import RemoldingRecommendationService

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_primary_pages_share_one_layout_contract():
    for name in ("dashboard", "inventory", "formation", "remolding_optimizer", "tactics", "data_sync"):
        source = _text(f"src/gfl2tool/qtui/pages/{name}.py")
        assert "page_layout(" in source
        assert "setContentsMargins(20,18,20,18)" not in source
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    assert "PAGE_MARGINS = (20, 18, 20, 18)" in widgets
    assert "PAGE_SPACING = 10" in widgets


def test_qt_critical_errors_use_shared_detailed_dialog():
    for path in (ROOT / "src/gfl2tool/qtui").rglob("*.py"):
        if path.name == "widgets.py":
            continue
        assert "QMessageBox.critical(" not in path.read_text(encoding="utf-8"), path
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    assert "def show_error(" in widgets
    assert "setDetailedText(detail)" in widgets


def test_theme_covers_secondary_panels_disabled_controls_and_focus():
    theme = _text("src/gfl2tool/qtui/theme.py")
    assert "QFrame#PanelAlt" in theme
    assert "QPushButton:disabled" in theme
    assert "QPushButton#DangerButton" in theme
    assert "QDoubleSpinBox" in theme and "QTextEdit:focus" in theme




def test_custom_character_slot_profile_requires_six_slots(tmp_path):
    with Repository(tmp_path / "slots.db") as repo:
        svc = RemoldingRecommendationService(repo)
        with pytest.raises(ValueError, match="정확히 6"):
            svc.save_character_profile(
                "nemesis",
                slot_counts={"sentinel": 3, "vanguard": 2, "bulwark": 0, "support": 0},
            )
        saved = svc.save_character_profile(
            "nemesis",
            slot_counts={"sentinel": 3, "vanguard": 3, "bulwark": 0, "support": 0},
        )
        assert sum(int(row["count"]) for row in saved["slotDistribution"]) == 6




def test_dead_mainwindow_closing_flag_and_duplicate_worker_reference_are_gone():
    assert "_closing" not in _text("src/gfl2tool/qtui/mainwindow.py")
    workers = _text("src/gfl2tool/qtui/workers.py")
    handle = workers.split("class CancellableWorkerHandle", 1)[1].split("def run_cancellable_worker", 1)[0]
    assert "self.worker" not in handle






def test_release_audit_rejects_multi_statement_lines_and_unused_source_imports():
    source = _text("tools/package_release.py")
    assert "source_statement_semicolons" in source
    assert "tokenize.generate_tokens" in source
    assert "multiple Python statements on one line" in source
    assert "unused_source_imports" in source
    assert "unused source imports" in source
    assert "silent_broad_exception_handlers" in source
    assert "silent 'except Exception: pass' handlers" in source


def test_sources_do_not_use_statement_semicolons():
    import io
    import tokenize

    for path in (ROOT / "src/gfl2tool").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        statements = [
            token
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.OP and token.string == ";"
        ]
        assert not statements, path




def test_sources_do_not_silently_swallow_broad_exceptions():
    import ast

    for path in (ROOT / "src/gfl2tool").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            if ast.unparse(node.type) != "Exception":
                continue
            assert not (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)), path


def test_release_audit_rejects_unused_private_module_and_class_helpers():
    source = _text("tools/package_release.py")
    assert "unused_private_definitions" in source
    assert "unused private module/class helpers" in source
    assert "unused_public_module_functions" in source
    assert "unused public module helpers" in source


def test_warning_tone_comes_from_shared_theme():
    widgets = _text("src/gfl2tool/qtui/widgets.py")
    theme = _text("src/gfl2tool/qtui/theme.py")
    assert "WarningText" in widgets
    assert "DANGER_TEXT" in theme
    assert "#FFB5B5" not in widgets
    packager = _text("tools/package_release.py")
    assert "qt_hardcoded_colors" in packager
    assert "hard-coded Qt colors outside theme.py" in packager
