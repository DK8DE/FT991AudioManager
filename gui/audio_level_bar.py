"""Horizontale Pegelanzeige für Audio-Ein-/Ausgänge."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class AudioLevelBar(QWidget):
    """Peak-Anzeige (0…100 %) mit sanftem Abfall."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._peak = 0.0
        self._display = 0.0
        self._active = True
        self.setFixedHeight(12)
        self.setMinimumWidth(80)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setToolTip("Momentaner Audio-Pegel (Windows-Gerät)")
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if not self._active:
            self._peak = 0.0
            self._display = 0.0
            self.update()

    def set_peak_level(self, level: float) -> None:
        """*level* 0.0…1.0 (Peak vom Windows-Endpunkt)."""
        if not self._active:
            return
        v = max(0.0, min(1.0, float(level)))
        if v >= self._peak:
            self._peak = v
        elif v > self._display:
            self._display = v

    def _tick(self) -> None:
        if not self._active:
            return
        decay = 0.82
        self._display = max(self._peak, self._display * decay)
        self._peak *= decay
        if self._peak < 0.002:
            self._peak = 0.0
        if self._display < 0.002:
            self._display = 0.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(1, 1, -1, -1)
            p.setPen(QColor(55, 55, 55))
            p.setBrush(QColor(28, 28, 30))
            p.drawRoundedRect(rect, 3, 3)
            if self._display <= 0.0:
                return
            fill_w = max(2, int(rect.width() * self._display))
            fill_rect = rect.adjusted(0, 0, fill_w - rect.width(), 0)
            frac = self._display
            if frac > 0.88:
                color = QColor(231, 76, 60)
            elif frac > 0.65:
                color = QColor(241, 196, 15)
            else:
                color = QColor(93, 220, 122)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(fill_rect, 2, 2)
