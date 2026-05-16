"""Tests für die Auto-Sync-Mechanik im ProfileWidget.

Wir verifizieren das Verhalten an den nicht-CAT-abhängigen Teilen
(``_schedule_action``, Debounce-Timer, ``set_cat_available``,
Pending-Queue). Echte Worker-Starts werden über einen Mock-SerialCAT
und über ein Patchen von ``_start_worker`` umgangen, damit kein
serieller Port benötigt wird.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.profile_widget import ProfileWidget  # noqa: E402
from model import AudioProfile, PresetStore  # noqa: E402
from model.preset_store import DEFAULT_PROFILE_NAME  # noqa: E402


def _ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_widget(connected: bool = True) -> ProfileWidget:
    _ensure_qapp()
    cat = MagicMock()
    cat.is_connected.return_value = connected
    # Eigener Store auf tmp-Pfad, damit Tests keine echten Profile anrühren.
    tmp_path = Path(tempfile.mkdtemp(prefix="ft991_test_")) / "presets.json"
    store = PresetStore.load(tmp_path)
    return ProfileWidget(cat, store)


class InitialProfileSelectionTest(unittest.TestCase):
    def test_startup_selects_last_profile_from_settings(self) -> None:
        _ensure_qapp()
        tmp_path = Path(tempfile.mkdtemp(prefix="ft991_test_")) / "presets.json"
        store = PresetStore.load(tmp_path)
        store.upsert(AudioProfile(name="Alpha"))
        store.upsert(AudioProfile(name="Beta"))
        store.save()
        cat = MagicMock()
        cat.is_connected.return_value = False
        widget = ProfileWidget(cat, store, initial_last_profile="Beta")
        self.assertEqual(widget.current_profile_name(), "Beta")

    def test_unknown_last_profile_falls_back_to_default(self) -> None:
        _ensure_qapp()
        tmp_path = Path(tempfile.mkdtemp(prefix="ft991_test_")) / "presets.json"
        store = PresetStore.load(tmp_path)
        store.ensure_defaults()
        cat = MagicMock()
        cat.is_connected.return_value = False
        widget = ProfileWidget(cat, store, initial_last_profile="NichtDa")
        self.assertEqual(widget.current_profile_name(), DEFAULT_PROFILE_NAME)


class AutoSyncStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = _make_widget(connected=False)

    def test_sync_label_reflects_connection_state(self) -> None:
        # initial nicht verbunden
        self.assertIn("aus", self.widget._sync_label.text())
        # connect
        self.widget._cat.is_connected.return_value = True
        # Verhindere echten Worker-Start beim Connect-Auto-Write
        self.widget._dispatch_action = MagicMock()
        self.widget.set_cat_available(True)
        self.assertIn("aktiv", self.widget._sync_label.text())
        self.widget._dispatch_action.assert_called_once()
        kind, _ = self.widget._dispatch_action.call_args.args
        self.assertEqual(kind, "write_full")
        # disconnect
        self.widget._cat.is_connected.return_value = False
        self.widget.set_cat_available(False)
        self.assertIn("aus", self.widget._sync_label.text())

    def test_disconnect_clears_pending_and_stops_timer(self) -> None:
        self.widget._pending_action = ("write_full", None)
        self.widget._auto_write_timer.start()
        self.assertTrue(self.widget._auto_write_timer.isActive())
        self.widget.set_cat_available(False)
        self.assertIsNone(self.widget._pending_action)
        self.assertFalse(self.widget._auto_write_timer.isActive())

    def test_mark_dirty_starts_timer_only_when_connected(self) -> None:
        # nicht verbunden → kein Timer
        self.widget._auto_write_timer.stop()
        self.widget._mark_dirty()
        self.assertFalse(self.widget._auto_write_timer.isActive())
        # verbunden → Timer läuft an
        self.widget._cat.is_connected.return_value = True
        self.widget._mark_dirty()
        self.assertTrue(self.widget._auto_write_timer.isActive())


class ScheduleActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = _make_widget(connected=True)
        self.widget._dispatch_action = MagicMock()

    def test_immediate_dispatch_when_idle(self) -> None:
        self.widget._schedule_action("read")
        self.widget._dispatch_action.assert_called_once()
        kind, _ = self.widget._dispatch_action.call_args.args
        self.assertEqual(kind, "read")

    def test_pending_queued_when_worker_busy(self) -> None:
        # Simuliere laufenden Worker
        self.widget._worker_thread = object()  # nicht None
        self.widget._schedule_action("write_full")
        self.widget._dispatch_action.assert_not_called()
        self.assertEqual(self.widget._pending_action[0], "write_full")
        # neue Aktion überschreibt vorherige Pending
        self.widget._schedule_action("read")
        self.assertEqual(self.widget._pending_action[0], "read")
        # cleanup
        self.widget._worker_thread = None

    def test_read_schedule_stops_debounce_timer(self) -> None:
        self.widget._auto_write_timer.start()
        self.assertTrue(self.widget._auto_write_timer.isActive())
        self.widget._schedule_action("read")
        self.assertFalse(self.widget._auto_write_timer.isActive())


class FlushAutoWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = _make_widget(connected=True)
        self.widget._dispatch_action = MagicMock()

    def test_flush_skips_when_disconnected(self) -> None:
        self.widget._cat.is_connected.return_value = False
        self.widget._flush_auto_write()
        self.widget._dispatch_action.assert_not_called()

    def test_flush_dispatches_write_full(self) -> None:
        self.widget._current_profile_name = self.widget.profile_combo.currentText()
        self.widget._flush_auto_write()
        self.widget._dispatch_action.assert_called_once()
        kind, _profile = self.widget._dispatch_action.call_args.args
        self.assertEqual(kind, "write_full")


class NotifyRadioModeTest(unittest.TestCase):
    """notify_radio_mode() folgt dem Radio nur bei echten Modus-Wechseln."""

    def setUp(self) -> None:
        self.widget = _make_widget(connected=True)
        # Auto-Read aus dem Mode-Wechsel-Pfad abfangen.
        self.widget._dispatch_action = MagicMock()

    def test_switches_combo_for_cw_mode(self) -> None:
        from mapping.rx_mapping import RxMode
        self.widget._last_radio_mode = RxMode.USB
        self.widget.notify_radio_mode(RxMode.CW_U)
        self.assertEqual(self.widget.mode_combo.currentText(), "CW-U")

    def test_switches_combo_on_mode_change(self) -> None:
        from mapping.rx_mapping import RxMode
        self.widget._last_radio_mode = RxMode.USB
        self.widget.notify_radio_mode(RxMode.AM)
        self.assertEqual(self.widget.mode_combo.currentText(), "AM")
        self.widget._dispatch_action.reset_mock()
        self.widget.notify_radio_mode(RxMode.AM_N)
        self.assertEqual(self.widget.mode_combo.currentText(), "AM-N")
        self.widget._dispatch_action.assert_called_once()
        kind, _payload = self.widget._dispatch_action.call_args.args
        self.assertEqual(kind, "read")

    def test_user_lock_suppresses_pong_after_manual_switch(self) -> None:
        """Verzögertes Polling mit altem Modus darf die Combo nicht zurücksetzen."""
        from mapping.rx_mapping import RxMode
        import time as _time
        self.widget._last_radio_mode = RxMode.FM
        self.widget._user_mode_lock_until = _time.monotonic() + 4.0
        idx_fm = self.widget.mode_combo.findText("FM")
        self.widget.mode_combo.setCurrentIndex(idx_fm)
        self.widget._dispatch_action.reset_mock()
        self.widget.notify_radio_mode(RxMode.USB)
        self.assertEqual(self.widget.mode_combo.currentText(), "FM")
        self.assertEqual(self.widget._last_radio_mode, RxMode.FM)

    def test_user_lock_expires_and_combo_follows_radio(self) -> None:
        from mapping.rx_mapping import RxMode
        import time as _time
        self.widget._last_radio_mode = RxMode.FM
        self.widget._user_mode_lock_until = _time.monotonic() - 0.1
        idx_fm = self.widget.mode_combo.findText("FM")
        self.widget.mode_combo.setCurrentIndex(idx_fm)
        self.widget._dispatch_action.reset_mock()
        self.widget.notify_radio_mode(RxMode.USB)
        self.assertEqual(self.widget.mode_combo.currentText(), "USB")


class OnModeChangedTest(unittest.TestCase):
    """Manueller Mode-Wechsel in der GUI triggert je nach Radio-Stand
    entweder ein reines Read oder ein „Mode setzen + Read"."""

    def setUp(self) -> None:
        self.widget = _make_widget(connected=True)
        self.widget._dispatch_action = MagicMock()

    def _switch_combo_to(self, group: str) -> None:
        idx = self.widget.mode_combo.findText(group)
        self.assertGreaterEqual(idx, 0)
        self.widget.mode_combo.setCurrentIndex(idx)

    def test_user_switches_to_different_mode_triggers_mode_set(self) -> None:
        from mapping.rx_mapping import RxMode
        import time as _time
        self.widget._last_radio_mode = RxMode.USB
        self.widget._user_mode_lock_until = 0.0
        self._switch_combo_to("USB")
        self.widget._dispatch_action.reset_mock()
        before = _time.monotonic()
        self._switch_combo_to("AM")
        self.widget._dispatch_action.assert_called_once()
        kind, payload = self.widget._dispatch_action.call_args.args
        self.assertEqual(kind, "set_mode_and_read")
        self.assertEqual(payload, RxMode.AM)
        self.assertEqual(self.widget._last_radio_mode, RxMode.AM)
        self.assertGreaterEqual(
            self.widget._user_mode_lock_until - before, 3.5
        )

    def test_user_picks_same_mode_as_radio_only_reads(self) -> None:
        from mapping.rx_mapping import RxMode
        self.widget._last_radio_mode = RxMode.AM
        self._switch_combo_to("AM")
        # Möglicherweise wurde gar nichts gedispatcht, wenn der Index sich
        # nicht änderte. In jedem Fall darf KEIN „set_mode_and_read"
        # passieren.
        for call in self.widget._dispatch_action.call_args_list:
            kind, _payload = call.args
            self.assertNotEqual(kind, "set_mode_and_read")


