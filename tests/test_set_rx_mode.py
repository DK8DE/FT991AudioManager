"""Tests für ``FT991CAT.set_rx_mode`` und die zugehörigen Mappings."""

from __future__ import annotations

import unittest

from cat import TxLockError
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.rx_mapping import (
    DEFAULT_MODE_FOR_GROUP,
    MODE_TO_CODE,
    RxMode,
    format_mode_set,
)


class _FakeSerialCAT(SerialCAT):
    """Mini-Fake: hält Mode-State + TX-State und merkt sich Kommandos."""

    def __init__(self, *, transmitting: bool = False) -> None:
        super().__init__()
        self.transmitting = transmitting
        self.commands: list[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(self, command: str, *, read_response: bool = True) -> str:  # type: ignore[override]
        self.commands.append(command)
        if command == "TX;":
            return "TX1;" if self.transmitting else "TX0;"
        if command == "ID;":
            return "ID0570;"
        return ""


class FormatModeSetTest(unittest.TestCase):
    def test_known_modes_round_trip(self) -> None:
        # Roundtrip: format_mode_set → "MD0X;" sollte MODE_TO_CODE benutzen.
        for mode, code in MODE_TO_CODE.items():
            self.assertEqual(format_mode_set(mode), f"MD0{code};")

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            format_mode_set(RxMode.UNKNOWN)

    def test_defaults_for_groups_cover_valid_groups(self) -> None:
        self.assertEqual(DEFAULT_MODE_FOR_GROUP["SSB"], RxMode.USB)
        self.assertEqual(DEFAULT_MODE_FOR_GROUP["AM"], RxMode.AM)
        self.assertEqual(DEFAULT_MODE_FOR_GROUP["FM"], RxMode.FM)
        self.assertEqual(DEFAULT_MODE_FOR_GROUP["DATA"], RxMode.DATA_USB)
        self.assertEqual(DEFAULT_MODE_FOR_GROUP["C4FM"], RxMode.C4FM)


class SetRxModeTest(unittest.TestCase):
    def test_sends_md_command_when_not_transmitting(self) -> None:
        cat = _FakeSerialCAT(transmitting=False)
        ft = FT991CAT(cat)
        ft.set_rx_mode(RxMode.AM)
        # erst TX-Check, dann MD0X
        self.assertIn("TX;", cat.commands)
        self.assertIn("MD05;", cat.commands)

    def test_uses_correct_code_for_usb(self) -> None:
        cat = _FakeSerialCAT(transmitting=False)
        ft = FT991CAT(cat)
        ft.set_rx_mode(RxMode.USB)
        self.assertIn("MD02;", cat.commands)

    def test_uses_correct_code_for_data_usb(self) -> None:
        cat = _FakeSerialCAT(transmitting=False)
        ft = FT991CAT(cat)
        ft.set_rx_mode(RxMode.DATA_USB)
        self.assertIn("MD0C;", cat.commands)

    def test_raises_tx_lock_when_transmitting(self) -> None:
        cat = _FakeSerialCAT(transmitting=True)
        ft = FT991CAT(cat)
        with self.assertRaises(TxLockError):
            ft.set_rx_mode(RxMode.AM)
        # Kein MD-Befehl gegen das Radio
        self.assertNotIn("MD05;", cat.commands)

    def test_tx_lock_skippable(self) -> None:
        """Mit ``tx_lock=False`` schreiben wir auch während TX (Sonderfall)."""
        cat = _FakeSerialCAT(transmitting=True)
        ft = FT991CAT(cat)
        ft.set_rx_mode(RxMode.AM, tx_lock=False)
        self.assertIn("MD05;", cat.commands)


class WorkerTargetModeTest(unittest.TestCase):
    """``_ProfileIoWorker`` setzt mit ``target_mode`` zuerst den Radio-Mode."""

    def test_target_mode_sets_mode_before_read(self) -> None:
        from unittest.mock import MagicMock, patch
        from gui.profile_widget import _ProfileIoWorker
        from mapping.rx_mapping import RxMode

        # Wir mocken FT991CAT vollständig — die _do_read-Pipeline ruft
        # diverse get_*-Methoden auf, deren Rückgabe für die Mode-Set-Test
        # irrelevant ist (Default-Werte reichen).
        ft = MagicMock()
        ft.set_rx_mode = MagicMock()
        ft.get_mic_gain.return_value = 50
        ft.get_processor_enabled.return_value = False
        ft.get_processor_level.return_value = 50
        ft.get_mic_eq_enabled.return_value = False
        ft.get_ssb_bpf.return_value = "BPF_FULL"
        from model import EQSettings
        ft.read_eq.return_value = EQSettings.default()
        ft.read_extended_for_mode.return_value = {}
        ft.get_log.return_value = None

        worker = _ProfileIoWorker(
            ft,
            write=False,
            live_mode_group="AM",
            target_mode=RxMode.AM,
        )
        # time.sleep abfangen, damit der Test schnell bleibt
        with patch("gui.profile_widget.time.sleep") as sleep_mock:
            worker._do_read()
        ft.set_rx_mode.assert_called_once_with(RxMode.AM)
        sleep_mock.assert_called_once()  # kurze Pause nach dem Mode-Set


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
