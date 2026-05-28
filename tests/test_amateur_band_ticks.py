"""Tests für Band-Streifen-Tick-Hilfen."""

from __future__ import annotations

import unittest

from mapping.amateur_bands import (
    AMATEUR_BANDS,
    SPECIAL_BANDS,
    band_100khz_tick_frequencies,
    band_strip_groove_tick_frequencies,
    band_strip_label_tick_frequencies,
    band_strip_tick_frequencies,
    band_strip_tick_label,
    band_tick_frequencies,
    cb_all_channel_frequencies_hz,
    cb_band_strip_label_frequencies,
    cb_channel_frequency_hz,
    freenet_all_channel_frequencies_hz,
    snap_band_strip_frequency_hz,
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
        groove = band_strip_groove_tick_frequencies(band)
        labels = band_strip_label_tick_frequencies(band)
        self.assertEqual(len(groove), 80)
        self.assertEqual(groove, cb_all_channel_frequencies_hz())
        self.assertEqual(labels, cb_band_strip_label_frequencies())
        self.assertEqual(groove[0], cb_channel_frequency_hz(41))
        self.assertEqual(groove[39], cb_channel_frequency_hz(80))
        self.assertEqual(groove[40], cb_channel_frequency_hz(1))
        self.assertEqual(groove[-1], cb_channel_frequency_hz(40))
        self.assertEqual(band_strip_tick_label(groove[0], band), "41")
        self.assertEqual(band_strip_tick_label(groove[-1], band), "40")
        self.assertEqual(band_strip_tick_label(27_075_000, band), "10")

    def test_snap_cb_and_freenet(self) -> None:
        cb_band = SPECIAL_BANDS[0]
        fn_band = SPECIAL_BANDS[1]
        self.assertEqual(
            snap_band_strip_frequency_hz(27_072_000, cb_band), 27_075_000
        )
        self.assertEqual(
            snap_band_strip_frequency_hz(26_864_000, cb_band),
            cb_channel_frequency_hz(71),
        )
        self.assertEqual(
            snap_band_strip_frequency_hz(27_000_005, cb_band), 27_005_000
        )
        self.assertEqual(
            snap_band_strip_frequency_hz(149_040_000, fn_band), 149_037_500
        )

    def test_freenet_band_strip_ticks(self) -> None:
        band = SPECIAL_BANDS[1]
        groove = band_strip_groove_tick_frequencies(band)
        self.assertEqual(groove, freenet_all_channel_frequencies_hz())
        self.assertEqual(len(groove), 6)
        self.assertEqual(groove[0], 149_025_000)
        self.assertEqual(groove[-1], 149_087_500)
        self.assertEqual(band_strip_tick_label(groove[0], band), "1")
        self.assertEqual(band_strip_tick_label(groove[3], band), "4")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
