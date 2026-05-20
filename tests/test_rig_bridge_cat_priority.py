"""Rig-Bridge: CAT-Priorität für FLRig (kein blockierendes Lesen im TCP-Thread)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from rig_bridge.manager import RigBridgeManager


class RigBridgeCatRefreshTest(unittest.TestCase):
    def test_refresh_enqueues_readfreq_not_sync_cat(self) -> None:
        cat = MagicMock()
        cat.is_connected.return_value = True
        mgr = RigBridgeManager(
            {"enabled": True, "flrig": {"enabled": False, "autostart": False}},
            get_cat=lambda: cat,
            log_write=lambda _l, _m: None,
        )
        mgr.on_app_connected()
        with patch.object(mgr._backend, "write_command") as wc:
            self.assertTrue(mgr.request_cat_refresh_async())
            wc.assert_called_once()
            args, kwargs = wc.call_args
            self.assertEqual(args[0], "READFREQ")
        mgr.on_app_disconnected()


class MeterPollerFlrigNoteTest(unittest.TestCase):
    def test_note_flrig_does_not_set_app_write_guard(self) -> None:
        from gui.meter_widget import MeterPoller

        cat = MagicMock()
        poller = MeterPoller(cat)
        poller.note_flrig_frequency_hz(14_074_000)
        self.assertEqual(poller._last_polled_freq_a, 14_074_000)
        self.assertEqual(poller._last_app_write_hz, 0)


if __name__ == "__main__":
    unittest.main()
