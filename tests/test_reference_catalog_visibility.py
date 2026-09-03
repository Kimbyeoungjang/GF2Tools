from __future__ import annotations

import json
from pathlib import Path

from gfl2tool import reference


def test_bundled_doll_catalog_is_present_and_nontrivial() -> None:
    reference.configure_override_root(None)
    names = reference.bundled_doll_display_names()
    assert len(names) >= 50
    assert all(isinstance(did, int) and did > 0 for did in names)
    assert all(str(name).strip() for name in names.values())


def test_empty_doll_override_falls_back_to_bundled(tmp_path: Path) -> None:
    ref_dir = tmp_path / "reference_data"
    ref_dir.mkdir()
    (ref_dir / "dolls.json").write_text("{}", encoding="utf-8")
    reference.configure_override_root(tmp_path)
    try:
        assert len(reference.bundled_dolls()) >= 50
    finally:
        reference.configure_override_root(None)


def test_empty_doll_import_is_rejected() -> None:
    try:
        reference.validate_dataset_payload("dolls", {})
    except ValueError:
        pass
    else:
        raise AssertionError("empty doll reference must be rejected")


def test_inventory_ui_explains_owned_vs_basic_reference() -> None:
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "src/gfl2tool/qtui/pages/inventory.py").read_text(encoding="utf-8")
    data_sync = (root / "src/gfl2tool/qtui/pages/data_sync.py").read_text(encoding="utf-8")
    assert "기본 인형 레퍼런스" in inventory
    assert "이 화면은 보유로 등록된 인형만 표시합니다" in inventory
    assert "보조 툴 사용자 데이터" in data_sync
    assert "정적 API" in data_sync and "GitHub Release" in data_sync


def test_bundled_program_remolding_catalog_matches_table_1151583() -> None:
    reference.configure_override_root(None)
    catalog = reference.program_remolding_catalog()
    assert catalog["source_table_version"] == "1151583"
    assert len(catalog["property_definitions"]) == 139
    assert len(catalog["power_effects"]) == 16
    assert len(catalog["power_map"]) == 33
    assert len(catalog["mod_types"]) == 37
    assert len(catalog["ranks"]) == 4
    assert len(catalog["polarity_plans"]) == 6


def test_equipment_ui_renders_game_description_markup() -> None:
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "src/gfl2tool/qtui/pages/inventory.py").read_text(encoding="utf-8")
    rich = (root / "src/gfl2tool/qtui/rich_text.py").read_text(encoding="utf-8")
    assert "set_game_rich_text" in inventory
    assert "game_markup_to_qt_html" in inventory
    assert 'lower == "color"' in rich
    assert 'lower == "size"' in rich
    assert "html.escape" in rich
