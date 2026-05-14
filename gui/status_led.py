"""Wiederverwendbarer Status-Indikator (grün/rot Kreis).

Verwendet wird die Komponente im Hauptfenster, um den CAT-Verbindungsstatus
auf einen Blick anzuzeigen. Die LED ist absichtlich klein, anti-aliased und
respektiert Dark Mode (sie zeichnet ihre eigenen Farben).
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QWidget


# (Füllfarbe, Randfarbe)
_GREEN: Tuple[QColor, QColor] = (QColor(40, 180, 60), QColor(20, 100, 30))
_RED: Tuple[QColor, QColor] = (QColor(210, 60, 60), QColor(120, 25, 25))


class StatusLed(QFrame):
    """Kleiner farbiger Kreis: grün = aktiv, rot = inaktiv.

    ``set_active(True)`` schaltet auf grün, ``set_active(False)`` auf rot.
    Die Größe ist per Default 18×18 px, kann aber mit ``setFixedSize`` oder
    ``setMinimumSize`` angepasst werden.
    """

    def __init__(
        self,
        active: bool = False,
        diameter: int = 18,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._active = bool(active)
        self.setFixedSize(diameter, diameter)
        self.setToolTip("verbunden" if active else "nicht verbunden")

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        if bool(active) != self._active:
            self._active = bool(active)
            self.setToolTip("verbunden" if self._active else "nicht verbunden")
            self.update()

    # ------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        fill, border = _GREEN if self._active else _RED
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(border)
        painter.setBrush(fill)
        painter.drawEllipse(2, 2, self.width() - 4, self.height() - 4)
        painter.end()
