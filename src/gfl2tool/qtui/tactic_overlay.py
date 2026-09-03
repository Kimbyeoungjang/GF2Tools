from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..settings import OverlayAppearance
from ..tactics import Tactic
from ..settings import windows_hotkey_keys
from . import theme
from .app_settings import AppSettings
from .dialogs.tactic_visuals import TacticVisualSettingsDialog
from .tactic_widgets import TacticGridWidget
from .widgets import dialog_layout


class _OverlayAppearanceDialog(QDialog):
    """Live overlay size and five-group tactic color controls."""

    def __init__(self, overlay: "TacticOverlayWindow"):
        super().__init__(overlay)
        self.overlay = overlay
        self.setWindowTitle("오버레이 표시 조절")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(500, 260)
        root = dialog_layout(self)

        intro = QLabel(
            "오버레이를 보면서 크기와 투명도를 바로 조절할 수 있습니다. "
            "색상 설정은 별도 창에서 현재 적용 색을 확인하며 변경할 수 있습니다."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        root.addWidget(intro)

        self.width_value = QLabel()
        self.width_slider = self._slider(420, 1400, overlay.width(), self._width_changed)
        self._add_slider_row(root, "너비", self.width_slider, self.width_value)

        self.height_value = QLabel()
        self.height_slider = self._slider(520, 1600, overlay.height(), self._height_changed)
        self._add_slider_row(root, "높이", self.height_slider, self.height_value)

        self.opacity_value = QLabel()
        self.opacity_slider = self._slider(25, 100, overlay.opacity_slider.value(), self._opacity_changed)
        self._add_slider_row(root, "투명도", self.opacity_slider, self.opacity_value)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("색상"))
        colors = QPushButton("색상 설정 새 창에서 열기")
        colors.setObjectName("AccentButton")
        colors.clicked.connect(self._open_tactic_colors)
        color_row.addWidget(colors)
        color_row.addStretch(1)
        root.addLayout(color_row)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        root.addLayout(close_row)
        self._sync_labels()

    @staticmethod
    def _slider(minimum: int, maximum: int, value: int, slot) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(max(minimum, min(maximum, int(value))))
        slider.valueChanged.connect(slot)
        return slider

    @staticmethod
    def _add_slider_row(root, label: str, slider: QSlider, value_label: QLabel) -> None:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setMinimumWidth(52)
        value_label.setMinimumWidth(70)
        row.addWidget(name)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        root.addLayout(row)

    def _width_changed(self, value: int) -> None:
        self.overlay.resize(value, self.overlay.height())
        self.overlay._persist_appearance_size()
        self._sync_labels()

    def _height_changed(self, value: int) -> None:
        self.overlay.resize(self.overlay.width(), value)
        self.overlay._persist_appearance_size()
        self._sync_labels()

    def _opacity_changed(self, value: int) -> None:
        self.overlay.opacity_slider.setValue(value)
        self._sync_labels()

    def _open_tactic_colors(self) -> None:
        TacticVisualSettingsDialog(
            self.overlay._settings,
            on_changed=self.overlay.grid.refresh_theme,
            parent=self,
        ).exec()

    def _sync_labels(self) -> None:
        self.width_value.setText(f"{self.overlay.width()} px")
        self.height_value.setText(f"{self.overlay.height()} px")
        self.opacity_value.setText(f"{self.overlay.opacity_slider.value()}%")


