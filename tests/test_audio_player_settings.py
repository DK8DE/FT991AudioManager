"""Tests für Audio-Player-Einstellungen."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model import AppSettings
from model.audio_player_settings import (
    AudioPlayerSettings,
    encode_pause_token_seconds,
    merge_playlist_order,
    scan_audio_files,
)


class AudioPlayerSettingsTest(unittest.TestCase):
    def test_merge_playlist_order(self) -> None:
        merged = merge_playlist_order(
            ["b.mp3", "a.wav", "gone.mp3"],
            ["a.wav", "c.mp3", "b.mp3"],
        )
        self.assertEqual(merged, ["b.mp3", "a.wav", "c.mp3"])

    def test_merge_playlist_order_keeps_pause_tokens(self) -> None:
        p1 = encode_pause_token_seconds(3)
        p2 = encode_pause_token_seconds(5)
        merged = merge_playlist_order(
            ["b.mp3", p1, "gone.mp3", p2],
            ["a.wav", "c.mp3", "b.mp3"],
        )
        self.assertEqual(merged, ["b.mp3", p1, p2, "a.wav", "c.mp3"])

    def test_scan_audio_files_flat_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.mp3").write_bytes(b"x")
            (root / "two.WAV").write_bytes(b"x")
            (root / "sub").mkdir()
            (root / "sub" / "three.mp3").write_bytes(b"x")
            (root / "readme.txt").write_text("nope")
            names = scan_audio_files(root)
            self.assertEqual(sorted(names), ["one.mp3", "two.WAV"])

    def test_app_settings_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = AppSettings()
            s.audio_player.folder_path = "C:/audio"
            s.audio_player.playlist_order = ["a.mp3", encode_pause_token_seconds(2)]
            s.save(path)
            loaded = AppSettings.load(path)
            self.assertEqual(loaded.audio_player.folder_path, "C:/audio")
            self.assertEqual(
                loaded.audio_player.playlist_order,
                ["a.mp3", encode_pause_token_seconds(2)],
            )

    def test_from_dict_defaults(self) -> None:
        cfg = AudioPlayerSettings.from_dict({})
        self.assertEqual(cfg.playback_mode, "single")
        self.assertEqual(cfg.volume_percent, 100)
        self.assertEqual(cfg.playlist_order, [])
        self.assertTrue(cfg.warn_transmission_end_enabled)

    def test_warn_transmission_end_roundtrip(self) -> None:
        cfg = AudioPlayerSettings()
        self.assertTrue(cfg.warn_transmission_end_enabled)
        d = cfg.to_dict()
        self.assertTrue(d["warn_transmission_end_enabled"])
        off = AudioPlayerSettings.from_dict({"warn_transmission_end_enabled": False})
        self.assertFalse(off.warn_transmission_end_enabled)

    def test_volume_clamped(self) -> None:
        cfg = AudioPlayerSettings.from_dict({"volume_percent": 150})
        self.assertEqual(cfg.volume_percent, 100)
        cfg2 = AudioPlayerSettings.from_dict({"volume_percent": -5})
        self.assertEqual(cfg2.volume_percent, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
