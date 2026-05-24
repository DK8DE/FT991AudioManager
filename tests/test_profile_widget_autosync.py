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
from typing import cast
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.profile_widget import ProfileWidget  # noqa: E402
from mapping.audio_mapping import MIC_GAIN_MAX, MIC_GAIN_MIN  # noqa: E402
from model import AudioProfile, PresetStore  # noqa: E402
from model.preset_store import DEFAULT_PROFILE_NAME  # noqa: E402


def _ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_widget(connected: bool = True) -> tuple[ProfileWidget, MagicMock]:
    _ensure_qapp()
    cat = MagicMock()
    cat.is_connected.return_value = connected
    # Eigener Store auf tmp-Pfad, damit Tests keine echten Profile anrühren.
    tmp_path = Path(tempfile.mkdtemp(prefix="ft991_test_")) / "presets.json"
    store = PresetStore.load(tmp_path)
    return ProfileWidget(cat, store), cat


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
        self.widget, self.cat = _make_widget(connected=False)
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

    def test_sync_label_reflects_connection_state(self) -> None:
        # initial nicht verbunden
        self.assertIn("aus", self.widget._sync_label.text())
        # connect
        self.cat.is_connected.return_value = True
        self.dispatch.reset_mock()
        self.widget.set_cat_available(True)
        self.assertIn("aktiv", self.widget._sync_label.text())
        self.dispatch.assert_called_once()
        kind, _ = self.dispatch.call_args.args
        self.assertEqual(kind, "write_full")
        # disconnect
        self.cat.is_connected.return_value = False
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
        self.cat.is_connected.return_value = True
        self.widget._mark_dirty()
        self.assertTrue(self.widget._auto_write_timer.isActive())


class ScheduleActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.widget, _cat = _make_widget(connected=True)
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

    def test_immediate_dispatch_when_idle(self) -> None:
        self.widget._schedule_action("read")
        self.dispatch.assert_called_once()
        kind, _ = self.dispatch.call_args.args
        self.assertEqual(kind, "read")

    def test_pending_queued_when_worker_busy(self) -> None:
        # Simuliere laufenden Worker
        self.widget._worker_thread = cast(QThread, MagicMock())
        self.widget._schedule_action("write_full")
        self.dispatch.assert_not_called()
        pending = self.widget._pending_action
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending[0], "write_full")
        # neue Aktion überschreibt vorherige Pending
        self.widget._schedule_action("read")
        pending = self.widget._pending_action
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending[0], "read")
        # cleanup
        self.widget._worker_thread = None

    def test_read_schedule_stops_debounce_timer(self) -> None:
        self.widget._auto_write_timer.start()
        self.assertTrue(self.widget._auto_write_timer.isActive())
        self.widget._schedule_action("read")
        self.assertFalse(self.widget._auto_write_timer.isActive())


class FlushAutoWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.widget, self.cat = _make_widget(connected=True)
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

    def test_flush_skips_when_disconnected(self) -> None:
        self.cat.is_connected.return_value = False
        self.widget._flush_auto_write()
        self.dispatch.assert_not_called()

    def test_flush_dispatches_write_full(self) -> None:
        self.widget._current_profile_name = self.widget.profile_combo.currentText()
        self.widget._flush_auto_write()
        self.dispatch.assert_called_once()
        kind, _profile = self.dispatch.call_args.args
        self.assertEqual(kind, "write_full")


class NotifyRadioModeTest(unittest.TestCase):
    """notify_radio_mode() folgt dem Radio nur bei echten Modus-Wechseln."""

    def setUp(self) -> None:
        self.widget, _cat = _make_widget(connected=True)
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

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
        self.dispatch.reset_mock()
        self.widget.notify_radio_mode(RxMode.AM_N)
        self.assertEqual(self.widget.mode_combo.currentText(), "AM-N")
        self.dispatch.assert_called_once()
        kind, _payload = self.dispatch.call_args.args
        self.assertEqual(kind, "read")

    def test_user_lock_suppresses_pong_after_manual_switch(self) -> None:
        """Verzögertes Polling mit altem Modus darf die Combo nicht zurücksetzen."""
        from mapping.rx_mapping import RxMode
        import time as _time
        self.widget._last_radio_mode = RxMode.FM
        self.widget._user_mode_lock_until = _time.monotonic() + 4.0
        idx_fm = self.widget.mode_combo.findText("FM")
        self.widget.mode_combo.setCurrentIndex(idx_fm)
        self.dispatch.reset_mock()
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
        self.dispatch.reset_mock()
        self.widget.notify_radio_mode(RxMode.USB)
        self.assertEqual(self.widget.mode_combo.currentText(), "USB")


class OnModeChangedTest(unittest.TestCase):
    """Manueller Mode-Wechsel in der GUI triggert je nach Radio-Stand
    entweder ein reines Read oder ein „Mode setzen + Read"."""

    def setUp(self) -> None:
        self.widget, _cat = _make_widget(connected=True)
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

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
        self.dispatch.reset_mock()
        before = _time.monotonic()
        self._switch_combo_to("AM")
        self.dispatch.assert_called_once()
        kind, payload = self.dispatch.call_args.args
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
        for call in self.dispatch.call_args_list:
            kind, _payload = call.args
            self.assertNotEqual(kind, "set_mode_and_read")


