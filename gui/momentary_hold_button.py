"""Taste „gedrückt halten“ — funktioniert mit Maus und Touchscreen (PTT)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFocusEvent, QHideEvent, QMouseEvent, QTouchEvent
from PySide6.QtWidgets import QPushButton


class MomentaryHoldButton(QPushButton):
    """Wie QPushButton, aber ``pressed``/``released`` bleiben bis Finger/Maus wirklich los."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._hold_active = False
        self._touch_point_ids: set[int] = set()
        self._mouse_grabbed = False

    def is_held(self) -> bool:
        return bool(self._hold_active)

    def release_hold(self) -> None:
        """Halten abbrechen (z. B. Fenster verliert Fokus)."""
        self._disengage()

    def _engage(self) -> None:
        if self._hold_active:
            return
        self._hold_active = True
        self.setDown(True)
        self.pressed.emit()

    def _disengage(self) -> None:
        if not self._hold_active:
            return
        self._hold_active = False
        self._touch_point_ids.clear()
        self.setDown(False)
        if self._mouse_grabbed:
            self.releaseMouse()
            self._mouse_grabbed = False
        self.released.emit()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._touch_point_ids:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.grabMouse()
            self._mouse_grabbed = True
            self._engage()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._touch_point_ids:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._disengage()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:
        self._disengage()
        super().focusOutEvent(event)

    def hideEvent(self, event: QHideEvent | None) -> None:
        self._disengage()
        super().hideEvent(event)

    def touchEvent(self, event: QTouchEvent | None) -> None:
        if event is None:
            return
        for point in event.points():
            pid = int(point.id())
            state = point.state()
            if state == Qt.TouchPointState.TouchPointPressed:
                self._touch_point_ids.add(pid)
                self._engage()
            elif state in (
                Qt.TouchPointState.TouchPointReleased,
                Qt.TouchPointState.TouchPointCanceled,
            ):
                self._touch_point_ids.discard(pid)
                if not self._touch_point_ids:
                    self._disengage()
        event.accept()
