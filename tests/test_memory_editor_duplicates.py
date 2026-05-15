"""Tests für doppelte Frequenzen im Speicherkanal-Editor."""

from __future__ import annotations

import unittest

from model.memory_editor_channel import MemoryChannelBank, MemoryEditorChannel


class DuplicateFrequencyTest(unittest.TestCase):
    def test_duplicate_frequency_hz(self) -> None:
        bank = MemoryChannelBank()
        bank.channels[0] = MemoryEditorChannel(
            number=1, enabled=True, rx_frequency_hz=145_500_000
        )
        bank.channels[1] = MemoryEditorChannel(
            number=2, enabled=True, rx_frequency_hz=145_500_000
        )
        bank.channels[2] = MemoryEditorChannel(
            number=3, enabled=True, rx_frequency_hz=432_375_000
        )
        self.assertEqual(bank.duplicate_frequency_hz(), {145_500_000})


    def test_channels_for_radio_write_includes_trailing_empty(self) -> None:
        bank = MemoryChannelBank()
        bank.channels[0] = MemoryEditorChannel(
            number=1, enabled=True, name="A", rx_frequency_hz=145_500_000
        )
        for i in range(1, 100):
            bank.channels[i] = MemoryEditorChannel.empty_slot(i + 1)
        bank.channels[50].changed = True
        to_write = bank.channels_for_radio_write()
        numbers = [ch.number for ch in to_write]
        self.assertEqual(len(to_write), 100)
        self.assertIn(51, numbers)
        self.assertIn(100, numbers)

    def test_empty_slot_count(self) -> None:
        bank = MemoryChannelBank()
        bank.channels[0] = MemoryEditorChannel(
            number=1, enabled=True, name="A", rx_frequency_hz=145_500_000
        )
        self.assertEqual(bank.empty_slot_count(), 99)

    def test_append_imported(self) -> None:
        bank = MemoryChannelBank()
        bank.channels[0] = MemoryEditorChannel(
            number=1, enabled=True, name="A", rx_frequency_hz=145_500_000
        )
        imported = [
            MemoryEditorChannel(
                number=99, enabled=True, name="B", rx_frequency_hz=145_600_000
            ),
            MemoryEditorChannel(number=100, enabled=False),
        ]
        appended, skipped = bank.append_imported(imported)
        self.assertEqual(appended, 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(bank.channels[1].name, "B")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
