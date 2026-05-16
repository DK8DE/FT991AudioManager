"""Tests für PC (Sendeleistung) und EX (TX-MAX) CAT-Befehle."""

from __future__ import annotations

import unittest

from mapping.tx_power_mapping import (
    encode_tx_max_power_menu,
    format_pc_set,
    parse_pc_response,
)


class TxPowerPcTest(unittest.TestCase):
    def test_pc_format_and_parse(self) -> None:
        self.assertEqual(format_pc_set(5), "PC005;")
        self.assertEqual(format_pc_set(50, max_watts=50), "PC050;")
        self.assertEqual(parse_pc_response("PC050;"), 50)

    def test_pc_clamps_to_band_max(self) -> None:
        self.assertEqual(format_pc_set(100, max_watts=50), "PC050;")

    def test_ex_max_menu_encode(self) -> None:
        self.assertEqual(encode_tx_max_power_menu(50), "050")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
