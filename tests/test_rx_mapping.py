"""Tests für ``mapping/rx_mapping.py`` und die zugehörigen ``FT991CAT``-Methoden."""

from __future__ import annotations

import unittest
from typing import Dict, List

from cat.cat_errors import CatProtocolError
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.rx_mapping import (
    AgcMode,
    RxMode,
    format_af_gain_query,
    format_agc_query,
    format_auto_notch_query,
    format_frequency_b_query,
    format_frequency_hz,
    format_frequency_query,
    format_mode_query,
    format_nb_level_query,
    format_nb_query,
    format_nr_level_query,
    format_nr_query,
    format_rf_gain_query,
    format_squelch_query,
    mode_group_for,
    parse_af_gain_response,
    parse_agc_response,
    parse_auto_notch_response,
    parse_frequency_b_response,
    parse_frequency_response,
    parse_mode_response,
    parse_nb_level_response,
    parse_nb_response,
    parse_nr_level_response,
    parse_nr_response,
    parse_rf_gain_response,
    parse_squelch_response,
)


# ----------------------------------------------------------------------
# Mapping-Tests (reine Encode/Decode)
# ----------------------------------------------------------------------


class AgcMappingTest(unittest.TestCase):
    def test_query(self) -> None:
        self.assertEqual(format_agc_query(), "GT0;")

    def test_parse(self) -> None:
        # FT-991A verwendet intern das FTDX-Schema mit drei AUTO-Sub-Modi
        # (4=AUTO-F, 5=AUTO-M, 6=AUTO-S). Für die GUI sind alle gleich „AUTO".
        self.assertEqual(parse_agc_response("GT00;"), AgcMode.OFF)
        self.assertEqual(parse_agc_response("GT01;"), AgcMode.FAST)
        self.assertEqual(parse_agc_response("GT02;"), AgcMode.MID)
        self.assertEqual(parse_agc_response("GT03;"), AgcMode.SLOW)
        self.assertEqual(parse_agc_response("GT04;"), AgcMode.AUTO)
        self.assertEqual(parse_agc_response("GT05;"), AgcMode.AUTO)
        self.assertEqual(parse_agc_response("GT06;"), AgcMode.AUTO)

    def test_parse_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_agc_response("GT07;")     # Index außer Tabelle
        with self.assertRaises(ValueError):
            parse_agc_response("GT0X;")     # nicht-numerisch
        with self.assertRaises(ValueError):
            parse_agc_response("GT0")       # ohne ;


class ModeMappingTest(unittest.TestCase):
    def test_query(self) -> None:
        self.assertEqual(format_mode_query(), "MD0;")

    def test_parse_known(self) -> None:
        self.assertEqual(parse_mode_response("MD01;"), RxMode.LSB)
        self.assertEqual(parse_mode_response("MD02;"), RxMode.USB)
        self.assertEqual(parse_mode_response("MD05;"), RxMode.AM)
        self.assertEqual(parse_mode_response("MD0C;"), RxMode.DATA_USB)
        self.assertEqual(parse_mode_response("MD0E;"), RxMode.C4FM)

    def test_parse_unknown_returns_marker(self) -> None:
        self.assertEqual(parse_mode_response("MD0F;"), RxMode.UNKNOWN)

    def test_mode_group(self) -> None:
        self.assertEqual(mode_group_for(RxMode.USB), "SSB")
        self.assertEqual(mode_group_for(RxMode.AM_N), "AM")
        self.assertEqual(mode_group_for(RxMode.DATA_USB), "DATA")
        self.assertEqual(mode_group_for(RxMode.C4FM), "C4FM")
        self.assertEqual(mode_group_for(RxMode.UNKNOWN), "OTHER")


class FrequencyMappingTest(unittest.TestCase):
    def test_query(self) -> None:
        self.assertEqual(format_frequency_query(), "FA;")
        self.assertEqual(format_frequency_b_query(), "FB;")

    def test_parse(self) -> None:
        self.assertEqual(parse_frequency_response("FA014250000;"), 14_250_000)
        self.assertEqual(parse_frequency_response("FA007074000;"), 7_074_000)

    def test_parse_vfob(self) -> None:
        self.assertEqual(parse_frequency_b_response("FB014280000;"), 14_280_000)
        self.assertEqual(parse_frequency_b_response("FB000136000;"), 136_000)

    def test_parse_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_frequency_response("FA12345678;")     # zu kurz
        with self.assertRaises(ValueError):
            parse_frequency_response("FA1234567890;")   # zu lang
        with self.assertRaises(ValueError):
            parse_frequency_response("XX014250000;")    # falscher Prefix
        with self.assertRaises(ValueError):
            parse_frequency_b_response("FA014250000;")  # VFO-A statt VFO-B

    def test_format_hz(self) -> None:
        self.assertEqual(format_frequency_hz(14_250_000), "14.250000 MHz")
        self.assertEqual(format_frequency_hz(7_074_000), "7.074000 MHz")


