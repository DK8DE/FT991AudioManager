"""Tests für T.CALL-Rufton-Pfad."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

from audio.t_call_controller import resolve_t_call_wav_path


def test_resolve_t_call_wav_path_finds_bundled_file() -> None:
    path = resolve_t_call_wav_path()
    assert path is not None
    assert path.is_file()
    assert path.name.lower() in ("1750.wav", "1750.waf")
    assert path.parent.name == "audio"


def test_resolve_t_call_wav_path_frozen_uses_resource_dir() -> None:
    audio_dir = Path(__file__).resolve().parent.parent / "audio"
    fake_meipass = audio_dir.parent / "_fake_meipass"
    fake_bundle = fake_meipass / "audio"
    fake_bundle.mkdir(parents=True, exist_ok=True)
    wav = fake_bundle / "1750.wav"
    try:
        wav.write_bytes(b"RIFF")
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "_MEIPASS", str(fake_meipass), create=True),
        ):
            path = resolve_t_call_wav_path()
        assert path is not None
        assert path.resolve() == wav.resolve()
    finally:
        if wav.is_file():
            wav.unlink()
        if fake_bundle.is_dir():
            fake_bundle.rmdir()
        if fake_meipass.is_dir():
            fake_meipass.rmdir()
