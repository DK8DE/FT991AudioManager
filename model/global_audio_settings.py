"""Globale Soundeinstellungen (Geräte, Windows-Lautstärke, Mithören)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .audio_player_settings import AudioPlayerSettings, DEFAULT_VOLUME_PERCENT
from .audio_recorder_settings import AudioRecorderSettings

ROLE_INPUT = "input"
ROLE_SEND = "send"
ROLE_PC = "pc"
AUDIO_ROLES = (ROLE_INPUT, ROLE_SEND, ROLE_PC)


@dataclass
class GlobalAudioSettings:
    input_device_id: str = ""
    send_output_device_id: str = ""
    pc_output_device_id: str = ""
    input_volume_percent: int = DEFAULT_VOLUME_PERCENT
    send_volume_percent: int = DEFAULT_VOLUME_PERCENT
    pc_volume_percent: int = DEFAULT_VOLUME_PERCENT
    input_muted: bool = False
    send_muted: bool = False
    pc_muted: bool = False
    tx_monitor_to_pc_enabled: bool = False
    window_geometry: str = ""

    def device_id_for(self, role: str) -> str:
        if role == ROLE_INPUT:
            return self.input_device_id
        if role == ROLE_SEND:
            return self.send_output_device_id
        if role == ROLE_PC:
            return self.pc_output_device_id
        raise ValueError(f"Unbekannte Audio-Rolle: {role!r}")

    def set_device_id_for(self, role: str, device_id: str) -> None:
        dev = str(device_id or "")
        if role == ROLE_INPUT:
            self.input_device_id = dev
        elif role == ROLE_SEND:
            self.send_output_device_id = dev
        elif role == ROLE_PC:
            self.pc_output_device_id = dev
        else:
            raise ValueError(f"Unbekannte Audio-Rolle: {role!r}")

    def volume_percent_for(self, role: str) -> int:
        if role == ROLE_INPUT:
            return int(self.input_volume_percent)
        if role == ROLE_SEND:
            return int(self.send_volume_percent)
        if role == ROLE_PC:
            return int(self.pc_volume_percent)
        raise ValueError(f"Unbekannte Audio-Rolle: {role!r}")

    def set_volume_percent_for(self, role: str, percent: int) -> None:
        v = _clamp_volume(percent)
        if role == ROLE_INPUT:
            self.input_volume_percent = v
        elif role == ROLE_SEND:
            self.send_volume_percent = v
        elif role == ROLE_PC:
            self.pc_volume_percent = v
        else:
            raise ValueError(f"Unbekannte Audio-Rolle: {role!r}")

    def muted_for(self, role: str) -> bool:
        if role == ROLE_INPUT:
            return bool(self.input_muted)
        if role == ROLE_SEND:
            return bool(self.send_muted)
        if role == ROLE_PC:
            return bool(self.pc_muted)
        raise ValueError(f"Unbekannte Audio-Rolle: {role!r}")

    def set_muted_for(self, role: str, muted: bool) -> None:
        m = bool(muted)
        if role == ROLE_INPUT:
            self.input_muted = m
        elif role == ROLE_SEND:
            self.send_muted = m
        elif role == ROLE_PC:
            self.pc_muted = m
        else:
            raise ValueError(f"Unbekannte Audio-Rolle: {role!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_device_id": self.input_device_id,
            "send_output_device_id": self.send_output_device_id,
            "pc_output_device_id": self.pc_output_device_id,
            "input_volume_percent": int(self.input_volume_percent),
            "send_volume_percent": int(self.send_volume_percent),
            "pc_volume_percent": int(self.pc_volume_percent),
            "input_muted": bool(self.input_muted),
            "send_muted": bool(self.send_muted),
            "pc_muted": bool(self.pc_muted),
            "tx_monitor_to_pc_enabled": bool(self.tx_monitor_to_pc_enabled),
            "window_geometry": self.window_geometry,
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "GlobalAudioSettings":
        r = raw or {}
        return cls(
            input_device_id=str(r.get("input_device_id", "") or ""),
            send_output_device_id=str(r.get("send_output_device_id", "") or ""),
            pc_output_device_id=str(r.get("pc_output_device_id", "") or ""),
            input_volume_percent=_clamp_volume(r.get("input_volume_percent")),
            send_volume_percent=_clamp_volume(r.get("send_volume_percent")),
            pc_volume_percent=_clamp_volume(r.get("pc_volume_percent")),
            input_muted=bool(r.get("input_muted", False)),
            send_muted=bool(r.get("send_muted", False)),
            pc_muted=bool(r.get("pc_muted", False)),
            tx_monitor_to_pc_enabled=bool(r.get("tx_monitor_to_pc_enabled", False)),
            window_geometry=str(r.get("window_geometry", "") or ""),
        )

    @classmethod
    def migrate_from_legacy(
        cls,
        player: AudioPlayerSettings,
        recorder: AudioRecorderSettings,
    ) -> "GlobalAudioSettings":
        """Erzeugt globale Werte aus älteren Player/Recorder-Sektionen."""
        send_dev = player.output_device_id or recorder.output_device_id
        send_vol = (
            player.volume_percent
            if player.output_device_id
            else recorder.output_volume_percent
        )
        if player.output_device_id and recorder.output_device_id:
            send_vol = player.volume_percent

        pc_dev = player.pc_output_device_id or recorder.pc_output_device_id
        pc_vol = (
            player.pc_output_volume_percent
            if player.pc_output_device_id
            else recorder.pc_output_volume_percent
        )
        if player.pc_output_device_id and recorder.pc_output_device_id:
            pc_vol = player.pc_output_volume_percent

        tx_mon = bool(player.tx_monitor_to_pc_enabled) or bool(
            recorder.tx_monitor_to_pc_enabled
        )

        return cls(
            input_device_id=recorder.input_device_id,
            send_output_device_id=send_dev,
            pc_output_device_id=pc_dev,
            input_volume_percent=recorder.input_volume_percent,
            send_volume_percent=send_vol,
            pc_volume_percent=pc_vol,
            tx_monitor_to_pc_enabled=tx_mon,
        )


def _clamp_volume(value: object) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = DEFAULT_VOLUME_PERCENT
    return max(0, min(100, v))


def sync_global_to_legacy(
    global_audio: GlobalAudioSettings,
    player: AudioPlayerSettings,
    recorder: AudioRecorderSettings,
) -> None:
    """Spiegelt globale Werte in die bisherigen Player/Recorder-Felder."""
    g = global_audio
    player.output_device_id = g.send_output_device_id
    player.volume_percent = g.send_volume_percent
    player.pc_output_device_id = g.pc_output_device_id
    player.pc_output_volume_percent = g.pc_volume_percent
    player.tx_monitor_to_pc_enabled = g.tx_monitor_to_pc_enabled

    recorder.input_device_id = g.input_device_id
    recorder.output_device_id = g.send_output_device_id
    recorder.pc_output_device_id = g.pc_output_device_id
    recorder.input_volume_percent = g.input_volume_percent
    recorder.output_volume_percent = g.send_volume_percent
    recorder.pc_output_volume_percent = g.pc_volume_percent
    recorder.tx_monitor_to_pc_enabled = g.tx_monitor_to_pc_enabled
