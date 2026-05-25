"""Taste „gedrückt halten“ — funktioniert mit Maus und Touchscreen (PTT, T.CALL)."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent, QHideEvent, QMouseEvent, QTouchEvent
from PySide6.QtWidgets import QPushButton

# Mindest-Touchfläche (ca. Material/Finger-Richtwert).
_TOUCH_MIN = 44
# Kurzer synthetischer „Tap“ vom System (Touch→Maus) soll Halten nicht sofort beenden.
_SYNTH_RELEASE_IGNORE_S = 0.12


class MomentaryHoldButton(QPushButton):
    """Wie QPushButton, aber ``pressed``/``released`` bleiben bis Finger/Maus wirklich los."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        # Erster Touch soll sofort drücken — nicht erst Fokus setzen (Windows-Touchscreen).
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

    def _engage(self) -> None:
        if self._hold_active:
            return
        self._ensure_window_active()
        self._hold_active = True
        self._engage_mono = time.monotonic()
        self.setDown(True)
        self.pressed.emit()

    def _disengage(self) -> None:
        if not self._hold_active:
            return
        self._hold_active = False
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

    def event(self, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.TouchBegin:
            self._ensure_window_active()
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
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
