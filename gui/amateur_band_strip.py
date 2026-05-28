"""Band-Streifen: Position der VFO-A-Frequenz im Amateurband."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from mapping.amateur_bands import (
    AmateurBand,
    BandKind,
    band_strip_groove_tick_frequencies,
    band_strip_label_tick_frequencies,
    band_strip_tick_label,
    snap_band_strip_frequency_hz,
)

from i18n import tr
from i18n.retranslatable import RetranslatableMixin

_TRACK_HEIGHT = 12
_TRACK_RADIUS = 5
_LABEL_ROW_H = 16
_TICK_BELOW_BAR_PX = 4
_LABEL_GAP_BELOW_TICK_PX = 5
_LABEL_EXTRA_OFFSET_PX = 5
_NEEDLE_HIT_PX = 14
# Mindestabstand zwischen rechter und linker Kante benachbarter Beschriftungen (Pixel).
_MIN_LABEL_TEXT_GAP_PX = 8
# Striche innerhalb des schwarzen Track-Balkens (inaktiver Zustand: anderer Track).
_INNER_TICK_ON_DARK_TRACK = QColor(255, 255, 255)
_INACTIVE_GRAY = QColor(90, 90, 92)
_TRACK_FILL = QColor(0, 0, 0)
_TRACK_BORDER = QColor(255, 255, 255)
_NEEDLE_COLOR = QColor(255, 80, 80)
_NEEDLE_LINE_COLOR = QColor(255, 70, 70)
_NEEDLE_COLOR_SPECIAL = QColor(240, 192, 48)
_NEEDLE_LINE_COLOR_SPECIAL = QColor(220, 170, 30)
_TRACK_BORDER_SPECIAL = QColor(240, 200, 64)


class AmateurBandStripWidget(RetranslatableMixin, QWidget):
    """Horizontaler Streifen mit Ticks, Frequenzlabels und beweglichem Zeiger."""

    frequency_changed = Signal(int)
    frequency_drag_finished = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._band: Optional[AmateurBand] = None
        self._frequency_hz = 0
        self._target_ratio = 0.0
        self._display_ratio = 0.0
        self._active = False
        self._dragging = False
        self._groove_ticks: List[int] = []
        self._label_ticks: List[int] = []
        self.setMinimumHeight(
            _TRACK_HEIGHT + _LABEL_ROW_H + 18 + _LABEL_EXTRA_OFFSET_PX
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setMouseTracking(True)
        self._register_retranslate()
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._animate)
        self._timer.start()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self._update_tooltip()
        self.update()

    def is_dragging(self) -> bool:
        return self._dragging

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update()

    def set_band(self, band: Optional[AmateurBand]) -> None:
        if self._band == band:
            return
        self._band = band
        self._groove_ticks = (
            band_strip_groove_tick_frequencies(band) if band is not None else []
        )
        self._label_ticks = (
            band_strip_label_tick_frequencies(band) if band is not None else []
        )
        self._recompute_target_ratio()
        self.update()

    def set_frequency_hz(self, hz: int) -> None:
        if self._dragging:
            return
        f = int(hz)
        if f == self._frequency_hz:
            return
        self._frequency_hz = f
        self._recompute_target_ratio()
        self._update_tooltip()

    def set_active(self, active: bool) -> None:
        if self._active == bool(active):
            return
        self._active = bool(active)
        if not self._active:
            self._target_ratio = 0.0
            self._display_ratio = 0.0
            self._dragging = False
            self.unsetCursor()
        else:
            self._recompute_target_ratio()
        self._update_tooltip()
        self.update()

    def _band_span_hz(self) -> int:
        if self._band is None:
            return 0
        return self._band.max_hz - self._band.min_hz

    def _band_kind(self) -> Optional[BandKind]:
        return self._band.kind if self._band is not None else None

    def _is_special_band(self) -> bool:
        kind = self._band_kind()
        return kind is not None and kind is not BandKind.AMATEUR

    def _needle_colors(self) -> tuple[QColor, QColor, QColor]:
        if self._is_special_band():
            return (
                _NEEDLE_COLOR_SPECIAL,
                _NEEDLE_LINE_COLOR_SPECIAL,
                QColor(140, 100, 10),
            )
        return _NEEDLE_COLOR, _NEEDLE_LINE_COLOR, QColor(120, 20, 20)

    def _track_border_color(self) -> QColor:
        if self._is_special_band():
            return _TRACK_BORDER_SPECIAL
        return _TRACK_BORDER

    def _inner_ticks(self) -> List[int]:
        """Kanal-/Tick-Markierungen ohne ersten/letzten Strich am Balkenrand."""
        if len(self._groove_ticks) <= 2:
            return []
        return self._groove_ticks[1:-1]

    def _in_band_frequency(self) -> bool:
        return (
            self._active
            and self._band is not None
            and self._frequency_hz > 0
            and self._band.min_hz <= self._frequency_hz <= self._band.max_hz
        )

    def _recompute_target_ratio(self) -> None:
        if (
            not self._active
            or self._band is None
            or self._frequency_hz <= 0
        ):
            self._target_ratio = 0.0
            return
        span = self._band_span_hz()
        if span <= 0:
            self._target_ratio = 0.0
            return
        if self._frequency_hz < self._band.min_hz:
            self._target_ratio = 0.0
        elif self._frequency_hz > self._band.max_hz:
            self._target_ratio = 1.0
        else:
            self._target_ratio = (self._frequency_hz - self._band.min_hz) / span
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        if not self._active:
            self.setToolTip(tr("band_strip.tooltip_disconnected"))
            return
        if self._band is None or self._frequency_hz <= 0:
            self.setToolTip(tr("band_strip.tooltip_outside"))
            return
        mhz = self._frequency_hz / 1_000_000.0
        pct = int(round(self._target_ratio * 100))
        self.setToolTip(
            tr(
                "band_strip.tooltip_active",
                mhz=mhz,
                band=self._band.name,
                pct=pct,
            )
        )

    def _animate(self) -> None:
        if self._dragging:
            return
        if abs(self._display_ratio - self._target_ratio) < 0.0005:
            if self._display_ratio != self._target_ratio:
                self._display_ratio = self._target_ratio
                self.update()
            return
        self._display_ratio += (self._target_ratio - self._display_ratio) * 0.35
        self.update()

    def _label_font(self) -> QFont:
        font = self.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        return font

    def _horizontal_label_pad(self) -> int:
        if self._band is None or not self._label_ticks:
            return 6
        fm = QFontMetrics(self._label_font())
        first = band_strip_tick_label(self._label_ticks[0], self._band)
        last = band_strip_tick_label(self._label_ticks[-1], self._band)
        return max(
            6,
            (fm.horizontalAdvance(first) + 4) // 2,
            (fm.horizontalAdvance(last) + 4) // 2,
        )

    def _track_rect(self):
        pad = self._horizontal_label_pad()
        top = 8
        bottom = _LABEL_ROW_H + 6 + _LABEL_EXTRA_OFFSET_PX
        return self.rect().adjusted(pad, top, -pad, -bottom)

    def _hz_to_x(self, hz: int, track) -> float:
        if self._band is None:
            return float(track.left())
        span = self._band_span_hz()
        if span <= 0:
            return float(track.left())
        ratio = (hz - self._band.min_hz) / span
        ratio = max(0.0, min(1.0, ratio))
        return track.left() + ratio * track.width()

    def _x_to_hz(self, x: float) -> Optional[int]:
        if self._band is None:
            return None
        track = self._track_rect()
        if track.width() <= 0:
            return None
        ratio = (x - track.left()) / track.width()
        ratio = max(0.0, min(1.0, ratio))
        span = self._band_span_hz()
        hz = self._band.min_hz + int(round(ratio * span))
        hz = max(self._band.min_hz, min(self._band.max_hz, hz))
        return snap_band_strip_frequency_hz(hz, self._band)

    def _apply_drag_hz(self, hz: int) -> None:
        if self._band is None:
            return
        self._frequency_hz = hz
        span = self._band_span_hz()
        if span > 0:
            self._target_ratio = (hz - self._band.min_hz) / span
            self._display_ratio = self._target_ratio
        self._update_tooltip()
        self.update()
        self.frequency_changed.emit(hz)

    def _label_text_bounds(
        self,
        x: float,
        label: str,
        align: str,
        fm: QFontMetrics,
    ) -> tuple[int, int]:
        """Horizontale Pixelspanne wie in :meth:`_draw_tick_label` (links, rechts exkl.)."""
        text_w = fm.horizontalAdvance(label)
        margin = 2
        if align == "left":
            text_x = max(margin, int(round(x)))
        elif align == "right":
            text_x = min(self.width() - margin - text_w, int(round(x)) - text_w)
        else:
            text_x = int(round(x - text_w / 2))
            text_x = max(margin, min(text_x, self.width() - margin - text_w))
        return text_x, text_x + text_w

    def _visible_tick_labels(self, track, font: QFont) -> List[tuple[int, str, float, str]]:
        if self._band is None or not self._label_ticks:
            return []
        fm = QFontMetrics(font)
        items: List[tuple[int, str, float, str]] = []
        for i, hz in enumerate(self._label_ticks):
            label = band_strip_tick_label(hz, self._band)
            x = self._hz_to_x(hz, track)
            if i == 0:
                align = "left"
            elif i == len(self._label_ticks) - 1:
                align = "right"
            else:
                align = "center"
            items.append((hz, label, x, align))
        if not items:
            return []

        gap = max(_MIN_LABEL_TEXT_GAP_PX, int(fm.averageCharWidth()))

        visible: List[tuple[int, str, float, str]] = [items[0]]
        _, pr = self._label_text_bounds(items[0][2], items[0][1], items[0][3], fm)

        for entry in items[1:-1]:
            _hz, label, x, align = entry
            pl, pr_new = self._label_text_bounds(x, label, align, fm)
            if pl >= pr + gap:
                visible.append(entry)
                pr = pr_new

        if len(items) <= 1:
            return visible

        last = items[-1]
        ll, _lr = self._label_text_bounds(last[2], last[1], last[3], fm)

        while len(visible) > 1:
            _, pr_tail = self._label_text_bounds(
                visible[-1][2], visible[-1][1], visible[-1][3], fm
            )
            if ll >= pr_tail + gap:
                break
            visible.pop()

        if last[0] != visible[-1][0]:
            _, pr_tail = self._label_text_bounds(
                visible[-1][2], visible[-1][1], visible[-1][3], fm
            )
            if ll >= pr_tail + gap:
                visible.append(last)

        return visible

    def _draw_tick_label(
        self,
        p: QPainter,
        x: float,
        label: str,
        text_y: int,
        align: str,
        fm: QFontMetrics,
    ) -> None:
        text_x, _ = self._label_text_bounds(x, label, align, fm)
        p.drawText(text_x, text_y, label)

    def _interactive(self) -> bool:
        return self._in_band_frequency() and self._band is not None

    def _update_hover_cursor(self, pos) -> None:
        if not self._interactive():
            self.unsetCursor()
            return
        track = self._track_rect()
        needle_x = track.left() + self._display_ratio * track.width()
        near_needle = abs(pos.x() - needle_x) <= _NEEDLE_HIT_PX
        on_track = track.contains(pos.toPoint()) or near_needle
        if on_track:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._interactive()
        ):
            hz = self._x_to_hz(event.position().x())
            if hz is not None:
                self._dragging = True
                self._apply_drag_hz(hz)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            hz = self._x_to_hz(event.position().x())
            if hz is not None and hz != self._frequency_hz:
                self._apply_drag_hz(hz)
            event.accept()
            return
        self._update_hover_cursor(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            if self._frequency_hz > 0:
                self.frequency_drag_finished.emit(self._frequency_hz)
            self._update_hover_cursor(event.position())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            self.unsetCursor()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            track = self._track_rect()
            if track.width() < 8:
                return

            in_band = self._in_band_frequency()
            label_font = self._label_font()
            p.setFont(label_font)
            fm = QFontMetrics(label_font)

            if in_band:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_TRACK_FILL)
                p.drawRoundedRect(track, _TRACK_RADIUS, _TRACK_RADIUS)
                p.setPen(QPen(self._track_border_color(), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(track, _TRACK_RADIUS, _TRACK_RADIUS)
                for hz in self._inner_ticks():
                    x = self._hz_to_x(hz, track)
                    p.setPen(QPen(_INNER_TICK_ON_DARK_TRACK, 1))
                    p.drawLine(int(x), track.top() + 1, int(x), track.bottom() - 1)
            else:
                p.setPen(QPen(QColor(80, 80, 82), 1))
                p.setBrush(QColor(22, 22, 24))
                p.drawRoundedRect(track, _TRACK_RADIUS, _TRACK_RADIUS)

            if not self._active:
                p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
                p.drawText(
                    track,
                    Qt.AlignmentFlag.AlignCenter,
                    tr("band_strip.paint_not_connected"),
                )
                return

            if self._band is None:
                ph = self.palette().color(QPalette.ColorRole.PlaceholderText)
                p.setPen(ph if ph.isValid() else _INACTIVE_GRAY)
                p.drawText(
                    track,
                    Qt.AlignmentFlag.AlignCenter,
                    tr("band_strip.paint_outside"),
                )
                return

            tick_end_y = track.bottom() + _TICK_BELOW_BAR_PX
            text_y = tick_end_y + _LABEL_GAP_BELOW_TICK_PX + fm.ascent()
            tick_color = self.palette().color(QPalette.ColorRole.WindowText)
            inner_tick_set = set(self._inner_ticks())
            for hz, label, x, align in self._visible_tick_labels(track, label_font):
                if hz in inner_tick_set:
                    p.setPen(QPen(tick_color, 1))
                    p.drawLine(int(x), track.bottom(), int(x), tick_end_y)
                p.setPen(tick_color)
                self._draw_tick_label(p, x, label, text_y, align, fm)

            if in_band:
                needle_x = track.left() + self._display_ratio * track.width()
                needle_h = 9
                needle_fill, needle_line, needle_outline = self._needle_colors()
                tri = QPolygonF(
                    [
                        QPointF(needle_x, track.top() + needle_h),
                        QPointF(needle_x - 6, track.top()),
                        QPointF(needle_x + 6, track.top()),
                    ]
                )
                p.setPen(QPen(needle_outline, 1))
                p.setBrush(needle_fill)
                p.drawPolygon(tri)
                p.setPen(QPen(needle_line, 2))
                p.drawLine(
                    int(needle_x),
                    track.top() + needle_h,
                    int(needle_x),
                    track.bottom(),
                )
