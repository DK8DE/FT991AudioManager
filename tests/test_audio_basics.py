"""Tests für die Version 0.3-CAT-API: MIC Gain, Processor, EQ on/off, SSB BPF."""

from __future__ import annotations

import unittest
from typing import Dict, List

from cat.cat_errors import CatProtocolError
from cat.ft991_cat import FT991CAT, TxLockError
from cat.serial_cat import SerialCAT
from mapping.audio_mapping import (
    SSB_BPF_DEFAULT_KEY,
    format_pr_query,
    format_pr_set,
    parse_pr_response,
    ssb_bpf_decode_from_menu,
    ssb_bpf_encode_for_menu,
    ssb_bpf_index_to_key,
    ssb_bpf_key_to_index,
)


# ----------------------------------------------------------------------
# Fake-Serial mit MG/PR/PL/EX-Unterstützung
# ----------------------------------------------------------------------


class _FakeRadio(SerialCAT):
    """Simuliert die Antworten eines FT-991A für MG/PR/PL und EX-Menüs.

    PR-Konvention laut FT-991A-Manual: P2=0 ist OFF, P2=1 ist ON.
    """

    def __init__(self) -> None:
        super().__init__()
        self.transmitting = False
        self.mic_gain = 50
        self.processor_on = False
        self.mic_eq_on = True
        self.processor_level = 35
        self.ssb_bpf_index = 1   # "100-2900"
        self.ex_store: Dict[int, str] = {}
        self.sent: List[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        self.sent.append(command)
        # ---- TX-Status ----
        if command == "TX;":
            return "TX1;" if self.transmitting else "TX0;"
        # ---- MIC Gain ----
        if command == "MG;":
            return f"MG{self.mic_gain:03d};"
        if command.startswith("MG") and len(command) > 3 and command.endswith(";"):
            self.mic_gain = int(command[2:-1])
            return ""
        # ---- Processor Level ----
        if command == "PL;":
            return f"PL{self.processor_level:03d};"
        if command.startswith("PL") and len(command) > 3 and command.endswith(";"):
            self.processor_level = int(command[2:-1])
            return ""
        # ---- PR (Manual-Konvention FT-991A: 0=OFF, 1=ON) ----
        if command == "PR0;":
            return f"PR0{1 if self.processor_on else 0};"
        if command == "PR1;":
            return f"PR1{1 if self.mic_eq_on else 0};"
        if command in ("PR00;", "PR01;"):
            self.processor_on = command == "PR01;"
            return ""
        if command in ("PR10;", "PR11;"):
            self.mic_eq_on = command == "PR11;"
            return ""
        # ---- EX-Menüs ----
        if command.startswith("EX") and command.endswith(";"):
            body = command[2:-1]
            menu = int(body[:3])
            payload = body[3:]
            if payload == "":
                # Lesen — SSB TX BPF ist laut Manual 1-stellig (EX110).
                if menu == 110:
                    return f"EX110{self.ssb_bpf_index:d};"
                return f"EX{menu:03d}{self.ex_store.get(menu, '00')};"
            # Schreiben
            if menu == 110:
                self.ssb_bpf_index = int(payload)
            else:
                self.ex_store[menu] = payload
            return ""
        if command == "ID;":
            return "ID0570;"
        raise AssertionError(f"Unerwartetes Kommando: {command!r}")


# ----------------------------------------------------------------------
# Mapping-Tests
# ----------------------------------------------------------------------


class PrMappingTest(unittest.TestCase):
    """Manual-Konvention FT-991A: P2 = 0 (OFF) oder 1 (ON)."""

    def test_query_format(self) -> None:
        self.assertEqual(format_pr_query(0), "PR0;")
        self.assertEqual(format_pr_query(1), "PR1;")

    def test_set_format(self) -> None:
        self.assertEqual(format_pr_set(0, True),  "PR01;")
        self.assertEqual(format_pr_set(0, False), "PR00;")
        self.assertEqual(format_pr_set(1, True),  "PR11;")
        self.assertEqual(format_pr_set(1, False), "PR10;")

    def test_parse_canonical_states(self) -> None:
        self.assertTrue(parse_pr_response("PR01;", 0))
        self.assertFalse(parse_pr_response("PR00;", 0))
        self.assertTrue(parse_pr_response("PR11;", 1))
        self.assertFalse(parse_pr_response("PR10;", 1))

    def test_parse_legacy_two_state_tolerated_as_on(self) -> None:
        # Falls jemand mit der alten 1/2-Codierung am Radio gespielt hat:
        # ``2`` darf als ON durchgehen, damit der Haken im UI nicht
        # zurückspringt. Geschrieben wird trotzdem ``0/1``.
        self.assertTrue(parse_pr_response("PR02;", 0))
        self.assertTrue(parse_pr_response("PR12;", 1))

    def test_parse_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_pr_response("PR03;", 0)  # 3 ist kein gültiger State
        with self.assertRaises(ValueError):
            parse_pr_response("PR01", 0)   # ohne ;


class SsbBpfMappingTest(unittest.TestCase):
    def test_index_key_roundtrip(self) -> None:
        for i in range(5):
            key = ssb_bpf_index_to_key(i)
            self.assertEqual(ssb_bpf_key_to_index(key), i)

    def test_encode_decode(self) -> None:
        # Laut Manual ist EX112 1-stellig.
        self.assertEqual(ssb_bpf_encode_for_menu("100-2900"), "1")
        # Decoder ist tolerant gegen ein- oder zweistellige Rohwerte.
        self.assertEqual(ssb_bpf_decode_from_menu("1"), "100-2900")
        self.assertEqual(ssb_bpf_decode_from_menu("01"), "100-2900")
        self.assertEqual(ssb_bpf_decode_from_menu("4"), "400-2600")

    def test_default_key_is_valid(self) -> None:
        self.assertEqual(ssb_bpf_key_to_index(SSB_BPF_DEFAULT_KEY), 1)

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            ssb_bpf_index_to_key(9)
        with self.assertRaises(ValueError):
            ssb_bpf_decode_from_menu("9")
        with self.assertRaises(ValueError):
            ssb_bpf_decode_from_menu("ab")


# ----------------------------------------------------------------------
# FT991CAT-Integration
# ----------------------------------------------------------------------


class MicGainCatTest(unittest.TestCase):
    def test_get(self) -> None:
        radio = _FakeRadio()
        radio.mic_gain = 73
        ft = FT991CAT(radio)
        self.assertEqual(ft.get_mic_gain(), 73)
        self.assertIn("MG;", radio.sent)

    def test_set_checks_tx(self) -> None:
        radio = _FakeRadio()
        ft = FT991CAT(radio)
        ft.set_mic_gain(40)
        self.assertEqual(radio.mic_gain, 40)
        self.assertIn("TX;", radio.sent)
        self.assertIn("MG040;", radio.sent)

    def test_set_blocked_during_tx(self) -> None:
        radio = _FakeRadio()
        radio.transmitting = True
        ft = FT991CAT(radio)
        with self.assertRaises(TxLockError):
            ft.set_mic_gain(40)
        # Es darf KEIN MG-Set abgesetzt worden sein.
        self.assertNotIn("MG040;", radio.sent)

    def test_set_clamps_out_of_range(self) -> None:
        radio = _FakeRadio()
        ft = FT991CAT(radio)
        ft.set_mic_gain(150)
        self.assertEqual(radio.mic_gain, 100)
        ft.set_mic_gain(-99)
        self.assertEqual(radio.mic_gain, 0)


class ProcessorCatTest(unittest.TestCase):
    def test_get_enabled(self) -> None:
        radio = _FakeRadio()
        radio.processor_on = True
        ft = FT991CAT(radio)
        self.assertTrue(ft.get_processor_enabled())

    def test_set_enabled(self) -> None:
        radio = _FakeRadio()
        ft = FT991CAT(radio)
        ft.set_processor_enabled(True)
        self.assertTrue(radio.processor_on)
        ft.set_processor_enabled(False)
        self.assertFalse(radio.processor_on)

    def test_processor_level(self) -> None:
        radio = _FakeRadio()
        radio.processor_level = 42
        ft = FT991CAT(radio)
        self.assertEqual(ft.get_processor_level(), 42)
        ft.set_processor_level(60)
        self.assertEqual(radio.processor_level, 60)

    def test_set_during_tx_blocked(self) -> None:
        radio = _FakeRadio()
        radio.transmitting = True
        ft = FT991CAT(radio)
        with self.assertRaises(TxLockError):
            ft.set_processor_enabled(True)
        with self.assertRaises(TxLockError):
            ft.set_processor_level(10)


class MicEqOnOffTest(unittest.TestCase):
    def test_get_set(self) -> None:
        radio = _FakeRadio()
        ft = FT991CAT(radio)
        self.assertTrue(ft.get_mic_eq_enabled())
        ft.set_mic_eq_enabled(False)
        self.assertFalse(radio.mic_eq_on)
        # OFF = "0" laut Manual.
        self.assertIn("PR10;", radio.sent)
        ft.set_mic_eq_enabled(True)
        self.assertIn("PR11;", radio.sent)


class SsbBpfCatTest(unittest.TestCase):
    def test_get(self) -> None:
        radio = _FakeRadio()
        radio.ssb_bpf_index = 3
        ft = FT991CAT(radio)
        self.assertEqual(ft.get_ssb_bpf(), "300-2700")

    def test_set(self) -> None:
        radio = _FakeRadio()
        ft = FT991CAT(radio)
        ft.set_ssb_bpf("400-2600")
        self.assertEqual(radio.ssb_bpf_index, 4)
        # Manual: EX110 ist 1-stellig -> EX1104;
        self.assertIn("EX1104;", radio.sent)

    def test_set_unknown_key_raises(self) -> None:
        radio = _FakeRadio()
        ft = FT991CAT(radio)
        with self.assertRaises(ValueError):
            ft.set_ssb_bpf("9999-9999")


class ProcessorEqRoundtripTest(unittest.TestCase):
    def test_processor_eq_uses_menus_128_136(self) -> None:
        """Laut Manual (1711-D): Processor-EQ ist EX128..EX136 mit
        Slot-Reihenfolge **Freq / Level / BW** pro Band.
        """
        radio = _FakeRadio()
        # Slots: 128/129/130 (EQ1), 131/132/133 (EQ2), 134/135/136 (EQ3)
        # EQ1 LOW:  Freq=03 (300 Hz), Level=-02, BW=05
        # EQ2 MID:  Freq=04 (1000 Hz, idx 4 MID), Level=+03, BW=04
        # EQ3 HIGH: Freq=13 (2700 Hz, idx 13 HIGH), Level=+05, BW=03
        radio.ex_store.update({
            128: "03", 129: "-02", 130: "05",
            131: "04", 132: "+03", 133: "04",
            134: "13", 135: "+05", 136: "03",
        })
        ft = FT991CAT(radio)
        eq = ft.read_processor_eq()
        self.assertEqual(eq.eq1.freq, 300)
        self.assertEqual(eq.eq1.bw, 5)
        self.assertEqual(eq.eq1.level, -2)
        self.assertEqual(eq.eq2.freq, 1000)
        self.assertEqual(eq.eq2.level, 3)
        self.assertEqual(eq.eq3.freq, 2700)
        self.assertEqual(eq.eq3.level, 5)


class ProtocolErrorTest(unittest.TestCase):
    def test_garbled_mg_response(self) -> None:
        radio = _FakeRadio()
        # Override send_command, um eine kaputte Antwort zu liefern
        original = radio.send_command

        def odd(cmd: str, *, read_response: bool = True):
            if cmd == "MG;":
                return "MG??;"
            return original(cmd, read_response=read_response)

        radio.send_command = odd  # type: ignore[method-assign]
        ft = FT991CAT(radio)
        with self.assertRaises(CatProtocolError):
            ft.get_mic_gain()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
