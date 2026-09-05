from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme


_ICON_SIZE = 20
_RENDER_SIZE = 40


def _pen(color: str, width: float = 2.15) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _render(draw: Callable[[QPainter], None], color: str) -> QPixmap:
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(2.0, 2.0)
    painter.setPen(_pen(color))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def _home(p: QPainter) -> None:
    path = QPainterPath(QPointF(3.0, 9.0))
    path.lineTo(10.0, 3.3)
    path.lineTo(17.0, 9.0)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(5.0, 8.0, 10.0, 8.5), 1.1, 1.1)
    p.drawLine(QPointF(9.0, 16.5), QPointF(9.0, 12.0))
    p.drawLine(QPointF(9.0, 12.0), QPointF(12.0, 12.0))
    p.drawLine(QPointF(12.0, 12.0), QPointF(12.0, 16.5))



def _checklist(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 2.8, 13.0, 14.5), 1.6, 1.6)
    for y in (6.0, 10.0, 14.0):
        p.drawRoundedRect(QRectF(5.3, y - 1.1, 2.2, 2.2), 0.4, 0.4)
        p.drawLine(QPointF(8.8, y), QPointF(14.3, y))
    p.drawLine(QPointF(5.7, 5.9), QPointF(6.3, 6.5))
    p.drawLine(QPointF(6.3, 6.5), QPointF(7.2, 5.2))

def _inventory(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.2, 4.0, 13.6, 12.6), 1.5, 1.5)
    p.drawLine(QPointF(3.7, 8.0), QPointF(16.3, 8.0))
    p.drawLine(QPointF(7.6, 8.2), QPointF(7.6, 16.2))
    p.drawLine(QPointF(12.4, 8.2), QPointF(12.4, 16.2))
    p.drawLine(QPointF(8.2, 5.9), QPointF(11.8, 5.9))


def _formation(p: QPainter) -> None:
    for center in ((5.0, 6.0), (15.0, 6.0), (5.0, 14.0), (15.0, 14.0)):
        p.drawEllipse(QPointF(*center), 2.15, 2.15)
    p.drawLine(QPointF(7.2, 6.0), QPointF(12.8, 6.0))
    p.drawLine(QPointF(7.2, 14.0), QPointF(12.8, 14.0))
    p.drawLine(QPointF(5.0, 8.2), QPointF(5.0, 11.8))
    p.drawLine(QPointF(15.0, 8.2), QPointF(15.0, 11.8))
    p.drawLine(QPointF(6.6, 7.5), QPointF(13.4, 12.5))


def _optimizer(p: QPainter) -> None:
    p.drawEllipse(QPointF(10.0, 10.0), 6.2, 6.2)
    p.drawEllipse(QPointF(10.0, 10.0), 2.6, 2.6)
    p.drawLine(QPointF(10.0, 1.9), QPointF(10.0, 5.0))
    p.drawLine(QPointF(10.0, 15.0), QPointF(10.0, 18.1))
    p.drawLine(QPointF(1.9, 10.0), QPointF(5.0, 10.0))
    p.drawLine(QPointF(15.0, 10.0), QPointF(18.1, 10.0))
    p.drawLine(QPointF(10.0, 10.0), QPointF(14.2, 6.0))


def _cooking(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(4.0, 7.7, 12.0, 8.4), 2.2, 2.2)
    p.drawLine(QPointF(2.5, 9.4), QPointF(4.0, 9.4))
    p.drawLine(QPointF(16.0, 9.4), QPointF(17.5, 9.4))
    p.drawLine(QPointF(5.0, 6.0), QPointF(15.0, 6.0))
    p.drawLine(QPointF(7.8, 6.0), QPointF(8.7, 4.2))
    p.drawLine(QPointF(11.1, 6.0), QPointF(12.0, 4.2))
    p.drawLine(QPointF(7.0, 16.1), QPointF(7.0, 17.5))
    p.drawLine(QPointF(13.0, 16.1), QPointF(13.0, 17.5))


def _tactics(p: QPainter) -> None:
    p.drawEllipse(QPointF(10.0, 10.0), 5.5, 5.5)
    p.drawEllipse(QPointF(10.0, 10.0), 1.8, 1.8)
    p.drawLine(QPointF(10.0, 1.8), QPointF(10.0, 5.0))
    p.drawLine(QPointF(10.0, 15.0), QPointF(10.0, 18.2))
    p.drawLine(QPointF(1.8, 10.0), QPointF(5.0, 10.0))
    p.drawLine(QPointF(15.0, 10.0), QPointF(18.2, 10.0))


def _data_sync(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.2, 4.0, 8.6, 12.0), 1.7, 1.7)
    p.drawLine(QPointF(5.5, 7.2), QPointF(9.5, 7.2))
    p.drawLine(QPointF(5.5, 10.0), QPointF(8.5, 10.0))
    path = QPainterPath(QPointF(12.2, 8.4))
    path.lineTo(16.8, 8.4)
    path.lineTo(15.1, 6.7)
    p.drawPath(path)
    path = QPainterPath(QPointF(16.8, 11.7))
    path.lineTo(12.2, 11.7)
    path.lineTo(13.9, 13.4)
    p.drawPath(path)


def _backup(p: QPainter) -> None:
    p.drawEllipse(QPointF(10.0, 10.0), 6.4, 6.4)
    p.drawLine(QPointF(10.0, 10.0), QPointF(10.0, 6.3))
    p.drawLine(QPointF(10.0, 10.0), QPointF(13.0, 11.8))
    p.drawLine(QPointF(4.0, 5.5), QPointF(4.0, 2.8))
    p.drawLine(QPointF(4.0, 2.8), QPointF(6.7, 2.8))
    p.drawLine(QPointF(4.1, 3.0), QPointF(6.0, 4.6))


def _settings(p: QPainter) -> None:
    p.drawEllipse(QPointF(10.0, 10.0), 3.1, 3.1)
    for x1, y1, x2, y2 in (
        (10, 1.8, 10, 5.2), (10, 14.8, 10, 18.2),
        (1.8, 10, 5.2, 10), (14.8, 10, 18.2, 10),
        (4.2, 4.2, 6.4, 6.4), (13.6, 13.6, 15.8, 15.8),
        (15.8, 4.2, 13.6, 6.4), (6.4, 13.6, 4.2, 15.8),
    ):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.drawEllipse(QPointF(10.0, 10.0), 6.0, 6.0)


_DRAWERS: dict[str, Callable[[QPainter], None]] = {
    "dashboard": _home,
    "checklist": _checklist,
    "inventory": _inventory,
    "formation": _formation,
    "remolding_optimizer": _optimizer,
    "cooking": _cooking,
    "tactics": _tactics,
    "data_sync": _data_sync,
    "backup": _backup,
    "settings": _settings,
}


def nav_icon(name: str) -> QIcon:
    """Return a crisp theme-aware two-state navigation icon."""
    drawer = _DRAWERS.get(name, _inventory)
    icon = QIcon()
    icon.addPixmap(_render(drawer, theme.MUTED), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_render(drawer, theme.ACCENT), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(_render(drawer, theme.TEXT), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(_render(drawer, theme.ACCENT_HOVER), QIcon.Mode.Active, QIcon.State.On)
    return icon


def nav_icon_size() -> QSize:
    return QSize(_ICON_SIZE, _ICON_SIZE)
