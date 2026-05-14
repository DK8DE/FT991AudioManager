"""Interaktiver Live-Editor für die EQ-Kurve.

Statt klassischer Slider werden alle drei EQ-Parameter pro Band direkt
über Maus-Drag auf einem Plot eingestellt:

* **Center-Punkt ziehen**          → Frequenz (horizontal) + Level (vertikal)
* **Linke / rechte BW-Kante ziehen** → Bandbreite (Q-Faktor 1..10)
* **Rechtsklick auf Punkt**          → Band an/aus (OFF ↔ erste erlaubte Freq)

Visuell:

* Karo-Hintergrund mit Frequenz- und dB-Skala
* Hellblauer Bereich pro Band markiert die effektive Halbwertsbreite
* Grüne Summen-Kurve (Yaesu-Grün, identisch zu den DSP-LEDs)
* Center-Marker (gefüllte Kreise) auf der Kurve
* Footer mit den drei aktuellen Center-Frequenzen

Die Modellierung jedes Bandes ist eine Gauss-Approximation eines Peaking-
Filters (Visualisierung, kein DSP) — die Bandbreite in Oktaven ist
``2.5 / bw`` (BW=1 → 2.5 Oktaven breit, BW=10 → 0.25 Oktaven schmal).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from mapping.eq_mapping import (
    BW_MAX,
    BW_MIN,
    LEVEL_DB_MAX,
    LEVEL_DB_MIN,
    freq_table_for_band,
)
from model.eq_band import EQBand, EQSettings


# Anzeigegrenzen -------------------------------------------------------------
_F_MIN = 60.0
_F_MAX = 5000.0
_DB_MAX = max(20.0, float(LEVEL_DB_MAX))
_DB_MIN = min(-20.0, float(LEVEL_DB_MIN))

_NUM_SAMPLES = 160

# Farben ---------------------------------------------------------------------
_BG = QColor("#161616")
_GRID = QColor("#2a2a2a")
_GRID_MAJOR = QColor("#3a3a3a")
_ZERO_LINE = QColor("#7c7c7c")
_LABEL_COLOR = QColor("#9a9a9a")
_CURVE = QColor("#52c41a")              # Yaesu-Grün
_CURVE_FILL_TOP = QColor(82, 196, 26, 90)
_CURVE_FILL_BOTTOM = QColor(82, 196, 26, 0)
_BW_FILL = QColor(80, 160, 220, 55)     # hellblauer Bandbreiten-Bereich
_BW_EDGE = QColor(120, 200, 240, 200)
_BW_EDGE_HOVER = QColor(180, 230, 255)
_BAND_POINT_ACTIVE = QColor("#9eff9e")
_BAND_POINT_OFF = QColor(120, 120, 120, 160)

_FREQ_TICKS = [(100, "100"), (200, "200"), (500, "500"),
               (1000, "1k"), (2000, "2k"), (3000, "3k")]
_DB_TICKS = [-15, -10, -5, 0, 5, 10, 15]

# Hit-Test ----------------------------------------------------------------
_HIT_RADIUS_CENTER = 11    # Pixel um den Mittelpunkt
_HIT_RADIUS_EDGE = 8       # Pixel um die linken/rechten BW-Kanten

#: Halbwertsbreite (links + rechts vom Center) in Oktaven, abhängig von BW.
def _half_width_oct_for_bw(bw: int) -> float:
    bw = max(BW_MIN, min(BW_MAX, int(bw)))
    return 1.25 / bw  # eine Seite — Gesamt ist 2× dieser Wert


def _band_gain_db(band: EQBand, freq_hz: float) -> float:
    """Gain einer Gauss-Approximation eines Peaking-Filters bei ``freq_hz``."""
    if band.freq == "OFF" or band.level == 0:
        return 0.0
    try:
        f0 = float(band.freq)
    except (TypeError, ValueError):
        return 0.0
    if f0 <= 0 or freq_hz <= 0:
        return 0.0
    bw = max(BW_MIN, min(BW_MAX, int(band.bw)))
    width_oct = 1.5 / bw
    dist_oct = math.log2(freq_hz / f0)
    falloff = math.exp(-(dist_oct / width_oct) ** 2)
    return float(band.level) * falloff


def _total_gain_db(bands: List[EQBand], freq_hz: float) -> float:
    return sum(_band_gain_db(b, freq_hz) for b in bands)


def _nearest_freq_for_band(band_index: int, target_hz: float) -> int:
    """Liefert die nächstgelegene erlaubte Frequenz (ohne ``OFF``)."""
    candidates = [v for v in freq_table_for_band(band_index) if isinstance(v, int)]
    if not candidates:
        return 0
    # log-Abstand, weil die Anzeige logarithmisch ist
    target_log = math.log10(max(1.0, target_hz))
    return min(candidates, key=lambda c: abs(math.log10(c) - target_log))


def _first_enabled_freq(band_index: int) -> int:
    """Erste echte (Nicht-``OFF``) Frequenz aus der Tabelle des Bandes."""
    for v in freq_table_for_band(band_index):
        if isinstance(v, int):
            return v
    return 1000  # Fallback (sollte nie greifen)


# ----------------------------------------------------------------------
# Interner Drag-Zustand
# ----------------------------------------------------------------------


@dataclass
class _DragState:
    band_index: int
    mode: str           # 'center' | 'edge_left' | 'edge_right'


# ----------------------------------------------------------------------
# Plot-Canvas (interaktiv)
# ----------------------------------------------------------------------


class _EqCurveCanvas(QWidget):
    """Reines Mal-Widget für die Kurve. Verarbeitet Maus-Events und
    propagiert Band-Änderungen über ``bands_changed``.

    Der eigentliche Bands-Zustand lebt hier, das ``EQEditorWidget`` ist
    über ``bands_changed`` informiert.
    """

    bands_changed = Signal(object)   # List[EQBand]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bands: List[EQBand] = [EQBand(), EQBand(), EQBand()]
        self._hover: Optional[_DragState] = None
        self._drag: Optional[_DragState] = None
        self.setMinimumSize(260, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.ArrowCursor)

    # ------------------------------------------------------------------
    # Externe API
    # ------------------------------------------------------------------

    def set_bands(self, bands: List[EQBand]) -> None:
        self._bands = list(bands)
        self.update()

    def bands(self) -> List[EQBand]:
        return list(self._bands)

    # ------------------------------------------------------------------
    # Geometrie-Helfer
    # ------------------------------------------------------------------

    def _plot_geometry(self) -> tuple[int, int, int, int]:
        margin_left = 22
        margin_right = 6
        margin_top = 6
        margin_bottom = 14
        plot_x = margin_left
        plot_y = margin_top
        plot_w = max(1, self.width() - margin_left - margin_right)
        plot_h = max(1, self.height() - margin_top - margin_bottom)
        return plot_x, plot_y, plot_w, plot_h

    def _x_for_freq(self, freq: float, plot_x: int, plot_w: int) -> float:
        log_min = math.log10(_F_MIN)
        log_max = math.log10(_F_MAX)
        return plot_x + (math.log10(freq) - log_min) / (log_max - log_min) * plot_w

    def _freq_for_x(self, x: float, plot_x: int, plot_w: int) -> float:
        log_min = math.log10(_F_MIN)
        log_max = math.log10(_F_MAX)
        frac = max(0.0, min(1.0, (x - plot_x) / plot_w))
        return 10 ** (log_min + frac * (log_max - log_min))

    def _y_for_db(self, db: float, plot_y: int, plot_h: int) -> float:
        frac = (db - _DB_MIN) / (_DB_MAX - _DB_MIN)
        return plot_y + (1.0 - frac) * plot_h

    def _db_for_y(self, y: float, plot_y: int, plot_h: int) -> float:
        frac = max(0.0, min(1.0, (y - plot_y) / plot_h))
        return _DB_MAX - frac * (_DB_MAX - _DB_MIN)

    # ------------------------------------------------------------------
    # Hit-Test
    # ------------------------------------------------------------------

    def _band_center_freq_for_display(self, band: EQBand, band_index: int) -> int:
        """Für die Anzeige (Hit-Test, Marker) brauchen wir auch bei ``OFF``
        eine Frequenz. Wir nehmen dann die erste echte Frequenz der Tabelle."""
        if isinstance(band.freq, int):
            return band.freq
        return _first_enabled_freq(band_index)

    def _hit_test(self, x: float, y: float) -> Optional[_DragState]:
        plot_x, plot_y, plot_w, plot_h = self._plot_geometry()
        # Außerhalb des Plots → kein Hit
        if not (plot_x <= x <= plot_x + plot_w and plot_y <= y <= plot_y + plot_h):
            return None

        # Erst Center-Punkte (haben Vorrang vor BW-Kanten)
        for idx, band in enumerate(self._bands):
            f_display = self._band_center_freq_for_display(band, idx)
            level = int(band.level) if band.freq != "OFF" else 0
            cx = self._x_for_freq(f_display, plot_x, plot_w)
            cy = self._y_for_db(level, plot_y, plot_h)
            if (x - cx) ** 2 + (y - cy) ** 2 <= _HIT_RADIUS_CENTER ** 2:
                return _DragState(band_index=idx, mode="center")

        # Dann BW-Kanten (nur bei aktiven Bändern)
        for idx, band in enumerate(self._bands):
            if band.freq == "OFF":
                continue
            f0 = float(band.freq)
            half = _half_width_oct_for_bw(int(band.bw))
            left_f = f0 * (2 ** -half)
            right_f = f0 * (2 ** half)
            left_x = self._x_for_freq(max(_F_MIN, left_f), plot_x, plot_w)
            right_x = self._x_for_freq(min(_F_MAX, right_f), plot_x, plot_w)
            if abs(x - left_x) <= _HIT_RADIUS_EDGE:
                return _DragState(band_index=idx, mode="edge_left")
            if abs(x - right_x) <= _HIT_RADIUS_EDGE:
                return _DragState(band_index=idx, mode="edge_right")
        return None

    # ------------------------------------------------------------------
    # Maus-Events
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if self._drag is not None:
            self._apply_drag(pos.x(), pos.y())
            return
        # Hover-Cursor
        hit = self._hit_test(pos.x(), pos.y())
        self._hover = hit
        if hit is None:
            self.setCursor(Qt.ArrowCursor)
        elif hit.mode == "center":
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.SizeHorCursor)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        hit = self._hit_test(pos.x(), pos.y())
        if event.button() == Qt.RightButton:
            if hit is not None and hit.mode == "center":
                self._toggle_off(hit.band_index)
            return
        if event.button() != Qt.LeftButton:
            return
        if hit is None:
            return
        self._drag = hit
        # Beim Drag-Start sofort einmal anwenden — verhindert "Sprung"
        self._apply_drag(pos.x(), pos.y(), initial=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = None

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if self._drag is None:
            self._hover = None
            self.setCursor(Qt.ArrowCursor)
            self.update()

    # ------------------------------------------------------------------
    # Drag-Anwendung
    # ------------------------------------------------------------------

    def _apply_drag(self, x: float, y: float, *, initial: bool = False) -> None:
        assert self._drag is not None
        idx = self._drag.band_index
        plot_x, plot_y, plot_w, plot_h = self._plot_geometry()
        band = self._bands[idx]

        if self._drag.mode == "center":
            # Frequenz aus X (snap), Level aus Y (clamp + round)
            f_raw = self._freq_for_x(x, plot_x, plot_w)
            new_freq = _nearest_freq_for_band(idx, f_raw)
            db_raw = self._db_for_y(y, plot_y, plot_h)
            new_level = int(round(max(LEVEL_DB_MIN, min(LEVEL_DB_MAX, db_raw))))
            updated = EQBand(freq=new_freq, level=new_level, bw=int(band.bw))
        else:
            # BW-Kante: aktuelle Center-Frequenz bleibt; neue BW aus Distanz
            if band.freq == "OFF":
                return
            f0 = float(band.freq)
            target_f = self._freq_for_x(x, plot_x, plot_w)
            if target_f <= 0:
                return
            dist_oct = abs(math.log2(target_f / f0))
            if dist_oct < 1e-3:
                dist_oct = 1e-3
            # half = 1.25 / bw  →  bw = 1.25 / half
            bw_raw = 1.25 / dist_oct
            new_bw = int(round(max(BW_MIN, min(BW_MAX, bw_raw))))
            updated = EQBand(freq=int(band.freq), level=int(band.level), bw=new_bw)

        if updated != band or initial:
            self._bands[idx] = updated
            self.bands_changed.emit(list(self._bands))
            self.update()

    def _toggle_off(self, band_index: int) -> None:
        band = self._bands[band_index]
        if band.freq == "OFF":
            new_freq = _first_enabled_freq(band_index)
            self._bands[band_index] = EQBand(
                freq=new_freq, level=int(band.level), bw=int(band.bw)
            )
        else:
            self._bands[band_index] = EQBand(
                freq="OFF", level=int(band.level), bw=int(band.bw)
            )
        self.bands_changed.emit(list(self._bands))
        self.update()

    # ------------------------------------------------------------------
    # Malen
    # ------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        plot_x, plot_y, plot_w, plot_h = self._plot_geometry()
        painter.fillRect(plot_x, plot_y, plot_w, plot_h, _BG)

        # Karo-Grid: feine Linien
        painter.setPen(QPen(_GRID, 1))
        for log_step in self._fine_log_steps():
            x = self._x_for_freq(10 ** log_step, plot_x, plot_w)
            painter.drawLine(int(x), plot_y, int(x), plot_y + plot_h)
        db = _DB_MIN
        while db <= _DB_MAX:
            y = self._y_for_db(db, plot_y, plot_h)
            painter.drawLine(plot_x, int(y), plot_x + plot_w, int(y))
            db += 2.5

        # Major-Grid
        painter.setPen(QPen(_GRID_MAJOR, 1))
        for freq, _label in _FREQ_TICKS:
            x = self._x_for_freq(freq, plot_x, plot_w)
            painter.drawLine(int(x), plot_y, int(x), plot_y + plot_h)
        for db_t in _DB_TICKS:
            y = self._y_for_db(db_t, plot_y, plot_h)
            painter.drawLine(plot_x, int(y), plot_x + plot_w, int(y))

        # Bandbreiten-Boxen (hellblau) — hinter der Kurve
        for idx, band in enumerate(self._bands):
            if band.freq == "OFF":
                continue
            f0 = float(band.freq)
            half = _half_width_oct_for_bw(int(band.bw))
            left_x = self._x_for_freq(max(_F_MIN, f0 * (2 ** -half)), plot_x, plot_w)
            right_x = self._x_for_freq(min(_F_MAX, f0 * (2 ** half)), plot_x, plot_w)
            box_x = int(min(left_x, right_x))
            box_w = max(1, int(abs(right_x - left_x)))
            painter.setPen(Qt.NoPen)
            painter.setBrush(_BW_FILL)
            painter.drawRect(box_x, plot_y, box_w, plot_h)
            # Kanten markieren (Hover-Highlight)
            hovered_left = self._is_hovered(idx, "edge_left")
            hovered_right = self._is_hovered(idx, "edge_right")
            painter.setPen(QPen(_BW_EDGE_HOVER if hovered_left else _BW_EDGE, 2))
            painter.drawLine(int(left_x), plot_y, int(left_x), plot_y + plot_h)
            painter.setPen(QPen(_BW_EDGE_HOVER if hovered_right else _BW_EDGE, 2))
            painter.drawLine(int(right_x), plot_y, int(right_x), plot_y + plot_h)

        # 0-dB-Linie
        zero_y = self._y_for_db(0.0, plot_y, plot_h)
        painter.setPen(QPen(_ZERO_LINE, 1, Qt.DashLine))
        painter.drawLine(plot_x, int(zero_y), plot_x + plot_w, int(zero_y))

        # Plot-Rahmen
        painter.setPen(QPen(_GRID_MAJOR, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(plot_x, plot_y, plot_w, plot_h)

        # Achsen-Labels
        scale_font = QFont(self.font())
        scale_font.setPointSizeF(max(6.5, scale_font.pointSizeF() * 0.7))
        painter.setFont(scale_font)
        fm = QFontMetrics(scale_font)
        painter.setPen(_LABEL_COLOR)
        for freq, label in _FREQ_TICKS:
            x = self._x_for_freq(freq, plot_x, plot_w)
            painter.drawText(
                int(x) - fm.horizontalAdvance(label) // 2,
                plot_y + plot_h + fm.ascent() + 1,
                label,
            )
        for db_label in (-10, 0, 10):
            y = self._y_for_db(db_label, plot_y, plot_h)
            text = f"{db_label:+d}" if db_label != 0 else "0"
            painter.drawText(2, int(y) + fm.ascent() // 2 - 1, text)

        # Kurve berechnen + zeichnen
        log_min = math.log10(_F_MIN)
        log_max = math.log10(_F_MAX)
        polyline = QPolygonF()
        for i in range(_NUM_SAMPLES):
            frac = i / (_NUM_SAMPLES - 1)
            log_f = log_min + frac * (log_max - log_min)
            f = 10 ** log_f
            db_v = max(_DB_MIN, min(_DB_MAX, _total_gain_db(self._bands, f)))
            x = self._x_for_freq(f, plot_x, plot_w)
            y = self._y_for_db(db_v, plot_y, plot_h)
            polyline.append(QPointF(x, y))

        if polyline.size() >= 2:
            fill_poly = QPolygonF(polyline)
            fill_poly.append(QPointF(polyline[-1].x(), zero_y))
            fill_poly.append(QPointF(polyline[0].x(), zero_y))
            grad = QLinearGradient(0, plot_y, 0, plot_y + plot_h)
            grad.setColorAt(0.0, _CURVE_FILL_TOP)
            grad.setColorAt(1.0, _CURVE_FILL_BOTTOM)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPolygon(fill_poly)

            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(_CURVE, 2))
            painter.drawPolyline(polyline)

        # Center-Punkte (Drag-Marker)
        for idx, band in enumerate(self._bands):
            f0 = self._band_center_freq_for_display(band, idx)
            level = int(band.level) if band.freq != "OFF" else 0
            cx = self._x_for_freq(f0, plot_x, plot_w)
            cy = self._y_for_db(level, plot_y, plot_h)
            is_off = band.freq == "OFF"
            hovered = self._is_hovered(idx, "center")
            color = _BAND_POINT_OFF if is_off else _BAND_POINT_ACTIVE
            radius = 6.5 if hovered else 5.0
            painter.setPen(QPen(QColor("#0a0a0a"), 1))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), radius, radius)

    def _fine_log_steps(self) -> List[float]:
        log_min = math.log10(_F_MIN)
        log_max = math.log10(_F_MAX)
        steps: List[float] = []
        step = log_min
        while step < log_max:
            steps.append(step)
            step += 0.15
        return steps

    def _is_hovered(self, band_index: int, mode: str) -> bool:
        active = self._drag or self._hover
        return active is not None and active.band_index == band_index and active.mode == mode


# ----------------------------------------------------------------------
# EqCurveView: Canvas + Frequenz-Footer
# ----------------------------------------------------------------------


class EqCurveView(QWidget):
    """Interaktiver Plot mit Frequenz-Footer.

    Emittiert ``settings_changed`` (mit der neuen :class:`EQSettings`)
    bei jeder Maus-Interaktion. ``set_settings`` aktualisiert die Anzeige
    ohne ein Signal zu feuern (programmatischer Set-Pfad).
    """

    settings_changed = Signal(object)   # EQSettings

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.canvas = _EqCurveCanvas()
        self.canvas.bands_changed.connect(self._on_canvas_changed)
        layout.addWidget(self.canvas, stretch=1)

        self.footer = QLabel("LOW —   ·   MID —   ·   HIGH —")
        self.footer.setAlignment(Qt.AlignCenter)
        ff = self.footer.font()
        ff.setPointSizeF(ff.pointSizeF() * 0.88)
        self.footer.setFont(ff)
        self.footer.setStyleSheet("color: #a8a8a8;")
        layout.addWidget(self.footer)

    # ------------------------------------------------------------------

    def set_settings(self, settings: EQSettings) -> None:
        """Programmatischer Set: aktualisiert das Bild, ohne ``settings_changed``
        zu emittieren.

        ``_EqCurveCanvas.set_bands`` feuert von sich aus kein
        ``bands_changed`` — daher reicht der direkte Aufruf.
        """
        self.canvas.set_bands([settings.eq1, settings.eq2, settings.eq3])
        self.footer.setText(_format_footer(settings))

    def get_settings(self) -> EQSettings:
        bands = self.canvas.bands()
        return EQSettings(eq1=bands[0], eq2=bands[1], eq3=bands[2])

    def set_read_only(self, read_only: bool) -> None:
        self.canvas.setEnabled(not read_only)

    # ------------------------------------------------------------------

    def _on_canvas_changed(self, bands: object) -> None:
        if not isinstance(bands, list) or len(bands) != 3:
            return
        settings = EQSettings(eq1=bands[0], eq2=bands[1], eq3=bands[2])
        self.footer.setText(_format_footer(settings))
        self.settings_changed.emit(settings)


def _format_footer(settings: EQSettings) -> str:
    def _band(label: str, band: EQBand) -> str:
        if band.freq == "OFF":
            return f"{label} —"
        try:
            f = int(band.freq)
        except (TypeError, ValueError):
            return f"{label} —"
        f_text = f"{f / 1000:.1f} kHz" if f >= 1000 else f"{f} Hz"
        return f"{label} {f_text}"

    return (
        f"{_band('LOW', settings.eq1)}   ·   "
        f"{_band('MID', settings.eq2)}   ·   "
        f"{_band('HIGH', settings.eq3)}"
    )
