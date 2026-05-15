"""Tests fuer die Lern-Logik im RX-Poller bei nicht unterstuetzten Befehlen.

Hintergrund: Der FT-991 (ohne A) kennt einige CAT-Befehle nicht, die der
FT-991A unterstuetzt -- konkret ``NR0;`` (Noise Reduction) und ``BC0;``
(Beat Cancel/Auto Notch). Das Geraet antwortet darauf mit ``?;``.

Frueher hat der Slow-Path bei jedem Tick (alle ~3 s) erneut versucht,
diese Befehle zu lesen, und das CAT-Log mit WARN-Meldungen geflutet.

Erwartetes Verhalten jetzt:

1. Beim ersten Auftreten von ``?;`` wird der Read als INFO geloggt
   ("...wird vom Funkgeraet nicht unterstuetzt...").
2. Der Read landet in ``MeterPoller._disabled_reads`` und wird in
   kuenftigen Ticks lautlos uebersprungen -- kein CAT-Roundtrip mehr.
3. Bei einem neuen ``start()`` (Reconnect, evtl. anderes Geraet am
   Port) wird das Set geleert und alle Reads sind wieder aktiv.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cat.cat_log import CatLog, LogLevel
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT


class _FT991PartialFake(SerialCAT):
    """Spielt einen FT-991 ohne A: ``NR0;`` und ``BC0;`` -> ``?;``.

    Andere Slow-Path-Reads liefern minimal gueltige Antworten, damit der
    Poller normal durchlaeuft.
    """

    _CANNED: Dict[str, str] = {
        "TX;": "TX0;",
        "SM0;": "SM0044;",
        "SQ0;": "SQ0006;",
        "AG0;": "AG0067;",
        "RG0;": "RG0251;",
        "GT0;": "GT01;",
        "NB0;": "NB00;",
        "NL0;": "NL0004;",
        "RL0;": "RL001;",
        "MD0;": "MD04;",
        "FA;": "FA014255900;",
        "FB;": "FB014441250;",
        "MG;": "MG050;",
        "SH0;": "SH0012;",
        # FT-991 ohne A kennt diese hier nicht:
        "NR0;": "?;",
        "BC0;": "?;",
    }

    def __init__(self, log: CatLog) -> None:
        super().__init__(log=log)
        self.calls: list[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        log = self.get_log()
        if log is not None:
            log.log_tx(command)
        self.calls.append(command)
        if not read_response:
            return ""
        response = self._CANNED.get(command, "?;")
        if log is not None:
            log.log_rx(response)
        if response == "?;":
            from cat.cat_errors import CatCommandUnsupportedError
            raise CatCommandUnsupportedError(
                f"Funkgeraet kennt Befehl {command!r} nicht (Antwort '?;')"
            )
        return response


class PollerLearningTest(unittest.TestCase):
    def _make_poller(self, radio):
        from gui.meter_widget import MeterPoller
        return MeterPoller(radio, tx_interval_ms=50, rx_interval_ms=50)

    def test_first_slow_path_logs_info_and_remembers_unsupported(self) -> None:
        log = CatLog()
        radio = _FT991PartialFake(log)
        poller = self._make_poller(radio)
        ft = FT991CAT(radio)

        # Slow-Path erzwingen (sonst wird nur das S-Meter gelesen).
        poller._force_full_rx = True
        poller._poll_rx(ft)

        # Beide nicht unterstuetzten Befehle landen jetzt im Set.
        self.assertIn("nr_on", poller._disabled_reads)
        self.assertIn("auto_notch", poller._disabled_reads)

        # ...und im Log steht je eine INFO-Meldung, KEIN WARN.
        infos = [e for e in log.snapshot() if e.level == LogLevel.INFO]
        info_texts = " | ".join(e.text for e in infos)
        self.assertIn("nr_on", info_texts)
        self.assertIn("auto_notch", info_texts)
        warns = [e for e in log.snapshot() if e.level == LogLevel.WARN]
        # Wir akzeptieren etwaige andere WARNs, aber keine fuer NR0;/BC0;.
        for w in warns:
            self.assertNotIn("NR-Antwort", w.text)
            self.assertNotIn("BC-Antwort", w.text)

    def test_second_slow_path_skips_unsupported_without_sending(self) -> None:
        log = CatLog()
        radio = _FT991PartialFake(log)
        poller = self._make_poller(radio)
        ft = FT991CAT(radio)

        poller._force_full_rx = True
        poller._poll_rx(ft)
        # Reset der Aufrufliste, damit wir nur die Anfragen des
        # zweiten Slow-Path-Durchlaufs sehen.
        radio.calls.clear()
        # Nach SLOW_PATH_TICKS Tick-Aufrufen waere Slow-Path wieder
        # faellig -- wir erzwingen ihn der Einfachheit halber.
        poller._force_full_rx = True
        poller._poll_rx(ft)

        # NR0; und BC0; werden NICHT mehr gesendet.
        self.assertNotIn("NR0;", radio.calls)
        self.assertNotIn("BC0;", radio.calls)
        # Aber alle anderen Slow-Path-Befehle schon.
        for cmd in ("SQ0;", "AG0;", "RG0;", "GT0;", "NB0;", "NL0;", "RL0;",
                    "MG;", "MD0;", "FA;", "FB;", "SH0;"):
            self.assertIn(cmd, radio.calls)

    def test_start_clears_disabled_set(self) -> None:
        # Wenn der User neu verbindet, soll das Geraet wieder neu
        # gelernt werden -- evtl. haengt jetzt ein 991A statt eines 991
        # am Port.
        log = CatLog()
        radio = _FT991PartialFake(log)
        poller = self._make_poller(radio)
        poller._disabled_reads.add("nr_on")
        poller._disabled_reads.add("auto_notch")

        poller.start()
        try:
            self.assertEqual(poller._disabled_reads, set())
        finally:
            poller.stop()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
