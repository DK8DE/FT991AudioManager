"""Zentraler Einstellungsdialog.

Wird aus dem Datei-Menü heraus geöffnet. Enthält zwei Abschnitte:

- **CAT-Verbindung**: Port, Baudrate, Timeout sowie zwei Hilfsfunktionen
  (Port-Liste aktualisieren + Verbindung testen via ``ID;``).
- **Live-Meter Polling**: Intervalle für TX und RX. Das Polling läuft
  ansonsten automatisch im Hintergrund, sobald die CAT-Verbindung steht.

Beim ``OK`` werden die Werte auf die übergebene :class:`AppSettings`
geschrieben und ``settings_changed`` emittiert — das Hauptfenster
persistiert dann und propagiert die Polling-Intervalle an das Meter-Widget.
"""

from __future__ import annotations

from typing import List, Optional

import serial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cat import (
    CatError,
    CatTimeoutError,
    FT991A_RADIO_ID,
    FT991CAT,
    PortInfo,
    SerialCAT,
)
from model import AppSettings
from model.app_settings import POLL_MAX_MS, POLL_MIN_MS


COMMON_BAUDRATES = [4800, 9600, 19200, 38400]


class ConnectionSettingsDialog(QDialog):
    """Modaler Einstellungsdialog (CAT-Verbindung + Polling-Intervalle)."""

    settings_changed = Signal()

    def __init__(
        self,
        settings: AppSettings,
        serial_cat: SerialCAT,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setModal(True)
        self.resize(540, 360)

        self._settings = settings
        self._cat = serial_cat

        self._build_ui()
        self._refresh_ports(preferred_device=settings.cat.port)

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        outer.addWidget(self._build_cat_group())
        outer.addWidget(self._build_polling_group())
        outer.addWidget(self._build_profile_view_group())

        # Status-Label (für Testergebnisse)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: gray;")
        outer.addWidget(self.status_label)

        outer.addStretch(1)

        # OK / Abbrechen
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _build_cat_group(self) -> QGroupBox:
        box = QGroupBox("CAT-Verbindung")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        outer.addWidget(
            QLabel(
                "Port, Baudrate und Timeout für die Kommunikation mit dem "
                "FT-991 / FT-991A. Werte werden beim Klick auf OK gespeichert "
                "und ab dem nächsten Verbinden verwendet."
            )
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        outer.addLayout(grid)

        # Port
        grid.addWidget(QLabel("Port:"), 0, 0, Qt.AlignRight)
        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.port_combo.setMinimumWidth(260)
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid.addWidget(self.port_combo, 0, 1)

        self.refresh_button = QPushButton("Aktualisieren")
        self.refresh_button.clicked.connect(lambda: self._refresh_ports())
        grid.addWidget(self.refresh_button, 0, 2)

        # Baudrate
        grid.addWidget(QLabel("Baudrate:"), 1, 0, Qt.AlignRight)
        self.baud_combo = QComboBox()
        for b in COMMON_BAUDRATES:
            self.baud_combo.addItem(str(b), userData=b)
        idx = self.baud_combo.findData(self._settings.cat.baudrate)
        if idx >= 0:
            self.baud_combo.setCurrentIndex(idx)
        grid.addWidget(self.baud_combo, 1, 1)

        # Timeout
        grid.addWidget(QLabel("Timeout:"), 2, 0, Qt.AlignRight)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 5000)
        self.timeout_spin.setSingleStep(50)
        self.timeout_spin.setSuffix(" ms")
        self.timeout_spin.setValue(self._settings.cat.timeout_ms)
        grid.addWidget(self.timeout_spin, 2, 1)

        # Auto-Connect
        self.auto_connect_check = QCheckBox(
            "Beim Programmstart automatisch verbinden — und nach Verbindungs­"
            "abbruch (Kabel/Strom) im Hintergrund neu versuchen"
        )
        self.auto_connect_check.setChecked(self._settings.cat.auto_connect)
        outer.addWidget(self.auto_connect_check)

        # Test-Button
        test_row = QHBoxLayout()
        self.test_button = QPushButton("Verbindung testen")
        self.test_button.setToolTip(
            "Öffnet kurz den gewählten Port, sendet ID; und prüft die Antwort"
        )
        self.test_button.clicked.connect(self._on_test_clicked)
        test_row.addWidget(self.test_button)
        test_row.addStretch(1)
        outer.addLayout(test_row)

        return box

    def _build_polling_group(self) -> QGroupBox:
        box = QGroupBox("Live-Meter — Polling")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        outer.addWidget(
            QLabel(
                "Polling läuft automatisch, sobald die CAT-Verbindung steht. "
                "Im RX-Modus wird nur der TX-Status abgefragt; sobald gesendet "
                "wird, schaltet das Polling auf das kürzere TX-Intervall um."
            )
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        outer.addLayout(grid)

        grid.addWidget(QLabel("TX-Intervall:"), 0, 0, Qt.AlignRight)
        self.poll_tx_spin = QSpinBox()
        self.poll_tx_spin.setRange(POLL_MIN_MS, POLL_MAX_MS)
        self.poll_tx_spin.setSingleStep(50)
        self.poll_tx_spin.setSuffix(" ms")
        self.poll_tx_spin.setValue(self._settings.polling.tx_interval_ms)
        self.poll_tx_spin.setToolTip(
            "Wie oft die Meter (ALC/COMP/PO/SWR) während TX abgefragt werden."
        )
        self.poll_tx_spin.valueChanged.connect(self._on_tx_spin_changed)
        grid.addWidget(self.poll_tx_spin, 0, 1)

        grid.addWidget(QLabel("RX-Intervall:"), 1, 0, Qt.AlignRight)
        self.poll_rx_spin = QSpinBox()
        self.poll_rx_spin.setRange(POLL_MIN_MS, POLL_MAX_MS)
        self.poll_rx_spin.setSingleStep(100)
        self.poll_rx_spin.setSuffix(" ms")
        self.poll_rx_spin.setValue(self._settings.polling.rx_interval_ms)
        self.poll_rx_spin.setToolTip(
            "Wie oft im Empfangsbetrieb der TX-Status abgefragt wird "
            "(höhere Werte entlasten die CAT-Schnittstelle)."
        )
        grid.addWidget(self.poll_rx_spin, 1, 1)

        grid.setColumnStretch(1, 1)
        return box

    def _build_profile_view_group(self) -> QGroupBox:
        box = QGroupBox("Profil-Anzeige")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        outer.addWidget(
            QLabel(
                "Stellt ein, welche Bereiche im Profil-Tab sichtbar sind. "
                "Hilfreich, um die Oberfläche kompakter zu halten, wenn "
                "bestimmte Werte selten verändert werden."
            )
        )

        self.hide_extended_ssb_check = QCheckBox(
            "„Erweiterte Einstellungen“ bei SSB ausblenden"
        )
        self.hide_extended_ssb_check.setToolTip(
            "Versteckt im Profil-Tab die Sektion mit SSB Low-/High-Cut, "
            "Mic-Select und Out-Level — die bleibt dann nur in den anderen "
            "Modi (AM/FM/DATA/RTTY) sichtbar."
        )
        self.hide_extended_ssb_check.setChecked(
            self._settings.ui.hide_extended_in_ssb
        )
        outer.addWidget(self.hide_extended_ssb_check)

        return box

    # ------------------------------------------------------------------
    # Konsistenz: RX-Intervall darf nicht unter TX-Intervall fallen
    # ------------------------------------------------------------------

    def _on_tx_spin_changed(self, ms: int) -> None:
        if self.poll_rx_spin.value() < ms:
            self.poll_rx_spin.setValue(ms)
        # Untergrenze für RX nachziehen, damit der User nicht unter TX kommt.
        self.poll_rx_spin.setMinimum(max(POLL_MIN_MS, ms))

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    def _refresh_ports(self, *, preferred_device: Optional[str] = None) -> None:
        previous = preferred_device or self._current_port_device()
        ports: List[PortInfo] = SerialCAT.list_ports()

        self.port_combo.blockSignals(True)
        try:
            self.port_combo.clear()
            if not ports:
                self.port_combo.addItem("(keine Ports gefunden)", userData=None)
            else:
                for p in ports:
                    self.port_combo.addItem(p.display, userData=p.device)
                if previous:
                    idx = self.port_combo.findData(previous)
                    if idx >= 0:
                        self.port_combo.setCurrentIndex(idx)
        finally:
            self.port_combo.blockSignals(False)

    def _current_port_device(self) -> Optional[str]:
        data = self.port_combo.currentData()
        return data if isinstance(data, str) else None

    # ------------------------------------------------------------------
    # Test-Button
    # ------------------------------------------------------------------

    def _on_test_clicked(self) -> None:
        port = self._current_port_device()
        if not port:
            QMessageBox.warning(self, "Kein Port", "Bitte zuerst einen Port auswählen.")
            return
        baud = self.baud_combo.currentData()
        if not isinstance(baud, int):
            baud = 38400
        timeout = int(self.timeout_spin.value())

        was_connected = self._cat.is_connected()
        opened_temporarily = False
        if not was_connected:
            try:
                self._cat.connect(port, baudrate=baud, timeout_ms=timeout)
                opened_temporarily = True
            except (serial.SerialException, OSError) as exc:
                self._set_status_error(f"Port konnte nicht geöffnet werden: {exc}")
                QMessageBox.critical(
                    self,
                    "Verbindung fehlgeschlagen",
                    f"Port {port} konnte nicht geöffnet werden:\n\n{exc}",
                )
                return

        ft = FT991CAT(self._cat)
        try:
            identity = ft.test_connection()
        except CatTimeoutError as exc:
            self._set_status_error("Port geöffnet, aber keine Antwort vom Gerät")
            QMessageBox.warning(
                self,
                "Keine Antwort",
                (
                    "Es wurde kein vollständiges CAT-Telegramm empfangen.\n\n"
                    "Mögliche Ursachen:\n"
                    " • Falscher COM-Port (Enhanced vs. Standard).\n"
                    " • Falsche Baudrate (Menü 031 prüfen).\n"
                    " • Gerät ausgeschaltet oder USB-Kabel nicht verbunden.\n\n"
                    f"Detail: {exc}"
                ),
            )
            if opened_temporarily:
                self._cat.disconnect()
            return
        except CatError as exc:
            self._set_status_error(f"CAT-Fehler: {exc}")
            QMessageBox.critical(self, "CAT-Fehler", str(exc))
            if opened_temporarily:
                self._cat.disconnect()
            return

        if identity.is_ft991:
            self._set_status_ok(
                f"Verbunden mit FT-991/FT-991A (ID {identity.radio_id})"
            )
            QMessageBox.information(
                self,
                "Gerät erkannt",
                f"FT-991/FT-991A erkannt.\nAntwort: {identity.raw}",
            )
        elif identity.radio_id is not None:
            self._set_status_warn(
                f"Antwort {identity.raw.strip()} — kein FT-991(A), "
                f"erwartet ID{FT991A_RADIO_ID};"
            )
            QMessageBox.warning(
                self,
                "Anderes Gerät",
                (
                    f"Das Gerät hat geantwortet, ist aber kein FT-991(A).\n\n"
                    f"Antwort: {identity.raw}\n"
                    f"Erwartet: ID{FT991A_RADIO_ID};"
                ),
            )
        else:
            self._set_status_warn(
                f"Port geöffnet, aber keine gültige FT-991A-Antwort (roh: {identity.raw!r})"
            )

        if opened_temporarily:
            self._cat.disconnect()

    # ------------------------------------------------------------------
    # OK / Abbrechen
    # ------------------------------------------------------------------

    def accept(self) -> None:  # type: ignore[override]
        port = self._current_port_device()
        baud = self.baud_combo.currentData()
        if not isinstance(baud, int):
            baud = 38400
        self._settings.cat.port = port
        self._settings.cat.baudrate = int(baud)
        self._settings.cat.timeout_ms = int(self.timeout_spin.value())
        self._settings.cat.auto_connect = bool(self.auto_connect_check.isChecked())

        tx_ms = int(self.poll_tx_spin.value())
        rx_ms = int(self.poll_rx_spin.value())
        if rx_ms < tx_ms:
            rx_ms = tx_ms
        self._settings.polling.tx_interval_ms = tx_ms
        self._settings.polling.rx_interval_ms = rx_ms

        self._settings.ui.hide_extended_in_ssb = bool(
            self.hide_extended_ssb_check.isChecked()
        )

        self.settings_changed.emit()
        super().accept()

    # ------------------------------------------------------------------
    # Status-Label
    # ------------------------------------------------------------------

    def _set_status_ok(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #2e7d32;")

    def _set_status_warn(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #ed8a19;")

    def _set_status_error(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #c62828;")
