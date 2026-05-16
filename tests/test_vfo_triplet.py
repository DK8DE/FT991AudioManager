"""Tests für die VFO-Dreiteilung MHz | kHz | Hz."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.vfo_triplet_widget import (  # noqa: E402
    VfoTripletWidget,
    compose_frequency_hz,
    decompose_frequency_hz,
    field_width_for_digits,
    snap_vfo_hz_to_10hz_grid,
)


def _ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


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
        self.assertEqual(compose_frequency_hz(1, 999, 989), 1_999_990)

    def test_snap_hz_tenth_grid(self) -> None:
        self.assertEqual(snap_vfo_hz_to_10hz_grid(14_229_254), 14_229_250)
        self.assertEqual(snap_vfo_hz_to_10hz_grid(14_229_255), 14_229_260)


class VfoTripletDisplayTest(unittest.TestCase):
    def test_khz_block_shows_three_digits(self) -> None:
        _ensure_qapp()
        w = VfoTripletWidget(font_scale=2.3)
        w.set_frequency_hz(145_130_000)
        self.assertEqual(w._khz.text(), "130")
        self.assertEqual(w._hz.text(), "000")
        self.assertGreaterEqual(
            w._khz.width(),
            field_width_for_digits(w._khz, 3),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
