"""Drei-Band-Editor für ein Parametric-EQ-Set.

Statt klassischer Slider erfolgt die komplette Bedienung über
Maus-Interaktion auf der :class:`EqCurveView`:

* Punkt **ziehen**          → Frequenz (X) und Level (Y)
* Hellblaue **BW-Kante**    → Bandbreite (Q) durch Aufziehen
* **Rechtsklick** auf Punkt → Band an/aus (toggle ``OFF``)

Zusätzliche Anzeigen rund um den Plot:

* Oben:  aktuelle Bandbreiten (Q) der drei Bänder
* Rechts: aktueller Level (dB) gestapelt für HIGH / MID / LOW
* Unten (im Plot):  aktuelle Center-Frequenzen LOW / MID / HIGH

Die externen Schnittstellen (``set_settings``, ``get_settings``,
``set_read_only``, ``set_path_status``, ``changed``) bleiben kompatibel
zur vorherigen Version, damit der Rest der App nicht angefasst werden
muss.
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

from model.eq_band import EQBand, EQSettings

from .eq_curve_view import EqCurveView


# Banner-Stile (aktiver vs. inaktiver EQ-Pfad).
_STATUS_ACTIVE_STYLE = "color: #2ea043; font-weight: bold;"
_STATUS_INACTIVE_STYLE = "color: #ffae42; font-style: italic;"

# Stile für die seitlichen Wert-Anzeigen.
_VALUE_LABEL_STYLE = (
    "QLabel { color: #d6d6d6; font-weight: 600; }"
)
_CAPTION_STYLE = (
    "QLabel { color: #9a9a9a; font-size: 10px; letter-spacing: 0.5px; }"
)
_INACTIVE_OPACITY = "color: #6a6a6a;"

_BAND_LABELS = ("LOW", "MID", "HIGH")
# Reihenfolge im rechten Stack: HIGH oben, LOW unten — wie an einem
# Mischpult-EQ. (Indizes 0=LOW, 1=MID, 2=HIGH.)
_DB_STACK_ORDER = (2, 1, 0)


def _format_level(band: EQBand) -> str:
    if band.freq == "OFF":
        return "—"
    return f"{int(band.level):+d} dB"


def _format_bw(band: EQBand) -> str:
    if band.freq == "OFF":
        return "—"
    return f"Q = {int(band.bw)}"


class EQEditorWidget(QWidget):
    """Komplettes 3-Band-EQ-Editorwidget — vollständig per Maus bedienbar."""

    changed = Signal()
    """Wird gefeuert, sobald sich die EQ-Einstellungen ändern."""

    def __init__(self, parent: Optional[QWidget] = None, *, title: Optional[str] = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        if title:
            heading = QLabel(f"<b>{title}</b>")
            outer.addWidget(heading)

        # Status-Banner (aktiv / inaktiv) ----------------------------------
        self._status_label = QLabel("")
        self._status_label.setVisible(False)
        font = QFont(self._status_label.font())
        font.setPointSizeF(font.pointSizeF() * 0.95)
        self._status_label.setFont(font)
        outer.addWidget(self._status_label)

        # Mittelteil: linke Spalte (BW-Werte + Plot) | rechte Spalte (dB)
        plot_row = QHBoxLayout()
        plot_row.setSpacing(10)
        outer.addLayout(plot_row, stretch=1)

        left_column = QVBoxLayout()
        left_column.setSpacing(4)
        left_column.setContentsMargins(0, 0, 0, 0)
        plot_row.addLayout(left_column, stretch=1)

        # Oben in der linken Spalte: Bandbreiten gleichmäßig auf drei
        # Spalten verteilt, sodass LOW/MID/HIGH grob über ihren Center-
        # Punkten im Plot sitzen.
        bw_row = QHBoxLayout()
        bw_row.setSpacing(0)
        self._bw_value_labels: List[QLabel] = []
        for i, label_text in enumerate(_BAND_LABELS):
            cell = QHBoxLayout()
            cell.setSpacing(4)
            cell.setContentsMargins(0, 0, 0, 0)
            name_label = QLabel(label_text)
            name_label.setStyleSheet(_CAPTION_STYLE)
            value_label = QLabel("Q = 5")
            value_label.setStyleSheet(_VALUE_LABEL_STYLE)
            value_label.setMinimumWidth(46)
            cell_wrapper = QHBoxLayout()
            cell_wrapper.setContentsMargins(0, 0, 0, 0)
            cell_wrapper.setSpacing(0)
            cell_wrapper.addStretch(1)
            cell.addWidget(name_label)
            cell.addWidget(value_label)
            cell_wrapper.addLayout(cell)
            cell_wrapper.addStretch(1)
            bw_row.addLayout(cell_wrapper, stretch=1)
            self._bw_value_labels.append(value_label)
        left_column.addLayout(bw_row)

        self.curve_view = EqCurveView()
        self.curve_view.settings_changed.connect(self._on_curve_changed)
        left_column.addWidget(self.curve_view, stretch=1)

        plot_row.addWidget(self._build_db_stack())

        # Hinweis-Zeile zur Bedienung
        hint = QLabel(
            "Punkt ziehen = Frequenz/Level · hellblauer Rand ziehen = Bandbreite · "
            "Rechtsklick = an/aus"
        )
        hint.setStyleSheet(_CAPTION_STYLE)
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Programmatischer Initialwert (alles ``OFF``) ---------------------
        self._update_value_labels(EQSettings.default())

    # ------------------------------------------------------------------
    # Sub-Layout: rechter dB-Stack
    # ------------------------------------------------------------------

    def _build_db_stack(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("eqDbStack")
        frame.setFrameShape(QFrame.NoFrame)
        layout = QGridLayout(frame)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(8)

        caption = QLabel("Level")
        caption.setStyleSheet(_CAPTION_STYLE)
        caption.setAlignment(Qt.AlignHCenter)
        layout.addWidget(caption, 0, 0, 1, 1)

        # 3 Zeilen für HIGH / MID / LOW (Reihenfolge in _DB_STACK_ORDER).
        self._db_value_labels: List[Optional[QLabel]] = [None, None, None]
        for row_offset, band_index in enumerate(_DB_STACK_ORDER, start=1):
            name_label = QLabel(_BAND_LABELS[band_index])
            name_label.setStyleSheet(_CAPTION_STYLE)
            name_label.setAlignment(Qt.AlignHCenter)
            value_label = QLabel("—")
            value_label.setStyleSheet(_VALUE_LABEL_STYLE)
            value_label.setAlignment(Qt.AlignHCenter)
            value_label.setMinimumWidth(60)
            value_font = QFont(value_label.font())
            value_font.setPointSizeF(value_font.pointSizeF() * 1.05)
            value_font.setBold(True)
            value_label.setFont(value_font)
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(name_label)
            cell.addWidget(value_label)
            cell_container = QWidget()
            cell_container.setLayout(cell)
            layout.addWidget(cell_container, row_offset, 0)
            self._db_value_labels[band_index] = value_label
        return frame

    # ------------------------------------------------------------------
    # Externe API
    # ------------------------------------------------------------------

    def set_settings(self, settings: EQSettings) -> None:
        """Programmatischer Set — aktualisiert Plot und Wert-Anzeigen."""
        # ``settings_changed`` wird vom EqCurveView nur bei echten Maus-
        # Interaktionen gefeuert, daher hier kein Block-Aufwand nötig.
        self.curve_view.set_settings(settings)
        self._update_value_labels(settings)
        # Bestehender Vertrag: ``changed`` wird auch beim programmatischen
        # Set gefeuert (so verhielt sich auch die vorherige Implementierung).
        self.changed.emit()

    def get_settings(self) -> EQSettings:
        return self.curve_view.get_settings()

    def set_read_only(self, read_only: bool) -> None:
        self.curve_view.set_read_only(read_only)

    # ------------------------------------------------------------------

    def set_path_status(self, *, active: bool, hint_text: str = "") -> None:
        """Markiert den EQ-Pfad als aktiv/inaktiv (rein optisch)."""
        if hint_text:
            self._status_label.setText(hint_text)
            self._status_label.setStyleSheet(
                _STATUS_ACTIVE_STYLE if active else _STATUS_INACTIVE_STYLE
            )
            self._status_label.setVisible(True)
        else:
            self._status_label.clear()
            self._status_label.setVisible(False)

        # Dezenter Hint via Stylesheet auf den seitlichen Wert-Labels.
        value_style = _VALUE_LABEL_STYLE if active else _INACTIVE_OPACITY
        for label in self._bw_value_labels:
            label.setStyleSheet(value_style)
        for label in self._db_value_labels:
            if label is not None:
                label.setStyleSheet(value_style)

    # ------------------------------------------------------------------
    # Slot vom EqCurveView
    # ------------------------------------------------------------------

    def _on_curve_changed(self, settings: object) -> None:
        if not isinstance(settings, EQSettings):
            return
        self._update_value_labels(settings)
        self.changed.emit()

    # ------------------------------------------------------------------

    def _update_value_labels(self, settings: EQSettings) -> None:
        bands = [settings.eq1, settings.eq2, settings.eq3]
        for label, band in zip(self._bw_value_labels, bands):
            label.setText(_format_bw(band))
        for i, band in enumerate(bands):
            value_label = self._db_value_labels[i]
            if value_label is not None:
                value_label.setText(_format_level(band))
