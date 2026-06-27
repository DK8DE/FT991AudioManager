"""Hauptfenster des FT-991/A Audiomanagers.

Neuer schlanker Aufbau (ab 0.5.1):

- Oben **rechts**: VFO-A/B und RX/TX-Anzeige; darunter ein **großer
  Meter-Bereich** (S-Meter + DSP links, AF/RF + TX-Meter rechts);
  darunter **Tune / Simp / RPT± / REV** und Audio-Buttons; unten **Mode-Gruppe**,
  **EQ-Profil**, **Speicherkanal** und **Band**; darunter ein eigener Bereich
  **Favoriten** (persistente Soll-Vorgaben).
- **EQ-Profil- und Mode-Auswahl** bleiben im Hauptfenster; der Equalizer-Editor
  (Grundwerte, EQ, Erweitert, Speichern) liegt unter **Funktionen → Equalizer**.
- Verbindung: **Datei → Verbinden** / **Datei → Trennen**.
- Die Verbindungs-Konfiguration liegt unter **Datei → Einstellungen**.
- Speicherkanäle unter **Funktionen → Speicherkanäle**.
- Das CAT-Log liegt unter **Ansicht → CAT-Log anzeigen** (eigenes Fenster).
- **Hilfe → Anleitung**: Handbuch-PDF (DE/EN) des aktuellen Releases im Browser.
- **Hilfe → Update prüfen**: Abgleich mit dem neuesten Release auf GitHub.
"""

from __future__ import annotations

import sys
import time
from functools import partial
from typing import Any, Optional, cast, cast

import serial

from PySide6.QtCore import QEvent, QMetaObject, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QStyle,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from cat import (
    CatConnectionLostError,
    CatError,
    CatLog,
    CatTimeoutError,
    FT991A_RADIO_ID,
    FT991CAT,
    SerialCAT,
    TxLockError,
)
from mapping.memory_mapping import MemoryChannel
from mapping.rx_mapping import RxMode, coarse_mode_group_for, rx_mode_from_selection
from audio.audio_settings_hub import AudioSettingsHub
from audio.t_call_controller import TCallController
from model import AppSettings, PresetStore
from model.memory_combo_cache import (
    load_memory_combo_cache,
    memory_channels_from_editor_bank,
    memory_combo_cache_path,
    save_memory_combo_cache,
)
from model.memory_editor_channel import MEMORY_EDITOR_MAX, MemoryChannelBank
from model.favorites_store import (
    FavoritesStore,
    RadioFavorite,
    format_favorite_combo_label,
)
from rig_bridge import RigBridgeManager

from version import APP_NAME, APP_VERSION

from i18n import tr, set_language, language_manager, current_language
from i18n.retranslatable import RetranslatableMixin

from .about_window import AboutWindow
from .app_icon import app_icon
from .menu_icons import (
    control_bar_live_green_led_icon,
    menu_action_icon,
    menu_speaker_white_icon,
)
from .audio_radio_session import AudioRadioSessionHost
from .audio_player_window import AudioPlayerWindow
from .audio_recorder_window import AudioRecorderWindow
from .equalizer_window import EqualizerWindow
from .sound_settings_dialog import SoundSettingsWindow
from .favorites_panel import FavoritesPanelWidget
from .log_widget import LogWindow
from .memory_editor_dialog import open_memory_editor
from .memory_loader import MemoryChannelLoader
from .meter_widget import (
    APP_FREQ_WRITE_DRAG_HOLD_MS,
    APP_FREQ_WRITE_HOLD_MS,
    FREQ_CATCHUP_POLL_MS,
    MeterWidget,
)
from .profile_widget import ProfileWidget
from .radio_control_bar import RadioControlBar
from .settings_dialog import ConnectionSettingsDialog
from .update_check import UpdateCheckOutcome, UpdateCheckThread
from .user_manual import manual_pdf_download_url
from mapping.amateur_bands import (
    BandKind,
    band_at_center_hz,
    band_combo_target_frequency_hz,
    display_band_at_hz,
    display_band_label_at_hz,
    combo_entries_high_to_low,
    preferred_voice_rx_mode_for_hz,
    VFO_BAND_CHOICE,
)
from mapping.meter_mapping import (
    apply_smeter_calibration_from_settings,
    smeter_set_calibration_frequency_hz,
)

from .amateur_band_strip import AmateurBandStripWidget
from .vfo_triplet_widget import VfoTripletWidget

_VFO_CAPTION_STYLE_IDLE = "color: #888888; font-weight: bold;"
_VFO_CAPTION_STYLE_IN_BAND = "color: #5ddc7a; font-weight: bold;"
_VFO_CAPTION_STYLE_SPECIAL_BAND = "color: #f0c040; font-weight: bold;"
_VFO_CAPTION_STYLE_OUT_OF_BAND = "color: #ff6b6b; font-weight: bold;"
_VFO_CAPTION_TO_FREQ_GAP_PX = 10
# Feste Ziffernfarbe nur bei erzwungenem Dark-Mode; sonst palette(window-text).
_VFO_TRIPLET_FREQ_COLOR_DARK = "#FFFFFF"

#: Abgleich VFO-A vs. ``MT``-Frequenz: ``MC`` bleibt nach VFO-Drehen oft auf alter Kanalnummer.
_MEM_FREQ_MATCH_TOLERANCE_HZ = 500

def _restore_memory_channel_if_fa_matches_slot(
    ft: FT991CAT, active_mc: Optional[int], fa_hz: int
) -> Optional[int]:
    """Liefert die Kanalnummer für Connect-/UI-Restore nur, wenn sie zur VFO-A-Frequenz passt.

    Wenn ``MC`` noch einen Kanal meldet, die aktuelle ``FA``-Frequenz aber nicht mehr
    mit dem Inhalt von ``MT`` (Speicher-Slot) übereinstimmt, wurde faktisch per
    Drehknopf auf VFO getuned — dann ``None`` (VFO-Wiederherstellung).
    """
    if active_mc is None or int(active_mc) <= 0:
        return None
    ch = int(active_mc)
    if fa_hz <= 0:
        return ch
    try:
        mem = ft.read_memory_channel_tag(ch)
    except CatError:
        return ch
    if mem is None or mem.frequency_hz <= 0:
        return ch
    if abs(int(fa_hz) - int(mem.frequency_hz)) > _MEM_FREQ_MATCH_TOLERANCE_HZ:
        return None
    return ch


def _status_bar_mode_text(mode_value: str) -> str:
    """Abstand vor dem Statusleisten-Trennstrich (|) nach dem Mode-Text."""
    return tr("status.mode", mode_value=mode_value)


def _status_bar_tx_text(transmitting: bool) -> str:
    return tr("status.tx_on" if transmitting else "status.tx_off")


def _invoke_cat_worker_slot(receiver: QObject, method_name: str) -> None:
    """Queued ``invokeMethod`` für RadioSetupWorker-Slots (PySide6 6.x)."""
    invoke = cast(Any, QMetaObject.invokeMethod)
    invoke(receiver, method_name, Qt.ConnectionType.QueuedConnection)


