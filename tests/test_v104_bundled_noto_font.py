from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_builder_downloads_pinned_noto_sans_kr_and_embeds_it() -> None:
    text = (ROOT / "tools" / "build_executable.py").read_text(encoding="utf-8")
    assert 'NOTO_SANS_KR_VERSION = "2.004"' in text
    assert 'NOTO_SANS_KR_COMMIT = "523d033d6cb47f4a80c58a35753646f5c3608a78"' in text
    assert 'Sans/Variable/TTF/Subset/NotoSansKR-VF.ttf' in text
    assert 'gfl2tool/resources/fonts' in text
    assert 'NotoSansKR-OFL-1.1.txt' in text


def test_tactic_export_registers_bundled_application_font() -> None:
    text = (ROOT / "src" / "gfl2tool" / "qtui" / "tactic_widgets.py").read_text(encoding="utf-8")
    assert 'QFontDatabase.addApplicationFont' in text
    assert 'resources" / "fonts" / "NotoSansKR-VF.ttf' in text
    assert '_bundled_noto_sans_kr_family()' in text
    assert 'font.setPixelSize' in text


def test_source_release_does_not_vendor_font_binary() -> None:
    assert not (ROOT / "src" / "gfl2tool" / "resources" / "fonts" / "NotoSansKR-VF.ttf").exists()
    assert (ROOT / "licenses" / "NotoSansKR-OFL-1.1.txt").is_file()
