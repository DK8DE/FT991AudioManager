"""Tests für Live-Gerätenamen (PA ↔ Qt/Windows)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from live.live_devices import (
    _best_pa_row_for_qt_name,
    _match_score,
    _norm_match_key,
)


class LiveDeviceLabelTest(unittest.TestCase):
    def test_norm_match_strips_r_mark(self) -> None:
        a = _norm_match_key("Mikrofon (Realtek(R) Audio)")
        b = _norm_match_key("Mikrofon (Realtek Audio)")
        self.assertEqual(a, b)

    def test_match_score_similar_names(self) -> None:
        sc = _match_score(
            "Mikrofon (Realtek Audio)",
            "Mikrofon (Realtek(R) Audio)",
        )
        self.assertGreaterEqual(sc, 0.8)

    def test_best_pa_row_fuzzy(self) -> None:
        sd = MagicMock()
        sd.query_devices.return_value = [
            {
                "name": "Speakers (Realtek(R) Audio)",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "hostapi": 0,
            }
        ]
        sd.query_hostapis.return_value = [{"name": "Windows WASAPI"}]

        row = _best_pa_row_for_qt_name(
            sd,
            "Speakers (Realtek Audio)",
            want_input=False,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row[0], 0)


if __name__ == "__main__":
    unittest.main()
