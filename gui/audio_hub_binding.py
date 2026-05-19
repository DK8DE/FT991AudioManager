"""AudioSettingsHub mit Player-/Recorder-Fenstern verbinden."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtWidgets import QCheckBox, QComboBox

from model.global_audio_settings import ROLE_INPUT, ROLE_PC, ROLE_SEND

if TYPE_CHECKING:
    from audio.audio_settings_hub import AudioSettingsHub
    from gui.volume_control_row import VolumeControlRow


def connect_level_meters(
    hub: "AudioSettingsHub",
    rows_by_role: dict[str, "VolumeControlRow"],
) -> None:
    """Pegelanzeigen an den Hub-Monitor koppeln."""

    def on_level(role: str, level: float) -> None:
        row = rows_by_role.get(role)
        if row is not None:
            row.set_peak_level(level)

    hub.level_monitor.level_changed.connect(on_level)


def _select_combo_device(combo: QComboBox, device_id: str) -> None:
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


def connect_player_hub(
    *,
    hub: "AudioSettingsHub",
    combo_send: QComboBox,
    combo_pc: QComboBox,
    vol_send: "VolumeControlRow",
    vol_pc: "VolumeControlRow",
    check_tx_monitor: QCheckBox,
    on_send_device: Callable[[str], None],
    on_pc_device: Callable[[str], None],
    on_send_volume: Callable[[int], None],
    on_pc_volume: Callable[[int], None],
    on_send_mute: Callable[[bool], None],
    on_pc_mute: Callable[[bool], None],
    on_tx_monitor: Callable[[bool], None],
) -> None:
    def device_changed(role: str, device_id: str) -> None:
        if role == ROLE_SEND:
            _select_combo_device(combo_send, device_id)
            on_send_device(device_id)
        elif role == ROLE_PC:
            _select_combo_device(combo_pc, device_id)
            on_pc_device(device_id)

    def volume_changed(role: str, percent: int) -> None:
        if role == ROLE_SEND:
            vol_send.set_value(percent)
            on_send_volume(percent)
        elif role == ROLE_PC:
            vol_pc.set_value(percent)
            on_pc_volume(percent)

    def mute_changed(role: str, muted: bool) -> None:
        if role == ROLE_SEND:
            vol_send.set_muted(muted)
            on_send_mute(muted)
        elif role == ROLE_PC:
            vol_pc.set_muted(muted)
            on_pc_mute(muted)

    def tx_monitor_changed(enabled: bool) -> None:
        check_tx_monitor.blockSignals(True)
        try:
            check_tx_monitor.setChecked(bool(enabled))
        finally:
            check_tx_monitor.blockSignals(False)
        on_tx_monitor(bool(enabled))

    hub.device_changed.connect(device_changed)
    hub.volume_changed.connect(volume_changed)
    hub.mute_changed.connect(mute_changed)
    hub.tx_monitor_changed.connect(tx_monitor_changed)


def connect_recorder_hub(
    *,
    hub: "AudioSettingsHub",
    combo_input: QComboBox,
    combo_send: QComboBox,
    combo_pc: QComboBox,
    vol_input: "VolumeControlRow",
    vol_send: "VolumeControlRow",
    vol_pc: "VolumeControlRow",
    check_tx_monitor: QCheckBox,
    on_input_device: Callable[[str], None],
    on_send_device: Callable[[str], None],
    on_pc_device: Callable[[str], None],
    on_input_volume: Callable[[int], None],
    on_send_volume: Callable[[int], None],
    on_pc_volume: Callable[[int], None],
    on_input_mute: Callable[[bool], None],
    on_send_mute: Callable[[bool], None],
    on_pc_mute: Callable[[bool], None],
    on_tx_monitor: Callable[[bool], None],
) -> None:
    def device_changed(role: str, device_id: str) -> None:
        if role == ROLE_INPUT:
            _select_combo_device(combo_input, device_id)
            on_input_device(device_id)
        elif role == ROLE_SEND:
            _select_combo_device(combo_send, device_id)
            on_send_device(device_id)
        elif role == ROLE_PC:
            _select_combo_device(combo_pc, device_id)
            on_pc_device(device_id)

    def volume_changed(role: str, percent: int) -> None:
        if role == ROLE_INPUT:
            vol_input.set_value(percent)
            on_input_volume(percent)
        elif role == ROLE_SEND:
            vol_send.set_value(percent)
            on_send_volume(percent)
        elif role == ROLE_PC:
            vol_pc.set_value(percent)
            on_pc_volume(percent)

    def mute_changed(role: str, muted: bool) -> None:
        if role == ROLE_INPUT:
            vol_input.set_muted(muted)
            on_input_mute(muted)
        elif role == ROLE_SEND:
            vol_send.set_muted(muted)
            on_send_mute(muted)
        elif role == ROLE_PC:
            vol_pc.set_muted(muted)
            on_pc_mute(muted)

    def tx_monitor_changed(enabled: bool) -> None:
        check_tx_monitor.blockSignals(True)
        try:
            check_tx_monitor.setChecked(bool(enabled))
        finally:
            check_tx_monitor.blockSignals(False)
        on_tx_monitor(bool(enabled))

    hub.device_changed.connect(device_changed)
    hub.volume_changed.connect(volume_changed)
    hub.mute_changed.connect(mute_changed)
    hub.tx_monitor_changed.connect(tx_monitor_changed)


def load_global_audio_into_combos(
    hub: "AudioSettingsHub",
    *,
    combo_input: Optional[QComboBox] = None,
    combo_send: Optional[QComboBox] = None,
    combo_pc: Optional[QComboBox] = None,
    vol_input: Optional["VolumeControlRow"] = None,
    vol_send: Optional["VolumeControlRow"] = None,
    vol_pc: Optional["VolumeControlRow"] = None,
    check_tx_monitor: Optional[QCheckBox] = None,
) -> None:
    g = hub.global_audio
    if combo_input is not None:
        _select_combo_device(combo_input, g.input_device_id)
    if combo_send is not None:
        _select_combo_device(combo_send, g.send_output_device_id)
    if combo_pc is not None:
        _select_combo_device(combo_pc, g.pc_output_device_id)
    if vol_input is not None:
        vol_input.set_value(g.input_volume_percent)
        vol_input.set_muted(g.input_muted)
    if vol_send is not None:
        vol_send.set_value(g.send_volume_percent)
        vol_send.set_muted(g.send_muted)
    if vol_pc is not None:
        vol_pc.set_value(g.pc_volume_percent)
        vol_pc.set_muted(g.pc_muted)
    if check_tx_monitor is not None:
        check_tx_monitor.blockSignals(True)
        try:
            check_tx_monitor.setChecked(g.tx_monitor_to_pc_enabled)
        finally:
            check_tx_monitor.blockSignals(False)
