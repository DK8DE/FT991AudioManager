"""Siebenband-Live‑EQ‑Kurve — gleiches Bedienkonzept wie im Equalizer (EqCurveView).

* Center **ziehen** → Frequenz (X) und Level (Y), wie am FT‑991‑Parametric‑EQ
* Hellblaue **BW‑Kanten** → Q/Bandbreite (kontinuierlich 0,5…10, mit feinem Raster beim Ziehen)
* **Rechtsklick** auf Punkt → Band an/aus

Die Kurve ist eine Gauss‑Näherung zur Anzeige; der DSP verwendet echte Peak‑Filters.
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

from model.live_settings import (
    DEFAULT_LIVE_EQ_FREQ_HZ,
    LIVE_EQ_GAIN_DB_MAX,
    LIVE_EQ_GAIN_DB_MIN,
    LiveEqBandSettings,
)

_NUM_LIVE_BANDS = len(DEFAULT_LIVE_EQ_FREQ_HZ)

_F_MIN = 60.0
_F_MAX = 5000.0
_DB_MAX = float(LIVE_EQ_GAIN_DB_MAX)
_DB_MIN = float(LIVE_EQ_GAIN_DB_MIN)

_NUM_SAMPLES = 160

_BG = QColor("#161616")
_GRID = QColor("#2a2a2a")
_GRID_MAJOR = QColor("#3a3a3a")
_ZERO_LINE = QColor("#7c7c7c")
_LABEL_COLOR = QColor("#9a9a9a")
_CURVE = QColor("#52c41a")
_CURVE_FILL_TOP = QColor(82, 196, 26, 90)
_CURVE_FILL_BOTTOM = QColor(82, 196, 26, 0)
_BW_FILL = QColor(80, 160, 220, 55)
_BW_EDGE = QColor(120, 200, 240, 200)
_BW_EDGE_HOVER = QColor(180, 230, 255)
_BAND_POINT_ACTIVE = QColor("#9eff9e")
_BAND_POINT_OFF = QColor(120, 120, 120, 160)

_FREQ_TICKS = [(100, "100"), (200, "200"), (500, "500"),
               (1000, "1k"), (2000, "2k"), (3000, "3k")]
_DB_TICKS = [-15, -10, -5, 0, 5, 10, 15]

_HIT_RADIUS_CENTER = 11
_HIT_RADIUS_EDGE = 8


# Ziehen der hellblauen Kanten — feines Raster (Live DSP: echtes Q in LiveEqBandSettings).
_LIVE_Q_MIN = 0.5
_LIVE_Q_MAX = 10.0
_LIVE_Q_DRAG_STEP = 0.05


def _clamp_live_eq_q(q: float) -> float:
    return max(_LIVE_Q_MIN, min(_LIVE_Q_MAX, float(q)))


def _snap_live_eq_q_drag(raw_q: float) -> float:
    """Rastern auf feste Schritte, damit viele wiederholbare Stufen ohne Gleitkomma‑Glitch."""
    step = _LIVE_Q_DRAG_STEP
    lo_i = round(_LIVE_Q_MIN / step)
    hi_i = round(_LIVE_Q_MAX / step)
    qi = round(float(raw_q) / step)
    qi_int = max(int(lo_i), min(int(hi_i), int(qi)))
    # 0.05 ist binär nicht exakt — auf 6 Dezimal runden gegen Anzeige‑/Persistenz‑Glitch.
    return round(float(qi_int) * step, 6)


def _half_width_oct_for_q(q_val: float) -> float:
    q = _clamp_live_eq_q(q_val)
    return 1.25 / q


def _visual_width_oct(q_val: float) -> float:
    q = _clamp_live_eq_q(q_val)
    return 1.5 / q


def _live_band_visual_db(b: LiveEqBandSettings, freq_hz: float) -> float:
    if not b.enabled or abs(float(b.gain_db)) < 1e-9:
        return 0.0
    f0 = float(b.freq_hz)
    if f0 <= 0 or freq_hz <= 0:
        return 0.0
    width_oct = _visual_width_oct(float(b.q))
    dist_oct = math.log2(freq_hz / f0)
    falloff = math.exp(-(dist_oct / width_oct) ** 2)
    return float(b.gain_db) * falloff


def _total_gain_db_live(bands: List[LiveEqBandSettings], freq_hz: float) -> float:
    return sum(_live_band_visual_db(b, freq_hz) for b in bands)


def _clone_bands(bs: List[LiveEqBandSettings]) -> List[LiveEqBandSettings]:
    out: List[LiveEqBandSettings] = []
    for x in bs:
        out.append(LiveEqBandSettings.from_dict(x.to_dict()))
    return out


def _format_edge_frequency_label(hz: float) -> str:
    h = int(round(max(1.0, float(hz))))
    if h >= 1000:
        return f"{h / 1000:.1f} kHz"
    return f"{h} Hz"



@dataclass
class _DragState:
    band_index: int
    mode: str  # 'center' | 'edge_left' | 'edge_right'


class _LiveEqCurveCanvas(QWidget):
    """Interaktiver Plot wie _EqCurveCanvas, für :class:`LiveEqBandSettings`."""

    bands_changed = Signal(object)  # List[LiveEqBandSettings]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bands: List[LiveEqBandSettings] = [
            LiveEqBandSettings(
                freq_hz=float(DEFAULT_LIVE_EQ_FREQ_HZ[i]),
                enabled=False,
                gain_db=0.0,
                q=2.0,
            )
            for i in range(_NUM_LIVE_BANDS)
        ]
        self._hover: Optional[_DragState] = None
        self._drag: Optional[_DragState] = None
        self.setMinimumSize(380, 120)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)

    # ------------------------------------------------------------------

    def set_bands(self, bands: List[LiveEqBandSettings]) -> None:
        if len(bands) != _NUM_LIVE_BANDS:
            padded = list(bands[:_NUM_LIVE_BANDS])
            while len(padded) < _NUM_LIVE_BANDS:
                i = len(padded)
                padded.append(LiveEqBandSettings(freq_hz=float(DEFAULT_LIVE_EQ_FREQ_HZ[i])))
            bands = padded
        self._bands = _clone_bands(bands)
        for b in self._bands:
            b.clamp()
        self.update()

    def bands(self) -> List[LiveEqBandSettings]:
        return _clone_bands(self._bands)

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
        return float(10 ** (log_min + frac * (log_max - log_min)))

    def _y_for_db(self, db: float, plot_y: int, plot_h: int) -> float:
        frac = (db - _DB_MIN) / (_DB_MAX - _DB_MIN)
        return plot_y + (1.0 - frac) * plot_h

    def _db_for_y(self, y: float, plot_y: int, plot_h: int) -> float:
        frac = max(0.0, min(1.0, (y - plot_y) / plot_h))
        return _DB_MAX - frac * (_DB_MAX - _DB_MIN)

    # ------------------------------------------------------------------

    def _hit_test(self, x: float, y: float) -> Optional[_DragState]:
        plot_x, plot_y, plot_w, plot_h = self._plot_geometry()
        if not (plot_x <= x <= plot_x + plot_w and plot_y <= y <= plot_y + plot_h):
            return None

        for idx, band in enumerate(self._bands):
            f_display = float(band.freq_hz)
            level_display = float(band.gain_db) if band.enabled else 0.0
            cx = self._x_for_freq(max(_F_MIN, min(_F_MAX, f_display)), plot_x, plot_w)
            cy = self._y_for_db(level_display, plot_y, plot_h)
            if (x - cx) ** 2 + (y - cy) ** 2 <= _HIT_RADIUS_CENTER ** 2:
                return _DragState(band_index=idx, mode="center")

        for idx, band in enumerate(self._bands):
            if not band.enabled:
                continue
            f0 = float(band.freq_hz)
            half = _half_width_oct_for_q(float(band.q))
            left_f = f0 * (2 ** -half)
            right_f = f0 * (2 ** half)
            left_x = self._x_for_freq(max(_F_MIN, left_f), plot_x, plot_w)
            right_x = self._x_for_freq(min(_F_MAX, right_f), plot_x, plot_w)
            if abs(x - left_x) <= _HIT_RADIUS_EDGE:
                return _DragState(band_index=idx, mode="edge_left")
            if abs(x - right_x) <= _HIT_RADIUS_EDGE:
                return _DragState(band_index=idx, mode="edge_right")
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag is not None:
            self._apply_drag(pos.x(), pos.y())
            return
        hit = self._hit_test(pos.x(), pos.y())
        self._hover = hit
        if hit is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif hit.mode == "center":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        hit = self._hit_test(pos.x(), pos.y())
        if event.button() == Qt.MouseButton.RightButton:
            if hit is not None and hit.mode == "center":
                self._toggle_band(hit.band_index)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if hit is None:
            return
        self._drag = hit
        self._apply_drag(pos.x(), pos.y(), initial=True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None

    def leaveEvent(self, _event) -> None:
        if self._drag is None:
            self._hover = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    # ------------------------------------------------------------------

    def _apply_drag(self, x: float, y: float, *, initial: bool = False) -> None:
        assert self._drag is not None
        idx = self._drag.band_index
        plot_x, plot_y, plot_w, plot_h = self._plot_geometry()
        band = self._bands[idx]

        if self._drag.mode == "center":
            raw_f = self._freq_for_x(x, plot_x, plot_w)
            new_freq_hz = max(_F_MIN, min(_F_MAX, float(raw_f)))
            db_raw = self._db_for_y(y, plot_y, plot_h)
            new_lvl = float(
                int(
                    round(
                        max(LIVE_EQ_GAIN_DB_MIN, min(LIVE_EQ_GAIN_DB_MAX, db_raw))
                    )
                )
            )
            nb = LiveEqBandSettings(
                freq_hz=float(new_freq_hz),
                enabled=True,
                gain_db=new_lvl,
                q=float(band.q),
            )
            nb.clamp()
            self._bands[idx] = nb
        else:
            if not band.enabled:
                return
            f0 = float(band.freq_hz)
            target_f = self._freq_for_x(x, plot_x, plot_w)
            if target_f <= 0:
                return
            dist_oct = abs(math.log2(target_f / max(1e-9, f0)))
            if dist_oct < 1e-3:
                dist_oct = 1e-3
            bw_raw = 1.25 / dist_oct
            new_q = _snap_live_eq_q_drag(bw_raw)
            nb = LiveEqBandSettings(
                freq_hz=float(band.freq_hz),
                enabled=band.enabled,
                gain_db=float(band.gain_db),
                q=float(new_q),
            )
            nb.clamp()
            self._bands[idx] = nb

        if initial:
            pass
        self.bands_changed.emit(self.bands())
        self.update()

    def _toggle_band(self, idx: int) -> None:
        b = self._bands[idx]
        if b.enabled:
            nb = LiveEqBandSettings(
                freq_hz=float(b.freq_hz),
                enabled=False,
                gain_db=0.0,
                q=float(b.q),
            )
        else:
            nb = LiveEqBandSettings(
                freq_hz=float(b.freq_hz),
                enabled=True,
                gain_db=0.0,
                q=float(b.q),
            )
        nb.clamp()
        self._bands[idx] = nb
        self.bands_changed.emit(self.bands())
        self.update()

    # ------------------------------------------------------------------

    def _fine_log_steps(self) -> List[float]:
        log_min = math.log10(_F_MIN)
        log_max = math.log10(_F_MAX)
        steps: List[float] = []
        step = log_min
        while step < log_max:
            steps.append(step)
            step += 0.15
        return steps

    def _is_hovered(self, band_idx: int, mode: str) -> bool:
        act = self._drag or self._hover
        return act is not None and act.band_index == band_idx and act.mode == mode

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        plot_x, plot_y, plot_w, plot_h = self._plot_geometry()
        painter.fillRect(plot_x, plot_y, plot_w, plot_h, _BG)

        painter.setPen(QPen(_GRID, 1))
        for log_step in self._fine_log_steps():
            xf = self._x_for_freq(10 ** log_step, plot_x, plot_w)
            painter.drawLine(int(xf), plot_y, int(xf), plot_y + plot_h)
        db_loop = _DB_MIN
        while db_loop <= _DB_MAX:
            y = self._y_for_db(db_loop, plot_y, plot_h)
            painter.drawLine(plot_x, int(y), plot_x + plot_w, int(y))
            db_loop += 2.5

        painter.setPen(QPen(_GRID_MAJOR, 1))
        for freq, _lbl in _FREQ_TICKS:
            xf = self._x_for_freq(freq, plot_x, plot_w)
            painter.drawLine(int(xf), plot_y, int(xf), plot_y + plot_h)
        for db_t in _DB_TICKS:
            y = self._y_for_db(db_t, plot_y, plot_h)
            painter.drawLine(plot_x, int(y), plot_x + plot_w, int(y))

        edge_font = QFont(self.font())
        edge_font.setPointSizeF(max(6.5, edge_font.pointSizeF() * 0.62 + 1.0))
        fm_edge = QFontMetrics(edge_font)

        for idx, band in enumerate(self._bands):
            if not band.enabled:
                continue
            f0 = float(band.freq_hz)
            half = _half_width_oct_for_q(float(band.q))
            f_left_hz = f0 * (2 ** -half)
            f_right_hz = f0 * (2 ** half)
            lx = self._x_for_freq(max(_F_MIN, f_left_hz), plot_x, plot_w)
            rx = self._x_for_freq(min(_F_MAX, f_right_hz), plot_x, plot_w)
            box_x = int(min(lx, rx))
            box_w = max(1, int(abs(rx - lx)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_BW_FILL)
            painter.drawRect(box_x, plot_y, box_w, plot_h)

            painter.setPen(
                QPen(
                    _BW_EDGE_HOVER
                    if self._is_hovered(idx, "edge_left")
                    else _BW_EDGE,
                    2,
                )
            )
            painter.drawLine(int(lx), plot_y, int(lx), plot_y + plot_h)
            painter.setPen(
                QPen(
                    _BW_EDGE_HOVER
                    if self._is_hovered(idx, "edge_right")
                    else _BW_EDGE,
                    2,
                )
            )
            painter.drawLine(int(rx), plot_y, int(rx), plot_y + plot_h)

            painter.setFont(edge_font)
            painter.setPen(_LABEL_COLOR)
            txt_l = _format_edge_frequency_label(f_left_hz)
            txt_r = _format_edge_frequency_label(f_right_hz)
            wl = fm_edge.horizontalAdvance(txt_l)
            wr = fm_edge.horizontalAdvance(txt_r)
            row_gap = fm_edge.height() + 2
            baseline = plot_y + plot_h - fm_edge.descent() - 2 - idx * row_gap // 7
            inner_pad = 4
            x_l_txt = int(lx) + inner_pad
            x_r_txt = int(rx) - wr - inner_pad
            if x_r_txt < x_l_txt + wl + inner_pad:
                painter.drawText(x_l_txt, baseline - row_gap // 3, txt_l)
                painter.drawText(x_r_txt, baseline + row_gap // 3, txt_r)
            else:
                painter.drawText(x_l_txt, baseline, txt_l)
                painter.drawText(x_r_txt, baseline, txt_r)

        zero_y = self._y_for_db(0.0, plot_y, plot_h)
        painter.setPen(QPen(_ZERO_LINE, 1, Qt.PenStyle.DashLine))
        painter.drawLine(plot_x, int(zero_y), plot_x + plot_w, int(zero_y))

        painter.setPen(QPen(_GRID_MAJOR, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(plot_x, plot_y, plot_w, plot_h)

        scale_font = QFont(self.font())
        scale_font.setPointSizeF(max(6.5, scale_font.pointSizeF() * 0.7))
        painter.setFont(scale_font)
        fm = QFontMetrics(scale_font)
        painter.setPen(_LABEL_COLOR)
        for fq, lbl in _FREQ_TICKS:
            xd = self._x_for_freq(fq, plot_x, plot_w)
            painter.drawText(
                int(xd) - fm.horizontalAdvance(lbl) // 2,
                plot_y + plot_h + fm.ascent() + 1,
                lbl,
            )
        for db_lab in (-15, 0, 15):
            yd = self._y_for_db(db_lab, plot_y, plot_h)
            tex = f"{db_lab:+d}" if db_lab != 0 else "0"
            painter.drawText(2, int(yd) + fm.ascent() // 2 - 1, tex)

        log_min = math.log10(_F_MIN)
        log_max = math.log10(_F_MAX)
        polyline = QPolygonF()
        for i in range(_NUM_SAMPLES):
            frac = i / (_NUM_SAMPLES - 1)
            log_f = log_min + frac * (log_max - log_min)
            f_hz = float(10**log_f)
            db_val = max(
                _DB_MIN,
                min(_DB_MAX, _total_gain_db_live(self._bands, f_hz)),
            )
            xv = self._x_for_freq(f_hz, plot_x, plot_w)
            yv = self._y_for_db(db_val, plot_y, plot_h)
            polyline.append(QPointF(xv, yv))

        if polyline.size() >= 2:
            fill_poly = QPolygonF(polyline)
            fill_poly.append(
                QPointF(polyline.at(polyline.size() - 1).x(), zero_y)
            )
            fill_poly.append(QPointF(polyline.at(0).x(), zero_y))
            grad = QLinearGradient(0, plot_y, 0, plot_y + plot_h)
            grad.setColorAt(0.0, _CURVE_FILL_TOP)
            grad.setColorAt(1.0, _CURVE_FILL_BOTTOM)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPolygon(fill_poly)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_CURVE, 2))
            painter.drawPolyline(polyline)

        for idx, band in enumerate(self._bands):
            f0 = float(band.freq_hz)
            level = float(band.gain_db) if band.enabled else 0.0
            cx = self._x_for_freq(max(_F_MIN, min(_F_MAX, f0)), plot_x, plot_w)
            cy = self._y_for_db(level, plot_y, plot_h)
            is_off = not band.enabled
            hovered_c = self._is_hovered(idx, "center")
            col = _BAND_POINT_OFF if is_off else _BAND_POINT_ACTIVE
            radius = 6.5 if hovered_c else 5.0
            painter.setPen(QPen(QColor("#0a0a0a"), 1))
            painter.setBrush(col)
            painter.drawEllipse(QPointF(cx, cy), radius, radius)


class LiveEqCurveView(QWidget):
    """Äußeres Widget wie :class:`~gui.eq_curve_view.EqCurveView` (+ Footer‑Zeilen)."""

    bands_changed = Signal(object)  # List[LiveEqBandSettings]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.canvas = _LiveEqCurveCanvas()
        self.canvas.bands_changed.connect(self._on_canvas_bands_changed)
        layout.addWidget(self.canvas, stretch=1)

        self._footer = QLabel("—")
        self._footer.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._footer.setWordWrap(False)
        ff = self._footer.font()
        ff.setPointSizeF(ff.pointSizeF() * 0.88)
        self._footer.setFont(ff)
        self._footer.setStyleSheet("color:#a8a8a8;")
        layout.addWidget(self._footer)

    # ------------------------------------------------------------------

    def set_bands(self, bands: List[LiveEqBandSettings]) -> None:
        self.canvas.set_bands(bands)
        self._refresh_footer_from_canvas()

    def get_bands(self) -> List[LiveEqBandSettings]:
        return self.canvas.bands()

    def set_read_only(self, read_only: bool) -> None:
        self.canvas.setEnabled(not read_only)

    def _refresh_footer_from_canvas(self) -> None:
        bs = self.canvas.bands()
        texts = [_footer_cell(i + 1, b) for i, b in enumerate(bs)]
        self._footer.setText("   ·   ".join(texts) if texts else "—")

    def _on_canvas_bands_changed(self, bs: object) -> None:
        if not isinstance(bs, list) or len(bs) != _NUM_LIVE_BANDS:
            return
        self._refresh_footer_from_canvas()
        self.bands_changed.emit(_clone_bands(bs))


def _footer_cell(num: int, b: LiveEqBandSettings) -> str:
    if not b.enabled:
        return f"#{num} aus"
    f0 = float(b.freq_hz)
    ft = f"{f0 / 1000:.1f} kHz" if f0 >= 1000 else f"{int(round(f0))} Hz"
    gd = float(b.gain_db)
    gi = int(round(gd))
    if abs(float(gi) - gd) < 0.001:
        return f"#{num} {ft} {gi:+d} dB"
    return f"#{num} {ft} {gd:+.1f} dB"


__all__ = ["LiveEqCurveView", "_NUM_LIVE_BANDS"]
