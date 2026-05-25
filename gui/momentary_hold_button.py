"""Taste „gedrückt halten“ — funktioniert mit Maus und Touchscreen (PTT, T.CALL)."""

from __future__ import annotations

import sys
import time
import weakref
from typing import ClassVar, Optional

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QObject,
    Qt,
)
from PySide6.QtGui import (
    QContextMenuEvent,
    QFocusEvent,
    QHideEvent,
    QMouseEvent,
    QTouchEvent,
)
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from gui.windows_touch_suppress import (
    restore_windows_touch_press_and_hold,
    suppress_windows_touch_press_and_hold,
)

# Mindest-Touchfläche (ca. Material/Finger-Richtwert).
_TOUCH_MIN = 44
# Kurzer synthetischer „Tap“ vom System (Touch→Maus) soll Halten nicht sofort beenden.
_SYNTH_RELEASE_IGNORE_S = 0.12
# Windows „Drücken und Halten“-Fenster (ca. 500 ms) — TouchRelease dort ignorieren.
_WIN_PRESS_HOLD_CANCEL_MIN_S = 0.35
_WIN_PRESS_HOLD_CANCEL_MAX_S = 1.5

# Windows: langes Touch-Halten → WM_CONTEXTMENU / Rechtsklick / Geste.
_WM_CONTEXTMENU = 0x007B
_WM_RBUTTONDOWN = 0x0204
_WM_RBUTTONUP = 0x0205
_WM_RBUTTONDBLCLK = 0x0206
_WM_NCRBUTTONDOWN = 0x00A4
_WM_NCRBUTTONUP = 0x00A5
_WM_GESTURE = 0x0119
_WM_GESTURENOTIFY = 0x0116
_BLOCK_WIN_MSG = frozenset(
    {
        _WM_CONTEXTMENU,
        _WM_RBUTTONDOWN,
        _WM_RBUTTONUP,
        _WM_RBUTTONDBLCLK,
        _WM_NCRBUTTONDOWN,
        _WM_NCRBUTTONUP,
        _WM_GESTURE,
        _WM_GESTURENOTIFY,
    }
)


def _is_windows_generic_msg(eventType) -> bool:
    raw = eventType
    if hasattr(raw, "data"):
        try:
            raw = raw.data()
        except TypeError:
            raw = bytes(raw)
    if isinstance(raw, (bytes, bytearray, memoryview)):
        text = bytes(raw).decode("ascii", errors="ignore").lower()
    else:
        text = str(raw).lower()
    return "windows" in text and "msg" in text


def _windows_msg_from_pointer(message):
    if sys.platform != "win32":
        return None
    import ctypes

    class _Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _Msg(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_size_t),
            ("lParam", ctypes.c_size_t),
            ("time", ctypes.c_uint),
            ("pt", _Point),
        ]

    try:
        addr = int(message)
    except (TypeError, ValueError):
        return None
    if addr == 0:
        return None
    return _Msg.from_address(addr)


def _widget_is_hold_button(widget: QWidget | None) -> bool:
    while widget is not None:
        if isinstance(widget, MomentaryHoldButton):
            return True
        widget = widget.parentWidget()
    return False


def _global_pos_for_event(event: QEvent) -> Optional[tuple[int, int]]:
    if isinstance(event, QContextMenuEvent):
        pos = event.globalPos()
        return int(pos.x()), int(pos.y())
    if isinstance(event, QMouseEvent):
        pos = event.globalPosition()
        return int(pos.x()), int(pos.y())
    return None


class _MomentaryHoldButtonState:
    buttons: ClassVar[weakref.WeakSet[MomentaryHoldButton]] = weakref.WeakSet()
    held: ClassVar[weakref.WeakSet[MomentaryHoldButton]] = weakref.WeakSet()
    _filter_installed: ClassVar[bool] = False
    _window_filters: ClassVar[weakref.WeakSet[QObject]] = weakref.WeakSet()


