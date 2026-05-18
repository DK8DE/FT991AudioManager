"""Tests für :class:`~model.smeter_calibration_settings.SmeterCalibrationSettings`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model import AppSettings
from model.smeter_calibration_settings import SmeterCalibrationPoint, SmeterCalibrationSettings


class SmeterCalibrationSettingsTest(unittest.TestCase):
    def test_factory_defaults_enabled_with_four_points_each(self) -> None:
        s = SmeterCalibrationSettings()
        self.assertTrue(s.use_custom)
        self.assertEqual(len(s.points_hf), 4)
        self.assertEqual(len(s.points_vhf), 4)
        self.assertEqual(s.points_hf[0].raw, 60)
        self.assertEqual(s.points_hf[1].raw, 133)
        self.assertEqual(s.points_hf[2].raw, 160)
        self.assertEqual(s.points_vhf[0].raw, 44)
        self.assertEqual(s.points_vhf[3].raw, 165)
        self.assertEqual(len(s.effective_points_hf()), 4)
        self.assertEqual(len(s.effective_points_vhf()), 4)

    def test_from_empty_dict_uses_factory_defaults(self) -> None:
        s = SmeterCalibrationSettings.from_dict({})
        self.assertTrue(s.use_custom)
        self.assertEqual(len(s.points_hf), 4)
        self.assertEqual(len(s.points_vhf), 4)

    def test_legacy_points_key_copies_to_both_bands(self) -> None:
        s = SmeterCalibrationSettings.from_dict(
            {
                "use_custom": True,
                "points": [
                    {"raw": 50, "db_over_s9": -24.0},
                    {"raw": 90, "db_over_s9": 0.0},
                ],
            }
        )
        self.assertEqual(len(s.points_hf), 2)
        self.assertEqual(len(s.points_vhf), 2)
        self.assertEqual(s.points_hf[0].raw, 50)
        self.assertEqual(s.points_vhf[1].raw, 90)

    def test_roundtrip_via_app_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            s = AppSettings.load(path)
            s.smeter_calibration.use_custom = True
            s.smeter_calibration.points_hf = [
                SmeterCalibrationPoint(58, -24.0),
                SmeterCalibrationPoint(120, 20.0),
            ]
            s.smeter_calibration.points_vhf = [
                SmeterCalibrationPoint(30, -30.0),
                SmeterCalibrationPoint(99, 10.0),
            ]
            s.save(path)
            loaded = AppSettings.load(path)
            self.assertTrue(loaded.smeter_calibration.use_custom)
            self.assertEqual(len(loaded.smeter_calibration.points_hf), 2)
            self.assertEqual(len(loaded.smeter_calibration.points_vhf), 2)
            self.assertEqual(loaded.smeter_calibration.points_hf[0].raw, 58)
            self.assertAlmostEqual(
                loaded.smeter_calibration.points_vhf[1].db_over_s9, 10.0, places=5
            )

    def test_to_dict_contains_both_keys(self) -> None:
        s = SmeterCalibrationSettings()
        d = s.to_dict()
        self.assertIn("points_hf", d)
        self.assertIn("points_vhf", d)
        self.assertEqual(len(d["points_hf"]), 4)

    def test_effective_points_requires_two_distinct_raws(self) -> None:
        s = SmeterCalibrationSettings(
            use_custom=True,
            points_hf=[SmeterCalibrationPoint(10, 0.0)],
            points_vhf=[SmeterCalibrationPoint(10, 0.0)],
        )
        self.assertEqual(s.effective_points_hf(), [])
        s.points_hf.append(SmeterCalibrationPoint(20, 10.0))
        self.assertEqual(len(s.effective_points_hf()), 2)
