"""Globales Fenster: Soundeinstellungen (Funktionen → Soundeinstellung)."""

from __future__ import annotations

import base64
import sys
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QShowEvent
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
from live.live_devices import (
    list_input_devices,
    list_output_devices,
    remap_live_device_id,
)
from model.global_audio_settings import ROLE_INPUT, ROLE_PC, ROLE_SEND, sync_global_to_legacy

from i18n import tr
from i18n.retranslatable import RetranslatableMixin

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


class SoundSettingsWindow(RetranslatableMixin, QMainWindow):
    """Zentrale Auswahl von Aufnahme-, Sende- und PC-Soundkarte inkl. Windows-Lautstärke."""

    closed = Signal()
    live_devices_changed = Signal()

    def __init__(
        self,
        settings: "AppSettings",
        audio_hub: AudioSettingsHub,
        parent: Optional[QWidget] = None,
    ) -> None:
        del parent
        super().__init__(None)
        self._settings = settings
        self._hub = audio_hub

        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # Kein Parent — frei beweglich, MainWindow kann darüber liegen (s. LogWindow).
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(520, 420)

        self._form_labels: dict[str, QLabel] = {}

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
        self._load_live_devices_from_settings()
        self._restore_geometry()
        self.retranslate_ui()
        self._register_retranslate()

    def _sync_status_hint(self) -> str:
        if windows_endpoint_volume_available():
            return tr("sound.hint.sync_active")
        return tr("sound.hint.sync_inactive", python=sys.executable)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("sound.window.title"))
        self._hint.setText(
            tr("sound.hint", sync_status=self._sync_status_hint())
        )
        self._devices_box.setTitle(tr("sound.group.devices"))
        self._live_box.setTitle(tr("sound.group.live"))
        self._live_hint.setText(tr("sound.live.hint"))
        self._form_labels["input_device"].setText(tr("sound.label.input_device"))
        self._form_labels["input_volume"].setText(tr("common.volume_input"))
        self._form_labels["send_output"].setText(tr("sound.label.send_output"))
        self._form_labels["send_volume"].setText(tr("sound.label.send_volume"))
        self._form_labels["pc_output"].setText(tr("sound.label.pc_output"))
        self._form_labels["pc_volume"].setText(tr("sound.label.pc_volume"))
        self._form_labels["live_mic"].setText(tr("sound.label.live_mic"))
        self._form_labels["live_monitor"].setText(tr("sound.label.live_monitor"))
        self._form_labels["live_funk_in"].setText(tr("sound.label.live_funk_in"))
        self._form_labels["live_funk_out"].setText(tr("sound.label.live_funk_out"))
        self._combo_send.setToolTip(tr("sound.combo.send.tooltip"))
        self._combo_pc.setToolTip(tr("sound.combo.pc.tooltip"))
        self._combo_live_funk.setToolTip(tr("sound.combo.funk_in.tooltip"))
        self._combo_live_funk_listen.setToolTip(tr("sound.combo.funk_out.tooltip"))
        self._check_tx_monitor.setText(tr("common.tx_monitor"))
        self._check_tx_monitor.setToolTip(tr("common.tx_monitor_tooltip_sound"))
        self._vol_input._help_tooltip = tr("common.volume_input_tooltip")
        self._vol_input._apply_tooltips()
        self._vol_send._help_tooltip = tr("sound.send_volume.tooltip")
        self._vol_send._apply_tooltips()
        self._vol_pc._help_tooltip = tr("sound.pc_volume.tooltip")
        self._vol_pc._apply_tooltips()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        self._devices_box = QGroupBox()
        lay = QVBoxLayout(self._devices_box)
        label_w = 170

        def form_label(key: str, text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setMinimumWidth(label_w)
            self._form_labels[key] = lbl
            return lbl

        in_row = QHBoxLayout()
        in_row.addWidget(form_label("input_device", ""))
        self._combo_input = QComboBox()
        self._fill_input_devices()
        self._combo_input.currentIndexChanged.connect(self._on_input_device)
        in_row.addWidget(self._combo_input, 1)
        lay.addLayout(in_row)

        self._vol_input = VolumeControlRow(
            tooltip="",
            leading_icon=volume_role_record_icon(),
        )
        self._vol_input.value_changed.connect(
            lambda v: self._hub.set_volume_percent(ROLE_INPUT, v)
        )
        self._vol_input.mute_toggled.connect(
            lambda m: self._hub.set_muted(ROLE_INPUT, m)
        )
        vol_in = QHBoxLayout()
        vol_in.addWidget(form_label("input_volume", ""))
        vol_in.addWidget(self._vol_input, 1)
        lay.addLayout(vol_in)

        send_row = QHBoxLayout()
        send_row.addWidget(form_label("send_output", ""))
        self._combo_send = QComboBox()
        self._fill_output_devices(self._combo_send)
        self._combo_send.currentIndexChanged.connect(self._on_send_device)
        send_row.addWidget(self._combo_send, 1)
        lay.addLayout(send_row)

        self._vol_send = VolumeControlRow(
            tooltip="",
            leading_icon=volume_role_send_icon(),
        )
        self._vol_send.value_changed.connect(
            lambda v: self._hub.set_volume_percent(ROLE_SEND, v)
        )
        self._vol_send.mute_toggled.connect(
            lambda m: self._hub.set_muted(ROLE_SEND, m)
        )
        vol_send = QHBoxLayout()
        vol_send.addWidget(form_label("send_volume", ""))
        vol_send.addWidget(self._vol_send, 1)
        lay.addLayout(vol_send)

        pc_row = QHBoxLayout()
        pc_row.addWidget(form_label("pc_output", ""))
        self._combo_pc = QComboBox()
        self._fill_output_devices(self._combo_pc)
        self._combo_pc.currentIndexChanged.connect(self._on_pc_device)
        pc_row.addWidget(self._combo_pc, 1)
        lay.addLayout(pc_row)

        self._vol_pc = VolumeControlRow(
            tooltip="",
            leading_icon=volume_role_pc_icon(),
        )
        self._vol_pc.value_changed.connect(
            lambda v: self._hub.set_volume_percent(ROLE_PC, v)
        )
        self._vol_pc.mute_toggled.connect(lambda m: self._hub.set_muted(ROLE_PC, m))
        vol_pc = QHBoxLayout()
        vol_pc.addWidget(form_label("pc_volume", ""))
        vol_pc.addWidget(self._vol_pc, 1)
        lay.addLayout(vol_pc)

        self._check_tx_monitor = QCheckBox()
        self._check_tx_monitor.toggled.connect(self._on_tx_monitor)
        lay.addWidget(self._check_tx_monitor)

        root.addWidget(self._devices_box)

        self._live_box = QGroupBox()
        liv_lay = QVBoxLayout(self._live_box)
        self._live_hint = QLabel()
        self._live_hint.setWordWrap(True)
        liv_lay.addWidget(self._live_hint)

        rin = QHBoxLayout()
        rin.addWidget(form_label("live_mic", ""))
        self._combo_live_in = QComboBox()
        self._combo_live_in.currentIndexChanged.connect(self._on_live_in_dev)
        rin.addWidget(self._combo_live_in, 1)
        liv_lay.addLayout(rin)

        rout = QHBoxLayout()
        rout.addWidget(form_label("live_monitor", ""))
        self._combo_live_out = QComboBox()
        self._combo_live_out.currentIndexChanged.connect(self._on_live_out_dev)
        rout.addWidget(self._combo_live_out, 1)
        liv_lay.addLayout(rout)

        rfunk = QHBoxLayout()
        rfunk.addWidget(form_label("live_funk_in", ""))
        self._combo_live_funk = QComboBox()
        self._combo_live_funk.currentIndexChanged.connect(self._on_live_funk_dev)
        rfunk.addWidget(self._combo_live_funk, 1)
        liv_lay.addLayout(rfunk)

        r_listen = QHBoxLayout()
        r_listen.addWidget(form_label("live_funk_out", ""))
        self._combo_live_funk_listen = QComboBox()
        self._combo_live_funk_listen.currentIndexChanged.connect(
            self._on_live_funk_listen_dev
        )
        r_listen.addWidget(self._combo_live_funk_listen, 1)
        liv_lay.addLayout(r_listen)

        root.addWidget(self._live_box)

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
        from audio.qt_device_resolve import remap_global_audio_devices

        if remap_global_audio_devices(self._hub.global_audio):
            sync_global_to_legacy(
                self._hub.global_audio,
                self._settings.audio_player,
                self._settings.audio_recorder,
            )
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
        self._hub.set_device_id(
            ROLE_INPUT, dev_id, label=self._combo_input.currentText()
        )

    def _on_send_device(self) -> None:
        dev_id = self._combo_send.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._hub.set_device_id(
            ROLE_SEND, dev_id, label=self._combo_send.currentText()
        )

    def _on_pc_device(self) -> None:
        dev_id = self._combo_pc.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._hub.set_device_id(
            ROLE_PC, dev_id, label=self._combo_pc.currentText()
        )

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

    def _load_live_devices_from_settings(self) -> None:
        """PortAudio-/Live-Geräte aus ``settings.live``."""
        self._combo_live_in.blockSignals(True)
        self._combo_live_out.blockSignals(True)
        self._combo_live_funk.blockSignals(True)
        self._combo_live_funk_listen.blockSignals(True)
        try:
            self._combo_live_in.clear()
            self._combo_live_out.clear()
            self._combo_live_funk.clear()
            self._combo_live_funk_listen.clear()
            tip_role = Qt.ItemDataRole.ToolTipRole
            for did, label, tip in list_input_devices():
                self._combo_live_in.addItem(label, did)
                if tip:
                    self._combo_live_in.setItemData(
                        self._combo_live_in.count() - 1, tip, tip_role
                    )
                self._combo_live_funk_listen.addItem(label, did)
                if tip:
                    self._combo_live_funk_listen.setItemData(
                        self._combo_live_funk_listen.count() - 1, tip, tip_role
                    )
            for did, label, tip in list_output_devices():
                for cb in (self._combo_live_out, self._combo_live_funk):
                    cb.addItem(label, did)
                    if tip:
                        cb.setItemData(cb.count() - 1, tip, tip_role)

            self._settings.live.clamp_recursive()
            rin = (
                remap_live_device_id(
                    str(self._settings.live.input_device_id),
                    input_device=True,
                )
                or str(self._settings.live.input_device_id or "")
            )
            rout = (
                remap_live_device_id(
                    str(self._settings.live.output_device_id),
                    input_device=False,
                )
                or str(self._settings.live.output_device_id or "")
            )
            rfunk = (
                remap_live_device_id(
                    str(self._settings.live.funk_output_device_id),
                    input_device=False,
                )
                or str(self._settings.live.funk_output_device_id or "")
            )
            rflisten = (
                remap_live_device_id(
                    str(self._settings.live.funk_listen_input_device_id),
                    input_device=True,
                )
                or str(self._settings.live.funk_listen_input_device_id or "")
            )
            if rin != self._settings.live.input_device_id:
                self._settings.live.input_device_id = rin
            if rout != self._settings.live.output_device_id:
                self._settings.live.output_device_id = rout
            if rfunk != self._settings.live.funk_output_device_id:
                self._settings.live.funk_output_device_id = rfunk
            if rflisten != self._settings.live.funk_listen_input_device_id:
                self._settings.live.funk_listen_input_device_id = rflisten

            self._select_combo_device(self._combo_live_in, self._settings.live.input_device_id)
            self._select_combo_device(self._combo_live_out, self._settings.live.output_device_id)
            self._select_combo_device(self._combo_live_funk, self._settings.live.funk_output_device_id)
            self._select_combo_device(
                self._combo_live_funk_listen,
                self._settings.live.funk_listen_input_device_id,
            )
            self._settings.live.funk_listen_enabled = True
        finally:
            self._combo_live_in.blockSignals(False)
            self._combo_live_out.blockSignals(False)
            self._combo_live_funk.blockSignals(False)
            self._combo_live_funk_listen.blockSignals(False)

    def _on_live_in_dev(self, *_idx: int) -> None:
        rid = self._combo_live_in.currentData()
        self._settings.live.input_device_id = str(rid) if isinstance(rid, str) else ""
        self._emit_live_devices_changed()

    def _on_live_out_dev(self, *_idx: int) -> None:
        rid = self._combo_live_out.currentData()
        self._settings.live.output_device_id = str(rid) if isinstance(rid, str) else ""
        self._emit_live_devices_changed()

    def _on_live_funk_dev(self, *_idx: int) -> None:
        rid = self._combo_live_funk.currentData()
        self._settings.live.funk_output_device_id = str(rid) if isinstance(rid, str) else ""
        self._emit_live_devices_changed()

    def _on_live_funk_listen_dev(self, *_idx: int) -> None:
        rid = self._combo_live_funk_listen.currentData()
        self._settings.live.funk_listen_input_device_id = (
            str(rid) if isinstance(rid, str) else ""
        )
        self._settings.live.funk_listen_enabled = True
        self._emit_live_devices_changed()

    def _emit_live_devices_changed(self) -> None:
        self.live_devices_changed.emit()

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

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        from audio.windows_endpoint_volume import invalidate_windows_audio_device_cache

        invalidate_windows_audio_device_cache()
        self._load_live_devices_from_settings()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_geometry()
        self.closed.emit()
        super().closeEvent(event)

    def force_close(self) -> None:
        application_exit_close_requested(self)
