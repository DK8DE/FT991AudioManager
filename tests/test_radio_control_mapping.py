"""Tests für Band-/Kanal-CAT-Befehle."""

from __future__ import annotations

import unittest

from mapping.radio_control_mapping import (
    format_band_down,
    format_band_up,
    format_memory_channel_down,
    format_memory_channel_up,
)


class RadioControlMappingTest(unittest.TestCase):
    def test_band_commands(self) -> None:
        self.assertEqual(format_band_up(), "BU0;")
        self.assertEqual(format_band_down(), "BD0;")

    def test_memory_channel_commands(self) -> None:
        self.assertEqual(format_memory_channel_up(), "CH0;")
        self.assertEqual(format_memory_channel_down(), "CH1;")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
