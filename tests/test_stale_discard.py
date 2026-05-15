"""Tests fuer den Stale-Response-Filter in :class:`SerialCAT`.

Hintergrund: Yaesu-Funkgeraete senden unaufgeforderte ``AI``-Frames
(Auto Information), sobald am Front-Panel etwas passiert -- z. B. ein
Druck auf die NB-Taste. Diese Frames landen im seriellen Puffer und
wuerden, ohne Schutz, vom naechsten Read als Antwort auf die naechste
Anfrage interpretiert. ``SerialCAT.send_command`` filtert solche
Frames anhand des Anfrage-Praefix wieder heraus.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Iterable, List

# Sicherstellen, dass das Paket gefunden wird, wenn die Tests direkt
# ueber ``python tests/test_stale_discard.py`` laufen.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cat.cat_errors import CatCommandUnsupportedError, CatProtocolError
from cat.cat_log import CatLog, LogLevel
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT


class _ScriptedSerial:
    """Minimale ``serial.Serial``-Attrappe, die eine vorab definierte
    Liste von Antwort-Frames zurueckliefert.

    Jeder Eintrag in ``frames`` ist eine Antwort *eines* Reads. Mehrere
    Reads in Folge konsumieren die Liste nacheinander -- so koennen wir
    Off-by-N-Szenarien (Stale-Frames vor der echten Antwort) modellieren.
    """

    is_open = True

    def __init__(self, frames: Iterable[bytes]) -> None:
        self._frames: List[bytes] = list(frames)
        self.writes: List[bytes] = []
        self.reset_in_count = 0

    def reset_input_buffer(self) -> None:
        self.reset_in_count += 1

    def reset_output_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read_until(self, expected: bytes, size: int) -> bytes:
        if not self._frames:
            return b""
        return self._frames.pop(0)

    def close(self) -> None:
        self.is_open = False


def _make_cat(frames: Iterable[bytes]):
    """Bindet eine ``_ScriptedSerial``-Instanz an eine echte ``SerialCAT``,
    sodass die echte ``send_command``/``_read_with_stale_discard``-
    Logik aktiv ist.
    """
    log = CatLog()
    cat = SerialCAT(log=log)
    fake = _ScriptedSerial(frames)
    cat._serial = fake  # type: ignore[attr-defined]
    cat._port = "COMTEST"  # type: ignore[attr-defined]
    cat._timeout_s = 0.05  # type: ignore[attr-defined]
    return cat, fake, log


class StaleDiscardTest(unittest.TestCase):
    def test_first_frame_matches_prefix_no_discard(self) -> None:
        cat, fake, log = _make_cat([b"SQ0006;"])
        response = cat.send_command("SQ0;")
        self.assertEqual(response, "SQ0006;")
        warns = [e for e in log.snapshot() if e.level == LogLevel.WARN]
        self.assertEqual(warns, [])
        self.assertEqual(fake.reset_in_count, 1)

    def test_one_stale_frame_gets_discarded(self) -> None:
        # Vor unserer SQ0-Anfrage liegt noch eine verzoegerte TX0;-
        # Antwort im Puffer -- wie im realen Fehlerlog beobachtet.
        cat, _fake, log = _make_cat([b"TX0;", b"SQ0006;"])
        response = cat.send_command("SQ0;")
        self.assertEqual(response, "SQ0006;")
        warns = [e for e in log.snapshot() if e.level == LogLevel.WARN]
        self.assertEqual(len(warns), 1)
        self.assertIn("TX0;", warns[0].text)
        self.assertIn("SQ0", warns[0].text)

    def test_multiple_stale_frames_get_discarded(self) -> None:
        cat, _fake, _log = _make_cat([b"TX0;", b"NB01;", b"SM0021;", b"GT01;"])
        response = cat.send_command("GT0;")
        self.assertEqual(response, "GT01;")

    def test_question_mark_response_raises_unsupported(self) -> None:
        # Yaesus "command not understood" wird als eigene Exception
        # gemeldet, damit hoehere Schichten den Befehl gezielt
        # deaktivieren koennen. Eine echte Stale-Verwerfung passiert
        # dabei nicht (keine WARN-Meldung).
        cat, _fake, log = _make_cat([b"?;"])
        with self.assertRaises(CatCommandUnsupportedError):
            cat.send_command("NR0;")
        warns = [e for e in log.snapshot() if e.level == LogLevel.WARN]
        self.assertEqual(warns, [])

    def test_too_many_stale_frames_raise_protocol_error(self) -> None:
        cat, _fake, _log = _make_cat(
            [b"TX0;", b"NB01;", b"SM0021;", b"AG0067;", b"RG0251;", b"GT01;"]
        )
        with self.assertRaises(CatProtocolError):
            cat.send_command("FA;")


class DisableAutoInformationTest(unittest.TestCase):
    def test_disable_ai_sends_ai0_without_reading(self) -> None:
        # AI0; ist ein reiner Write -- wir lieferen *keinen* Frame, der
        # Test wuerde sonst in den Stale-Filter laufen.
        cat, fake, log = _make_cat([])
        FT991CAT(cat).disable_auto_information()
        self.assertEqual(fake.writes, [b"AI0;"])
        infos = [e for e in log.snapshot() if e.level == LogLevel.INFO]
        self.assertTrue(any("AI0" in e.text for e in infos))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
