"""Tests für Amateurband-Erkennung."""

from __future__ import annotations

import unittest

from mapping.amateur_bands import amateur_band_for_hz, is_in_amateur_band


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
