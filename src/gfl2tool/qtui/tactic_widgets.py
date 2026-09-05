from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..settings import TacticVisualSettings
from ..tactics import MAX_MARKERS_PER_STEP, Tactic, TacticMarker
from . import theme
from .widgets import dialog_layout



_VISUALS = TacticVisualSettings()
_MODULE_DIR = Path(__file__).resolve().parent

def apply_visual_settings(visuals: TacticVisualSettings | None = None) -> None:
    global _VISUALS
    _VISUALS = (visuals or TacticVisualSettings()).normalized()


def _visual(name: str, default: str) -> str:
    value = str(getattr(_VISUALS, name, "") or "")
    return value or default



def _color(value: str, alpha: int = 255) -> QColor:
    color = QColor(value)
    color.setAlpha(max(0, min(255, int(alpha))))
    return color

@lru_cache(maxsize=1)
def _register_embedded_export_fonts() -> tuple[str, ...]:
    """Register bundled export fonts shipped in gfl2tool/resources/fonts."""
    fonts_dir = _MODULE_DIR.parent / "resources" / "fonts"
    families: list[str] = []
    if not fonts_dir.is_dir():
        return tuple()
    for name in (
        "PretendardVariable.ttf",
        "Pretendard-Regular.ttf",
        "Pretendard-Medium.ttf",
        "Pretendard-SemiBold.ttf",
        "Pretendard-Bold.ttf",
        "Pretendard-ExtraBold.ttf",
    ):
        path = fonts_dir / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            family = str(family).strip()
            if family and family not in families:
                families.append(family)
    return tuple(families)


def _export_font(*, pixel_size: float | None = None, point_size: float | None = None,
    families: list[str] | None = None, weight: QFont.Weight = QFont.Weight.Normal,
    bold: bool | None = None) -> QFont:
    """Create a clean export font close to the preferred reference sheet."""
    embedded = list(_register_embedded_export_fonts())
    default_families = [name for name in [
        *embedded,
        "Pretendard Variable", "Pretendard", "SUIT Variable", "SUIT",
        "Apple SD Gothic Neo", "Malgun Gothic", "Segoe UI",
    ] if name]
    font = QFont()
    font.setFamilies(families or default_families)
    font.setStyleHint(QFont.StyleHint.SansSerif, QFont.StyleStrategy.PreferQuality)
    try:
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    except AttributeError:
        pass
    if pixel_size is not None:
        font.setPixelSize(max(1, int(round(pixel_size))))
    elif point_size is not None:
        font.setPointSizeF(max(1.0, float(point_size)))
    font.setWeight(weight)
    if bold is not None:
        font.setBold(bool(bold))
    return font


def _board_geometry(bounds: QRect, rows: int, cols: int, *, margin: int = 12) -> QRectF:
    usable_w = max(40, bounds.width() - margin * 2)
    usable_h = max(40, bounds.height() - margin * 2)
    cell = max(2.0, min(usable_w / max(1, cols), usable_h / max(1, rows)))
    width = cell * cols
    height = cell * rows
    left = bounds.left() + (bounds.width() - width) / 2.0
    top = bounds.top() + (bounds.height() - height) / 2.0
    return QRectF(left, top, width, height)


def _cell_center(board: QRectF, rows: int, cols: int, row: int, col: int) -> QPointF:
    cw = board.width() / max(1, cols)
    ch = board.height() / max(1, rows)
    return QPointF(board.left() + (col + 0.5) * cw, board.top() + (row + 0.5) * ch)


def _cell_rect(board: QRectF, rows: int, cols: int, row: int, col: int, *, inset: float = 2.0) -> QRectF:
    cw = board.width() / max(1, cols)
    ch = board.height() / max(1, rows)
    return QRectF(
        board.left() + col * cw + inset,
        board.top() + row * ch + inset,
        max(1.0, cw - inset * 2),
        max(1.0, ch - inset * 2),
    )


def _draw_arrow(
    painter: QPainter,
    start: QPointF,
    end: QPointF,
    *,
    alpha: int = 255,
    label: str = "",
    caption: str = "",
    color: QColor | None = None,
    background: QColor | None = None,
    width: float = 3.0,
) -> None:
    line_color = QColor(color) if color is not None else _color(_visual("arrow", theme.ACCENT), alpha)
    line_color.setAlpha(max(0, min(255, int(alpha))))
    painter.setPen(QPen(line_color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(start, end)
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    size = max(10.0, min(24.0, length * 0.20))
    base = QPointF(end.x() - ux * size, end.y() - uy * size)
    poly = QPolygonF([
        end,
        QPointF(base.x() + px * size * 0.50, base.y() + py * size * 0.50),
        QPointF(base.x() - px * size * 0.50, base.y() - py * size * 0.50),
    ])
    painter.setBrush(line_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(poly)

    note = str(caption or "").strip()[:16]
    if note:
        normal_x, normal_y = -uy, ux
        center = QPointF(
            start.x() + dx * 0.58 + normal_x * max(12.0, width * 3.0),
            start.y() + dy * 0.58 + normal_y * max(12.0, width * 3.0),
        )
        font = _export_font(pixel_size=max(11.0, min(15.0, length * 0.085)), weight=QFont.Weight.Medium)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(note)
        text_h = metrics.height()
        pad_x = 7.0
        pad_y = 4.0
        badge = QRectF(
            center.x() - text_w / 2 - pad_x,
            center.y() - text_h / 2 - pad_y,
            text_w + pad_x * 2,
            text_h + pad_y * 2,
        )
        fill = QColor(background) if background is not None else QColor(_visual("background", theme.PANEL))
        fill.setAlpha(max(232, int(alpha)))
        painter.setBrush(fill)
        painter.setPen(QPen(line_color, max(1.1, width * 0.45)))
        painter.drawRoundedRect(badge, 6.0, 6.0)
        painter.setPen(line_color)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, note)

    number = str(label or "").strip()
    if number:
        radius = max(8.0, min(13.0, length * 0.11))
        center = QPointF(
            start.x() + dx * 0.28,
            start.y() + dy * 0.28,
        )
        fill = QColor(background) if background is not None else QColor(_visual("background", theme.PANEL))
        fill.setAlpha(max(220, int(alpha)))
        painter.setBrush(fill)
        painter.setPen(QPen(line_color, max(1.5, width * 0.62)))
        painter.drawEllipse(center, radius, radius)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(7.0, radius * 0.86))
        painter.setFont(font)
        painter.setPen(line_color)
        painter.drawText(
            QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2),
            Qt.AlignmentFlag.AlignCenter,
            number[:2],
        )


