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
    h3_from_hz_segment_display,
    hz_segment_display_from_h3,
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

    def test_snap_does_not_land_in_2m_uhf_gap(self) -> None:
        """164.999.999 MHz (CAT-Max 2 m) darf nicht als 165 MHz (Lücke) erscheinen."""
        self.assertEqual(snap_vfo_hz_to_10hz_grid(164_999_999), 164_999_990)
        self.assertEqual(snap_vfo_hz_to_10hz_grid(165_000_000), 164_999_990)

    def test_compose_rejects_165mhz_block_in_gap(self) -> None:
        self.assertEqual(compose_frequency_hz(165, 0, 0), 164_999_990)

    def test_hz_display_omits_ones_digit(self) -> None:
        self.assertEqual(hz_segment_display_from_h3(500), 50)
        self.assertEqual(hz_segment_display_from_h3(250), 25)
        self.assertEqual(hz_segment_display_from_h3(0), 0)
        self.assertEqual(h3_from_hz_segment_display(50), 500)
        self.assertEqual(h3_from_hz_segment_display(25), 250)


class VfoTripletDisplayTest(unittest.TestCase):
    def test_khz_block_shows_three_digits(self) -> None:
        _ensure_qapp()
        w = VfoTripletWidget(font_scale=2.3)
        w.set_frequency_hz(145_130_000)
        self.assertEqual(w._khz.text(), "130")
        self.assertEqual(w._hz.text(), "00")
        self.assertGreaterEqual(
            w._khz.width(),
            field_width_for_digits(w._khz, 3),
        )

    def test_hz_block_shows_two_digits_without_ones(self) -> None:
        _ensure_qapp()
        w = VfoTripletWidget(font_scale=2.3)
        w.set_frequency_hz(149_112_560)
        self.assertEqual(w._mhz.text(), "149")
        self.assertEqual(w._khz.text(), "112")
        self.assertEqual(w._hz.text(), "56")
        self.assertGreaterEqual(
            w._hz.width(),
            field_width_for_digits(w._hz, 2),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
