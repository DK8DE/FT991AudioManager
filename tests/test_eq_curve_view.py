"""Tests für den interaktiven EQ-Kurven-Editor (`gui.eq_curve_view`)."""

from __future__ import annotations

import math
import os
import unittest

# Headless-Qt — falls die Umgebung keine Anzeige hat.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.eq_curve_view import (  # noqa: E402
    _EqCurveCanvas,
    _band_gain_db,
    _first_enabled_freq,
    _half_width_oct_for_bw,
    _nearest_freq_for_band,
    _total_gain_db,
    EqCurveView,
)
from mapping.eq_mapping import (  # noqa: E402
    BW_MAX,
    BW_MIN,
    LEVEL_DB_MAX,
    LEVEL_DB_MIN,
    EQ_LOW_FREQS,
    EQ_MID_FREQS,
    EQ_HIGH_FREQS,
)
from model.eq_band import EQBand, EQSettings  # noqa: E402


# ----------------------------------------------------------------------
# Reine Logik
# ----------------------------------------------------------------------


class NearestFreqTest(unittest.TestCase):
    def test_picks_exact_match(self) -> None:
        self.assertEqual(_nearest_freq_for_band(0, 200), 200)
        self.assertEqual(_nearest_freq_for_band(2, 2400), 2400)

    def test_snaps_to_nearest_in_log_space(self) -> None:
        # LOW: erlaubte 100..700 in 100er-Schritten.
        self.assertEqual(_nearest_freq_for_band(0, 105), 100)
        self.assertEqual(_nearest_freq_for_band(0, 850), 700)
        # MID: 700..1500
        self.assertEqual(_nearest_freq_for_band(1, 600), 700)
        self.assertEqual(_nearest_freq_for_band(1, 2000), 1500)

    def test_excludes_off_value(self) -> None:
        # "OFF" liegt in der Tabelle, darf aber nicht als Snap-Ziel zählen.
        self.assertEqual(_nearest_freq_for_band(0, 1), 100)


class FirstEnabledFreqTest(unittest.TestCase):
    def test_returns_first_int(self) -> None:
        self.assertEqual(_first_enabled_freq(0), EQ_LOW_FREQS[1])
        self.assertEqual(_first_enabled_freq(1), EQ_MID_FREQS[1])
        self.assertEqual(_first_enabled_freq(2), EQ_HIGH_FREQS[1])


class HalfWidthOctTest(unittest.TestCase):
    def test_monotonic_decreasing(self) -> None:
        widths = [_half_width_oct_for_bw(bw) for bw in range(BW_MIN, BW_MAX + 1)]
        for a, b in zip(widths, widths[1:]):
            self.assertGreater(a, b)

    def test_clamped(self) -> None:
        self.assertEqual(_half_width_oct_for_bw(0), _half_width_oct_for_bw(BW_MIN))
        self.assertEqual(_half_width_oct_for_bw(99), _half_width_oct_for_bw(BW_MAX))


class BandGainTest(unittest.TestCase):
    def test_off_band_is_zero(self) -> None:
        band = EQBand(freq="OFF", level=10, bw=5)
        self.assertEqual(_band_gain_db(band, 1000), 0.0)

    def test_zero_level_is_zero(self) -> None:
        band = EQBand(freq=1000, level=0, bw=5)
        self.assertEqual(_band_gain_db(band, 1000), 0.0)

    def test_peak_at_center(self) -> None:
        band = EQBand(freq=1000, level=6, bw=5)
        peak = _band_gain_db(band, 1000)
        side = _band_gain_db(band, 2000)
        self.assertAlmostEqual(peak, 6.0, places=2)
        self.assertLess(side, peak)

    def test_total_gain_sums_bands(self) -> None:
        bands = [
            EQBand(freq=200, level=4, bw=5),
            EQBand(freq=1000, level=-3, bw=5),
            EQBand(freq=3000, level=6, bw=5),
        ]
        total_1k = _total_gain_db(bands, 1000)
        # Mitte ist klar der Peak des MID-Bandes.
        self.assertAlmostEqual(total_1k, -3.0, delta=0.5)


# ----------------------------------------------------------------------
# Widget-Smoke (mit QApplication, offscreen)
# ----------------------------------------------------------------------


