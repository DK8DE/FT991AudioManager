"""Kompakte CAT-Steuerung unter den Meter-Anzeigen (Tune, REV, Audio)."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .led_widget import Led

# Kurze rot/grün-Sequenz bei TCP-Daten (FLRig) — wie RotorTcpBridge.
_RIG_IO_BLINK_SEQ = (True, False, True, False, True, False, True, False)


class RadioControlBar(QFrame):
    """Tune, REV und Schnellzugriff auf Audio-Player / Recorder."""

    tune_clicked = Signal()
    rev_toggled = Signal(bool)
    audio_player_clicked = Signal()
    audio_recorder_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._flrig_blink_active = False
        self._flrig_blink_phase = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self._tune_btn = QPushButton("Tune")
        self._tune_btn.setMinimumWidth(72)
        self._tune_btn.setToolTip("Antennentuner starten (CAT AC002)")
        self._tune_btn.clicked.connect(self.tune_clicked.emit)

        self._rev_btn = QPushButton("REV")
        self._rev_btn.setCheckable(True)
        self._rev_btn.setMinimumWidth(52)
        self._rev_btn.setToolTip(
            "Relais: auf Eingangs-QRG schalten (REV ein) / zurück zur "
            "Ausgangs-QRG (REV aus)"
        )
        self._rev_btn.setStyleSheet(
            "QPushButton:checked {"
            "  background-color: #5ddc7a;"
            "  color: #101010;"
            "  font-weight: bold;"
            "  border: 1px solid #2f8a47;"
            "}"
        )
        self._rev_btn.toggled.connect(self.rev_toggled.emit)

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

        layout.addWidget(self._tune_btn)
        layout.addWidget(self._rev_btn)
        layout.addWidget(self._audio_btn)
        layout.addWidget(self._recorder_btn)

        # --- Rig-Bridge: FLRig (nach Audiorecorder) ----------------
        bridge_tip = (
            "FLRig-Rig-Bridge über die App-CAT-Leitung.\n"
            "Grün: Server läuft. Rot: in Einstellungen aus oder gestoppt / kein CAT.\n"
            "Rot/Grün wechselnd: gerade TCP-Datenverkehr."
        )
        self._lbl_flrig_title = QLabel("FLRig")
        self._lbl_flrig_title.setToolTip(bridge_tip)
        lf = self._lbl_flrig_title.font()
        lf.setBold(True)
        self._lbl_flrig_title.setFont(lf)
        self._led_flrig = Led(9, self)
        self._led_flrig.setToolTip(bridge_tip)
        self._lbl_flrig_clients = QLabel("—")
        self._lbl_flrig_clients.setMinimumWidth(22)
        self._lbl_flrig_clients.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._lbl_flrig_clients.setToolTip("Anzahl verbundener Clients (logische Gegenstellen)")

        layout.addSpacing(10)
        layout.addWidget(self._lbl_flrig_title)
        layout.addWidget(self._led_flrig)
        layout.addWidget(self._lbl_flrig_clients)

        layout.addStretch(1)
        self.set_controls_enabled(False)

    def refresh_rig_bridge_indicators(
        self,
        rig_bridge_cfg: dict[str, Any],
        proto_status: dict[str, Any],
        flrig_io: bool,
    ) -> None:
        """Aktualisiert LED und Client-Zähler (periodisch vom Hauptfenster)."""
        rb = rig_bridge_cfg or {}
        rb_on = bool(rb.get("enabled", True))
        fl_cfg = rb.get("flrig") if isinstance(rb.get("flrig"), dict) else {}
        fl_want = rb_on and bool(fl_cfg.get("enabled", True))

        fl_on = bool(proto_status.get("flrig_active"))
        n_fl = int(proto_status.get("flrig_clients", 0) or 0)

        seq = _RIG_IO_BLINK_SEQ
        if flrig_io and fl_want and fl_on:
            self._flrig_blink_active = True
            self._flrig_blink_phase = 0
        if self._flrig_blink_active:
            if self._flrig_blink_phase < len(seq):
                self._led_flrig.set_state(bool(seq[self._flrig_blink_phase]))
                self._flrig_blink_phase += 1
            else:
                self._flrig_blink_active = False
        if not self._flrig_blink_active:
            self._led_flrig.set_state(bool(fl_want and fl_on))
        if fl_want and fl_on:
            self._lbl_flrig_clients.setText(str(n_fl))
        else:
            self._lbl_flrig_clients.setText("—")

    def set_rev_checked(self, checked: bool) -> None:
        self._rev_btn.blockSignals(True)
        self._rev_btn.setChecked(checked)
        self._rev_btn.blockSignals(False)

    def set_controls_enabled(self, enabled: bool) -> None:
        self._tune_btn.setEnabled(enabled)
        self._rev_btn.setEnabled(enabled)
        if not enabled:
            self.set_rev_checked(False)
        self._audio_btn.setEnabled(True)
        self._recorder_btn.setEnabled(True)
