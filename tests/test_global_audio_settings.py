"""Tests für globale Soundeinstellungen und Legacy-Migration."""

from __future__ import annotations

from model.app_settings import AppSettings
from model.audio_player_settings import AudioPlayerSettings
from model.audio_recorder_settings import AudioRecorderSettings
from model.global_audio_settings import (
    GlobalAudioSettings,
    ROLE_INPUT,
    ROLE_PC,
    ROLE_SEND,
    sync_global_to_legacy,
)


def test_migrate_from_legacy_prefers_player_send_and_pc() -> None:
    player = AudioPlayerSettings(
        output_device_id="send-player",
        volume_percent=42,
        pc_output_device_id="pc-player",
        pc_output_volume_percent=17,
        tx_monitor_to_pc_enabled=True,
    )
    recorder = AudioRecorderSettings(
        input_device_id="mic-1",
        input_volume_percent=88,
        output_device_id="send-rec",
        output_volume_percent=55,
        pc_output_device_id="pc-rec",
        pc_output_volume_percent=33,
        tx_monitor_to_pc_enabled=False,
    )
    g = GlobalAudioSettings.migrate_from_legacy(player, recorder)
    assert g.input_device_id == "mic-1"
    assert g.input_volume_percent == 88
    assert g.send_output_device_id == "send-player"
    assert g.send_volume_percent == 42
    assert g.pc_output_device_id == "pc-player"
    assert g.pc_volume_percent == 17
    assert g.tx_monitor_to_pc_enabled is True


def test_sync_global_to_legacy_mirrors_all_fields() -> None:
    g = GlobalAudioSettings(
        input_device_id="in",
        send_output_device_id="send",
        pc_output_device_id="pc",
        input_volume_percent=10,
        send_volume_percent=20,
        pc_volume_percent=30,
        input_muted=True,
        send_muted=False,
        pc_muted=True,
        tx_monitor_to_pc_enabled=True,
    )
    player = AudioPlayerSettings()
    recorder = AudioRecorderSettings()
    sync_global_to_legacy(g, player, recorder)
    assert recorder.input_device_id == "in"
    assert player.output_device_id == "send"
    assert recorder.output_device_id == "send"
    assert player.volume_percent == 20
    assert recorder.output_volume_percent == 20
    assert player.pc_output_device_id == "pc"
    assert recorder.pc_output_volume_percent == 30
    assert player.tx_monitor_to_pc_enabled is True
    assert recorder.tx_monitor_to_pc_enabled is True


def test_app_settings_default_has_global_audio() -> None:
    settings = AppSettings()
    assert isinstance(settings.global_audio, GlobalAudioSettings)
    assert settings.global_audio.device_id_for(ROLE_INPUT) == ""
    assert settings.global_audio.device_id_for(ROLE_SEND) == ""
    assert settings.global_audio.device_id_for(ROLE_PC) == ""
