"""Tests für CAT SH WIDTH (Sendebandbreite)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cat.cat_errors import CatCommandUnsupportedError, CatProtocolError
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.rx_mapping import RxMode
from mapping.sh_width_mapping import (
    format_sh_width_query,
    format_sh_width_set,
    parse_sh_width_response,
    sh_bandwidth_visible_for_mode,
    sh_display_hz,
    sh_snap_p2_to_supported,
    sh_supported_p2_indices,
)


class ShWidthMappingTest(unittest.TestCase):
    def test_query(self) -> None:
        self.assertEqual(format_sh_width_query(), "SH0;")

    def test_set_roundtrip_indices(self) -> None:
        self.assertEqual(format_sh_width_set(0), "SH000;")
        self.assertEqual(format_sh_width_set(5), "SH005;")
        self.assertEqual(format_sh_width_set(21), "SH021;")

    def test_parse(self) -> None:
        self.assertEqual(parse_sh_width_response("SH0000;"), 0)
        self.assertEqual(parse_sh_width_response("SH0018;"), 18)
        self.assertEqual(parse_sh_width_response("SH0021;"), 21)

    def test_parse_alt_two_digit(self) -> None:
        self.assertEqual(parse_sh_width_response("SH018;"), 18)

    def test_display_usb(self) -> None:
        self.assertEqual(sh_display_hz(RxMode.USB, 21), 3200)
        self.assertEqual(sh_display_hz(RxMode.LSB, 1), 200)

    def test_visible_modes(self) -> None:
        self.assertTrue(sh_bandwidth_visible_for_mode(RxMode.USB))
        self.assertTrue(sh_bandwidth_visible_for_mode(RxMode.CW_U))
        self.assertTrue(sh_bandwidth_visible_for_mode(RxMode.DATA_USB))
        self.assertFalse(sh_bandwidth_visible_for_mode(RxMode.FM))
        self.assertFalse(sh_bandwidth_visible_for_mode(RxMode.DATA_FM))

    def test_cw_p2_not_full_range(self) -> None:
        self.assertNotIn(21, sh_supported_p2_indices(RxMode.CW_U))
        self.assertIn(17, sh_supported_p2_indices(RxMode.CW_U))

    def test_snap_p2_cw(self) -> None:
        self.assertEqual(sh_snap_p2_to_supported(20, RxMode.CW_U), 17)

    def test_usb_allows_full_p2_range(self) -> None:
        s = sh_supported_p2_indices(RxMode.USB)
        self.assertEqual(len(s), 22)


class _ShWriteFake(SerialCAT):
    """Minimaler Fake: SH-Schreiben mit Echo oder ``?;``."""

    def __init__(self, *, reject: frozenset[int] | None = None) -> None:
        super().__init__()
        self.reject = reject or frozenset()
        self.calls: list[tuple[str, bool]] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(  # type: ignore[override]
        self,
        command: str,
        *,
        read_response: bool = True,
        expected_prefix: object = None,
    ) -> str:
        self.calls.append((command, read_response))
        if command.startswith("SH0") and command.endswith(";") and command != "SH0;":
            body = command[3:-1]
            if len(body) != 2 or not body.isdigit():
                raise AssertionError(command)
            p2 = int(body)
            if p2 in self.reject:
                raise CatCommandUnsupportedError("simuliertes ?;")
            if read_response:
                return command
            return ""
        raise AssertionError(command)


class ShWriteReadbackTest(unittest.TestCase):
    def test_write_tx_bandwidth_reads_echo(self) -> None:
        fake = _ShWriteFake()
        FT991CAT(fake).write_tx_bandwidth_sh(12)
        self.assertEqual(len(fake.calls), 1)
        cmd, rr = fake.calls[0]
        self.assertTrue(rr)
        self.assertEqual(cmd, "SH012;")

    def test_write_rejected_becomes_protocol_error(self) -> None:
        fake = _ShWriteFake(reject=frozenset({15}))
        with self.assertRaises(CatProtocolError):
            FT991CAT(fake).write_tx_bandwidth_sh(15)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
