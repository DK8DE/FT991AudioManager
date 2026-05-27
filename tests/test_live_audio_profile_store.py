"""Tests für Live-Audioprofile."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from model.live_audio_profile import LiveAudioProfile
from model.live_audio_profile_store import (
    DEFAULT_LIVE_AUDIO_PROFILE_NAME,
    LiveAudioProfileStore,
)
from model.live_settings import LiveGateSettings, LiveSettings


def test_first_start_creates_default_profile() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "live_audio_profiles.json"
        store = LiveAudioProfileStore.load(path)
        assert path.exists()
        assert store.names() == [DEFAULT_LIVE_AUDIO_PROFILE_NAME]


def test_save_and_reload_profile() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "live_audio_profiles.json"
        store = LiveAudioProfileStore.load(path)
        liv = LiveSettings()
        liv.gate = LiveGateSettings(enabled=True, threshold_db=-40.0)
        liv.eq_enabled = False
        liv.input_gain = 1.25
        liv.funk_listen_gain = 0.5
        store.upsert(LiveAudioProfile.from_live_settings(liv, "Contest"))
        reloaded = LiveAudioProfileStore.load(path)
        profile = reloaded.find("Contest")
        assert profile is not None
        assert profile.eq_enabled is False
        assert abs(profile.gate.threshold_db + 40.0) < 0.01
        assert abs(profile.input_gain - 1.25) < 0.01
        assert abs(profile.funk_listen_gain - 0.5) < 0.01


def test_remove_profile() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "live_audio_profiles.json"
        store = LiveAudioProfileStore.load(path)
        store.upsert(LiveAudioProfile.from_live_settings(LiveSettings(), "Extra"))
        assert store.remove("Extra") is True
        assert store.find("Extra") is None
