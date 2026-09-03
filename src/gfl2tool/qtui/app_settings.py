from __future__ import annotations

from PySide6.QtCore import QSettings

from ..settings import (
    DEFAULT_HOTKEYS,
    DEFAULT_OVERLAY_APPEARANCE,
    DEFAULT_TACTIC_VISUALS,
    DEFAULT_THEME,
    OverlayAppearance,
    OverlayHotkeys,
    TacticVisualSettings,
    validate_overlay_hotkeys,
)

ORGANIZATION = "GFL2 Tools"
APPLICATION = "GFL2 Tools"
DEFAULT_PROGRAM_UPDATE_RELEASE_URL = "https://github.com/Kimbyeoungjang/GF2Tools/releases"


class AppSettings:
    """Typed QSettings adapter for user-facing application preferences."""

    DEFAULT_THEME = DEFAULT_THEME
    DEFAULT_HOTKEYS = DEFAULT_HOTKEYS
    DEFAULT_OVERLAY_APPEARANCE = DEFAULT_OVERLAY_APPEARANCE
    DEFAULT_TACTIC_VISUALS = DEFAULT_TACTIC_VISUALS
    DEFAULT_PROGRAM_UPDATE_RELEASE_URL = DEFAULT_PROGRAM_UPDATE_RELEASE_URL

    _KEY_THEME = "appearance/theme"
    _KEY_APP_UPDATE_RELEASE_URL = "updates/program_release_url"
    _KEY_APP_UPDATE_AUTO_CHECK = "updates/program_auto_check"
    _KEY_HOTKEY_PREVIOUS = "overlay/hotkey_previous"
    _KEY_HOTKEY_NEXT = "overlay/hotkey_next"
    _KEY_HOTKEY_LOCK = "overlay/hotkey_toggle_lock"
    _KEY_OVERLAY_WIDTH = "overlay/width"
    _KEY_OVERLAY_HEIGHT = "overlay/height"
    _KEY_OVERLAY_BACKGROUND = "overlay/background"
    _KEY_OVERLAY_TEXT = "overlay/text"
    _KEY_OVERLAY_ACCENT = "overlay/accent"
    _TACTIC_COLOR_KEYS = {
        "background": "tactic/background",
        "grid": "tactic/grid",
        "boss": "tactic/boss",
        "blocked": "tactic/blocked",
        "cover": "tactic/cover",
        "arrow": "tactic/arrow",
        "unit": "tactic/unit",
        "summon": "tactic/summon",
        "text": "tactic/text",
    }

    def __init__(self, settings: QSettings | None = None):
        self._settings = settings or QSettings(ORGANIZATION, APPLICATION)

    def theme(self) -> str:
        return str(self._settings.value(self._KEY_THEME, self.DEFAULT_THEME) or self.DEFAULT_THEME)

    def set_theme(self, value: str) -> None:
        self._settings.setValue(self._KEY_THEME, str(value or self.DEFAULT_THEME))


    def program_update_release_url(self) -> str:
        return str(
            self._settings.value(
                self._KEY_APP_UPDATE_RELEASE_URL,
                self.DEFAULT_PROGRAM_UPDATE_RELEASE_URL,
            )
            or self.DEFAULT_PROGRAM_UPDATE_RELEASE_URL
        ).strip()

    def set_program_update_release_url(self, value: str) -> None:
        self._settings.setValue(self._KEY_APP_UPDATE_RELEASE_URL, str(value or "").strip())

    def program_update_auto_check(self) -> bool:
        value = self._settings.value(self._KEY_APP_UPDATE_AUTO_CHECK, True)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() not in {"0", "false", "no", "off"}

    def set_program_update_auto_check(self, enabled: bool) -> None:
        self._settings.setValue(self._KEY_APP_UPDATE_AUTO_CHECK, bool(enabled))

    def overlay_hotkeys(self) -> OverlayHotkeys:
        hotkeys = OverlayHotkeys(
            previous=str(
                self._settings.value(self._KEY_HOTKEY_PREVIOUS, self.DEFAULT_HOTKEYS.previous)
                or self.DEFAULT_HOTKEYS.previous
            ),
            next=str(
                self._settings.value(self._KEY_HOTKEY_NEXT, self.DEFAULT_HOTKEYS.next)
                or self.DEFAULT_HOTKEYS.next
            ),
            toggle_lock=str(
                self._settings.value(self._KEY_HOTKEY_LOCK, self.DEFAULT_HOTKEYS.toggle_lock)
                or self.DEFAULT_HOTKEYS.toggle_lock
            ),
        )
        try:
            return validate_overlay_hotkeys(hotkeys)
        except ValueError:
            return self.DEFAULT_HOTKEYS

    def set_overlay_hotkeys(self, hotkeys: OverlayHotkeys) -> None:
        validated = validate_overlay_hotkeys(hotkeys)
        self._settings.setValue(self._KEY_HOTKEY_PREVIOUS, validated.previous)
        self._settings.setValue(self._KEY_HOTKEY_NEXT, validated.next)
        self._settings.setValue(self._KEY_HOTKEY_LOCK, validated.toggle_lock)

    def overlay_appearance(self) -> OverlayAppearance:
        defaults = self.DEFAULT_OVERLAY_APPEARANCE
        try:
            value = OverlayAppearance(
                width=int(self._settings.value(self._KEY_OVERLAY_WIDTH, defaults.width)),
                height=int(self._settings.value(self._KEY_OVERLAY_HEIGHT, defaults.height)),
                background=str(self._settings.value(self._KEY_OVERLAY_BACKGROUND, "") or ""),
                text=str(self._settings.value(self._KEY_OVERLAY_TEXT, "") or ""),
                accent=str(self._settings.value(self._KEY_OVERLAY_ACCENT, "") or ""),
            )
            return value.normalized()
        except (TypeError, ValueError):
            return defaults

    def set_overlay_appearance(self, appearance: OverlayAppearance) -> None:
        value = appearance.normalized()
        self._settings.setValue(self._KEY_OVERLAY_WIDTH, value.width)
        self._settings.setValue(self._KEY_OVERLAY_HEIGHT, value.height)
        self._settings.setValue(self._KEY_OVERLAY_BACKGROUND, value.background)
        self._settings.setValue(self._KEY_OVERLAY_TEXT, value.text)
        self._settings.setValue(self._KEY_OVERLAY_ACCENT, value.accent)

    def tactic_visuals(self) -> TacticVisualSettings:
        payload = {
            key: str(self._settings.value(setting_key, "") or "")
            for key, setting_key in self._TACTIC_COLOR_KEYS.items()
        }
        try:
            return TacticVisualSettings(**payload).normalized()
        except ValueError:
            return self.DEFAULT_TACTIC_VISUALS

    def set_tactic_visuals(self, visuals: TacticVisualSettings) -> None:
        value = visuals.normalized()
        for key, setting_key in self._TACTIC_COLOR_KEYS.items():
            self._settings.setValue(setting_key, getattr(value, key))

    def reset(self) -> None:
        self.set_theme(self.DEFAULT_THEME)
        self.set_program_update_release_url(self.DEFAULT_PROGRAM_UPDATE_RELEASE_URL)
        self.set_program_update_auto_check(True)
        self.set_overlay_hotkeys(self.DEFAULT_HOTKEYS)
        self.set_overlay_appearance(self.DEFAULT_OVERLAY_APPEARANCE)
        self.set_tactic_visuals(self.DEFAULT_TACTIC_VISUALS)
        self.sync()

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": 1,
            "theme": self.theme(),
            "program_update_release_url": self.program_update_release_url(),
            "program_update_auto_check": self.program_update_auto_check(),
            "overlay_hotkeys": self.overlay_hotkeys().as_dict(),
            "overlay_appearance": self.overlay_appearance().as_dict(),
            "tactic_visuals": self.tactic_visuals().as_dict(),
        }

    def apply_snapshot(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        theme_value = payload.get("theme")
        if theme_value:
            self.set_theme(str(theme_value))
        if "program_update_release_url" in payload:
            self.set_program_update_release_url(str(payload.get("program_update_release_url") or ""))
        if "program_update_auto_check" in payload:
            self.set_program_update_auto_check(bool(payload.get("program_update_auto_check")))
        # ``worker_count`` existed in older snapshots.  Runtime concurrency is
        # now derived automatically from the CPU, so legacy values are accepted
        # by simply ignoring them instead of re-introducing a dead preference.
        hotkey_payload = payload.get("overlay_hotkeys")
        if isinstance(hotkey_payload, dict):
            defaults = self.DEFAULT_HOTKEYS
            restored = OverlayHotkeys(
                previous=str(hotkey_payload.get("previous") or defaults.previous),
                next=str(hotkey_payload.get("next") or defaults.next),
                toggle_lock=str(hotkey_payload.get("toggle_lock") or defaults.toggle_lock),
            )
            try:
                self.set_overlay_hotkeys(restored)
            except ValueError:
                self.set_overlay_hotkeys(defaults)
        appearance_payload = payload.get("overlay_appearance")
        if isinstance(appearance_payload, dict):
            defaults = self.DEFAULT_OVERLAY_APPEARANCE
            try:
                self.set_overlay_appearance(
                    OverlayAppearance(
                        width=int(appearance_payload.get("width", defaults.width)),
                        height=int(appearance_payload.get("height", defaults.height)),
                        background=str(appearance_payload.get("background") or ""),
                        text=str(appearance_payload.get("text") or ""),
                        accent=str(appearance_payload.get("accent") or ""),
                    )
                )
            except (TypeError, ValueError):
                self.set_overlay_appearance(defaults)
        visual_payload = payload.get("tactic_visuals")
        if isinstance(visual_payload, dict):
            try:
                self.set_tactic_visuals(
                    TacticVisualSettings(
                        **{
                            key: str(visual_payload.get(key) or "")
                            for key in self._TACTIC_COLOR_KEYS
                        }
                    )
                )
            except ValueError:
                self.set_tactic_visuals(self.DEFAULT_TACTIC_VISUALS)
        self.sync()

    def sync(self) -> None:
        self._settings.sync()




__all__ = [
    "AppSettings",
    "OverlayAppearance",
    "OverlayHotkeys",
    "TacticVisualSettings",
]
