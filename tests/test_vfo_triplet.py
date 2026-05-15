"""Tests für die VFO-Dreiteilung MHz | kHz | Hz."""

from __future__ import annotations

import unittest

from gui.vfo_triplet_widget import (
    compose_frequency_hz,
    decompose_frequency_hz,
)


class VfoDecomposeTest(unittest.TestCase):
    def test_149_112_500(self) -> None:
        hz = 149_112_500
        self.assertEqual(decompose_frequency_hz(hz), (149, 112, 500))
        self.assertEqual(compose_frequency_hz(149, 112, 500), hz)

    def test_round_trip_hf(self) -> None:
        hz = 14_229_250
        m, k, h = decompose_frequency_hz(hz)
        self.assertEqual((m, k, h), (14, 229, 250))
        self.assertEqual(compose_frequency_hz(m, k, h), hz)

    def test_clamp_khz_hz_parts(self) -> None:
        self.assertEqual(compose_frequency_hz(1, 9999, 9999), 1_999_999)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