def _arrow_marker_label(marker: TacticMarker, ordinal: int) -> str:
    """Return an explicit 1-5 arrow order, with a legacy insertion-order fallback."""
    label = str(marker.label or "").strip()
    if label in {"1", "2", "3", "4", "5"}:
        return label
    return str(((max(1, int(ordinal)) - 1) % 5) + 1)


def _draw_cover(painter: QPainter, rect: QRectF, edges: str, *, alpha: int = 255) -> None:
    thickness = max(4.0, min(rect.width(), rect.height()) * 0.18)
    pen = QPen(_color(_visual("cover", theme.COVER), alpha), thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap)
    painter.setPen(pen)
    if "N" in edges:
        painter.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.right(), rect.top()))
    if "E" in edges:
        painter.drawLine(QPointF(rect.right(), rect.top()), QPointF(rect.right(), rect.bottom()))
    if "S" in edges:
        painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))
    if "W" in edges:
        painter.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))


def draw_tactic_step(
    painter: QPainter,
    tactic: Tactic,
    step_index: int,
    bounds: QRect,
    *,
    show_previous: bool | None = None,
    background_alpha: int = 242,
) -> None:
    if not tactic.steps:
        return
    index = max(0, min(int(step_index), len(tactic.steps) - 1))
    rows, cols = tactic.grid_size(index)
    board = _board_geometry(bounds, rows, cols)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillRect(board, _color(_visual("background", theme.PANEL), background_alpha))

    cw = board.width() / cols
    ch = board.height() / rows
    painter.setPen(QPen(_color(_visual("grid", theme.BORDER), 210), 1))
    for col in range(cols + 1):
        x = board.left() + col * cw
        painter.drawLine(QPointF(x, board.top()), QPointF(x, board.bottom()))
    for row in range(rows + 1):
        y = board.top() + row * ch
        painter.drawLine(QPointF(board.left(), y), QPointF(board.right(), y))

    previous_enabled = tactic.show_previous if show_previous is None else bool(show_previous)
    if previous_enabled and index > 0 and tactic.grid_size(index - 1) == (rows, cols):
        painter.save()
        painter.setPen(QPen(_color(theme.MUTED, 92), 2, Qt.PenStyle.DashLine))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(max(6.0, min(cw, ch) * 0.26))
        painter.setFont(font)
        for marker in tactic.steps[index - 1].markers:
            if marker.kind == "unit":
                painter.drawText(
                    _cell_rect(board, rows, cols, marker.row, marker.col, inset=3),
                    Qt.AlignmentFlag.AlignCenter,
                    tactic.marker_label(marker)[:6],
                )
        painter.restore()

    step = tactic.steps[index]
    arrow_ordinal = 0
    for marker in step.markers:
        if marker.kind == "arrow":
            arrow_ordinal += 1
            end_row = marker.to_row if marker.to_row is not None else marker.row
            end_col = marker.to_col if marker.to_col is not None else marker.col
            _draw_arrow(
                painter,
                _cell_center(board, rows, cols, marker.row, marker.col),
                _cell_center(board, rows, cols, end_row, end_col),
                label=_arrow_marker_label(marker, arrow_ordinal),
            )
            continue
        rect = QRectF(
            board.left() + marker.col * cw + 2,
            board.top() + marker.row * ch + 2,
            max(1.0, marker.width * cw - 4),
            max(1.0, marker.height * ch - 4),
        )
        if marker.kind == "blocked":
            painter.fillRect(rect, _color(_visual("blocked", theme.TERRAIN_BLOCK), 245))
            continue
        if marker.kind == "cover":
            _draw_cover(painter, _cell_rect(board, rows, cols, marker.row, marker.col, inset=1), marker.edges)
            continue
        if marker.kind == "boss":
            painter.fillRect(rect, _color(_visual("boss", theme.INFO), 215))
            painter.setPen(_color(_visual("text", theme.TEXT)))
            font = painter.font()
            font.setBold(True)
            font.setPointSizeF(max(7.0, min(rect.width(), rect.height()) * 0.11))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, marker.label or "보스")
            continue
        if marker.kind == "summon":
            painter.setPen(QPen(_color(_visual("summon", theme.ACCENT), 235), max(2.0, min(cw, ch) * 0.07)))
            painter.setBrush(_color(_visual("summon", theme.ACCENT), 34))
            painter.drawEllipse(rect.adjusted(3, 3, -3, -3))
            painter.setPen(_color(_visual("text", theme.TEXT)))
            font = painter.font()
            font.setBold(True)
            font.setPointSizeF(max(8.0, min(cw, ch) * 0.34))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, marker.label or "*")
            continue
        if marker.kind == "custom":
            misc = _visual("boss", theme.ACCENT)
            painter.setPen(QPen(_color(misc, 230), max(1.5, min(cw, ch) * 0.045), Qt.PenStyle.DashLine))
            painter.setBrush(_color(misc, 25))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(_color(_visual("text", theme.TEXT)))
            font = painter.font()
            font.setBold(True)
            font.setPointSizeF(max(6.5, min(cw, ch) * 0.24))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, (marker.label or "기타")[:12])
            continue
        painter.setPen(_color(_visual("unit", theme.TEXT)))
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(max(7.0, min(cw, ch) * 0.31))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tactic.marker_label(marker)[:6])
    painter.restore()


