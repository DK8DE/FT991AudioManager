"""CAT-Verbindungsleiste mit Port-Liste, Baudrate, Verbinden/Trennen/Testen.

Das Widget arbeitet auf einer Instanz von :class:`cat.SerialCAT`. Verbindungs-
zustandsänderungen werden über Qt-Signale an das umgebende Fenster gemeldet.
"""

from __future__ import annotations

from typing import List, Optional

import serial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
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
    FT991_RADIO_IDS,
    FT991A_RADIO_ID,
    FT991CAT,
    PortInfo,
    RadioIdentity,
    SerialCAT,
)


# Häufige Baudraten beim FT-991A — Werks-Default ist 38400.
COMMON_BAUDRATES = [4800, 9600, 19200, 38400]


class ConnectionWidget(QFrame):
    """Verbindungsleiste oben im Hauptfenster."""

    connection_changed = Signal(bool)
    """``True`` bei erfolgreichem Verbinden, ``False`` bei Trennen/Verlust."""

    status_message = Signal(str)
    """Wird mit einer kurzen Statuszeile (z. B. für die Statusbar) gefeuert."""

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        initial_port: Optional[str] = None,
        initial_baudrate: int = 38400,
        initial_timeout_ms: int = 1000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)

        self._cat = serial_cat
        self._initial_port = initial_port
        self._initial_baudrate = initial_baudrate
        self._initial_timeout_ms = initial_timeout_ms

        self._build_ui()
        self.refresh_ports(preferred_device=initial_port)
        self._update_buttons_enabled()

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        title = QLabel("<b>CAT-Verbindung</b>")
        outer.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        outer.addLayout(grid)

        # Zeile 0: Port + Baudrate
        grid.addWidget(QLabel("Port:"), 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.port_combo.setMinimumWidth(320)
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid.addWidget(self.port_combo, 0, 1)

        grid.addWidget(QLabel("Baudrate:"), 0, 2)
        self.baud_combo = QComboBox()
        for b in COMMON_BAUDRATES:
            self.baud_combo.addItem(str(b), userData=b)
        index = self.baud_combo.findData(self._initial_baudrate)
        if index >= 0:
            self.baud_combo.setCurrentIndex(index)
        grid.addWidget(self.baud_combo, 0, 3)

        grid.addWidget(QLabel("Timeout:"), 0, 4)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 5000)
        self.timeout_spin.setSingleStep(50)
        self.timeout_spin.setSuffix(" ms")
        self.timeout_spin.setValue(self._initial_timeout_ms)
        grid.addWidget(self.timeout_spin, 0, 5)

        grid.setColumnStretch(1, 1)

        # Zeile 1: Buttons
        button_row = QHBoxLayout()
        outer.addLayout(button_row)

        self.refresh_button = QPushButton("Aktualisieren")
        self.refresh_button.clicked.connect(lambda: self.refresh_ports())
        button_row.addWidget(self.refresh_button)

        self.connect_button = QPushButton("Verbinden")
        self.connect_button.clicked.connect(self._on_connect_clicked)
        button_row.addWidget(self.connect_button)

        self.disconnect_button = QPushButton("Trennen")
        self.disconnect_button.clicked.connect(self._on_disconnect_clicked)
        button_row.addWidget(self.disconnect_button)

        self.test_button = QPushButton("Verbindung testen")
        self.test_button.clicked.connect(self._on_test_clicked)
        button_row.addWidget(self.test_button)

        button_row.addStretch(1)

        # Zeile 2: Status
        self.status_label = QLabel("Status: nicht verbunden")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    def refresh_ports(self, *, preferred_device: Optional[str] = None) -> None:
        """Liest die Liste der seriellen Ports neu ein."""
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
                    index = self.port_combo.findData(previous)
                    if index >= 0:
                        self.port_combo.setCurrentIndex(index)
        finally:
            self.port_combo.blockSignals(False)

        self._update_buttons_enabled()

    def _current_port_device(self) -> Optional[str]:
        data = self.port_combo.currentData()
        return data if isinstance(data, str) else None

    # ------------------------------------------------------------------
    # Status / Buttons
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_label.setText(f"Status: {text}")
        self.status_message.emit(text)

    def _update_buttons_enabled(self) -> None:
        connected = self._cat.is_connected()
        has_port = self._current_port_device() is not None
        self.connect_button.setEnabled(not connected and has_port)
        self.disconnect_button.setEnabled(connected)
        self.test_button.setEnabled(has_port)
        self.port_combo.setEnabled(not connected)
        self.baud_combo.setEnabled(not connected)
        self.timeout_spin.setEnabled(not connected)

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def selected_port(self) -> Optional[str]:
        return self._current_port_device()

    def selected_baudrate(self) -> int:
        data = self.baud_combo.currentData()
        if isinstance(data, int):
            return data
        try:
            return int(self.baud_combo.currentText())
        except ValueError:
            return 38400

    def selected_timeout_ms(self) -> int:
        return int(self.timeout_spin.value())

    def _on_connect_clicked(self) -> None:
        port = self._current_port_device()
        if not port:
            QMessageBox.warning(self, "Kein Port", "Bitte zuerst einen seriellen Port auswählen.")
            return
        baud = self.selected_baudrate()
        timeout_ms = self.selected_timeout_ms()
        try:
            self._cat.connect(port, baudrate=baud, timeout_ms=timeout_ms)
        except (serial.SerialException, OSError) as exc:
            self._set_status(f"Verbindung fehlgeschlagen ({exc})")
            QMessageBox.critical(
                self,
                "Verbindung fehlgeschlagen",
                f"Port {port} konnte nicht geöffnet werden:\n\n{exc}",
            )
            self._update_buttons_enabled()
            self.connection_changed.emit(False)
            return

        self._set_status(f"Port {port} bei {baud} Baud geöffnet — Gerät noch nicht geprüft")
        self._update_buttons_enabled()
        self.connection_changed.emit(True)
        # Direkt anschließend kurz testen, damit der Anwender sofort Feedback hat.
        self._run_identity_test(silent=True)

    def _on_disconnect_clicked(self) -> None:
        self._cat.disconnect()
        self._set_status("nicht verbunden")
        self._update_buttons_enabled()
        self.connection_changed.emit(False)

    def _on_test_clicked(self) -> None:
        if not self._cat.is_connected():
            # Temporäre Verbindung nur für den Test aufbauen.
            port = self._current_port_device()
            if not port:
                return
            try:
                self._cat.connect(
                    port,
                    baudrate=self.selected_baudrate(),
                    timeout_ms=self.selected_timeout_ms(),
                )
            except (serial.SerialException, OSError) as exc:
                self._set_status(f"Verbindung fehlgeschlagen ({exc})")
                QMessageBox.critical(
                    self,
                    "Verbindung fehlgeschlagen",
                    f"Port {port} konnte nicht geöffnet werden:\n\n{exc}",
                )
                self._update_buttons_enabled()
                return
            identity = self._run_identity_test(silent=False)
            self._update_buttons_enabled()
            if identity is None or not identity.is_ft991:
                # Testverbindung wieder schließen, wenn kein FT-991(A) gefunden.
                # Wenn doch erkannt, bleibt die Verbindung bestehen — das ist
                # in der Regel das, was der Anwender möchte.
                if identity is None or not identity.is_ft991:
                    pass
            self.connection_changed.emit(self._cat.is_connected())
            return

        self._run_identity_test(silent=False)

    # ------------------------------------------------------------------
    # ID-Test
    # ------------------------------------------------------------------

    def _run_identity_test(self, *, silent: bool) -> Optional[RadioIdentity]:
        """Führt ``ID;`` aus und aktualisiert den Status entsprechend.

        Bei ``silent=False`` wird zusätzlich eine MessageBox angezeigt.
        Gibt die :class:`RadioIdentity` zurück oder ``None`` bei Fehler.
        """
        if not self._cat.is_connected():
            self._set_status("nicht verbunden")
            return None

        ft = FT991CAT(self._cat)
        try:
            identity = ft.test_connection()
        except CatTimeoutError as exc:
            self._set_status("Port geöffnet, aber keine Antwort vom Gerät")
            if not silent:
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
            return None
        except CatError as exc:
            self._set_status(f"CAT-Fehler: {exc}")
            if not silent:
                QMessageBox.critical(self, "CAT-Fehler", str(exc))
            return None

        if identity.is_ft991:
            self._set_status(f"Verbunden mit FT-991/FT-991A (ID {identity.radio_id})")
            if not silent:
                QMessageBox.information(
                    self,
                    "Gerät erkannt",
                    f"FT-991/FT-991A erkannt.\nAntwort: {identity.raw}",
                )
        elif identity.radio_id is not None:
            expected_str = " oder ".join(f"ID{rid};" for rid in FT991_RADIO_IDS)
            self._set_status(
                f"Antwort {identity.raw.strip()} — kein FT-991(A), erwartet {expected_str}"
            )
            if not silent:
                QMessageBox.warning(
                    self,
                    "Anderes Gerät",
                    (
                        f"Das Gerät hat geantwortet, ist aber kein FT-991(A).\n\n"
                        f"Antwort: {identity.raw}\n"
                        f"Erwartet: {expected_str}"
                    ),
                )
        else:
            self._set_status(
                f"Port geöffnet, aber keine gültige FT-991A-Antwort (roh: {identity.raw!r})"
            )
            if not silent:
                QMessageBox.warning(
                    self,
                    "Unerwartete Antwort",
                    (
                        "Die Antwort entspricht nicht dem Format ID####;.\n\n"
                        f"Empfangen: {identity.raw!r}"
                    ),
                )

        return identity
