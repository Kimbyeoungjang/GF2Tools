from __future__ import annotations

from gfl2tool.services.ocr_import import parse_inventory_ocr


def test_inventory_ocr_parser_finds_known_doll_and_remolding_option():
    dolls, options = parse_inventory_ocr("마키아토 Lv.60\n공격 강화 3\n")
    assert any(item.name == "마키아토" for item in dolls)
    attack = next(item for item in options if item.name == "공격 강화")
    assert attack.level == 3
    assert attack.factor_type == "sentinel"


def test_inventory_ocr_parser_deduplicates_known_doll():
    dolls, _options = parse_inventory_ocr("마키아토 마키아토\n")
    assert [item.name for item in dolls].count("마키아토") == 1


def test_ocr_image_accepts_successful_process_with_none_stdout(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    import gfl2tool.services.ocr_import as ocr_import

    source = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "white").save(source)
    monkeypatch.setattr(ocr_import, "find_tesseract", lambda: "tesseract")
    monkeypatch.setattr(
        ocr_import,
        "ocr_engine_status",
        lambda: {"available": True, "languages": ["kor", "eng"]},
    )
    monkeypatch.setattr(
        ocr_import.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=None, stderr=None),
    )
    assert ocr_import.ocr_image(source) == ""


def test_inventory_ocr_parser_handles_both_remolding_detail_layouts():
    first = "Lv.3 생명 강화\nLv.3 탁류 저항\n"
    _dolls, first_options = parse_inventory_ocr(first)
    assert [(item.name, item.level) for item in first_options] == [
        ("생명 강화", 3),
        ("탁류 저항", 3),
    ]

    second = "Lv.3 왕좌 분쇄\nLv.2 탁류 강화\nLv.1 저격 엘리트\n"
    _dolls, second_options = parse_inventory_ocr(second)
    assert [(item.name, item.level) for item in second_options] == [
        ("왕좌 분쇄", 3),
        ("탁류 강화", 2),
        ("저격 엘리트", 1),
    ]


def test_ocr_subprocess_forces_utf8_on_windows_style_output(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    import gfl2tool.services.ocr_import as ocr_import

    source = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "white").save(source)
    calls = []
    monkeypatch.setattr(ocr_import, "find_tesseract", lambda: "tesseract")

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        if "--list-langs" in args[0]:
            return SimpleNamespace(returncode=0, stdout="List of available languages\nkor\neng\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="Lv.3 생명 강화", stderr="")

    monkeypatch.setattr(ocr_import.subprocess, "run", fake_run)
    text = ocr_import.ocr_image(source)
    assert "생명 강화" in text
    assert calls
    assert all(call.get("encoding") == "utf-8" for call in calls)
    assert all(call.get("errors") == "replace" for call in calls)


def test_inventory_ocr_caps_title_false_positive_to_three_real_option_rows():
    text = "연소 방출 암맥의 뿌리\nLv.3 생명 강화\nLv.2 연소 강타\nLv.1 균형 파괴 추격\n"
    _dolls, options = parse_inventory_ocr(text)
    assert [(item.name, item.level) for item in options] == [
        ("생명 강화", 3), ("연소 강타", 2), ("균형 파괴 추격", 1),
    ]
