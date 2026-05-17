"""Tests für die Audio-Recorder-Einstellungen + Filename-Helper."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from model import AppSettings
from model.audio_recorder_settings import (
    ALLOWED_BITRATES_KBPS,
    DEFAULT_BITRATE_KBPS,
    DEFAULT_VOLUME_PERCENT,
    AudioRecorderSettings,
    build_recording_filename,
    default_recordings_folder,
    scan_recordings,
)


class AudioRecorderSettingsTest(unittest.TestCase):
    def test_default_construction(self) -> None:
        s = AudioRecorderSettings()
        self.assertEqual(s.mp3_bitrate_kbps, DEFAULT_BITRATE_KBPS)
        self.assertEqual(s.folder_path, "")
        self.assertEqual(s.selected_filename, "")
        self.assertEqual(s.input_volume_percent, DEFAULT_VOLUME_PERCENT)
        self.assertEqual(s.output_volume_percent, DEFAULT_VOLUME_PERCENT)

    def test_from_dict_volume_defaults_when_missing(self) -> None:
        s = AudioRecorderSettings.from_dict({})
        self.assertEqual(s.input_volume_percent, DEFAULT_VOLUME_PERCENT)
        self.assertEqual(s.output_volume_percent, DEFAULT_VOLUME_PERCENT)
        self.assertEqual(s.pc_output_volume_percent, DEFAULT_VOLUME_PERCENT)

    def test_from_dict_clamps_volume_out_of_range(self) -> None:
        s = AudioRecorderSettings.from_dict(
            {
                "input_volume_percent": -10,
                "output_volume_percent": 250,
                "pc_output_volume_percent": 1000,
            }
        )
        self.assertEqual(s.input_volume_percent, 0)
        self.assertEqual(s.output_volume_percent, 100)
        self.assertEqual(s.pc_output_volume_percent, 100)

    def test_from_dict_volume_garbage_fallback(self) -> None:
        s = AudioRecorderSettings.from_dict(
            {
                "input_volume_percent": "loud",
                "output_volume_percent": None,
                "pc_output_volume_percent": "off",
            }
        )
        self.assertEqual(s.input_volume_percent, DEFAULT_VOLUME_PERCENT)
        self.assertEqual(s.output_volume_percent, DEFAULT_VOLUME_PERCENT)
        self.assertEqual(s.pc_output_volume_percent, DEFAULT_VOLUME_PERCENT)

    def test_from_dict_defaults_when_empty(self) -> None:
        s = AudioRecorderSettings.from_dict({})
        self.assertEqual(s.mp3_bitrate_kbps, DEFAULT_BITRATE_KBPS)

    def test_from_dict_ignores_legacy_pre_roll(self) -> None:
        """Alte settings.json mit ``pre_roll_ms`` darf den Recorder nicht crashen."""
        s = AudioRecorderSettings.from_dict({"pre_roll_ms": 1234})
        self.assertFalse(hasattr(s, "pre_roll_ms"))

    def test_from_dict_clamps_invalid_bitrate(self) -> None:
        s = AudioRecorderSettings.from_dict({"mp3_bitrate_kbps": 47})
        # 47 ist kein erlaubter Wert; Algorithmus rundet auf nächst-niedrigeren
        # erlaubten Wert oder auf den Default — also <= 64.
        self.assertIn(s.mp3_bitrate_kbps, ALLOWED_BITRATES_KBPS)
        self.assertLessEqual(s.mp3_bitrate_kbps, 64)

    def test_from_dict_accepts_allowed_bitrate(self) -> None:
        for kbps in ALLOWED_BITRATES_KBPS:
            s = AudioRecorderSettings.from_dict({"mp3_bitrate_kbps": kbps})
            self.assertEqual(s.mp3_bitrate_kbps, kbps)

    def test_from_dict_bitrate_garbage_fallback(self) -> None:
        s = AudioRecorderSettings.from_dict({"mp3_bitrate_kbps": "abc"})
        self.assertEqual(s.mp3_bitrate_kbps, DEFAULT_BITRATE_KBPS)

    def test_roundtrip_to_dict_from_dict(self) -> None:
        original = AudioRecorderSettings(
            folder_path="C:/rec",
            input_device_id="dev-in",
            output_device_id="dev-out",
            pc_output_device_id="dev-pc",
            window_geometry="abc",
            mp3_bitrate_kbps=192,
            selected_filename="Record_x.mp3",
            input_volume_percent=33,
            output_volume_percent=77,
            pc_output_volume_percent=42,
        )
        restored = AudioRecorderSettings.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_from_dict_pc_output_device_id_default_empty(self) -> None:
        s = AudioRecorderSettings.from_dict({})
        self.assertEqual(s.pc_output_device_id, "")

    def test_from_dict_pc_output_device_id_preserves_value(self) -> None:
        s = AudioRecorderSettings.from_dict({"pc_output_device_id": "audio-pc-1"})
        self.assertEqual(s.pc_output_device_id, "audio-pc-1")


class BuildRecordingFilenameTest(unittest.TestCase):
    def test_filename_format(self) -> None:
        ts = datetime(2026, 5, 17, 9, 28, 0)
        self.assertEqual(
            build_recording_filename(ts),
            "Record_2026_05_17_Stunde_09_28_00.mp3",
        )

    def test_filename_uses_zero_padding(self) -> None:
        ts = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(
            build_recording_filename(ts),
            "Record_2026_01_02_Stunde_03_04_05.mp3",
        )

    def test_filename_default_now(self) -> None:
        name = build_recording_filename()
        self.assertTrue(name.startswith("Record_"))
        self.assertTrue(name.endswith(".mp3"))


class ScanRecordingsTest(unittest.TestCase):
    def test_scan_filters_and_sorts_descending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Record_2026_05_17_Stunde_09_00_00.mp3").write_bytes(b"x")
            (root / "Record_2026_05_17_Stunde_10_00_00.mp3").write_bytes(b"x")
            (root / "Record_2026_05_16_Stunde_18_00_00.mp3").write_bytes(b"x")
            (root / "notes.txt").write_text("nope")
            (root / "ignored.wav").write_bytes(b"x")
            (root / "sub").mkdir()
            (root / "sub" / "sub.mp3").write_bytes(b"x")

            files = scan_recordings(root)
            self.assertEqual(
                files,
                [
                    "Record_2026_05_17_Stunde_10_00_00.mp3",
                    "Record_2026_05_17_Stunde_09_00_00.mp3",
                    "Record_2026_05_16_Stunde_18_00_00.mp3",
                ],
            )

    def test_scan_missing_folder(self) -> None:
        self.assertEqual(scan_recordings(Path("/does/not/exist/qwerty")), [])


class DefaultFolderTest(unittest.TestCase):
    def test_default_folder_ends_with_documents_subdir(self) -> None:
        folder = default_recordings_folder()
        self.assertEqual(folder.name, "FT991_Recordings")


class AppSettingsIntegrationTest(unittest.TestCase):
    def test_app_settings_roundtrip_includes_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = AppSettings()
            s.audio_recorder.folder_path = "C:/rec"
            s.audio_recorder.mp3_bitrate_kbps = 192
            s.audio_recorder.selected_filename = "Record_X.mp3"
            s.save(path)
            loaded = AppSettings.load(path)
            self.assertEqual(loaded.audio_recorder.folder_path, "C:/rec")
            self.assertEqual(loaded.audio_recorder.mp3_bitrate_kbps, 192)
            self.assertEqual(
                loaded.audio_recorder.selected_filename, "Record_X.mp3"
            )

    def test_app_settings_load_without_recorder_key(self) -> None:
        """Alte settings.json ohne ``audio_recorder``-Sektion → Defaults."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "cat": {},
                        "ui": {},
                        "polling": {},
                        "rig_bridge": {},
                        "audio_player": {},
                    }
                ),
                encoding="utf-8",
            )
            loaded = AppSettings.load(path)
            self.assertEqual(
                loaded.audio_recorder.mp3_bitrate_kbps, DEFAULT_BITRATE_KBPS
            )
            self.assertEqual(loaded.audio_recorder.folder_path, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
