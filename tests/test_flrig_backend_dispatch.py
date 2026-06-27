"""Tests für FLRig-CAT-Bridge-Befehle (Ft991SharedCatBackend._dispatch)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from rig_bridge.ft991_backend import Ft991SharedCatBackend, _WriteCommand
from rig_bridge.state import RadioStateCache


class FlrigBackendDispatchTest(unittest.TestCase):
    def _backend_with_mock_ft(self) -> tuple[Ft991SharedCatBackend, MagicMock, unittest.mock._patch]:
        from mapping.rx_mapping import AgcMode, RxMode

        state = RadioStateCache()
        cat = MagicMock()
        cat.is_connected.return_value = True
        backend = Ft991SharedCatBackend(
            state,
            get_cat=lambda: cat,
            log_write=lambda *_a, **_k: None,
        )
        ft = MagicMock()
        ft.read_rx_mode.return_value = RxMode.USB
        ft.read_tx_bandwidth_sh.return_value = 16
        ft.read_if_shift_direction.return_value = 0
        ft.get_tx_status.return_value = False
        ft.read_agc.return_value = AgcMode.AUTO
        ft.read_auto_notch.return_value = False
        ft.read_af_gain.return_value = 128
        ft.read_rf_gain.return_value = 128
        ft.get_mic_gain.return_value = 50
        ft.read_pc_power_watts.return_value = 50
        p = patch("rig_bridge.ft991_backend.FT991CAT", return_value=ft)
        p.start()
        return backend, ft, p

    def test_setvol_dispatches_af_gain(self) -> None:
        backend, ft, p = self._backend_with_mock_ft()
        try:
            backend._dispatch(_WriteCommand(command="SETVOL 80", log_ctx="test"))
            ft.write_af_gain.assert_called_once()
            self.assertEqual(backend._state.volume, 80)
        finally:
            p.stop()

    def test_setfreqb_writes_vfo_b(self) -> None:
        backend, ft, p = self._backend_with_mock_ft()
        try:
            backend._dispatch(_WriteCommand(command="SETFREQB 145500000", log_ctx="test"))
            ft.write_frequency_b.assert_called_once_with(145500000)
            self.assertEqual(backend._state.frequency_hz_b, 145500000)
        finally:
            p.stop()

    def test_swapvfo_calls_cat(self) -> None:
        backend, ft, p = self._backend_with_mock_ft()
        try:
            ft.read_frequency.return_value = 14_074_000
            ft.read_frequency_b.return_value = 14_074_200
            backend._dispatch(_WriteCommand(command="SWAPVFO", log_ctx="test"))
            ft.swap_vfo_a_and_b.assert_called_once()
        finally:
            p.stop()

    def test_catstring_sends_raw_command(self) -> None:
        backend, ft, p = self._backend_with_mock_ft()
        try:
            cat = backend._get_cat()
            cat.send_command.return_value = "ID0670;"
            backend._dispatch(_WriteCommand(command="CATSTRING ID;", log_ctx="test"))
            cat.send_command.assert_called_once()
            self.assertEqual(backend._state.cat_string_response, "ID0670;")
        finally:
            p.stop()


if __name__ == "__main__":
    unittest.main()
