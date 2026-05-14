"""Tests für die EQ-Operationen in FT991CAT, mit Fake-Serial-Backend."""

from __future__ import annotations

import unittest
from typing import Dict

from cat.cat_errors import CatProtocolError
from cat.ft991_cat import FT991CAT, TxLockError
from cat.serial_cat import SerialCAT
from mapping.eq_mapping import NORMAL_EQ_MENUS
from model import EQBand, EQSettings


class _FakeSerialCAT(SerialCAT):
    """Hält für jedes EX-Menü einen Rohwert vor und simuliert ``TX;``."""

    def __init__(self, initial: Dict[int, str], *, transmitting: bool = False) -> None:
        super().__init__()
        self.store: Dict[int, str] = dict(initial)
        self.transmitting = transmitting
        self.last_commands: list[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True) -> str:  # type: ignore[override]
        self.last_commands.append(command)
        # TX-Status-Query
        if command == "TX;":
            return "TX1;" if self.transmitting else "TX0;"

        # EX-Menü
        if command.startswith("EX") and command.endswith(";"):
            body = command[2:-1]  # ohne "EX" und ";"
            menu = int(body[:3])
            payload = body[3:]
            if payload == "":
                # Lesen
                return f"EX{menu:03d}{self.store.get(menu, '00')};"
            # Schreiben
            self.store[menu] = payload
            return ""

        if command == "ID;":
            return "ID0570;"

        raise AssertionError(f"Unerwartetes Kommando: {command!r}")


