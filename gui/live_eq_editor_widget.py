"""Siebenband-Live‑EQ — gleicher Aufbau wie :class:`~gui.eq_editor_widget.EQEditorWidget`.

* Oben BW‑Zeile (Q je Band)
* Mitte Plot (:class:`~gui.live_eq_curve_view.LiveEqCurveView`)
* Rechts gestapelte Level‑Anzeige
* Bedien‑Hinweis wie im Equalizer
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from gui.live_eq_curve_view import LiveEqCurveView, _NUM_LIVE_BANDS
from model.live_settings import DEFAULT_LIVE_EQ_FREQ_HZ, LiveEqBandSettings

_STATUS_ACTIVE_STYLE = "color: #2ea043; font-weight: bold;"
_STATUS_INACTIVE_STYLE = "color: #ffae42; font-style: italic;"

_VALUE_LABEL_STYLE = "QLabel { color: #d6d6d6; font-weight: 600; }"
_CAPTION_STYLE = (
    "QLabel { color: #9a9a9a; font-size: 10px; letter-spacing: 0.5px; }"
)
_INACTIVE_OPACITY = "color: #6a6a6a;"

_BW_CAPTIONS: List[str] = []
for _hz in DEFAULT_LIVE_EQ_FREQ_HZ:
    h = float(_hz)
    if h >= 1000 and h < 10000:
        BW_CAP = f"{h / 1000:g} k"
    else:
        BW_CAP = str(int(round(h)))
    _BW_CAPTIONS.append(BW_CAP)

# Rechter Stack: hohe Frequenz oben (Band 7 … Band 1)
_DB_STACK_ORDER = tuple(reversed(range(_NUM_LIVE_BANDS)))


def _format_level(b: LiveEqBandSettings) -> str:
    if not b.enabled:
        return "—"
    return f"{int(round(float(b.gain_db))):+d} dB"


def _format_q(b: LiveEqBandSettings) -> str:
    if not b.enabled:
        return "—"
    return f"Q = {int(round(float(b.q)))}"


class LiveEqEditorWidget(QWidget):
    """Equalizer‑Fenster‑Layout für die sieben Live‑DSP‑Bänder."""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._status_label = QLabel("")
        self._status_label.setVisible(False)
        font = QFont(self._status_label.font())
        font.setPointSizeF(font.pointSizeF() * 0.95)
        self._status_label.setFont(font)
        outer.addWidget(self._status_label)

        plot_row = QHBoxLayout()
        plot_row.setSpacing(10)
        outer.addLayout(plot_row, stretch=1)

        left_column = QVBoxLayout()
        left_column.setSpacing(4)
        left_column.setContentsMargins(0, 0, 0, 0)
        plot_row.addLayout(left_column, stretch=1)

        bw_row = QHBoxLayout()
        bw_row.setSpacing(0)
        self._bw_value_labels: List[QLabel] = []
        for i in range(_NUM_LIVE_BANDS):
            cell = QHBoxLayout()
            cell.setSpacing(4)
            cell.setContentsMargins(0, 0, 0, 0)
            name_label = QLabel(_BW_CAPTIONS[i])
            name_label.setStyleSheet(_CAPTION_STYLE)
            name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            value_label = QLabel("Q = 5")
            value_label.setStyleSheet(_VALUE_LABEL_STYLE)
            value_label.setMinimumWidth(52)
            value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(name_label, 0, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(value_label, 0, Qt.AlignmentFlag.AlignHCenter)
            wrap = QWidget()
            wrap.setLayout(col)
            cell_wrapper = QHBoxLayout()
            cell_wrapper.setContentsMargins(0, 0, 0, 0)
            cell_wrapper.setSpacing(0)
            cell_wrapper.addStretch(1)
            cell_wrapper.addWidget(wrap)
            cell_wrapper.addStretch(1)
            bw_row.addLayout(cell_wrapper, stretch=1)
            self._bw_value_labels.append(value_label)
        left_column.addLayout(bw_row)

        self.curve_view = LiveEqCurveView()
        self.curve_view.bands_changed.connect(self._on_curve_changed)
        left_column.addWidget(self.curve_view, stretch=1)

        plot_row.addWidget(self._build_db_stack())

        hint = QLabel(
            "Punkt ziehen = Frequenz/Level · hellblauer Rand ziehen = Bandbreite (Q) · "
            "Rechtsklick = an/aus"
        )
        hint.setStyleSheet(_CAPTION_STYLE)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.set_bands(self._default_bands())

    def _default_bands(self) -> List[LiveEqBandSettings]:
        return [
            LiveEqBandSettings(
                freq_hz=float(DEFAULT_LIVE_EQ_FREQ_HZ[i]),
                enabled=False,
                gain_db=0.0,
                q=2.0,
            )
            for i in range(_NUM_LIVE_BANDS)
        ]

    def _build_db_stack(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("liveEqDbStack")
        frame.setFrameShape(QFrame.Shape.NoFrame)
        layout = QGridLayout(frame)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(6)

        caption = QLabel("Level")
        caption.setStyleSheet(_CAPTION_STYLE)
        caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(caption, 0, 0, 1, 1)

        self._db_value_labels: List[Optional[QLabel]] = [None] * _NUM_LIVE_BANDS
        for row_offset, band_index in enumerate(_DB_STACK_ORDER, start=1):
            name_label = QLabel(f"#{band_index + 1} ({_BW_CAPTIONS[band_index]})")
            name_label.setStyleSheet(_CAPTION_STYLE)
            name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            value_label = QLabel("—")
            value_label.setStyleSheet(_VALUE_LABEL_STYLE)
            value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            value_label.setMinimumWidth(78)
            vf = QFont(value_label.font())
            vf.setPointSizeF(vf.pointSizeF() * 1.05)
            vf.setBold(True)
            value_label.setFont(vf)
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(name_label)
            cell.addWidget(value_label)
            box = QWidget()
            box.setLayout(cell)
            layout.addWidget(box, row_offset, 0)
            self._db_value_labels[band_index] = value_label
        return frame

    # ------------------------------------------------------------------

    def set_bands(self, bands: List[LiveEqBandSettings]) -> None:
        self.curve_view.set_bands(bands)
        self._update_value_labels(self.curve_view.get_bands())

    def get_bands(self) -> List[LiveEqBandSettings]:
        return self.curve_view.get_bands()

    def set_read_only(self, read_only: bool) -> None:
        self.curve_view.set_read_only(read_only)

    def set_path_status(self, *, active: bool, hint_text: str = "") -> None:
        if hint_text:
            self._status_label.setText(hint_text)
            self._status_label.setStyleSheet(
                _STATUS_ACTIVE_STYLE if active else _STATUS_INACTIVE_STYLE
            )
            self._status_label.setVisible(True)
        else:
            self._status_label.clear()
            self._status_label.setVisible(False)

        value_style = _VALUE_LABEL_STYLE if active else _INACTIVE_OPACITY
        for lbl in self._bw_value_labels:
            lbl.setStyleSheet(value_style)
        for lbl in self._db_value_labels:
            if lbl is not None:
                lbl.setStyleSheet(value_style)

    # ------------------------------------------------------------------

    def _on_curve_changed(self, bands: object) -> None:
        if not isinstance(bands, list):
            return
        self._update_value_labels(list(bands))
        self.changed.emit()

    def _update_value_labels(self, bands: List[LiveEqBandSettings]) -> None:
        nb = bands[:]
        while len(nb) < _NUM_LIVE_BANDS:
            i = len(nb)
            nb.append(LiveEqBandSettings(freq_hz=float(DEFAULT_LIVE_EQ_FREQ_HZ[i])))
        nb = nb[:_NUM_LIVE_BANDS]
        for i, lab in enumerate(self._bw_value_labels):
            lab.setText(_format_q(nb[i]))
        for i in range(_NUM_LIVE_BANDS):
            vl = self._db_value_labels[i]
            if vl is not None:
                vl.setText(_format_level(nb[i]))


__all__ = ["LiveEqEditorWidget"]
