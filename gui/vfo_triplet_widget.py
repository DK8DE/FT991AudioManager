"""Dreiteilige VFO-Frequenz (MHz | kHz | Hz) mit Mausrad und flachem Eingabe-Stil.

Beispiel 149.112500 MHz → Anzeige ``149`` **·** ``112`` ``500`` (dicht, Punkt vor kHz).
Mausrad über einem Block: Schritt 1 MHz / 1 kHz / 1 Hz pro Raster (120°-Tick).
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget

# FT-991/991A: sinnvoller CAT-Rahmen (9 Stellen; 470 MHz Obergrenze).
VFO_MIN_HZ = 0
VFO_MAX_HZ = 470_000_000


def decompose_frequency_hz(hz: int) -> Tuple[int, int, int]:
    """Hz → (MHz-Anteil, drei kHz-Stellen, drei Hz-Stellen)."""
    h = max(0, min(VFO_MAX_HZ, int(hz)))
    mhz = h // 1_000_000
    rest = h % 1_000_000
    k3 = rest // 1000
    h3 = rest % 1000
    return mhz, k3, h3


def compose_frequency_hz(mhz: int, k3: int, h3: int) -> int:
    """Baustein → Hz, Anteile begrenzt."""
    m = max(0, int(mhz))
    k = max(0, min(999, int(k3)))
    h = max(0, min(999, int(h3)))
    return min(VFO_MAX_HZ, m * 1_000_000 + k * 1000 + h)


class _HzStepEdit(QLineEdit):
    """Flaches Feld; Mausrad meldet Schritte in Einheiten ``step_hz``."""

    wheel_steps = Signal(int)

    def __init__(self, *, min_width: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setMinimumWidth(min_width)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        y = event.angleDelta().y()
        if y == 0:
            super().wheelEvent(event)
            return
        steps = y // 120
        if steps == 0:
            steps = 1 if y > 0 else -1
        self.wheel_steps.emit(int(steps))
        event.accept()


class VfoTripletWidget(QWidget):
    """Drei Blöcke für MHz (variabel) | kHz (000–999) | Hz (000–999)."""

    user_frequency_changed = Signal(int)

    def __init__(
        self,
        *,
        text_color: str = "#d8d8d8",
        font_scale: float = 2.3,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._text_color = text_color
        self._font_scale = font_scale
        self._suppress_emit = False
        self._last_hz: int = 0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._mhz = _HzStepEdit(min_width=46)
        self._khz = _HzStepEdit(min_width=36)
        self._hz = _HzStepEdit(min_width=36)

        self._dot = QLabel(".")
        self._dot.setAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        df = self._dot.font()
        df.setBold(True)
        df.setPointSizeF(df.pointSizeF() * self._font_scale)
        self._dot.setFont(df)
        self._dot.setStyleSheet(
            f"color: {self._text_color}; background: transparent; padding: 0 1px; margin: 0;"
        )
        self._dot.setFixedWidth(10)

        for w in (self._mhz, self._khz, self._hz):
            self._style_field(w)
        lay.addWidget(self._mhz)
        lay.addWidget(self._dot)
        lay.addWidget(self._khz)
        lay.addWidget(self._hz)

        self._mhz.wheel_steps.connect(lambda s: self._on_wheel(0, s))
        self._khz.wheel_steps.connect(lambda s: self._on_wheel(1, s))
        self._hz.wheel_steps.connect(lambda s: self._on_wheel(2, s))

        for w in (self._mhz, self._khz, self._hz):
            w.editingFinished.connect(self._on_edit_finished)

    def _style_field(self, w: QLineEdit) -> None:
        f = w.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * self._font_scale)
        w.setFont(f)
        w.setStyleSheet(
            f"QLineEdit {{ border: none; background: transparent; padding: 0px 1px; "
            f"color: {self._text_color}; }}"
            "QLineEdit:focus { border: none; outline: none; }"
        )

    def _parse_blocks(self) -> Optional[int]:
        try:
            m = int(self._mhz.text().strip() or "0")
            k = int(self._khz.text().strip() or "0")
            h = int(self._hz.text().strip() or "0")
        except ValueError:
            return None
        return compose_frequency_hz(m, k, h)

    def _display_hz(self, hz: int) -> None:
        m, k, h = decompose_frequency_hz(hz)
        self._mhz.setText(str(m))
        self._khz.setText(f"{k:03d}")
        self._hz.setText(f"{h:03d}")

    def _any_segment_focused(self) -> bool:
        """True, solange der Nutzer per Tastatur in einem Block tippt."""
        return self._mhz.hasFocus() or self._khz.hasFocus() or self._hz.hasFocus()

    def set_frequency_hz(self, hz: int) -> None:
        """Anzeige aus CAT/RX aktualisieren (ohne ``user_frequency_changed``).

        Während ein Feld den Fokus hat, keine Überschreibung — sonst stört
        der Poller die Tastatureingabe.
        """
        if self._any_segment_focused():
            return
        h = max(VFO_MIN_HZ, min(VFO_MAX_HZ, int(hz)))
        self._last_hz = h
        self._suppress_emit = True
        try:
            self._display_hz(h)
        finally:
            self._suppress_emit = False

    def set_placeholder_empty(self) -> None:
        """Nach Disconnect — Felder leeren / deaktivieren."""
        for w in (self._mhz, self._khz, self._hz):
            w.clearFocus()
        self._suppress_emit = True
        try:
            self._mhz.clear()
            self._khz.clear()
            self._hz.clear()
        finally:
            self._suppress_emit = False
        self._last_hz = 0

    def set_interactive(self, on: bool) -> None:
        for w in (self._mhz, self._khz, self._hz):
            w.setReadOnly(not on)
            w.setEnabled(on)

    def _emit_user_if_needed(self, hz: int) -> None:
        if self._suppress_emit:
            return
        hz = max(VFO_MIN_HZ, min(VFO_MAX_HZ, hz))
        if hz == self._last_hz:
            return
        self._last_hz = hz
        self.user_frequency_changed.emit(hz)

    def _on_wheel(self, segment: int, steps: int) -> None:
        if steps == 0 or not self._mhz.isEnabled():
            return
        parsed = self._parse_blocks()
        base = parsed if parsed is not None else self._last_hz
        step = (1_000_000, 1_000, 1)[segment] * steps
        new_hz = max(VFO_MIN_HZ, min(VFO_MAX_HZ, base + step))
        self._display_hz(new_hz)
        self._emit_user_if_needed(new_hz)

    def _on_edit_finished(self) -> None:
        if not self._mhz.isEnabled():
            return
        parsed = self._parse_blocks()
        if parsed is None:
            self._display_hz(self._last_hz)
            return
        self._display_hz(parsed)
        self._emit_user_if_needed(parsed)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            w = self.childAt(event.pos())
            if isinstance(w, QLineEdit):
                w.setFocus()
            else:
                # Punkt/Abstand haben keinen Fokus — sonst bliebe ein Segment
                # fokussiert und ``set_frequency_hz`` würde nie wieder vom
                # Funkgerät nachziehen.
                for fld in (self._mhz, self._khz, self._hz):
                    fld.clearFocus()
        super().mousePressEvent(event)