class _WinHoldContextMenuFilter(QAbstractNativeEventFilter):
    """Windows Touch-Rechtsklick / Kontextmenü-Nachrichten abfangen."""

    def nativeEventFilter(self, eventType, message) -> tuple[bool, int]:  # noqa: N802
        if sys.platform != "win32" or not _is_windows_generic_msg(eventType):
            return False, 0
        msg = _windows_msg_from_pointer(message)
        if msg is None or msg.message not in _BLOCK_WIN_MSG:
            return False, 0
        if _MomentaryHoldButtonState.held:
            return True, 0
        if _hwnd_is_hold_button(msg.hwnd):
            return True, 0
        return False, 0


def _hwnd_is_hold_button(hwnd) -> bool:
    if hwnd is None:
        return False
    try:
        target = int(hwnd)
    except (TypeError, ValueError):
        return False
    if target == 0:
        return False
    for btn in list(_MomentaryHoldButtonState.buttons):
        try:
            if int(btn.winId()) == target:
                return True
        except RuntimeError:
            continue
    return False


class _WindowHoldButtonFilter(QObject):
    """Qt-Ebene: Kontextmenü / Rechtsklick über Halte-Tasten verwerfen."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        del watched
        et = event.type()
        if et == QEvent.Type.ContextMenu:
            return True
        if et in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            if not isinstance(event, QMouseEvent):
                return False
            if event.button() != Qt.MouseButton.RightButton:
                return False
            gpos = _global_pos_for_event(event)
            if gpos is None:
                return False
            app = QApplication.instance()
            if app is None:
                return False
            w = app.widgetAt(gpos[0], gpos[1])
            if _widget_is_hold_button(w):
                event.accept()
                return True
        return False


def _ensure_windows_hold_filter() -> None:
    if _MomentaryHoldButtonState._filter_installed:
        return
    suppress_windows_touch_press_and_hold()
    app = QApplication.instance()
    if app is None:
        return
    if sys.platform == "win32":
        app.installNativeEventFilter(_WinHoldContextMenuFilter())
    _MomentaryHoldButtonState._filter_installed = True


def _ensure_window_filter_for(button: MomentaryHoldButton) -> None:
    win = button.window()
    if win is None:
        return
    for existing in _MomentaryHoldButtonState._window_filters:
        if existing.parent() is win:
            return
    flt = _WindowHoldButtonFilter(win)
    flt.setParent(win)
    win.installEventFilter(flt)
    _MomentaryHoldButtonState._window_filters.add(flt)


class MomentaryHoldButton(QPushButton):
    """Wie QPushButton, aber ``pressed``/``released`` bleiben bis Finger/Maus wirklich los."""

    def __init__(
        self,
        *args,
        touch_min: int = _TOUCH_MIN,
        touch_min_width_only: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        _ensure_windows_hold_filter()
        _MomentaryHoldButtonState.buttons.add(self)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._hold_active = False
        self._touch_point_ids: set[int] = set()
        self._touch_canceled_await_release = False
        self._pointer_grabbed = False
        self._engage_mono = 0.0
        self._apply_touch_minimum_size(
            touch_min=touch_min,
            width_only=touch_min_width_only,
        )

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        _ensure_window_filter_for(self)
        super().showEvent(event)

    def _apply_touch_minimum_size(
        self,
        *,
        touch_min: int = _TOUCH_MIN,
        width_only: bool = False,
    ) -> None:
        if width_only:
            self.setMinimumWidth(max(self.minimumWidth(), touch_min))
            return
        self.setMinimumSize(
            max(self.minimumWidth(), touch_min),
            max(self.minimumHeight(), touch_min),
        )

    def is_held(self) -> bool:
        return bool(self._hold_active)

    def release_hold(self) -> None:
        """Halten abbrechen (z. B. Fenster verliert Fokus)."""
        self._disengage()

    def _ensure_window_active(self) -> None:
        win = self.window()
        if win is None:
            return
        if not win.isActiveWindow():
            win.raise_()
            win.activateWindow()

    def _grab_pointer(self) -> None:
        if not self._pointer_grabbed:
            self.grabMouse()
            self._pointer_grabbed = True

    def _release_pointer_grab(self) -> None:
        if self._pointer_grabbed:
            self.releaseMouse()
            self._pointer_grabbed = False

    def _register_hold(self) -> None:
        _MomentaryHoldButtonState.held.add(self)

    def _unregister_hold(self) -> None:
        _MomentaryHoldButtonState.held.discard(self)

    def _engage(self) -> None:
        if self._hold_active:
            return
        self._ensure_window_active()
        self._hold_active = True
        self._touch_canceled_await_release = False
        self._register_hold()
        self._engage_mono = time.monotonic()
        self.setDown(True)
        self.pressed.emit()

    def _disengage(self) -> None:
        if not self._hold_active:
            return
        self._hold_active = False
        self._unregister_hold()
        self._touch_point_ids.clear()
        self._touch_canceled_await_release = False
        self._engage_mono = 0.0
        self.setDown(False)
        self._release_pointer_grab()
        self.released.emit()

    def _using_touch(self) -> bool:
        return bool(self._touch_point_ids)

    def _allow_mouse_release_while_touch(self) -> bool:
        return self._touch_canceled_await_release and not self._using_touch()

    def _ignore_synthetic_release(self, event: QMouseEvent) -> bool:
        if not self._hold_active:
            return False
        if self._using_touch() and not self._touch_canceled_await_release:
            return False
        if event.source() != Qt.MouseEventSource.MouseEventSynthesizedBySystem:
            return False
        if self._engage_mono <= 0.0:
            return False
        return (time.monotonic() - self._engage_mono) < _SYNTH_RELEASE_IGNORE_S

    def nativeEvent(self, eventType, message):  # noqa: N802
        if sys.platform == "win32" and _is_windows_generic_msg(eventType):
            msg = _windows_msg_from_pointer(message)
            if msg is not None and msg.message in _BLOCK_WIN_MSG:
                return True, 0
        return super().nativeEvent(eventType, message)

    def event(self, event: QEvent | None) -> bool:
        if event is None:
            return super().event(event)
        et = event.type()
        if et == QEvent.Type.ContextMenu:
            event.accept()
            return True
        if et in (QEvent.Type.Gesture, QEvent.Type.GestureOverride):
            event.accept()
            return True
        if et == QEvent.Type.TouchBegin:
            self._ensure_window_active()
        return super().event(event)

    def contextMenuEvent(self, event: QContextMenuEvent | None) -> None:
        if event is not None:
            event.accept()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        if self._using_touch() and not self._touch_canceled_await_release:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab_pointer()
            self._engage()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        if self._using_touch() and not self._allow_mouse_release_while_touch():
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._ignore_synthetic_release(event):
                event.accept()
                return
            self._disengage()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:
        if not self._hold_active:
            super().focusOutEvent(event)

    def hideEvent(self, event: QHideEvent | None) -> None:
        self._disengage()
        super().hideEvent(event)

    def _windows_spurious_touch_release(self) -> bool:
        if sys.platform != "win32" or not self._hold_active or self._engage_mono <= 0.0:
            return False
        elapsed = time.monotonic() - self._engage_mono
        return _WIN_PRESS_HOLD_CANCEL_MIN_S <= elapsed <= _WIN_PRESS_HOLD_CANCEL_MAX_S

    def _handle_touch_point(self, pid: int, state: Qt.TouchPointState, pos) -> None:
        if state == Qt.TouchPointState.TouchPointPressed:
            self._touch_canceled_await_release = False
            self._touch_point_ids.add(pid)
            self._grab_pointer()
            self._apply_touch_position(pos)
        elif state in (
            Qt.TouchPointState.TouchPointMoved,
            Qt.TouchPointState.TouchPointStationary,
        ):
            if pid in self._touch_point_ids:
                self._apply_touch_position(pos)
        elif state == Qt.TouchPointState.TouchPointReleased:
            self._touch_point_ids.discard(pid)
            if not self._touch_point_ids:
                if self._windows_spurious_touch_release():
                    # Windows bricht Touch für Rechtsklick ab — Halten bis Maus-Release.
                    self._touch_canceled_await_release = True
                    return
                self._touch_canceled_await_release = False
                self._disengage()

    def _apply_touch_position(self, pos) -> None:
        del pos
        self._engage()

    def touchEvent(self, event: QTouchEvent | None) -> None:
        if event is None:
            return
        for point in event.points():
            self._handle_touch_point(
                int(point.id()),
                point.state(),
                point.position(),
            )
        event.accept()
