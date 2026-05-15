"""Tests fuer die Memory-Channel-Methoden in ``FT991CAT``."""

from __future__ import annotations

import unittest
from typing import Dict, Optional

from cat import CatCommandUnsupportedError
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.memory_mapping import MemoryChannel
from mapping.rx_mapping import RxMode
from model.memory_editor_channel import MemoryEditorChannel


class _FakeSerialCAT(SerialCAT):
    """SerialCAT-Fake mit programmierbarem Response-Mapping.

    Wenn ein Command keinen Eintrag im Mapping hat, wird die Antwort
    aus ``default_response`` geliefert oder eine
    :class:`CatCommandUnsupportedError` geworfen, falls ``default`` ``"?;"``.
    """

    def __init__(
        self,
        responses: Optional[Dict[str, str]] = None,
        *,
        default_response: str = "",
    ) -> None:
        super().__init__()
        self.responses: Dict[str, str] = responses or {}
        self.default_response = default_response
        self.commands: list[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(  # type: ignore[override]
        self,
        command: str,
        *,
        read_response: bool = True,
        expected_prefix: Optional[str] = None,
    ) -> str:
        self.commands.append(command)
        if not read_response:
            return ""
        response = self.responses.get(command, self.default_response)
        if response == "?;":
            raise CatCommandUnsupportedError(
                f"Funkgeraet kennt Befehl {command!r} nicht (Antwort '?;')"
            )
        return response


class ReadMemoryChannelTagTest(unittest.TestCase):
    def test_returns_channel_for_filled_slot(self) -> None:
        cat = _FakeSerialCAT(
            {"MT012;": "MT012145500000+0000004100000RELAIS DB0XX;"}
        )
        ft = FT991CAT(cat)
        result = ft.read_memory_channel_tag(12)
        self.assertIsNotNone(result)
        assert isinstance(result, MemoryChannel)
        self.assertEqual(result.channel, 12)
        self.assertEqual(result.frequency_hz, 145_500_000)
        self.assertEqual(result.tag, "RELAIS DB0XX")
        self.assertEqual(result.mode, RxMode.FM)

    def test_overrides_channel_when_echo_differs(self) -> None:
        """FT-991/A-Bug: Echo zeigt den *aktiven* Channel statt des
        angefragten. Der Inhalt gehoert aber zum angefragten Slot —
        wir muessen ``channel`` im Result auf die Anfrage setzen.
        """
        # Anfrage MT001, Antwort enthaelt aber "MT008..." (Echo des
        # aktiven Channels) mit Tag/Frequenz des Kanals 001.
        cat = _FakeSerialCAT(
            {"MT001;": "MT008446012500+0000004100000Luca        ;"}
        )
        ft = FT991CAT(cat)
        result = ft.read_memory_channel_tag(1)
        self.assertIsNotNone(result)
        assert isinstance(result, MemoryChannel)
        # WICHTIG: Channel-Nr im Result entspricht der ANFRAGE, nicht
        # dem Antwort-Echo.
        self.assertEqual(result.channel, 1)
        self.assertEqual(result.frequency_hz, 446_012_500)
        self.assertEqual(result.tag, "Luca")
        self.assertEqual(result.mode, RxMode.FM)

    def test_returns_none_for_empty_slot(self) -> None:
        cat = _FakeSerialCAT(
            {"MT042;": "MT042000000000+0000000000000            ;"}
        )
        ft = FT991CAT(cat)
        self.assertIsNone(ft.read_memory_channel_tag(42))

    def test_propagates_unsupported_for_question_mark(self) -> None:
        cat = _FakeSerialCAT({"MT099;": "?;"})
        ft = FT991CAT(cat)
        with self.assertRaises(CatCommandUnsupportedError):
            ft.read_memory_channel_tag(99)


class SelectMemoryChannelTest(unittest.TestCase):
    def test_sends_mc_command(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        ft.select_memory_channel(7)
        self.assertIn("MC007;", cat.commands)


class WriteMemoryEditorChannelTest(unittest.TestCase):
    def test_empty_channel_uses_mw_clear_only(self) -> None:
        cat = _FakeSerialCAT()
        ft = FT991CAT(cat)
        ft.write_memory_editor_channel(
            MemoryEditorChannel(number=91, enabled=False, rx_frequency_hz=0)
        )
        self.assertIn("MW091000000000+0000000000000;", cat.commands)
        self.assertNotIn("MT091000000000+0000000000000            ;", cat.commands)


class ReadActiveMemoryChannelTest(unittest.TestCase):
    def test_returns_int_when_in_memory_mode(self) -> None:
        cat = _FakeSerialCAT({"MC;": "MC012;"})
        ft = FT991CAT(cat)
        self.assertEqual(ft.read_active_memory_channel(), 12)

    def test_returns_none_when_in_vfo_mode(self) -> None:
        # ``?;`` → CatCommandUnsupportedError → wird in read_active_memory_channel
        # zu None umgewandelt.
        cat = _FakeSerialCAT({"MC;": "?;"})
        ft = FT991CAT(cat)
        self.assertIsNone(ft.read_active_memory_channel())


class SwitchToVfoModeTest(unittest.TestCase):
    def test_reads_fa_and_writes_back(self) -> None:
        # FA; liefert die aktuelle VFO-A-Frequenz; wir schreiben sie
        # unveraendert zurueck und triggern damit den VFO-Modus.
        cat = _FakeSerialCAT({"FA;": "FA014250000;"})
        ft = FT991CAT(cat)
        self.assertTrue(ft.switch_to_vfo_mode())
        self.assertIn("FA;", cat.commands)
        self.assertIn("FA014250000;", cat.commands)

    def test_returns_false_on_read_failure(self) -> None:
        # Wenn FA;-Read fehlschlaegt (Funkgeraet antwortet `?;`), gibt
        # die Methode False zurueck und schreibt nicht zurueck.
        cat = _FakeSerialCAT({"FA;": "?;"})
        ft = FT991CAT(cat)
        self.assertFalse(ft.switch_to_vfo_mode())
        # Kein FA-Write darf in commands stehen
        writes = [c for c in cat.commands if c.startswith("FA0")]
        self.assertEqual(writes, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
