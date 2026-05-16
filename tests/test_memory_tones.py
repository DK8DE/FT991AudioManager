"""Tests für ``mapping/memory_tones.py`` — CN lesen/parsen."""

from __future__ import annotations

import unittest

from mapping.memory_tones import (
    ToneMode,
    apply_cn_read_to_channel,
    ctcss_hz_from_cat_tone_number,
    format_cn_set,
    parse_cn_read_response,
)
from model.memory_editor_channel import MemoryEditorChannel, ShiftDirection


class MemoryTonesCnTest(unittest.TestCase):
    def test_parse_cn_ctcss(self) -> None:
        p2, num = parse_cn_read_response("CN00017;")
        self.assertEqual(p2, 0)
        self.assertEqual(num, 17)
        self.assertAlmostEqual(ctcss_hz_from_cat_tone_number(num), 118.8)

    def test_parse_cn_dcs(self) -> None:
        p2, num = parse_cn_read_response("CN01000;")
        self.assertEqual(p2, 1)
        self.assertEqual(num, 0)

    def test_apply_cn_to_channel(self) -> None:
        ch = MemoryEditorChannel(
            number=91,
            enabled=True,
            name="T",
            rx_frequency_hz=438_975_000,
            tone_mode=ToneMode.CTCSS_ENC,
            shift_direction=ShiftDirection.MINUS,
            shift_offset_hz=7_600_000,
        )
        apply_cn_read_to_channel(ch, p2=0, number=17)
        self.assertAlmostEqual(ch.ctcss_tone_hz, 118.8)

    def test_apply_cn_index_zero_is_67hz(self) -> None:
        ch = MemoryEditorChannel(
            number=91,
            enabled=True,
            name="T",
            rx_frequency_hz=438_975_000,
            tone_mode=ToneMode.CTCSS_ENC,
            ctcss_tone_hz=118.8,
        )
        apply_cn_read_to_channel(ch, p2=0, number=0)
        self.assertAlmostEqual(ch.ctcss_tone_hz, 67.0)

    def test_format_cn_set_ctcss(self) -> None:
        cmd = format_cn_set(
            ToneMode.CTCSS_ENC, ctcss_hz=118.8, dcs_code=23
        )
        self.assertEqual(cmd, "CN00017;")

    def test_format_cn_set_dcs(self) -> None:
        cmd = format_cn_set(
            ToneMode.DCS_ENC, ctcss_hz=88.5, dcs_code=23
        )
        self.assertEqual(cmd, "CN01000;")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
