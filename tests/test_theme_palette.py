from __future__ import annotations

from gfl2tool.qtui import theme


def test_theme_switch_updates_runtime_palette_and_choices():
    original = theme.active_theme()
    try:
        assert [key for key, _label in theme.theme_choices()] == ["dark", "light", "midnight", "graphite"]
        assert [label for _key, label in theme.theme_choices()] == [
            "전술 블랙", "클린 화이트", "엘모 네이비", "그리폰 그레이"
        ]

        assert theme.set_active_theme("light") == "light"
        assert theme.BG == "#F2F2EF"
        assert theme.PANEL == "#FFFFFF"
        assert theme.ACCENT == "#E85F14"
        assert theme.FACTOR_PANEL_COLORS["sentinel"] == "#FFF0F0"

        assert theme.set_active_theme("midnight") == "midnight"
        assert theme.ACCENT == "#F26C1C"
        assert theme.INFO == "#50C3CF"
        assert theme.FACTOR_PANEL_COLORS["bulwark"] == "#163342"

        assert theme.set_active_theme("not-a-theme") == "dark"
        assert theme.active_theme() == "dark"
    finally:
        theme.set_active_theme(original)
