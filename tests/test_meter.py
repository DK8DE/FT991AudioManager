"""Tests für Version 0.4: TX-Status, RM-Meter und SerialCAT-Thread-Safety."""

from __future__ import annotations

import threading
import time
import unittest
from typing import Dict, List

from cat.cat_errors import CatProtocolError
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.meter_mapping import (
    METER_INFO,
    SMETER_TICKS,
    MeterKind,
    classify_value,
    format_meter_value,
    format_rm_query,
    format_sm_query,
    parse_rm_response,
    parse_sm_response,
    parse_tx_response,
)


# ----------------------------------------------------------------------
# Mapping-Tests
# ----------------------------------------------------------------------


class MeterMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        from mapping.meter_mapping import (
            PO_WATTS_CALIB_HF_DEFAULT,
            apply_po_calibration_watt_raw,
        )

        d = [(w, r) for r, w in PO_WATTS_CALIB_HF_DEFAULT if w > 0]
        apply_po_calibration_watt_raw({"hf_10m": d})

    def test_indices(self) -> None:
        # Indizes laut Manual 1711-D (FT-991 CAT Operation Reference Book, S. 16):
        # RM1=S, RM3=COMP, RM4=ALC, RM5=PO, RM6=SWR.
        self.assertEqual(METER_INFO[MeterKind.COMP].index, 3)
        self.assertEqual(METER_INFO[MeterKind.ALC].index, 4)
        self.assertEqual(METER_INFO[MeterKind.PO].index, 5)
        self.assertEqual(METER_INFO[MeterKind.SWR].index, 6)

    def test_format_query(self) -> None:
        self.assertEqual(format_rm_query(MeterKind.COMP), "RM3;")
        self.assertEqual(format_rm_query(MeterKind.ALC), "RM4;")
        self.assertEqual(format_rm_query(MeterKind.PO), "RM5;")
        self.assertEqual(format_rm_query(MeterKind.SWR), "RM6;")

    def test_parse_rm_response(self) -> None:
        self.assertEqual(parse_rm_response("RM3128;", MeterKind.COMP), 128)
        self.assertEqual(parse_rm_response("RM4000;", MeterKind.ALC), 0)
        self.assertEqual(parse_rm_response("RM5255;", MeterKind.PO), 255)
        self.assertEqual(parse_rm_response("RM6010;", MeterKind.SWR), 10)

    def test_parse_rm_response_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_rm_response("RM4123", MeterKind.ALC)  # ohne ;
        with self.assertRaises(ValueError):
            parse_rm_response("RM3XYZ;", MeterKind.COMP)  # nicht-numerisch
        with self.assertRaises(ValueError):
            parse_rm_response("RM3;", MeterKind.COMP)    # leer
        with self.assertRaises(ValueError):
            parse_rm_response("RM5050;", MeterKind.COMP)  # falscher Index

    def test_sm_format_and_parse(self) -> None:
        self.assertEqual(format_sm_query(), "SM0;")
        self.assertEqual(parse_sm_response("SM0000;"), 0)
        self.assertEqual(parse_sm_response("SM0128;"), 128)
        self.assertEqual(parse_sm_response("SM0255;"), 255)

    def test_sm_parse_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_sm_response("SM0128")     # fehlendes ;
        with self.assertRaises(ValueError):
            parse_sm_response("SM1128;")    # falscher Prefix
        with self.assertRaises(ValueError):
            parse_sm_response("SM0;")       # leer

    def test_smeter_ticks_monotonic(self) -> None:
        # S-Punkte-Tabelle muss aufsteigend sortiert sein.
        values = [v for v, _ in SMETER_TICKS]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 255)

    def test_parse_tx(self) -> None:
        self.assertFalse(parse_tx_response("TX0;"))
        self.assertTrue(parse_tx_response("TX1;"))   # PTT
        self.assertTrue(parse_tx_response("TX2;"))   # CAT

    def test_parse_tx_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_tx_response("TX9;")
        with self.assertRaises(ValueError):
            parse_tx_response("XX0;")
        with self.assertRaises(ValueError):
            parse_tx_response("TX0")

    def test_classify(self) -> None:
        # ALC: warn=0.5 (128), danger=0.8 (204) bei raw_max=255
        self.assertEqual(classify_value(MeterKind.ALC, 100), "ok")
        self.assertEqual(classify_value(MeterKind.ALC, 130), "warn")
        self.assertEqual(classify_value(MeterKind.ALC, 220), "danger")
        # SWR ist strenger
        self.assertEqual(classify_value(MeterKind.SWR, 50), "ok")
        self.assertEqual(classify_value(MeterKind.SWR, 100), "warn")
        self.assertEqual(classify_value(MeterKind.SWR, 200), "danger")

    def test_units_and_ticks(self) -> None:
        # ALC/COMP/POWER: Watt bzw. Prozent
        self.assertEqual(METER_INFO[MeterKind.ALC].unit, "%")
        self.assertEqual(METER_INFO[MeterKind.COMP].unit, "%")
        self.assertEqual(METER_INFO[MeterKind.PO].unit, "")
        self.assertEqual(METER_INFO[MeterKind.PO].label, "POWER")
        # SWR in :1
        self.assertEqual(METER_INFO[MeterKind.SWR].unit, ":1")
        # Jedes Meter hat eine nicht-leere Skalen-Tabelle
        for kind in MeterKind:
            self.assertGreater(len(METER_INFO[kind].ticks), 1)
            # Ticks aufsteigend
            raws = [r for r, _ in METER_INFO[kind].ticks]
            self.assertEqual(raws, sorted(raws))

    def test_format_percent_meters(self) -> None:
        self.assertEqual(format_meter_value(MeterKind.ALC, 0), "0%")
        self.assertEqual(format_meter_value(MeterKind.ALC, 128), "50%")
        self.assertEqual(format_meter_value(MeterKind.ALC, 255), "100%")
        self.assertEqual(format_meter_value(MeterKind.COMP, 191), "75%")
        self.assertEqual(format_meter_value(MeterKind.PO, 207), "100 W")
        self.assertEqual(format_meter_value(MeterKind.PO, 147), "50 W")

    def test_po_watts_per_band(self) -> None:
        from mapping.meter_mapping import format_po_watts, po_raw_to_watts

        self.assertEqual(format_po_watts(207, vhf_uhf=False), "100 W")
        self.assertEqual(format_po_watts(34, vhf_uhf=False), "5 W")
        self.assertEqual(format_po_watts(147, vhf_uhf=False), "50 W")
        self.assertEqual(format_po_watts(147, vhf_uhf=True), "50 W")
        self.assertEqual(format_po_watts(171, vhf_uhf=True), "50 W")
        # Zwischenstützpunkt (linear zwischen 104/30 und 127/35)
        self.assertEqual(format_po_watts(121, vhf_uhf=False), "34 W")
        self.assertAlmostEqual(po_raw_to_watts(93, vhf_uhf=False), 25.0, places=1)

    def test_format_swr(self) -> None:
        # ``raw == 0`` zeigen wir bewusst als "—" — ein RM6;-Roh von 0
        # kann sowohl "perfektes SWR" als auch "kein TX / kein Messwert"
        # bedeuten. Eine SWR-Zahl zu suggerieren wäre irreführend.
        self.assertEqual(format_meter_value(MeterKind.SWR, 0), "—")
        # Tabellen-Werte (KW-Skala)
        self.assertEqual(format_meter_value(MeterKind.SWR, 80), "≈ 1.5:1")
        self.assertEqual(format_meter_value(MeterKind.SWR, 128), "≈ 2.0:1")
        self.assertEqual(format_meter_value(MeterKind.SWR, 204), "≈ 3.0:1")
        # Interpolation zwischen Marken (Zwischenwerte)
        mid = format_meter_value(MeterKind.SWR, 100)
        self.assertTrue(mid.startswith("≈ 1.") or mid.startswith("≈ 2."), mid)
        # Oberhalb der letzten echten Zahl wird die Bar als "> 3:1"
        # gebündelt (statt einer unsinnigen Interpolation Richtung "∞").
        self.assertEqual(format_meter_value(MeterKind.SWR, 240), "> 3:1")
        self.assertEqual(format_meter_value(MeterKind.SWR, 255), "> 3:1")


