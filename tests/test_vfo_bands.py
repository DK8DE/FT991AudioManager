"""Tests für bandweise VFO-Schritte (FT-991/A)."""

from __future__ import annotations

import unittest

from mapping.vfo_bands import (
    is_valid_vfo_frequency_hz,
    step_vfo_frequency_hz,
)


class VfoBandsTest(unittest.TestCase):
    def test_step_within_hf(self) -> None:
        self.assertEqual(
            step_vfo_frequency_hz(14_250_000, 1_000),
            14_251_000,
        )

    def test_step_up_from_hf_to_2m(self) -> None:
        self.assertEqual(
            step_vfo_frequency_hz(55_999_999, 1_000),
            144_000_000,
        )

    def test_step_up_from_2m_to_70cm(self) -> None:
        self.assertEqual(
            step_vfo_frequency_hz(164_999_999, 1),
            430_000_000,
        )

    def test_gap_not_valid(self) -> None:
        self.assertFalse(is_valid_vfo_frequency_hz(60_000_000))
        self.assertFalse(is_valid_vfo_frequency_hz(200_000_000))

    def test_step_down_from_76m_to_hf(self) -> None:
        self.assertEqual(
            step_vfo_frequency_hz(76_000_000, -1_000),
            55_999_999,
        )

    def test_floor_at_30_khz(self) -> None:
        self.assertEqual(step_vfo_frequency_hz(30_000, -1_000), 30_000)
        self.assertEqual(step_vfo_frequency_hz(30_000, -1), 30_000)

    def test_ceiling_at_70cm_max(self) -> None:
        self.assertEqual(
            step_vfo_frequency_hz(469_999_999, 1_000),
            469_999_999,
        )
        self.assertEqual(
            step_vfo_frequency_hz(469_999_999, 1),
            469_999_999,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