class LevelMappingTest(unittest.TestCase):
    def test_squelch(self) -> None:
        self.assertEqual(format_squelch_query(), "SQ0;")
        self.assertEqual(parse_squelch_response("SQ0050;"), 50)
        self.assertEqual(parse_squelch_response("SQ0000;"), 0)
        self.assertEqual(parse_squelch_response("SQ0100;"), 100)

    def test_af_gain(self) -> None:
        self.assertEqual(format_af_gain_query(), "AG0;")
        self.assertEqual(parse_af_gain_response("AG0128;"), 128)

    def test_rf_gain(self) -> None:
        self.assertEqual(format_rf_gain_query(), "RG0;")
        self.assertEqual(parse_rf_gain_response("RG0255;"), 255)

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_squelch_response("SQ050;")        # zu kurz
        with self.assertRaises(ValueError):
            parse_af_gain_response("AG0XYZ;")       # nicht-numerisch
        with self.assertRaises(ValueError):
            parse_rf_gain_response("RG255;")        # falscher Prefix


class DspMappingTest(unittest.TestCase):
    def test_noise_blanker(self) -> None:
        self.assertEqual(format_nb_query(), "NB0;")
        self.assertTrue(parse_nb_response("NB01;"))
        self.assertFalse(parse_nb_response("NB00;"))
        with self.assertRaises(ValueError):
            parse_nb_response("NB02;")

    def test_noise_blanker_level(self) -> None:
        self.assertEqual(format_nb_level_query(), "NL0;")
        self.assertEqual(parse_nb_level_response("NL0005;"), 5)
        self.assertEqual(parse_nb_level_response("NL0010;"), 10)

    def test_noise_reduction(self) -> None:
        self.assertEqual(format_nr_query(), "NR0;")
        self.assertTrue(parse_nr_response("NR01;"))
        self.assertFalse(parse_nr_response("NR00;"))

    def test_noise_reduction_level(self) -> None:
        self.assertEqual(format_nr_level_query(), "RL0;")
        # RL nutzt 2-stellige Werte (01-15)!
        self.assertEqual(parse_nr_level_response("RL001;"), 1)
        self.assertEqual(parse_nr_level_response("RL015;"), 15)

    def test_auto_notch(self) -> None:
        self.assertEqual(format_auto_notch_query(), "BC0;")
        self.assertTrue(parse_auto_notch_response("BC01;"))
        self.assertFalse(parse_auto_notch_response("BC00;"))


class DspSetterMappingTest(unittest.TestCase):
    """Setter, die das MeterWidget nutzt, wenn der User an den DSP-Slidern zieht."""

    def test_nb_on_off(self) -> None:
        from mapping.rx_mapping import format_nb_set
        self.assertEqual(format_nb_set(True), "NB01;")
        self.assertEqual(format_nb_set(False), "NB00;")

    def test_nb_level_clamps_and_formats_three_digits(self) -> None:
        from mapping.rx_mapping import format_nb_level_set
        self.assertEqual(format_nb_level_set(0), "NL0000;")
        self.assertEqual(format_nb_level_set(5), "NL0005;")
        self.assertEqual(format_nb_level_set(10), "NL0010;")
        # Out-of-range wird geklemmt — kein ValueError.
        self.assertEqual(format_nb_level_set(-3), "NL0000;")
        self.assertEqual(format_nb_level_set(99), "NL0010;")

    def test_nr_on_off(self) -> None:
        from mapping.rx_mapping import format_nr_set
        self.assertEqual(format_nr_set(True), "NR01;")
        self.assertEqual(format_nr_set(False), "NR00;")

    def test_nr_level_clamps_and_formats_two_digits(self) -> None:
        from mapping.rx_mapping import format_nr_level_set
        self.assertEqual(format_nr_level_set(1), "RL001;")
        self.assertEqual(format_nr_level_set(15), "RL015;")
        # Außerhalb geklemmt (Range ist 1..15).
        self.assertEqual(format_nr_level_set(0), "RL001;")
        self.assertEqual(format_nr_level_set(20), "RL015;")

    def test_auto_notch_on_off(self) -> None:
        from mapping.rx_mapping import format_auto_notch_set
        self.assertEqual(format_auto_notch_set(True), "BC01;")
        self.assertEqual(format_auto_notch_set(False), "BC00;")