class EqReadWriteTest(unittest.TestCase):
    """Tests basieren auf dem korrigierten Manual-Mapping (1711-D):

    - Normal-EQ:    EX119..EX127
    - Processor-EQ: EX128..EX136
    - Slot-Reihenfolge pro Band: **Freq, Level, BW**
    - Level-Range: ``-20..+10`` dB
    - Frequenz-Tabellen: LOW 100..700, MID 700..1500, HIGH 1500..3200 (100-Hz-Schritte)
    """

    def _setup_store_with_known_eq(self) -> _FakeSerialCAT:
        # Slot-Reihenfolge: Freq / Level / BW
        # EQ1 LOW:  Freq=03 (300 Hz), Level=-03 dB, BW=05
        # EQ2 MID:  Freq=04 (1000 Hz, idx 4 in MID), Level=+02 dB, BW=04
        # EQ3 HIGH: Freq=13 (2700 Hz, idx 13 in HIGH), Level=+04 dB, BW=03
        initial = {
            119: "03", 120: "-03", 121: "05",
            122: "04", 123: "+02", 124: "04",
            125: "13", 126: "+04", 127: "03",
        }
        return _FakeSerialCAT(initial)

    def test_read_eq_roundtrip(self) -> None:
        fake = self._setup_store_with_known_eq()
        ft = FT991CAT(fake)
        eq = ft.read_eq(NORMAL_EQ_MENUS)

        self.assertEqual(eq.eq1.freq, 300)
        self.assertEqual(eq.eq1.bw, 5)
        self.assertEqual(eq.eq1.level, -3)
        self.assertEqual(eq.eq2.freq, 1000)
        self.assertEqual(eq.eq2.bw, 4)
        self.assertEqual(eq.eq2.level, 2)
        self.assertEqual(eq.eq3.freq, 2700)
        self.assertEqual(eq.eq3.bw, 3)
        self.assertEqual(eq.eq3.level, 4)

        # Sanity: 9 EX-Reads im Bereich 119..127; kein TX-Query beim Lesen
        ex_reads = [c for c in fake.last_commands if c.startswith("EX") and c.endswith(";")]
        self.assertEqual(len(ex_reads), 9)
        self.assertNotIn("TX;", fake.last_commands)
        self.assertEqual(ex_reads[0], "EX119;")
        self.assertEqual(ex_reads[-1], "EX127;")

    def test_write_eq_checks_tx_status(self) -> None:
        fake = self._setup_store_with_known_eq()
        ft = FT991CAT(fake)
        eq = EQSettings(
            eq1=EQBand(freq=200, level=-5, bw=6),
            eq2=EQBand(freq=1000, level=4, bw=3),
            eq3=EQBand(freq=2700, level=6, bw=2),
        )

        ft.write_eq(eq, NORMAL_EQ_MENUS)

        # Vor dem ersten Write muss TX; abgefragt worden sein
        self.assertIn("TX;", fake.last_commands)
        # Genau 9 EX-Writes
        ex_writes = [c for c in fake.last_commands if c.startswith("EX") and len(c) > 6]
        self.assertEqual(len(ex_writes), 9)

        # Im Store stehen die neuen Werte (Slot-Reihenfolge: Freq/Level/BW)
        # EQ1 LOW: 200 Hz = idx 2
        self.assertEqual(fake.store[119], "02")
        self.assertEqual(fake.store[120], "-05")
        self.assertEqual(fake.store[121], "06")
        # EQ2 MID: 1000 Hz = idx 4
        self.assertEqual(fake.store[122], "04")
        self.assertEqual(fake.store[123], "+04")
        self.assertEqual(fake.store[124], "03")
        # EQ3 HIGH: 2700 Hz = idx 13
        self.assertEqual(fake.store[125], "13")
        self.assertEqual(fake.store[126], "+06")
        self.assertEqual(fake.store[127], "02")

    def test_write_blocked_during_tx(self) -> None:
        fake = self._setup_store_with_known_eq()
        fake.transmitting = True
        ft = FT991CAT(fake)
        eq = EQSettings.default()
        with self.assertRaises(TxLockError):
            ft.write_eq(eq, NORMAL_EQ_MENUS)
        # Keine EX-Writes durchgeführt
        ex_writes = [c for c in fake.last_commands if c.startswith("EX") and len(c) > 6]
        self.assertEqual(ex_writes, [])

    def test_read_eq_protocol_error(self) -> None:
        # Ungültiger Frequenz-Index 99 — LOW-Tabelle hat 8 Einträge.
        fake = _FakeSerialCAT(
            {
                119: "99", 120: "-03", 121: "05",
                122: "01", 123: "+00", 124: "05",
                125: "01", 126: "+00", 127: "05",
            }
        )
        ft = FT991CAT(fake)
        with self.assertRaises(CatProtocolError):
            ft.read_eq(NORMAL_EQ_MENUS)

    def test_read_eq_tolerate_bands_keeps_good_bands(self) -> None:
        """Mit ``tolerate_bands=True`` bringt ein defektes Band die anderen
        zwei nicht zu Fall. Hier: Band 3 hat einen Frequenz-Index ausserhalb
        der HIGH-Tabelle (19 Einträge), die ersten beiden Bänder sind ok.
        """
        from mapping.eq_mapping import PROCESSOR_EQ_MENUS

        fake = _FakeSerialCAT(
            {
                128: "02", 129: "+00", 130: "02",
                131: "01", 132: "+00", 133: "07",
                # Band 3 defekt: Frequenz-Index 99 ausserhalb HIGH-Tabelle.
                134: "99", 135: "+00", 136: "05",
            }
        )
        ft = FT991CAT(fake)
        skipped: list[str] = []
        eq = ft.read_eq(PROCESSOR_EQ_MENUS, tolerate_bands=True, skipped=skipped)

        # Band 1 LOW: idx 2 = 200 Hz
        self.assertEqual(eq.eq1.freq, 200)
        self.assertEqual(eq.eq1.level, 0)
        self.assertEqual(eq.eq1.bw, 2)
        # Band 2 MID: idx 1 = 700 Hz
        self.assertEqual(eq.eq2.freq, 700)
        self.assertEqual(eq.eq2.level, 0)
        self.assertEqual(eq.eq2.bw, 7)
        # Band 3: auf Default zurückgefallen
        self.assertEqual(eq.eq3, EQBand())
        self.assertEqual(len(skipped), 1)
        self.assertIn("EQ3", skipped[0])
        self.assertIn("EX134", skipped[0])

    def test_read_eq_tolerate_bands_default_off_keeps_raising(self) -> None:
        """Ohne ``tolerate_bands`` bleibt das alte Abbruchverhalten erhalten."""
        from mapping.eq_mapping import PROCESSOR_EQ_MENUS

        fake = _FakeSerialCAT(
            {
                128: "02", 129: "+00", 130: "02",
                131: "01", 132: "+00", 133: "07",
                134: "99", 135: "+00", 136: "05",
            }
        )
        ft = FT991CAT(fake)
        with self.assertRaises(CatProtocolError):
            ft.read_eq(PROCESSOR_EQ_MENUS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