class TacticGridWidget(QWidget):
    modified = Signal()
    hoverChanged = Signal(str)

    def __init__(
        self,
        tactic: Tactic,
        parent: QWidget | None = None,
        *,
        editable: bool = True,
        move_only: bool = False,
    ):
        super().__init__(parent)
        self.tactic = tactic
        self.step_index = 0
        self.editable = bool(editable)
        self.move_only = bool(move_only)
        self.tool = "move"
        self.unit_label = "마"
        self.unit_key = ""
        self.summon_label = "*"
        self.custom_label = ""
        self.arrow_label = "1"
        self.arrow_caption = ""
        self.boss_size = (3, 3)
        self._arrow_start: tuple[int, int] | None = None
        self._selected_marker: TacticMarker | None = None
        self._selected_origin: tuple[int, int] | None = None
        self._move_press_cell: tuple[int, int] | None = None
        self._move_initial: tuple[int, int, int | None, int | None] | None = None
        self._move_changed = False
        self._hover_cell: tuple[int, int] | None = None
        self._hover_cover_target: tuple[int, int, str] | None = None
        self._drag_tool: str | None = None
        self._drag_enable = True
        self._drag_seen: set[tuple[object, ...]] = set()
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)

    def refresh_theme(self) -> None:
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(560, 560)

    def _grid_size(self) -> tuple[int, int]:
        return self.tactic.grid_size(self.step_index)

    def set_tactic(self, tactic: Tactic) -> None:
        self.tactic = tactic
        self.step_index = min(self.step_index, max(0, len(tactic.steps) - 1))
        self._arrow_start = None
        self._selected_marker = None
        self._selected_origin = None
        self._reset_move_drag()
        self._hover_cell = None
        self._hover_cover_target = None
        self._reset_drag()
        self.update()

    def set_step_index(self, index: int) -> None:
        self.step_index = max(0, min(int(index), max(0, len(self.tactic.steps) - 1)))
        self._arrow_start = None
        self._selected_marker = None
        self._selected_origin = None
        self._reset_move_drag()
        self._hover_cell = None
        self._hover_cover_target = None
        self._reset_drag()
        self.update()

    def set_tool(self, tool: str) -> None:
        valid_tools = {"move", "unit", "summon", "custom", "boss", "blocked", "cover", "arrow", "clear"}
        self.tool = "move" if self.move_only else (tool if tool in valid_tools else "move")
        self._arrow_start = None
        self._selected_marker = None
        self._selected_origin = None
        self._reset_move_drag()
        self._hover_cover_target = None
        self._reset_drag()
        self.update()

    def _board(self) -> QRectF:
        rows, cols = self._grid_size()
        return _board_geometry(self.rect(), rows, cols, margin=14)

    def _cell_at(self, pos: QPoint) -> tuple[int, int] | None:
        rows, cols = self._grid_size()
        board = self._board()
        if not board.contains(QPointF(pos)):
            return None
        col = int((pos.x() - board.left()) / (board.width() / cols))
        row = int((pos.y() - board.top()) / (board.height() / rows))
        if 0 <= row < rows and 0 <= col < cols:
            return row, col
        return None

    def _cover_target_at(self, pos: QPoint) -> tuple[int, int, str] | None:
        """Return the cell edge closest to the pointer.

        Cover is a line on a cell boundary, not a filled cell.  Choosing the
        edge from the pointer position makes painting deterministic and removes
        the old global N/E/S/W direction selector.
        """
        cell = self._cell_at(pos)
        if cell is None:
            return None
        row, col = cell
        rows, cols = self._grid_size()
        board = self._board()
        cw = board.width() / max(1, cols)
        ch = board.height() / max(1, rows)
        left = board.left() + col * cw
        top = board.top() + row * ch
        x = float(pos.x())
        y = float(pos.y())
        distances = {
            "N": abs(y - top),
            "E": abs((left + cw) - x),
            "S": abs((top + ch) - y),
            "W": abs(x - left),
        }
        edge = min(("N", "E", "S", "W"), key=lambda value: (distances[value], "NESW".index(value)))
        return row, col, edge

    @staticmethod
    def _marker_contains(marker: TacticMarker, row: int, col: int) -> bool:
        if marker.kind == "arrow":
            return (marker.row, marker.col) == (row, col) or (marker.to_row, marker.to_col) == (row, col)
        return marker.row <= row < marker.row + marker.height and marker.col <= col < marker.col + marker.width

    def _marker_at(self, row: int, col: int) -> TacticMarker | None:
        step = self.tactic.steps[self.step_index]
        priority = {"unit": 7, "summon": 6, "custom": 5, "boss": 4, "arrow": 3, "cover": 2, "blocked": 1}
        candidates = [marker for marker in step.markers if self._marker_contains(marker, row, col)]
        if not candidates:
            return None
        return max(candidates, key=lambda marker: (priority.get(marker.kind, 0), step.markers.index(marker)))

    def _reset_move_drag(self) -> None:
        self._move_press_cell = None
        self._move_initial = None
        self._move_changed = False
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.unsetCursor()

    def _begin_move_drag(self, marker: TacticMarker, row: int, col: int) -> None:
        self._selected_marker = marker
        self._selected_origin = (row, col)
        self._move_press_cell = (row, col)
        self._move_initial = (marker.row, marker.col, marker.to_row, marker.to_col)
        self._move_changed = False
        self.grabMouse()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _drag_selected_to(self, row: int, col: int) -> bool:
        marker = self._selected_marker
        initial = self._move_initial
        press = self._move_press_cell
        if marker is None or initial is None or press is None:
            return False
        rows, cols = self._grid_size()
        delta_row = row - press[0]
        delta_col = col - press[1]
        origin_row, origin_col, origin_to_row, origin_to_col = initial
        if marker.kind == "arrow":
            end_row = origin_to_row if origin_to_row is not None else origin_row
            end_col = origin_to_col if origin_to_col is not None else origin_col
            min_delta_row = -min(origin_row, end_row)
            max_delta_row = rows - 1 - max(origin_row, end_row)
            min_delta_col = -min(origin_col, end_col)
            max_delta_col = cols - 1 - max(origin_col, end_col)
            delta_row = max(min_delta_row, min(max_delta_row, delta_row))
            delta_col = max(min_delta_col, min(max_delta_col, delta_col))
            new_values = (
                origin_row + delta_row,
                origin_col + delta_col,
                end_row + delta_row,
                end_col + delta_col,
            )
            old_values = (marker.row, marker.col, marker.to_row, marker.to_col)
            if new_values == old_values:
                return False
            marker.row, marker.col, marker.to_row, marker.to_col = new_values
            return True

        target_row = origin_row + delta_row
        target_col = origin_col + delta_col
        target_row = max(0, min(rows - marker.height, target_row))
        target_col = max(0, min(cols - marker.width, target_col))
        if (target_row, target_col) == (marker.row, marker.col):
            return False
        marker.row = target_row
        marker.col = target_col
        return True

    def _move_selected_to(self, row: int, col: int) -> bool:
        marker = self._selected_marker
        if marker is None:
            return False
        rows, cols = self._grid_size()
        origin_row, origin_col = self._selected_origin or (marker.row, marker.col)
        delta_row = row - origin_row
        delta_col = col - origin_col
        if marker.kind == "arrow":
            end_row = marker.to_row if marker.to_row is not None else marker.row
            end_col = marker.to_col if marker.to_col is not None else marker.col
            min_row = min(marker.row, end_row) + delta_row
            max_row = max(marker.row, end_row) + delta_row
            min_col = min(marker.col, end_col) + delta_col
            max_col = max(marker.col, end_col) + delta_col
            if min_row < 0 or max_row >= rows or min_col < 0 or max_col >= cols:
                return False
            marker.row += delta_row
            marker.col += delta_col
            marker.to_row = end_row + delta_row
            marker.to_col = end_col + delta_col
        else:
            new_row = max(0, min(rows - marker.height, marker.row + delta_row))
            new_col = max(0, min(cols - marker.width, marker.col + delta_col))
            if (new_row, new_col) == (marker.row, marker.col):
                return False
            marker.row = new_row
            marker.col = new_col
        self._selected_origin = (marker.row, marker.col)
        return True

    def _erase_at(self, row: int, col: int) -> bool:
        step = self.tactic.steps[self.step_index]
        before = len(step.markers)
        step.markers = [marker for marker in step.markers if not self._marker_contains(marker, row, col)]
        self._arrow_start = None
        return len(step.markers) != before

    def _reset_drag(self) -> None:
        self._drag_tool = None
        self._drag_enable = True
        self._drag_seen.clear()

    def _blocked_at(self, row: int, col: int) -> TacticMarker | None:
        step = self.tactic.steps[self.step_index]
        return next(
            (marker for marker in step.markers if marker.kind == "blocked" and marker.row == row and marker.col == col),
            None,
        )

    def _cover_at(self, row: int, col: int) -> TacticMarker | None:
        step = self.tactic.steps[self.step_index]
        return next(
            (marker for marker in step.markers if marker.kind == "cover" and marker.row == row and marker.col == col),
            None,
        )

    def _set_blocked(self, row: int, col: int, enabled: bool) -> bool:
        step = self.tactic.steps[self.step_index]
        existing = self._blocked_at(row, col)
        if enabled and existing is None:
            if len(step.markers) >= MAX_MARKERS_PER_STEP:
                return False
            step.markers.append(TacticMarker(kind="blocked", row=row, col=col))
            return True
        if not enabled and existing is not None:
            step.markers.remove(existing)
            return True
        return False

    def _set_cover_edge(self, row: int, col: int, edge: str, enabled: bool) -> bool:
        step = self.tactic.steps[self.step_index]
        edge = edge if edge in "NESW" else "N"
        existing = self._cover_at(row, col)
        if existing is None:
            if enabled:
                if len(step.markers) >= MAX_MARKERS_PER_STEP:
                    return False
                step.markers.append(TacticMarker(kind="cover", row=row, col=col, edges=edge))
                return True
            return False
        edges = set(existing.edges)
        before = set(edges)
        if enabled:
            edges.add(edge)
        else:
            edges.discard(edge)
        if edges == before:
            return False
        existing.edges = "".join(item for item in "NESW" if item in edges)
        if not existing.edges:
            step.markers.remove(existing)
        return True

    def _apply_drag_cell(self, row: int, col: int) -> bool:
        cell = (row, col)
        if cell in self._drag_seen:
            return False
        self._drag_seen.add(cell)
        if self._drag_tool == "clear":
            return self._erase_at(row, col)
        if self._drag_tool == "blocked":
            return self._set_blocked(row, col, self._drag_enable)
        return False

    def _apply_cover_target(self, row: int, col: int, edge: str) -> bool:
        key = ("cover", row, col, edge)
        if key in self._drag_seen:
            return False
        self._drag_seen.add(key)
        return self._set_cover_edge(row, col, edge, self._drag_enable)

    def mouseMoveEvent(self, event):  # noqa: N802
        cell = self._cell_at(event.position().toPoint())
        if cell != self._hover_cell:
            self._hover_cell = cell
            if cell is None:
                self.hoverChanged.emit("")
            else:
                row, col = cell
                self.hoverChanged.emit(f"행 {row + 1} · 열 {col + 1}")
            self.update()
        cover_target = self._cover_target_at(event.position().toPoint()) if self.editable and self.tool == "cover" else None
        if cover_target != self._hover_cover_target:
            self._hover_cover_target = cover_target
            self.update()
        if self._drag_tool is not None and not (
            event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        ):
            self._reset_drag()
        if self.editable and self.tool == "move" and self._move_press_cell is None:
            if cell is not None and self._marker_at(*cell) is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
        if (
            self.editable
            and self.tool == "move"
            and self._move_press_cell is not None
            and cell is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._drag_selected_to(*cell):
                self._move_changed = True
                self.update()
        if self.editable and self._drag_tool is not None:
            changed = False
            if self._drag_tool == "cover":
                target = self._cover_target_at(event.position().toPoint())
                if target is not None:
                    changed = self._apply_cover_target(*target)
            elif cell is not None:
                changed = self._apply_drag_cell(*cell)
            if changed:
                self.modified.emit()
                self.update()
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover_cell = None
        self._hover_cover_target = None
        self.hoverChanged.emit("")
        if self._move_press_cell is None:
            self.unsetCursor()
        self.update()
        return super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if not self.editable:
            return super().mousePressEvent(event)
        cell = self._cell_at(event.position().toPoint())
        if cell is None:
            return super().mousePressEvent(event)
        row, col = cell

        if self.move_only and event.button() == Qt.MouseButton.RightButton:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.RightButton or self.tool == "clear":
            self._drag_tool = "clear"
            self._drag_seen.clear()
            if self._apply_drag_cell(row, col):
                self.modified.emit()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        step = self.tactic.steps[self.step_index]
        rows, cols = self._grid_size()
        changed = True
        if self.tool == "move":
            self._reset_drag()
            clicked = self._marker_at(row, col)
            if clicked is not None:
                self._begin_move_drag(clicked, row, col)
                self.update()
                return
            if self._selected_marker is not None:
                # Keep click-then-click as an accessibility fallback while the
                # primary mouse workflow is direct drag-and-drop.
                changed = self._move_selected_to(row, col)
                self._selected_marker = None
                self._selected_origin = None
                self._reset_move_drag()
            else:
                changed = False
        elif self.tool == "boss":
            width = min(self.boss_size[0], cols)
            height = min(self.boss_size[1], rows)
            col = min(col, cols - width)
            row = min(row, rows - height)
            step.markers = [marker for marker in step.markers if marker.kind != "boss"]
            step.markers.append(TacticMarker(kind="boss", row=row, col=col, label="보스", width=width, height=height))
        elif self.tool == "blocked":
            self._drag_tool = "blocked"
            self._drag_enable = self._blocked_at(row, col) is None
            self._drag_seen.clear()
            changed = self._apply_drag_cell(row, col)
        elif self.tool == "cover":
            target = self._cover_target_at(event.position().toPoint())
            if target is None:
                return
            target_row, target_col, edge = target
            self._drag_tool = "cover"
            existing = self._cover_at(target_row, target_col)
            self._drag_enable = existing is None or edge not in existing.edges
            self._drag_seen.clear()
            changed = self._apply_cover_target(target_row, target_col, edge)
        elif self.tool == "arrow":
            self._reset_drag()
            if self._arrow_start is None:
                self._arrow_start = (row, col)
                self.update()
                return
            start_row, start_col = self._arrow_start
            self._arrow_start = None
            if (start_row, start_col) == (row, col):
                self.update()
                return
            if len(step.markers) >= MAX_MARKERS_PER_STEP:
                self.update()
                return
            step.markers.append(
                TacticMarker(
                    kind="arrow",
                    row=start_row,
                    col=start_col,
                    to_row=row,
                    to_col=col,
                    label=(self.arrow_label if self.arrow_label in {"1", "2", "3", "4", "5"} else "1"),
                    caption=(str(self.arrow_caption or "").strip()[:24]),
                )
            )
        else:
            self._reset_drag()
            if self.tool == "summon":
                kind = "summon"
                label = (self.summon_label or "*")[:12]
                unit_key = ""
            elif self.tool == "custom":
                kind = "custom"
                label = (self.custom_label or "기타")[:24]
                unit_key = ""
            else:
                kind = "unit"
                label = (self.unit_label or "?")[:12]
                unit_key = self.unit_key
            existing = next(
                (marker for marker in step.markers if marker.kind == kind and marker.row == row and marker.col == col),
                None,
            )
            if existing is None:
                if len(step.markers) >= MAX_MARKERS_PER_STEP:
                    return
                step.markers.append(TacticMarker(kind=kind, row=row, col=col, label=label, unit_key=unit_key))
            elif existing.label != label or existing.unit_key != unit_key:
                existing.label = label
                existing.unit_key = unit_key
            else:
                changed = False
        if changed:
            self.modified.emit()
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._reset_drag()
        if event.button() == Qt.MouseButton.LeftButton and self._move_press_cell is not None:
            moved = self._move_changed
            self._reset_move_drag()
            if moved:
                self._selected_marker = None
                self._selected_origin = None
                self.modified.emit()
                self.update()
        return super().mouseReleaseEvent(event)

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        draw_tactic_step(painter, self.tactic, self.step_index, self.rect())
        rows, cols = self._grid_size()
        board = self._board()
        if self._hover_cell is not None and self.editable:
            row, col = self._hover_cell
            hover = _cell_rect(board, rows, cols, row, col, inset=2)
            painter.setPen(QPen(_color(theme.ACCENT, 180), 2))
            painter.setBrush(_color(theme.ACCENT, 24))
            painter.drawRect(hover)
        if self._hover_cover_target is not None and self.editable and self.tool == "cover":
            row, col, edge = self._hover_cover_target
            cell_rect = _cell_rect(board, rows, cols, row, col, inset=2)
            painter.setPen(QPen(_color(theme.ACCENT, 235), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if edge == "N":
                painter.drawLine(QPointF(cell_rect.left(), cell_rect.top()), QPointF(cell_rect.right(), cell_rect.top()))
            elif edge == "E":
                painter.drawLine(QPointF(cell_rect.right(), cell_rect.top()), QPointF(cell_rect.right(), cell_rect.bottom()))
            elif edge == "S":
                painter.drawLine(QPointF(cell_rect.left(), cell_rect.bottom()), QPointF(cell_rect.right(), cell_rect.bottom()))
            else:
                painter.drawLine(QPointF(cell_rect.left(), cell_rect.top()), QPointF(cell_rect.left(), cell_rect.bottom()))
        if self._selected_marker is not None:
            marker = self._selected_marker
            if marker.kind == "arrow":
                end_row = marker.to_row if marker.to_row is not None else marker.row
                end_col = marker.to_col if marker.to_col is not None else marker.col
                start = _cell_center(board, rows, cols, marker.row, marker.col)
                end = _cell_center(board, rows, cols, end_row, end_col)
                painter.setPen(QPen(_color(theme.ACCENT, 230), 3, Qt.PenStyle.DashLine))
                painter.drawLine(start, end)
            else:
                rect = QRectF(
                    board.left() + marker.col * board.width() / cols + 2,
                    board.top() + marker.row * board.height() / rows + 2,
                    max(1.0, marker.width * board.width() / cols - 4),
                    max(1.0, marker.height * board.height() / rows - 4),
                )
                painter.setPen(QPen(_color(theme.ACCENT, 240), 3, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
        if self._arrow_start is not None:
            row, col = self._arrow_start
            center = _cell_center(board, rows, cols, row, col)
            painter.setPen(QPen(_color(theme.ACCENT), 2))
            painter.setBrush(_color(theme.ACCENT, 80))
            radius = max(5.0, min(board.width() / cols, board.height() / rows) * 0.18)
            painter.drawEllipse(center, radius, radius)


def _export_draw_step(painter: QPainter, tactic: Tactic, step_index: int, bounds: QRect) -> None:
    """Draw one tactic board with an OCR-friendly export palette.

    The interactive editor/overlay follows the live theme, but exported PNGs
    intentionally use a high-contrast light palette and stronger line weights so
    messengers and OCR engines preserve the grid and labels more reliably.
    User visual overrides still take precedence when explicitly configured.
    """
    if not tactic.steps:
        return
    rows, cols = tactic.grid_size(step_index)
    # Export boards always use a true white canvas.  Theme/custom background
    # tints are useful in the editor but lower OCR contrast in shared PNGs.
    board = _board_geometry(bounds, rows, cols, margin=3)
    painter.fillRect(board, QColor(theme.EXPORT_BACKGROUND))
    cw = board.width() / max(1, cols)
    ch = board.height() / max(1, rows)
    grid_pen = QPen(QColor(_visual("grid", theme.EXPORT_GRID)), 1.35)
    painter.setPen(grid_pen)
    for col in range(cols + 1):
        x = board.left() + col * cw
        painter.drawLine(QPointF(x, board.top()), QPointF(x, board.bottom()))
    for row in range(rows + 1):
        y = board.top() + row * ch
        painter.drawLine(QPointF(board.left(), y), QPointF(board.right(), y))

    step = tactic.steps[step_index]
    arrow_ordinal = 0
    for marker in step.markers:
        if marker.kind == "arrow":
            arrow_ordinal += 1
            end_row = marker.to_row if marker.to_row is not None else marker.row
            end_col = marker.to_col if marker.to_col is not None else marker.col
            painter.save()
            start = _cell_center(board, rows, cols, marker.row, marker.col)
            end = _cell_center(board, rows, cols, end_row, end_col)
            _draw_arrow(
                painter,
                start,
                end,
                label=_arrow_marker_label(marker, arrow_ordinal),
                color=QColor(_visual("arrow", theme.EXPORT_ARROW)),
                background=QColor(theme.EXPORT_BACKGROUND),
                width=max(3.0, min(cw, ch) * 0.055),
            )
            painter.restore()
            continue
        rect = QRectF(
            board.left() + marker.col * cw + 1,
            board.top() + marker.row * ch + 1,
            max(1.0, marker.width * cw - 2),
            max(1.0, marker.height * ch - 2),
        )
        if marker.kind == "blocked":
            painter.fillRect(rect, QColor(_visual("blocked", theme.EXPORT_BLOCKED)))
            continue
        if marker.kind == "cover":
            painter.save()
            # OCR recognises cover most reliably when it is rendered as a solid
            # gray edge band inside the cell, similar to the high-accuracy
            # reference sheets. Keep it fully inside the grid cell so it stays
            # distinct from the grid line itself.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.EXPORT_COVER))
            cell = _cell_rect(board, rows, cols, marker.row, marker.col, inset=0)
            thickness = max(3.0, min(cw, ch) * 0.13)
            inset = max(0.7, min(cw, ch) * 0.012)
            left = cell.left() + inset
            right = cell.right() - inset
            top = cell.top() + inset
            bottom = cell.bottom() - inset
            if "N" in marker.edges:
                painter.drawRect(QRectF(left, top, max(1.0, right - left), thickness))
            if "E" in marker.edges:
                painter.drawRect(QRectF(right - thickness, top, thickness, max(1.0, bottom - top)))
            if "S" in marker.edges:
                painter.drawRect(QRectF(left, bottom - thickness, max(1.0, right - left), thickness))
            if "W" in marker.edges:
                painter.drawRect(QRectF(left, top, thickness, max(1.0, bottom - top)))
            painter.restore()
            continue
        if marker.kind == "boss":
            painter.fillRect(rect, QColor(_visual("boss", theme.EXPORT_BOSS)))
            painter.setPen(QColor(_visual("text", theme.EXPORT_TEXT)))
            font = _export_font(
                pixel_size=max(12.0, min(rect.width(), rect.height()) * 0.235),
                weight=QFont.Weight.Bold,
            )
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, marker.label or "보스")
            continue
        if marker.kind == "custom":
            marker_color, fallback = "boss", theme.EXPORT_ARROW
        elif marker.kind == "summon":
            marker_color, fallback = "summon", theme.EXPORT_SUMMON
        else:
            marker_color, fallback = "unit", theme.EXPORT_TEXT
        # Summons/installations must never inherit the orange boss palette in
        # exported sheets; black is intentionally fixed for recognition tools.
        painter.setPen(QColor(theme.EXPORT_SUMMON if marker.kind == "summon" else _visual(marker_color, fallback)))
        label = marker.label if marker.kind in {"summon", "custom"} else tactic.marker_label(marker)
        label = (label or "*")[:12]
        if marker.kind == "summon":
            # A black heavyweight asterisk survives recompression better than
            # the normal unit-label weight and is deliberately special-cased.
            scale = 0.62 if label.strip() == "*" else 0.44
            summon_scale = 0.62 if label.strip() == "*" else 0.48
            font = _export_font(
                pixel_size=max(13.0, min(cw, ch) * summon_scale),
                weight=QFont.Weight.Black,
            )
        elif marker.kind == "custom":
            font = _export_font(
                pixel_size=max(11.0, min(cw, ch) * 0.56),
                weight=QFont.Weight.DemiBold,
            )
        else:
            font = _export_font(
                pixel_size=max(11.0, min(cw, ch) * 0.56),
                weight=QFont.Weight.DemiBold,
            )
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)


def _export_step_label(step_name: str, index: int) -> str:
    label = str(step_name or f"T{index + 1}").strip()
    if label.replace(" ", "") == "제대배치":
        return "제대 배치"
    return label


def _export_cycle_text(step, index: int) -> str:
    """Text shown in the per-step skill-cycle strip.

    Formation placement is a structural step rather than a skill rotation, so
    label that strip explicitly instead of exporting an OCR-hostile dash.
    """
    if _export_step_label(step.name, index).replace(" ", "") == "제대배치":
        return "제대 배치"
    return step.cycle.strip() or "—"


def _export_unit_values(unit) -> tuple[str, str, str, str, str, str]:
    return (
        f"{unit.name or '인형'}  {int(unit.rank)}돌",
        unit.weapon.strip() or "미입력",
        "\n".join(unit.unique_keys) if unit.unique_keys else "미장착",
        unit.expansion_label(include_prefix=False),
        "\n".join(unit.common_keys) if unit.common_keys else "미장착",
        unit.display_label(),
    )


def _export_text_units(value: str) -> int:
    total = 0
    for char in str(value):
        if char == "\n":
            continue
        total += 2 if ord(char) >= 0x2E80 else 1
    return total


def _estimate_export_row_height(values: tuple[str, ...], col_widths: list[int]) -> int:
    max_lines = 1
    for value, col_w in zip(values, col_widths):
        explicit = str(value).splitlines() or [""]
        capacity = max(8, int(max(1, col_w - 18) / 6.6))
        lines = 0
        for line in explicit:
            lines += max(1, math.ceil(_export_text_units(line) / capacity))
        max_lines = max(max_lines, lines)
    return max(82, min(190, 22 + max_lines * 19))


def _draw_cycle_summary(painter: QPainter, tactic: Tactic, rect: QRect) -> None:
    painter.setPen(QPen(QColor(theme.EXPORT_BORDER), 1.2))
    painter.setBrush(QColor(theme.EXPORT_PANEL))
    painter.drawRoundedRect(rect, 7, 7)
    rows = max(1, len(tactic.steps))
    top = rect.top() + 14
    left = rect.left() + 12
    width = rect.width() - 24
    row_h = max(31, min(48, (rect.height() - 28) // rows))
    label_w = 45
    small = _export_font(pixel_size=13, weight=QFont.Weight.Medium)
    for index, step in enumerate(tactic.steps):
        y = top + index * row_h
        if y + row_h > rect.bottom() - 4:
            break
        painter.fillRect(QRect(left, y, label_w, row_h), QColor(theme.EXPORT_PANEL_ALT))
        painter.setPen(QColor(theme.EXPORT_BORDER))
        painter.drawRect(QRect(left, y, width, row_h))
        painter.drawLine(left + label_w, y, left + label_w, y + row_h)
        painter.setFont(small)
        painter.setPen(QColor(theme.EXPORT_TEXT))
        bold = QFont(small)
        bold.setWeight(QFont.Weight.Bold)
        painter.setFont(bold)
        painter.drawText(
            QRect(left, y, label_w, row_h),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            _export_step_label(step.name, index),
        )
        painter.setFont(small)
        painter.drawText(
            QRect(left + label_w + 8, y + 2, width - label_w - 14, row_h - 4),
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
            _export_cycle_text(step, index),
        )


def render_tactic_sheet(
    tactic: Tactic,
    *,
    cell_panel: QSize = QSize(360, 420),
    export_scale: float = theme.EXPORT_SCALE,
) -> QImage:
    """Render the established GF2Tools tactic sheet at high pixel resolution.

    The original GF2Tools layout is deliberately preserved: title, board-card
    grid, optional cycle summary and character specification table keep their
    existing positions. Export-specific styling only changes contrast, line
    weight and palette so the sheet remains recognisable as GF2Tools while
    surviving messenger recompression and OCR round-trips more reliably.
    """
    has_cycles = any(
        step.cycle.strip() or _export_step_label(step.name, index).replace(" ", "") == "제대배치"
        for index, step in enumerate(tactic.steps)
    )
    item_count = len(tactic.steps) + (1 if has_cycles and tactic.steps else 0)
    columns = 3 if item_count >= 5 else 2 if item_count >= 3 else 1
    rows = max(1, math.ceil(max(1, item_count) / columns))
    margin = 18
    gap = 14
    title_h = 58
    panel_w = cell_panel.width()
    panel_h = cell_panel.height() if has_cycles else max(332, cell_panel.height() - 48)
    specs_header_h = 44
    width = margin * 2 + columns * panel_w + (columns - 1) * gap
    table_w = width - margin * 2 - 20
    ratios = (0.15, 0.18, 0.24, 0.14, 0.20, 0.09)
    col_widths = [int(table_w * r) for r in ratios]
    col_widths[-1] += table_w - sum(col_widths)
    units = tactic.units or []
    unit_values = [_export_unit_values(unit) for unit in units]
    row_heights = [_estimate_export_row_height(values, col_widths) for values in unit_values]
    if not row_heights:
        row_heights = [82]
    specs_h = specs_header_h + sum(row_heights) + 22
    grid_h = rows * panel_h + (rows - 1) * gap
    height = title_h + margin + grid_h + margin + specs_h + margin

    scale = max(1.0, float(export_scale))
    out_w = max(1, round(width * scale))
    out_h = max(1, round(height * scale))
    image = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(theme.EXPORT_BACKGROUND))
    # Do not override the QImage DPI. QFont point sizes use the paint device DPI;
    # forcing 300 DPI made text physically scale up inside the unchanged logical
    # layout. Pixel resolution is improved solely through painter scaling.
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.scale(scale, scale)

    title_font = _export_font(pixel_size=34, weight=QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.setPen(QColor(theme.EXPORT_TEXT))
    title_text = tactic.title + (f" · {tactic.category}" if tactic.category.strip() else "")
    title_rect = QRect(margin, 8, width - margin * 2, 42)
    painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, title_text)
    metrics = painter.fontMetrics()
    underline_w = max(68, min(metrics.horizontalAdvance(title_text), title_rect.width() - 20))
    underline_y = title_rect.bottom() + 1
    center_x = title_rect.center().x()
    painter.setPen(QPen(QColor(theme.EXPORT_BOSS), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(center_x - underline_w / 2, underline_y, center_x + underline_w / 2, underline_y)

    content_top = title_h + margin
    for index, step in enumerate(tactic.steps):
        col = index % columns
        row = index // columns
        left = margin + col * (panel_w + gap)
        top = content_top + row * (panel_h + gap)
        card = QRect(left, top, panel_w, panel_h)
        painter.setPen(QPen(QColor(theme.EXPORT_BORDER), 1.4))
        painter.setBrush(QColor(theme.EXPORT_PANEL))
        painter.drawRoundedRect(card, 7, 7)
        board_bottom = 48 if has_cycles else 12
        board_rect = QRect(left + 3, top + 3, panel_w - 6, panel_h - board_bottom - 3)
        _export_draw_step(painter, tactic, index, board_rect)
        if has_cycles:
            cycle_rect = QRect(left + 8, top + panel_h - 42, panel_w - 16, 32)
            painter.setPen(QColor(theme.EXPORT_BORDER))
            painter.drawRect(cycle_rect)
            cycle_font = _export_font(pixel_size=14, weight=QFont.Weight.Medium)
            painter.setFont(cycle_font)
            painter.setPen(QColor(theme.EXPORT_TEXT))
            painter.drawText(
                cycle_rect.adjusted(7, 1, -7, -1),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                _export_cycle_text(step, index),
            )

    if has_cycles and tactic.steps:
        index = len(tactic.steps)
        col = index % columns
        row = index // columns
        left = margin + col * (panel_w + gap)
        top = content_top + row * (panel_h + gap)
        _draw_cycle_summary(painter, tactic, QRect(left, top, panel_w, panel_h))

    specs_top = content_top + grid_h + margin
    small_caps = _export_font(pixel_size=14, weight=QFont.Weight.Bold)
    small_caps.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
    painter.setFont(small_caps)
    painter.setPen(QColor(theme.EXPORT_MUTED))
    painter.drawText(
        QRect(margin + 10, specs_top, width - margin * 2, 24),
        Qt.AlignmentFlag.AlignVCenter,
        "CHARACTER SPECS",
    )

    table_top = specs_top + 28
    table_left = margin + 10
    headers = ("인형 / 돌파", "무기", "고유키", "도약키", "공용키", "비고")
    painter.setPen(QColor(theme.EXPORT_BORDER))
    painter.drawLine(table_left, table_top + 28, table_left + table_w, table_top + 28)
    header_font = _export_font(pixel_size=12, weight=QFont.Weight.Medium)
    painter.setFont(header_font)
    x = table_left
    for label, col_w in zip(headers, col_widths):
        painter.setPen(QColor(theme.EXPORT_MUTED))
        painter.drawText(QRect(x + 8, table_top, col_w - 12, 28), Qt.AlignmentFlag.AlignVCenter, label)
        x += col_w

    row_font = _export_font(pixel_size=14, weight=QFont.Weight.Medium)
    y = table_top + 28
    for row_idx, row_h in enumerate(row_heights):
        painter.setPen(QColor(theme.EXPORT_BORDER))
        painter.drawLine(table_left, y + row_h, table_left + table_w, y + row_h)
        if row_idx >= len(units):
            painter.setPen(QColor(theme.EXPORT_MUTED))
            painter.setFont(row_font)
            painter.drawText(
                QRect(table_left + 8, y, table_w - 16, row_h),
                Qt.AlignmentFlag.AlignVCenter,
                "등록된 사용 인형 없음",
            )
            y += row_h
            continue
        values = unit_values[row_idx]
        x = table_left
        for col_idx, (value, col_w) in enumerate(zip(values, col_widths)):
            font = QFont(row_font)
            font.setBold(col_idx == 0)
            painter.setFont(font)
            painter.setPen(QColor(theme.EXPORT_TEXT))
            painter.drawText(
                QRect(x + 8, y + 4, col_w - 12, row_h - 8),
                Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                value,
            )
            x += col_w
        y += row_h

    painter.end()
    return image


class TacticSheetPreviewDialog(QDialog):
    def __init__(self, tactic: Tactic, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{tactic.title} · 전체 미리보기")
        self.resize(1040, 760)
        self.setMinimumSize(760, 560)
        root = dialog_layout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(12)
        has_cycles = any(
            step.cycle.strip() or _export_step_label(step.name, index).replace(" ", "") == "제대배치"
            for index, step in enumerate(tactic.steps)
        )
        for index, step in enumerate(tactic.steps):
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(8, 8, 8, 8)
            panel_layout.setSpacing(5)
            rows, cols = tactic.grid_size(index)
            title = QLabel(f"{step.name} · {rows}×{cols}")
            title.setObjectName("SectionTitle")
            panel_layout.addWidget(title)
            board = TacticGridWidget(tactic, editable=False)
            board.setMinimumSize(300, 300)
            board.set_step_index(index)
            panel_layout.addWidget(board, 1)
            if has_cycles:
                cycle = QLabel("스킬 사이클 · " + _export_cycle_text(step, index))
                cycle.setObjectName("SectionTitle")
                cycle.setWordWrap(True)
                panel_layout.addWidget(cycle)
            note = QLabel(step.note or "설명 없음")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            panel_layout.addWidget(note)
            grid.addWidget(panel, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
