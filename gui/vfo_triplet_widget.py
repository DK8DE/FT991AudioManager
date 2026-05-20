"""Dreiteilige VFO-Frequenz (MHz | kHz | Hz) mit Mausrad und flachem Eingabe-Stil.

Beispiel 149.112500 MHz → Anzeige ``149`` **·** ``112`` ``500`` (dicht, Punkt vor kHz).
Mausrad über einem Block: Schritt 1 MHz / 1 kHz / 10 Hz pro Raster (120°-Tick);
der Hz-Block bleibt auf Zehner (…0 Hz).
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget

from mapping.vfo_bands import (
    VFO_CAT_MAX_HZ,
    VFO_CAT_MIN_HZ,
    clamp_vfo_frequency_hz,
    is_valid_vfo_frequency_hz,
    step_vfo_frequency_hz,
)

VFO_MIN_HZ = VFO_CAT_MIN_HZ
VFO_MAX_HZ = VFO_CAT_MAX_HZ
#: Hz-Segment (letzte drei Stellen): nur 10-Hz-Schritte, Einerstelle immer 0.
VFO_HZ_SEGMENT_STEP = 10


def snap_vfo_hz_to_10hz_grid(hz: int) -> int:
    """Frequenz auf 10-Hz-Raster; nie in Bandlücken (z. B. 165 MHz nach Rundung).

    ``164.999.999`` MHz rundet mathematisch auf ``165.000.000`` MHz — das liegt in der
    CAT-Lücke zwischen 2 m und 70 cm. Dann den gültigen 10-Hz-Nachbarn wählen.
    """
    raw = max(VFO_CAT_MIN_HZ, min(VFO_CAT_MAX_HZ, int(hz)))
    rounded = int(round(raw / VFO_HZ_SEGMENT_STEP) * VFO_HZ_SEGMENT_STEP)
    rounded = max(VFO_CAT_MIN_HZ, min(VFO_CAT_MAX_HZ, rounded))
    if is_valid_vfo_frequency_hz(rounded):
        return rounded
    hi = rounded + VFO_HZ_SEGMENT_STEP
    lo = rounded - VFO_HZ_SEGMENT_STEP
    hi_ok = hi <= VFO_CAT_MAX_HZ and is_valid_vfo_frequency_hz(hi)
    lo_ok = lo >= VFO_CAT_MIN_HZ and is_valid_vfo_frequency_hz(lo)
    if lo_ok and not hi_ok:
        return lo
    if hi_ok and not lo_ok:
        return hi
    if lo_ok and hi_ok:
        return lo if abs(raw - lo) <= abs(raw - hi) else hi
    return clamp_vfo_frequency_hz(raw)


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
    return snap_vfo_hz_to_10hz_grid(m * 1_000_000 + k * 1000 + h)


def field_width_for_digits(field: QLineEdit, digits: int, *, extra_px: int = 10) -> int:
    """Mindestbreite für ``digits`` Ziffern in der aktuellen Feld-Schrift."""
    fm = QFontMetrics(field.font())
    return fm.horizontalAdvance("8" * digits) + extra_px


class _HzStepEdit(QLineEdit):
    """Flaches Feld; Mausrad meldet Schritte in Einheiten ``step_hz``."""

    wheel_steps = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sp = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setSizePolicy(sp)

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
        text_color: Optional[str] = None,
        font_scale: float = 2.3,
        digit_font: Optional[QFont] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        #: ``None`` = System-/Palettenfarbe (für helles Windows ohne erzwungenes Dark)
        self._text_color: Optional[str] = text_color
        #: Wenn gesetzt: exakt diese Schrift für MHz/kHz/Hz (z. B. gleich „VFO-A:“).
        self._digit_font: Optional[QFont] = (
            QFont(digit_font) if digit_font is not None else None
        )
        self._font_scale = font_scale
        self._suppress_emit = False
        self._last_hz: int = 0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._mhz = _HzStepEdit()
        self._khz = _HzStepEdit()
        self._hz = _HzStepEdit()

        self._dot = QLabel(".")
        self._dot.setAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        if self._digit_font is not None:
            df = QFont(self._digit_font)
        else:
            base = QGuiApplication.font()
            df = QFont(base)
            df.setBold(True)
            df.setPointSizeF(base.pointSizeF() * self._font_scale)
        self._dot.setFont(df)
        dmf = QFontMetrics(df)
        self._dot.setFixedWidth(max(10, dmf.horizontalAdvance(".") + 4))

        self._apply_frequency_text_styling()
        self._apply_field_widths()
        lay.addWidget(self._mhz)
        lay.addWidget(self._dot)
        lay.addWidget(self._khz)
        lay.addWidget(self._hz)

        tsp = self.sizePolicy()
        tsp.setHorizontalPolicy(QSizePolicy.Policy.Fixed)
        tsp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(tsp)

        self._mhz.wheel_steps.connect(lambda s: self._on_wheel(0, s))
        self._khz.wheel_steps.connect(lambda s: self._on_wheel(1, s))
        self._hz.wheel_steps.connect(lambda s: self._on_wheel(2, s))

        for w in (self._mhz, self._khz, self._hz):
            w.editingFinished.connect(self._on_edit_finished)

    def set_text_color(self, color: Optional[str]) -> None:
        """Frequenz-Ziffern: feste Farbe (z. B. weiß im Dark-Mode) oder ``None`` = Palette."""
        self._text_color = color
        self._apply_frequency_text_styling()

    def _apply_frequency_text_styling(self) -> None:
        self._style_field(self._mhz, pad_left_px=2)
        for w in (self._khz, self._hz):
            self._style_field(w)
        if self._text_color:
            dot_qss = (
                f"color: {self._text_color}; background: transparent; "
                "padding: 0 1px; margin: 0;"
            )
        else:
            dot_qss = (
                "color: palette(window-text); background: transparent; "
                "padding: 0 1px; margin: 0;"
            )
        self._dot.setStyleSheet(dot_qss)

    def _style_field(self, w: QLineEdit, *, pad_left_px: int = 0) -> None:
        if self._digit_font is not None:
            f = QFont(self._digit_font)
        else:
            base = QGuiApplication.font()
            f = QFont(base)
            f.setBold(True)
            f.setPointSizeF(base.pointSizeF() * self._font_scale)
        w.setFont(f)
        if self._text_color:
            color_css = f"color: {self._text_color};"
            sel_css = ""
        else:
            color_css = "color: palette(window-text);"
            sel_css = (
                "selection-background-color: palette(highlight); "
                "selection-color: palette(highlighted-text);"
            )
        w.setStyleSheet(
            f"QLineEdit {{ border: none; background: transparent; "
            f"padding: 0px 3px 0px {int(pad_left_px)}px; "
            f"{color_css} {sel_css}}}"
            "QLineEdit:focus { border: none; outline: none; }"
        )

    def _apply_field_widths(self) -> None:
        """Feste Pixelbreite reicht bei großer Schrift nicht — an FontMetrics anpassen."""
        self._mhz.setFixedWidth(field_width_for_digits(self._mhz, 3, extra_px=12))
        self._khz.setFixedWidth(field_width_for_digits(self._khz, 3))
        self._hz.setFixedWidth(field_width_for_digits(self._hz, 3))

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
        h = snap_vfo_hz_to_10hz_grid(hz)
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
        hz = snap_vfo_hz_to_10hz_grid(hz)
        if hz == self._last_hz:
            return
        self._last_hz = hz
        self.user_frequency_changed.emit(hz)

    def _on_wheel(self, segment: int, steps: int) -> None:
        if steps == 0 or not self._mhz.isEnabled():
            return
        parsed = self._parse_blocks()
        base = parsed if parsed is not None else self._last_hz
        step = (1_000_000, 1_000, VFO_HZ_SEGMENT_STEP)[segment] * steps
        new_hz = snap_vfo_hz_to_10hz_grid(
            clamp_vfo_frequency_hz(step_vfo_frequency_hz(base, step))
        )
        self._display_hz(new_hz)
        self._emit_user_if_needed(new_hz)

    def _on_edit_finished(self) -> None:
        if not self._mhz.isEnabled():
            return
        parsed = self._parse_blocks()
        if parsed is None:
            self._display_hz(self._last_hz)
            return
        clamped = clamp_vfo_frequency_hz(parsed)
        self._display_hz(clamped)
        self._emit_user_if_needed(clamped)

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

