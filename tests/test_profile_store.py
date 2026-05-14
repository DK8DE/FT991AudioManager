"""Tests für model.preset_store / model.audio_profile."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mapping.audio_mapping import MIC_GAIN_DEFAULT, SSB_BPF_DEFAULT_KEY
from model import AudioProfile, EQBand, EQSettings, PresetStore


class PresetStoreTest(unittest.TestCase):
    def test_default_initialization_creates_seed_profiles(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore.load(path)
            self.assertTrue(path.exists())
            self.assertGreaterEqual(len(store.profiles), 3)
            self.assertIn("SSB Sprache", store.names())

    def test_save_and_reload(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            store = PresetStore.load(path)
            store.upsert(
                AudioProfile(
                    name="Test",
                    mode_group="SSB",
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
            assert store.find("SSB Sprache") is not None
            self.assertTrue(store.remove("SSB Sprache"))
            self.assertFalse(store.remove("SSB Sprache"))

            reloaded = PresetStore.load(path)
            self.assertIsNone(reloaded.find("SSB Sprache"))

    def test_corrupt_file_returns_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "presets.json"
            path.write_text("{ not json", encoding="utf-8")
            store = PresetStore.load(path)
            self.assertGreaterEqual(len(store.profiles), 1)


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
        self.assertEqual(decoded.mode_group, "FM")
        self.assertEqual(decoded.normal_eq.eq1.freq, "OFF")
        self.assertEqual(decoded.normal_eq.eq2.freq, 1000)
        # Grundwerte sind jetzt immer gesetzt (Defaults)
        self.assertEqual(decoded.mic_gain, MIC_GAIN_DEFAULT)
        self.assertEqual(decoded.ssb_tx_bpf, SSB_BPF_DEFAULT_KEY)

    def test_legacy_profile_without_basics_loads_with_defaults(self) -> None:
        """Profile aus Version 0.2 hatten keine Grundwerte — wir fallen
        auf Defaults zurück und brechen nichts."""
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

    def test_invalid_mode_group_falls_back_to_ssb(self) -> None:
        decoded = AudioProfile.from_dict({"name": "x", "mode_group": "WTF"})
        self.assertEqual(decoded.mode_group, "SSB")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
