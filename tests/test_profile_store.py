"""Tests für model.preset_store / model.audio_profile."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mapping.audio_mapping import MIC_GAIN_DEFAULT, SSB_BPF_DEFAULT_KEY
from model import (
    AudioProfile,
    DEFAULT_PROFILE_NAME,
    EQBand,
    EQSettings,
    PresetStore,
    make_flat_default_profile,
)


class PresetStoreTest(unittest.TestCase):
    def test_first_start_creates_flat_default(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore.load(path)
            self.assertTrue(path.exists())
            self.assertEqual(store.names(), [DEFAULT_PROFILE_NAME])
            p = store.find(DEFAULT_PROFILE_NAME)
            assert p is not None
            self.assertFalse(p.mic_eq_enabled)
            self.assertFalse(p.speech_processor_enabled)
            self.assertTrue(p.normal_eq.eq1.is_off())

    def test_save_and_reload(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore.load(path)
            store.upsert(
                AudioProfile(
                    name="Test",
                    normal_eq=EQSettings(
                        eq1=EQBand(freq=200, level=-3, bw=5),
                        eq2=EQBand(freq=1200, level=2, bw=4),
                        eq3=EQBand(freq=2400, level=4, bw=3),
                    ),
                )
            )

            reloaded = PresetStore.load(path)
            test = reloaded.find("Test")
            self.assertIsNotNone(test)
            assert test is not None
            self.assertEqual(test.normal_eq.eq1.freq, 200)
            self.assertEqual(test.normal_eq.eq1.level, -3)
            self.assertEqual(test.normal_eq.eq3.bw, 3)

    def test_remove(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore.load(path)
            store.upsert(AudioProfile(name="Zusatz", normal_eq=EQSettings.default()))
            self.assertTrue(store.remove(DEFAULT_PROFILE_NAME))
            self.assertIsNone(store.find(DEFAULT_PROFILE_NAME))

    def test_remove_last_restores_flat_default(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore(path=path, profiles=[])
            store.upsert(
                AudioProfile(
                    name="Einzelnes",
                    normal_eq=EQSettings.default(),
                )
            )
            self.assertTrue(store.remove("Einzelnes"))
            self.assertEqual(store.names(), [DEFAULT_PROFILE_NAME])
            p = store.find(DEFAULT_PROFILE_NAME)
            assert p is not None
            self.assertFalse(p.mic_eq_enabled)

    def test_export_import_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            export_path = Path(tmp) / "export.json"
            store = PresetStore.load(path)
            store.upsert(AudioProfile(name="Extra", normal_eq=EQSettings.default()))
            store.export_to_file(export_path)
            store.replace_all([make_flat_default_profile()])
            count = store.import_replace_all_from_file(export_path)
            self.assertEqual(count, 2)
            self.assertIn("Extra", store.names())
            self.assertIn(DEFAULT_PROFILE_NAME, store.names())

    def test_rename(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore.load(path)
            self.assertTrue(store.rename(DEFAULT_PROFILE_NAME, "Basis"))
            self.assertIsNone(store.find(DEFAULT_PROFILE_NAME))
            self.assertIsNotNone(store.find("Basis"))

    def test_corrupt_file_returns_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            path.write_text("{ not json", encoding="utf-8")
            store = PresetStore.load(path)
            self.assertEqual(store.names(), [DEFAULT_PROFILE_NAME])


class AudioProfileSerializationTest(unittest.TestCase):
    def test_roundtrip_minimal(self) -> None:
        p = AudioProfile(
            name="X",
            mode_group="FM",
            normal_eq=EQSettings(
                eq1=EQBand(freq="OFF", level=0, bw=5),
                eq2=EQBand(freq=1000, level=2, bw=4),
                eq3=EQBand(freq=2500, level=3, bw=3),
            ),
        )
        encoded = json.dumps(p.to_dict())
        decoded = AudioProfile.from_dict(json.loads(encoded))
        self.assertEqual(decoded.name, "X")
        self.assertNotIn("mode_group", json.loads(encoded))
        self.assertEqual(decoded.normal_eq.eq1.freq, "OFF")
        self.assertEqual(decoded.normal_eq.eq2.freq, 1000)
        self.assertEqual(decoded.mic_gain, MIC_GAIN_DEFAULT)
        self.assertEqual(decoded.ssb_tx_bpf, SSB_BPF_DEFAULT_KEY)

    def test_legacy_profile_without_basics_loads_with_defaults(self) -> None:
        legacy = {
            "name": "Legacy",
            "mode_group": "SSB",
            "normal_eq": {
                "eq1": {"freq": 300, "level": -3, "bw": 5},
                "eq2": {"freq": 1200, "level": 2, "bw": 4},
                "eq3": {"freq": 2500, "level": 4, "bw": 3},
            },
        }
        decoded = AudioProfile.from_dict(legacy)
        self.assertEqual(decoded.mic_gain, MIC_GAIN_DEFAULT)
        self.assertEqual(decoded.ssb_tx_bpf, SSB_BPF_DEFAULT_KEY)
        self.assertTrue(decoded.mic_eq_enabled)
        self.assertFalse(decoded.speech_processor_enabled)

    def test_roundtrip_full(self) -> None:
        p = AudioProfile(
            name="Voll",
            mode_group="SSB",
            mic_gain=55,
            mic_eq_enabled=True,
            speech_processor_enabled=True,
            speech_processor_level=35,
            ssb_tx_bpf="100-2900",
            normal_eq=EQSettings(
                eq1=EQBand(freq=300, level=-3, bw=5),
                eq2=EQBand(freq=1200, level=2, bw=4),
                eq3=EQBand(freq=2500, level=4, bw=3),
            ),
            processor_eq=EQSettings(
                eq1=EQBand(freq=300, level=-2, bw=5),
                eq2=EQBand(freq=1200, level=3, bw=4),
                eq3=EQBand(freq=2500, level=5, bw=3),
            ),
            advanced={"ssb_hcut_freq": 3000},
        )
        decoded = AudioProfile.from_dict(p.to_dict())
        self.assertEqual(decoded.mic_gain, 55)
        self.assertTrue(decoded.mic_eq_enabled)
        self.assertEqual(decoded.speech_processor_level, 35)
        self.assertEqual(decoded.processor_eq.eq3.level, 5)
        self.assertEqual(decoded.advanced.get("ssb_hcut_freq"), 3000)

    def test_mode_group_in_json_is_ignored(self) -> None:
        decoded = AudioProfile.from_dict({"name": "x", "mode_group": "FM"})
        self.assertEqual(decoded.name, "x")
        self.assertEqual(decoded.mode_group, "SSB")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
