"""Globales Fenster: Soundeinstellungen (Bearbeiten → Soundeinstellung)."""

from __future__ import annotations

import base64
import sys
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from audio.audio_recorder import list_audio_input_devices
from audio.audio_settings_hub import AudioSettingsHub
from audio.player_controller import list_audio_output_devices
from audio.windows_endpoint_volume import windows_endpoint_volume_available
from model.global_audio_settings import ROLE_INPUT, ROLE_PC, ROLE_SEND

from .app_icon import app_icon
from .audio_hub_binding import connect_level_meters
from .menu_icons import (
    volume_role_pc_icon,
    volume_role_record_icon,
    volume_role_send_icon,
)
from .volume_control_row import VolumeControlRow
from .window_lifecycle import application_exit_close_requested

if TYPE_CHECKING:
    from model import AppSettings


class SoundSettingsWindow(QMainWindow):
    """Zentrale Auswahl von Aufnahme-, Sende- und PC-Soundkarte inkl. Windows-Lautstärke."""

    closed = Signal()

    def __init__(
        self,
        settings: "AppSettings",
        audio_hub: AudioSettingsHub,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._hub = audio_hub

        self.setWindowTitle("Soundeinstellungen")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.resize(520, 420)

        self._hub.device_changed.connect(self._on_hub_device_changed)
        self._hub.volume_changed.connect(self._on_hub_volume_changed)
        self._hub.mute_changed.connect(self._on_hub_mute_changed)
        self._hub.tx_monitor_changed.connect(self._on_hub_tx_monitor_changed)

        self._build_ui()
        connect_level_meters(
            self._hub,
            {
                ROLE_INPUT: self._vol_input,
                ROLE_SEND: self._vol_send,
                ROLE_PC: self._vol_pc,
            },
        )
        self._load_from_hub()
        self._restore_geometry()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        hint = QLabel(
            "Diese Einstellungen gelten für Audio-Player und Audio-Recorder. "
            "Lautstärke steuert den Windows-Mixer des gewählten Geräts "
            + (
                "(Windows-Sync aktiv)."
                if windows_endpoint_volume_available()
                else (
                    "(Windows-Sync inaktiv — im Terminal: "
                    f"{sys.executable} -m pip install pycaw comtypes)"
                )
            )
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        box = QGroupBox("Geräte & Lautstärke")
        lay = QVBoxLayout(box)
        label_w = 170

        def form_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setMinimumWidth(label_w)
            return lbl

        in_row = QHBoxLayout()
        in_row.addWidget(form_label("Aufnahme-Gerät:"))
        self._combo_input = QComboBox()
        self._fill_input_devices()
        self._combo_input.currentIndexChanged.connect(self._on_input_device)
        in_row.addWidget(self._combo_input, 1)
        lay.addLayout(in_row)

        self._vol_input = VolumeControlRow(
            tooltip="Windows-Lautstärke des Aufnahme-Geräts",
            leading_icon=volume_role_record_icon(),
        )
        self._vol_input.value_changed.connect(
            lambda v: self._hub.set_volume_percent(ROLE_INPUT, v)
        )
        self._vol_input.mute_toggled.connect(
            lambda m: self._hub.set_muted(ROLE_INPUT, m)
        )
        vol_in = QHBoxLayout()
        vol_in.addWidget(form_label("Aufnahme-Lautstärke:"))
        vol_in.addWidget(self._vol_input, 1)
        lay.addLayout(vol_in)

        send_row = QHBoxLayout()
        send_row.addWidget(form_label("Sende-Ausgabe:"))
        self._combo_send = QComboBox()
        self._combo_send.setToolTip("CAT-Sendung / Replay (zum Funkgerät)")
        self._fill_output_devices(self._combo_send)
        self._combo_send.currentIndexChanged.connect(self._on_send_device)
        send_row.addWidget(self._combo_send, 1)
        lay.addLayout(send_row)

        self._vol_send = VolumeControlRow(
            tooltip="Windows-Lautstärke der Sende-Soundkarte",
            leading_icon=volume_role_send_icon(),
        )
        self._vol_send.value_changed.connect(
            lambda v: self._hub.set_volume_percent(ROLE_SEND, v)
        )
        self._vol_send.mute_toggled.connect(
            lambda m: self._hub.set_muted(ROLE_SEND, m)
        )
        vol_send = QHBoxLayout()
        vol_send.addWidget(form_label("Sende-Lautstärke:"))
        vol_send.addWidget(self._vol_send, 1)
        lay.addLayout(vol_send)

        pc_row = QHBoxLayout()
        pc_row.addWidget(form_label("PC-Ausgabe:"))
        self._combo_pc = QComboBox()
        self._combo_pc.setToolTip("Lokale Vorhöre / Mithören am PC")
        self._fill_output_devices(self._combo_pc)
        self._combo_pc.currentIndexChanged.connect(self._on_pc_device)
        pc_row.addWidget(self._combo_pc, 1)
        lay.addLayout(pc_row)

        self._vol_pc = VolumeControlRow(
            tooltip="Windows-Lautstärke der PC-Ausgabe",
            leading_icon=volume_role_pc_icon(),
        )
        self._vol_pc.value_changed.connect(
            lambda v: self._hub.set_volume_percent(ROLE_PC, v)
        )
        self._vol_pc.mute_toggled.connect(lambda m: self._hub.set_muted(ROLE_PC, m))
        vol_pc = QHBoxLayout()
        vol_pc.addWidget(form_label("PC-Lautstärke:"))
        vol_pc.addWidget(self._vol_pc, 1)
        lay.addLayout(vol_pc)

        self._check_tx_monitor = QCheckBox("Ausgabe Mithören")
        self._check_tx_monitor.setToolTip(
            "Während CAT-Sendung/Replay dieselbe Tonspur zusätzlich auf dem "
            "PC-Ausgabegerät wiedergeben."
        )
        self._check_tx_monitor.toggled.connect(self._on_tx_monitor)
        lay.addWidget(self._check_tx_monitor)

        root.addWidget(box)
        root.addStretch(1)
        self.setCentralWidget(central)

    def _fill_input_devices(self) -> None:
        self._combo_input.blockSignals(True)
        try:
            self._combo_input.clear()
            for _i, (dev_id, label) in enumerate(list_audio_input_devices()):
                self._combo_input.addItem(label, dev_id)
        finally:
            self._combo_input.blockSignals(False)

    @staticmethod
    def _fill_output_devices(combo: QComboBox) -> None:
        combo.blockSignals(True)
        try:
            combo.clear()
            for _i, (dev_id, label) in enumerate(list_audio_output_devices()):
                combo.addItem(label, dev_id)
        finally:
            combo.blockSignals(False)

    def _select_combo_device(self, combo: QComboBox, device_id: str) -> None:
        combo.blockSignals(True)
        try:
            idx = 0
            for i in range(combo.count()):
                if combo.itemData(i) == device_id:
                    idx = i
                    break
            combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)

    def _load_from_hub(self) -> None:
        g = self._hub.global_audio
        self._select_combo_device(self._combo_input, g.input_device_id)
        self._select_combo_device(self._combo_send, g.send_output_device_id)
        self._select_combo_device(self._combo_pc, g.pc_output_device_id)
        self._vol_input.set_value(g.input_volume_percent)
        self._vol_input.set_muted(g.input_muted)
        self._vol_send.set_value(g.send_volume_percent)
        self._vol_send.set_muted(g.send_muted)
        self._vol_pc.set_value(g.pc_volume_percent)
        self._vol_pc.set_muted(g.pc_muted)
        self._check_tx_monitor.blockSignals(True)
        try:
            self._check_tx_monitor.setChecked(g.tx_monitor_to_pc_enabled)
        finally:
            self._check_tx_monitor.blockSignals(False)

    def _on_input_device(self) -> None:
        dev_id = self._combo_input.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._hub.set_device_id(ROLE_INPUT, dev_id)

    def _on_send_device(self) -> None:
        dev_id = self._combo_send.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._hub.set_device_id(ROLE_SEND, dev_id)

    def _on_pc_device(self) -> None:
        dev_id = self._combo_pc.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._hub.set_device_id(ROLE_PC, dev_id)

    def _on_tx_monitor(self, checked: bool) -> None:
        self._hub.set_tx_monitor_to_pc_enabled(bool(checked))

    def _on_hub_device_changed(self, role: str, device_id: str) -> None:
        if role == ROLE_INPUT:
            self._select_combo_device(self._combo_input, device_id)
        elif role == ROLE_SEND:
            self._select_combo_device(self._combo_send, device_id)
        elif role == ROLE_PC:
            self._select_combo_device(self._combo_pc, device_id)

    def _on_hub_volume_changed(self, role: str, percent: int) -> None:
        if role == ROLE_INPUT:
            self._vol_input.set_value(percent)
        elif role == ROLE_SEND:
            self._vol_send.set_value(percent)
        elif role == ROLE_PC:
            self._vol_pc.set_value(percent)

    def _on_hub_mute_changed(self, role: str, muted: bool) -> None:
        if role == ROLE_INPUT:
            self._vol_input.set_muted(muted)
        elif role == ROLE_SEND:
            self._vol_send.set_muted(muted)
        elif role == ROLE_PC:
            self._vol_pc.set_muted(muted)

    def _on_hub_tx_monitor_changed(self, enabled: bool) -> None:
        self._check_tx_monitor.blockSignals(True)
        try:
            self._check_tx_monitor.setChecked(bool(enabled))
        finally:
            self._check_tx_monitor.blockSignals(False)

    def _save_geometry(self) -> None:
        geo = self.saveGeometry()
        if geo.isEmpty():
            return
        self._settings.global_audio.window_geometry = base64.b64encode(
            geo.data()
        ).decode("ascii")

    def _restore_geometry(self) -> None:
        raw = self._settings.global_audio.window_geometry
        if not raw:
            return
        try:
            data = base64.b64decode(raw.encode("ascii"))
            geo = QByteArray(data)
            if not geo.isEmpty():
                self.restoreGeometry(geo)
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_geometry()
        self.closed.emit()
        super().closeEvent(event)

    def force_close(self) -> None:
        application_exit_close_requested(self)
