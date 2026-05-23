"""Tests für logarithmische Live-Lautstärke-Slider."""

from __future__ import annotations

import unittest

from model.live_volume_curve import (
    live_gain_display_percent,
    live_gain_from_slider,
    live_slider_from_gain,
)


class LiveVolumeCurveTest(unittest.TestCase):
    def test_unity_and_max(self) -> None:
        self.assertAlmostEqual(live_gain_from_slider(100), 1.0, places=5)
        self.assertAlmostEqual(live_gain_from_slider(200), 2.0, places=5)
        self.assertEqual(live_gain_from_slider(0), 0.0)

    def test_low_end_gentler_than_linear(self) -> None:
        g20 = live_gain_from_slider(20)
        self.assertLess(g20, 0.2)
        self.assertGreater(g20, 0.0)

    def test_round_trip(self) -> None:
        for gain in (0.0, 0.05, 0.25, 1.0, 1.5, 2.0):
            s = live_slider_from_gain(gain)
            back = live_gain_from_slider(s)
            self.assertAlmostEqual(back, gain, places=2)

    def test_display_percent_matches_gain(self) -> None:
        self.assertEqual(live_gain_display_percent(1.0), 100)
        self.assertEqual(live_gain_display_percent(0.5), 50)


if __name__ == "__main__":
    unittest.main()