class MainWindow(QMainWindow, RetranslatableMixin):
    """Hauptfenster mit VFO-Zeile, großem Meter-Panel und EQ-Profilzeile."""

    #: Start- und Mindestgröße des Hauptfensters (logische Pixel).
    MAIN_START_WIDTH = 800
    MAIN_START_HEIGHT = 680

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("main_window.title", app_name=APP_NAME, version=APP_VERSION))
        # Doppelt setzen ist Absicht: QApplication.setWindowIcon() reicht
        # auf Windows/macOS, aber manche Linux-Window-Manager (X11) lesen
        # das Icon nur vom konkreten Toplevel.
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)

        self._settings = settings
        self._cat_log = CatLog()
        self._cat = SerialCAT(log=self._cat_log)
        self._audio_radio_session = AudioRadioSessionHost(
            settings,
            self._cat,
            parent=self,
        )
        self._rig_bridge = RigBridgeManager(
            settings.rig_bridge.to_dict(),
            get_cat=lambda: self._cat,
            log_write=self._rig_bridge_log_write,
        )
        self._preset_store = PresetStore.load()
        self._favorites_store = FavoritesStore.load()

        apply_smeter_calibration_from_settings(settings.smeter_calibration)

        self._log_window: Optional[LogWindow] = None
        self._audio_hub = AudioSettingsHub(self._settings, parent=self)
        self._audio_hub.sync_from_windows()

        self._equalizer_window: Optional[EqualizerWindow] = None
        self._sound_settings_window: Optional[SoundSettingsWindow] = None
        self._audio_player_window: Optional[AudioPlayerWindow] = None
        self._audio_recorder_window: Optional[AudioRecorderWindow] = None
        self._live_window: Optional[QWidget] = None
        self._live_tx_meter_bridge: bool = False
        self._memory_editor: Optional[QWidget] = None
        self._application_shutting_down = False
        self._last_identity_info: str = ""
        self._vfo_a_pending_hz: Optional[int] = None
        self._vfo_b_pending_hz: Optional[int] = None
        self._vfo_a_write_timer = QTimer(self)
        self._vfo_a_write_timer.setSingleShot(True)
        self._vfo_a_write_timer.setInterval(150)
        self._vfo_a_write_timer.timeout.connect(self._flush_vfo_a_frequency_write)
        self._vfo_b_write_timer = QTimer(self)
        self._vfo_b_write_timer.setSingleShot(True)
        self._vfo_b_write_timer.setInterval(150)
        self._vfo_b_write_timer.timeout.connect(self._flush_vfo_b_frequency_write)
        self._vfo_a_display_hz: int = 0
        self._vfo_b_display_hz: int = 0
        self._vfo_a_last_written_hz: Optional[int] = None
        self._vfo_a_last_write_mono: float = 0.0
        self._relay_rev_active: bool = False
        self._relay_output_hz: Optional[int] = None
        # Memory-Kanal, der vor REV-Einschalten aktiv war (``None`` =
        # VFO-Modus). Beim REV-Ausschalten stellen wir diesen Zustand
        # wieder her, damit der User dort landet, wo er war.
        self._relay_pre_rev_memory_channel: Optional[int] = None
        #: Speicherkanal beim Connect (``None`` = war VFO, sonst Kanalnr.).
        self._connect_restore_memory_channel: Optional[int] = None
        #: VFO/Mode beim Connect (Wiederherstellung nach Speicherkanal-Scan).
        self._connect_restore_vfo_a_hz: Optional[int] = None
        self._connect_restore_vfo_b_hz: Optional[int] = None
        self._connect_restore_mode: Optional[RxMode] = None
        #: Zähler offener Connect-Init-Schritte (Profil-Write + Memory-Load).
        self._connect_init_pending: int = 0
        #: T.CALL wartet auf DATA-FM per CAT (Apply/Engage).
        self._tcall_cat_pending: bool = False
        #: Nach T.CALL: volles Restore (Snapshot) oder nur Sprach-Mode.
        self._tcall_release_restore_full: bool = False
        self._tcall_release_engage_plain: bool = False
        #: Vorübergehend von DATA-USB/… auf DATA-FM — danach zurückschalten.
        self._tcall_restore_data_mode: Optional[RxMode] = None
        #: Betriebsart vor T.CALL (für Wiederherstellung statt engage_plain→FM).
        self._tcall_saved_rx_mode: Optional[RxMode] = None
        #: Speicherkanal vor T.CALL (wie REV) — ``set_rx_mode`` allein würde VFO erzwingen.
        self._tcall_saved_memory_channel: Optional[int] = None
        #: Während T.CALL keine Mode-/Band-UI vom Poller nachziehen.
        self._tcall_suppress_radio_ui: bool = False
        self._tcall_async_restore_pending: bool = False
        #: MT-Frequenz pro Kanal (Memory-Loader) — Abgleich bei VFO-Drehen mit aktivem MC.
        self._memory_slot_frequency_hz: dict[int, int] = {}
        #: Für Dropdown-Persistenz: vollständige :class:`~mapping.memory_mapping.MemoryChannel`.
        self._memory_combo_catalog: dict[int, MemoryChannel] = {}
        #: Zuletzt per Band/Frequenz automatisch gesetzter Phone-Mode (LSB/USB/FM).
        self._last_applied_band_voice_mode: Optional[RxMode] = None
        self._update_check_thread: Optional[UpdateCheckThread] = None
        self._status_tx_transmitting: bool = False
        self._status_mode_display: str = tr("common.dash")

        self._build_ui()
        self._t_call = TCallController(self._audio_hub, parent=self)
        self._t_call.error.connect(self._on_t_call_error)
        self._t_call.active_changed.connect(self._on_t_call_active_changed)
        _tcall_worker = self._audio_radio_session.worker
        _tcall_worker.apply_finished.connect(self._on_tcall_radio_apply_finished)
        _tcall_worker.engage_data_finished.connect(
            self._on_tcall_radio_engage_data_finished
        )
        _tcall_worker.restore_finished.connect(self._on_tcall_async_restore_finished)
        self._build_menu()

        # Statusleiste: links Verbindung + Speicherkanal-Laden, rechts Mode/TX.
        self._connection_footer_label = QLabel(tr("status.not_connected"))
        self._connection_footer_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        sb = QStatusBar()
        sb.addWidget(self._connection_footer_label, 1)
        self._tx_label = QLabel(_status_bar_tx_text(False))
        self._mode_label = QLabel(_status_bar_mode_text(tr("common.dash")))
        sb.addPermanentWidget(self._mode_label)
        sb.addPermanentWidget(self._tx_label)
        self.setStatusBar(sb)

        # Verbindungs-Signale verdrahten
        self.meter_widget.tx_status_changed.connect(self._on_tx_status_changed)
        self.meter_widget.connection_lost.connect(self._on_connection_lost)
        self.meter_widget.rx_info_changed.connect(self._on_rx_info_changed)
        self.meter_widget.repeater_shift_polled.connect(
            self._on_repeater_shift_polled
        )
        self.meter_widget.status_message_requested.connect(
            self._on_meter_status_message
        )
        self._rig_bridge.set_on_frequency_written(self._on_rig_bridge_frequency_written)
        self.meter_widget.set_cat_yield_checker(
            self._rig_bridge.flrig_poller_should_yield
        )
        self.meter_widget.set_cat_catchup_limit_checker(
            self._rig_bridge.flrig_has_clients
        )
        # User dreht am Gerät den MEM/CH-Knopf → Combo nachziehen.
        self.meter_widget.memory_channel_changed.connect(
            self._on_memory_channel_from_radio
        )
        self.profile_widget.connection_lost.connect(self._on_connection_lost)
        self.profile_widget.silent_worker_finished.connect(
            self._on_connect_profile_worker_finished
        )
        # Meter-Poller liefert die Quelle der Wahrheit für TX/RX und Mode.
        # ProfileWidget hängt sich daran, um (a) bei TX→RX-Übergang einen
        # pausierten Auto-Write nachzuziehen und (b) bei Mode-Wechsel am
        # Gerät die GUI-Mode-Combo nachzuziehen.
        self.meter_widget.tx_status_changed.connect(
            self.profile_widget.notify_tx_state
        )
        self.meter_widget.rx_info_changed.connect(
            self._on_rx_info_for_profile
        )
        # MIC Gain: vertikaler Meter-Slider ↔ Equalizer-Grundwerte (ohne
        # Rückkopplungsschleife dank MicGainSlider._applying_remote).
        self.meter_widget.mic_gain_slider.value_chosen.connect(
            self.profile_widget.apply_mic_gain_from_meter
        )
        self.meter_widget.mic_gain_synced_from_radio.connect(
            self.profile_widget.apply_mic_gain_from_meter
        )
        self.profile_widget.basics.mic_gain_slider.valueChanged.connect(
            self.meter_widget.mic_gain_slider.set_value
        )
        self.profile_widget.basics.mic_gain_synced.connect(
            self.meter_widget.mic_gain_slider.set_value
        )

        self.profile_widget.mode_combo.currentTextChanged.connect(
            self._on_main_operating_mode_changed
        )

        # Reconnect-Watcher: läuft bei Verbindungsverlust und bei
        # konfiguriertem Auto-Connect, bis der Port wieder verfügbar ist.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(2000)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

        self._rig_bridge_ui_timer = QTimer(self)
        self._rig_bridge_ui_timer.setInterval(150)
        self._rig_bridge_ui_timer.timeout.connect(self._refresh_rig_bridge_toolbar_leds)
        self._rig_bridge_ui_timer.start()

        # Speicherkanal-Loader: liest beim Connect im Hintergrund alle
        # belegten Memory-Slots aus und befüllt die Combo neben VFO-B.
        self._memory_loader = MemoryChannelLoader(self._cat, parent=self)
        self._memory_loader.channel_loaded.connect(self._on_memory_channel_loaded)
        self._memory_loader.progressed.connect(self._on_memory_load_progress)
        self._memory_loader.finished.connect(self._on_memory_load_finished)
        self._memory_loader.failed.connect(self._on_memory_load_failed)
        self._memory_loader.connection_lost.connect(self._on_connection_lost)

        # Log-Fenster anhand der gespeicherten Sichtbarkeit zeigen
        if self._settings.ui.show_cat_log:
            self._show_log_window()

        # Startgröße setzen und zentrieren nach erstem Layout-Durchlauf.
        QTimer.singleShot(0, self._apply_startup_window_geometry)
        self._sync_meter_dsp_mode_visibility()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._register_retranslate()
        self.retranslate_ui()

        self._global_hotkey_controller = None
        if sys.platform == "win32":
            from .global_hotkeys_win import GlobalHotkeyController

            self._global_hotkey_controller = GlobalHotkeyController(
                lambda: int(self.winId()),
                lambda a: QTimer.singleShot(
                    0, partial(self._apply_global_shortcut_action, a)
                ),
            )
        QTimer.singleShot(0, self._refresh_global_hotkeys)

        self._live_momentary_ptt_poll_timer = QTimer(self)
        self._live_momentary_ptt_poll_timer.setInterval(45)
        self._live_momentary_ptt_poll_timer.timeout.connect(
            self._poll_live_momentary_ptt_release
        )

    def _refresh_global_hotkeys(self) -> None:
        hc = getattr(self, "_global_hotkey_controller", None)
        if hc is None:
            return
        try:
            hc.apply_config(self._settings.ui.global_shortcuts.to_dict())
        except Exception:
            pass

    def _apply_global_shortcut_action(self, action: str) -> None:
        gs = self._settings.ui.global_shortcuts
        if not gs.enabled:
            return
        if action == "contest_play":
            self._trigger_contest_hotkey_play()
        elif action == "live_ptt_latch":
            self._trigger_live_ptt_latch_hotkey()
        elif action == "live_ptt_momentary":
            self._trigger_live_momentary_ptt_hotkey()

    def _find_active_live_window(self) -> Optional[QWidget]:
        from gui.live_window import LiveWindow, _live_window_accepts_background_audio

        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return None
        for w in app.topLevelWidgets():
            if isinstance(w, LiveWindow) and _live_window_accepts_background_audio(w):
                return w
        return None

    def _trigger_live_ptt_latch_hotkey(self) -> None:
        lw = self._find_active_live_window()
        if lw is None:
            return
        fn = getattr(lw, "trigger_global_ptt_latch_hotkey", None)
        if callable(fn):
            fn()

    def _trigger_live_momentary_ptt_hotkey(self) -> None:
        lw = self._find_active_live_window()
        if lw is None:
            return
        start = getattr(lw, "_kbd_native_apply_momentary_start", None)
        if callable(start):
            start()
        if getattr(lw, "_kbd_ptt_momentary_engaged", False):
            self._live_momentary_ptt_poll_timer.start()

    def _poll_live_momentary_ptt_release(self) -> None:
        lw = self._find_active_live_window()
        if lw is None or not getattr(lw, "_kbd_ptt_momentary_engaged", False):
            self._live_momentary_ptt_poll_timer.stop()
            return
        from gui.global_hotkeys_win import hotkey_binding_physically_held

        gs = self._settings.ui.global_shortcuts.to_dict()
        if hotkey_binding_physically_held(gs, "key_live_ptt_momentary", "Y"):
            return
        end = getattr(lw, "_kbd_native_apply_momentary_end", None)
        if callable(end):
            end()
        self._live_momentary_ptt_poll_timer.stop()

    def _trigger_contest_hotkey_play(self) -> None:
        win = self._audio_player_window
        if win is None:
            return
        trigger = getattr(win, "trigger_contest_hotkey_play", None)
        if callable(trigger):
            trigger()

    def nativeEvent(self, eventType, message):  # noqa: N802
        hc = getattr(self, "_global_hotkey_controller", None)
        if hc is not None:
            r = hc.process_native_event(eventType, message)
            if r is not None:
                return r
        return super().nativeEvent(eventType, message)

    def _mouse_global_inside_any_vfo_triplet(self, global_pos) -> bool:
        for triplet in (self._vfo_a_triplet, self._vfo_b_triplet):
            if triplet.rect().contains(triplet.mapFromGlobal(global_pos)):
                return True
        return False

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Lässt VFO-Felder den Fokus verlieren, wenn irgendwo anders geklickt wird.

        Viele Widgets (Labels, Frames, Slider-Flächen…) übernehmen keinen Fokus.
        Ohne explizites ``clearFocus()`` bliebe ein VFO-``QLineEdit`` aktiv und
        blockierte per ``_any_segment_focused()`` die CAT-Anzeige-Aktualisierung.
        """
        if event.type() != QEvent.Type.MouseButtonPress:
            return super().eventFilter(_watched, event)
        me = cast(QMouseEvent, event)
        qapp = QApplication.instance()
        fw = qapp.focusWidget() if isinstance(qapp, QApplication) else None
        if fw is None:
            return super().eventFilter(_watched, event)
        in_a = self._vfo_a_triplet.isAncestorOf(fw)
        in_b = self._vfo_b_triplet.isAncestorOf(fw)
        if not in_a and not in_b:
            return super().eventFilter(_watched, event)
        global_pt = me.globalPosition().toPoint()
        if self._mouse_global_inside_any_vfo_triplet(global_pt):
            return super().eventFilter(_watched, event)
        fw.clearFocus()
        return super().eventFilter(_watched, event)

    def _main_operating_mode(self) -> RxMode:
        """Aktuelle Betriebsart aus der Hauptfenster-Mode-Combo."""
        return rx_mode_from_selection(self.profile_widget.mode_combo.currentText())

    def _live_transmit_blocked_by_other_windows(self) -> str:
        """Leer wenn Live starten darf — sonst kurzer deutschsprachiger Grund."""
        p = self._audio_player_window
        if p is not None:
            ctl = getattr(p, "_controller", None)
            if ctl is not None and ctl.is_busy():
                return tr("live.blocked.player_busy")
        r = self._audio_recorder_window
        if r is not None:
            rec = getattr(r, "_recorder", None)
            if rec is not None and rec.is_busy():
                return tr("live.blocked.recorder_recording")
            pl = getattr(r, "_player", None)
            if pl is not None and pl.is_busy():
                return tr("live.blocked.recorder_replay")
        return ""

    def _on_main_operating_mode_changed(self, _text: str) -> None:
        """DSP-Anzeige + Audio-Player/Recorder/Live-DATA-Modus anpassen."""
        self._sync_meter_dsp_mode_visibility()
        mode = self._main_operating_mode()
        player = self._audio_player_window
        if player is not None and player.isVisible():
            player.sync_data_mode_from_main(mode)
        recorder = self._audio_recorder_window
        if recorder is not None and recorder.isVisible():
            recorder.sync_data_mode_from_main(mode)
        live_win = self._live_window
        if live_win is not None:
            from gui.live_window import _live_window_accepts_background_audio

            if _live_window_accepts_background_audio(live_win):
                sync = getattr(live_win, "sync_data_mode_from_main", None)
                if callable(sync):
                    sync(mode)

    def _sync_meter_dsp_mode_visibility(self) -> None:
        """DSP-Slider ausblenden, wenn die Betriebsart sie am FT-991 nicht nutzt."""
        text = self.profile_widget.mode_combo.currentText()
        mg = coarse_mode_group_for(text)
        mode = rx_mode_from_selection(text)
        self.meter_widget.apply_dsp_mode_relevance(mg, operating_mode=mode)

    def _apply_startup_window_geometry(self) -> None:
        """Fenster auf :attr:`MAIN_START_*` setzen und auf dem Bildschirm zentrieren."""
        self.setMinimumSize(self.MAIN_START_WIDTH, self.MAIN_START_HEIGHT)
        cw = self.centralWidget()
        if cw is not None:
            cw.setMinimumSize(0, 0)
        screen = QGuiApplication.primaryScreen()
        start = QSize(self.MAIN_START_WIDTH, self.MAIN_START_HEIGHT)
        if screen is not None:
            r = QStyle.alignedRect(
                Qt.LayoutDirection.LeftToRight,
                Qt.AlignmentFlag.AlignCenter,
                start,
                screen.availableGeometry(),
            )
            self.setGeometry(r)
        else:
            self.resize(start)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Profil-Logik (Koordinator); Combos werden unten eingebettet.
        self.profile_widget = ProfileWidget(
            self._cat,
            self._preset_store,
            initial_last_profile=self._settings.ui.last_profile,
        )
        self.profile_widget.active_profile_changed.connect(
            self._on_active_profile_changed
        )
        self.profile_widget.hide()
        self.profile_widget.set_cat_available(False)
        self.profile_widget.set_hide_extended_in_ssb(
            self._settings.ui.hide_extended_in_ssb
        )

        vfo_caption_font = self.font()
        vfo_caption_font.setBold(True)
        vfo_caption_font.setPointSizeF(vfo_caption_font.pointSizeF() * 1.15 * 2)

        self._vfo_a_caption = QLabel(tr("main.vfo_a_caption"))
        self._vfo_a_caption.setFont(vfo_caption_font)
        self._vfo_a_caption.setStyleSheet(_VFO_CAPTION_STYLE_IN_BAND)
        _vfo_digits_color = _VFO_TRIPLET_FREQ_COLOR_DARK
        self._vfo_a_triplet = VfoTripletWidget(
            text_color=_vfo_digits_color,
            digit_font=QFont(vfo_caption_font),
        )
        self._vfo_a_triplet.user_frequency_changed.connect(
            self._on_user_vfo_a_frequency
        )

        self._vfo_b_caption = QLabel(tr("main.vfo_b_caption"))
        self._vfo_b_caption.setFont(vfo_caption_font)
        self._vfo_b_caption.setStyleSheet(_VFO_CAPTION_STYLE_IN_BAND)
        self._vfo_b_triplet = VfoTripletWidget(
            text_color=_vfo_digits_color,
            digit_font=QFont(vfo_caption_font),
        )
        self._vfo_b_triplet.user_frequency_changed.connect(
            self._on_user_vfo_b_frequency
        )

        self._vfo_a_triplet.set_interactive(False)
        self._vfo_b_triplet.set_interactive(False)

        self._vfo_ab_button = QPushButton(tr("main.vfo_ab_button"))
        self._vfo_ab_button.setEnabled(False)
        self._vfo_ab_button.clicked.connect(self._on_vfo_ab_clicked)

        self.meter_widget = MeterWidget(
            self._cat,
            tx_interval_ms=self._settings.polling.tx_interval_ms,
            rx_interval_ms=self._settings.polling.rx_interval_ms,
            tx_poll=self._settings.polling.tx_poll,
            integrated_main_layout=True,
        )

        # ----- Oben rechts: VFO-A/B + RX/TX --------------------------------
        top_bar = QFrame()
        top_bar.setObjectName("panelFrame")
        top_bar.setFrameShape(QFrame.Shape.StyledPanel)
        top_row = QHBoxLayout(top_bar)
        top_row.setContentsMargins(10, 6, 10, 6)
        top_row.setSpacing(12)

        for triplet in (self._vfo_a_triplet, self._vfo_b_triplet):
            triplet.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
            )

        for cap in (self._vfo_a_caption, self._vfo_b_caption):
            cap.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        vfo_a_box = QWidget()
        vfo_a_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        vfo_a_row = QHBoxLayout(vfo_a_box)
        vfo_a_row.setContentsMargins(0, 0, 0, 0)
        vfo_a_row.setSpacing(0)
        vfo_a_row.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        vfo_a_row.addWidget(self._vfo_a_caption)
        vfo_a_row.addSpacing(_VFO_CAPTION_TO_FREQ_GAP_PX)
        vfo_a_row.addWidget(self._vfo_a_triplet)
        vfo_a_row.addSpacing(8)
        vfo_a_row.addWidget(self._vfo_ab_button)

        vfo_b_box = QWidget()
        vfo_b_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        vfo_b_row = QHBoxLayout(vfo_b_box)
        vfo_b_row.setContentsMargins(0, 0, 0, 0)
        vfo_b_row.setSpacing(0)
        vfo_b_row.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        vfo_b_row.addWidget(self._vfo_b_caption)
        vfo_b_row.addSpacing(_VFO_CAPTION_TO_FREQ_GAP_PX)
        vfo_b_row.addWidget(self._vfo_b_triplet)

        top_row.addWidget(vfo_a_box, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_row.addStretch(1)
        top_row.addWidget(vfo_b_box, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_row.addStretch(1)
        self.meter_widget.tx_led.setParent(top_bar)
        self.meter_widget.tx_label.setParent(top_bar)
        top_row.addWidget(
            self.meter_widget.tx_led, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        top_row.addWidget(
            self.meter_widget.tx_label,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        layout.addWidget(top_bar)

        band_panel = QFrame()
        band_panel.setObjectName("panelFrame")
        band_panel.setFrameShape(QFrame.Shape.StyledPanel)
        band_row = QHBoxLayout(band_panel)
        band_row.setContentsMargins(10, 4, 10, 4)
        band_row.setSpacing(10)
        self._band_strip_caption = QLabel(tr("main.band_caption"))
        band_caption_font = self.font()
        band_caption_font.setBold(True)
        self._band_strip_caption.setFont(band_caption_font)
        self._band_strip_caption.setStyleSheet(_VFO_CAPTION_STYLE_IDLE)
        self._band_strip_caption.setFixedWidth(52)
        self._band_strip_name = QLabel(tr("common.dash"))
        self._band_strip_name.setFont(band_caption_font)
        self._band_strip_name.setStyleSheet(_VFO_CAPTION_STYLE_IDLE)
        self._band_strip_name.setMinimumWidth(80)
        self._band_strip = AmateurBandStripWidget()
        self._band_strip.frequency_changed.connect(self._on_band_strip_frequency)
        self._band_strip.frequency_drag_finished.connect(
            self._on_band_strip_drag_finished
        )
        band_row.addWidget(self._band_strip_caption, 0, Qt.AlignmentFlag.AlignVCenter)
        band_row.addWidget(self._band_strip_name, 0, Qt.AlignmentFlag.AlignVCenter)
        band_row.addWidget(self._band_strip, stretch=1)
        layout.addWidget(band_panel)

        layout.addWidget(self.meter_widget, stretch=1)

        self._radio_control_bar = RadioControlBar()
        self._radio_control_bar.repeater_minus_toggled.connect(
            self._on_repeater_minus_toggled
        )
        self._radio_control_bar.tune_clicked.connect(self._on_tune_clicked)
        self._radio_control_bar.rev_toggled.connect(self._on_rev_toggled)
        self._radio_control_bar.t_call_pressed.connect(self._on_t_call_pressed)
        self._radio_control_bar.t_call_released.connect(self._on_t_call_released)
        self._radio_control_bar.audio_player_clicked.connect(
            self._on_audio_player_action
        )
        self._radio_control_bar.audio_recorder_clicked.connect(
            self._on_audio_recorder_action
        )
        self._radio_control_bar.live_clicked.connect(self._on_live_action)
        self._radio_control_bar.sound_settings_clicked.connect(
            self._on_sound_settings_action
        )
        self.meter_widget.af_gain_set_requested.connect(self._on_af_gain_slider_changed)
        self.meter_widget.rf_gain_set_requested.connect(self._on_rf_gain_slider_changed)
        layout.addWidget(self._radio_control_bar)

        # ----- Unten: Mode + EQ-Profil; Speicherkanal darunter (volle Breite) --
        bottom_bar = QFrame()
        bottom_bar.setObjectName("panelFrame")
        bottom_bar.setFrameShape(QFrame.Shape.StyledPanel)
        bottom_outer = QVBoxLayout(bottom_bar)
        bottom_outer.setContentsMargins(8, 6, 8, 6)
        # Gleicher Zeilenabstand wie zwischen den beiden Combo-Zeilen oben.
        bottom_outer.setSpacing(8)

        bottom_row1 = QHBoxLayout()
        bottom_row1.setSpacing(10)

        self._lbl_mode_group = QLabel(tr("main.mode_group"))
        bottom_row1.addWidget(self._lbl_mode_group)
        self.profile_widget.mode_combo.setParent(bottom_bar)
        bottom_row1.addWidget(self.profile_widget.mode_combo)

        self._lbl_eq_profile = QLabel(tr("main.eq_profile"))
        bottom_row1.addSpacing(14)
        bottom_row1.addWidget(self._lbl_eq_profile)
        self.profile_widget.profile_combo.setParent(bottom_bar)
        bottom_row1.addWidget(self.profile_widget.profile_combo, stretch=1)
        bottom_outer.addLayout(bottom_row1)

        bottom_row2 = QHBoxLayout()
        bottom_row2.setSpacing(10)
        self._lbl_memory_channel = QLabel(tr("main.memory_channel"))
        bottom_row2.addWidget(self._lbl_memory_channel)
        self.memory_combo = QComboBox(bottom_bar)
        self.memory_combo.setEnabled(False)
        self.memory_combo.setMinimumWidth(260)
        self.memory_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._reset_memory_combo()
        self.memory_combo.activated.connect(self._on_memory_combo_activated)
        bottom_row2.addWidget(self.memory_combo, stretch=1)

        self._lbl_band_combo = QLabel(tr("main.band_combo"))
        bottom_row2.addWidget(self._lbl_band_combo)
        self.band_combo = QComboBox(bottom_bar)
        self.band_combo.setEnabled(False)
        self.band_combo.setMinimumWidth(280)
        for label, data in combo_entries_high_to_low():
            self.band_combo.addItem(label, data)
        self.band_combo.activated.connect(self._on_band_combo_activated)
        bottom_row2.addWidget(self.band_combo)

        bottom_outer.addLayout(bottom_row2)

        layout.addWidget(bottom_bar)

        # Eigener panelFrame wie Mode/Speicher — nicht im selben Kasten wie Speicherkanal.
        favorites_bar = QFrame()
        favorites_bar.setObjectName("panelFrame")
        favorites_bar.setFrameShape(QFrame.Shape.StyledPanel)
        fav_outer = QVBoxLayout(favorites_bar)
        fav_outer.setContentsMargins(8, 6, 8, 6)
        fav_outer.setSpacing(0)
        self._favorites_panel = FavoritesPanelWidget(favorites_bar)
        fav_outer.addWidget(self._favorites_panel)
        self._favorites_panel.btn_save.clicked.connect(self._on_favorite_save_clicked)
        self._favorites_panel.btn_delete.clicked.connect(self._on_favorite_delete_clicked)
        self._favorites_panel.btn_edit.clicked.connect(self._on_favorite_edit_clicked)
        self._favorites_panel.combo.activated.connect(self._on_favorite_combo_activated)
        self._refresh_favorites_combo()
        self._favorites_panel.setEnabled(False)

        layout.addWidget(favorites_bar)

        self.setCentralWidget(central)
        self._refresh_band_strip()

    def _build_menu(self) -> None:
        menu = self.menuBar()

        # === Datei ====================================================
        self._file_menu = menu.addMenu(tr("menu.file"))

        self._settings_action = QAction(tr("menu.file.settings"), self)
        self._settings_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
                theme_name="preferences-system",
            )
        )
        self._settings_action.setShortcut("Ctrl+E")
        self._settings_action.triggered.connect(self._on_settings_action)
        self._file_menu.addAction(self._settings_action)

        self._connect_action = QAction(tr("menu.file.connect"), self)
        self._connect_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_DriveNetIcon,
                theme_name="network-connect",
            )
        )
        self._connect_action.setShortcut("Ctrl+V")
        self._connect_action.triggered.connect(self._on_connect_menu)
        self._file_menu.addAction(self._connect_action)

        self._disconnect_action = QAction(tr("menu.file.disconnect"), self)
        self._disconnect_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_BrowserStop,
                theme_name="network-disconnect",
            )
        )
        self._disconnect_action.setShortcut("Ctrl+T")
        self._disconnect_action.triggered.connect(self._on_disconnect_menu)
        self._file_menu.addAction(self._disconnect_action)

        self._file_menu.addSeparator()

        self._quit_action = QAction(tr("menu.file.quit"), self)
        self._quit_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_TitleBarCloseButton,
                theme_name="application-exit",
            )
        )
        self._quit_action.setShortcut("Ctrl+Q")
        self._quit_action.triggered.connect(self.close)
        self._file_menu.addAction(self._quit_action)

        # === Funktionen =============================================
        self._functions_menu = menu.addMenu(tr("menu.functions"))

        self._memory_action = QAction(tr("menu.functions.memory_channels"), self)
        self._memory_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_DirOpenIcon,
                theme_name="folder-open",
            )
        )
        self._memory_action.setShortcut("Ctrl+Shift+K")
        self._memory_action.triggered.connect(self._on_memory_editor_action)
        self._functions_menu.addAction(self._memory_action)

        self._functions_menu.addSeparator()

        self._equalizer_action = QAction(tr("menu.functions.equalizer"), self)
        self._equalizer_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_MediaVolume,
                theme_name="audio-volume-high",
            )
        )
        self._equalizer_action.setShortcut("Ctrl+Shift+E")
        self._equalizer_action.triggered.connect(self._on_equalizer_action)
        self._functions_menu.addAction(self._equalizer_action)

        self._sound_settings_action = QAction(tr("menu.functions.sound_settings"), self)
        self._sound_settings_action.setIcon(menu_speaker_white_icon())
        self._sound_settings_action.setShortcut("Ctrl+Shift+S")
        self._sound_settings_action.triggered.connect(self._on_sound_settings_action)
        self._functions_menu.addAction(self._sound_settings_action)

        self._audio_player_action = QAction(tr("menu.functions.audio_player"), self)
        self._audio_player_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_MediaPlay,
                theme_name="media-playback-start",
            )
        )
        self._audio_player_action.setShortcut("Ctrl+Shift+A")
        self._audio_player_action.triggered.connect(self._on_audio_player_action)
        self._functions_menu.addAction(self._audio_player_action)

        self._audio_recorder_action = QAction(tr("menu.functions.audio_recorder"), self)
        self._audio_recorder_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_FileIcon,
                theme_name="media-record",
            )
        )
        self._audio_recorder_action.setShortcut("Ctrl+Shift+R")
        self._audio_recorder_action.triggered.connect(self._on_audio_recorder_action)
        self._functions_menu.addAction(self._audio_recorder_action)

        self._live_audio_action = QAction(tr("menu.functions.live_pc"), self)
        self._live_audio_action.setIcon(control_bar_live_green_led_icon())
        self._live_audio_action.setShortcut("Ctrl+Shift+L")
        self._live_audio_action.triggered.connect(self._on_live_action)
        self._functions_menu.addAction(self._live_audio_action)

        # === Ansicht ==================================================
        self._view_menu = menu.addMenu(tr("menu.view"))

        self.log_toggle_action = QAction(tr("menu.view.cat_log"), self)
        self.log_toggle_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_FileDialogInfoView,
                theme_name="utilities-log-viewer",
            )
        )
        self.log_toggle_action.setCheckable(True)
        self.log_toggle_action.setChecked(self._settings.ui.show_cat_log)
        self.log_toggle_action.setShortcut("Ctrl+L")
        self.log_toggle_action.toggled.connect(self._on_log_toggle)
        self._view_menu.addAction(self.log_toggle_action)

        self._view_menu.addSeparator()

        self._language_menu = self._view_menu.addMenu(tr("menu.view.language"))
        self._language_de_action = QAction(tr("menu.view.language_de"), self)
        self._language_de_action.setCheckable(True)
        self._language_de_action.triggered.connect(self._on_language_de)
        self._language_menu.addAction(self._language_de_action)

        self._language_en_action = QAction(tr("menu.view.language_en"), self)
        self._language_en_action.setCheckable(True)
        self._language_en_action.triggered.connect(self._on_language_en)
        self._language_menu.addAction(self._language_en_action)

        self._sync_language_menu_checks()

        # === Hilfe ====================================================
        self._help_menu = menu.addMenu(tr("menu.help"))
        self._version_action = QAction(tr("menu.help.version"), self)
        self._version_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_MessageBoxInformation,
                theme_name="help-about",
            )
        )
        self._version_action.triggered.connect(self._show_about)
        self._help_menu.addAction(self._version_action)

        self._manual_action = QAction(tr("menu.help.manual"), self)
        self._manual_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
                theme_name="help-contents",
            )
        )
        self._manual_action.triggered.connect(self._on_open_user_manual)
        self._help_menu.addAction(self._manual_action)

        self._update_check_action = QAction(tr("menu.help.check_updates"), self)
        self._update_check_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_BrowserReload,
                theme_name="view-refresh",
            )
        )
        self._update_check_action.triggered.connect(self._on_check_for_updates)
        self._help_menu.addAction(self._update_check_action)

    # ------------------------------------------------------------------
    # Verbinden / Trennen
    # ------------------------------------------------------------------

    def _on_connect_menu(self) -> None:
        """Datei → Verbinden."""
        if self._cat.is_connected():
            return
        self._do_connect(interactive=True)

    def _on_disconnect_menu(self) -> None:
        """Datei → Trennen."""
        if not self._cat.is_connected():
            return
        self._do_disconnect()

    def _do_connect(self, *, interactive: bool) -> bool:
        """Versucht zu verbinden.

        ``interactive=True``: Dialoge bei fehlendem Port / Fehlschlag (User
        hat aktiv geklickt). ``interactive=False``: kein Dialog, Rückgabe
        ``True``/``False`` — wird vom Auto-Connect und vom Reconnect-Watcher
        benutzt.
        """
        port = self._settings.cat.port
        if not port:
            if interactive:
                QMessageBox.information(
                    self,
                    tr("connect.no_port.title"),
                    tr("connect.no_port.message"),
                )
                self._on_settings_action()
            return False

        try:
            self._cat.connect(
                port,
                baudrate=self._settings.cat.baudrate,
                timeout_ms=self._settings.cat.timeout_ms,
            )
        except (serial.SerialException, OSError) as exc:
            if interactive:
                QMessageBox.critical(
                    self,
                    tr("connect.failed.title"),
                    tr("connect.failed.message", port=port, error=str(exc)),
                )
            self._refresh_header_status(connected=False, info="")
            return False

        # Erfolgreich geöffnet — Reconnect-Watcher aus, ID-Test, Auto-Read.
        self._reconnect_timer.stop()
        try:
            self._last_identity_info = self._silent_identity_test()
        except CatConnectionLostError:
            # Verbindung direkt nach dem Öffnen wieder weg (sehr seltener
            # Zwischenfall). Sauber als Verlust behandeln.
            self._on_connection_lost()
            return False
        # Direkt nach dem ID-Test: Auto-Information am Radio ausschalten.
        # Sonst sendet das FT-991/A bei jedem NB-/Mode-/VFO-Druck am
        # Front-Panel proaktive AI-Frames, die unseren RX-Poller aus
        # dem Tritt bringen.
        try:
            FT991CAT(self._cat).disable_auto_information()
        except CatConnectionLostError:
            self._on_connection_lost()
            return False
        self._capture_connect_radio_state()
        self._prepare_connect_for_cat_bulk_io()
        self._begin_connect_init()
        self._refresh_header_status(connected=True, info=self._last_identity_info)
        self._on_connection_changed(True)
        # EQ-Profil wird in ``set_cat_available(True)`` per write_full
        # ins Gerät übertragen (siehe ProfileWidget).
        if not self.profile_widget.request_apply_active_profile():
            self._connect_init_step_done("profile")
        # Speicherkanäle im Hintergrund laden (nach kurzer Verzögerung).
        if (
            self._settings.ui.memory_dropdown_scan_completed
            and memory_combo_cache_path().is_file()
        ):
            QTimer.singleShot(50, self._apply_memory_dropdown_from_cache)
        else:
            QTimer.singleShot(50, self._start_memory_load)
        return True

    def _do_disconnect(self) -> None:
        # Manuelles Trennen schaltet auch den Auto-Reconnect aus, bis der
        # User wieder explizit "Verbinden" wählt oder die App neu startet.
        self._reconnect_timer.stop()
        self._clear_connect_restore_snapshot()
        self._connect_init_pending = 0
        self._memory_slot_frequency_hz.clear()
        self._memory_loader.stop()
        self._cat.disconnect()
        self._last_identity_info = ""
        self._refresh_header_status(connected=False, info="")
        self._on_connection_changed(False)

    # ------------------------------------------------------------------
    # Robustheit: Verbindungsverlust + automatisches Wieder-Verbinden
    # ------------------------------------------------------------------

    def _on_connection_lost(self) -> None:
        """Wird gerufen, wenn MeterPoller oder Profil-Worker einen IO-Fehler
        bekommen. SerialCAT hat sich intern schon getrennt.
        """
        self._clear_connect_restore_snapshot()
        self._connect_init_pending = 0
        self._memory_slot_frequency_hz.clear()
        if self._cat.is_connected():
            # Sicherheitsnetz — sollte normalerweise schon im SerialCAT
            # passiert sein.
            try:
                self._cat.disconnect()
            except Exception:
                pass
        lost = tr("status.connection_lost")
        if not self._last_identity_info.startswith(lost):
            self._last_identity_info = lost
        self._refresh_header_status(connected=False, info=self._last_identity_info)
        self._on_connection_changed(False)
        # Reconnect-Watcher starten, sofern erwünscht und Port konfiguriert.
        if self._settings.cat.auto_connect and self._settings.cat.port:
            if not self._reconnect_timer.isActive():
                self._reconnect_timer.start()

    def _try_reconnect(self) -> None:
        """Periodischer Versuch, die Verbindung wiederherzustellen.

        Bricht still ab, wenn der Port noch fehlt; bei Erfolg stoppt sich
        der Timer in :meth:`_do_connect` selbst.
        """
        if self._cat.is_connected():
            self._reconnect_timer.stop()
            return
        if not self._settings.cat.auto_connect or not self._settings.cat.port:
            self._reconnect_timer.stop()
            return
        self._do_connect(interactive=False)

    def _silent_identity_test(self) -> str:
        """Führt nach erfolgtem ``connect()`` einen leisen ID-Test aus.

        Liefert einen kurzen Info-Text für die Statuszeile. Bei eindeutig
        fremdem Gerät oder fehlender Antwort gibt es einen kleinen Hinweis,
        bei FT-991(A) den ID-String.
        """
        if not self._cat.is_connected():
            return ""
        ft = FT991CAT(self._cat)
        try:
            identity = ft.test_connection()
        except CatConnectionLostError:
            # Bubblet hoch zum Aufrufer — der entscheidet, ob es als
            # Verbindungsverlust behandelt wird.
            raise
        except CatTimeoutError:
            return tr("status.identity.no_response")
        except CatError:
            return tr("status.identity.cat_error")

        if identity.is_ft991:
            return tr("status.identity.ft991", radio_id=identity.radio_id)
        if identity.radio_id is not None:
            return tr("status.identity.foreign_id", radio_id=identity.radio_id)
        return tr("status.identity.unclear")

    def _refresh_header_status(self, *, connected: bool, info: str) -> None:
        """Aktualisiert Verbindungs-/Port-Text in der Statusleiste (links)."""
        self._connect_action.setEnabled(not connected)
        self._disconnect_action.setEnabled(connected)
        port = self._settings.cat.port or "?"
        baud = self._settings.cat.baudrate
        if connected:
            parts = [tr("status.connected")]
            if info:
                parts.append(info)
            parts.append(tr("status.connected_part_port", port=port, baud=baud))
            self._connection_footer_label.setText(" — ".join(parts))
            self._connection_footer_label.setStyleSheet("")
        else:
            self._connection_footer_label.setStyleSheet("color: gray;")
            if info and self._reconnect_timer.isActive():
                cfg_port = self._settings.cat.port or "?"
                self._connection_footer_label.setText(
                    tr(
                        "status.not_connected_reconnecting",
                        info=info,
                        port=cfg_port,
                        interval_s=self._reconnect_timer.interval() // 1000,
                    )
                )
            else:
                cfg_port = self._settings.cat.port
                if cfg_port:
                    self._connection_footer_label.setText(
                        tr(
                            "status.not_connected_ready",
                            port=cfg_port,
                            baud=baud,
                        )
                    )
                else:
                    self._connection_footer_label.setText(
                        tr("status.no_port_configured")
                    )

    # ------------------------------------------------------------------
    # Radio-Steuerung (Tune / Bandwahl)
    # ------------------------------------------------------------------

    def _on_tune_clicked(self) -> None:
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).start_antenna_tuner()
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 5000)

    def _on_repeater_minus_toggled(self, checked: bool) -> None:
        """Repeater-Minus per CAT (``OS02;`` / ``OS00;``) — nur FM/C4FM wirksam."""
        if not self._cat.is_connected():
            self._radio_control_bar.set_repeater_minus_checked(False)
            return
        try:
            ft = FT991CAT(self._cat)
            if checked:
                ft.try_set_repeater_shift_minus()
            else:
                ft.try_set_repeater_shift_simplex()
        except CatConnectionLostError:
            self._on_connection_lost()

    def _on_af_gain_slider_changed(self, level: int) -> None:
        """User hat den AF-Slider bewegt — CAT AG0 schreiben."""
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).write_af_gain(int(level))
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 5000)

    def _on_rf_gain_slider_changed(self, level: int) -> None:
        """User hat den RF-Slider bewegt — CAT RG0 schreiben."""
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).write_rf_gain(int(level))
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 5000)

    def _reset_relay_rev_state(self) -> None:
        self._relay_rev_active = False
        self._relay_output_hz = None
        self._relay_pre_rev_memory_channel = None
        self._radio_control_bar.set_rev_checked(False)

    def _try_clear_fm_repeater_shift_simplex(self) -> None:
        """FM/C4FM: Repeater-Shift per ``OS00;`` auf Simplex (wenn nicht Minus-Taste aktiv).

        Ist **Minus** in der Kontrollleiste eingeschaltet, wird nichts gesendet —
        sonst würde jede QRG-Änderung den Shift überschreiben.
        """
        if not self._cat.is_connected() or self._relay_rev_active:
            return
        if self._radio_control_bar.is_repeater_minus_checked():
            return
        try:
            FT991CAT(self._cat).try_set_repeater_shift_simplex()
        except CatConnectionLostError:
            self._on_connection_lost()
        self._radio_control_bar.set_repeater_minus_checked(False)

    def _audio_tx_busy(self) -> bool:
        """CAT-Sendung über Audio-Player oder -Recorder läuft."""
        if self._audio_player_window is not None:
            try:
                if self._audio_player_window._controller.is_busy():
                    return True
            except Exception:
                pass
        if self._audio_recorder_window is not None:
            try:
                if (
                    self._audio_recorder_window._player.is_busy()
                    or self._audio_recorder_window._recorder.is_busy()
                ):
                    return True
            except Exception:
                pass
        return False

    def _begin_tcall_session(self) -> None:
        self._tcall_suppress_radio_ui = True
        self._tcall_async_restore_pending = False
        self._tcall_saved_rx_mode = None
        self._tcall_saved_memory_channel = None
        if self._cat.is_connected():
            try:
                ft = FT991CAT(self._cat)
                self._tcall_saved_rx_mode = ft.read_rx_mode()
                active = ft.read_active_memory_channel()
                fa = ft.read_frequency()
                self._tcall_saved_memory_channel = _restore_memory_channel_if_fa_matches_slot(
                    ft, active, fa
                )
            except CatError:
                pass
        if self._tcall_saved_rx_mode is None:
            self._tcall_saved_rx_mode = self._main_operating_mode()

    def _tcall_restore_saved_memory_channel(self) -> None:
        """Speicherkanal nach T.CALL wieder aktivieren (``MC``/``VM`` — nicht nur ``MD``)."""
        ch = self._tcall_saved_memory_channel
        if ch is None or not self._cat.is_connected():
            return
        try:
            ft = FT991CAT(self._cat)
            ft.select_memory_channel(int(ch))
            self._select_memory_combo_by_channel(int(ch))
            restored_hz = ft.read_frequency()
            if restored_hz > 0:
                self._apply_vfo_a_display_hz(restored_hz)
            self._sync_mode_combo_to_saved_rx_mode(ft.read_rx_mode())
        except CatError as exc:
            self._on_t_call_error(str(exc))

    def _finish_tcall_session(self) -> None:
        if self._tcall_async_restore_pending:
            return
        self._tcall_restore_saved_memory_channel()
        self._tcall_suppress_radio_ui = False
        self._tcall_saved_rx_mode = None
        self._tcall_saved_memory_channel = None
        self.meter_widget.ensure_polling()
        self.meter_widget.request_immediate_poll()
        self._sync_meter_dsp_mode_visibility()

    def _tcall_invoke_async_restore(self) -> None:
        self._tcall_async_restore_pending = True
        _invoke_cat_worker_slot(self._audio_radio_session.worker, "run_restore")

    def _sync_mode_combo_to_saved_rx_mode(self, mode: RxMode) -> None:
        pw = self.profile_widget
        pw._last_radio_mode = mode
        idx = pw.mode_combo.findText(mode.value)
        if idx >= 0:
            pw.mode_combo.blockSignals(True)
            try:
                pw.mode_combo.setCurrentIndex(idx)
            finally:
                pw.mode_combo.blockSignals(False)
        self._status_mode_display = mode.value
        self._mode_label.setText(_status_bar_mode_text(mode.value))
        self._sync_meter_dsp_mode_visibility()

    def _tcall_restore_saved_rx_mode(self) -> bool:
        """Stellt die Betriebsart von vor T.CALL wieder her (nicht DATA-FM→FM)."""
        saved = self._tcall_saved_rx_mode
        if saved is None or not self._cat.is_connected():
            return False
        setup = self._audio_radio_session.setup
        try:
            ft = FT991CAT(self._cat)
            if ft.read_rx_mode() != saved:
                if saved in (RxMode.DATA_USB, RxMode.DATA_LSB, RxMode.DATA_FM):
                    ok, msg = setup.set_data_mode(saved)
                    if not ok:
                        self._on_t_call_error(
                            msg or tr("tcall.mode_not_restored", mode=saved.value)
                        )
                        return False
                elif not ft.set_rx_mode(saved):
                    self._on_t_call_error(
                        tr("tcall.mode_not_restored", mode=saved.value)
                    )
                    return False
            setup.reconcile_in_data_mode_with_radio()
        except CatError as exc:
            self._on_t_call_error(str(exc))
            return False
        self._sync_mode_combo_to_saved_rx_mode(saved)
        return True

    def _on_t_call_active_changed(self, active: bool) -> None:
        self._radio_control_bar.set_t_call_active(active)

    def _on_t_call_pressed(self) -> None:
        if not self._cat.is_connected():
            self._on_t_call_error(tr("tcall.cat_not_connected"))
            return
        if self._audio_tx_busy():
            QMessageBox.information(
                self,
                tr("tcall.title"),
                tr("tcall.audio_busy"),
            )
            return
        if self._tcall_cat_pending:
            return

        self.meter_widget.pause_polling()
        self._begin_tcall_session()
        self._tcall_release_restore_full = False
        self._tcall_release_engage_plain = False
        self._tcall_restore_data_mode = None

        setup = self._audio_radio_session.setup
        setup.set_data_mode(RxMode.DATA_FM)
        worker = self._audio_radio_session.worker

        if setup.in_data_mode:
            prev_data = setup.data_mode
            if prev_data != RxMode.DATA_FM:
                self._tcall_restore_data_mode = prev_data
                ok, msg = setup.set_data_mode(RxMode.DATA_FM)
                if not ok:
                    self._on_t_call_error(msg or tr("tcall.data_fm_not_set"))
                    self._finish_tcall_session()
                    return
                if msg:
                    self.statusBar().showMessage(tr("tcall.status", message=msg), 4000)
            QTimer.singleShot(150, self._t_call_arm_tx_and_audio)
            return

        self.statusBar().showMessage(tr("tcall.switching_data_fm"), 0)
        self._tcall_cat_pending = True

        if setup.is_applied:
            self._tcall_release_engage_plain = True
            _invoke_cat_worker_slot(worker, "run_engage_data")
            return

        has_audio_win = self._audio_radio_session.has_open_audio_windows
        self._tcall_release_restore_full = not has_audio_win
        self._tcall_release_engage_plain = has_audio_win
        _invoke_cat_worker_slot(worker, "run_apply")

    def _on_tcall_radio_apply_finished(self, ok: bool, message: str) -> None:
        if not self._tcall_cat_pending:
            return
        self._on_tcall_radio_cat_finished(ok, message)

    def _on_tcall_radio_engage_data_finished(self, ok: bool, message: str) -> None:
        if not self._tcall_cat_pending:
            return
        self._on_tcall_radio_cat_finished(ok, message)

    def _on_tcall_radio_cat_finished(self, ok: bool, message: str) -> None:
        self._tcall_cat_pending = False
        if not self._radio_control_bar.is_t_call_pressed():
            self._tcall_abort_radio_switch()
            return
        if not ok:
            self._on_t_call_error(message or tr("tcall.data_fm_failed"))
            self._finish_tcall_session()
            return
        if message:
            self.statusBar().showMessage(tr("tcall.status", message=message), 4000)
        QTimer.singleShot(150, self._t_call_arm_tx_and_audio)

    def _tcall_abort_radio_switch(self) -> None:
        """Taste losgelassen, bevor DATA-FM fertig — Funkzustand zurück."""
        if self._tcall_restore_data_mode is not None:
            mode = self._tcall_restore_data_mode
            self._tcall_restore_data_mode = None
            if self._cat.is_connected():
                self._audio_radio_session.setup.set_data_mode(mode)
            self._sync_mode_combo_to_saved_rx_mode(mode)
            self._tcall_release_restore_full = False
            self._tcall_release_engage_plain = False
            self._tcall_cat_pending = False
            self._finish_tcall_session()
            return
        if self._tcall_release_restore_full:
            self._tcall_invoke_async_restore()
        else:
            self._tcall_restore_saved_rx_mode()
        self._tcall_release_restore_full = False
        self._tcall_release_engage_plain = False
        self._tcall_restore_data_mode = None
        self._tcall_cat_pending = False
        if not self._tcall_async_restore_pending:
            self._finish_tcall_session()

    def _t_call_arm_tx_and_audio(self) -> None:
        if not self._radio_control_bar.is_t_call_pressed():
            self._tcall_abort_radio_switch()
            return
        if not self._cat.is_connected():
            self._finish_tcall_session()
            return
        try:
            FT991CAT(self._cat).set_cat_transmit(True, wait=False)
        except CatError as exc:
            self._on_t_call_error(str(exc))
            self._tcall_abort_radio_switch()
            return
        self._t_call.start()

    def _on_t_call_released(self) -> None:
        self._t_call.stop()
        if self._tcall_cat_pending:
            self._tcall_abort_radio_switch()
        elif self._cat.is_connected():
            try:
                FT991CAT(self._cat).set_cat_transmit(False, wait=False)
            except CatError as exc:
                self._on_t_call_error(str(exc))
            self._tcall_restore_radio_after_call()
        else:
            self._finish_tcall_session()

    def _on_tcall_async_restore_finished(self, ok: bool, message: str) -> None:
        if not self._tcall_async_restore_pending:
            return
        self._tcall_async_restore_pending = False
        if not ok and message:
            self._on_t_call_error(message)
        elif ok and message:
            self.statusBar().showMessage(tr("tcall.status", message=message), 4000)
        saved = self._tcall_saved_rx_mode
        if saved is not None:
            self._sync_mode_combo_to_saved_rx_mode(saved)
        self._finish_tcall_session()

    def _tcall_restore_radio_after_call(self) -> None:
        """Nach T.CALL: vorherigen Funk-Mode/Menüs wiederherstellen."""
        if self._tcall_restore_data_mode is not None:
            mode = self._tcall_restore_data_mode
            self._tcall_restore_data_mode = None
            if self._cat.is_connected():
                ok, msg = self._audio_radio_session.setup.set_data_mode(mode)
                if not ok:
                    self._on_t_call_error(
                        msg or tr("tcall.mode_not_restored", mode=mode.value)
                    )
                elif msg:
                    self.statusBar().showMessage(tr("tcall.status", message=msg), 4000)
            self._sync_mode_combo_to_saved_rx_mode(mode)
            self._tcall_release_restore_full = False
            self._tcall_release_engage_plain = False
            self._finish_tcall_session()
            return
        if not self._cat.is_connected():
            self._tcall_release_restore_full = False
            self._tcall_release_engage_plain = False
            self._finish_tcall_session()
            return
        if self._tcall_release_restore_full:
            self.statusBar().showMessage(tr("tcall.restoring_radio"), 3000)
            self._tcall_invoke_async_restore()
        else:
            self._tcall_restore_saved_rx_mode()
            self._finish_tcall_session()
        self._tcall_release_restore_full = False
        self._tcall_release_engage_plain = False

    def _on_t_call_error(self, message: str) -> None:
        self.statusBar().showMessage(tr("tcall.status", message=message), 5000)

    def _on_rev_toggled(self, active: bool) -> None:
        if not self._cat.is_connected():
            self._radio_control_bar.set_rev_checked(False)
            return
        from mapping.repeater_offset import relay_listen_hz

        ft = FT991CAT(self._cat)
        try:
            if active:
                # Aktuellen Zustand merken, damit wir ihn beim REV-Aus
                # wiederherstellen koennen: Memory-Kanal (oder VFO) +
                # Ausgangs-QRG.
                try:
                    pre_channel = ft.read_active_memory_channel()
                except CatError:
                    pre_channel = None
                output_hz = ft.read_frequency()
                if output_hz <= 0:
                    raise CatError(tr("error.no_valid_vfo_a_frequency"))
                try:
                    shift_dir = ft.read_if_shift_direction()
                except CatError:
                    shift_dir = 2
                listen_hz = relay_listen_hz(output_hz, shift_dir=shift_dir)
                # write_frequency setzt FA und schiebt das Geraet damit
                # implizit in den VFO-Mode (auch wenn vorher Memory aktiv
                # war). Das ist gewollt — die Eingangsfrequenz steht
                # ueblicherweise nicht im Memory.
                ft.write_frequency(listen_hz)
                self._notify_meter_app_frequency_write(listen_hz)
                self._relay_output_hz = output_hz
                self._relay_pre_rev_memory_channel = pre_channel
                self._relay_rev_active = True
                self._apply_vfo_a_display_hz(listen_hz)
            else:
                restore_channel = self._relay_pre_rev_memory_channel
                restore_hz = self._relay_output_hz
                if restore_channel is not None:
                    # Memory-Kanal stellt Frequenz + Mode + alle weiteren
                    # Kanalparameter atomar wieder her. Kein zusaetzliches
                    # FA-Write noetig (waere kontraproduktiv: wuerde das
                    # Geraet wieder in den VFO werfen).
                    ft.select_memory_channel(int(restore_channel))
                    self._select_memory_combo_by_channel(int(restore_channel))
                    restored_hz = ft.read_frequency()
                    if restored_hz > 0:
                        self._apply_vfo_a_display_hz(restored_hz)
                else:
                    if restore_hz is None or restore_hz <= 0:
                        restore_hz = ft.read_frequency()
                    ft.write_frequency(restore_hz)
                    self._notify_meter_app_frequency_write(restore_hz)
                    self._select_memory_combo_vfo()
                    self._apply_vfo_a_display_hz(restore_hz)
                self._relay_rev_active = False
                self._relay_output_hz = None
                self._relay_pre_rev_memory_channel = None
        except CatConnectionLostError:
            self._reset_relay_rev_state()
            self._on_connection_lost()
        except CatError as exc:
            self._reset_relay_rev_state()
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 5000)

    def _on_band_combo_activated(self, _index: int) -> None:
        data = self.band_combo.currentData()
        if data is None:
            return
        self._on_band_choice_activated(int(data))

    def _select_band_combo_vfo(self) -> None:
        idx = self.band_combo.findData(VFO_BAND_CHOICE)
        if idx >= 0:
            self.band_combo.blockSignals(True)
            self.band_combo.setCurrentIndex(idx)
            self.band_combo.blockSignals(False)

    def _sync_band_combo_to_frequency(self, hz: int) -> None:
        """Band-Dropdown = aktuelles Band (Amateur/CB/Freenet) oder „VFO“."""
        f = int(hz)
        if f <= 0:
            self._select_band_combo_vfo()
            return
        band = display_band_at_hz(f)
        if band is None:
            self._select_band_combo_vfo()
            return
        idx = self.band_combo.findData(band.center_hz)
        if idx < 0:
            return
        self.band_combo.blockSignals(True)
        self.band_combo.setCurrentIndex(idx)
        self.band_combo.blockSignals(False)

    def _on_band_choice_activated(self, choice: int) -> None:
        if not self._cat.is_connected():
            return

        ft = FT991CAT(self._cat)
        try:
            self._reset_relay_rev_state()
            if choice == VFO_BAND_CHOICE:
                if not ft.switch_to_vfo_mode():
                    raise CatError(tr("error.vfo_mode_failed"))
            else:
                if not ft.switch_to_vfo_mode():
                    raise CatError(tr("error.vfo_mode_failed"))
                band = band_at_center_hz(int(choice))
                hz = (
                    band_combo_target_frequency_hz(band)
                    if band is not None
                    else int(choice)
                )
                ft.write_frequency(hz)
                self._notify_meter_app_frequency_write(hz)
                self._relay_output_hz = hz
                self._apply_vfo_a_display_hz(hz)
            self._select_memory_combo_vfo()
            self._sync_band_combo_to_frequency(self._vfo_a_display_hz)
            # Bandwahl (Dropdown): Repeater-Minus immer aus — unabhängig vom Minus-Taster.
            self._radio_control_bar.set_repeater_minus_checked(False)
            self._try_clear_fm_repeater_shift_simplex()
            if choice == VFO_BAND_CHOICE:
                self._maybe_apply_band_voice_mode_for_hz(self._vfo_a_display_hz)
            else:
                target_hz = (
                    band_combo_target_frequency_hz(band)
                    if band is not None
                    else int(choice)
                )
                self._maybe_apply_band_voice_mode_for_hz(target_hz)
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 5000)

    def _maybe_apply_band_voice_mode_for_hz(self, hz: int) -> None:
        """HF: LSB unter 10 MHz, USB ab 10 MHz; UKW-Bänder: FM — bei Band- und QRG-Wahl."""
        if not self._cat.is_connected() or self._relay_rev_active:
            return
        if self._audio_tx_busy():
            return
        band = display_band_at_hz(int(hz))
        if band is None:
            self._last_applied_band_voice_mode = None
            return
        mode = preferred_voice_rx_mode_for_hz(int(hz))
        if mode is None:
            return
        if mode == self._last_applied_band_voice_mode:
            return
        try:
            FT991CAT(self._cat).set_rx_mode(mode)
            self._last_applied_band_voice_mode = mode
        except TxLockError:
            pass
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 4000)

    def _select_memory_combo_vfo(self, *, reset_favorites_placeholder: bool = True) -> None:
        vfo_idx = self.memory_combo.findData(self._VFO_ITEM_DATA)
        if vfo_idx < 0:
            return
        self.memory_combo.blockSignals(True)
        self.memory_combo.setCurrentIndex(vfo_idx)
        self.memory_combo.blockSignals(False)
        if reset_favorites_placeholder:
            self._reset_favorites_combo_to_placeholder()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _rig_bridge_log_write(self, level: str, msg: str) -> None:
        lvl = (level or "INFO").upper()
        if lvl == "WARN":
            self._cat_log.log_warn(msg)
        elif lvl == "ERROR":
            self._cat_log.log_error(msg)
        else:
            self._cat_log.log_info(msg)

    def _refresh_rig_bridge_toolbar_leds(self) -> None:
        """FLRig-LED und Client-Zähler (inkl. TCP-Aktivitäts-Blink)."""
        fl_io = self._rig_bridge.take_bridge_activity_flags()
        self._radio_control_bar.refresh_rig_bridge_indicators(
            self._settings.rig_bridge.to_dict(),
            self._rig_bridge.protocol_status(),
            fl_io,
        )

    def _on_connection_changed(self, connected: bool) -> None:
        self.profile_widget.set_cat_available(connected)
        self.meter_widget.on_connection_changed(connected)
        self._radio_control_bar.set_controls_enabled(connected)
        self._favorites_panel.setEnabled(connected)
        if connected:
            self._rig_bridge.update_config(self._settings.rig_bridge.to_dict())
            self._rig_bridge.on_app_connected()
        else:
            self._rig_bridge.on_app_disconnected()
        if not connected:
            self._t_call.stop()
            self._reset_relay_rev_state()
            self._last_applied_band_voice_mode = None
            self._status_mode_display = tr("common.dash")
            self._mode_label.setText(_status_bar_mode_text(self._status_mode_display))
            self._vfo_a_display_hz = 0
            self._vfo_b_display_hz = 0
            self._update_vfo_caption_band_color(self._vfo_a_caption, 0)
            self._update_vfo_caption_band_color(self._vfo_b_caption, 0)
            self._vfo_a_triplet.set_placeholder_empty()
            self._vfo_b_triplet.set_placeholder_empty()
            self._vfo_a_triplet.set_interactive(False)
            self._vfo_b_triplet.set_interactive(False)
            self._vfo_ab_button.setEnabled(False)
            self._tx_label.setText(_status_bar_tx_text(False))
            # Bei Verbindungsverlust laufenden Loader stoppen und die
            # Combo zurücksetzen — sonst zeigt sie veraltete Kanäle.
            self._memory_loader.stop()
            self._reset_memory_combo()
            self.memory_combo.setEnabled(False)
            self.band_combo.setEnabled(False)
        else:
            self._vfo_a_triplet.set_interactive(True)
            self._vfo_b_triplet.set_interactive(True)
            self._vfo_ab_button.setEnabled(True)
        self._refresh_band_strip()
        self._persist_settings()
        self._notify_live_window_cat_connection(connected)

    def _notify_live_window_cat_connection(self, connected: bool) -> None:
        if connected:
            return
        win = self._live_window
        if win is None:
            return
        fn = getattr(win, "handle_cat_disconnected", None)
        if callable(fn):
            fn()

    def _resume_live_window_after_connect(self) -> None:
        """Live-Fenster nach abgeschlossener Connect-Init wieder anbinden."""
        win = getattr(self, "_live_window", None)
        if win is None or not win.isVisible():
            return
        if not self._cat.is_connected():
            return
        fn = getattr(win, "handle_cat_reconnected", None)
        if callable(fn):
            fn()

    def _on_meter_status_message(self, message: str, timeout_ms: int) -> None:
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(message, max(2000, int(timeout_ms)))

    def _on_tx_status_changed(self, transmitting: bool) -> None:
        self._status_tx_transmitting = bool(transmitting)
        self._tx_label.setText(_status_bar_tx_text(transmitting))
        self._rig_bridge.update_from_radio(ptt=transmitting)
        if transmitting:
            self._tx_label.setStyleSheet("color: #ff6060; font-weight: bold;")
        else:
            self._tx_label.setStyleSheet("")

    def _update_vfo_caption_band_color(self, caption: QLabel, hz: int) -> None:
        if hz <= 0:
            caption.setStyleSheet(_VFO_CAPTION_STYLE_IDLE)
            caption.setToolTip("")
            return
        band = display_band_at_hz(hz)
        if band is None:
            caption.setStyleSheet(_VFO_CAPTION_STYLE_OUT_OF_BAND)
            caption.setToolTip(tr("main.vfo.tooltip.out_of_band"))
            return
        if band.kind is BandKind.AMATEUR:
            caption.setStyleSheet(_VFO_CAPTION_STYLE_IN_BAND)
            caption.setToolTip(tr("main.vfo.tooltip.in_band", band=band.name))
        else:
            caption.setStyleSheet(_VFO_CAPTION_STYLE_SPECIAL_BAND)
            caption.setToolTip(tr("main.vfo.tooltip.special_band", band=display_band_label_at_hz(hz) or band.name))

    def _band_strip_name_style(self, band) -> str:
        if band is None:
            return _VFO_CAPTION_STYLE_IDLE
        if band.kind is BandKind.AMATEUR:
            return _VFO_CAPTION_STYLE_IN_BAND
        return _VFO_CAPTION_STYLE_SPECIAL_BAND

    def _refresh_band_strip(self) -> None:
        connected = self._cat.is_connected()
        hz = self._vfo_a_display_hz if connected else 0
        band = display_band_at_hz(hz) if hz > 0 else None
        if band is not None:
            self._band_strip_name.setText(display_band_label_at_hz(hz) or band.name)
            self._band_strip_name.setStyleSheet(self._band_strip_name_style(band))
        elif connected and hz > 0:
            self._band_strip_name.setText(tr("common.dash"))
            self._band_strip_name.setStyleSheet(_VFO_CAPTION_STYLE_OUT_OF_BAND)
        else:
            self._band_strip_name.setText(tr("common.dash"))
            self._band_strip_name.setStyleSheet(_VFO_CAPTION_STYLE_IDLE)
        self._band_strip.set_active(connected)
        self._band_strip.set_band(band)
        self._band_strip.set_frequency_hz(hz)
        if connected:
            self._sync_band_combo_to_frequency(hz)

    def _on_band_strip_frequency(self, hz: int) -> None:
        if not self._cat.is_connected():
            return
        self._vfo_a_display_hz = hz
        if not self._relay_rev_active:
            self._relay_output_hz = hz
        self._vfo_a_triplet.set_frequency_hz(hz)
        self._update_vfo_caption_band_color(self._vfo_a_caption, hz)
        self._refresh_band_strip()
        self._write_vfo_a_to_radio(hz, fast_drag=True)

    def _on_band_strip_drag_finished(self, hz: int) -> None:
        if not self._cat.is_connected():
            return
        self._write_vfo_a_to_radio(hz, force=True)

    def _apply_vfo_a_display_hz(self, hz: int) -> None:
        """VFO-A-Anzeige sofort setzen (z. B. nach REV oder CAT-Schreiben)."""
        if hz <= 0:
            return
        if self._band_strip.is_dragging():
            return
        self._vfo_a_display_hz = hz
        self._vfo_a_triplet.set_frequency_hz(hz)
        self._update_vfo_caption_band_color(self._vfo_a_caption, hz)
        self._rig_bridge.update_from_radio(frequency_hz=hz)
        self._refresh_band_strip()

    def _maybe_leave_memory_for_vfo_tune(self, frequency_hz: int) -> None:
        """MC/Combo noch Speicher, aber Frequenz ≠ Slot — Nutzer hat gedreht → VFO.

        Nicht während **TX** anwenden: ``FA`` kann sich beim Senden vom
        gespeicherten ``MT``-Raster unterscheiden (Clarifier, Geräteverhalten),
        ohne dass der Nutzer wirklich in den VFO-Modus gewechselt hat.
        """
        if self._relay_rev_active:
            return
        if not self.memory_combo.isEnabled():
            return
        cur = self.memory_combo.currentData()
        if not isinstance(cur, int) or int(cur) <= 0:
            return
        ch = int(cur)
        expected = self._memory_slot_frequency_hz.get(ch)
        if expected is None:
            return
        if abs(int(frequency_hz) - int(expected)) <= _MEM_FREQ_MATCH_TOLERANCE_HZ:
            return
        try:
            FT991CAT(self._cat).switch_to_vfo_mode()
        except (CatConnectionLostError, CatError):
            pass
        self._select_memory_combo_vfo()
        self._sync_band_combo_to_frequency(int(frequency_hz))
        self._try_clear_fm_repeater_shift_simplex()

    def _on_repeater_shift_polled(self, direction: int) -> None:
        """IF; P10 vom Poller — Shift-Anzeige (Simp / RPT+ / RPT-) und Minus-Taste."""
        if self._relay_rev_active:
            return
        self._radio_control_bar.sync_repeater_shift_from_if(int(direction))

    def _on_rx_info_changed(
        self,
        mode: object,
        frequency_hz: int,
        frequency_b_hz: int,
        radio_transmitting: bool = False,
    ) -> None:
        """Vom MeterWidget bei VFO/Mode-Updates (RX-Slow-Path und ggf. FA/FB während TX)."""
        if self._tcall_suppress_radio_ui:
            return
        if isinstance(mode, RxMode):
            self._status_mode_display = mode.value
            self._mode_label.setText(_status_bar_mode_text(mode.value))
            self._rig_bridge.update_from_radio(mode=mode.value)
        else:
            self._rig_bridge.update_from_radio()
        if frequency_hz > 0:
            if not self._relay_rev_active:
                if not radio_transmitting:
                    self._maybe_leave_memory_for_vfo_tune(int(frequency_hz))
                self._relay_output_hz = frequency_hz
            self._apply_vfo_a_display_hz(frequency_hz)
        if frequency_b_hz > 0:
            self._vfo_b_display_hz = frequency_b_hz
            self._vfo_b_triplet.set_frequency_hz(frequency_b_hz)
            self._update_vfo_caption_band_color(self._vfo_b_caption, frequency_b_hz)

    def _on_memory_channel_from_radio(self, channel: int) -> None:
        """Slow-Path-Poller meldet einen Wechsel des aktiven Memory-
        Kanals (User hat am Gerät den MEM/CH-Knopf gedreht). Die Combo
        wird nachgezogen, ohne dabei einen CAT-Befehl zu senden.

        Während REV aktiv ist, ignorieren wir das Update: Das Gerät steht
        dann im VFO-Modus, aber die Combo soll weiterhin den vor REV
        aktiven Memory-Kanal zeigen (der nach REV-Aus wiederhergestellt
        wird).
        """
        if self._relay_rev_active:
            return
        # Während der Memory-Loader läuft, ist die Combo disabled und
        # wird vom Loader selbst befüllt — kein paralleles Update nötig.
        if not self.memory_combo.isEnabled():
            return
        current = self.memory_combo.currentData()
        if channel <= 0:
            if current != self._VFO_ITEM_DATA:
                self._select_memory_combo_vfo()
                self._sync_band_combo_to_frequency(self._vfo_a_display_hz)
        else:
            if current != channel:
                self._select_memory_combo_by_channel(channel)

    def _vfo_a_fast_write_interval_ms(self) -> int:
        """CAT-Takt beim Band-Streifen (schnell, an RX-Poll orientiert)."""
        rx_ms = int(self._settings.polling.rx_interval_ms)
        return max(50, min(rx_ms, FREQ_CATCHUP_POLL_MS))

    def _vfo_a_write_interval_ms(self) -> int:
        """CAT-Takt für VFO-Eingabe — entspricht dem RX-Poll-Intervall."""
        return max(50, int(self._settings.polling.rx_interval_ms))

    def _on_rig_bridge_frequency_written(self, hz: int, from_flrig: bool = True) -> None:
        """FLRig/Bridge: kein 900-ms-Poll-Stopp — nur Referenz für Catchup-Logik."""
        if hz > 0:
            self.meter_widget.note_flrig_frequency_hz(int(hz))
            self._rig_bridge.update_from_radio(frequency_hz=int(hz))

    def _notify_meter_app_frequency_write(
        self, hz: int, *, hold_ms: int = -1
    ) -> None:
        if hz > 0:
            self.meter_widget.notify_app_frequency_write(int(hz), hold_ms)

    def _write_vfo_a_to_radio(
        self, hz: int, *, force: bool = False, fast_drag: bool = False
    ) -> None:
        if not self._cat.is_connected():
            return
        target = int(hz)
        if target <= 0:
            return
        now = time.monotonic()
        if fast_drag:
            interval_s = self._vfo_a_fast_write_interval_ms() / 1000.0
            hold_ms = APP_FREQ_WRITE_DRAG_HOLD_MS
        else:
            interval_s = self._vfo_a_write_interval_ms() / 1000.0
            hold_ms = APP_FREQ_WRITE_HOLD_MS
        if (
            not force
            and self._vfo_a_last_written_hz == target
            and (now - self._vfo_a_last_write_mono) < interval_s
        ):
            self._vfo_a_pending_hz = target
            return
        self._vfo_a_write_timer.stop()
        self._vfo_a_pending_hz = None
        try:
            FT991CAT(self._cat).write_frequency(target)
            self._vfo_a_last_written_hz = target
            self._vfo_a_last_write_mono = now
            if not self._relay_rev_active:
                self._relay_output_hz = target
            self._notify_meter_app_frequency_write(target, hold_ms=hold_ms)
            if force:
                self._try_clear_fm_repeater_shift_simplex()
            self._maybe_apply_band_voice_mode_for_hz(target)
        except CatError as exc:
            QMessageBox.warning(
                self, tr("vfo.error.title_a"), tr("vfo.error.message", error=str(exc))
            )

    def _on_user_vfo_a_frequency(self, hz: int) -> None:
        if not self._cat.is_connected():
            return
        self._vfo_a_display_hz = hz
        if not self._relay_rev_active:
            self._relay_output_hz = hz
        self._update_vfo_caption_band_color(self._vfo_a_caption, hz)
        self._vfo_a_pending_hz = hz
        pending_ms = max(
            1,
            self._vfo_a_write_interval_ms() - int(
                (time.monotonic() - self._vfo_a_last_write_mono) * 1000
            ),
        )
        self._vfo_a_write_timer.setInterval(pending_ms)
        self._vfo_a_write_timer.start()

    def _flush_vfo_a_frequency_write(self) -> None:
        if not self._cat.is_connected() or self._vfo_a_pending_hz is None:
            return
        hz = self._vfo_a_pending_hz
        self._write_vfo_a_to_radio(hz, force=True)

    def _on_user_vfo_b_frequency(self, hz: int) -> None:
        if not self._cat.is_connected():
            return
        self._vfo_b_display_hz = hz
        self._update_vfo_caption_band_color(self._vfo_b_caption, hz)
        self._vfo_b_pending_hz = hz
        self._vfo_b_write_timer.start()

    def _flush_vfo_b_frequency_write(self) -> None:
        if not self._cat.is_connected() or self._vfo_b_pending_hz is None:
            return
        hz = self._vfo_b_pending_hz
        self._vfo_b_pending_hz = None
        try:
            FT991CAT(self._cat).write_frequency_b(hz)
        except CatError as exc:
            QMessageBox.warning(
                self, tr("vfo.error.title_b"), tr("vfo.error.message", error=str(exc))
            )

    def _on_vfo_ab_clicked(self) -> None:
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).swap_vfo_a_and_b()
        except CatError as exc:
            QMessageBox.warning(
                self, tr("vfo.error.title_ab"), tr("vfo.error.message", error=str(exc))
            )

    def _on_rx_info_for_profile(
        self,
        mode: object,
        _frequency_hz: int,
        _frequency_b_hz: int,
        _radio_transmitting: bool = False,
    ) -> None:
        """Reicht den Radio-Mode an das ProfileWidget weiter.

        Damit folgt die EQ-Profil-Mode-Combo automatisch dem, was am Radio
        eingestellt ist (SSB/AM/FM/DATA/C4FM). Andere Modi (CW/RTTY) werden
        ignoriert, sodass die letzte gültige Auswahl erhalten bleibt.
        """
        if self._tcall_suppress_radio_ui:
            return
        self.profile_widget.notify_radio_mode(mode)

    def _sync_language_menu_checks(self) -> None:
        lang = self._settings.ui.language
        self._language_de_action.blockSignals(True)
        self._language_en_action.blockSignals(True)
        try:
            self._language_de_action.setChecked(lang == "de")
            self._language_en_action.setChecked(lang == "en")
        finally:
            self._language_de_action.blockSignals(False)
            self._language_en_action.blockSignals(False)

    def _on_language_de(self) -> None:
        if self._settings.ui.language == "de":
            self._sync_language_menu_checks()
            return
        set_language("de")
        self._settings.ui.language = "de"
        self._persist_settings()
        self.retranslate_ui()
        self._propagate_retranslate_to_children()

    def _on_language_en(self) -> None:
        if self._settings.ui.language == "en":
            self._sync_language_menu_checks()
            return
        set_language("en")
        self._settings.ui.language = "en"
        self._persist_settings()
        self.retranslate_ui()
        self._propagate_retranslate_to_children()

    def _propagate_retranslate_to_children(self) -> None:
        for win in (
            self._log_window,
            self._equalizer_window,
            self._audio_player_window,
            self._audio_recorder_window,
            self._live_window,
        ):
            if win is None:
                continue
            retranslate = getattr(win, "retranslate_ui", None)
            if callable(retranslate):
                retranslate()

    def _retranslate_memory_combo_labels(self) -> None:
        if self.memory_combo.count() <= 0:
            return
        if self.memory_combo.itemData(0) == self._VFO_ITEM_DATA:
            if self._memory_loader.is_running:
                vfo_label = tr("main.memory.vfo_loading_channels")
            elif (
                not self.memory_combo.isEnabled()
                and self._connection_footer_label.text()
                == tr("status.memory_cache_header")
            ):
                vfo_label = tr("main.memory.vfo_loading")
            else:
                vfo_label = tr("main.memory.vfo")
            self.memory_combo.setItemText(0, vfo_label)
        for i in range(1, self.memory_combo.count()):
            data = self.memory_combo.itemData(i)
            if not isinstance(data, int) or int(data) <= 0:
                continue
            ch = int(data)
            mem = self._memory_combo_catalog.get(ch)
            if mem is not None:
                self.memory_combo.setItemText(
                    i, self._format_memory_channel_combo_label(mem)
                )

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("main_window.title", app_name=APP_NAME, version=APP_VERSION))

        self._file_menu.setTitle(tr("menu.file"))
        self._settings_action.setText(tr("menu.file.settings"))
        self._connect_action.setText(tr("menu.file.connect"))
        self._disconnect_action.setText(tr("menu.file.disconnect"))
        self._quit_action.setText(tr("menu.file.quit"))

        self._functions_menu.setTitle(tr("menu.functions"))
        self._memory_action.setText(tr("menu.functions.memory_channels"))
        self._equalizer_action.setText(tr("menu.functions.equalizer"))
        self._sound_settings_action.setText(tr("menu.functions.sound_settings"))
        self._audio_player_action.setText(tr("menu.functions.audio_player"))
        self._audio_recorder_action.setText(tr("menu.functions.audio_recorder"))
        self._live_audio_action.setText(tr("menu.functions.live_pc"))

        self._view_menu.setTitle(tr("menu.view"))
        self.log_toggle_action.setText(tr("menu.view.cat_log"))
        self._language_menu.setTitle(tr("menu.view.language"))
        self._language_de_action.setText(tr("menu.view.language_de"))
        self._language_en_action.setText(tr("menu.view.language_en"))
        self._sync_language_menu_checks()

        self._help_menu.setTitle(tr("menu.help"))
        self._version_action.setText(tr("menu.help.version"))
        self._manual_action.setText(tr("menu.help.manual"))
        self._update_check_action.setText(tr("menu.help.check_updates"))

        self._vfo_a_caption.setText(tr("main.vfo_a_caption"))
        self._vfo_b_caption.setText(tr("main.vfo_b_caption"))
        self._vfo_ab_button.setText(tr("main.vfo_ab_button"))
        self._vfo_ab_button.setToolTip(tr("main.vfo_ab_tooltip"))
        self._band_strip_caption.setText(tr("main.band_caption"))

        self._lbl_mode_group.setText(tr("main.mode_group"))
        self._lbl_eq_profile.setText(tr("main.eq_profile"))
        self._lbl_memory_channel.setText(tr("main.memory_channel"))
        self._lbl_band_combo.setText(tr("main.band_combo"))
        self.memory_combo.setToolTip(tr("main.memory_channel_tooltip"))
        self.band_combo.setToolTip(tr("main.band_combo_tooltip"))

        self._favorites_panel.retranslate_ui()
        fav_placeholder_idx = self._favorites_panel.combo.findData(None)
        if fav_placeholder_idx >= 0:
            self._favorites_panel.combo.setItemText(
                fav_placeholder_idx, tr("favorites.placeholder")
            )

        self._radio_control_bar.retranslate_ui()
        band_strip_rt = getattr(self._band_strip, "retranslate_ui", None)
        if callable(band_strip_rt):
            band_strip_rt()

        self._update_vfo_caption_band_color(self._vfo_a_caption, self._vfo_a_display_hz)
        self._update_vfo_caption_band_color(self._vfo_b_caption, self._vfo_b_display_hz)
        self._refresh_band_strip()
        self._retranslate_memory_combo_labels()

        self._mode_label.setText(_status_bar_mode_text(self._status_mode_display))
        self._tx_label.setText(_status_bar_tx_text(self._status_tx_transmitting))

        self._refresh_header_status(
            connected=self._cat.is_connected(),
            info=self._last_identity_info,
        )

    # ------------------------------------------------------------------
    # Connect-Init: Funkzustand merken / wiederherstellen
    # ------------------------------------------------------------------

    def _clear_connect_restore_snapshot(self) -> None:
        self._connect_restore_memory_channel = None
        self._connect_restore_vfo_a_hz = None
        self._connect_restore_vfo_b_hz = None
        self._connect_restore_mode = None

    def _capture_connect_radio_state(self) -> None:
        """Liest Speicherkanal, VFO-A/B und Mode vom Funkgerät vor dem MT-Scan."""
        self._clear_connect_restore_snapshot()
        if not self._cat.is_connected():
            return
        try:
            ft = FT991CAT(self._cat)
            active = ft.read_active_memory_channel()
            try:
                self._connect_restore_mode = ft.read_rx_mode()
            except CatError as exc:
                self._cat_log.log_warn(f"Connect: Mode lesen fehlgeschlagen: {exc}")
            fa = 0
            try:
                fa = int(ft.read_frequency())
                if fa > 0:
                    self._connect_restore_vfo_a_hz = fa
            except CatError as exc:
                self._cat_log.log_warn(f"Connect: VFO-A lesen fehlgeschlagen: {exc}")
            try:
                fb = ft.read_frequency_b()
                if fb > 0:
                    self._connect_restore_vfo_b_hz = int(fb)
            except CatError as exc:
                self._cat_log.log_warn(f"Connect: VFO-B lesen fehlgeschlagen: {exc}")

            restore_ch = _restore_memory_channel_if_fa_matches_slot(ft, active, fa)
            if restore_ch is not None:
                self._connect_restore_memory_channel = restore_ch
                self._cat_log.log_info(
                    f"Connect: Funkgerät auf Speicherkanal {restore_ch:03d} "
                    "(wird nach Init wiederhergestellt)"
                )
            else:
                if active is not None and int(active) > 0 and fa > 0:
                    self._cat_log.log_info(
                        f"Connect: VFO — MC meldet Kanal {int(active):03d}, aber VFO-A "
                        "passt nicht zum Speicherinhalt (nach Init VFO/Frequenz wiederherstellen)"
                    )
                else:
                    self._cat_log.log_info(
                        "Connect: Funkgerät im VFO-Modus "
                        "(Frequenz und Mode werden nach Init wiederhergestellt)"
                    )
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        except CatError as exc:
            self._cat_log.log_warn(f"Connect: Funkzustand lesen fehlgeschlagen: {exc}")
            return
        self._apply_connect_snapshot_to_ui()

    def _apply_connect_snapshot_to_ui(self) -> None:
        """Zeigt den beim Connect gelesenen Funkzustand sofort in der GUI."""
        if self._connect_restore_vfo_a_hz and self._connect_restore_vfo_a_hz > 0:
            self._apply_vfo_a_display_hz(self._connect_restore_vfo_a_hz)
        if self._connect_restore_vfo_b_hz and self._connect_restore_vfo_b_hz > 0:
            self._vfo_b_display_hz = self._connect_restore_vfo_b_hz
            self._vfo_b_triplet.set_frequency_hz(self._connect_restore_vfo_b_hz)
            self._update_vfo_caption_band_color(
                self._vfo_b_caption, self._connect_restore_vfo_b_hz
            )
        if self._connect_restore_mode is not None:
            self._mode_label.setText(
                _status_bar_mode_text(self._connect_restore_mode.value)
            )
            self.profile_widget.notify_radio_mode(self._connect_restore_mode)

    def _prepare_connect_for_cat_bulk_io(self) -> None:
        """VFO-Modus für MT-Scan und Profil-Roundtrips (nur wenn vorher Memory)."""
        if self._connect_restore_memory_channel is None:
            return
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).switch_to_vfo_mode()
            self._cat_log.log_info(
                "Connect: VFO-Modus für Speicherkanal-Scan und Profil-Sync"
            )
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            self._cat_log.log_warn(
                f"Connect: VFO-Umschaltung vor Init fehlgeschlagen: {exc}"
            )

    def _begin_connect_init(self) -> None:
        self._connect_init_pending = 2

    def _connect_init_step_done(self, _source: str) -> None:
        if self._connect_init_pending <= 0:
            return
        self._connect_init_pending -= 1
        if self._connect_init_pending > 0:
            return
        self._finish_connect_init()

    def _on_connect_profile_worker_finished(self) -> None:
        if self._connect_init_pending > 0:
            self._connect_init_step_done("profile")

    def _finish_connect_init(self) -> None:
        """Nach Profil-Write und Memory-Load: Funkzustand vom Start wiederherstellen."""
        if not self._cat.is_connected():
            self._connect_init_pending = 0
            return
        restore_ch = self._connect_restore_memory_channel
        restore_a = self._connect_restore_vfo_a_hz
        restore_b = self._connect_restore_vfo_b_hz
        restore_mode = self._connect_restore_mode
        self._clear_connect_restore_snapshot()
        self._connect_init_pending = 0
        ft = FT991CAT(self._cat)
        status_msg = ""
        try:
            if restore_ch is not None:
                self._cat_log.log_info(
                    f"=== Speicherkanal {restore_ch:03d} wiederherstellen "
                    "(Zustand vor Software-Start) ==="
                )
                ft.select_memory_channel(restore_ch)
                status_msg = tr(
                    "status.radio_restore_memory", channel=int(restore_ch)
                )
            else:
                self._cat_log.log_info(
                    "=== VFO-Zustand wiederherstellen "
                    "(Frequenz und Mode vom Start) ==="
                )
                if not ft.switch_to_vfo_mode():
                    raise CatError(tr("error.vfo_mode_failed"))
                if restore_a is not None and restore_a > 0:
                    ft.write_frequency(restore_a)
                    self._notify_meter_app_frequency_write(restore_a)
                if restore_b is not None and restore_b > 0:
                    ft.write_frequency_b(restore_b)
                if restore_mode is not None:
                    ft.set_rx_mode(restore_mode)
                status_msg = tr("status.radio_restore_vfo")
            hz = ft.read_frequency()
            if hz > 0:
                self._apply_vfo_a_display_hz(hz)
            try:
                fb = ft.read_frequency_b()
                if fb > 0:
                    self._vfo_b_display_hz = fb
                    self._vfo_b_triplet.set_frequency_hz(fb)
                    self._update_vfo_caption_band_color(self._vfo_b_caption, fb)
            except CatError:
                pass
            try:
                mode = ft.read_rx_mode()
                self._status_mode_display = mode.value
                self._mode_label.setText(_status_bar_mode_text(mode.value))
                self.profile_widget.notify_radio_mode(mode)
            except CatError:
                if restore_mode is not None:
                    self._status_mode_display = restore_mode.value
                    self._mode_label.setText(
                        _status_bar_mode_text(restore_mode.value)
                    )
                    self.profile_widget.notify_radio_mode(restore_mode)
            self._sync_memory_combo_from_radio(
                preferred_memory_after_restore=(
                    restore_ch if restore_ch is not None else None
                ),
            )
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        except CatError as exc:
            self._cat_log.log_warn(
                f"Funkzustand nach Connect-Init nicht wiederherstellbar: {exc}"
            )
            self._sync_memory_combo_from_radio(
                preferred_memory_after_restore=(
                    restore_ch if restore_ch is not None else None
                ),
            )
        sb = self.statusBar()
        if sb is not None and status_msg:
            sb.showMessage(status_msg, 4000)
        self._resume_live_window_after_connect()

    # ------------------------------------------------------------------
    # Speicherkanal-Combo
    # ------------------------------------------------------------------

    #: Sentinel, der den VFO-Eintrag im Combo markiert (anstelle einer
    #: Kanalnummer wird beim Wechsel auf diesen Eintrag VFO-Modus
    #: aktiviert).
    _VFO_ITEM_DATA = -1

    def _reset_memory_combo(self, *, placeholder: str | None = None) -> None:
        """Setzt die Combo auf den Initial-Zustand: nur „VFO" als erster
        Eintrag. Signale werden während des Resets blockiert, damit kein
        Memory-Wechsel zum Radio geschickt wird.
        """
        if placeholder is None:
            placeholder = tr("main.memory.vfo")
        self._memory_combo_catalog.clear()
        self._memory_slot_frequency_hz.clear()
        self.memory_combo.blockSignals(True)
        try:
            self.memory_combo.clear()
            self.memory_combo.addItem(placeholder, self._VFO_ITEM_DATA)
            self.memory_combo.setCurrentIndex(0)
        finally:
            self.memory_combo.blockSignals(False)

    def _normalize_memory_combo_vfo_label(self) -> None:
        """Ersten Eintrag nach dem Laden wieder auf „VFO" setzen."""
        if self.memory_combo.count() > 0:
            self.memory_combo.setItemText(0, tr("main.memory.vfo"))

    def _format_memory_channel_combo_label(self, mem: MemoryChannel) -> str:
        """Einzeiliger Combobox-Text wie beim Hintergrund-Loader."""
        freq_mhz = mem.frequency_hz / 1_000_000.0
        tag = mem.tag.strip() or tr("main.memory.no_name")
        mode_label = (
            mem.mode.value
            if mem.mode is not None and mem.mode.value != "?"
            else "?"
        )
        return tr(
            "main.memory.channel_label",
            channel=mem.channel,
            tag=tag,
            freq_mhz=freq_mhz,
            mode=mode_label,
        )

    def _memory_combo_index_for_channel(self, channel: int) -> int:
        """Index der Zeile mit Nutzdaten ``channel``, sonst -1.

        ``itemData`` kann je nach Qt-Binding als ``int`` oder anderer
        numerischer Typ kommen — daher ``int(...)``-Vergleich.
        """
        ch = int(channel)
        for i in range(self.memory_combo.count()):
            data = self.memory_combo.itemData(i)
            if data is None:
                continue
            try:
                if int(data) == ch:
                    return i
            except (TypeError, ValueError):
                continue
        return -1

    def _memory_combo_insert_index_for_channel(self, channel: int) -> int:
        """Einfügeindex für einen Speicherkanal (>0): VFO bei 0, danach nach Kanalnr. aufsteigend."""
        ch_i = int(channel)
        for i in range(1, self.memory_combo.count()):
            data = self.memory_combo.itemData(i)
            if data is None:
                continue
            try:
                other = int(data)
            except (TypeError, ValueError):
                continue
            if other <= 0:
                continue
            if ch_i < other:
                return i
        return self.memory_combo.count()

    def _upsert_memory_combo_channel(self, mem: MemoryChannel) -> None:
        """Eine Memo-Zeile idempotent nach Kanalnummer: Duplikate entfernen, sortiert einfügen.

        Verhindert doppelte Einträge wenn z. B. :meth:`_select_memory_combo_by_channel`
        vor Ende des MT-Scans eingreift — der Loader hätte später sonst erneut
        ``addItem`` aufgerufen.
        """
        ch = int(mem.channel)
        hz = int(mem.frequency_hz)
        self._memory_slot_frequency_hz[ch] = hz
        self._memory_combo_catalog[ch] = mem
        label = self._format_memory_channel_combo_label(mem)
        for i in range(self.memory_combo.count() - 1, 0, -1):
            data = self.memory_combo.itemData(i)
            if data is None:
                continue
            try:
                if int(data) != ch:
                    continue
            except (TypeError, ValueError):
                continue
            self.memory_combo.removeItem(i)
        insert_at = self._memory_combo_insert_index_for_channel(ch)
        self.memory_combo.insertItem(insert_at, label, ch)

    def _apply_local_memory_overrides(self, channel: int) -> None:
        """Lokale SQL-/Power-Werte für Speicherkanal ``channel`` (settings.json) ans Gerät."""
        if not self._cat.is_connected():
            return
        ch = int(channel)
        if ch <= 0:
            return

        raw_sql = self._settings.ui.memory_channel_local_sql.get(str(ch))
        if raw_sql is not None:
            try:
                sql = int(cast(Any, raw_sql))
            except (TypeError, ValueError):
                pass
            else:
                self.meter_widget.apply_local_memory_sql(sql)

        raw_pw = self._settings.ui.memory_channel_local_pc_power.get(str(ch))
        if raw_pw is not None:
            try:
                watts = int(cast(Any, raw_pw))
            except (TypeError, ValueError):
                pass
            else:
                hz = int(self._memory_slot_frequency_hz.get(ch, 0))
                self.meter_widget.apply_local_memory_pc_power(
                    watts, frequency_hz=hz if hz > 0 else None
                )

    def _select_memory_combo_by_channel(
        self, channel: int, *, reset_favorites_placeholder: bool = True
    ) -> None:
        """Wählt einen Kanal in der Combo (ohne CAT-Befehl).

        Wenn die Zeile nach dem Laden fehlt (z. B. Nutzdaten-Typ oder
        Timing), wird ``MT`` einmal gelesen und dieselbe Beschriftung wie
        beim Loader erzeugt — nicht dauerhaft „(aktuell aktiv)".
        """
        ch = int(channel)
        idx = self._memory_combo_index_for_channel(ch)
        if idx >= 0:
            self.memory_combo.setCurrentIndex(idx)
            if reset_favorites_placeholder:
                self._reset_favorites_combo_to_placeholder()
            self._apply_local_memory_overrides(ch)
            return

        label: str
        mem: Optional[MemoryChannel] = None
        if self._cat.is_connected():
            try:
                mem = FT991CAT(self._cat).read_memory_channel_tag(ch)
            except CatError:
                mem = None

        self.memory_combo.blockSignals(True)
        try:
            if mem is not None:
                self._upsert_memory_combo_channel(mem)
                idx = self._memory_combo_index_for_channel(ch)
                if idx >= 0:
                    self.memory_combo.setCurrentIndex(idx)
            else:
                label = tr("main.memory.channel_active", channel=ch)
                insert_at = self._memory_combo_insert_index_for_channel(ch)
                self.memory_combo.insertItem(insert_at, label, ch)
                idx = self._memory_combo_index_for_channel(ch)
                if idx >= 0:
                    self.memory_combo.setCurrentIndex(idx)
        finally:
            self.memory_combo.blockSignals(False)

        if reset_favorites_placeholder:
            self._reset_favorites_combo_to_placeholder()
        self._apply_local_memory_overrides(ch)

    def _sync_memory_combo_from_radio(
        self, *, preferred_memory_after_restore: Optional[int] = None
    ) -> None:
        """Liest ``MC;`` + ``FA`` und stellt die Combo auf VFO bzw. aktiven Kanal.

        ``preferred_memory_after_restore``: direkt nach :meth:`_finish_connect_init`
        programmatisch ``MCnnn`` gesetzt — manche Funkgerät-/Poller-Kombinationen
        liefern ``MC;`` dann kurzzeitig leer oder ``FA`` weicht noch um einen Tick ab.
        Wenn gesetzt, wird die Combo zuverlässig auf diesen Kanal gestellt und die
        heuristische Zuordnung übersprungen.
        """
        if not self._cat.is_connected():
            return
        self._normalize_memory_combo_vfo_label()
        hinted = preferred_memory_after_restore
        if hinted is not None and int(hinted) > 0:
            self._select_memory_combo_by_channel(
                int(hinted), reset_favorites_placeholder=True
            )
            return
        ft = FT991CAT(self._cat)
        active: Optional[int]
        try:
            active = ft.read_active_memory_channel()
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        except CatError:
            active = None
        fa = 0
        try:
            fr = ft.read_frequency()
            if fr is not None and fr > 0:
                fa = int(fr)
        except CatError:
            pass
        effective = _restore_memory_channel_if_fa_matches_slot(ft, active, fa)
        self.memory_combo.blockSignals(True)
        try:
            if effective is None:
                vfo_idx = self.memory_combo.findData(self._VFO_ITEM_DATA)
                if vfo_idx >= 0:
                    self.memory_combo.setCurrentIndex(vfo_idx)
            else:
                self._select_memory_combo_by_channel(effective)
        finally:
            self.memory_combo.blockSignals(False)

    def _on_memory_channel_loaded(self, channel: object) -> None:
        """Wird vom Loader pro gefundenem Speicherkanal aufgerufen."""
        if not isinstance(channel, MemoryChannel):
            return
        self.memory_combo.blockSignals(True)
        try:
            self._upsert_memory_combo_channel(channel)
        finally:
            self.memory_combo.blockSignals(False)

    def _finalize_memory_load_ui(
        self, *, occupied_count: int, from_cache: bool
    ) -> None:
        self.memory_combo.setEnabled(self._cat.is_connected())
        self.band_combo.setEnabled(self._cat.is_connected())
        self._refresh_header_status(
            connected=self._cat.is_connected(),
            info=self._last_identity_info,
        )
        if self._cat.is_connected():
            self.meter_widget.resume_polling()
        sb = self.statusBar()
        if sb is not None and self._cat.is_connected():
            if from_cache:
                sb.showMessage(
                    tr("status.memory_from_cache", count=occupied_count),
                    4000,
                )
            else:
                sb.showMessage(
                    tr("status.memory_loaded", count=occupied_count),
                    4000,
                )
        self._connect_init_step_done("memory")

    def _apply_memory_dropdown_from_cache(self) -> None:
        """Befüllt die Memory-Combo aus ``memory_combo_cache.json`` (nach erstem Scan)."""
        if not self._cat.is_connected():
            self._connect_init_step_done("memory")
            return
        if (
            not self._settings.ui.memory_dropdown_scan_completed
            or not memory_combo_cache_path().is_file()
        ):
            self._start_memory_load()
            return
        rows_opt = load_memory_combo_cache()
        if rows_opt is None:
            self._settings.ui.memory_dropdown_scan_completed = False
            try:
                self._persist_settings()
            except OSError:
                pass
            self._start_memory_load()
            return
        rows = rows_opt
        self.meter_widget.pause_polling()
        self.memory_combo.blockSignals(True)
        try:
            self._memory_combo_catalog.clear()
            self._reset_memory_combo(placeholder=tr("main.memory.vfo_loading"))
            self.memory_combo.setEnabled(False)
            self.band_combo.setEnabled(False)
            self._connection_footer_label.setText(tr("status.memory_cache_header"))
            self._refresh_header_status(
                connected=True,
                info=self._last_identity_info,
            )
            for m in sorted(rows, key=lambda x: int(x.channel)):
                self._upsert_memory_combo_channel(m)
        finally:
            self.memory_combo.blockSignals(False)
        self._finalize_memory_load_ui(
            occupied_count=len(rows), from_cache=True
        )

    def _sync_memory_dropdown_from_editor_bank(self, bank: MemoryChannelBank) -> None:
        """Nach Editor-Lesen/Schreiben: Dropdown wie die lokale Kanaliste."""
        if not self._cat.is_connected():
            return
        from_editor = memory_channels_from_editor_bank(bank)
        merged: dict[int, MemoryChannel] = {}
        for mc in list(self._memory_combo_catalog.values()):
            if int(mc.channel) > MEMORY_EDITOR_MAX:
                merged[int(mc.channel)] = mc
        for mc in from_editor:
            merged[int(mc.channel)] = mc
        ordered = sorted(merged.values(), key=lambda m: int(m.channel))
        prev = self.memory_combo.currentData()

        self.memory_combo.blockSignals(True)
        try:
            self._memory_combo_catalog.clear()
            self._reset_memory_combo()
            self._normalize_memory_combo_vfo_label()
            for m in ordered:
                self._upsert_memory_combo_channel(m)
            if isinstance(prev, int) and int(prev) > 0:
                idx = self._memory_combo_index_for_channel(int(prev))
                if idx >= 0:
                    self.memory_combo.setCurrentIndex(idx)
        finally:
            self.memory_combo.blockSignals(False)
        try:
            save_memory_combo_cache(ordered)
            self._settings.ui.memory_dropdown_scan_completed = True
            self._persist_settings()
        except OSError:
            pass

    def _on_memory_load_progress(self, current: int, total: int) -> None:
        if self._cat.is_connected():
            self._connection_footer_label.setText(
                tr("status.memory_loading", current=current, total=total)
            )

    def _on_memory_load_finished(self, found: int) -> None:
        try:
            rows = sorted(
                self._memory_combo_catalog.values(),
                key=lambda m: int(m.channel),
            )
            save_memory_combo_cache(rows)
            self._settings.ui.memory_dropdown_scan_completed = True
            self._persist_settings()
        except OSError:
            pass
        self._finalize_memory_load_ui(occupied_count=found, from_cache=False)

    def _on_memory_load_failed(self, message: object) -> None:
        self.memory_combo.setEnabled(self._cat.is_connected())
        self.band_combo.setEnabled(self._cat.is_connected())
        if self._cat.is_connected():
            self._connection_footer_label.setText(
                tr("status.memory_load_failed", message=str(message))
            )
            self.meter_widget.resume_polling()
        self._finalize_memory_load_ui(occupied_count=0, from_cache=False)

    def _on_memory_combo_activated(self, index: int) -> None:
        """User hat einen Eintrag im Memory-Dropdown gewählt."""
        if not self._cat.is_connected():
            return
        self._reset_favorites_combo_to_placeholder()
        data = self.memory_combo.itemData(index)
        ft = FT991CAT(self._cat)
        try:
            if data == self._VFO_ITEM_DATA:
                ft.switch_to_vfo_mode()
                self._sync_band_combo_to_frequency(self._vfo_a_display_hz)
                self._try_clear_fm_repeater_shift_simplex()
            elif isinstance(data, int):
                ft.select_memory_channel(int(data))
                self._apply_local_memory_overrides(int(data))
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(
                    tr("status.memory_switch_failed", error=str(exc)), 5000
                )

    # ------------------------------------------------------------------
    # Favoriten (Soll-Vorgaben)
    # ------------------------------------------------------------------

    def _reset_favorites_combo_to_placeholder(self) -> None:
        """Zeile „Favoriten“ wählen (kein konkreter Eintrag aktiv)."""
        if self._favorites_panel.combo.count() <= 0:
            return
        self._favorites_panel.combo.blockSignals(True)
        self._favorites_panel.combo.setCurrentIndex(0)
        self._favorites_panel.combo.blockSignals(False)

    def _refresh_favorites_combo(self, *, select_placeholder: bool = True) -> None:
        self._favorites_panel.combo.blockSignals(True)
        self._favorites_panel.combo.clear()
        self._favorites_panel.combo.addItem(tr("favorites.placeholder"), None)
        for i, fav in enumerate(self._favorites_store.favorites):
            self._favorites_panel.combo.addItem(
                format_favorite_combo_label(fav), int(i)
            )
        if select_placeholder:
            self._favorites_panel.combo.setCurrentIndex(0)
        self._favorites_panel.combo.blockSignals(False)

    def _favorites_selected_store_index(self) -> Optional[int]:
        idx = self._favorites_panel.combo.currentIndex()
        if idx < 0:
            return None
        data = self._favorites_panel.combo.itemData(idx)
        if data is None:
            return None
        i = int(data)
        if i < 0 or i >= len(self._favorites_store.favorites):
            return None
        return i

    def _snapshot_favorite_from_radio(self, name: str) -> RadioFavorite:
        ft = FT991CAT(self._cat)
        fq = int(ft.read_frequency())
        mode = ft.read_rx_mode()
        sql = int(ft.read_squelch())
        ag = int(ft.read_af_gain())
        rg = int(ft.read_rf_gain())
        pc = int(ft.read_pc_power_watts())
        eq_name = self.profile_widget.profile_combo.currentText().strip()
        return RadioFavorite(
            name=RadioFavorite.validate_name(name),
            frequency_hz=fq,
            mode=mode.value,
            eq_profile_name=eq_name,
            squelch=sql,
            af_gain=ag,
            rf_gain=rg,
            pc_power_watts=pc,
        )

    def _on_favorite_save_clicked(self) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                tr("favorites.title"),
                tr("favorites.connect_first"),
            )
            return
        sel = self._favorites_selected_store_index()
        replace_idx: Optional[int] = None
        new_name: Optional[str] = None
        if sel is not None:
            box = QMessageBox(self)
            box.setWindowTitle(tr("favorites.save.title"))
            box.setText(tr("favorites.save.prompt"))
            btn_over = box.addButton(
                tr("dialog.overwrite"), QMessageBox.ButtonRole.AcceptRole
            )
            btn_new = box.addButton(
                tr("dialog.create_new"), QMessageBox.ButtonRole.ActionRole
            )
            btn_cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is None or clicked == btn_cancel:
                return
            if clicked == btn_over:
                replace_idx = sel
            else:
                text, ok = QInputDialog.getText(
                    self,
                    tr("favorites.new.title"),
                    tr("favorites.new.name_label"),
                )
                if not ok:
                    return
                new_name = text
        else:
            text, ok = QInputDialog.getText(
                self,
                tr("favorites.new.title"),
                tr("favorites.new.name_label"),
            )
            if not ok:
                return
            new_name = text
        try:
            if replace_idx is not None:
                nm = self._favorites_store.favorites[replace_idx].name
                snap = self._snapshot_favorite_from_radio(nm)
                self._favorites_store.upsert(snap, replace_index=replace_idx)
            else:
                assert new_name is not None
                snap = self._snapshot_favorite_from_radio(new_name)
                self._favorites_store.upsert(snap)
            self._favorites_store.save()
        except (ValueError, CatError) as exc:
            if isinstance(exc, CatConnectionLostError):
                self._on_connection_lost()
                return
            QMessageBox.warning(self, tr("favorites.title"), str(exc))
            return
        self._refresh_favorites_combo()
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(tr("favorites.saved"), 4000)

    def _on_favorite_delete_clicked(self) -> None:
        sel = self._favorites_selected_store_index()
        if sel is None:
            QMessageBox.information(
                self,
                tr("favorites.title"),
                tr("favorites.select_first"),
            )
            return
        fav = self._favorites_store.favorites[sel]
        if (
            QMessageBox.question(
                self,
                tr("favorites.delete.title"),
                tr("favorites.delete.confirm", name=fav.name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._favorites_store.remove_at(sel)
            self._favorites_store.save()
        except (IndexError, OSError) as exc:
            QMessageBox.warning(self, tr("favorites.title"), str(exc))
            return
        self._refresh_favorites_combo()

    def _on_favorite_edit_clicked(self) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                tr("favorites.title"),
                tr("favorites.connect_first"),
            )
            return
        sel = self._favorites_selected_store_index()
        if sel is None:
            QMessageBox.information(
                self,
                tr("favorites.title"),
                tr("favorites.select_first"),
            )
            return
        fav = self._favorites_store.favorites[sel]
        try:
            snap = self._snapshot_favorite_from_radio(fav.name)
            self._favorites_store.upsert(snap, replace_index=sel)
            self._favorites_store.save()
        except (ValueError, CatError) as exc:
            if isinstance(exc, CatConnectionLostError):
                self._on_connection_lost()
                return
            QMessageBox.warning(self, tr("favorites.title"), str(exc))
            return
        self._refresh_favorites_combo(select_placeholder=False)
        qi = self._favorites_panel.combo.findData(sel)
        self._favorites_panel.combo.blockSignals(True)
        self._favorites_panel.combo.setCurrentIndex(qi if qi >= 0 else 0)
        self._favorites_panel.combo.blockSignals(False)
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(tr("favorites.updated", name=fav.name), 4000)

    def _on_favorite_combo_activated(self, index: int) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                tr("favorites.title"),
                tr("favorites.connect_first"),
            )
            return
        if index < 0:
            return
        data = self._favorites_panel.combo.itemData(index)
        if data is None:
            return
        store_i = int(data)
        if store_i < 0 or store_i >= len(self._favorites_store.favorites):
            return
        self._apply_favorite(
            self._favorites_store.favorites[store_i],
            favorite_store_index=store_i,
        )

    def _apply_favorite(
        self,
        fav: RadioFavorite,
        *,
        favorite_store_index: Optional[int] = None,
    ) -> None:
        if not self._cat.is_connected():
            return
        ft = FT991CAT(self._cat)
        try:
            if not ft.switch_to_vfo_mode():
                raise CatError(tr("error.vfo_mode_failed"))
            mode = rx_mode_from_selection(fav.mode, default=RxMode.USB)
            ft.set_rx_mode(mode)
            if fav.frequency_hz > 0:
                ft.write_frequency(fav.frequency_hz)
                self._notify_meter_app_frequency_write(fav.frequency_hz)
            ft.write_squelch(max(0, min(100, fav.squelch)))
            ft.write_af_gain(max(0, min(255, fav.af_gain)))
            ft.write_rf_gain(max(0, min(255, fav.rf_gain)))
            if fav.pc_power_watts > 0:
                ft.set_pc_power_watts(fav.pc_power_watts)
        except CatError as exc:
            if isinstance(exc, CatConnectionLostError):
                self._on_connection_lost()
                return
            QMessageBox.warning(
                self, tr("favorites.title_singular"), tr("vfo.error.message", error=str(exc))
            )
            return
        self._try_clear_fm_repeater_shift_simplex()
        self._apply_vfo_a_display_hz(fav.frequency_hz)
        self._sync_band_combo_to_frequency(fav.frequency_hz)
        self._status_mode_display = mode.value
        self._mode_label.setText(_status_bar_mode_text(mode.value))
        self.profile_widget.notify_radio_mode(mode)
        eq = fav.eq_profile_name.strip()
        if eq:
            if not self.profile_widget.select_profile_by_name(eq):
                QMessageBox.information(
                    self,
                    tr("favorites.title_singular"),
                    tr("favorites.eq_not_found", profile=eq),
                )
        self._select_memory_combo_vfo(reset_favorites_placeholder=False)
        if favorite_store_index is not None:
            ri = self._favorites_panel.combo.findData(int(favorite_store_index))
            self._favorites_panel.combo.blockSignals(True)
            self._favorites_panel.combo.setCurrentIndex(ri if ri >= 0 else 0)
            self._favorites_panel.combo.blockSignals(False)
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(tr("favorites.applied", name=fav.name), 4000)

    def _start_memory_load(self) -> None:
        """Stößt den Hintergrund-Loader an. Idempotent — laufende Loads
        werden vom Loader selbst sauber gestoppt.

        Pausiert den :class:`MeterPoller`, damit der serielle Port
        ungeteilt dem Loader zur Verfuegung steht. ``_on_memory_load_*``
        setzt das Polling am Ende wieder fort.
        """
        if not self._cat.is_connected():
            return
        self._memory_combo_catalog.clear()
        self._reset_memory_combo(placeholder=tr("main.memory.vfo_loading_channels"))
        self.memory_combo.setEnabled(False)
        self.band_combo.setEnabled(False)
        # MeterPoller stilllegen — der MT-Burst klemmt sonst minutenlang
        # zwischen Live-Polls. Resume passiert nach ``finished``/``failed``.
        self.meter_widget.pause_polling()
        self._memory_loader.start()

    # ------------------------------------------------------------------
    # Einstellungs-Dialog
    # ------------------------------------------------------------------

    def _ensure_equalizer_window(self) -> EqualizerWindow:
        if self._equalizer_window is None:
            self._equalizer_window = EqualizerWindow(
                self.profile_widget,
                parent=self,
            )
            self._equalizer_window.closed.connect(self._on_equalizer_window_closed)
        return self._equalizer_window

    def _on_equalizer_action(self) -> None:
        win = self._ensure_equalizer_window()
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_equalizer_window_closed(self) -> None:
        pass

    def _ensure_live_window(self, progress=None):  # type: ignore[no-untyped-def]
        """Live-DSP Fenster lazy laden (heavy deps: scipy/sounddevice)."""
        win = self._live_window
        if win is not None:
            return win
        if progress is not None:
            progress.bump(8)
        from gui.live_window import LiveWindow as _LiveWin

        if progress is not None:
            progress.bump(18)
            QApplication.processEvents()
        w = _LiveWin(
            self._settings,
            persist_settings=self._persist_settings,
            open_sound_settings=self._on_sound_settings_action,
            serial_cat=self._cat,
            audio_radio_session=self._audio_radio_session,
            operating_mode_provider=self._main_operating_mode,
            other_audio_blocking=self._live_transmit_blocked_by_other_windows,
            request_cat_tx_poll=self.meter_widget.request_immediate_poll,
            profile_widget=self.profile_widget,
        )
        if progress is not None:
            progress.bump(75)
            QApplication.processEvents()
        if not self._live_tx_meter_bridge:
            h = getattr(w, "handle_tx_state_changed", None)
            if callable(h):
                self.meter_widget.tx_state_changed.connect(h)
                self._live_tx_meter_bridge = True
            led_sync = getattr(w, "_update_tx_rx_led", None)
            if callable(led_sync):
                last = getattr(self.meter_widget, "_last_tx_state", None)
                if last is not None:
                    led_sync(int(last))
        if progress is not None:
            progress.bump(88)
        self._live_window = w
        return w

    def _on_live_action(self) -> None:
        loading = None
        win = self._live_window
        try:
            if win is None:
                from gui.animated_wait_dialog import AnimatedWaitDialog

                loading = AnimatedWaitDialog(
                    tr("live.loading.message"),
                    tr("live.loading.title"),
                    parent=self,
                )
                loading.start()

            win = self._ensure_live_window(progress=loading)
            rf = getattr(win, "reload_from_app_settings", None)
            if callable(rf):
                rf()
            if loading is not None:
                loading.bump(92)
            led_sync = getattr(win, "_update_tx_rx_led", None)
            if callable(led_sync):
                last = getattr(self.meter_widget, "_last_tx_state", None)
                if last is not None:
                    led_sync(int(last))
        finally:
            if loading is not None:
                loading.finish()
                loading.close()

        if win is not None:
            win.show()
            win.raise_()
            win.activateWindow()

    def _ensure_sound_settings_window(self) -> SoundSettingsWindow:
        if self._sound_settings_window is None:
            self._sound_settings_window = SoundSettingsWindow(
                self._settings,
                self._audio_hub,
            )
            self._sound_settings_window.closed.connect(
                self._on_sound_settings_window_closed
            )
            self._sound_settings_window.live_devices_changed.connect(
                self._on_sound_settings_live_devices_changed
            )
        return self._sound_settings_window

    def _on_sound_settings_live_devices_changed(self) -> None:
        win = self._live_window
        if win is None:
            return
        apply_fn = getattr(win, "apply_devices_from_settings", None)
        if callable(apply_fn):
            apply_fn()

    def _on_sound_settings_action(self) -> None:
        win = self._ensure_sound_settings_window()
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_sound_settings_window_closed(self) -> None:
        self._persist_settings()
        for win in (self._audio_player_window, self._audio_recorder_window):
            if win is None:
                continue
            reload_fn = getattr(win, "reload_routing_from_settings", None)
            if callable(reload_fn):
                reload_fn()

    def _ensure_audio_player_window(self) -> AudioPlayerWindow:
        if self._audio_player_window is None:
            self._audio_player_window = AudioPlayerWindow(
                self._settings,
                self._cat,
                audio_radio_session=self._audio_radio_session,
                operating_mode_provider=self._main_operating_mode,
                audio_hub=self._audio_hub,
                open_sound_settings=self._on_sound_settings_action,
            )
            self._audio_player_window.closed.connect(
                self._on_audio_player_window_closed
            )
            # MIC-PTT (TX-State 2) unterbricht laufende Audio-Wiedergabe.
            self.meter_widget.tx_state_changed.connect(
                self._audio_player_window.handle_tx_state_changed
            )
        return self._audio_player_window

    def _on_audio_player_action(self) -> None:
        win = self._ensure_audio_player_window()
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_audio_player_window_closed(self) -> None:
        if self._audio_player_window is not None:
            self._audio_player_window.persist_settings()
            self._persist_settings()

    def _ensure_audio_recorder_window(self) -> AudioRecorderWindow:
        if self._audio_recorder_window is None:
            self._audio_recorder_window = AudioRecorderWindow(
                self._settings,
                self._cat,
                audio_radio_session=self._audio_radio_session,
                operating_mode_provider=self._main_operating_mode,
                audio_hub=self._audio_hub,
                open_sound_settings=self._on_sound_settings_action,
            )
            self._audio_recorder_window.closed.connect(
                self._on_audio_recorder_window_closed
            )
            # MIC-PTT bricht Aufnahme/Replay ab (analog Audio-Player).
            self.meter_widget.tx_state_changed.connect(
                self._audio_recorder_window.handle_tx_state_changed
            )
        return self._audio_recorder_window

    def _on_audio_recorder_action(self) -> None:
        win = self._ensure_audio_recorder_window()
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_audio_recorder_window_closed(self) -> None:
        if self._audio_recorder_window is not None:
            self._audio_recorder_window.persist_settings()
            self._persist_settings()

    def _on_memory_editor_action(self) -> None:
        if not self._cat.is_connected():
            QMessageBox.information(
                self,
                tr("memory_editor.not_connected.title"),
                tr("memory_editor.not_connected.message"),
            )
            return
        editor = self._memory_editor
        if editor is not None and editor.isVisible():
            editor.raise_()
            editor.activateWindow()
            return
        # Pausieren statt stoppen — Thread bleibt, Anzeige wird nach Schließen
        # zuverlässig mit ensure_polling() fortgesetzt.
        self.meter_widget.pause_polling()
        self._memory_editor = open_memory_editor(
            self._cat,
            profile_widget=self.profile_widget,
            app_settings=self._settings,
            persist_settings=self._persist_settings,
            apply_local_memory_overrides=self._apply_local_memory_overrides,
            sync_main_memory_dropdown=self._sync_memory_dropdown_from_editor_bank,
            on_closed=self._on_memory_editor_closed,
        )

    def _on_memory_editor_closed(self, *_args: object) -> None:
        self._memory_editor = None
        self.profile_widget.set_cat_blocked(False)
        self.meter_widget.ensure_polling()

    def _on_po_calibration_busy(self, busy: bool) -> None:
        if busy:
            self.meter_widget.pause_polling()
            self.profile_widget.set_cat_blocked(True)
        else:
            self.profile_widget.set_cat_blocked(False)
            self.meter_widget.ensure_polling()

    def _on_settings_action(self) -> None:
        dialog = ConnectionSettingsDialog(
            self._settings,
            self._cat,
            get_rig_bridge=lambda: self._rig_bridge,
            parent=self,
        )
        dialog.settings_changed.connect(self._persist_settings)
        dialog.po_calibration_applied.connect(
            self.meter_widget.refresh_po_calibration
        )
        dialog.po_calibration_busy.connect(self._on_po_calibration_busy)
        dialog.exec()
        self.profile_widget.set_cat_blocked(False)
        self.meter_widget.ensure_polling()
        # Nach dem Schließen die Anzeige in der Statusleiste aktualisieren
        # (Port/Baud können sich geändert haben). ID-Info bleibt, falls noch
        # verbunden.
        self._refresh_header_status(
            connected=self._cat.is_connected(),
            info=self._last_identity_info,
        )

    # ------------------------------------------------------------------
    # Log-Fenster
    # ------------------------------------------------------------------

    def _ensure_log_window(self) -> LogWindow:
        if self._log_window is None:
            self._log_window = LogWindow(self._cat_log)
            self._log_window.closed.connect(self._on_log_window_closed)
            self._log_window.restore_geometry_from_base64(
                self._settings.ui.log_window_geometry
            )
            self._log_window.set_dark_mode(True)
        return self._log_window

    def _show_log_window(self) -> None:
        win = self._ensure_log_window()
        win.show()
        win.raise_()

    def _on_log_toggle(self, checked: bool) -> None:
        if checked:
            self._show_log_window()
            self._settings.ui.show_cat_log = True
        else:
            if self._log_window is not None and self._log_window.isVisible():
                # Geometrie sichern, bevor wir es ausblenden.
                self._settings.ui.log_window_geometry = (
                    self._log_window.geometry_to_base64()
                )
                self._log_window.hide()
            self._settings.ui.show_cat_log = False
        self._persist_settings()

    def _on_log_window_closed(self) -> None:
        """Der User hat das Log-Fenster über das X geschlossen — Menüstatus
        synchronisieren, Geometrie sichern."""
        if self._log_window is not None:
            self._settings.ui.log_window_geometry = (
                self._log_window.geometry_to_base64()
            )
        self._settings.ui.show_cat_log = False
        self.log_toggle_action.blockSignals(True)
        try:
            self.log_toggle_action.setChecked(False)
        finally:
            self.log_toggle_action.blockSignals(False)
        self._persist_settings()

    # ------------------------------------------------------------------
    # Über
    # ------------------------------------------------------------------

    def _show_about(self) -> None:
        AboutWindow(self).exec()

    def _on_open_user_manual(self) -> None:
        """Hilfe -> Anleitung — Handbuch-PDF der installierten Version im Browser."""
        lang = current_language()
        if lang not in ("de", "en"):
            lang = "de"
        url = manual_pdf_download_url(APP_VERSION, lang)
        QDesktopServices.openUrl(QUrl(url))

    def _on_check_for_updates(self) -> None:
        """Hilfe → Update prüfen — neuestes Release per GitHub-API."""
        t = self._update_check_thread
        if t is not None and t.isRunning():
            return
        thread = UpdateCheckThread(self)
        self._update_check_thread = thread
        thread.outcome.connect(self._on_update_check_outcome)

        def _clear_thread_ref() -> None:
            if self._update_check_thread is thread:
                self._update_check_thread = None

        thread.finished.connect(_clear_thread_ref)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_update_check_outcome(self, outcome: object) -> None:
        if not isinstance(outcome, UpdateCheckOutcome):
            return
        o = outcome
        if not o.ok:
            QMessageBox.warning(
                self,
                tr("update.dialog.check.title"),
                tr(
                    "update.dialog.check.failed",
                    current=o.current,
                    error=o.error_message,
                ),
            )
            return
        if o.update_available:
            box = QMessageBox(self)
            box.setWindowTitle(tr("update.dialog.available.title"))
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(
                tr(
                    "update.dialog.available.text",
                    current=o.current,
                    latest=o.latest,
                )
            )
            box.setInformativeText(tr("update.dialog.available.info"))
            open_btn = box.addButton(
                tr("update.dialog.available.open"), QMessageBox.ButtonRole.AcceptRole
            )
            close_btn = box.addButton(tr("dialog.close"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(close_btn)
            box.exec()
            if box.clickedButton() == open_btn:
                QDesktopServices.openUrl(QUrl(o.release_url))
        else:
            QMessageBox.information(
                self,
                tr("update.dialog.check.title"),
                tr(
                    "update.dialog.current.text",
                    current=o.current,
                    latest=o.latest,
                ),
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _on_active_profile_changed(self, name: str) -> None:
        self._settings.ui.last_profile = name
        self._persist_settings()

    def _persist_settings(self) -> None:
        name = self.profile_widget.current_profile_name()
        if name:
            self._settings.ui.last_profile = name
        # Polling-Intervalle live ans Meter-Widget durchreichen, damit eine
        # Änderung im Settings-Dialog sofort greift — auch wenn gerade
        # gepollt wird.
        self.meter_widget.set_intervals(
            self._settings.polling.tx_interval_ms,
            self._settings.polling.rx_interval_ms,
        )
        self.meter_widget.set_tx_poll_settings(self._settings.polling.tx_poll)
        # Sichtbarkeit der "Erweiterte Einstellungen"-Sektion synchron halten.
        self.profile_widget.set_hide_extended_in_ssb(
            self._settings.ui.hide_extended_in_ssb
        )
        self._refresh_global_hotkeys()
        lw = self._live_window
        if lw is not None:
            refresh = getattr(lw, "refresh_keyboard_shortcuts_from_settings", None)
            if callable(refresh):
                refresh()
        apply_smeter_calibration_from_settings(self._settings.smeter_calibration)
        smeter_set_calibration_frequency_hz(
            int(getattr(self.meter_widget, "_last_vfo_a_hz", 0) or 0)
        )
        self.meter_widget.refresh_smeter_scale_ticks()
        self._audio_radio_session.reload_data_mode_from_settings()
        try:
            self._settings.save()
        except OSError:
            pass

    def shutdown_background_services(self) -> None:
        """Hintergrund-Threads/Timer vor App-Ende sauber stoppen."""
        try:
            self._audio_hub.stop_polling()
        except Exception:
            pass
        try:
            self._t_call.shutdown()
        except Exception:
            pass

    def _shutdown_auxiliary_windows(self) -> None:
        """Alle Hilfsfenster endgültig schließen (nicht nur verstecken)."""
        if self._log_window is not None:
            self._settings.ui.log_window_geometry = (
                self._log_window.geometry_to_base64()
            )
            self._log_window.panel.deactivate()
            self._log_window.close()
            self._log_window = None
        if self._equalizer_window is not None:
            self._equalizer_window.force_close()
            self._equalizer_window = None
        if self._audio_player_window is not None:
            self._audio_player_window.force_close()
            self._audio_player_window = None
        if self._live_window is not None:
            lw = getattr(self._live_window, "force_close", None)
            if callable(lw):
                lw()
            try:
                self._live_window.close()
            except Exception:
                pass
            self._live_window = None
            self._live_tx_meter_bridge = False
        if self._audio_recorder_window is not None:
            self._audio_recorder_window.force_close()
            self._audio_recorder_window = None
        if self._memory_editor is not None:
            self._memory_editor.close()
            self._memory_editor = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._application_shutting_down = True
        hc = getattr(self, "_global_hotkey_controller", None)
        if hc is not None:
            try:
                hc.unregister_all()
            except Exception:
                pass
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._reconnect_timer.stop()
        self._rig_bridge_ui_timer.stop()
        try:
            self._rig_bridge.on_app_disconnected()
        except Exception:
            pass
        try:
            self.meter_widget.stop_polling()
        finally:
            try:
                self.shutdown_background_services()
            except Exception:
                pass
            try:
                self._cat.disconnect()
            finally:
                self._shutdown_auxiliary_windows()
                self._audio_radio_session.shutdown()
                self._persist_settings()
                super().closeEvent(event)
        if app is not None:
            app.quit()

    # ------------------------------------------------------------------
    # showEvent — Header-Status initial setzen
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Beim ersten Anzeigen Statusleiste in Sync mit den Settings bringen.
        self._refresh_header_status(
            connected=self._cat.is_connected(),
            info=self._last_identity_info,
        )
        # Einmal-Initialisierung: Auto-Connect, falls aktiv und Port da.
        if not getattr(self, "_auto_connect_attempted", False):
            self._auto_connect_attempted = True
            if (
                self._settings.cat.auto_connect
                and self._settings.cat.port
                and not self._cat.is_connected()
            ):
                # Kurzer Delay, damit das Fenster erst sauber gerendert ist.
                # Bei Fehlschlag startet der Watcher und versucht es weiter.
                QTimer.singleShot(150, self._auto_connect_on_startup)

    def _auto_connect_on_startup(self) -> None:
        if self._do_connect(interactive=False):
            return
        # Port (noch) nicht da -> Watcher übernimmt im Hintergrund.
        if not self._reconnect_timer.isActive():
            self._reconnect_timer.start()
        self._last_identity_info = tr("status.waiting_for_port")
        self._refresh_header_status(connected=False, info=self._last_identity_info)