class NotifyTxStateTest(unittest.TestCase):
    """notify_tx_state() löst beim TX→RX-Übergang einen Retry aus."""

    def setUp(self) -> None:
        self.widget = _make_widget(connected=True)
        self.widget._dispatch_action = MagicMock()

    def test_no_action_when_idle(self) -> None:
        self.widget.notify_tx_state(False)
        self.widget._dispatch_action.assert_not_called()

    def test_tx_to_rx_with_pending_block_flushes(self) -> None:
        self.widget._tx_active = True
        self.widget._tx_block_pending = True
        self.widget._dirty = True
        self.widget._current_profile_name = self.widget.profile_combo.currentText()
        self.widget.notify_tx_state(False)
        # _flush_auto_write → _schedule_action → _dispatch_action wird einmal
        # gerufen mit kind="write_full"
        self.widget._dispatch_action.assert_called_once()
        kind, _profile = self.widget._dispatch_action.call_args.args
        self.assertEqual(kind, "write_full")

    def test_rx_to_tx_blocks_write(self) -> None:
        """Während TX aktiv → write_full wird zu pending tx_block, kein dispatch."""
        self.widget._tx_active = True
        # Direkter Dispatch über write_full mit TX an
        # (statt das _dispatch_action-Mock zu rufen, prüfen wir den
        # echten Pfad mit gestopptem _start_worker)
        self.widget._dispatch_action = type(self.widget)._dispatch_action.__get__(self.widget)
        self.widget._start_worker = MagicMock()
        self.widget._current_profile_name = self.widget.profile_combo.currentText()
        self.widget._schedule_action(
            "write_full", self.widget._build_profile_from_editors("test")
        )
        self.widget._start_worker.assert_not_called()
        self.assertTrue(self.widget._tx_block_pending)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