class SuppressDirtyAndMicMeterTest(unittest.TestCase):
    """Kein Pseudo-„dirty“ ohne echte Änderung (MIC-Poll / Suppress-Stapel)."""

    def test_mic_gain_noop_when_unchanged(self) -> None:
        w, _cat = _make_widget(connected=False)
        mg = w.basics.get_values().mic_gain
        w.apply_mic_gain_from_meter(mg)
        self.assertFalse(w._dirty)

    def test_mic_gain_change_still_marks_dirty(self) -> None:
        w, _cat = _make_widget(connected=False)
        mg = w.basics.get_values().mic_gain
        other = mg + 1 if mg < MIC_GAIN_MAX else mg - 1
        other = max(MIC_GAIN_MIN, min(MIC_GAIN_MAX, other))
        self.assertNotEqual(other, mg)
        w.apply_mic_gain_from_meter(other)
        self.assertTrue(w._dirty)

    def test_nested_suppress_blocks_mark_dirty(self) -> None:
        w, _cat = _make_widget(connected=False)
        self.assertEqual(w._suppress_dirty_depth, 0)
        with w._hold_suppress_dirty():
            self.assertEqual(w._suppress_dirty_depth, 1)
            with w._hold_suppress_dirty():
                self.assertEqual(w._suppress_dirty_depth, 2)
                w._mark_dirty()
                self.assertFalse(w._dirty)
            self.assertEqual(w._suppress_dirty_depth, 1)
        self.assertEqual(w._suppress_dirty_depth, 0)


class NotifyTxStateTest(unittest.TestCase):
    """notify_tx_state() löst beim TX→RX-Übergang einen Retry aus."""

    def setUp(self) -> None:
        self.widget, _cat = _make_widget(connected=True)
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

    def test_no_action_when_idle(self) -> None:
        self.widget.notify_tx_state(False)
        self.dispatch.assert_not_called()

    def test_tx_to_rx_with_pending_block_flushes(self) -> None:
        self.widget._tx_active = True
        self.widget._tx_block_pending = True
        self.widget._dirty = True
        self.widget._current_profile_name = self.widget.profile_combo.currentText()
        self.widget.notify_tx_state(False)
        # _flush_auto_write → _schedule_action → _dispatch_action wird einmal
        # gerufen mit kind="write_full"
        self.dispatch.assert_called_once()
        kind, _profile = self.dispatch.call_args.args
        self.assertEqual(kind, "write_full")

    def test_rx_to_tx_blocks_write(self) -> None:
        """Während TX aktiv → write_full wird zu pending tx_block, kein dispatch."""
        self.widget._tx_active = True
        # Echter _dispatch_action-Pfad mit gestopptem _start_worker
        self.widget._dispatch_action = type(self.widget)._dispatch_action.__get__(
            self.widget
        )
        self.start_worker = MagicMock()
        self.widget._start_worker = self.start_worker
        self.widget._current_profile_name = self.widget.profile_combo.currentText()
        self.widget._schedule_action(
            "write_full", self.widget._build_profile_from_editors("test")
        )
        self.start_worker.assert_not_called()
        self.assertTrue(self.widget._tx_block_pending)


class LiveEqSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_qapp()
        self.cat = MagicMock()
        self.cat.is_connected.return_value = True
        tmp_path = Path(tempfile.mkdtemp(prefix="ft991_test_")) / "presets.json"
        self.store = PresetStore.load(tmp_path)
        self.store.upsert(AudioProfile(name="SSB Voice"))
        self.store.save()
        self.widget = ProfileWidget(self.cat, self.store, initial_last_profile="SSB Voice")
        self.dispatch = MagicMock()
        self.widget._dispatch_action = self.dispatch

    def test_enter_writes_default_and_updates_ui(self) -> None:
        self.assertEqual(self.widget.current_profile_name(), "SSB Voice")
        self.assertTrue(self.widget.enter_live_eq_session())
        self.assertEqual(self.widget.current_profile_name(), DEFAULT_PROFILE_NAME)
        self.assertEqual(
            self.widget.profile_combo.currentText(), DEFAULT_PROFILE_NAME
        )
        self.assertEqual(self.widget._live_eq_session_saved_profile, "SSB Voice")
        self.assertFalse(self.widget.profile_combo.isEnabled())
        self.dispatch.assert_called_once()
        kind, profile = self.dispatch.call_args.args
        self.assertEqual(kind, "write_full")
        self.assertEqual(profile.name, DEFAULT_PROFILE_NAME)

    def test_exit_restores_saved_profile(self) -> None:
        self.widget.enter_live_eq_session()
        self.dispatch.reset_mock()
        self.widget.exit_live_eq_session()
        self.assertIsNone(self.widget._live_eq_session_saved_profile)
        self.assertEqual(self.widget.current_profile_name(), "SSB Voice")
        self.assertEqual(self.widget.profile_combo.currentText(), "SSB Voice")
        self.assertTrue(self.widget.profile_combo.isEnabled())
        self.dispatch.assert_called_once()
        _kind, profile = self.dispatch.call_args.args
        self.assertEqual(profile.name, "SSB Voice")

    def test_connect_during_live_writes_default(self) -> None:
        self.widget.enter_live_eq_session()
        self.dispatch.reset_mock()
        self.widget.set_cat_available(True)
        self.dispatch.assert_called_once()
        _kind, profile = self.dispatch.call_args.args
        self.assertEqual(profile.name, DEFAULT_PROFILE_NAME)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
