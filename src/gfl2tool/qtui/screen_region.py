from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent, QScreen, QShortcut
from PySide6.QtWidgets import QDialog, QLabel, QRubberBand, QVBoxLayout


class ScreenRegionSelector(QDialog):
    """Local screen-region picker with reliable Escape cancellation."""

    def __init__(self, screen: QScreen, parent=None):
        super().__init__(parent)
        self.screen = screen
        self.selected_rect: QRect | None = None
        self._origin: QPoint | None = None
        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)

        self.setWindowTitle("OCR 캡처 영역 선택")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowOpacity(0.48)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        hint = QLabel("OCR로 계속 감시할 영역을 드래그하세요. Esc를 누르면 취소합니다.")
        hint.setObjectName("Panel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(hint, 0, Qt.AlignmentFlag.AlignTop)
        root.addStretch(1)

        # Some Windows/game-overlay combinations send Escape to the modal
        # application rather than this child widget. ApplicationShortcut keeps
        # cancel reliable while the picker is open without installing a global
        # system hotkey.
        self._escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._escape.activated.connect(self._cancel)
        QTimer.singleShot(0, self._take_focus)

    def _take_focus(self) -> None:
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # Full-screen games can keep keyboard focus even while this top-level
        # selector receives mouse events. Grab the keyboard for the selector's
        # short lifetime so Escape always reaches Qt; release on close.
        self.grabKeyboard()

    def done(self, result: int) -> None:
        if self.keyboardGrabber() is self:
            self.releaseKeyboard()
        super().done(result)

    def _cancel(self) -> None:
        self.selected_rect = None
        self._origin = None
        self._rubber.hide()
        self.reject()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self._rubber.setGeometry(rect)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self._origin = None
            if rect.width() >= 24 and rect.height() >= 24:
                self.selected_rect = rect.intersected(self.rect())
                self.accept()
            else:
                self._rubber.hide()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
            return
        super().keyPressEvent(event)
