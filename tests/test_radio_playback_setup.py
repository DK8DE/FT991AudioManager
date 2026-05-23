"""Tests für DATA-FM / Menü-070+072-Umschaltung beim Audio-Player / Recorder."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from audio.radio_playback_setup import (
    RadioAudioSnapshot,
    RadioPlaybackSetup,
    data_mode_for_rx_mode,
)
from mapping.extended_mapping import (
    AM_PORT_SELECT_MENU,
    DATA_IN_SELECT_MENU,
    DATA_PORT_MENU,
    FM_PKT_PORT_SELECT_MENU,
    SSB_PORT_SELECT_MENU,
)
from mapping.rx_mapping import RxMode


class DataModeForRxModeTest(unittest.TestCase):
    def test_voice_modes_map_to_matching_data(self) -> None:
        self.assertEqual(data_mode_for_rx_mode(RxMode.FM), RxMode.DATA_FM)
        self.assertEqual(data_mode_for_rx_mode(RxMode.FM_N), RxMode.DATA_FM)
        self.assertEqual(data_mode_for_rx_mode(RxMode.USB), RxMode.DATA_USB)
        self.assertEqual(data_mode_for_rx_mode(RxMode.LSB), RxMode.DATA_LSB)
        self.assertEqual(data_mode_for_rx_mode(RxMode.CW_U), RxMode.DATA_USB)
        self.assertEqual(data_mode_for_rx_mode(RxMode.CW_L), RxMode.DATA_LSB)

    def test_already_data_modes_unchanged(self) -> None:
        self.assertEqual(data_mode_for_rx_mode(RxMode.DATA_FM), RxMode.DATA_FM)
        self.assertEqual(data_mode_for_rx_mode(RxMode.DATA_USB), RxMode.DATA_USB)
        self.assertEqual(data_mode_for_rx_mode(RxMode.DATA_LSB), RxMode.DATA_LSB)


class RadioPlaybackSetupTest(unittest.TestCase):
    def test_engage_data_mode_uses_configured_data_not_snapshot_voice(self) -> None:
        """Hauptfenster USB nach Öffnen auf FM: Replay darf nicht DATA-FM erzwingen."""
        cat = MagicMock()
        cat.is_connected.return_value = True
        setup = RadioPlaybackSetup(cat, RxMode.DATA_FM)
        setup._snapshot = RadioAudioSnapshot(
            rx_mode=RxMode.FM,
            am_port_raw="0",
            data_in_select_raw="0",
            data_port_raw="0",
            fm_pkt_port_raw="0",
            ssb_port_raw="0",
        )
        setup._data_mode = RxMode.DATA_USB
        setup._in_data_mode = False

        with patch("audio.radio_playback_setup.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.set_rx_mode.return_value = True
            ok, _ = setup.engage_data_mode()
            self.assertTrue(ok)
            ft.set_rx_mode.assert_called_with(RxMode.DATA_USB)

    def test_apply_and_restore(self) -> None:
        cat = MagicMock()
        cat.is_connected.return_value = True
        setup = RadioPlaybackSetup(cat)
        setup.align_data_mode_to_rx_mode(RxMode.USB)

        with patch("audio.radio_playback_setup.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.read_rx_mode.return_value = RxMode.USB
            ft.read_menu.return_value = "0"
            ft.set_rx_mode.return_value = True

            ok, msg = setup.apply()
            self.assertTrue(ok)
            self.assertTrue(setup.is_applied)
            ft.set_rx_mode.assert_called_with(RxMode.DATA_USB)
            ft.read_menu.assert_has_calls(
                [
                    call(AM_PORT_SELECT_MENU),
                    call(DATA_IN_SELECT_MENU),
                    call(DATA_PORT_MENU),
                    call(FM_PKT_PORT_SELECT_MENU),
                    call(SSB_PORT_SELECT_MENU),
                ]
            )
            ft.write_menu.assert_has_calls(
                [
                    call(AM_PORT_SELECT_MENU, "1", tx_lock=True),
                    call(DATA_IN_SELECT_MENU, "1", tx_lock=True),
                    call(DATA_PORT_MENU, "1", tx_lock=True),
                    call(FM_PKT_PORT_SELECT_MENU, "1", tx_lock=True),
                    call(SSB_PORT_SELECT_MENU, "1", tx_lock=True),
                ]
            )

            ok2, _ = setup.restore()
            self.assertTrue(ok2)
            self.assertFalse(setup.is_applied)
            ft.write_menu.assert_has_calls(
                [
                    call(AM_PORT_SELECT_MENU, "0", tx_lock=False),
                    call(DATA_IN_SELECT_MENU, "0", tx_lock=False),
                    call(DATA_PORT_MENU, "0", tx_lock=False),
                    call(FM_PKT_PORT_SELECT_MENU, "0", tx_lock=False),
                    call(SSB_PORT_SELECT_MENU, "0", tx_lock=False),
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

    def test_pc_menus_only_then_apply_engages_data(self) -> None:
        cat = MagicMock()
        cat.is_connected.return_value = True
        setup = RadioPlaybackSetup(cat)
        with patch("audio.radio_playback_setup.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.read_rx_mode.return_value = RxMode.FM
            ft.read_menu.return_value = "0"
            ft.set_rx_mode.return_value = True

            ok, _ = setup.apply_pc_audio_menus_only()
            self.assertTrue(ok)
            self.assertTrue(setup.is_applied)
            self.assertFalse(setup.in_data_mode)
            ft.set_rx_mode.assert_not_called()

            ok2, _ = setup.apply()
            self.assertTrue(ok2)
            self.assertTrue(setup.in_data_mode)
            self.assertEqual(ft.set_rx_mode.call_args.args[0], RxMode.DATA_FM)

    def test_reconcile_clears_stale_in_data_when_radio_left_data(self) -> None:
        """Speicherkanal FM: intern noch DATA, Gerät schon FM → PTT muss engage_data."""
        cat = MagicMock()
        cat.is_connected.return_value = True
        setup = RadioPlaybackSetup(cat, RxMode.DATA_FM)
        setup._snapshot = RadioAudioSnapshot(
            rx_mode=RxMode.DATA_FM,
            am_port_raw="0",
            data_in_select_raw="0",
            data_port_raw="0",
            fm_pkt_port_raw="0",
            ssb_port_raw="0",
        )
        setup._in_data_mode = True

        with patch("audio.radio_playback_setup.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.read_rx_mode.return_value = RxMode.FM
            self.assertFalse(setup.reconcile_in_data_mode_with_radio())
            self.assertFalse(setup.in_data_mode)

            setup._in_data_mode = True
            ft.read_rx_mode.return_value = RxMode.DATA_FM
            self.assertTrue(setup.reconcile_in_data_mode_with_radio())
            self.assertTrue(setup.in_data_mode)

    def test_engage_data_mode_when_stale_in_data_flag(self) -> None:
        """``_in_data_mode`` True, Gerät in FM — muss trotzdem DATA schalten."""
        cat = MagicMock()
        cat.is_connected.return_value = True
        setup = RadioPlaybackSetup(cat, RxMode.DATA_FM)
        setup._snapshot = RadioAudioSnapshot(
            rx_mode=RxMode.FM,
            am_port_raw="0",
            data_in_select_raw="0",
            data_port_raw="0",
            fm_pkt_port_raw="0",
            ssb_port_raw="0",
        )
        setup._in_data_mode = True

        with patch("audio.radio_playback_setup.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.read_rx_mode.return_value = RxMode.FM
            ft.set_rx_mode.return_value = True
            ok, _ = setup.engage_data_mode()
            self.assertTrue(ok)
            ft.set_rx_mode.assert_called_with(RxMode.DATA_FM)
            self.assertTrue(setup.in_data_mode)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