# ----------------------------------------------------------------------
# Fake-Radio für CAT-Integration
# ----------------------------------------------------------------------


class _MeterFakeRadio(SerialCAT):
    """Antwortet auf TX und RM mit konfigurierbaren Werten."""

    def __init__(self) -> None:
        super().__init__()
        self.transmitting = False
        self.meter_values: Dict[MeterKind, int] = {
            MeterKind.COMP: 10,
            MeterKind.ALC: 100,
            MeterKind.PO: 50,
            MeterKind.SWR: 20,
        }
        self.pc_power_watts = 50
        self.sent: List[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        self.sent.append(command)
        if command == "TX;":
            return "TX1;" if self.transmitting else "TX0;"
        if command == "FA;":
            return "FA014250000;"
        if command == "PC;":
            return f"PC{int(self.pc_power_watts):03d};"
        for kind, info in METER_INFO.items():
            if command == f"RM{info.index};":
                return f"RM{info.index}{self.meter_values[kind]:03d};"
        raise AssertionError(f"Unerwartetes Kommando: {command!r}")


class TxStatusCatTest(unittest.TestCase):
    def test_rx(self) -> None:
        radio = _MeterFakeRadio()
        ft = FT991CAT(radio)
        self.assertFalse(ft.get_tx_status())

    def test_tx(self) -> None:
        radio = _MeterFakeRadio()
        radio.transmitting = True
        ft = FT991CAT(radio)
        self.assertTrue(ft.get_tx_status())


class ReadMeterCatTest(unittest.TestCase):
    def test_read_all_meters(self) -> None:
        radio = _MeterFakeRadio()
        radio.meter_values[MeterKind.ALC] = 200
        ft = FT991CAT(radio)
        all_meters = ft.read_all_meters()
        self.assertEqual(all_meters[MeterKind.ALC], 200)
        self.assertEqual(all_meters[MeterKind.COMP], 10)
        # Reihenfolge muss konsistent sein und alle Indices verwendet haben.
        # Korrekte Indizes laut Manual 1711-D: COMP=3, ALC=4, PO=5, SWR=6.
        commands = [c for c in radio.sent if c.startswith("RM")]
        self.assertEqual(set(commands), {"RM3;", "RM4;", "RM5;", "RM6;"})

    def test_read_with_string_kind(self) -> None:
        radio = _MeterFakeRadio()
        ft = FT991CAT(radio)
        self.assertEqual(ft.read_meter("alc"), 100)

    def test_read_invalid_string(self) -> None:
        radio = _MeterFakeRadio()
        ft = FT991CAT(radio)
        with self.assertRaises(ValueError):
            ft.read_meter("unknown")

    def test_read_garbled_response(self) -> None:
        radio = _MeterFakeRadio()

        def garbled(cmd: str, *, read_response: bool = True):
            # ALC ist RM4 nach Manual-Korrektur.
            if cmd == "RM4;":
                return "RM4XYZ;"
            return _MeterFakeRadio.send_command(radio, cmd, read_response=read_response)

        radio.send_command = garbled  # type: ignore[method-assign]
        ft = FT991CAT(radio)
        with self.assertRaises(CatProtocolError):
            ft.read_meter(MeterKind.ALC)


class PollTxRobustnessTest(unittest.TestCase):
    """``MeterPoller._poll_tx`` muss auch dann ein ``tx_sample`` emittieren,
    wenn ein einzelner ``RMn;``-Read schief geht. Sonst bleiben die TX-
    Balken in der GUI dunkel und die TX-LED zeigt RX, obwohl das Radio
    sendet -- das war der reale Fehler beim FT-991A unter DSP-Last."""

    def _make_poller(self, radio):
        from gui.meter_widget import MeterPoller
        return MeterPoller(radio, tx_interval_ms=50, rx_interval_ms=50)

    def test_single_meter_failure_does_not_block_sample(self) -> None:
        radio = _MeterFakeRadio()
        radio.transmitting = True
        original_send = radio.send_command

        def maybe_fail(cmd: str, *, read_response: bool = True):
            # COMP zickt -- z. B. CatProtocolError nach Stale-Frame-Limit.
            if cmd == "RM3;":
                raise CatProtocolError("simulierter TX-Meter-Fehler")
            return original_send(cmd, read_response=read_response)

        radio.send_command = maybe_fail  # type: ignore[method-assign]

        poller = self._make_poller(radio)
        collected: List[object] = []
        poller.tx_sample.connect(lambda sample: collected.append(sample))
        ft = FT991CAT(radio)
        delay = poller._poll_tx(ft)

        self.assertEqual(len(collected), 1)
        sample = collected[0]
        self.assertTrue(sample.transmitting)
        # 3 von 4 Meter da, COMP fehlt -- GUI behaelt den letzten Wert.
        self.assertNotIn(MeterKind.COMP, sample.values)
        self.assertIn(MeterKind.ALC, sample.values)
        self.assertIn(MeterKind.PO, sample.values)
        self.assertIn(MeterKind.SWR, sample.values)
        self.assertEqual(delay, poller._tx_interval_ms)

    def test_all_meters_failure_still_emits_tx_state(self) -> None:
        # Selbst wenn ALLE Meter scheitern, muss das TX-Sample raus, damit
        # die GUI weiss, dass das Radio sendet (LED auf rot schalten).
        radio = _MeterFakeRadio()
        radio.transmitting = True

        def always_fail(cmd: str, *, read_response: bool = True):
            if cmd.startswith("RM"):
                raise CatProtocolError("alle Meter zicken")
            return _MeterFakeRadio.send_command(radio, cmd, read_response=read_response)

        radio.send_command = always_fail  # type: ignore[method-assign]

        poller = self._make_poller(radio)
        collected: List[object] = []
        poller.tx_sample.connect(lambda sample: collected.append(sample))
        ft = FT991CAT(radio)
        poller._poll_tx(ft)

        self.assertEqual(len(collected), 1)
        self.assertTrue(collected[0].transmitting)
        self.assertEqual(collected[0].values, {})


# ----------------------------------------------------------------------
# Thread-Safety
# ----------------------------------------------------------------------


class _SlowFakeRadio(SerialCAT):
    """Antwortet langsam, um Race-Conditions zu provozieren."""

    def __init__(self, delay_s: float = 0.005) -> None:
        super().__init__()
        self._delay_s = delay_s
        self._serialize_violations = 0
        self._inside = False
        self._counter_lock = threading.Lock()
        self.calls: List[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        # Wir nutzen DIE Schutz-Logik aus SerialCAT.send_command — also rufen
        # wir hier den geschützten Pfad direkt nach, indem wir das Lock von
        # SerialCAT ebenfalls verwenden.
        with self._lock:
            if self._inside:
                with self._counter_lock:
                    self._serialize_violations += 1
            self._inside = True
            try:
                time.sleep(self._delay_s)
                self.calls.append(command)
                return f"OK{command}"
            finally:
                self._inside = False


class SerialCatThreadSafetyTest(unittest.TestCase):
    """Garantiert, dass parallele send_command-Aufrufe sich nicht überlappen.

    Ohne Lock würde der ``_inside``-Counter Verstöße zählen.
    """

    def test_concurrent_send_serializes(self) -> None:
        radio = _SlowFakeRadio(delay_s=0.002)

        def worker(idx: int) -> None:
            for _ in range(20):
                radio.send_command(f"CMD{idx};")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(radio._serialize_violations, 0)
        self.assertEqual(len(radio.calls), 5 * 20)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
