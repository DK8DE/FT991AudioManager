"""Taste „gedrückt halten“ — funktioniert mit Maus und Touchscreen (PTT, T.CALL)."""

from __future__ import annotations

import sys
import time
import weakref
from typing import ClassVar, Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, Qt
from PySide6.QtGui import (
    QContextMenuEvent,
    QFocusEvent,
    QHideEvent,
    QMouseEvent,
    QTouchEvent,
)
from PySide6.QtWidgets import QApplication, QPushButton

# Mindest-Touchfläche (ca. Material/Finger-Richtwert).
_TOUCH_MIN = 44
# Kurzer synthetischer „Tap“ vom System (Touch→Maus) soll Halten nicht sofort beenden.
_SYNTH_RELEASE_IGNORE_S = 0.12

# Windows: langes Touch-Halten → WM_CONTEXTMENU / Rechtsklick.
_WM_CONTEXTMENU = 0x007B
_WM_RBUTTONDOWN = 0x0204
_WM_RBUTTONUP = 0x0205
_WM_NCRBUTTONDOWN = 0x00A4
_WM_NCRBUTTONUP = 0x00A5
_BLOCK_WIN_MSG = frozenset(
    {
        _WM_CONTEXTMENU,
        _WM_RBUTTONDOWN,
        _WM_RBUTTONUP,
        _WM_NCRBUTTONDOWN,
        _WM_NCRBUTTONUP,
    }
)


class _WinHoldContextMenuFilter(QAbstractNativeEventFilter):
    """Unter Windows Touch-„Rechtsklick“ während Halte-Tasten unterdrücken."""

    def nativeEventFilter(self, eventType, message) -> tuple[bool, int]:  # noqa: N802
        if not _MomentaryHoldButtonState.held:
            return False, 0
        if not _is_windows_generic_msg(eventType):
            return False, 0
        msg = _windows_msg_from_pointer(message)
        if msg is None:
            return False, 0
        if msg.message not in _BLOCK_WIN_MSG:
            return False, 0
        return True, 0


class _MomentaryHoldButtonState:
    held: ClassVar[weakref.WeakSet[MomentaryHoldButton]] = weakref.WeakSet()
    _filter_installed: ClassVar[bool] = False


def _is_windows_generic_msg(eventType) -> bool:
    if isinstance(eventType, (bytes, bytearray)):
        return eventType == b"windows_generic_MSG"
    data = getattr(eventType, "data", None)
    if callable(data):
        try:
            return data() == b"windows_generic_MSG"
        except TypeError:
            pass
    return str(eventType) == "windows_generic_MSG"


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


def _ensure_windows_hold_filter() -> None:
    if sys.platform != "win32" or _MomentaryHoldButtonState._filter_installed:
        return
    app = QApplication.instance()
    if app is None:
        return
    app.installNativeEventFilter(_WinHoldContextMenuFilter())
    _MomentaryHoldButtonState._filter_installed = True


class MomentaryHoldButton(QPushButton):
    """Wie QPushButton, aber ``pressed``/``released`` bleiben bis Finger/Maus wirklich los."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        _ensure_windows_hold_filter()
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        # Erster Touch soll sofort drücken — nicht erst Fokus setzen (Windows-Touchscreen).
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Windows: langes Halten sonst Kontextmenü (Touch-Rechtsklick).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._hold_active = False
        self._touch_point_ids: set[int] = set()
        self._pointer_grabbed = False
        self._engage_mono = 0.0
        self._apply_touch_minimum_size()

    def _apply_touch_minimum_size(self) -> None:
        self.setMinimumSize(
            max(self.minimumWidth(), _TOUCH_MIN),
            max(self.minimumHeight(), _TOUCH_MIN),
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
        self._engage_mono = 0.0
        self.setDown(False)
        self._release_pointer_grab()
        self.released.emit()

    def _using_touch(self) -> bool:
        return bool(self._touch_point_ids)

    def _ignore_synthetic_release(self, event: QMouseEvent) -> bool:
        if not self._hold_active or self._using_touch():
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
        if self._using_touch():
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
        if self._using_touch():
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
        # Fokuswechsel darf aktives Halten nicht abbrechen (Touch erzeugt oft Fokus-Chaos).
        if not self._hold_active:
            super().focusOutEvent(event)

    def hideEvent(self, event: QHideEvent | None) -> None:
        self._disengage()
        super().hideEvent(event)

    def _handle_touch_point(self, pid: int, state: Qt.TouchPointState, pos) -> None:
        if state == Qt.TouchPointState.TouchPointPressed:
            self._touch_point_ids.add(pid)
            self._grab_pointer()
            self._apply_touch_position(pos)
        elif state == Qt.TouchPointState.TouchPointUpdated:
            if pid in self._touch_point_ids:
                self._apply_touch_position(pos)
        elif state in (
            Qt.TouchPointState.TouchPointReleased,
            Qt.TouchPointState.TouchPointCanceled,
        ):
            self._touch_point_ids.discard(pid)
            if not self._touch_point_ids:
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
