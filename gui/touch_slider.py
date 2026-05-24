"""QSlider mit Touch-Unterstützung (Finger ziehen statt nur kurzer Tap)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QTouchEvent
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

_TOUCH_MIN_CROSS = 32


class TouchSlider(QSlider):
    """Slider, der Touch-Events direkt in Wertänderungen umsetzt."""

    def __init__(self, orientation: Qt.Orientation, /, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._touch_point_ids: set[int] = set()
        self._touch_grabbed = False
        self._apply_touch_minimum_size()

    def _apply_touch_minimum_size(self) -> None:
        if self.orientation() == Qt.Orientation.Horizontal:
            self.setMinimumHeight(max(self.minimumHeight(), _TOUCH_MIN_CROSS))
        else:
            self.setMinimumWidth(max(self.minimumWidth(), _TOUCH_MIN_CROSS))

    def _value_at_position(self, pos: QPoint) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            opt,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        if not groove.isValid():
            return int(self.value())

        if self.orientation() == Qt.Orientation.Horizontal:
            pos_in = max(0, min(groove.width(), pos.x() - groove.x()))
            span = groove.width()
        else:
            pos_in = max(0, min(groove.height(), groove.bottom() - pos.y()))
            span = groove.height()

        if span <= 0:
            return int(self.value())

        return int(
            self.style().sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                pos_in,
                span,
                opt.upsideDown,
            )
        )

    def _apply_touch_position(self, pos: QPoint) -> None:
        val = self._value_at_position(pos)
        if not self.isSliderDown():
            self.setSliderDown(True)
            self.sliderPressed.emit()
        if val != self.sliderPosition():
            self.setSliderPosition(val)

    def _begin_touch_grab(self) -> None:
        if not self._touch_grabbed:
            self.grabMouse()
            self._touch_grabbed = True

    def _end_touch_grab(self) -> None:
        if self._touch_grabbed:
            self.releaseMouse()
            self._touch_grabbed = False

    def _finish_touch_interaction(self) -> None:
        self._end_touch_grab()
        if self.isSliderDown():
            self.setSliderDown(False)
            self.sliderReleased.emit()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._touch_point_ids:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._touch_point_ids:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._touch_point_ids:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def touchEvent(self, event: QTouchEvent | None) -> None:
        if event is None:
            return
        for point in event.points():
            pid = int(point.id())
            state = point.state()
            pos = point.position().toPoint()
            if state == Qt.TouchPointState.TouchPointPressed:
                self._touch_point_ids.add(pid)
                self._begin_touch_grab()
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
                    self._finish_touch_interaction()
        event.accept()
