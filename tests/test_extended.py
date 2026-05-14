"""Tests für Version 0.5: erweiterte Audio-Einstellungen.

Deckt ab:
- Encoder/Decoder im Mapping-Modul (SSB-Cut, Carrier, Mic-Sel, DATA)
- ExtendedSettings-Serialisierung mit Defaults
- AudioProfile-Roundtrip inkl. Extended-Block
- FT991CAT read_extended / write_extended + Mode-spezifisches Lesen
"""

from __future__ import annotations

import json
import unittest
from typing import Dict, List

from cat.cat_errors import CatProtocolError
from cat.ft991_cat import FT991CAT, TxLockError
from cat.serial_cat import SerialCAT
from mapping.extended_mapping import (
    AM_CARRIER_MENU,
    AM_MIC_SEL_MENU,
    DATA_TX_LEVEL_MENU,
    EXTENDED_DEFS,
    EXTENDED_DEFS_BY_KEY,
    FM_CARRIER_MENU,
    FM_MIC_SEL_MENU,
    MicSource,
    SSB_HCUT_FREQ_MENU,
    SSB_HCUT_FREQS,
    SSB_LCUT_FREQ_MENU,
    SSB_LCUT_FREQS,
    SSB_LCUT_SLOPE_MENU,
    SSB_HCUT_SLOPE_MENU,
    SsbSlope,
    decode_ssb_freq,
    decode_ssb_slope,
    defs_for_mode,
    encode_carrier_level,
    encode_mic_source,
    encode_ssb_freq,
    encode_ssb_slope,
)
from model import AudioProfile, ExtendedSettings


# ----------------------------------------------------------------------
# Mapping
# ----------------------------------------------------------------------


class ExtendedMappingTest(unittest.TestCase):
    def test_lcut_freq_roundtrip(self) -> None:
        for value in SSB_LCUT_FREQS:
            raw = encode_ssb_freq(value, SSB_LCUT_FREQS)
            self.assertEqual(decode_ssb_freq(raw, SSB_LCUT_FREQS), value)
        # OFF muss case-insensitiv funktionieren
        self.assertEqual(encode_ssb_freq("off", SSB_LCUT_FREQS), "00")

    def test_lcut_freq_invalid(self) -> None:
        with self.assertRaises(ValueError):
            encode_ssb_freq(123, SSB_LCUT_FREQS)  # nicht in Tabelle
        with self.assertRaises(ValueError):
            decode_ssb_freq("99", SSB_LCUT_FREQS)  # ausserhalb Index

    def test_hcut_freq_extremes(self) -> None:
        self.assertEqual(encode_ssb_freq("OFF", SSB_HCUT_FREQS), "00")
        self.assertEqual(decode_ssb_freq("01", SSB_HCUT_FREQS), 700)
        self.assertEqual(decode_ssb_freq(f"{len(SSB_HCUT_FREQS)-1:02d}", SSB_HCUT_FREQS), 4000)

    def test_slope_roundtrip(self) -> None:
        # Slope ist laut Manual 1-stellig (EX105/107).
        self.assertEqual(encode_ssb_slope(SsbSlope.DB6), "0")
        self.assertEqual(encode_ssb_slope(SsbSlope.DB18), "1")
        # Decoder akzeptiert sowohl "0" als auch "00".
        self.assertEqual(decode_ssb_slope("0"), SsbSlope.DB6)
        self.assertEqual(decode_ssb_slope("00"), SsbSlope.DB6)
        self.assertEqual(decode_ssb_slope("1"), SsbSlope.DB18)
        # String-Eingaben auch
        self.assertEqual(encode_ssb_slope("6dB/oct"), "0")

    def test_slope_invalid(self) -> None:
        with self.assertRaises(ValueError):
            decode_ssb_slope("2")
        with self.assertRaises(ValueError):
            encode_ssb_slope("foo")

    def test_carrier_level_clamps(self) -> None:
        self.assertEqual(encode_carrier_level(50), "050")
        self.assertEqual(encode_carrier_level(-5), "000")
        self.assertEqual(encode_carrier_level(150), "100")

    def test_mic_source(self) -> None:
        # Mic Select ist laut Manual 1-stellig (EX045/074).
        self.assertEqual(encode_mic_source(MicSource.MIC), "0")
        self.assertEqual(encode_mic_source("REAR"), "1")
        with self.assertRaises(ValueError):
            encode_mic_source("bogus")


