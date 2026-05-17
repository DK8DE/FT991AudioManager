"""Tests für Relais-Eingangs-/Ausgangs-QRG."""

from __future__ import annotations

import unittest

from mapping.repeater_offset import (
    SHIFT_MINUS,
    SHIFT_PLUS,
    default_repeater_offset_hz,
    parse_if_shift_direction,
    relay_listen_hz,
)


class RepeaterOffsetTest(unittest.TestCase):
    def test_default_offset(self) -> None:
        self.assertEqual(default_repeater_offset_hz(145_600_000), 600_000)
        self.assertEqual(default_repeater_offset_hz(432_000_000), 7_600_000)

    def test_relay_listen_minus(self) -> None:
        self.assertEqual(
            relay_listen_hz(145_600_000, shift_dir=SHIFT_MINUS),
            145_000_000,
        )

    def test_relay_listen_plus(self) -> None:
        self.assertEqual(
            relay_listen_hz(145_000_000, shift_dir=SHIFT_PLUS, offset_hz=600_000),
            145_600_000,
        )

    def test_parse_if_shift(self) -> None:
        body = "0" * 24 + "2"
        self.assertEqual(parse_if_shift_direction(f"IF{body};"), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
