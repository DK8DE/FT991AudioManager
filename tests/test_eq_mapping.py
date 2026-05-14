"""Tests für mapping.menu_mapping und mapping.eq_mapping."""

from __future__ import annotations

import unittest

from mapping.menu_mapping import format_ex_read, format_ex_write, parse_ex_response
from mapping.eq_mapping import (
    BW_MAX,
    BW_MIN,
    EQ_HIGH_FREQS,
    EQ_LOW_FREQS,
    EQ_MID_FREQS,
    LEVEL_DB_MAX,
    LEVEL_DB_MIN,
    decode_bw,
    decode_freq,
    decode_level,
    encode_bw,
    encode_freq,
    encode_level,
    freq_choices,
    freq_to_label,
    label_to_freq,
)


class MenuMappingTest(unittest.TestCase):
    def test_format_ex_read(self) -> None:
        self.assertEqual(format_ex_read(121), "EX121;")
        self.assertEqual(format_ex_read(7), "EX007;")

    def test_format_ex_write(self) -> None:
        self.assertEqual(format_ex_write(121, "03"), "EX12103;")
        self.assertEqual(format_ex_write(122, "10"), "EX12210;")

    def test_format_ex_write_invalid(self) -> None:
        with self.assertRaises(ValueError):
            format_ex_write(121, "")
        with self.assertRaises(ValueError):
            format_ex_write(121, "01;")
        with self.assertRaises(ValueError):
            format_ex_write(1000, "01")

    def test_parse_ex_response(self) -> None:
        self.assertEqual(parse_ex_response("EX12103;", 121), "03")
        self.assertEqual(parse_ex_response("EX0070123;", 7), "0123")

    def test_parse_ex_response_errors(self) -> None:
        # mapping.menu_mapping wirft ValueError; cat.ft991_cat konvertiert
        # das beim Aufruf in CatProtocolError (siehe test_ft991_eq).
        with self.assertRaises(ValueError):
            parse_ex_response("EX12103", 121)  # ohne ;
        with self.assertRaises(ValueError):
            parse_ex_response("EX12203;", 121)  # falsches Menü
        with self.assertRaises(ValueError):
            parse_ex_response("EX121;", 121)  # ohne Wertanteil


class FrequencyMappingTest(unittest.TestCase):
    def test_off_roundtrip_for_all_bands(self) -> None:
        for band in (0, 1, 2):
            self.assertEqual(encode_freq("OFF", band), "00")
            self.assertEqual(decode_freq("00", band), "OFF")

    def test_low_band_specific(self) -> None:
        # LOW-Band geht laut Manual von OFF + 100..700 Hz in 100-Hz-Schritten.
        self.assertEqual(encode_freq(300, 0), "03")
        self.assertEqual(decode_freq("03", 0), 300)
        # Höchste Frequenz: 700 Hz (Index 7)
        self.assertEqual(decode_freq("07", 0), 700)
        self.assertEqual(encode_freq(700, 0), "07")
        # 1000 Hz ist im LOW-Band **nicht** mehr erlaubt
        with self.assertRaises(ValueError):
            encode_freq(1000, 0)

    def test_invalid_freq(self) -> None:
        with self.assertRaises(ValueError):
            encode_freq(99999, 0)
        with self.assertRaises(ValueError):
            decode_freq("99", 0)

    def test_freq_choices_have_off_first(self) -> None:
        for band in (0, 1, 2):
            choices = freq_choices(band)
            self.assertEqual(choices[0], "OFF")
            self.assertGreater(len(choices), 1)

    def test_freq_label_roundtrip(self) -> None:
        self.assertEqual(freq_to_label(300), "300 Hz")
        self.assertEqual(freq_to_label("OFF"), "OFF")
        self.assertEqual(label_to_freq("300 Hz"), 300)
        self.assertEqual(label_to_freq("OFF"), "OFF")

    def test_tables_have_expected_first_entries(self) -> None:
        self.assertEqual(EQ_LOW_FREQS[0], "OFF")
        self.assertEqual(EQ_MID_FREQS[0], "OFF")
        self.assertEqual(EQ_HIGH_FREQS[0], "OFF")


class LevelMappingTest(unittest.TestCase):
    """Level-Range laut Manual: -20..+10 dB (asymmetrisch!)."""

    def test_zero_db(self) -> None:
        self.assertEqual(encode_level(0), "+00")
        self.assertEqual(decode_level("+00"), 0)

    def test_extremes(self) -> None:
        self.assertEqual(LEVEL_DB_MIN, -20)
        self.assertEqual(LEVEL_DB_MAX, +10)
        self.assertEqual(encode_level(LEVEL_DB_MAX), "+10")
        self.assertEqual(encode_level(LEVEL_DB_MIN), "-20")
        self.assertEqual(decode_level("-20"), LEVEL_DB_MIN)
        self.assertEqual(decode_level("+10"), LEVEL_DB_MAX)

    def test_clamping(self) -> None:
        self.assertEqual(encode_level(99), "+10")
        self.assertEqual(encode_level(-99), "-20")

    def test_negative_db(self) -> None:
        self.assertEqual(encode_level(-3), "-03")
        self.assertEqual(decode_level("-03"), -3)
        # -15 ist neu erlaubt (war früher außerhalb des Bereichs)
        self.assertEqual(encode_level(-15), "-15")
        self.assertEqual(decode_level("-15"), -15)

    def test_positive_db(self) -> None:
        self.assertEqual(encode_level(5), "+05")
        self.assertEqual(decode_level("+05"), 5)

    def test_decode_tolerates_unsigned(self) -> None:
        # Manche Geräte könnten die Antwort auch ohne Vorzeichen liefern.
        self.assertEqual(decode_level("02"), 2)

    def test_decode_out_of_range_raises(self) -> None:
        # +11 dB ist außerhalb des Manual-Bereichs
        with self.assertRaises(ValueError):
            decode_level("+11")
        # -21 dB ebenfalls
        with self.assertRaises(ValueError):
            decode_level("-21")


class BandwidthMappingTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        for bw in range(BW_MIN, BW_MAX + 1):
            raw = encode_bw(bw)
            self.assertEqual(decode_bw(raw), bw)

    def test_clamping(self) -> None:
        self.assertEqual(encode_bw(99), f"{BW_MAX:02d}")
        self.assertEqual(encode_bw(-3), f"{BW_MIN:02d}")

    def test_invalid_decode(self) -> None:
        with self.assertRaises(ValueError):
            decode_bw("ab")
        with self.assertRaises(ValueError):
            decode_bw("99")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
