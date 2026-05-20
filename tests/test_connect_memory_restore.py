"""Connect-Init: Funkzustand merken und nach Init wiederherstellen."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from gui.main_window import MainWindow
from mapping.memory_mapping import MemoryChannel
from mapping.rx_mapping import RxMode


class ConnectMemoryRestoreTest(unittest.TestCase):
    def test_finish_connect_init_restores_memory_channel(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()
        win._connect_restore_memory_channel = 15
        win._connect_restore_vfo_a_hz = 145_500_000
        win._connect_restore_vfo_b_hz = None
        win._connect_restore_mode = RxMode.FM
        win._connect_init_pending = 1
        win._mode_label = MagicMock()
        win._vfo_b_triplet = MagicMock()
        win._vfo_b_caption = MagicMock()
        win.statusBar = MagicMock(return_value=MagicMock())

        with patch.object(MainWindow, "_sync_memory_combo_from_radio") as sync_mock, patch(
            "gui.main_window.FT991CAT"
        ) as ft_cls:
            ft = ft_cls.return_value
            ft.read_frequency.return_value = 145_500_000
            ft.read_frequency_b.return_value = 0
            ft.read_rx_mode.return_value = RxMode.FM
            win._apply_vfo_a_display_hz = MagicMock()
            win._notify_meter_app_frequency_write = MagicMock()
            win._update_vfo_caption_band_color = MagicMock()
            win.profile_widget = MagicMock()

            win._finish_connect_init()

            ft.select_memory_channel.assert_called_once_with(15)
            ft.switch_to_vfo_mode.assert_not_called()
            sync_mock.assert_called_once()
            self.assertIsNone(win._connect_restore_memory_channel)
            self.assertEqual(win._connect_init_pending, 0)

    def test_finish_connect_init_restores_vfo_state(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()
        win._connect_restore_memory_channel = None
        win._connect_restore_vfo_a_hz = 14_250_000
        win._connect_restore_vfo_b_hz = 14_300_000
        win._connect_restore_mode = RxMode.USB
        win._connect_init_pending = 1
        win._mode_label = MagicMock()
        win._vfo_b_triplet = MagicMock()
        win._vfo_b_caption = MagicMock()
        win.statusBar = MagicMock(return_value=MagicMock())

        with patch.object(MainWindow, "_sync_memory_combo_from_radio") as sync_mock, patch(
            "gui.main_window.FT991CAT"
        ) as ft_cls:
            ft = ft_cls.return_value
            ft.switch_to_vfo_mode.return_value = True
            ft.read_frequency.return_value = 14_250_000
            ft.read_frequency_b.return_value = 14_300_000
            ft.read_rx_mode.return_value = RxMode.USB
            win._apply_vfo_a_display_hz = MagicMock()
            win._notify_meter_app_frequency_write = MagicMock()
            win._update_vfo_caption_band_color = MagicMock()
            win.profile_widget = MagicMock()

            win._finish_connect_init()

            ft.select_memory_channel.assert_not_called()
            ft.switch_to_vfo_mode.assert_called_once()
            ft.write_frequency.assert_called_once_with(14_250_000)
            ft.write_frequency_b.assert_called_once_with(14_300_000)
            ft.set_rx_mode.assert_called_once_with(RxMode.USB)
            sync_mock.assert_called_once()

    def test_prepare_connect_switches_vfo_only_from_memory(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()
        win._connect_restore_memory_channel = 7

        with patch("gui.main_window.FT991CAT") as ft_cls:
            win._prepare_connect_for_cat_bulk_io()
            ft_cls.return_value.switch_to_vfo_mode.assert_called_once()

        win._connect_restore_memory_channel = None
        with patch("gui.main_window.FT991CAT") as ft_cls:
            win._prepare_connect_for_cat_bulk_io()
            ft_cls.return_value.switch_to_vfo_mode.assert_not_called()

    def test_capture_connect_radio_state(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()
        win._mode_label = MagicMock()
        win._vfo_b_triplet = MagicMock()
        win._vfo_b_caption = MagicMock()

        mc_ch = MemoryChannel(
            channel=42,
            frequency_hz=7_100_000,
            mode=RxMode.LSB,
            tag="R",
        )

        with patch.object(
            MainWindow, "_apply_connect_snapshot_to_ui"
        ) as apply_mock, patch("gui.main_window.FT991CAT") as ft_cls:
            ft = ft_cls.return_value
            ft.read_active_memory_channel.return_value = 42
            ft.read_memory_channel_tag.return_value = mc_ch
            ft.read_rx_mode.return_value = RxMode.LSB
            ft.read_frequency.return_value = 7_100_000
            ft.read_frequency_b.return_value = 7_200_000

            win._capture_connect_radio_state()

            self.assertEqual(win._connect_restore_memory_channel, 42)
            self.assertEqual(win._connect_restore_vfo_a_hz, 7_100_000)
            self.assertEqual(win._connect_restore_vfo_b_hz, 7_200_000)
            self.assertEqual(win._connect_restore_mode, RxMode.LSB)
            apply_mock.assert_called_once()

            ft.read_frequency.return_value = 144_000_000
            win._capture_connect_radio_state()
            self.assertIsNone(win._connect_restore_memory_channel)
            self.assertEqual(win._connect_restore_vfo_a_hz, 144_000_000)

            ft.read_active_memory_channel.return_value = None
            win._capture_connect_radio_state()
            self.assertIsNone(win._connect_restore_memory_channel)
            self.assertEqual(win._connect_restore_vfo_a_hz, 144_000_000)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
