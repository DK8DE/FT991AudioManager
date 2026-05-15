"""Tests für Zeilenverschieben im Speicherkanal-Editor."""

from __future__ import annotations

import unittest

from gui.memory_editor_table import MemoryEditorTableModel
from model.memory_editor_channel import MemoryChannelBank, MemoryEditorChannel


class MemoryEditorTableReorderTest(unittest.TestCase):
    def test_reorder_row_move_up(self) -> None:
        bank = MemoryChannelBank()
        bank.channels[0] = MemoryEditorChannel(
            number=1, enabled=True, name="A", rx_frequency_hz=145_000_000
        )
        bank.channels[1] = MemoryEditorChannel(
            number=2, enabled=True, name="B", rx_frequency_hz=145_100_000
        )
        model = MemoryEditorTableModel(bank)
        self.assertTrue(model.reorder_row(1, 0))
        self.assertEqual(bank.channels[0].name, "B")
        self.assertEqual(bank.channels[1].name, "A")

    def test_reorder_row_moves_channel(self) -> None:
        bank = MemoryChannelBank()
        bank.channels[0] = MemoryEditorChannel(
            number=1, enabled=True, name="AAA", rx_frequency_hz=145_000_000
        )
        bank.channels[1] = MemoryEditorChannel(
            number=2, enabled=True, name="BBB", rx_frequency_hz=145_100_000
        )
        model = MemoryEditorTableModel(bank)
        self.assertTrue(model.reorder_row(0, 1))
        self.assertEqual(bank.channels[0].name, "BBB")
        self.assertEqual(bank.channels[1].name, "AAA")
        self.assertEqual(bank.channels[0].number, 1)
        self.assertEqual(bank.channels[1].number, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
