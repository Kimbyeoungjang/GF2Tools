from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton

from ...settings import TacticVisualSettings
from .. import theme
from ..app_settings import AppSettings
from ..tactic_widgets import apply_visual_settings
from ..widgets import dialog_layout


class TacticVisualSettingsDialog(QDialog):
    """Five grouped colors with large, readable current-color previews."""

    _GROUPS = (
        ("background", "격자 배경", ("background",)),
        ("text", "글씨", ("text", "unit")),
        ("grid", "격자 선", ("grid",)),
        ("terrain", "지형지물", ("blocked", "cover")),
        ("misc", "기타", ("boss", "arrow", "summon")),
    )

    def __init__(self, settings: AppSettings, *, on_changed=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.on_changed = on_changed
        self._values = settings.tactic_visuals().as_dict()
        self.setWindowTitle("택틱 · 오버레이 색상 설정")
        self.resize(680, 350)

        root = dialog_layout(self)
        intro = QLabel(
            "편집 화면과 오버레이가 같은 5개 색상 그룹을 사용합니다. 현재 적용 색을 넓은 미리보기로 확인한 뒤 ‘색상 변경’을 누르세요."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        root.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.addWidget(QLabel("항목"), 0, 0)
        grid.addWidget(QLabel("현재 색상"), 0, 1)
        grid.addWidget(QLabel("변경"), 0, 2)
        self._swatches: dict[str, QLabel] = {}
        for row, (group, label, _fields) in enumerate(self._GROUPS, start=1):
            name = QLabel(label)
            name.setMinimumWidth(95)
            grid.addWidget(name, row, 0)

            swatch = QLabel()
            swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)
            swatch.setMinimumWidth(300)
            swatch.setFixedHeight(38)
            grid.addWidget(swatch, row, 1)
            self._swatches[group] = swatch

            change = QPushButton("색상 변경…")
            change.clicked.connect(lambda _checked=False, name=group: self._pick(name))
            grid.addWidget(change, row, 2)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        actions = QHBoxLayout()
        reset_all = QPushButton("기본값으로 변경")
        close = QPushButton("닫기")
        close.setObjectName("AccentButton")
        reset_all.clicked.connect(self._reset_all)
        close.clicked.connect(self.accept)
        actions.addWidget(reset_all)
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)
        self._refresh_swatches()

    @classmethod
    def _fields(cls, group: str) -> tuple[str, ...]:
        for key, _label, fields in cls._GROUPS:
            if key == group:
                return fields
        raise KeyError(group)

    def _group_value(self, group: str) -> str:
        values = [str(self._values.get(field) or "") for field in self._fields(group)]
        pinned = [value for value in values if value]
        return pinned[0] if pinned else ""

    @staticmethod
    def _default_color(group: str) -> str:
        return {
            "background": theme.PANEL, "text": theme.TEXT, "grid": theme.BORDER,
            "terrain": theme.COVER, "misc": theme.ACCENT,
        }.get(group, theme.ACCENT)

    @staticmethod
    def _foreground_for(background: str) -> str:
        color = QColor(background)
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        return theme.BG if luminance >= 150 else theme.TEXT

    def _pick(self, group: str) -> None:
        current = self._group_value(group) or self._default_color(group)
        color = QColorDialog.getColor(QColor(current), self, f"{dict((k,l) for k,l,_ in self._GROUPS)[group]} 색상 선택")
        if color.isValid():
            self._set_group(group, color.name(QColor.NameFormat.HexRgb).upper())

    def _set_group(self, group: str, value: str) -> None:
        for field in self._fields(group):
            self._values[field] = value
        self._commit()

    def _reset_all(self) -> None:
        self._values = {key: "" for key in self._values}
        self._commit()

    def _commit(self) -> None:
        visuals = TacticVisualSettings(**self._values).normalized()
        self.settings.set_tactic_visuals(visuals)
        self.settings.sync()
        apply_visual_settings(visuals)
        self._values = visuals.as_dict()
        self._refresh_swatches()
        if callable(self.on_changed):
            self.on_changed()

    def _refresh_swatches(self) -> None:
        for group, swatch in self._swatches.items():
            custom = self._group_value(group)
            color = custom or self._default_color(group)
            fg = self._foreground_for(color)
            suffix = "" if custom else "  ·  기본값"
            swatch.setText(f"{color}{suffix}")
            # Apply declarations directly to the label.  The previous selector
            # string ended with a literal double closing brace, which Qt logged
            # as ``Could not parse stylesheet of object QLabel(...)`` every time
            # the preview was refreshed/reset.
            swatch.setStyleSheet(
                f"background-color: {color}; color: {fg}; border: 1px solid {theme.BORDER}; "
                "border-radius: 7px; padding: 5px 12px; font-weight: 600;"
            )
