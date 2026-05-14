"""Tests, die ohne reale Hardware laufen.

Wir testen hier nur die Parser- und Settings-Logik. Der serielle Layer
(:class:`cat.SerialCAT`) wird mit einem Fake getestet, der ``send_command``
ersetzt.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cat.ft991_cat import FT991A_RADIO_ID, FT991CAT, RadioIdentity
from cat.serial_cat import SerialCAT
from model import AppSettings


class _FakeSerialCAT(SerialCAT):
    """SerialCAT-Variante, die anstelle einer echten Verbindung canned
    Antworten liefert."""

    def __init__(self, canned_response: str) -> None:
        super().__init__()
        self._canned = canned_response
        self._connected = True

    def is_connected(self) -> bool:  # type: ignore[override]
        return self._connected

    def send_command(self, command: str, *, read_response: bool = True) -> str:  # type: ignore[override]
        if not read_response:
            return ""
        return self._canned


class IdParsingTest(unittest.TestCase):
    def test_ft991a_id(self) -> None:
        fake = _FakeSerialCAT("ID0570;")
        ft = FT991CAT(fake)
        identity = ft.get_radio_id()
        self.assertIsInstance(identity, RadioIdentity)
        self.assertEqual(identity.radio_id, FT991A_RADIO_ID)
        self.assertTrue(identity.is_ft991)

    def test_unknown_id(self) -> None:
        fake = _FakeSerialCAT("ID0241;")  # z. B. FT-2000
        ft = FT991CAT(fake)
        identity = ft.get_radio_id()
        self.assertEqual(identity.radio_id, "0241")
        self.assertFalse(identity.is_ft991)

    def test_malformed_response(self) -> None:
        fake = _FakeSerialCAT("?;")
        ft = FT991CAT(fake)
        identity = ft.get_radio_id()
        self.assertIsNone(identity.radio_id)
        self.assertFalse(identity.is_ft991)


class AppSettingsRoundtripTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings()
            settings.cat.port = "COM7"
            settings.cat.baudrate = 38400
            settings.cat.timeout_ms = 750
            settings.ui.last_profile = "SSB Sprache"
            settings.ui.auto_apply_profile = False
            settings.ui.show_advanced = True
            settings.ui.force_dark_mode = False
            settings.save(path)

            loaded = AppSettings.load(path)
            self.assertEqual(loaded.cat.port, "COM7")
            self.assertEqual(loaded.cat.baudrate, 38400)
            self.assertEqual(loaded.cat.timeout_ms, 750)
            self.assertEqual(loaded.ui.last_profile, "SSB Sprache")
            self.assertFalse(loaded.ui.auto_apply_profile)
            self.assertTrue(loaded.ui.show_advanced)
            self.assertFalse(loaded.ui.force_dark_mode)

    def test_force_dark_mode_default_is_true(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            loaded = AppSettings.load(path)
            self.assertTrue(loaded.ui.force_dark_mode)

    def test_load_missing_file_returns_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.json"
            loaded = AppSettings.load(path)
            self.assertIsNone(loaded.cat.port)
            self.assertEqual(loaded.cat.baudrate, 38400)
            self.assertEqual(loaded.cat.timeout_ms, 1000)

    def test_load_corrupt_file_returns_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{ not json", encoding="utf-8")
            loaded = AppSettings.load(path)
            self.assertEqual(loaded.cat.baudrate, 38400)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
