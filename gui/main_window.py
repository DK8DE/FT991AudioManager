"""Hauptfenster des FT-991A Audio-Profilmanagers.

Neuer schlanker Aufbau (ab 0.5.1):

- Oben **rechts**: VFO-A/B und RX/TX-Anzeige; darunter ein **großer
  Meter-Bereich** (S-Meter + DSP links, AF/RF + TX-Meter rechts); unten
  **Mode-Gruppe**, **EQ-Profil** und **Speicherkanal**.
- **EQ-Profil- und Mode-Auswahl** bleiben im Hauptfenster; der Equalizer-Editor
  (Grundwerte, EQ, Erweitert, Speichern) liegt in **Edit → Equalizer**.
- Verbindung: **Datei → Verbinden** / **Datei → Trennen**.
- Die Verbindungs-Konfiguration liegt unter **Datei → Einstellungen**.
- Speicherkanäle unter **Edit → Speicherkanäle**.
- Das CAT-Log liegt unter **Ansicht → CAT-Log anzeigen** (eigenes Fenster).
"""

from __future__ import annotations

from typing import Optional, cast

import serial

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QSize
from PySide6.QtGui import QAction, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
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
from mapping.rx_mapping import RxMode, coarse_mode_group_for
from model import AppSettings, PresetStore

from .app_icon import app_icon
from .equalizer_window import EqualizerWindow
from .log_widget import LogWindow
from .memory_editor_dialog import open_memory_editor
from .memory_loader import MemoryChannelLoader
from .meter_widget import MeterWidget
from .profile_widget import ProfileWidget
from .settings_dialog import ConnectionSettingsDialog
from .theme import apply_theme
from .vfo_triplet_widget import VfoTripletWidget


