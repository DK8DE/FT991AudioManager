"""Hauptfenster des FT-991/A Audiomanagers.

Neuer schlanker Aufbau (ab 0.5.1):

- Oben **rechts**: VFO-A/B und RX/TX-Anzeige; darunter ein **großer
  Meter-Bereich** (S-Meter + DSP links, AF/RF + TX-Meter rechts);
  darunter **Minus / Tune / REV** und Audio-Buttons; unten **Mode-Gruppe**,
  **EQ-Profil**, **Speicherkanal** und **Band**; darunter ein eigener Bereich
  **Favoriten** (persistente Soll-Vorgaben).
- **EQ-Profil- und Mode-Auswahl** bleiben im Hauptfenster; der Equalizer-Editor
  (Grundwerte, EQ, Erweitert, Speichern) liegt in **Bearbeiten → Equalizer**.
- Verbindung: **Datei → Verbinden** / **Datei → Trennen**.
- Die Verbindungs-Konfiguration liegt unter **Datei → Einstellungen**.
- Speicherkanäle unter **Bearbeiten → Speicherkanäle**.
- Das CAT-Log liegt unter **Ansicht → CAT-Log anzeigen** (eigenes Fenster).
- **Hilfe → Update prüfen**: Abgleich mit dem neuesten Release auf GitHub.
"""

from __future__ import annotations

import time
from typing import Optional, cast

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
)
from mapping.memory_mapping import MemoryChannel
from mapping.rx_mapping import RxMode, coarse_mode_group_for, rx_mode_from_selection
from audio.audio_settings_hub import AudioSettingsHub
from audio.t_call_controller import TCallController
from model import AppSettings, PresetStore
from model.favorites_store import (
    FavoritesStore,
    RadioFavorite,
    format_favorite_combo_label,
)
from rig_bridge import RigBridgeManager

from version import APP_NAME, APP_VERSION

from .about_window import AboutWindow
from .app_icon import app_icon
from .menu_icons import menu_action_icon, menu_speaker_white_icon
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
from .theme import apply_theme
from .update_check import UpdateCheckOutcome, UpdateCheckThread
from mapping.amateur_bands import (
    amateur_band_at_hz,
    amateur_band_for_hz,
    combo_entries_high_to_low,
    VFO_BAND_CHOICE,
)
from mapping.repeater_offset import SHIFT_MINUS
from mapping.meter_mapping import (
    apply_smeter_calibration_from_settings,
    smeter_set_calibration_frequency_hz,
)

from .amateur_band_strip import AmateurBandStripWidget
from .vfo_triplet_widget import VfoTripletWidget

_VFO_CAPTION_STYLE_IDLE = "color: #888888; font-weight: bold;"
_VFO_CAPTION_STYLE_IN_BAND = "color: #5ddc7a; font-weight: bold;"
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
    return f"Mode: {mode_value} "


def _status_bar_tx_text(transmitting: bool) -> str:
    return "TX: AN " if transmitting else "TX: aus "


