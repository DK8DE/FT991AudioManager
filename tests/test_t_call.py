"""Tests für T.CALL-Rufton-Pfad."""

from __future__ import annotations

from pathlib import Path

from audio.t_call_controller import resolve_t_call_wav_path


def test_resolve_t_call_wav_path_finds_bundled_file() -> None:
    path = resolve_t_call_wav_path()
    assert path is not None
    assert path.is_file()
    assert path.name.lower() in ("1750.wav", "1750.waf")
    assert path.parent == Path(__file__).resolve().parent.parent / "audio"
