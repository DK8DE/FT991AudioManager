"""Tests für die Diff-Write-Funktionalität in FT991CAT.

Wir nutzen das aus ``test_ft991_eq.py`` bewährte ``_FakeSerialCAT`` und
zählen die EX-Schreibkommandos, die tatsächlich am Bus gelandet sind.
"""

from __future__ import annotations

import unittest
from typing import Dict, List, Tuple

from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.eq_mapping import NORMAL_EQ_MENUS, PROCESSOR_EQ_MENUS
from model import EQBand, EQSettings


class _FakeSerialCAT(SerialCAT):
    """Schlanker Fake, der EX-Lesevorgänge gegen einen Store erfüllt und
    Schreibvorgänge mitzählt."""

    def __init__(self) -> None:
        super().__init__()
        self.store: Dict[int, str] = {}
        self.writes: List[Tuple[int, str]] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True) -> str:  # type: ignore[override]
        if command == "TX;":
            return "TX0;"
        if command == "ID;":
            return "ID0570;"
        if command.startswith("EX") and command.endswith(";"):
            body = command[2:-1]
            menu = int(body[:3])
            payload = body[3:]
            if payload == "":
                return f"EX{menu:03d}{self.store.get(menu, '00')};"
            self.store[menu] = payload
            self.writes.append((menu, payload))
            return ""
        # MG / PL / PR / SH usw. → Schreibvorgänge mitzählen, kein Echo.
        if any(command.startswith(p) for p in ("MG", "PL", "PR", "SH", "BD", "BU")):
            # Lesen?
            if command.endswith("0;") and len(command) <= 4:
                # Format-Variante MG0;? eher nicht — hier keine Reads erwartet.
                return ""
            return ""
        raise AssertionError(f"Unerwartetes Kommando: {command!r}")


# ----------------------------------------------------------------------
# write_eq mit baseline
# ----------------------------------------------------------------------


def _make_eq(level_low: int = 0, level_mid: int = 0, level_high: int = 0) -> EQSettings:
    return EQSettings(
        eq1=EQBand(freq=200, level=level_low, bw=5),
        eq2=EQBand(freq=1000, level=level_mid, bw=5),
        eq3=EQBand(freq=2400, level=level_high, bw=5),
    )


class WriteEqDiffTest(unittest.TestCase):
    def test_no_baseline_writes_all_nine(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        written = ft.write_eq(_make_eq(), NORMAL_EQ_MENUS)
        self.assertEqual(written, 9)
        # 9 EX-Schreibvorgänge auf die Normal-EQ-Menüs (EX119..EX127)
        ex_writes = [w for w in cat.writes if 119 <= w[0] <= 127]
        self.assertEqual(len(ex_writes), 9)

    def test_identical_baseline_writes_nothing(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        eq = _make_eq(level_low=3, level_mid=-2, level_high=5)
        written = ft.write_eq(eq, NORMAL_EQ_MENUS, baseline=eq)
        self.assertEqual(written, 0)
        self.assertEqual(len(cat.writes), 0)

    def test_only_changed_slot_written(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        baseline = _make_eq(level_low=0, level_mid=0, level_high=0)
        new = _make_eq(level_low=0, level_mid=4, level_high=0)
        written = ft.write_eq(new, NORMAL_EQ_MENUS, baseline=baseline)
        # Nur MID-Level (1 Slot) hat sich verändert.
        self.assertEqual(written, 1)
        self.assertEqual(len(cat.writes), 1)
        menu, _payload = cat.writes[0]
        # EX122 = Normal-EQ MID Level (lt. NORMAL_EQ_MENUS-Layout)
        self.assertEqual(menu, NORMAL_EQ_MENUS.band2_level)

    def test_processor_eq_path_uses_diff(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        baseline = _make_eq(level_high=3)
        new = _make_eq(level_high=4)
        written = ft.write_processor_eq(new, baseline=baseline)
        self.assertEqual(written, 1)
        self.assertEqual(cat.writes[0][0], PROCESSOR_EQ_MENUS.band3_level)

    def test_off_band_normalizes_stale_level_in_diff(self) -> None:
        """Freq OFF mit altem Level: Schreibplan setzt Freq 00 und Level +00."""
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        baseline = _make_eq(level_mid=5)
        new = EQSettings(
            eq1=baseline.eq1,
            eq2=EQBand(freq="OFF", level=8, bw=5),
            eq3=baseline.eq3,
        )
        written = ft.write_eq(new, NORMAL_EQ_MENUS, baseline=baseline)
        self.assertEqual(written, 2)
        payloads = dict(cat.writes)
        self.assertEqual(payloads[NORMAL_EQ_MENUS.band2_freq], "00")
        self.assertEqual(payloads[NORMAL_EQ_MENUS.band2_level], "+00")


# ----------------------------------------------------------------------
# write_extended_for_mode mit baseline
# ----------------------------------------------------------------------


class WriteExtendedDiffTest(unittest.TestCase):
    def test_no_baseline_writes_all_supplied(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        written = ft.write_extended_for_mode(
            "AM", {"am_carrier_level": 50, "am_mic_sel": "MIC"}
        )
        self.assertEqual(written, 2)

    def test_identical_baseline_writes_nothing(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        values = {"am_carrier_level": 50, "am_mic_sel": "MIC"}
        written = ft.write_extended_for_mode(
            "AM", values, baseline=dict(values)
        )
        self.assertEqual(written, 0)

    def test_only_changed_keys_written(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        baseline = {"am_carrier_level": 50, "am_mic_sel": "MIC"}
        new = {"am_carrier_level": 60, "am_mic_sel": "MIC"}
        written = ft.write_extended_for_mode("AM", new, baseline=baseline)
        self.assertEqual(written, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