class TacticOverlayWindow(QWidget):
    stateSaved = Signal(str, object)

    def __init__(
        self,
        tactic: Tactic,
        *,
        start_index: int = 0,
        saved_state: dict[str, Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.tactic = Tactic.from_dict(tactic.to_dict(include_id=True), preserve_id=True)
        self.index = max(0, min(int(start_index), len(self.tactic.steps) - 1))
        self.locked = False
        self._drag_origin: QPoint | None = None
        self._window_origin: QPoint | None = None
        self._key_state: dict[str, bool] = {}
        self._global_hotkeys: dict[str, tuple[tuple[int, ...], object]] = {}
        self._user32 = self._load_windows_user32() if sys.platform == "win32" else None
        self._settings = AppSettings()
        self._appearance = self._settings.overlay_appearance()
        self.setWindowTitle("GFL2 택틱 오버레이")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("TacticOverlay")
        self._build_ui()
        self._restore_state(saved_state or {})
        self.apply_runtime_settings()
        self._global_poll = QTimer(self)
        self._global_poll.setInterval(50)
        self._global_poll.timeout.connect(self._poll_global_hotkeys)
        if sys.platform == "win32":
            self._global_poll.start()
        self._refresh()

    @staticmethod
    def _load_windows_user32():
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = ctypes.c_short
            return user32
        except (AttributeError, OSError, TypeError):
            return None

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title_row = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setObjectName("OverlayTitle")
        title_row.addWidget(self.title_label, 1)
        appearance = QPushButton("⚙ 표시 설정")
        appearance.setToolTip("오버레이가 떠 있는 상태에서 크기·투명도를 조절하고 색상 설정 창을 엽니다.")
        appearance.clicked.connect(self._show_appearance_dialog)
        title_row.addWidget(appearance)
        root.addLayout(title_row)

        self.grid = TacticGridWidget(self.tactic, editable=False)
        self.grid.setMinimumSize(360, 360)
        root.addWidget(self.grid, 1)

        self.cycle = QLabel()
        self.cycle.setWordWrap(True)
        self.cycle.setObjectName("OverlayCycle")
        root.addWidget(self.cycle)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("OverlayNote")
        root.addWidget(self.note)

        controls = QHBoxLayout()
        previous = QPushButton("◀ 이전")
        following = QPushButton("다음 ▶")
        self.lock_button = QPushButton("🔓 조작 가능")
        close = QPushButton("닫기")
        previous.clicked.connect(self.previous_step)
        following.clicked.connect(self.next_step)
        self.lock_button.clicked.connect(self.toggle_lock)
        close.clicked.connect(self.close)
        controls.addWidget(previous)
        controls.addWidget(following)
        controls.addWidget(self.lock_button)
        controls.addStretch(1)
        controls.addWidget(close)
        controls.addWidget(QSizeGrip(self))
        root.addLayout(controls)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("투명도"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(25, 100)
        self.opacity_slider.setValue(84)
        self.opacity_slider.valueChanged.connect(self._opacity_changed)
        opacity_row.addWidget(self.opacity_slider, 1)
        root.addLayout(opacity_row)

        QShortcut(QKeySequence("Left"), self, activated=self.previous_step)
        QShortcut(QKeySequence("Right"), self, activated=self.next_step)
        QShortcut(QKeySequence("Space"), self, activated=self.toggle_lock)

    def _restore_state(self, state: dict[str, Any]) -> None:
        try:
            opacity = max(25, min(100, int(state.get("opacity", 84))))
            self.opacity_slider.setValue(opacity)
            if state.get("x") is not None and state.get("y") is not None:
                self.move(int(state["x"]), int(state["y"]))
            if state.get("width") is not None and state.get("height") is not None:
                self.resize(int(state["width"]), int(state["height"]))
        except (TypeError, ValueError):
            return

    def _refresh(self) -> None:
        step = self.tactic.steps[self.index]
        self.grid.set_step_index(self.index)
        self.title_label.setText(
            f"{self.tactic.title}  ·  {step.name}  ({self.index + 1}/{len(self.tactic.steps)})"
        )
        has_cycles = any(item.cycle.strip() for item in self.tactic.steps)
        self.cycle.setVisible(has_cycles)
        self.cycle.setText("스킬 사이클 · " + (step.cycle.strip() or "—"))
        note = step.note.strip()
        self.note.setVisible(bool(note))
        self.note.setText(note)

    def previous_step(self) -> None:
        self.index = max(0, self.index - 1)
        self._refresh()

    def next_step(self) -> None:
        self.index = min(len(self.tactic.steps) - 1, self.index + 1)
        self._refresh()

    def toggle_lock(self) -> None:
        self.locked = not self.locked
        self.lock_button.setText("🔒 클릭 통과" if self.locked else "🔓 조작 가능")
        self._set_click_through(self.locked)

    def _set_click_through(self, enabled: bool) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        if self._user32 is None:
            return
        try:
            hwnd = int(self.winId())
            gwl_exstyle = -20
            ws_ex_transparent = 0x00000020
            ws_ex_layered = 0x00080000
            get_style = getattr(self._user32, "GetWindowLongPtrW", self._user32.GetWindowLongW)
            set_style = getattr(self._user32, "SetWindowLongPtrW", self._user32.SetWindowLongW)
            style = int(get_style(hwnd, gwl_exstyle))
            if enabled:
                style |= ws_ex_transparent | ws_ex_layered
            else:
                style &= ~ws_ex_transparent
            set_style(hwnd, gwl_exstyle, style)
        except (AttributeError, OSError, TypeError, ValueError):
            return

    def _apply_appearance_style(self) -> None:
        # Window chrome follows the application theme. User color customization
        # is intentionally limited to the five tactic palette groups so the
        # overlay cannot end up with several overlapping color systems.
        self.setStyleSheet(
            "QWidget#TacticOverlay {"
            f"background-color: {theme.PANEL}; color: {theme.TEXT}; border: 1px solid {theme.ACCENT};"
            "}"
            f"QLabel#OverlayTitle {{ color: {theme.ACCENT}; font-weight: 700; }}"
            f"QLabel#OverlayCycle, QLabel#OverlayNote {{ color: {theme.TEXT}; }}"
        )
        self.grid.refresh_theme()

    def _persist_appearance(self) -> None:
        self._settings.set_overlay_appearance(self._appearance)
        self._settings.sync()

    def _persist_appearance_size(self) -> None:
        self._appearance = OverlayAppearance(
            width=self.width(),
            height=self.height(),
            background=self._appearance.background,
            text=self._appearance.text,
            accent=self._appearance.accent,
        ).normalized()
        self._persist_appearance()

    def set_appearance_color(self, key: str, value: str, *, persist: bool = True) -> None:
        payload = self._appearance.as_dict()
        payload[key] = value
        self._appearance = OverlayAppearance(**payload).normalized()
        self._apply_appearance_style()
        if persist:
            self._persist_appearance()

    def _show_appearance_dialog(self) -> None:
        if self.locked:
            self.toggle_lock()
        _OverlayAppearanceDialog(self).exec()

    def _opacity_changed(self, value: int) -> None:
        self.setWindowOpacity(value / 100.0)

    def apply_runtime_settings(self) -> None:
        app_settings = AppSettings()
        settings = app_settings.overlay_hotkeys()
        appearance = app_settings.overlay_appearance()
        self._appearance = appearance
        self.resize(appearance.width, appearance.height)
        self._apply_appearance_style()
        actions = {
            "previous": (settings.previous, self.previous_step),
            "next": (settings.next, self.next_step),
            "toggle_lock": (settings.toggle_lock, self.toggle_lock),
        }
        resolved: dict[str, tuple[tuple[int, ...], object]] = {}
        for name, (sequence, action) in actions.items():
            try:
                resolved[name] = (windows_hotkey_keys(sequence), action)
            except ValueError:
                defaults = AppSettings.DEFAULT_HOTKEYS.as_dict()
                resolved[name] = (windows_hotkey_keys(defaults[name]), action)
        self._global_hotkeys = resolved
        self._key_state.clear()

    def _poll_global_hotkeys(self) -> None:
        if self._user32 is None:
            self._global_poll.stop()
            return
        try:
            pressed = lambda vk: bool(self._user32.GetAsyncKeyState(vk) & 0x8000)
            for name, (keys, action) in self._global_hotkeys.items():
                active = all(pressed(key) for key in keys)
                previous = self._key_state.get(name, False)
                if active and not previous:
                    action()
                self._key_state[name] = active
        except (AttributeError, OSError, TypeError, ValueError):
            self._global_poll.stop()

    def mousePressEvent(self, event):  # noqa: N802
        if self.locked or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._drag_origin = event.globalPosition().toPoint()
        self._window_origin = self.pos()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self.locked or self._drag_origin is None or self._window_origin is None:
            return super().mouseMoveEvent(event)
        delta = event.globalPosition().toPoint() - self._drag_origin
        self.move(self._window_origin + delta)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_origin = None
        self._window_origin = None
        return super().mouseReleaseEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_appearance") and self.isVisible():
            # QSizeGrip and manual live controls both persist the final visual size.
            self._persist_appearance_size()

    def _state(self) -> dict[str, int]:
        return {
            "x": int(self.x()),
            "y": int(self.y()),
            "width": int(self.width()),
            "height": int(self.height()),
            "opacity": int(self.opacity_slider.value()),
        }

    def closeEvent(self, event: QCloseEvent):  # noqa: N802
        self._global_poll.stop()
        self._persist_appearance_size()
        if self.locked:
            self._set_click_through(False)
        self.stateSaved.emit(self.tactic.tactic_id, self._state())
        super().closeEvent(event)