class AgcSliderMappingTest(unittest.TestCase):
    """Mapping zwischen dem 4-Positionen-Slider und ``AgcMode``."""

    def test_format_agc_set_uses_device_indices(self) -> None:
        from mapping.rx_mapping import AgcMode, format_agc_set
        # FT-991A-Codierung (CAT Ref. 1612-C, P2):
        #   0=OFF, 1=FAST, 2=MID, 3=SLOW, 4=AUTO.
        self.assertEqual(format_agc_set(AgcMode.OFF), "GT00;")
        self.assertEqual(format_agc_set(AgcMode.FAST), "GT01;")
        self.assertEqual(format_agc_set(AgcMode.MID), "GT02;")
        self.assertEqual(format_agc_set(AgcMode.SLOW), "GT03;")
        self.assertEqual(format_agc_set(AgcMode.AUTO), "GT04;")

    def test_slider_position_mapping(self) -> None:
        from mapping.rx_mapping import (
            AGC_SLIDER_LABELS,
            AGC_SLIDER_MODES,
            AgcMode,
            agc_mode_to_slider_pos,
        )
        self.assertEqual(AGC_SLIDER_LABELS, ("AUTO", "FAST", "MID", "SLOW"))
        self.assertEqual(AGC_SLIDER_MODES[0], AgcMode.AUTO)
        self.assertEqual(AGC_SLIDER_MODES[1], AgcMode.FAST)
        self.assertEqual(AGC_SLIDER_MODES[2], AgcMode.MID)
        self.assertEqual(AGC_SLIDER_MODES[3], AgcMode.SLOW)

        self.assertEqual(agc_mode_to_slider_pos(AgcMode.AUTO), 0)
        self.assertEqual(agc_mode_to_slider_pos(AgcMode.FAST), 1)
        self.assertEqual(agc_mode_to_slider_pos(AgcMode.MID), 2)
        self.assertEqual(agc_mode_to_slider_pos(AgcMode.SLOW), 3)
        # OFF / unbekannt -> Slider neutral.
        self.assertEqual(agc_mode_to_slider_pos(AgcMode.OFF), -1)


# ----------------------------------------------------------------------
# FT991CAT-Integration mit Fake-Radio
# ----------------------------------------------------------------------


class _RxFakeRadio(SerialCAT):
    """Minimaler Fake, der die RX-CAT-Antworten zurückspielt."""

    def __init__(self) -> None:
        super().__init__()
        self.responses: Dict[str, str] = {
            "SM0;": "SM0084;",       # ≈ S9
            "SQ0;": "SQ0030;",
            "AG0;": "AG0200;",
            "RG0;": "RG0255;",
            "GT0;": "GT03;",         # SLOW
            "NB0;": "NB01;",
            "NL0;": "NL0005;",
            "NR0;": "NR00;",
            "RL0;": "RL003;",
            "BC0;": "BC00;",
            "MD0;": "MD02;",         # USB
            "FA;":  "FA014250000;",
            "FB;":  "FB014280000;",
        }
        self.sent: List[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        self.sent.append(command)
        try:
            return self.responses[command]
        except KeyError as exc:
            raise AssertionError(f"Unerwartetes Kommando: {command!r}") from exc


class FT991CatRxTest(unittest.TestCase):
    def test_smeter_and_levels(self) -> None:
        ft = FT991CAT(_RxFakeRadio())
        self.assertEqual(ft.read_smeter(), 84)
        self.assertEqual(ft.read_squelch(), 30)
        self.assertEqual(ft.read_af_gain(), 200)
        self.assertEqual(ft.read_rf_gain(), 255)

    def test_dsp_status(self) -> None:
        ft = FT991CAT(_RxFakeRadio())
        self.assertEqual(ft.read_agc(), AgcMode.SLOW)
        self.assertTrue(ft.read_noise_blanker())
        self.assertEqual(ft.read_noise_blanker_level(), 5)
        self.assertFalse(ft.read_noise_reduction())
        self.assertEqual(ft.read_noise_reduction_level(), 3)
        self.assertFalse(ft.read_auto_notch())

    def test_mode_and_freq(self) -> None:
        ft = FT991CAT(_RxFakeRadio())
        self.assertEqual(ft.read_rx_mode(), RxMode.USB)
        self.assertEqual(ft.read_frequency(), 14_250_000)
        self.assertEqual(ft.read_frequency_b(), 14_280_000)

    def test_get_mode_wrapper(self) -> None:
        ft = FT991CAT(_RxFakeRadio())
        self.assertEqual(ft.get_mode(), "USB")

    def test_unparseable_response_raises_protocol_error(self) -> None:
        radio = _RxFakeRadio()
        radio.responses["SM0;"] = "SM0XYZ;"
        ft = FT991CAT(radio)
        with self.assertRaises(CatProtocolError):
            ft.read_smeter()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
