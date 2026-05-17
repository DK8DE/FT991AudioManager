"""Tests für Amateurband-Erkennung."""

from __future__ import annotations

import unittest

from mapping.amateur_bands import (
    AMATEUR_BANDS_HIGH_TO_LOW,
    VFO_BAND_CHOICE,
    amateur_band_for_hz,
    combo_entries_high_to_low,
    is_in_amateur_band,
)


class AmateurBandsTest(unittest.TestCase):
    def test_hf_in_band(self) -> None:
        self.assertEqual(amateur_band_for_hz(14_250_000), "20 m")
        self.assertTrue(is_in_amateur_band(7_100_000))

    def test_vhf_in_band(self) -> None:
        self.assertEqual(amateur_band_for_hz(145_500_000), "2 m")
        self.assertEqual(amateur_band_for_hz(432_000_000), "70 cm")

    def test_out_of_band(self) -> None:
        self.assertIsNone(amateur_band_for_hz(55_999_400))
        self.assertFalse(is_in_amateur_band(100_000_000))

    def test_combo_order_high_to_low(self) -> None:
        entries = combo_entries_high_to_low()
        self.assertEqual(entries[0], ("VFO", VFO_BAND_CHOICE))
        self.assertEqual(entries[1][1], AMATEUR_BANDS_HIGH_TO_LOW[0].center_hz)
        self.assertIn("70 cm", entries[1][0])
        self.assertIn("160 m", entries[-1][0])

    def test_combo_label_format(self) -> None:
        label = AMATEUR_BANDS_HIGH_TO_LOW[0].combo_label()
        self.assertIn("430.000", label)
        self.assertIn("440.000", label)
        self.assertIn("(70 cm)", label)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