class MainWindow(QMainWindow):
    """Hauptfenster mit VFO-Zeile, großem Meter-Panel und EQ-Profilzeile."""

    #: Start- und Mindestgröße des Hauptfensters (logische Pixel).
    MAIN_START_WIDTH = 800
    MAIN_START_HEIGHT = 680

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
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
        #: MT-Frequenz pro Kanal (Memory-Loader) — Abgleich bei VFO-Drehen mit aktivem MC.
        self._memory_slot_frequency_hz: dict[int, int] = {}
        self._update_check_thread: Optional[UpdateCheckThread] = None

        self._build_ui()
        self._t_call = TCallController(self._audio_hub, parent=self)
        self._t_call.error.connect(self._on_t_call_error)
        self._t_call.active_changed.connect(self._on_t_call_active_changed)
        _tcall_worker = self._audio_radio_session.worker
        _tcall_worker.apply_finished.connect(self._on_tcall_radio_apply_finished)
        _tcall_worker.engage_data_finished.connect(
            self._on_tcall_radio_engage_data_finished
        )
        self._build_menu()

        # Statusleiste: links Verbindung + Speicherkanal-Laden, rechts Mode/TX.
        self._connection_footer_label = QLabel("Nicht verbunden")
        self._connection_footer_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        sb = QStatusBar()
        sb.addWidget(self._connection_footer_label, 1)
        self._tx_label = QLabel(_status_bar_tx_text(False))
        self._mode_label = QLabel(_status_bar_mode_text("—"))
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
        app = QApplication.instance()
        if app is None:
            return super().eventFilter(_watched, event)
        fw = app.focusWidget()
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

    def _on_main_operating_mode_changed(self, _text: str) -> None:
        """DSP-Anzeige + Audio-Player/Recorder-DATA-Modus anpassen."""
        self._sync_meter_dsp_mode_visibility()
        mode = self._main_operating_mode()
        player = self._audio_player_window
        if player is not None and player.isVisible():
            player.sync_data_mode_from_main(mode)
        recorder = self._audio_recorder_window
        if recorder is not None and recorder.isVisible():
            recorder.sync_data_mode_from_main(mode)

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

        self._vfo_a_caption = QLabel("VFO-A:")
        self._vfo_a_caption.setFont(vfo_caption_font)
        self._vfo_a_caption.setStyleSheet(_VFO_CAPTION_STYLE_IN_BAND)
        _vfo_digits_color = (
            _VFO_TRIPLET_FREQ_COLOR_DARK
            if self._settings.ui.force_dark_mode
            else None
        )
        self._vfo_a_triplet = VfoTripletWidget(
            text_color=_vfo_digits_color,
            digit_font=QFont(vfo_caption_font),
        )
        self._vfo_a_triplet.user_frequency_changed.connect(
            self._on_user_vfo_a_frequency
        )

        self._vfo_b_caption = QLabel("VFO-B:")
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

        self._vfo_ab_button = QPushButton("A/B")
        self._vfo_ab_button.setEnabled(False)
        self._vfo_ab_button.setToolTip(
            "VFO-A und VFO-B tauschen (CAT SV; — SWAP VFO). "
            "Die Anzeige folgt beim nächsten RX-Update."
        )
        self._vfo_ab_button.clicked.connect(self._on_vfo_ab_clicked)

        self.meter_widget = MeterWidget(
            self._cat,
            tx_interval_ms=self._settings.polling.tx_interval_ms,
            rx_interval_ms=self._settings.polling.rx_interval_ms,
            integrated_main_layout=True,
        )

        # ----- Oben rechts: VFO-A/B + RX/TX --------------------------------
        top_bar = QFrame()
        top_bar.setObjectName("panelFrame")
        top_bar.setFrameShape(QFrame.StyledPanel)
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
        band_panel.setFrameShape(QFrame.StyledPanel)
        band_row = QHBoxLayout(band_panel)
        band_row.setContentsMargins(10, 4, 10, 4)
        band_row.setSpacing(10)
        self._band_strip_caption = QLabel("Band:")
        band_caption_font = self.font()
        band_caption_font.setBold(True)
        self._band_strip_caption.setFont(band_caption_font)
        self._band_strip_caption.setStyleSheet(_VFO_CAPTION_STYLE_IDLE)
        self._band_strip_caption.setFixedWidth(52)
        self._band_strip_name = QLabel("—")
        self._band_strip_name.setFont(band_caption_font)
        self._band_strip_name.setStyleSheet(_VFO_CAPTION_STYLE_IDLE)
        self._band_strip_name.setMinimumWidth(48)
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
        self._radio_control_bar.sound_settings_clicked.connect(
            self._on_sound_settings_action
        )
        self.meter_widget.af_gain_set_requested.connect(self._on_af_gain_slider_changed)
        self.meter_widget.rf_gain_set_requested.connect(self._on_rf_gain_slider_changed)
        layout.addWidget(self._radio_control_bar)

        # ----- Unten: Mode + EQ-Profil; Speicherkanal darunter (volle Breite) --
        bottom_bar = QFrame()
        bottom_bar.setObjectName("panelFrame")
        bottom_bar.setFrameShape(QFrame.StyledPanel)
        bottom_outer = QVBoxLayout(bottom_bar)
        bottom_outer.setContentsMargins(8, 6, 8, 6)
        # Gleicher Zeilenabstand wie zwischen den beiden Combo-Zeilen oben.
        bottom_outer.setSpacing(8)

        bottom_row1 = QHBoxLayout()
        bottom_row1.setSpacing(10)

        bottom_row1.addWidget(QLabel("Mode-Gruppe:"))
        self.profile_widget.mode_combo.setParent(bottom_bar)
        bottom_row1.addWidget(self.profile_widget.mode_combo)

        bottom_row1.addSpacing(14)
        bottom_row1.addWidget(QLabel("EQ-Profil:"))
        self.profile_widget.profile_combo.setParent(bottom_bar)
        bottom_row1.addWidget(self.profile_widget.profile_combo, stretch=1)
        bottom_outer.addLayout(bottom_row1)

        bottom_row2 = QHBoxLayout()
        bottom_row2.setSpacing(10)
        bottom_row2.addWidget(QLabel("Speicherkanal:"))
        self.memory_combo = QComboBox(bottom_bar)
        self.memory_combo.setEnabled(False)
        self.memory_combo.setMinimumWidth(260)
        self.memory_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred
        )
        self.memory_combo.setToolTip(
            "Speicherkanäle des FT-991/991A. Wechsel sendet MCnnn; "
            "an das Radio (VFO ⇄ MEM)."
        )
        self._reset_memory_combo()
        self.memory_combo.activated.connect(self._on_memory_combo_activated)
        bottom_row2.addWidget(self.memory_combo, stretch=1)

        bottom_row2.addWidget(QLabel("Band:"))
        self.band_combo = QComboBox(bottom_bar)
        self.band_combo.setEnabled(False)
        self.band_combo.setMinimumWidth(280)
        self.band_combo.setToolTip(
            "VFO-Modus oder Amateurband (Mittenfrequenz auf VFO-A setzen)"
        )
        for label, data in combo_entries_high_to_low():
            self.band_combo.addItem(label, data)
        self.band_combo.activated.connect(self._on_band_combo_activated)
        bottom_row2.addWidget(self.band_combo)

        bottom_outer.addLayout(bottom_row2)

        layout.addWidget(bottom_bar)

        # Eigener panelFrame wie Mode/Speicher — nicht im selben Kasten wie Speicherkanal.
        favorites_bar = QFrame()
        favorites_bar.setObjectName("panelFrame")
        favorites_bar.setFrameShape(QFrame.StyledPanel)
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
        file_menu = menu.addMenu("&Datei")

        settings_action = QAction("&Einstellungen…", self)
        settings_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
                theme_name="preferences-system",
            )
        )
        settings_action.setShortcut("Ctrl+E")
        settings_action.triggered.connect(self._on_settings_action)
        file_menu.addAction(settings_action)

        self._connect_action = QAction("&Verbinden", self)
        self._connect_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_DriveNetIcon,
                theme_name="network-connect",
            )
        )
        self._connect_action.setShortcut("Ctrl+V")
        self._connect_action.triggered.connect(self._on_connect_menu)
        file_menu.addAction(self._connect_action)

        self._disconnect_action = QAction("&Trennen", self)
        self._disconnect_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_BrowserStop,
                theme_name="network-disconnect",
            )
        )
        self._disconnect_action.setShortcut("Ctrl+T")
        self._disconnect_action.triggered.connect(self._on_disconnect_menu)
        file_menu.addAction(self._disconnect_action)

        file_menu.addSeparator()

        quit_action = QAction("&Beenden", self)
        quit_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_TitleBarCloseButton,
                theme_name="application-exit",
            )
        )
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # === Bearbeiten ===============================================
        edit_menu = menu.addMenu("&Bearbeiten")

        memory_action = QAction("&Speicherkanäle…", self)
        memory_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_DirOpenIcon,
                theme_name="folder-open",
            )
        )
        memory_action.setShortcut("Ctrl+K")
        memory_action.triggered.connect(self._on_memory_editor_action)
        edit_menu.addAction(memory_action)

        edit_menu.addSeparator()

        equalizer_action = QAction("&Equalizer…", self)
        equalizer_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_MediaVolume,
                theme_name="audio-volume-high",
            )
        )
        equalizer_action.setShortcut("Ctrl+Shift+E")
        equalizer_action.triggered.connect(self._on_equalizer_action)
        edit_menu.addAction(equalizer_action)

        sound_settings_action = QAction("&Soundeinstellung…", self)
        sound_settings_action.setIcon(menu_speaker_white_icon())
        sound_settings_action.setShortcut("Ctrl+Shift+S")
        sound_settings_action.triggered.connect(self._on_sound_settings_action)
        edit_menu.addAction(sound_settings_action)

        audio_player_action = QAction("&Audio-Player…", self)
        audio_player_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_MediaPlay,
                theme_name="media-playback-start",
            )
        )
        audio_player_action.setShortcut("Ctrl+Shift+A")
        audio_player_action.triggered.connect(self._on_audio_player_action)
        edit_menu.addAction(audio_player_action)

        audio_recorder_action = QAction("Audio-&Recorder…", self)
        audio_recorder_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_FileIcon,
                theme_name="media-record",
            )
        )
        audio_recorder_action.setShortcut("Ctrl+Shift+R")
        audio_recorder_action.triggered.connect(self._on_audio_recorder_action)
        edit_menu.addAction(audio_recorder_action)

        # === Ansicht ==================================================
        view_menu = menu.addMenu("&Ansicht")

        self.log_toggle_action = QAction("CAT-&Log anzeigen", self)
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
        view_menu.addAction(self.log_toggle_action)

        view_menu.addSeparator()

        self.dark_mode_action = QAction("&Dark Mode", self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(self._settings.ui.force_dark_mode)
        self.dark_mode_action.setShortcut("Ctrl+D")
        self.dark_mode_action.toggled.connect(self._on_dark_mode_toggled)
        view_menu.addAction(self.dark_mode_action)

        # === Hilfe ====================================================
        help_menu = menu.addMenu("&Hilfe")
        version_action = QAction("&Version", self)
        version_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_MessageBoxInformation,
                theme_name="help-about",
            )
        )
        version_action.triggered.connect(self._show_about)
        help_menu.addAction(version_action)

        update_check_action = QAction("Update &prüfen…", self)
        update_check_action.setIcon(
            menu_action_icon(
                QStyle.StandardPixmap.SP_BrowserReload,
                theme_name="view-refresh",
            )
        )
        update_check_action.triggered.connect(self._on_check_for_updates)
        help_menu.addAction(update_check_action)

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
                    "Kein Port konfiguriert",
                    (
                        "Es ist noch kein COM-Port ausgewählt.\n\n"
                        "Bitte zuerst über „Datei → Einstellungen…“ einen "
                        "Port auswählen."
                    ),
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
                    "Verbindung fehlgeschlagen",
                    f"Port {port} konnte nicht geöffnet werden:\n\n{exc}",
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
        if not self._last_identity_info.startswith("Verbindung verloren"):
            self._last_identity_info = "Verbindung verloren"
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
            return "keine Antwort vom Gerät"
        except CatError:
            return "CAT-Fehler"

        if identity.is_ft991:
            return f"FT-991/A (ID {identity.radio_id})"
        if identity.radio_id is not None:
            return f"fremde ID {identity.radio_id}"
        return "Antwort unklar"

    def _refresh_header_status(self, *, connected: bool, info: str) -> None:
        """Aktualisiert Verbindungs-/Port-Text in der Statusleiste (links)."""
        self._connect_action.setEnabled(not connected)
        self._disconnect_action.setEnabled(connected)
        port = self._settings.cat.port or "?"
        baud = self._settings.cat.baudrate
        if connected:
            parts = ["Verbunden"]
            if info:
                parts.append(info)
            parts.append(f"{port} @ {baud} Baud")
            self._connection_footer_label.setText(" — ".join(parts))
            self._connection_footer_label.setStyleSheet("")
        else:
            self._connection_footer_label.setStyleSheet("color: gray;")
            if info and self._reconnect_timer.isActive():
                cfg_port = self._settings.cat.port or "?"
                self._connection_footer_label.setText(
                    "Nicht verbunden — "
                    f"{info} — versuche {cfg_port} alle "
                    f"{self._reconnect_timer.interval() // 1000} s erneut"
                )
            else:
                cfg_port = self._settings.cat.port
                if cfg_port:
                    self._connection_footer_label.setText(
                        "Nicht verbunden — "
                        f"bereit: {cfg_port} @ {baud} Baud"
                    )
                else:
                    self._connection_footer_label.setText("Kein Port konfiguriert")

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

    def _on_t_call_active_changed(self, active: bool) -> None:
        self._radio_control_bar.set_t_call_active(active)

    def _on_t_call_pressed(self) -> None:
        if not self._cat.is_connected():
            self._on_t_call_error("CAT nicht verbunden")
            return
        if self._audio_tx_busy():
            QMessageBox.information(
                self,
                "T.CALL",
                "Audio-Player oder -Recorder sendet bereits — "
                "bitte zuerst stoppen.",
            )
            return
        if self._tcall_cat_pending:
            return

        self.meter_widget.pause_polling()
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
                    self._on_t_call_error(msg or "DATA-FM nicht gesetzt")
                    self.meter_widget.ensure_polling()
                    return
                if msg:
                    self.statusBar().showMessage(f"T.CALL: {msg}", 4000)
            QTimer.singleShot(150, self._t_call_arm_tx_and_audio)
            return

        self.statusBar().showMessage("T.CALL: Schalte auf DATA-FM …", 0)
        self._tcall_cat_pending = True

        if setup.is_applied:
            self._tcall_release_engage_plain = True
            QMetaObject.invokeMethod(
                worker,
                "run_engage_data",
                Qt.ConnectionType.QueuedConnection,
            )
            return

        has_audio_win = self._audio_radio_session.has_open_audio_windows
        self._tcall_release_restore_full = not has_audio_win
        self._tcall_release_engage_plain = has_audio_win
        QMetaObject.invokeMethod(
            worker,
            "run_apply",
            Qt.ConnectionType.QueuedConnection,
        )

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
            self._on_t_call_error(message or "DATA-FM konnte nicht gesetzt werden")
            self.meter_widget.ensure_polling()
            return
        if message:
            self.statusBar().showMessage(f"T.CALL: {message}", 4000)
        QTimer.singleShot(150, self._t_call_arm_tx_and_audio)

    def _tcall_abort_radio_switch(self) -> None:
        """Taste losgelassen, bevor DATA-FM fertig — Funkzustand zurück."""
        if self._tcall_restore_data_mode is not None:
            mode = self._tcall_restore_data_mode
            self._tcall_restore_data_mode = None
            if self._cat.is_connected():
                self._audio_radio_session.setup.set_data_mode(mode)
            self._tcall_release_restore_full = False
            self._tcall_release_engage_plain = False
            self._tcall_cat_pending = False
            return
        if self._tcall_release_restore_full:
            QMetaObject.invokeMethod(
                self._audio_radio_session.worker,
                "run_restore",
                Qt.ConnectionType.QueuedConnection,
            )
        elif self._tcall_release_engage_plain:
            QMetaObject.invokeMethod(
                self._audio_radio_session.worker,
                "run_engage_plain",
                Qt.ConnectionType.QueuedConnection,
            )
        self._tcall_release_restore_full = False
        self._tcall_release_engage_plain = False
        self._tcall_restore_data_mode = None
        self._tcall_cat_pending = False

    def _t_call_arm_tx_and_audio(self) -> None:
        if not self._radio_control_bar.is_t_call_pressed():
            self._tcall_abort_radio_switch()
            self.meter_widget.ensure_polling()
            return
        if not self._cat.is_connected():
            self.meter_widget.ensure_polling()
            return
        try:
            FT991CAT(self._cat).set_cat_transmit(True, wait=False)
        except CatError as exc:
            self._on_t_call_error(str(exc))
            self._tcall_abort_radio_switch()
            self.meter_widget.ensure_polling()
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
        self.meter_widget.ensure_polling()

    def _tcall_restore_radio_after_call(self) -> None:
        """Nach T.CALL: vorherigen Funk-Mode/Menüs wiederherstellen."""
        if self._tcall_restore_data_mode is not None:
            mode = self._tcall_restore_data_mode
            self._tcall_restore_data_mode = None
            if self._cat.is_connected():
                ok, msg = self._audio_radio_session.setup.set_data_mode(mode)
                if not ok:
                    self._on_t_call_error(msg or f"{mode.value} nicht wiederhergestellt")
                elif msg:
                    self.statusBar().showMessage(f"T.CALL: {msg}", 4000)
            self._tcall_release_restore_full = False
            self._tcall_release_engage_plain = False
            return
        if not self._cat.is_connected():
            self._tcall_release_restore_full = False
            self._tcall_release_engage_plain = False
            return
        worker = self._audio_radio_session.worker
        if self._tcall_release_restore_full:
            self.statusBar().showMessage("T.CALL: Stelle Funkgerät wieder her …", 3000)
            QMetaObject.invokeMethod(
                worker,
                "run_restore",
                Qt.ConnectionType.QueuedConnection,
            )
        elif self._tcall_release_engage_plain:
            QMetaObject.invokeMethod(
                worker,
                "run_engage_plain",
                Qt.ConnectionType.QueuedConnection,
            )
        self._tcall_release_restore_full = False
        self._tcall_release_engage_plain = False

    def _on_t_call_error(self, message: str) -> None:
        self.statusBar().showMessage(f"T.CALL: {message}", 5000)

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
                    raise CatError("Keine gültige VFO-A-Frequenz.")
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
        """Band-Dropdown = aktuelles Amateurband oder „VFO“ (außerhalb)."""
        f = int(hz)
        if f <= 0:
            self._select_band_combo_vfo()
            return
        band = amateur_band_at_hz(f)
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
                    raise CatError("VFO-Modus konnte nicht gesetzt werden.")
            else:
                if not ft.switch_to_vfo_mode():
                    raise CatError("VFO-Modus konnte nicht gesetzt werden.")
                hz = int(choice)
                ft.write_frequency(hz)
                self._notify_meter_app_frequency_write(hz)
                self._relay_output_hz = hz
                self._apply_vfo_a_display_hz(hz)
            self._select_memory_combo_vfo()
            self._sync_band_combo_to_frequency(self._vfo_a_display_hz)
            # Bandwahl (Dropdown): Repeater-Minus immer aus — unabhängig vom Minus-Taster.
            self._radio_control_bar.set_repeater_minus_checked(False)
            self._try_clear_fm_repeater_shift_simplex()
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(str(exc), 5000)

    def _select_memory_combo_vfo(self) -> None:
        vfo_idx = self.memory_combo.findData(self._VFO_ITEM_DATA)
        if vfo_idx < 0:
            return
        self.memory_combo.blockSignals(True)
        self.memory_combo.setCurrentIndex(vfo_idx)
        self.memory_combo.blockSignals(False)

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
            self._mode_label.setText(_status_bar_mode_text("—"))
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

    def _on_meter_status_message(self, message: str, timeout_ms: int) -> None:
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(message, max(2000, int(timeout_ms)))

    def _on_tx_status_changed(self, transmitting: bool) -> None:
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
        band = amateur_band_for_hz(hz)
        if band is not None:
            caption.setStyleSheet(_VFO_CAPTION_STYLE_IN_BAND)
            caption.setToolTip(f"Amateurfunkband {band}")
        else:
            caption.setStyleSheet(_VFO_CAPTION_STYLE_OUT_OF_BAND)
            caption.setToolTip("Außerhalb der Amateurfunkbänder")

    def _refresh_band_strip(self) -> None:
        connected = self._cat.is_connected()
        hz = self._vfo_a_display_hz if connected else 0
        band = amateur_band_at_hz(hz) if hz > 0 else None
        if band is not None:
            self._band_strip_name.setText(band.name)
            self._band_strip_name.setStyleSheet(_VFO_CAPTION_STYLE_IN_BAND)
        elif connected and hz > 0:
            self._band_strip_name.setText("—")
            self._band_strip_name.setStyleSheet(_VFO_CAPTION_STYLE_OUT_OF_BAND)
        else:
            self._band_strip_name.setText("—")
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
        """IF; P10 vom Poller — Minus-Button an den tatsächlichen TRX-Stand anpassen."""
        if self._relay_rev_active:
            return
        self._radio_control_bar.set_repeater_minus_checked(
            int(direction) == SHIFT_MINUS
        )

    def _on_rx_info_changed(
        self,
        mode: object,
        frequency_hz: int,
        frequency_b_hz: int,
        radio_transmitting: bool = False,
    ) -> None:
        """Vom MeterWidget bei VFO/Mode-Updates (RX-Slow-Path und ggf. FA/FB während TX)."""
        if isinstance(mode, RxMode):
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
        except CatError as exc:
            QMessageBox.warning(self, "VFO-A", str(exc))

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
            QMessageBox.warning(self, "VFO-B", str(exc))

    def _on_vfo_ab_clicked(self) -> None:
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).swap_vfo_a_and_b()
        except CatError as exc:
            QMessageBox.warning(self, "VFO A/B", str(exc))

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
        self.profile_widget.notify_radio_mode(mode)

    def _on_dark_mode_toggled(self, checked: bool) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, dark=checked)
        digit_color = _VFO_TRIPLET_FREQ_COLOR_DARK if checked else None
        self._vfo_a_triplet.set_text_color(digit_color)
        self._vfo_b_triplet.set_text_color(digit_color)
        if self._log_window is not None:
            self._log_window.set_dark_mode(checked)
        self._settings.ui.force_dark_mode = bool(checked)
        self._persist_settings()

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
                status_msg = f"Funkgerät wieder auf Speicherkanal {restore_ch:03d}"
            else:
                self._cat_log.log_info(
                    "=== VFO-Zustand wiederherstellen "
                    "(Frequenz und Mode vom Start) ==="
                )
                if not ft.switch_to_vfo_mode():
                    raise CatError("VFO-Modus konnte nicht gesetzt werden.")
                if restore_a is not None and restore_a > 0:
                    ft.write_frequency(restore_a)
                    self._notify_meter_app_frequency_write(restore_a)
                if restore_b is not None and restore_b > 0:
                    ft.write_frequency_b(restore_b)
                if restore_mode is not None:
                    ft.set_rx_mode(restore_mode)
                status_msg = "Funkgerät: VFO-Zustand vom Start wiederhergestellt"
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
                self._mode_label.setText(_status_bar_mode_text(mode.value))
                self.profile_widget.notify_radio_mode(mode)
            except CatError:
                if restore_mode is not None:
                    self._mode_label.setText(
                        _status_bar_mode_text(restore_mode.value)
                    )
                    self.profile_widget.notify_radio_mode(restore_mode)
            self._sync_memory_combo_from_radio()
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        except CatError as exc:
            self._cat_log.log_warn(
                f"Funkzustand nach Connect-Init nicht wiederherstellbar: {exc}"
            )
            self._sync_memory_combo_from_radio()
        sb = self.statusBar()
        if sb is not None and status_msg:
            sb.showMessage(status_msg, 4000)

    # ------------------------------------------------------------------
    # Speicherkanal-Combo
    # ------------------------------------------------------------------

    #: Sentinel, der den VFO-Eintrag im Combo markiert (anstelle einer
    #: Kanalnummer wird beim Wechsel auf diesen Eintrag VFO-Modus
    #: aktiviert).
    _VFO_ITEM_DATA = -1

    def _reset_memory_combo(self, *, placeholder: str = "VFO") -> None:
        """Setzt die Combo auf den Initial-Zustand: nur „VFO" als erster
        Eintrag. Signale werden während des Resets blockiert, damit kein
        Memory-Wechsel zum Radio geschickt wird.
        """
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
            self.memory_combo.setItemText(0, "VFO")

    def _format_memory_channel_combo_label(self, mem: MemoryChannel) -> str:
        """Einzeiliger Combobox-Text wie beim Hintergrund-Loader."""
        freq_mhz = mem.frequency_hz / 1_000_000.0
        tag = mem.tag.strip() or "(ohne Name)"
        mode_label = (
            mem.mode.value
            if mem.mode is not None and mem.mode.value != "?"
            else "?"
        )
        return (
            f"{mem.channel:03d} — {tag} "
            f"({freq_mhz:.3f} MHz, {mode_label})"
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

    def _select_memory_combo_by_channel(self, channel: int) -> None:
        """Wählt einen Kanal in der Combo (ohne CAT-Befehl).

        Wenn die Zeile nach dem Laden fehlt (z. B. Nutzdaten-Typ oder
        Timing), wird ``MT`` einmal gelesen und dieselbe Beschriftung wie
        beim Loader erzeugt — nicht dauerhaft „(aktuell aktiv)".
        """
        ch = int(channel)
        idx = self._memory_combo_index_for_channel(ch)
        if idx >= 0:
            self.memory_combo.setCurrentIndex(idx)
            return
        label: str
        mem: Optional[MemoryChannel] = None
        if self._cat.is_connected():
            try:
                mem = FT991CAT(self._cat).read_memory_channel_tag(ch)
            except CatError:
                mem = None
        if mem is not None:
            self._memory_slot_frequency_hz[int(mem.channel)] = int(mem.frequency_hz)
            label = self._format_memory_channel_combo_label(mem)
        else:
            label = f"{ch:03d} — (aktuell aktiv)"
        self.memory_combo.addItem(label, ch)
        self.memory_combo.setCurrentIndex(self.memory_combo.count() - 1)

    def _sync_memory_combo_from_radio(self) -> None:
        """Liest ``MC;`` + ``FA`` und stellt die Combo auf VFO bzw. aktiven Kanal."""
        if not self._cat.is_connected():
            return
        self._normalize_memory_combo_vfo_label()
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
        self._memory_slot_frequency_hz[int(channel.channel)] = int(
            channel.frequency_hz
        )
        label = self._format_memory_channel_combo_label(channel)
        self.memory_combo.blockSignals(True)
        try:
            self.memory_combo.addItem(label, int(channel.channel))
        finally:
            self.memory_combo.blockSignals(False)

    def _on_memory_load_progress(self, current: int, total: int) -> None:
        if self._cat.is_connected():
            self._connection_footer_label.setText(
                f"lade Speicherkanäle… {current}/{total}"
            )

    def _on_memory_load_finished(self, found: int) -> None:
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
            sb.showMessage(
                f"Speicherkanäle: {found} belegte Slots geladen",
                4000,
            )
        self._connect_init_step_done("memory")

    def _on_memory_load_failed(self, message: object) -> None:
        self.memory_combo.setEnabled(self._cat.is_connected())
        self.band_combo.setEnabled(self._cat.is_connected())
        if self._cat.is_connected():
            self._connection_footer_label.setText(
                f"Verbunden — {message}"
            )
            self.meter_widget.resume_polling()
        self._connect_init_step_done("memory")

    def _on_memory_combo_activated(self, index: int) -> None:
        """User hat einen Eintrag im Memory-Dropdown gewählt."""
        if not self._cat.is_connected():
            return
        data = self.memory_combo.itemData(index)
        ft = FT991CAT(self._cat)
        try:
            if data == self._VFO_ITEM_DATA:
                ft.switch_to_vfo_mode()
                self._sync_band_combo_to_frequency(self._vfo_a_display_hz)
                self._try_clear_fm_repeater_shift_simplex()
            elif isinstance(data, int):
                ft.select_memory_channel(int(data))
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(f"Speicherkanal-Wechsel fehlgeschlagen: {exc}", 5000)

    # ------------------------------------------------------------------
    # Favoriten (Soll-Vorgaben)
    # ------------------------------------------------------------------

    def _refresh_favorites_combo(self) -> None:
        self._favorites_panel.combo.blockSignals(True)
        self._favorites_panel.combo.clear()
        for i, fav in enumerate(self._favorites_store.favorites):
            self._favorites_panel.combo.addItem(
                format_favorite_combo_label(fav), int(i)
            )
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
                "Favoriten",
                "Bitte zuerst mit dem Funkgerät verbinden.",
            )
            return
        sel = self._favorites_selected_store_index()
        replace_idx: Optional[int] = None
        new_name: Optional[str] = None
        if sel is not None:
            box = QMessageBox(self)
            box.setWindowTitle("Favorit speichern")
            box.setText(
                "Den gewählten Favoriten überschreiben oder einen neuen anlegen?"
            )
            btn_over = box.addButton(
                "Überschreiben", QMessageBox.ButtonRole.AcceptRole
            )
            btn_new = box.addButton(
                "Neu anlegen", QMessageBox.ButtonRole.ActionRole
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
                    "Neuer Favorit",
                    "Name:",
                )
                if not ok:
                    return
                new_name = text
        else:
            text, ok = QInputDialog.getText(
                self,
                "Neuer Favorit",
                "Name:",
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
            QMessageBox.warning(self, "Favoriten", str(exc))
            return
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        self._refresh_favorites_combo()
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage("Favorit gespeichert.", 4000)

    def _on_favorite_delete_clicked(self) -> None:
        sel = self._favorites_selected_store_index()
        if sel is None:
            QMessageBox.information(
                self,
                "Favoriten",
                "Bitte zuerst einen Favoriten auswählen.",
            )
            return
        fav = self._favorites_store.favorites[sel]
        if (
            QMessageBox.question(
                self,
                "Favorit löschen",
                f"Favorit „{fav.name}“ wirklich löschen?",
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
            QMessageBox.warning(self, "Favoriten", str(exc))
            return
        self._refresh_favorites_combo()

    def _on_favorite_edit_clicked(self) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                "Favoriten",
                "Bitte zuerst mit dem Funkgerät verbinden.",
            )
            return
        sel = self._favorites_selected_store_index()
        if sel is None:
            QMessageBox.information(
                self,
                "Favoriten",
                "Bitte zuerst einen Favoriten auswählen.",
            )
            return
        fav = self._favorites_store.favorites[sel]
        try:
            snap = self._snapshot_favorite_from_radio(fav.name)
            self._favorites_store.upsert(snap, replace_index=sel)
            self._favorites_store.save()
        except (ValueError, CatError) as exc:
            QMessageBox.warning(self, "Favoriten", str(exc))
            return
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        self._refresh_favorites_combo()
        self._favorites_panel.combo.setCurrentIndex(
            self._favorites_panel.combo.findData(sel)
        )
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(f"Favorit „{fav.name}“ aktualisiert.", 4000)

    def _on_favorite_combo_activated(self, index: int) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                "Favoriten",
                "Bitte zuerst mit dem Funkgerät verbinden.",
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
        self._apply_favorite(self._favorites_store.favorites[store_i])

    def _apply_favorite(self, fav: RadioFavorite) -> None:
        if not self._cat.is_connected():
            return
        ft = FT991CAT(self._cat)
        try:
            if not ft.switch_to_vfo_mode():
                raise CatError("VFO-Modus konnte nicht gesetzt werden.")
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
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        except CatError as exc:
            QMessageBox.warning(self, "Favorit", str(exc))
            return
        self._try_clear_fm_repeater_shift_simplex()
        self._apply_vfo_a_display_hz(fav.frequency_hz)
        self._sync_band_combo_to_frequency(fav.frequency_hz)
        self._mode_label.setText(_status_bar_mode_text(mode.value))
        self.profile_widget.notify_radio_mode(mode)
        eq = fav.eq_profile_name.strip()
        if eq:
            if not self.profile_widget.select_profile_by_name(eq):
                QMessageBox.information(
                    self,
                    "Favorit",
                    f"EQ-Profil „{eq}“ nicht gefunden — nur Funkwerte übernommen.",
                )
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(f"Favorit „{fav.name}“ angewendet.", 4000)

    def _start_memory_load(self) -> None:
        """Stößt den Hintergrund-Loader an. Idempotent — laufende Loads
        werden vom Loader selbst sauber gestoppt.

        Pausiert den :class:`MeterPoller`, damit der serielle Port
        ungeteilt dem Loader zur Verfuegung steht. ``_on_memory_load_*``
        setzt das Polling am Ende wieder fort.
        """
        if not self._cat.is_connected():
            return
        self._memory_slot_frequency_hz.clear()
        # Combo zurück auf „VFO" + disabled, damit der User während des
        # Loadings keinen halben Inhalt sieht.
        self._reset_memory_combo(placeholder="VFO (lade Kanäle…)")
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

    def _ensure_sound_settings_window(self) -> SoundSettingsWindow:
        if self._sound_settings_window is None:
            self._sound_settings_window = SoundSettingsWindow(
                self._settings,
                self._audio_hub,
                parent=self,
            )
            self._sound_settings_window.closed.connect(
                self._on_sound_settings_window_closed
            )
        return self._sound_settings_window

    def _on_sound_settings_action(self) -> None:
        win = self._ensure_sound_settings_window()
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_sound_settings_window_closed(self) -> None:
        self._persist_settings()

    def _ensure_audio_player_window(self) -> AudioPlayerWindow:
        if self._audio_player_window is None:
            self._audio_player_window = AudioPlayerWindow(
                self._settings,
                self._cat,
                audio_radio_session=self._audio_radio_session,
                operating_mode_provider=self._main_operating_mode,
                audio_hub=self._audio_hub,
                parent=self,
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
                parent=self,
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
                "Nicht verbunden",
                (
                    "Der Speicherkanal-Editor benötigt eine aktive "
                    "CAT-Verbindung.\n\nBitte zuerst verbinden."
                ),
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
            parent=self,
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
            self._log_window.set_dark_mode(self._settings.ui.force_dark_mode)
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
                "Update prüfen",
                (
                    f"Die eingebaute Version ist v{o.current}.\n\n"
                    f"Die Prüfung ist fehlgeschlagen:\n{o.error_message}"
                ),
            )
            return
        if o.update_available:
            box = QMessageBox(self)
            box.setWindowTitle("Update verfügbar")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(
                "Es gibt eine neuere Version auf GitHub.\n\n"
                f"Installiert: v{o.current}\n"
                f"Aktuelles Release: v{o.latest}"
            )
            box.setInformativeText(
                "Über die verlinkte Seite findest du Setup-EXE und portable ZIP."
            )
            open_btn = box.addButton(
                "Zur neuen Version", QMessageBox.ButtonRole.AcceptRole
            )
            close_btn = box.addButton("Schließen", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(close_btn)
            box.exec()
            if box.clickedButton() == open_btn:
                QDesktopServices.openUrl(QUrl(o.release_url))
        else:
            QMessageBox.information(
                self,
                "Update prüfen",
                (
                    "Version ist aktuell.\n\n"
                    f"Installiert: v{o.current}\n"
                    f"Neuestes GitHub-Release: v{o.latest}"
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
        # Sichtbarkeit der "Erweiterte Einstellungen"-Sektion synchron halten.
        self.profile_widget.set_hide_extended_in_ssb(
            self._settings.ui.hide_extended_in_ssb
        )
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
            self._log_window.close()
            self._log_window = None
        if self._equalizer_window is not None:
            self._equalizer_window.force_close()
            self._equalizer_window = None
        if self._audio_player_window is not None:
            self._audio_player_window.force_close()
            self._audio_player_window = None
        if self._audio_recorder_window is not None:
            self._audio_recorder_window.force_close()
            self._audio_recorder_window = None
        if self._memory_editor is not None:
            self._memory_editor.close()
            self._memory_editor = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._application_shutting_down = True
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
        self._last_identity_info = "warte auf Port…"
        self._refresh_header_status(connected=False, info=self._last_identity_info)
