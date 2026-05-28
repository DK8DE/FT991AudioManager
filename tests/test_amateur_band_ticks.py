"""Tests für Band-Streifen-Tick-Hilfen."""

from __future__ import annotations

import unittest

from mapping.amateur_bands import (
    AMATEUR_BANDS,
    SPECIAL_BANDS,
    band_100khz_tick_frequencies,
    band_strip_tick_frequencies,
    band_strip_tick_label,
    band_tick_frequencies,
    frequency_label_100khz,
    frequency_label_for_tick,
)


def _band(name: str):
    for band in AMATEUR_BANDS:
        if band.name == name:
            return band
    raise AssertionError(f"band {name!r} not found")


class AmateurBandTicksTest(unittest.TestCase):
    def test_20m_ticks_include_bounds(self) -> None:
        band = _band("20 m")
        ticks = band_tick_frequencies(band, max_ticks=7)
        self.assertEqual(ticks[0], band.min_hz)
        self.assertEqual(ticks[-1], band.max_hz)
        self.assertLessEqual(len(ticks), 7)
        self.assertGreater(len(ticks), 2)

    def test_60m_narrow_band(self) -> None:
        band = _band("60 m")
        ticks = band_tick_frequencies(band, max_ticks=7)
        self.assertEqual(ticks[0], 5_351_500)
        self.assertEqual(ticks[-1], 5_366_500)
        self.assertLessEqual(len(ticks), 7)

    def test_2m_ticks(self) -> None:
        band = _band("2 m")
        ticks = band_tick_frequencies(band, max_ticks=7)
        self.assertEqual(ticks[0], 144_000_000)
        self.assertEqual(ticks[-1], 146_000_000)
        self.assertLessEqual(len(ticks), 7)

    def test_frequency_label_precision(self) -> None:
        band = _band("20 m")
        self.assertEqual(frequency_label_for_tick(14_000_000, band), "14.00")
        band_2m = _band("2 m")
        self.assertEqual(frequency_label_for_tick(145_000_000, band_2m), "145.00")

    def test_100khz_ticks_20m(self) -> None:
        band = _band("20 m")
        ticks = band_100khz_tick_frequencies(band)
        self.assertEqual(ticks[0], 14_000_000)
        self.assertEqual(ticks[-1], 14_300_000)
        self.assertEqual(len(ticks), 4)

    def test_100khz_label(self) -> None:
        self.assertEqual(frequency_label_100khz(14_000_000), "14.0")
        self.assertEqual(frequency_label_100khz(14_100_000), "14.1")

    def test_cb_band_strip_ticks(self) -> None:
        band = SPECIAL_BANDS[0]
        ticks = band_strip_tick_frequencies(band)
        self.assertEqual(ticks[0], 26_965_000)
        self.assertEqual(ticks[-1], 27_405_000)
        self.assertLessEqual(len(ticks), 7)

    def test_freenet_band_strip_ticks(self) -> None:
        band = SPECIAL_BANDS[1]
        ticks = band_strip_tick_frequencies(band)
        self.assertEqual(ticks[0], 149_025_000)
        self.assertEqual(ticks[-1], 149_087_500)
        self.assertLessEqual(len(ticks), 7)
        self.assertIn("149.025", band_strip_tick_label(ticks[0], band))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
