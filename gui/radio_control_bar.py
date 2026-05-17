"""Kompakte CAT-Steuerung unter den Meter-Anzeigen (Tune, Bandwahl, Audioplayer)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from mapping.amateur_bands import VFO_BAND_CHOICE, combo_entries_high_to_low


class RadioControlBar(QFrame):
    """Tune, Amateurband-Auswahl (VFO) und Audioplayer."""

    tune_clicked = Signal()
    rev_toggled = Signal(bool)
    band_choice_activated = Signal(int)
    audio_player_clicked = Signal()
    audio_recorder_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self._tune_btn = QPushButton("Tune")
        self._tune_btn.setToolTip("Antennentuner starten (CAT AC002)")
        self._tune_btn.clicked.connect(self.tune_clicked.emit)

        self._rev_btn = QPushButton("REV")
        self._rev_btn.setCheckable(True)
        self._rev_btn.setMinimumWidth(52)
        self._rev_btn.setToolTip(
            "Relais: auf Eingangs-QRG schalten (REV ein) / zurück zur "
            "Ausgangs-QRG (REV aus)"
        )
        # Wenn REV aktiv ist, soll der Button im gleichen Gruen leuchten
        # wie die VFO-A-Beschriftung im Header (#5ddc7a).
        self._rev_btn.setStyleSheet(
            "QPushButton:checked {"
            "  background-color: #5ddc7a;"
            "  color: #101010;"
            "  font-weight: bold;"
            "  border: 1px solid #2f8a47;"
            "}"
        )
        self._rev_btn.toggled.connect(self.rev_toggled.emit)

        layout.addWidget(QLabel("Band:"))
        self._band_combo = QComboBox()
        self._band_combo.setMinimumWidth(280)
        self._band_combo.setToolTip(
            "VFO-Modus oder Amateurband (Mittenfrequenz auf VFO-A setzen)"
        )
        for label, data in combo_entries_high_to_low():
            self._band_combo.addItem(label, data)
        self._band_combo.activated.connect(self._on_band_combo_activated)

        self._audio_btn = QPushButton("Audioplayer")
        self._audio_btn.setMinimumWidth(96)
        self._audio_btn.setToolTip(
            "Audio-Player (MP3/WAV) mit CAT-PTT für Sendebetrieb"
        )
        self._audio_btn.clicked.connect(self.audio_player_clicked.emit)

        self._recorder_btn = QPushButton("Audiorecoder")
        self._recorder_btn.setMinimumWidth(110)
        self._recorder_btn.setToolTip(
            "MP3-Aufnahme mit CAT-DATA-Mode-Umschaltung "
            "(USB-CODEC → MP3, Replay über CAT-TX)"
        )
        self._recorder_btn.clicked.connect(self.audio_recorder_clicked.emit)

        self._tune_btn.setMinimumWidth(72)
        layout.addWidget(self._tune_btn)
        layout.addWidget(self._rev_btn)
        layout.addWidget(self._band_combo, stretch=1)
        layout.addWidget(self._audio_btn)
        layout.addWidget(self._recorder_btn)

        layout.addStretch(1)
        self.set_controls_enabled(False)

    def _on_band_combo_activated(self, _index: int) -> None:
        data = self._band_combo.currentData()
        if data is None:
            return
        self.band_choice_activated.emit(int(data))

    def select_vfo_item(self) -> None:
        """Combo auf „VFO“ ohne ``activated``."""
        idx = self._band_combo.findData(VFO_BAND_CHOICE)
        if idx >= 0:
            self._band_combo.blockSignals(True)
            self._band_combo.setCurrentIndex(idx)
            self._band_combo.blockSignals(False)

    def set_rev_checked(self, checked: bool) -> None:
        self._rev_btn.blockSignals(True)
        self._rev_btn.setChecked(checked)
        self._rev_btn.blockSignals(False)

    def set_controls_enabled(self, enabled: bool) -> None:
        self._tune_btn.setEnabled(enabled)
        self._rev_btn.setEnabled(enabled)
        self._band_combo.setEnabled(enabled)
        if not enabled:
            self.set_rev_checked(False)
        self._audio_btn.setEnabled(True)
        self._recorder_btn.setEnabled(True)
