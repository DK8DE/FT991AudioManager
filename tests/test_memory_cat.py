"""Tests fuer die Memory-Channel-Methoden in ``FT991CAT``."""

from __future__ import annotations

import unittest
from typing import Dict, Optional

from cat import CatCommandUnsupportedError
from cat.ft991_cat import FT991CAT
from cat.serial_cat import SerialCAT
from mapping.memory_mapping import MemoryChannel
from mapping.memory_tones import ToneMode
from mapping.rx_mapping import RxMode
from model.memory_editor_channel import MemoryEditorChannel, ShiftDirection


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
        default_response: str = "?;",
    ) -> None:
        super().__init__()
        self.commands: list[str] = []
        self._responses = dict(responses or {})
        self._default = default_response

    def connect(self, port: str, *, baudrate: int = 38400, timeout_ms: int = 1000) -> None:
        self._port = port

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def send_command(
        self,
        command: str,
        *,
        read_response: bool = True,
        expected_prefix: Optional[str] = None,
    ) -> str:
        if not command.endswith(";"):
            command = f"{command};"
        self.commands.append(command)
        if not read_response:
            return ""
        if command in self._responses:
            reply = self._responses[command]
            if reply == "?;":
                raise CatCommandUnsupportedError(
                    f"Funkgeraet kennt Befehl {command!r} nicht (Antwort '?;')"
                )
            return reply
        if self._default == "?;":
            raise CatCommandUnsupportedError(
                f"Funkgeraet kennt Befehl {command!r} nicht (Antwort '?;')"
            )
        return self._default


class ReadMemoryEditorChannelToneTest(unittest.TestCase):
    def test_reads_cn_after_mt_when_tone_active(self) -> None:
        mt = "MT091438975000+7600004020002XYZ         ;"
        cat = _FakeSerialCAT(
            {
                "MT091;": mt,
                "MC091;": "MC091;",
                "CN00;": "CN00017;",
                "FA;": "FA133000000;",
            }
        )
        ft = FT991CAT(cat)
        ch = ft.read_memory_editor_channel(91)
        self.assertEqual(ch.tone_mode, ToneMode.CTCSS_ENC)
        self.assertAlmostEqual(ch.ctcss_tone_hz, 118.8)
        self.assertIn("MC091;", cat.commands)
        self.assertIn("CN00;", cat.commands)

    def test_cn_index_zero_maps_to_67hz(self) -> None:
        mt = "MT091438975000+7600004020002XYZ         ;"
        cat = _FakeSerialCAT(
            {
                "MT091;": mt,
                "MC091;": "MC091;",
                "CN00;": "CN00000;",
                "FA;": "FA133000000;",
            }
        )
        ft = FT991CAT(cat)
        ch = ft.read_memory_editor_channel(91)
        self.assertEqual(ch.tone_mode, ToneMode.CTCSS_ENC)
        self.assertAlmostEqual(ch.ctcss_tone_hz, 67.0)


class ReadMemoryChannelTagTest(unittest.TestCase):
    def test_returns_channel_when_populated(self) -> None:
        response = "MT012145500000+0000004100000RELAIS DB0XX;"
        cat = _FakeSerialCAT({"MT012;": response})
        ft = FT991CAT(cat)
        ch = ft.read_memory_channel_tag(12)
        self.assertIsNotNone(ch)
        assert ch is not None
        self.assertEqual(ch.channel, 12)
        self.assertEqual(ch.frequency_hz, 145_500_000)

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
    def test_cleared_channel_uses_mt_only(self) -> None:
        cat = _FakeSerialCAT({"FA;": "FA133000000;"})
        ft = FT991CAT(cat)
        ft.write_memory_editor_channel(
            MemoryEditorChannel(number=91, enabled=False, rx_frequency_hz=0)
        )
        self.assertIn("MC091;", cat.commands)
        self.assertTrue(any(c.startswith("MT091") for c in cat.commands))
        self.assertNotIn("MW091", "".join(cat.commands))

    def test_ctcss_channel_uses_mc_cn_ct_mt(self) -> None:
        cat = _FakeSerialCAT({"FA;": "FA133000000;"})
        ft = FT991CAT(cat)
        ft.write_memory_editor_channel(
            MemoryEditorChannel(
                number=91,
                enabled=True,
                name="XYZ",
                rx_frequency_hz=438_975_000,
                mode=RxMode.FM,
                shift_direction=ShiftDirection.MINUS,
                shift_offset_hz=7_600_000,
                tone_mode=ToneMode.CTCSS_ENC,
                ctcss_tone_hz=118.8,
            )
        )
        self.assertIn("MC091;", cat.commands)
        self.assertIn("CN00017;", cat.commands)
        self.assertIn("CT02;", cat.commands)
        self.assertTrue(any(c.startswith("MT091") for c in cat.commands))
        self.assertNotIn("MW091", "".join(cat.commands))
        mc_idx = cat.commands.index("MC091;")
        cn_idx = cat.commands.index("CN00017;")
        mt_idx = next(i for i, c in enumerate(cat.commands) if c.startswith("MT091"))
        self.assertLess(mc_idx, cn_idx)
        self.assertLess(cn_idx, mt_idx)


class ReadActiveMemoryChannelTest(unittest.TestCase):
    def test_returns_int_when_in_memory_mode(self) -> None:
        cat = _FakeSerialCAT({"MC;": "MC012;"})
        ft = FT991CAT(cat)
        self.assertEqual(ft.read_active_memory_channel(), 12)

    def test_returns_none_when_in_vfo_mode(self) -> None:
        cat = _FakeSerialCAT({"MC;": "?;"})
        ft = FT991CAT(cat)
        self.assertIsNone(ft.read_active_memory_channel())


class SwitchToVfoModeTest(unittest.TestCase):
    def test_reads_fa_and_writes_back(self) -> None:
        cat = _FakeSerialCAT({"FA;": "FA014250000;"})
        ft = FT991CAT(cat)
        self.assertTrue(ft.switch_to_vfo_mode())
        self.assertIn("FA;", cat.commands)
        self.assertIn("FA014250000;", cat.commands)

    def test_returns_false_on_read_failure(self) -> None:
        cat = _FakeSerialCAT({"FA;": "?;"})
        ft = FT991CAT(cat)
        self.assertFalse(ft.switch_to_vfo_mode())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
