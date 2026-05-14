"""Tests für cat.cat_log und die Logging-Anbindung in der CAT-Schicht."""

from __future__ import annotations

import threading
import unittest
from typing import Dict

from cat.cat_errors import CatNotConnectedError, CatTimeoutError
from cat.cat_log import CatLog, LogEntry, LogLevel
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT


class CatLogBasicsTest(unittest.TestCase):
    def test_add_entries_and_observe(self) -> None:
        log = CatLog()
        received = []
        log.add_observer(received.append)

        log.log_tx("ID;")
        log.log_rx("ID0570;")
        log.log_info("hi")

        self.assertEqual(len(received), 3)
        self.assertEqual(received[0].level, LogLevel.TX)
        self.assertEqual(received[0].text, "ID;")
        self.assertEqual(received[1].level, LogLevel.RX)
        self.assertEqual(received[2].level, LogLevel.INFO)

    def test_log_tx_appends_semicolon(self) -> None:
        log = CatLog()
        entry = log.log_tx("ID")  # ohne ;
        self.assertEqual(entry.text, "ID;")

    def test_max_entries_capacity(self) -> None:
        log = CatLog(max_entries=5)
        for i in range(10):
            log.log_info(f"x{i}")
        snap = log.snapshot()
        self.assertEqual(len(snap), 5)
        self.assertEqual(snap[0].text, "x5")
        self.assertEqual(snap[-1].text, "x9")

    def test_cleared_observer(self) -> None:
        log = CatLog()
        cleared_count = [0]

        def on_cleared() -> None:
            cleared_count[0] += 1

        log.add_cleared_observer(on_cleared)
        log.log_info("eins")
        log.clear()
        self.assertEqual(len(log), 0)
        self.assertEqual(cleared_count[0], 1)

    def test_observer_exception_doesnt_break_log(self) -> None:
        log = CatLog()

        def bad(_entry: LogEntry) -> None:
            raise RuntimeError("kaputt")

        log.add_observer(bad)
        # Sollte trotzdem durchlaufen
        log.log_info("trotzdem")
        self.assertEqual(len(log), 1)

    def test_dump_text_contains_levels(self) -> None:
        log = CatLog()
        log.log_tx("TX;")
        log.log_rx("TX0;")
        log.log_error("kaputt")
        text = log.dump_text()
        self.assertIn("TX", text)
        self.assertIn("RX", text)
        self.assertIn("ERROR", text)
        self.assertIn("TX0;", text)

    def test_thread_safety_smoke(self) -> None:
        log = CatLog(max_entries=10000)
        received = []
        log.add_observer(received.append)

        def producer() -> None:
            for i in range(200):
                log.log_info(f"e{i}")

        threads = [threading.Thread(target=producer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 4 Threads * 200 Einträge = 800
        self.assertEqual(len(log), 800)
        self.assertEqual(len(received), 800)


# ----------------------------------------------------------------------
# Integration mit SerialCAT (via Fake)
# ----------------------------------------------------------------------


class _FakeSerialCAT(SerialCAT):
    def __init__(self, log: CatLog, canned: Dict[str, str]) -> None:
        super().__init__(log=log)
        self._canned = canned

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True):  # type: ignore[override]
        # WICHTIG: Fake muss selbst loggen, da wir nicht durch send_command
        # der Basisklasse gehen.
        log = self.get_log()
        if log is not None:
            log.log_tx(command)
        if not read_response:
            return ""
        response = self._canned.get(command, "?;")
        if log is not None:
            log.log_rx(response)
        return response


class FT991LoggingTest(unittest.TestCase):
    def test_id_query_logs_op_info_tx_and_rx(self) -> None:
        log = CatLog()
        fake = _FakeSerialCAT(log, {"ID;": "ID0570;"})
        ft = FT991CAT(fake)

        identity = ft.get_radio_id()
        self.assertTrue(identity.is_ft991)

        snap = log.snapshot()
        levels = [e.level for e in snap]
        texts = [e.text for e in snap]
        self.assertIn(LogLevel.INFO, levels)
        self.assertIn(LogLevel.TX, levels)
        self.assertIn(LogLevel.RX, levels)
        self.assertIn("ID;", texts)
        self.assertIn("ID0570;", texts)

    def test_unexpected_eq_raw_values_logged(self) -> None:
        """Bei Decodier-Fehlern werden alle 9 Rohwerte des EQ-Sets geloggt.

        Normal-EQ liegt laut Manual auf EX119..EX127.
        """
        log = CatLog()
        # EQ1 Freq=99 ist in unserer LOW-Tabelle nicht vorhanden -> Decode-Fehler.
        canned = {
            "TX;": "TX0;",
            "EX119;": "EX11999;",   # ungültig (Freq idx 99)
            "EX120;": "EX120+02;",  # Level +2
            "EX121;": "EX12105;",   # BW 5
            "EX122;": "EX12201;",
            "EX123;": "EX123+00;",
            "EX124;": "EX12405;",
            "EX125;": "EX12501;",
            "EX126;": "EX126+00;",
            "EX127;": "EX12705;",
        }
        fake = _FakeSerialCAT(log, canned)
        ft = FT991CAT(fake)

        from cat.cat_errors import CatProtocolError
        with self.assertRaises(CatProtocolError):
            ft.read_eq()

        # In der Fehlermeldung müssen alle 9 Rohwerte stehen
        errors = [e for e in log.snapshot() if e.level == LogLevel.ERROR]
        self.assertEqual(len(errors), 1)
        msg = errors[0].text
        for menu in (119, 120, 121, 122, 123, 124, 125, 126, 127):
            self.assertIn(f"EX{menu}=", msg)


class SerialCatLoggingTest(unittest.TestCase):
    def test_send_command_without_connection_logs_error(self) -> None:
        log = CatLog()
        cat = SerialCAT(log=log)
        with self.assertRaises(CatNotConnectedError):
            cat.send_command("ID;")
        errors = [e for e in log.snapshot() if e.level == LogLevel.ERROR]
        self.assertEqual(len(errors), 1)
        self.assertIn("ID;", errors[0].text)


class SerialCatConnectionLostTest(unittest.TestCase):
    """Simuliert einen verlorenen USB-Port mitten in einem Roundtrip."""

    def _make_fake_serial(self, *, fail_on: str):
        """Liefert ein Fake-Serial, das beim angegebenen Schritt eine
        ``serial.SerialException`` wirft (so wie pyserial, wenn der Port
        verschwindet).
        """
        import serial

        fake = self

        class _FakeSerial:
            is_open = True

            def __init__(self) -> None:
                self.calls = []

            def reset_input_buffer(self) -> None:
                self.calls.append("reset_in")
                if fail_on == "reset_in":
                    raise serial.SerialException("ClearCommError failed")

            def reset_output_buffer(self) -> None:
                self.calls.append("reset_out")

            def write(self, data: bytes) -> int:
                self.calls.append("write")
                if fail_on == "write":
                    raise serial.SerialException("WriteFile failed")
                return len(data)

            def flush(self) -> None:
                self.calls.append("flush")

            def read_until(self, expected: bytes, size: int) -> bytes:
                self.calls.append("read")
                if fail_on == "read":
                    raise OSError(22, "Bad file descriptor")
                return b""

            def close(self) -> None:
                self.calls.append("close")
                self.is_open = False

        return _FakeSerial()

    def _setup_cat(self, *, fail_on: str):
        from cat.cat_errors import CatConnectionLostError
        from cat.serial_cat import SerialCAT

        log = CatLog()
        cat = SerialCAT(log=log)
        # An den internen Zustand binden — sonst geht is_connected() davon
        # aus, dass nichts offen ist.
        fake = self._make_fake_serial(fail_on=fail_on)
        cat._serial = fake  # type: ignore[attr-defined]
        cat._port = "COMTEST"  # type: ignore[attr-defined]
        return cat, fake, log, CatConnectionLostError

    def test_write_failure_raises_connection_lost_and_disconnects(self) -> None:
        cat, fake, log, CatConnectionLostError = self._setup_cat(fail_on="write")
        self.assertTrue(cat.is_connected())
        with self.assertRaises(CatConnectionLostError):
            cat.send_command("ID;")
        self.assertFalse(cat.is_connected())
        # WARN, nicht ERROR — bei einem Verbindungsverlust soll der CAT-Log
        # nicht in Rot blinken.
        warnings = [e for e in log.snapshot() if e.level == LogLevel.WARN]
        self.assertTrue(warnings)
        self.assertIn("COMTEST", warnings[-1].text)

    def test_read_failure_raises_connection_lost(self) -> None:
        cat, _fake, _log, CatConnectionLostError = self._setup_cat(fail_on="read")
        with self.assertRaises(CatConnectionLostError):
            cat.send_command("ID;")
        self.assertFalse(cat.is_connected())

    def test_reset_buffer_failure_raises_connection_lost(self) -> None:
        cat, _fake, _log, CatConnectionLostError = self._setup_cat(fail_on="reset_in")
        with self.assertRaises(CatConnectionLostError):
            cat.send_command("ID;")
        self.assertFalse(cat.is_connected())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
