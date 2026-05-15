"""Tests fuer ``mapping/memory_mapping.py`` — MT/MC/VM-Befehle."""

from __future__ import annotations

import unittest

from mapping.memory_mapping import (
    MEMORY_CHANNEL_MAX,
    MEMORY_CHANNEL_MIN,
    MemoryChannel,
    format_mc_query,
    format_mc_set,
    format_mt_query,
    format_vm_set,
    parse_mc_response,
    parse_mt_or_empty,
    parse_mt_response,
)
from mapping.rx_mapping import RxMode


class FormatQueriesTest(unittest.TestCase):
    def test_mt_query_zero_pads_channel(self) -> None:
        self.assertEqual(format_mt_query(1), "MT001;")
        self.assertEqual(format_mt_query(12), "MT012;")
        self.assertEqual(format_mt_query(117), "MT117;")

    def test_mc_set_zero_pads_channel(self) -> None:
        self.assertEqual(format_mc_set(7), "MC007;")
        self.assertEqual(format_mc_set(117), "MC117;")

    def test_mc_query_is_simple(self) -> None:
        self.assertEqual(format_mc_query(), "MC;")

    def test_invalid_channel_raises(self) -> None:
        with self.assertRaises(ValueError):
            format_mt_query(0)
        with self.assertRaises(ValueError):
            format_mt_query(MEMORY_CHANNEL_MAX + 1)
        with self.assertRaises(ValueError):
            format_mc_set(0)
        with self.assertRaises(ValueError):
            format_mc_set(MEMORY_CHANNEL_MAX + 1)

    def test_vm_set_distinguishes_modes(self) -> None:
        self.assertEqual(format_vm_set(False), "VM0;")
        self.assertEqual(format_vm_set(True), "VM1;")

    def test_channel_range_is_117(self) -> None:
        # Schutz vor versehentlicher Aenderung der Konstanten — die
        # Manuale geben 001..117 vor.
        self.assertEqual(MEMORY_CHANNEL_MIN, 1)
        self.assertEqual(MEMORY_CHANNEL_MAX, 117)


class ParseMtResponseTest(unittest.TestCase):
    # Echte FT-991/A-Antwort (41 Zeichen, Body 38):
    #   MT <ch:3> <freq:9> <clar-sign:1> <clar-offset:4>
    #      <rx-clar:1> <tx-clar:1> <mode:1> <p8:1> <p9:5> <tag:12> ;
    # Mode steht an Body-Position 19.

    def test_parse_relais_example(self) -> None:
        # VHF-Relais mit Tag "RELAIS DB0XX" und Mode "4" (=FM) an Pos. 19.
        response = "MT012145500000+0000004100000RELAIS DB0XX;"
        result = parse_mt_response(response)
        self.assertEqual(result.channel, 12)
        self.assertEqual(result.frequency_hz, 145_500_000)
        self.assertEqual(result.tag, "RELAIS DB0XX")
        self.assertEqual(result.mode, RxMode.FM)
        self.assertFalse(result.is_empty)

    def test_parse_real_ft991_response(self) -> None:
        # Genauer Mitschnitt aus einem FT-991-Log:
        # CH008 "Free 4" 149.0875 MHz FM.
        response = "MT008149087500+0000004100000Free 4      ;"
        result = parse_mt_response(response)
        self.assertEqual(result.channel, 8)
        self.assertEqual(result.frequency_hz, 149_087_500)
        self.assertEqual(result.tag, "Free 4")
        self.assertEqual(result.mode, RxMode.FM)

    def test_parse_empty_slot_response(self) -> None:
        # Leerer Slot: Frequenz 0, Tag = 12 Leerzeichen.
        response = "MT042000000000+0000000000000            ;"
        result = parse_mt_response(response)
        self.assertEqual(result.channel, 42)
        self.assertEqual(result.frequency_hz, 0)
        self.assertEqual(result.tag, "")
        self.assertTrue(result.is_empty)

    def test_parse_mt_or_empty_returns_none_for_empty(self) -> None:
        response = "MT042000000000+0000000000000            ;"
        self.assertIsNone(parse_mt_or_empty(response))

    def test_parse_mt_or_empty_returns_channel_for_filled(self) -> None:
        response = "MT012145500000+0000004100000RELAIS DB0XX;"
        result = parse_mt_or_empty(response)
        self.assertIsNotNone(result)
        assert result is not None  # for mypy
        self.assertEqual(result.tag, "RELAIS DB0XX")

    def test_tag_trailing_spaces_are_stripped(self) -> None:
        # SSB-Memory auf 14.020 MHz, Mode "1" (=LSB).
        response = "MT001014020000+0000001000000DL0AB       ;"
        result = parse_mt_response(response)
        self.assertEqual(result.tag, "DL0AB")
        self.assertEqual(result.frequency_hz, 14_020_000)
        self.assertEqual(result.mode, RxMode.LSB)

    def test_invalid_response_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_mt_response("XX001;")
        with self.assertRaises(ValueError):
            parse_mt_response("MT001;")  # zu kurz
        with self.assertRaises(ValueError):
            # Channel-Feld nicht numerisch
            parse_mt_response("MT0AB145500000+0000004100000RELAIS DB0XX;")


class ParseMcResponseTest(unittest.TestCase):
    def test_parse_valid(self) -> None:
        self.assertEqual(parse_mc_response("MC001;"), 1)
        self.assertEqual(parse_mc_response("MC117;"), 117)

    def test_parse_invalid_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_mc_response("MC1;")
        with self.assertRaises(ValueError):
            parse_mc_response("MC0AB;")
        with self.assertRaises(ValueError):
            parse_mc_response("ZZ001;")


class MemoryChannelDataclassTest(unittest.TestCase):
    def test_is_empty_only_when_both_freq_and_tag_are_blank(self) -> None:
        empty = MemoryChannel(channel=5, frequency_hz=0, mode=RxMode.UNKNOWN, tag="")
        self.assertTrue(empty.is_empty)
        with_tag = MemoryChannel(channel=5, frequency_hz=0, mode=RxMode.UNKNOWN, tag="X")
        self.assertFalse(with_tag.is_empty)
        with_freq = MemoryChannel(
            channel=5, frequency_hz=145_000_000, mode=RxMode.FM, tag=""
        )
        self.assertFalse(with_freq.is_empty)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