class ExtendedDefsTest(unittest.TestCase):
    def test_all_keys_unique(self) -> None:
        keys = [d.key for d in EXTENDED_DEFS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_menus_consistent(self) -> None:
        # Sanity: die Menünummern müssen mit den Modulkonstanten übereinstimmen
        by_menu = {d.key: d.menu for d in EXTENDED_DEFS}
        self.assertEqual(by_menu["ssb_lcut_freq"], SSB_LCUT_FREQ_MENU)
        self.assertEqual(by_menu["ssb_lcut_slope"], SSB_LCUT_SLOPE_MENU)
        self.assertEqual(by_menu["ssb_hcut_freq"], SSB_HCUT_FREQ_MENU)
        self.assertEqual(by_menu["ssb_hcut_slope"], SSB_HCUT_SLOPE_MENU)
        # EX106 (SSB MIC SELECT) und EX107 (SSB OUT LEVEL) werden bewusst
        # nicht verwaltet — siehe ExtendedSettings/extended_mapping.
        self.assertNotIn("ssb_mic_sel", by_menu)
        self.assertNotIn("ssb_out_level", by_menu)
        self.assertEqual(by_menu["am_carrier_level"], AM_CARRIER_MENU)
        self.assertEqual(by_menu["fm_carrier_level"], FM_CARRIER_MENU)
        self.assertEqual(by_menu["am_mic_sel"], AM_MIC_SEL_MENU)
        self.assertEqual(by_menu["fm_mic_sel"], FM_MIC_SEL_MENU)
        self.assertEqual(by_menu["data_tx_level"], DATA_TX_LEVEL_MENU)

    def test_mode_relevance(self) -> None:
        ssb_keys = {d.key for d in defs_for_mode("SSB")}
        self.assertIn("ssb_lcut_freq", ssb_keys)
        self.assertIn("ssb_hcut_slope", ssb_keys)
        # EX106/EX107 sind nicht (mehr) Teil des Mappings.
        self.assertNotIn("ssb_mic_sel", ssb_keys)
        self.assertNotIn("ssb_out_level", ssb_keys)
        self.assertNotIn("am_carrier_level", ssb_keys)

        am_keys = {d.key for d in defs_for_mode("AM")}
        self.assertEqual(am_keys, {"am_carrier_level", "am_mic_sel"})

        fm_keys = {d.key for d in defs_for_mode("FM")}
        self.assertEqual(fm_keys, {"fm_carrier_level", "fm_mic_sel"})

        data_keys = {d.key for d in defs_for_mode("DATA")}
        self.assertIn("data_tx_level", data_keys)
        self.assertIn("ssb_lcut_freq", data_keys)
        self.assertNotIn("ssb_mic_sel", data_keys)
        self.assertNotIn("ssb_out_level", data_keys)


# ----------------------------------------------------------------------
# Modell-Roundtrip
# ----------------------------------------------------------------------


class ExtendedSettingsModelTest(unittest.TestCase):
    def test_defaults(self) -> None:
        ext = ExtendedSettings()
        self.assertEqual(ext.ssb_lcut_freq, "OFF")
        self.assertEqual(ext.ssb_lcut_slope, SsbSlope.DB6.value)
        self.assertEqual(ext.am_mic_sel, MicSource.MIC.value)
        self.assertEqual(ext.data_tx_level, 50)

    def test_roundtrip(self) -> None:
        ext = ExtendedSettings(
            ssb_lcut_freq=200,
            ssb_lcut_slope=SsbSlope.DB18.value,
            ssb_hcut_freq=3000,
            ssb_hcut_slope=SsbSlope.DB18.value,
            am_carrier_level=80,
            fm_carrier_level=70,
            am_mic_sel=MicSource.REAR.value,
            fm_mic_sel=MicSource.MIC.value,
            data_tx_level=45,
        )
        decoded = ExtendedSettings.from_dict(ext.to_dict())
        self.assertEqual(decoded.ssb_lcut_freq, 200)
        self.assertEqual(decoded.am_carrier_level, 80)
        self.assertEqual(decoded.am_mic_sel, "REAR")

    def test_legacy_profile_without_extended(self) -> None:
        # Ein Profil aus 0.4 hatte noch keinen Extended-Block
        legacy = {
            "name": "Legacy",
            "mode_group": "SSB",
            "normal_eq": {
                "eq1": {"freq": 300, "level": -3, "bw": 5},
                "eq2": {"freq": 1200, "level": 2, "bw": 4},
                "eq3": {"freq": 2500, "level": 4, "bw": 3},
            },
        }
        profile = AudioProfile.from_dict(legacy)
        self.assertEqual(profile.extended.ssb_lcut_freq, "OFF")
        self.assertEqual(profile.extended.data_tx_level, 50)

    def test_profile_full_roundtrip_with_extended(self) -> None:
        ext = ExtendedSettings(
            ssb_lcut_freq=300,
            am_carrier_level=85,
            fm_mic_sel=MicSource.REAR.value,
            data_tx_level=60,
        )
        p = AudioProfile(name="Voll", mode_group="SSB", extended=ext)
        encoded = json.dumps(p.to_dict())
        decoded = AudioProfile.from_dict(json.loads(encoded))
        self.assertEqual(decoded.extended.ssb_lcut_freq, 300)
        self.assertEqual(decoded.extended.am_carrier_level, 85)
        self.assertEqual(decoded.extended.fm_mic_sel, "REAR")
        self.assertEqual(decoded.extended.data_tx_level, 60)


# ----------------------------------------------------------------------
# CAT-Integration
# ----------------------------------------------------------------------


class _ExtFakeRadio(SerialCAT):
    """Speichert EX-Menüs als Strings, Beantwortet TX und EX-Roundtrips."""

    def __init__(self) -> None:
        super().__init__()
        self.ex_store: Dict[int, str] = {}
        self.transmitting = False
        self.sent: List[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        self.sent.append(command)
        if command == "TX;":
            return "TX1;" if self.transmitting else "TX0;"
        if command.startswith("EX") and command.endswith(";"):
            body = command[2:-1]
            menu = int(body[:3])
            payload = body[3:]
            if payload == "":
                return f"EX{menu:03d}{self.ex_store.get(menu, '00')};"
            self.ex_store[menu] = payload
            return ""
        raise AssertionError(f"Unerwartetes Kommando: {command!r}")


class ExtendedCatTest(unittest.TestCase):
    def test_read_lcut_freq(self) -> None:
        radio = _ExtFakeRadio()
        # 50-Hz-Schritte: Index 05 = 300 Hz (100, 150, 200, 250, 300)
        radio.ex_store[SSB_LCUT_FREQ_MENU] = "05"
        ft = FT991CAT(radio)
        self.assertEqual(ft.read_extended("ssb_lcut_freq"), 300)

    def test_write_lcut_freq(self) -> None:
        radio = _ExtFakeRadio()
        ft = FT991CAT(radio)
        ft.write_extended("ssb_lcut_freq", 500)
        # 50-Hz-Schritte: 500 Hz = Index 09
        self.assertEqual(radio.ex_store[SSB_LCUT_FREQ_MENU], "09")

    def test_write_during_tx_blocked(self) -> None:
        radio = _ExtFakeRadio()
        radio.transmitting = True
        ft = FT991CAT(radio)
        with self.assertRaises(TxLockError):
            ft.write_extended("am_carrier_level", 80)
        self.assertNotIn(AM_CARRIER_MENU, radio.ex_store)

    def test_read_invalid_raw_raises_protocol_error(self) -> None:
        radio = _ExtFakeRadio()
        radio.ex_store[SSB_LCUT_FREQ_MENU] = "99"  # ausserhalb Tabelle
        ft = FT991CAT(radio)
        with self.assertRaises(CatProtocolError):
            ft.read_extended("ssb_lcut_freq")

    def test_read_extended_for_mode_ssb(self) -> None:
        radio = _ExtFakeRadio()
        # In 50-Hz-Schritten: Index 02 = 150 Hz
        radio.ex_store[SSB_LCUT_FREQ_MENU] = "02"
        radio.ex_store[SSB_LCUT_SLOPE_MENU] = "1"     # 18 dB (1-stellig)
        radio.ex_store[SSB_HCUT_FREQ_MENU] = "10"
        radio.ex_store[SSB_HCUT_SLOPE_MENU] = "0"
        ft = FT991CAT(radio)
        values = ft.read_extended_for_mode("SSB")
        self.assertEqual(values["ssb_lcut_freq"], 150)
        self.assertEqual(values["ssb_lcut_slope"], SsbSlope.DB18)
        # Keine AM/FM/DATA-Werte im SSB-Mode
        self.assertNotIn("am_carrier_level", values)
        self.assertNotIn("data_tx_level", values)

    def test_write_extended_for_mode_am(self) -> None:
        radio = _ExtFakeRadio()
        ft = FT991CAT(radio)
        ft.write_extended_for_mode("AM", {
            "am_carrier_level": 75,
            "am_mic_sel": "REAR",
            # SSB-Werte werden ignoriert, da nicht relevant für AM
            "ssb_lcut_freq": 100,
        })
        self.assertEqual(radio.ex_store[AM_CARRIER_MENU], "075")
        # Mic-Sel ist 1-stellig
        self.assertEqual(radio.ex_store[AM_MIC_SEL_MENU], "1")
        self.assertNotIn(SSB_LCUT_FREQ_MENU, radio.ex_store)

    def test_write_extended_for_mode_data_includes_ssb_cuts(self) -> None:
        """DATA hat sowohl SSB-Cut als auch DATA-spezifische Werte."""
        radio = _ExtFakeRadio()
        ft = FT991CAT(radio)
        ft.write_extended_for_mode("DATA", {
            "ssb_lcut_freq": 100,
            "ssb_hcut_freq": 4000,
            "data_tx_level": 65,
            # Nicht-relevante werden ignoriert
            "fm_carrier_level": 99,
        })
        self.assertEqual(radio.ex_store[SSB_LCUT_FREQ_MENU], "01")
        self.assertEqual(radio.ex_store[DATA_TX_LEVEL_MENU], "065")
        self.assertNotIn(FM_CARRIER_MENU, radio.ex_store)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
