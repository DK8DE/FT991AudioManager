"""Connect-Init: Speicherkanal merken und nach Init wiederherstellen."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from gui.main_window import MainWindow
from mapping.rx_mapping import RxMode


class ConnectMemoryRestoreTest(unittest.TestCase):
    def test_finish_connect_init_restores_memory_channel(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()
        win._connect_restore_memory_channel = 15
        win._connect_init_pending = 1
        win._mode_label = MagicMock()
        win.statusBar = MagicMock(return_value=MagicMock())

        with patch.object(MainWindow, "_sync_memory_combo_from_radio") as sync_mock, patch(
            "gui.main_window.FT991CAT"
        ) as ft_cls:
            ft = ft_cls.return_value
            ft.read_frequency.return_value = 145_500_000
            ft.read_rx_mode.return_value = RxMode.FM
            win._apply_vfo_a_display_hz = MagicMock()

            win._finish_connect_init()

            ft.select_memory_channel.assert_called_once_with(15)
            sync_mock.assert_called_once()
            self.assertIsNone(win._connect_restore_memory_channel)
            self.assertEqual(win._connect_init_pending, 0)

    def test_finish_connect_init_skips_restore_when_was_vfo(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()
        win._connect_restore_memory_channel = None
        win._connect_init_pending = 1
        win.statusBar = MagicMock(return_value=MagicMock())

        with patch.object(MainWindow, "_sync_memory_combo_from_radio") as sync_mock, patch(
            "gui.main_window.FT991CAT"
        ) as ft_cls:
            ft = ft_cls.return_value
            win._finish_connect_init()

            ft.select_memory_channel.assert_not_called()
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

    def test_capture_connect_memory_state(self) -> None:
        win = MainWindow.__new__(MainWindow)
        win._cat = MagicMock()
        win._cat.is_connected.return_value = True
        win._cat_log = MagicMock()

        with patch("gui.main_window.FT991CAT") as ft_cls:
            ft_cls.return_value.read_active_memory_channel.return_value = 42
            win._capture_connect_memory_state()
            self.assertEqual(win._connect_restore_memory_channel, 42)

            ft_cls.return_value.read_active_memory_channel.return_value = None
            win._capture_connect_memory_state()
            self.assertIsNone(win._connect_restore_memory_channel)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
