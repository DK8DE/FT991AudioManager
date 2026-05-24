"""Read-only Gerätenamen für Player/Recorder/Live (Soundeinstellungen)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy

from audio.audio_recorder import list_audio_input_devices
from audio.player_controller import list_audio_output_devices
from i18n import tr


def hub_device_label(device_id: str, *, input_device: bool) -> str:
    did = str(device_id or "").strip()
    if not did:
        return tr("common.not_selected")
    listing = list_audio_input_devices() if input_device else list_audio_output_devices()
    for dev_id, lbl in listing:
        if str(dev_id) == did:
            return lbl
    return tr("common.device_id", did=did)


def mk_routing_caption(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#9a9a9a;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def mk_routing_value() -> QLabel:
    lbl = QLabel(tr("common.dash"))
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    lbl.setStyleSheet("color:#e8e8e8;")
    return lbl


def set_routing_device_label(
    lbl: QLabel,
    device_id: str,
    *,
    input_device: bool,
) -> None:
    lbl.setText(hub_device_label(device_id, input_device=input_device))
