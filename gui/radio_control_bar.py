"""Kompakte CAT-Tasten unter den Meter-Anzeigen (Tune, Kanal, Band)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget


class RadioControlBar(QFrame):
    """Tune, Speicherkanal ± und Amateurband ±."""

    tune_clicked = Signal()
    channel_up_clicked = Signal()
    channel_down_clicked = Signal()
    band_up_clicked = Signal()
    band_down_clicked = Signal()
    audio_player_clicked = Signal()

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

        self._ch_up_btn = QPushButton("CH +")
        self._ch_up_btn.setToolTip("Speicherkanal hoch (CAT CH0)")
        self._ch_up_btn.clicked.connect(self.channel_up_clicked.emit)

        self._ch_down_btn = QPushButton("CH −")
        self._ch_down_btn.setToolTip("Speicherkanal runter (CAT CH1)")
        self._ch_down_btn.clicked.connect(self.channel_down_clicked.emit)

        self._band_up_btn = QPushButton("Band +")
        self._band_up_btn.setToolTip("Nächstes Band (CAT BU0)")
        self._band_up_btn.clicked.connect(self.band_up_clicked.emit)

        self._band_down_btn = QPushButton("Band −")
        self._band_down_btn.setToolTip("Vorheriges Band (CAT BD0)")
        self._band_down_btn.clicked.connect(self.band_down_clicked.emit)

        self._audio_btn = QPushButton("Audioplayer")
        self._audio_btn.setMinimumWidth(96)
        self._audio_btn.setToolTip(
            "Audio-Player (MP3/WAV) mit CAT-PTT für Sendebetrieb"
        )
        self._audio_btn.clicked.connect(self.audio_player_clicked.emit)

        for btn in (
            self._tune_btn,
            self._ch_up_btn,
            self._ch_down_btn,
            self._band_up_btn,
            self._band_down_btn,
        ):
            btn.setMinimumWidth(72)
            layout.addWidget(btn)
        layout.addWidget(self._audio_btn)

        layout.addStretch(1)
        self.set_controls_enabled(False)

    def set_controls_enabled(self, enabled: bool) -> None:
        for btn in (
            self._tune_btn,
            self._ch_up_btn,
            self._ch_down_btn,
            self._band_up_btn,
            self._band_down_btn,
        ):
            btn.setEnabled(enabled)
        self._audio_btn.setEnabled(True)