class MainWindow(QMainWindow):
    """Hauptfenster mit VFO-Zeile, großem Meter-Panel und EQ-Profilzeile."""

    #: Startgröße beim ersten Öffnen (in logischen Pixeln).
    MAIN_START_WIDTH = 600
    MAIN_START_HEIGHT = 600

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FT-991A Audio-Profilmanager")
        # Doppelt setzen ist Absicht: QApplication.setWindowIcon() reicht
        # auf Windows/macOS, aber manche Linux-Window-Manager (X11) lesen
        # das Icon nur vom konkreten Toplevel.
        self.setWindowIcon(app_icon())

        self._settings = settings
        self._cat_log = CatLog()
        self._cat = SerialCAT(log=self._cat_log)
        self._preset_store = PresetStore.load()

        self._log_window: Optional[LogWindow] = None
        self._equalizer_window: Optional[EqualizerWindow] = None
        self._memory_editor: Optional[QWidget] = None
        self._last_identity_info: str = ""

        self._build_ui()
        self._build_menu()

        # Statusleiste: links Verbindung + Speicherkanal-Laden, rechts Mode/TX.
        self._connection_footer_label = QLabel("Nicht verbunden")
        self._connection_footer_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        sb = QStatusBar()
        sb.addWidget(self._connection_footer_label, 1)
        self._tx_label = QLabel("TX: aus")
        self._mode_label = QLabel("Mode: —")
        sb.addPermanentWidget(self._mode_label)
        sb.addPermanentWidget(self._tx_label)
        self.setStatusBar(sb)

        # Verbindungs-Signale verdrahten
        self.meter_widget.tx_status_changed.connect(self._on_tx_status_changed)
        self.meter_widget.connection_lost.connect(self._on_connection_lost)
        self.meter_widget.rx_info_changed.connect(self._on_rx_info_changed)
        self.profile_widget.connection_lost.connect(self._on_connection_lost)
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
            self._sync_meter_dsp_mode_visibility
        )

        # Reconnect-Watcher: läuft bei Verbindungsverlust und bei
        # konfiguriertem Auto-Connect, bis der Port wieder verfügbar ist.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(2000)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

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

    def _sync_meter_dsp_mode_visibility(self) -> None:
        """NB/DNR/DNF ausblenden, wenn die gewählte Modusgruppe sie nicht nutzt (z. B. FM)."""
        mg = coarse_mode_group_for(self.profile_widget.mode_combo.currentText())
        self.meter_widget.apply_dsp_mode_relevance(mg)

    def _apply_startup_window_geometry(self) -> None:
        """Fenster auf :attr:`MAIN_START_*` setzen und auf dem Bildschirm zentrieren."""
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
        self.profile_widget = ProfileWidget(self._cat, self._preset_store)
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
        self._vfo_a_caption.setStyleSheet("color: #d8d8d8;")
        self._vfo_a_triplet = VfoTripletWidget(text_color="#d8d8d8", font_scale=2.3)
        self._vfo_a_triplet.user_frequency_changed.connect(
            self._on_user_vfo_a_frequency
        )

        self._vfo_b_caption = QLabel("VFO-B:")
        self._vfo_b_caption.setFont(vfo_caption_font)
        self._vfo_b_caption.setStyleSheet("color: #a8a8a8;")
        self._vfo_b_triplet = VfoTripletWidget(text_color="#a8a8a8", font_scale=2.3)
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
        top_bar.setFrameShape(QFrame.StyledPanel)
        top_row = QHBoxLayout(top_bar)
        top_row.setContentsMargins(10, 6, 10, 6)
        top_row.setSpacing(12)
        top_row.addStretch(1)
        top_row.addWidget(self._vfo_a_caption)
        top_row.addWidget(self._vfo_a_triplet)
        top_row.addWidget(self._vfo_ab_button)
        top_row.addSpacing(12)
        top_row.addWidget(self._vfo_b_caption)
        top_row.addWidget(self._vfo_b_triplet)
        top_row.addSpacing(10)
        self.meter_widget.tx_led.setParent(top_bar)
        self.meter_widget.tx_label.setParent(top_bar)
        top_row.addWidget(self.meter_widget.tx_led)
        top_row.addWidget(self.meter_widget.tx_label)
        layout.addWidget(top_bar)

        layout.addWidget(self.meter_widget, stretch=1)

        # ----- Unten: Mode + EQ-Profil; Speicherkanal darunter (volle Breite) --
        bottom_bar = QFrame()
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
        bottom_outer.addLayout(bottom_row2)

        layout.addWidget(bottom_bar)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        # === Datei ====================================================
        file_menu = menu.addMenu("&Datei")

        settings_action = QAction("&Einstellungen…", self)
        settings_action.setShortcut("Ctrl+E")
        settings_action.triggered.connect(self._on_settings_action)
        file_menu.addAction(settings_action)

        self._connect_action = QAction("&Verbinden", self)
        self._connect_action.setShortcut("Ctrl+V")
        self._connect_action.triggered.connect(self._on_connect_menu)
        file_menu.addAction(self._connect_action)

        self._disconnect_action = QAction("&Trennen", self)
        self._disconnect_action.setShortcut("Ctrl+T")
        self._disconnect_action.triggered.connect(self._on_disconnect_menu)
        file_menu.addAction(self._disconnect_action)

        file_menu.addSeparator()

        quit_action = QAction("&Beenden", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # === Ansicht ==================================================
        view_menu = menu.addMenu("&Ansicht")

        self.log_toggle_action = QAction("CAT-&Log anzeigen", self)
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

        # === Edit =====================================================
        edit_menu = menu.addMenu("&Edit")

        memory_action = QAction("&Speicherkanäle…", self)
        memory_action.setShortcut("Ctrl+K")
        memory_action.triggered.connect(self._on_memory_editor_action)
        edit_menu.addAction(memory_action)

        edit_menu.addSeparator()

        equalizer_action = QAction("&Equalizer…", self)
        equalizer_action.setShortcut("Ctrl+Shift+E")
        equalizer_action.triggered.connect(self._on_equalizer_action)
        edit_menu.addAction(equalizer_action)

        # === Hilfe ====================================================
        help_menu = menu.addMenu("&Hilfe")
        about_action = QAction("Ü&ber", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

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
        self._refresh_header_status(connected=True, info=self._last_identity_info)
        self._on_connection_changed(True)
        # Direkt nach erfolgreicher Verbindung die aktuellen Werte einmal
        # vom Radio lesen — ohne Dialoge, dafür mit Fortschrittsbalken.
        # ``request_auto_read`` ist tolerant (kein Crash, wenn schon ein
        # Worker läuft).
        QTimer.singleShot(0, self.profile_widget.request_auto_read)
        # Speicherkanäle parallel im Hintergrund laden. Der Loader nutzt
        # denselben SerialCAT (mit RLock), arbeitet aber zwischen den
        # Profile-Lese-Roundtrips, sodass die GUI flüssig bleibt.
        QTimer.singleShot(50, self._start_memory_load)
        return True

    def _do_disconnect(self) -> None:
        # Manuelles Trennen schaltet auch den Auto-Reconnect aus, bis der
        # User wieder explizit "Verbinden" wählt oder die App neu startet.
        self._reconnect_timer.stop()
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
    # Slots
    # ------------------------------------------------------------------

    def _on_connection_changed(self, connected: bool) -> None:
        self.profile_widget.set_cat_available(connected)
        self.meter_widget.on_connection_changed(connected)
        if not connected:
            self._mode_label.setText("Mode: —")
            self._vfo_a_triplet.set_placeholder_empty()
            self._vfo_b_triplet.set_placeholder_empty()
            self._vfo_a_triplet.set_interactive(False)
            self._vfo_b_triplet.set_interactive(False)
            self._vfo_ab_button.setEnabled(False)
            self._tx_label.setText("TX: aus")
            # Bei Verbindungsverlust laufenden Loader stoppen und die
            # Combo zurücksetzen — sonst zeigt sie veraltete Kanäle.
            self._memory_loader.stop()
            self._reset_memory_combo()
            self.memory_combo.setEnabled(False)
        else:
            self._vfo_a_triplet.set_interactive(True)
            self._vfo_b_triplet.set_interactive(True)
            self._vfo_ab_button.setEnabled(True)
        self._persist_settings()

    def _on_tx_status_changed(self, transmitting: bool) -> None:
        self._tx_label.setText("TX: AN" if transmitting else "TX: aus")
        if transmitting:
            self._tx_label.setStyleSheet("color: #ff6060; font-weight: bold;")
        else:
            self._tx_label.setStyleSheet("")

    def _on_rx_info_changed(
        self, mode: object, frequency_hz: int, frequency_b_hz: int
    ) -> None:
        """Vom MeterWidget bei jedem Slow-Path-RX-Sample gerufen."""
        if isinstance(mode, RxMode):
            self._mode_label.setText(f"Mode: {mode.value}")
        if frequency_hz > 0:
            self._vfo_a_triplet.set_frequency_hz(frequency_hz)
        if frequency_b_hz > 0:
            self._vfo_b_triplet.set_frequency_hz(frequency_b_hz)

    def _on_user_vfo_a_frequency(self, hz: int) -> None:
        if not self._cat.is_connected():
            return
        try:
            FT991CAT(self._cat).write_frequency(hz)
        except CatError as exc:
            QMessageBox.warning(self, "VFO-A", str(exc))

    def _on_user_vfo_b_frequency(self, hz: int) -> None:
        if not self._cat.is_connected():
            return
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
        self, mode: object, _frequency_hz: int, _frequency_b_hz: int
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
        if self._log_window is not None:
            self._log_window.set_dark_mode(checked)
        self._settings.ui.force_dark_mode = bool(checked)
        self._persist_settings()

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

    def _select_memory_combo_by_channel(self, channel: int) -> None:
        """Wählt einen Kanal in der Combo (ohne CAT-Befehl)."""
        for i in range(self.memory_combo.count()):
            if self.memory_combo.itemData(i) == channel:
                self.memory_combo.setCurrentIndex(i)
                return
        self.memory_combo.addItem(f"{channel:03d} — (aktuell aktiv)", channel)
        self.memory_combo.setCurrentIndex(self.memory_combo.count() - 1)

    def _sync_memory_combo_from_radio(self) -> None:
        """Liest ``MC;`` und stellt die Combo auf VFO bzw. aktiven Kanal."""
        if not self._cat.is_connected():
            return
        self._normalize_memory_combo_vfo_label()
        active: Optional[int]
        try:
            active = FT991CAT(self._cat).read_active_memory_channel()
        except CatConnectionLostError:
            self._on_connection_lost()
            return
        except CatError:
            active = None
        self.memory_combo.blockSignals(True)
        try:
            if active is None:
                vfo_idx = self.memory_combo.findData(self._VFO_ITEM_DATA)
                if vfo_idx >= 0:
                    self.memory_combo.setCurrentIndex(vfo_idx)
            else:
                self._select_memory_combo_by_channel(active)
        finally:
            self.memory_combo.blockSignals(False)

    def _on_memory_channel_loaded(self, channel: object) -> None:
        """Wird vom Loader pro gefundenem Speicherkanal aufgerufen."""
        if not isinstance(channel, MemoryChannel):
            return
        freq_mhz = channel.frequency_hz / 1_000_000.0
        # Zeichenkette wie „012 — RELAIS DB0XX (145.500 MHz, FM)"
        # Tag-leere Slots bekommen ein „— (ohne Name)" Platzhalter.
        tag = channel.tag.strip() or "(ohne Name)"
        mode_label = (
            channel.mode.value
            if channel.mode is not None and channel.mode.value != "?"
            else "?"
        )
        label = (
            f"{channel.channel:03d} — {tag} "
            f"({freq_mhz:.3f} MHz, {mode_label})"
        )
        self.memory_combo.blockSignals(True)
        try:
            self.memory_combo.addItem(label, channel.channel)
        finally:
            self.memory_combo.blockSignals(False)

    def _on_memory_load_progress(self, current: int, total: int) -> None:
        if self._cat.is_connected():
            self._connection_footer_label.setText(
                f"lade Speicherkanäle… {current}/{total}"
            )

    def _on_memory_load_finished(self, found: int) -> None:
        # Combo aktivieren, sobald mindestens der „VFO"-Eintrag vorhanden
        # ist (also immer).
        self.memory_combo.setEnabled(self._cat.is_connected())
        if self._cat.is_connected():
            self._sync_memory_combo_from_radio()
        # Statuszeile wieder auf die echte Verbindungsinfo umschalten.
        self._refresh_header_status(
            connected=self._cat.is_connected(),
            info=self._last_identity_info,
        )
        # MeterPoller wieder aufdrehen — er war waehrend des Loads
        # pausiert, damit der Loader die volle Bandbreite hatte.
        if self._cat.is_connected():
            self.meter_widget.resume_polling()
            sb = self.statusBar()
            if sb is not None:
                active = self.memory_combo.currentData()
                if active == self._VFO_ITEM_DATA:
                    extra = ""
                elif isinstance(active, int):
                    extra = f" — Gerät auf Kanal {active:03d}"
                else:
                    extra = ""
                sb.showMessage(
                    f"Speicherkanäle: {found} belegte Slots geladen{extra}",
                    4000,
                )

    def _on_memory_load_failed(self, message: object) -> None:
        self.memory_combo.setEnabled(self._cat.is_connected())
        if self._cat.is_connected():
            self._sync_memory_combo_from_radio()
            self._connection_footer_label.setText(
                f"Verbunden — {message}"
            )
            self.meter_widget.resume_polling()

    def _on_memory_combo_activated(self, index: int) -> None:
        """User hat einen Eintrag im Memory-Dropdown gewählt."""
        if not self._cat.is_connected():
            return
        data = self.memory_combo.itemData(index)
        ft = FT991CAT(self._cat)
        try:
            if data == self._VFO_ITEM_DATA:
                ft.switch_to_vfo_mode()
            elif isinstance(data, int):
                ft.select_memory_channel(int(data))
        except CatConnectionLostError:
            self._on_connection_lost()
        except CatError as exc:
            sb = self.statusBar()
            if sb is not None:
                sb.showMessage(f"Speicherkanal-Wechsel fehlgeschlagen: {exc}", 5000)

    def _start_memory_load(self) -> None:
        """Stößt den Hintergrund-Loader an. Idempotent — laufende Loads
        werden vom Loader selbst sauber gestoppt.

        Pausiert den :class:`MeterPoller`, damit der serielle Port
        ungeteilt dem Loader zur Verfuegung steht. ``_on_memory_load_*``
        setzt das Polling am Ende wieder fort.
        """
        if not self._cat.is_connected():
            return
        # Combo zurück auf „VFO" + disabled, damit der User während des
        # Loadings keinen halben Inhalt sieht.
        self._reset_memory_combo(placeholder="VFO (lade Kanäle…)")
        self.memory_combo.setEnabled(False)
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

    def _on_settings_action(self) -> None:
        dialog = ConnectionSettingsDialog(self._settings, self._cat, parent=self)
        dialog.settings_changed.connect(self._persist_settings)
        dialog.exec()
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
        QMessageBox.about(
            self,
            "Über",
            (
                "<b>FT-991A Audio-Profilmanager</b><br>"
                "<br>"
                "Komfortable Steuerung der TX-Audio-Einstellungen<br>"
                "des Yaesu FT-991 / FT-991A über CAT.<br>"
                "<br>"
                "<b>Autor:</b> Jörg Körner (DK8DE)<br>"
                "<b>Lizenz:</b> "
                "<a href=\"https://www.apache.org/licenses/LICENSE-2.0\">"
                "Apache License 2.0</a><br>"
                "<br>"
                "<a href=\"https://github.com/DK8DE/FT991AudioManager\">"
                "github.com/DK8DE/FT991AudioManager</a>"
            ),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _persist_settings(self) -> None:
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
        try:
            self._settings.save()
        except OSError:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        try:
            self.meter_widget.stop_polling()
        finally:
            try:
                self._cat.disconnect()
            finally:
                # Log-Fenster sauber schließen + Geometrie sichern
                if self._log_window is not None:
                    self._settings.ui.log_window_geometry = (
                        self._log_window.geometry_to_base64()
                    )
                    self._log_window.close()
                if self._equalizer_window is not None:
                    self._equalizer_window.force_close()
                    self._equalizer_window = None
                self._persist_settings()
                super().closeEvent(event)

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