def _ensure_qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class CurveViewSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_qapp()
        self.view = EqCurveView()
        self.view.resize(400, 220)
        # Wichtig: Geometry erst nach show() / oder explizitem resize
        # vorhanden — wir rufen direkt das Canvas geometry-API.

    def test_set_get_roundtrip(self) -> None:
        s = EQSettings(
            eq1=EQBand(freq=300, level=4, bw=5),
            eq2=EQBand(freq=1000, level=-3, bw=6),
            eq3=EQBand(freq=2500, level=8, bw=2),
        )
        self.view.set_settings(s)
        out = self.view.get_settings()
        self.assertEqual(out, s)

    def test_settings_changed_not_emitted_on_programmatic_set(self) -> None:
        fired = []
        self.view.settings_changed.connect(lambda obj: fired.append(obj))
        self.view.set_settings(
            EQSettings(
                eq1=EQBand(freq=200, level=2, bw=5),
                eq2=EQBand(freq=1000, level=-1, bw=5),
                eq3=EQBand(freq=2200, level=3, bw=5),
            )
        )
        self.assertEqual(fired, [])


class CanvasInteractionTest(unittest.TestCase):
    """Drag-Logik mit künstlich gesetzter Plot-Geometrie."""

    def setUp(self) -> None:
        _ensure_qapp()
        self.canvas = _EqCurveCanvas()
        self.canvas.resize(420, 240)
        # Layout-Pass erzwingen, damit width()/height() sinnvoll sind.
        self.canvas.adjustSize()

    def _set_initial(self) -> None:
        self.canvas.set_bands([
            EQBand(freq=300, level=0, bw=5),
            EQBand(freq=1000, level=0, bw=5),
            EQBand(freq=2400, level=0, bw=5),
        ])

    def test_toggle_off_then_on(self) -> None:
        self._set_initial()
        # Toggle MID band off
        self.canvas._toggle_off(1)
        self.assertEqual(self.canvas.bands()[1].freq, "OFF")
        # Toggle on → erste echte Frequenz aus MID-Tabelle
        self.canvas._toggle_off(1)
        self.assertEqual(self.canvas.bands()[1].freq, EQ_MID_FREQS[1])

    def test_drag_center_updates_level_and_freq(self) -> None:
        """Setzt den MID-Punkt manuell auf eine Position und prüft Snap."""
        self._set_initial()
        plot_x, plot_y, plot_w, plot_h = self.canvas._plot_geometry()
        # Position für 1200 Hz auf der X-Achse, +5 dB auf der Y-Achse.
        target_x = self.canvas._x_for_freq(1200.0, plot_x, plot_w)
        target_y = self.canvas._y_for_db(5.0, plot_y, plot_h)
        # Drag-Modus setzen und anwenden
        from gui.eq_curve_view import _DragState
        self.canvas._drag = _DragState(band_index=1, mode="center")
        self.canvas._apply_drag(target_x, target_y, initial=True)
        band = self.canvas.bands()[1]
        self.assertEqual(band.freq, 1200)
        self.assertEqual(band.level, 5)

    def test_drag_edge_updates_bw(self) -> None:
        """Edge-Drag schmaler ziehen → BW steigt (Q wird größer)."""
        self._set_initial()
        # Start: BW=5 → halbe Breite = 1.25/5 = 0.25 Oktaven
        # Ziel: rechte Kante auf 0.125 Oktaven Distanz → BW=10
        plot_x, plot_y, plot_w, _plot_h = self.canvas._plot_geometry()
        f0 = 1000.0
        target_f = f0 * (2 ** 0.125)
        target_x = self.canvas._x_for_freq(target_f, plot_x, plot_w)
        from gui.eq_curve_view import _DragState
        self.canvas._drag = _DragState(band_index=1, mode="edge_right")
        self.canvas._apply_drag(target_x, 100.0, initial=True)
        bw_after = self.canvas.bands()[1].bw
        self.assertEqual(bw_after, BW_MAX)

    def test_drag_emits_signal(self) -> None:
        self._set_initial()
        fired = []
        self.canvas.bands_changed.connect(lambda obj: fired.append(obj))
        plot_x, plot_y, plot_w, plot_h = self.canvas._plot_geometry()
        target_x = self.canvas._x_for_freq(1100.0, plot_x, plot_w)
        target_y = self.canvas._y_for_db(3.0, plot_y, plot_h)
        from gui.eq_curve_view import _DragState
        self.canvas._drag = _DragState(band_index=1, mode="center")
        self.canvas._apply_drag(target_x, target_y, initial=True)
        self.assertGreaterEqual(len(fired), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
