"""Tests für DATA-FM / Menü-070+072-Umschaltung beim Audio-Player / Recorder."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from audio.radio_playback_setup import RadioPlaybackSetup
from mapping.extended_mapping import DATA_IN_SELECT_MENU, DATA_PORT_MENU
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
            ft.read_menu.assert_has_calls(
                [
                    call(DATA_IN_SELECT_MENU),
                    call(DATA_PORT_MENU),
                ]
            )
            ft.write_menu.assert_has_calls(
                [
                    call(DATA_IN_SELECT_MENU, "1", tx_lock=True),
                    call(DATA_PORT_MENU, "1", tx_lock=True),
                ]
            )

            ok2, _ = setup.restore()
            self.assertTrue(ok2)
            self.assertFalse(setup.is_applied)
            ft.write_menu.assert_has_calls(
                [
                    call(DATA_IN_SELECT_MENU, "0", tx_lock=False),
                    call(DATA_PORT_MENU, "0", tx_lock=False),
                ]
            )
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
