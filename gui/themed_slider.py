"""Vertikale Meter-Slider mit sichtbaren Skalenstrichen.

Qt-Stylesheets blenden ``QSlider``-Ticks aus (QTBUG-3304). Diese Klasse
zeichnet die Markierungen nach dem Standard-Slider in ``#9F9F9F``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from gui.theme import SLIDER_INACTIVE

_TICK_LEN = 5


class MeterVerticalSlider(QSlider):
    """Vertikaler Slider für SQL, DSP, AGC, MIC — mit rechts sichtbaren Ticks."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        pos = self.tickPosition()
        if pos == QSlider.TickPosition.NoTicks:
            super().paintEvent(event)
            return

        # Nur unsere Ticks zeichnen — der Qt-Style (z. B. Fusion auf Windows)
        # malt sonst zusätzlich Skalenstriche → doppelte Markierungen.
        self.setTickPosition(QSlider.TickPosition.NoTicks)
        try:
            super().paintEvent(event)
        finally:
            self.setTickPosition(pos)

        interval = self.tickInterval()
        if interval <= 0:
            interval = self.pageStep()
        if interval <= 0:
            interval = 1

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            opt,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        if not groove.isValid():
            return

        painter = QPainter(self)
        painter.setPen(QPen(QColor(SLIDER_INACTIVE), 1))
        minimum = self.minimum()
        maximum = self.maximum()
        span = maximum - minimum
        if span <= 0:
            painter.end()
            return

        tick_right = pos in (
            QSlider.TickPosition.TicksRight,
            QSlider.TickPosition.TicksBothSides,
        )
        tick_left = pos in (
            QSlider.TickPosition.TicksLeft,
            QSlider.TickPosition.TicksBothSides,
        )

        for value in range(minimum, maximum + 1, interval):
            ratio = (value - minimum) / span
            if self.invertedAppearance():
                ratio = 1.0 - ratio
            y = int(groove.bottom() - ratio * groove.height())
            if tick_right:
                x0 = groove.right() + 2
                painter.drawLine(x0, y, x0 + _TICK_LEN, y)
            if tick_left:
                x1 = groove.left() - 2
                painter.drawLine(x1 - _TICK_LEN, y, x1, y)
        painter.end()
