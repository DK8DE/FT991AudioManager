"""Hauptfenster des FT-991A Audio-Profilmanagers.

Neuer schlanker Aufbau (ab 0.5.1):

- Oben eine **kompakte Header-Bar**: Status-LED + Verbinden/Trennen-Button
  + Klartext-Status. Keine Port-/Baudrate-Felder mehr im Hauptfenster.
- Die Verbindungs-Konfiguration wandert in einen Einstellungs-Dialog,
  erreichbar über **Datei → Einstellungen**.
- Das CAT-Log wandert in ein eigenständiges Toplevel-Fenster, das über
  **Ansicht → CAT-Log anzeigen** ein-/ausgeblendet wird. Sichtbarkeit und
  Geometrie werden persistiert.
"""

from __future__ import annotations

from typing import Optional

import serial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
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
from mapping.rx_mapping import RxMode, format_frequency_hz
from model import AppSettings, PresetStore

from .log_widget import LogWindow
from .meter_widget import MeterWidget
from .profile_widget import ProfileWidget
from .settings_dialog import ConnectionSettingsDialog
from .status_led import StatusLed
from .theme import apply_theme


class MainWindow(QMainWindow):
    """Hauptfenster mit Header-Bar, Profil-Bereich und seitlichem Meter-Panel."""

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FT-991A Audio-Profilmanager")
        self.resize(1100, 720)

        self._settings = settings
        self._cat_log = CatLog()
        self._cat = SerialCAT(log=self._cat_log)
        self._preset_store = PresetStore.load()

        self._log_window: Optional[LogWindow] = None
        self._last_identity_info: str = ""

        self._build_ui()
        self._build_menu()

        # Statusbar mit dauerhaftem Mode-/TX-Indikator. Die Frequenz wird im
        # Header (oben neben dem Verbinden-Knopf) angezeigt — siehe
        # ``header_freq_label`` in :meth:`_build_header`.
        self._tx_label = QLabel("TX: aus")
        self._mode_label = QLabel("Mode: —")
        sb = QStatusBar()
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

        # Reconnect-Watcher: läuft bei Verbindungsverlust und bei
        # konfiguriertem Auto-Connect, bis der Port wieder verfügbar ist.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(2000)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

        # Log-Fenster anhand der gespeicherten Sichtbarkeit zeigen
        if self._settings.ui.show_cat_log:
            self._show_log_window()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ----- Schlanke Header-Bar mit LED + Connect/Disconnect ----------
        header = self._build_header()
        layout.addWidget(header)

        # ----- Body: Profil links, Meter rechts (horizontal aufgeteilt) --
        # ``QSplitter`` erlaubt dem User die Breite frei zu justieren — die
        # Meter-Sidebar bringt aber eine sinnvolle Default-/Max-Breite mit.
        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(6)
        layout.addWidget(self.body_splitter, stretch=1)

        # Profil-Bereich (Grundwerte + Normal-EQ + Processor-EQ + Erweitert)
        self.profile_widget = ProfileWidget(self._cat, self._preset_store)
        self.profile_widget.set_cat_available(False)
        self.profile_widget.set_hide_extended_in_ssb(
            self._settings.ui.hide_extended_in_ssb
        )
        self.profile_widget.setMinimumWidth(560)
        self.body_splitter.addWidget(self.profile_widget)

        # Live-Meter (hochkant) als rechte Sidebar — Polling läuft automatisch
        # mit den Intervallen aus den AppSettings.
        self.meter_widget = MeterWidget(
            self._cat,
            tx_interval_ms=self._settings.polling.tx_interval_ms,
            rx_interval_ms=self._settings.polling.rx_interval_ms,
        )
        self.body_splitter.addWidget(self.meter_widget)

        # Profilbereich soll allen verbleibenden Platz einnehmen
        self.body_splitter.setStretchFactor(0, 1)
        self.body_splitter.setStretchFactor(1, 0)
        # Default-Breitenverteilung: Meter-Sidebar bekommt ~220 px
        self.body_splitter.setSizes([1100 - 220, 220])

        self.setCentralWidget(central)

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        self.status_led = StatusLed(active=False, diameter=20)
        row.addWidget(self.status_led)

        self.connect_button = QPushButton("Verbinden")
        self.connect_button.setMinimumWidth(110)
        self.connect_button.clicked.connect(self._on_connect_toggle)
        row.addWidget(self.connect_button)

        self.connection_status_label = QLabel("nicht verbunden")
        self.connection_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        font = self.connection_status_label.font()
        font.setBold(True)
        self.connection_status_label.setFont(font)
        row.addWidget(self.connection_status_label)

        # Frequenz-Anzeigen (VFO-A und VFO-B) — fett, etwas größer.
        # Werden aus dem Slow-Path-RX-Sample aktualisiert.
        freq_font_factor = 1.15

        self.header_freq_label = QLabel("VFO-A: —")
        self.header_freq_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ff_a = self.header_freq_label.font()
        ff_a.setBold(True)
        ff_a.setPointSizeF(ff_a.pointSizeF() * freq_font_factor)
        self.header_freq_label.setFont(ff_a)
        self.header_freq_label.setStyleSheet("color: #d8d8d8;")
        row.addWidget(self.header_freq_label)

        self.header_freq_b_label = QLabel("VFO-B: —")
        self.header_freq_b_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ff_b = self.header_freq_b_label.font()
        ff_b.setBold(True)
        ff_b.setPointSizeF(ff_b.pointSizeF() * freq_font_factor)
        self.header_freq_b_label.setFont(ff_b)
        # VFO-B ist meist nur Kontext (Split-Modus) — dezenter darstellen.
        self.header_freq_b_label.setStyleSheet("color: #a8a8a8;")
        row.addWidget(self.header_freq_b_label, stretch=1)

        # Kleiner Hinweistext (Port + Baud) — rechts ausgerichtet.
        self.connection_detail_label = QLabel("")
        self.connection_detail_label.setStyleSheet("color: gray;")
        self.connection_detail_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.connection_detail_label)

        return frame

    def _build_menu(self) -> None:
        menu = self.menuBar()

        # === Datei ====================================================
        file_menu = menu.addMenu("&Datei")

        settings_action = QAction("&Einstellungen…", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._on_settings_action)
        file_menu.addAction(settings_action)

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

        # === Hilfe ====================================================
        help_menu = menu.addMenu("&Hilfe")
        about_action = QAction("Ü&ber", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------------
    # Verbinden / Trennen
    # ------------------------------------------------------------------

    def _on_connect_toggle(self) -> None:
        if self._cat.is_connected():
            self._do_disconnect()
        else:
            self._do_connect(interactive=True)

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
        self._refresh_header_status(connected=True, info=self._last_identity_info)
        self._on_connection_changed(True)
        # Direkt nach erfolgreicher Verbindung die aktuellen Werte einmal
        # vom Radio lesen — ohne Dialoge, dafür mit Fortschrittsbalken.
        # ``request_auto_read`` ist tolerant (kein Crash, wenn schon ein
        # Worker läuft).
        QTimer.singleShot(0, self.profile_widget.request_auto_read)
        return True

    def _do_disconnect(self) -> None:
        # Manuelles Trennen schaltet auch den Auto-Reconnect aus, bis der
        # User wieder explizit "Verbinden" wählt oder die App neu startet.
        self._reconnect_timer.stop()
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
        self.status_led.set_active(connected)
        self.connect_button.setText("Trennen" if connected else "Verbinden")
        if connected:
            port = self._settings.cat.port or "?"
            baud = self._settings.cat.baudrate
            self.connection_status_label.setText("verbunden")
            details = f"{port} @ {baud} Baud"
            if info:
                details = f"{info} — {details}"
            self.connection_detail_label.setText(details)
        else:
            # Auto-Reconnect-Hinweis priorisieren, sonst Default-Text.
            if info and self._reconnect_timer.isActive():
                self.connection_status_label.setText("nicht verbunden")
                cfg_port = self._settings.cat.port or "?"
                self.connection_detail_label.setText(
                    f"{info} — versuche {cfg_port} alle "
                    f"{self._reconnect_timer.interval() // 1000} s erneut"
                )
            else:
                self.connection_status_label.setText("nicht verbunden")
                cfg_port = self._settings.cat.port
                if cfg_port:
                    self.connection_detail_label.setText(
                        f"bereit: {cfg_port} @ {self._settings.cat.baudrate} Baud"
                    )
                else:
                    self.connection_detail_label.setText("kein Port konfiguriert")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_connection_changed(self, connected: bool) -> None:
        self.profile_widget.set_cat_available(connected)
        self.meter_widget.on_connection_changed(connected)
        if not connected:
            self._mode_label.setText("Mode: —")
            self.header_freq_label.setText("VFO-A: —")
            self.header_freq_b_label.setText("VFO-B: —")
            self._tx_label.setText("TX: aus")
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
            self.header_freq_label.setText(
                f"VFO-A: {format_frequency_hz(frequency_hz)}"
            )
        if frequency_b_hz > 0:
            self.header_freq_b_label.setText(
                f"VFO-B: {format_frequency_hz(frequency_b_hz)}"
            )

    def _on_rx_info_for_profile(
        self, mode: object, _frequency_hz: int, _frequency_b_hz: int
    ) -> None:
        """Reicht den Radio-Mode an das ProfileWidget weiter.

        Damit folgt die Profil-Mode-Combo automatisch dem, was am Radio
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
    # Einstellungs-Dialog
    # ------------------------------------------------------------------

    def _on_settings_action(self) -> None:
        dialog = ConnectionSettingsDialog(self._settings, self._cat, parent=self)
        dialog.settings_changed.connect(self._persist_settings)
        dialog.exec()
        # Nach dem Schließen die Anzeige im Header aktualisieren (Port/Baud
        # können sich geändert haben). ID-Info bleibt, falls noch verbunden.
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
                "Version 0.5 — Erweiterte Werte<br><br>"
                "Komfortable Steuerung der TX-Audio-Einstellungen<br>"
                "des Yaesu FT-991 / FT-991A über CAT."
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
                self._persist_settings()
                super().closeEvent(event)

    # ------------------------------------------------------------------
    # showEvent — Header-Status initial setzen
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Beim ersten Anzeigen Header in Sync mit den Settings bringen.
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
