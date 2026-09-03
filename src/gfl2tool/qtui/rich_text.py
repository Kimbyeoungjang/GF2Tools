from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

_TAG_RE = re.compile(
    r"<\s*(/?)\s*(color|size|b|i|u)\s*(?:=\s*([^>]+))?\s*>",
    flags=re.IGNORECASE,
)
_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?$")
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)%?$", flags=re.IGNORECASE)


def _open_tag(name: str, value: str) -> str | None:
    lower = name.lower()
    if lower == "color":
        match = _COLOR_RE.fullmatch(str(value or "").strip().strip('"\''))
        if match is None:
            return None
        return f'<span style="color:#{match.group(1)};">'
    if lower == "size":
        raw = str(value or "").strip().strip('"\'')
        match = _SIZE_RE.fullmatch(raw)
        if match is None:
            return None
        size = float(match.group(1))
        if raw.endswith("%"):
            size = max(60.0, min(200.0, size))
            rendered = str(int(size)) if size.is_integer() else f"{size:.1f}"
            return f'<span style="font-size:{rendered}%;">'
        size = max(8.0, min(48.0, size))
        rendered = str(int(size)) if size.is_integer() else f"{size:.1f}"
        return f'<span style="font-size:{rendered}px;">'
    if lower in {"b", "i", "u"}:
        return f"<{lower}>"
    return None


def _close_tag(name: str) -> str:
    lower = name.lower()
    if lower in {"color", "size"}:
        return "</span>"
    if lower in {"b", "i", "u"}:
        return f"</{lower}>"
    return ""


def game_markup_to_qt_html(value: object) -> str:
    """Convert the small Unity-style markup used by table descriptions to Qt HTML.

    Only a narrow allow-list is interpreted. All other input is HTML-escaped, so
    downloaded descriptions cannot inject arbitrary Qt rich-text markup.
    """
    text = str(value or "")
    if not text:
        return ""

    pieces: list[str] = []
    stack: list[str] = []
    cursor = 0
    for match in _TAG_RE.finditer(text):
        pieces.append(html.escape(text[cursor:match.start()], quote=False).replace("\n", "<br>"))
        closing, raw_name, raw_value = match.groups()
        name = raw_name.lower()
        if closing:
            if stack and stack[-1] == name:
                stack.pop()
                pieces.append(_close_tag(name))
            else:
                pieces.append(html.escape(match.group(0), quote=False))
        else:
            opened = _open_tag(name, raw_value or "")
            if opened is None:
                pieces.append(html.escape(match.group(0), quote=False))
            else:
                stack.append(name)
                pieces.append(opened)
        cursor = match.end()
    pieces.append(html.escape(text[cursor:], quote=False).replace("\n", "<br>"))
    while stack:
        pieces.append(_close_tag(stack.pop()))
    return "<qt>" + "".join(pieces) + "</qt>"


def game_markup_to_plain_text(value: object) -> str:
    """Return searchable/plain text without supported formatting tags."""
    text = str(value or "")
    return _TAG_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def set_game_rich_text(label: QLabel, value: object, *, empty: str = "설명 없음") -> None:
    text = str(value or "").strip()
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(game_markup_to_qt_html(text) if text else html.escape(empty))
