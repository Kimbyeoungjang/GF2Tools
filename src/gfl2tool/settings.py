from __future__ import annotations

import os
import re
from dataclasses import dataclass


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def recommended_worker_count() -> int:
    return max(1, min(8, int(os.cpu_count() or 4)))


def normalize_optional_color(value: object) -> str:
    """Normalize a user color override.

    An empty string means "follow the active theme".  Keeping theme-following
    values empty is important: switching themes should immediately recolor
    tactic/overlay surfaces unless the user explicitly pinned a custom color.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    if not _HEX_COLOR.fullmatch(text):
        raise ValueError("색상은 #RRGGBB 형식으로 지정하세요.")
    return text.upper()


@dataclass(frozen=True)
class OverlayHotkeys:
    previous: str
    next: str
    toggle_lock: str

    def as_dict(self) -> dict[str, str]:
        return {
            "previous": self.previous,
            "next": self.next,
            "toggle_lock": self.toggle_lock,
        }


@dataclass(frozen=True)
class OverlayAppearance:
    width: int = 560
    height: int = 690
    background: str = ""
    text: str = ""
    accent: str = ""

    def normalized(self) -> "OverlayAppearance":
        return OverlayAppearance(
            width=max(420, min(1400, int(self.width))),
            height=max(520, min(1600, int(self.height))),
            background=normalize_optional_color(self.background),
            text=normalize_optional_color(self.text),
            accent=normalize_optional_color(self.accent),
        )

    def as_dict(self) -> dict[str, object]:
        value = self.normalized()
        return {
            "width": value.width,
            "height": value.height,
            "background": value.background,
            "text": value.text,
            "accent": value.accent,
        }


@dataclass(frozen=True)
class TacticVisualSettings:
    background: str = ""
    grid: str = ""
    boss: str = ""
    blocked: str = ""
    cover: str = ""
    arrow: str = ""
    unit: str = ""
    summon: str = ""
    text: str = ""

    def normalized(self) -> "TacticVisualSettings":
        return TacticVisualSettings(
            **{
                key: normalize_optional_color(value)
                for key, value in self.as_dict().items()
            }
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "background": self.background,
            "grid": self.grid,
            "boss": self.boss,
            "blocked": self.blocked,
            "cover": self.cover,
            "arrow": self.arrow,
            "unit": self.unit,
            "summon": self.summon,
            "text": self.text,
        }


DEFAULT_THEME = "dark"
DEFAULT_HOTKEYS = OverlayHotkeys(
    previous="Ctrl+Alt+Left",
    next="Ctrl+Alt+Right",
    toggle_lock="Ctrl+Alt+Space",
)
DEFAULT_OVERLAY_APPEARANCE = OverlayAppearance()
DEFAULT_TACTIC_VISUALS = TacticVisualSettings()

_WINDOWS_KEY_NAMES = {
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
    "Space": 0x20,
    "Home": 0x24,
    "End": 0x23,
    "PgUp": 0x21,
    "PgDown": 0x22,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Insert": 0x2D,
    "Delete": 0x2E,
    "Tab": 0x09,
}
_WINDOWS_MODIFIERS = {
    "Ctrl": 0x11,
    "Control": 0x11,
    "Alt": 0x12,
    "Shift": 0x10,
}


def windows_hotkey_keys(sequence: str) -> tuple[int, ...]:
    """Translate one portable Qt key chord to GetAsyncKeyState virtual keys."""

    parts = [part.strip() for part in str(sequence or "").split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("전역 단축키에는 Ctrl, Alt 또는 Shift 조합이 필요합니다.")

    modifiers: list[int] = []
    primary_name = parts[-1]
    for part in parts[:-1]:
        vk = _WINDOWS_MODIFIERS.get(part)
        if vk is None:
            raise ValueError(f"지원하지 않는 보조키입니다: {part}")
        if vk not in modifiers:
            modifiers.append(vk)
    if not modifiers:
        raise ValueError("전역 단축키에는 Ctrl, Alt 또는 Shift 조합이 필요합니다.")

    primary = _WINDOWS_KEY_NAMES.get(primary_name)
    if primary is None and len(primary_name) == 1 and primary_name.isalnum():
        primary = ord(primary_name.upper())
    if primary is None and primary_name.startswith("F") and primary_name[1:].isdigit():
        number = int(primary_name[1:])
        if 1 <= number <= 12:
            primary = 0x6F + number
    if primary is None:
        raise ValueError(
            "지원하지 않는 키입니다. 방향키, Space, Home/End, PageUp/PageDown, "
            "Insert/Delete, 숫자·영문자, F1~F12를 사용할 수 있습니다."
        )
    if primary in modifiers:
        raise ValueError("보조키와 실행 키를 다르게 지정하세요.")
    return (*modifiers, primary)


def validate_overlay_hotkeys(hotkeys: OverlayHotkeys) -> OverlayHotkeys:
    """Validate one complete overlay hotkey set and reject semantic duplicates."""

    resolved: list[frozenset[int]] = []
    for sequence in hotkeys.as_dict().values():
        resolved.append(frozenset(windows_hotkey_keys(sequence)))
    if len(set(resolved)) != len(resolved):
        raise ValueError("세 단축키는 서로 다르게 지정하세요.")
    return hotkeys
