"""Tests für ``mapping/memory_editor_codec.py``."""

from __future__ import annotations

import unittest

from mapping.memory_editor_codec import (
    POS_CLAR_OFFSET_KHZ,
    POS_SHIFT_DIR,
    build_mw_command,
    build_mt_command,
    editor_channel_from_mt_response,
    empty_mw_command,
    empty_mt_body,
    normalize_channel_for_write,
    should_write_cleared,
)
from mapping.memory_tones import ToneMode
from mapping.rx_mapping import RxMode
from model.memory_editor_channel import (
    DEFAULT_EMPTY_FREQ_HZ,
    DEFAULT_EMPTY_NAME,
    MemoryEditorChannel,
    ShiftDirection,
)


class MemoryEditorCodecTest(unittest.TestCase):
    def test_empty_mt_body_matches_radio_format(self) -> None:
        body = empty_mt_body(91)
        self.assertEqual(len(body), 38)
        self.assertEqual(body, "091000000000+0000000000000            ")

    def test_empty_mw_command_matches_radio_format(self) -> None:
        self.assertEqual(
            empty_mw_command(91),
            "MW091000000000+0000000000000;",
        )

    def test_parse_real_response(self) -> None:
        response = "MT008446012500+0000004100000Luca        ;"
        ch = editor_channel_from_mt_response(response, requested_channel=1)
        self.assertEqual(ch.number, 1)
        self.assertEqual(ch.rx_frequency_hz, 446_012_500)
        self.assertEqual(ch.name, "Luca")
        self.assertEqual(ch.mode, RxMode.FM)
        self.assertEqual(len(ch.raw_mt_body), 38)

    def test_parse_relay_minus_from_p10_not_p3(self) -> None:
        """Relaiskanal: P3='+', P10 an Index 24='2' → Minus."""
        body = "003145625000+0000004000020HD ZH       "
        response = f"MT{body};"
        ch = editor_channel_from_mt_response(response, requested_channel=20)
        self.assertEqual(ch.shift_direction, ShiftDirection.MINUS)
        self.assertEqual(ch.shift_offset_hz, 600_000)

    def test_parse_offset_from_p4_khz(self) -> None:
        body = "003145700000+0600004000020Kalm XK     "
        response = f"MT{body};"
        ch = editor_channel_from_mt_response(response, requested_channel=21)
        self.assertEqual(ch.shift_direction, ShiftDirection.MINUS)
        self.assertEqual(ch.shift_offset_hz, 600_000)

    def test_ignore_small_p4_artifact(self) -> None:
        """P4=0020 (<100 kHz) ist kein echter Offset — Band-Default."""
        body = "003145700000+0020004000020Kalm XK     "
        response = f"MT{body};"
        ch = editor_channel_from_mt_response(response, requested_channel=21)
        self.assertEqual(ch.shift_direction, ShiftDirection.MINUS)
        self.assertEqual(ch.shift_offset_hz, 600_000)

    def test_parse_70cm_relay(self) -> None:
        body = "003432375000+0000004000020DB0ABC      "
        response = f"MT{body};"
        ch = editor_channel_from_mt_response(response, requested_channel=30)
        self.assertEqual(ch.shift_direction, ShiftDirection.MINUS)
        self.assertEqual(ch.shift_offset_hz, 7_600_000)

    def test_band_default_offset_when_p4_zero(self) -> None:
        """2 m Relais: P4=0, P10=2 → 600 kHz Band-Default."""
        body = "003145500000+0000004000020RELAIS      "
        response = f"MT{body};"
        ch = editor_channel_from_mt_response(response, requested_channel=12)
        self.assertEqual(ch.shift_direction, ShiftDirection.MINUS)
        self.assertEqual(ch.shift_offset_hz, 600_000)

    def test_build_preserves_clarifier_offset_field(self) -> None:
        raw = empty_mt_body(5)
        body_list = list(raw)
        body_list[POS_CLAR_OFFSET_KHZ] = list("1234")
        ch = MemoryEditorChannel(
            number=5,
            enabled=True,
            name="TEST",
            rx_frequency_hz=145_500_000,
            mode=RxMode.FM,
            shift_direction=ShiftDirection.MINUS,
            shift_offset_hz=600_000,
            raw_mt_body="".join(body_list),
        )
        cmd = build_mt_command(ch)
        body = cmd[2:-1]
        self.assertIn("0600", body)
        self.assertIn("TEST", cmd)

    def test_parse_zero_freq_as_cleared(self) -> None:
        body = "050000000000+0600004000020            "
        ch = editor_channel_from_mt_response(f"MT{body};", requested_channel=50)
        self.assertFalse(ch.enabled)
        self.assertEqual(ch.rx_frequency_hz, DEFAULT_EMPTY_FREQ_HZ)
        self.assertEqual(ch.name, DEFAULT_EMPTY_NAME)
        self.assertEqual(ch.shift_direction, ShiftDirection.SIMPLEX)

    def test_should_write_cleared_with_stale_enabled(self) -> None:
        ch = MemoryEditorChannel(
            number=5,
            enabled=True,
            name="",
            rx_frequency_hz=0,
            raw_mt_body="005145625000+0600004000020X           ",
        )
        self.assertTrue(should_write_cleared(ch))

    def test_build_empty_ignores_stale_raw(self) -> None:
        ch = MemoryEditorChannel(
            number=50,
            enabled=False,
            name="",
            rx_frequency_hz=0,
            raw_mt_body="050145625000+0600004000020HD ZH       ",
        )
        body = build_mt_command(ch)[2:-1]
        self.assertEqual(body, empty_mt_body(50))
        self.assertIn("+0000000000000", body)
        self.assertNotIn("4000000", body)

    def test_build_mw_empty_ignores_stale_raw(self) -> None:
        ch = MemoryEditorChannel(
            number=50,
            enabled=False,
            name="",
            rx_frequency_hz=0,
            raw_mt_body="050145625000+0600004000020HD ZH       ",
        )
        self.assertEqual(
            build_mw_command(ch),
            "MW050000000000+0000000000000;",
        )

    def test_build_shift_minus(self) -> None:
        ch = MemoryEditorChannel(
            number=10,
            enabled=True,
            name="RPT",
            rx_frequency_hz=145_600_000,
            mode=RxMode.FM,
            shift_direction=ShiftDirection.MINUS,
            shift_offset_hz=600_000,
            tone_mode=ToneMode.CTCSS_ENC,
        )
        cmd = build_mt_command(ch)
        body = cmd[2:-1]
        self.assertEqual(len(body), 38)
        self.assertEqual(body[13:17], "0600")
        self.assertEqual(body[POS_SHIFT_DIR], "2")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
