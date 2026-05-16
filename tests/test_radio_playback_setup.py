"""Tests für DATA-FM / Menü-072-Umschaltung beim Audio-Player."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from audio.radio_playback_setup import RadioAudioSnapshot, RadioPlaybackSetup
from mapping.extended_mapping import DATA_PORT_MENU
from mapping.rx_mapping import RxMode


class RadioPlaybackSetupTest(unittest.TestCase):
    def test_apply_and_restore(self) -> None:
        cat = MagicMock()
        cat.is_connected.return_value = True
        setup = RadioPlaybackSetup(cat)

        with patch("audio.radio_playback_setup.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.read_rx_mode.return_value = RxMode.USB
            ft.read_menu.return_value = "0"
            ft.set_rx_mode.return_value = True

            ok, msg = setup.apply()
            self.assertTrue(ok)
            self.assertTrue(setup.is_applied)
            ft.set_rx_mode.assert_called_with(RxMode.DATA_FM)
            ft.write_menu.assert_called()

            ok2, _ = setup.restore()
            self.assertTrue(ok2)
            self.assertFalse(setup.is_applied)
            ft.write_menu.assert_called_with(DATA_PORT_MENU, "0", tx_lock=False)
            ft.set_rx_mode.assert_called_with(RxMode.USB)

    def test_apply_without_cat(self) -> None:
        cat = MagicMock()
        cat.is_connected.return_value = False
        setup = RadioPlaybackSetup(cat)
        ok, msg = setup.apply()
        self.assertFalse(ok)
        self.assertIn("nicht verbunden", msg.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
