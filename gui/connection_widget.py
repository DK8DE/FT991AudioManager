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
    FT991CAT,
    PortInfo,
    RadioIdentity,
    SerialCAT,
)
from i18n import tr


# Häufige Baudraten beim FT-991A — Werks-Default ist 38400.
COMMON_BAUDRATES = [4800, 9600, 19200, 38400]


def _expected_radio_ids_str() -> str:
    return tr("joiner.or").join(
        tr("settings.test.expected_id_or", id=f"ID{rid}") for rid in FT991_RADIO_IDS
    )


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
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._cat = serial_cat
        self._initial_port = initial_port
        self._initial_baudrate = initial_baudrate
        self._initial_timeout_ms = initial_timeout_ms

        self._title_label: QLabel | None = None
        self._lbl_port: QLabel | None = None
        self._lbl_baudrate: QLabel | None = None
        self._lbl_timeout: QLabel | None = None

        self._status_text = tr("connection.status.not_connected")

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

        self._title_label = QLabel(tr("connection.title"))
        outer.addWidget(self._title_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        outer.addLayout(grid)

        self._lbl_port = QLabel(tr("connection.port"))
        grid.addWidget(self._lbl_port, 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.port_combo.setMinimumWidth(320)
        self.port_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        grid.addWidget(self.port_combo, 0, 1)

        self._lbl_baudrate = QLabel(tr("connection.baudrate"))
        grid.addWidget(self._lbl_baudrate, 0, 2)
        self.baud_combo = QComboBox()
        for b in COMMON_BAUDRATES:
            self.baud_combo.addItem(str(b), userData=b)
        index = self.baud_combo.findData(self._initial_baudrate)
        if index >= 0:
            self.baud_combo.setCurrentIndex(index)
        grid.addWidget(self.baud_combo, 0, 3)

        self._lbl_timeout = QLabel(tr("connection.timeout"))
        grid.addWidget(self._lbl_timeout, 0, 4)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 5000)
        self.timeout_spin.setSingleStep(50)
        self.timeout_spin.setSuffix(tr("common.ms_suffix"))
        self.timeout_spin.setValue(self._initial_timeout_ms)
        grid.addWidget(self.timeout_spin, 0, 5)

        grid.setColumnStretch(1, 1)

        button_row = QHBoxLayout()
        outer.addLayout(button_row)

        self.refresh_button = QPushButton(tr("connection.refresh"))
        self.refresh_button.clicked.connect(lambda: self.refresh_ports())
        button_row.addWidget(self.refresh_button)

        self.connect_button = QPushButton(tr("connection.connect"))
        self.connect_button.clicked.connect(self._on_connect_clicked)
        button_row.addWidget(self.connect_button)

        self.disconnect_button = QPushButton(tr("connection.disconnect"))
        self.disconnect_button.clicked.connect(self._on_disconnect_clicked)
        button_row.addWidget(self.disconnect_button)

        self.test_button = QPushButton(tr("connection.test"))
        self.test_button.clicked.connect(self._on_test_clicked)
        button_row.addWidget(self.test_button)

        button_row.addStretch(1)

        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        self._apply_status_label()

    def retranslate_ui(self) -> None:
        if self._title_label is not None:
            self._title_label.setText(tr("connection.title"))
        if self._lbl_port is not None:
            self._lbl_port.setText(tr("connection.port"))
        if self._lbl_baudrate is not None:
            self._lbl_baudrate.setText(tr("connection.baudrate"))
        if self._lbl_timeout is not None:
            self._lbl_timeout.setText(tr("connection.timeout"))
        self.timeout_spin.setSuffix(tr("common.ms_suffix"))
        self.refresh_button.setText(tr("connection.refresh"))
        self.connect_button.setText(tr("connection.connect"))
        self.disconnect_button.setText(tr("connection.disconnect"))
        self.test_button.setText(tr("connection.test"))
        self._apply_status_label()

    def _apply_status_label(self) -> None:
        self.status_label.setText(
            tr("connection.status_prefix", text=self._status_text)
        )

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
                self.port_combo.addItem(tr("settings.no_ports_found"), userData=None)
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
        self._status_text = text
        self._apply_status_label()
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
            QMessageBox.warning(
                self,
                tr("connection.no_port.title"),
                tr("connection.no_port.message"),
            )
            return
        baud = self.selected_baudrate()
        timeout_ms = self.selected_timeout_ms()
        try:
            self._cat.connect(port, baudrate=baud, timeout_ms=timeout_ms)
        except (serial.SerialException, OSError) as exc:
            self._set_status(tr("connection.status.failed", error=str(exc)))
            QMessageBox.critical(
                self,
                tr("connect.failed.title"),
                tr("connect.failed.message", port=port, error=str(exc)),
            )
            self._update_buttons_enabled()
            self.connection_changed.emit(False)
            return

        self._set_status(
            tr(
                "connection.status.port_open_unverified",
                port=port,
                baud=baud,
            )
        )
        self._update_buttons_enabled()
        self.connection_changed.emit(True)
        self._run_identity_test(silent=True)

    def _on_disconnect_clicked(self) -> None:
        self._cat.disconnect()
        self._set_status(tr("connection.status.not_connected"))
        self._update_buttons_enabled()
        self.connection_changed.emit(False)

    def _on_test_clicked(self) -> None:
        if not self._cat.is_connected():
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
                self._set_status(tr("connection.status.failed", error=str(exc)))
                QMessageBox.critical(
                    self,
                    tr("connect.failed.title"),
                    tr("connect.failed.message", port=port, error=str(exc)),
                )
                self._update_buttons_enabled()
                return
            identity = self._run_identity_test(silent=False)
            self._update_buttons_enabled()
            if identity is None or not identity.is_ft991:
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
            self._set_status(tr("connection.status.not_connected"))
            return None

        ft = FT991CAT(self._cat)
        try:
            identity = ft.test_connection()
        except CatTimeoutError as exc:
            self._set_status(tr("connection.status.no_device_response"))
            if not silent:
                QMessageBox.warning(
                    self,
                    tr("settings.test.no_response.title"),
                    tr("settings.test.no_response.message", error=str(exc)),
                )
            return None
        except CatError as exc:
            self._set_status(tr("connection.status.cat_error", error=str(exc)))
            if not silent:
                QMessageBox.critical(
                    self,
                    tr("settings.test.cat_error.title"),
                    tr("settings.test.cat_error.message", error=str(exc)),
                )
            return None

        if identity.is_ft991:
            self._set_status(
                tr(
                    "connection.status.connected_ft991",
                    radio_id=identity.radio_id,
                )
            )
            if not silent:
                QMessageBox.information(
                    self,
                    tr("settings.test.device_found.title"),
                    tr("settings.test.device_found.message", raw=identity.raw),
                )
        elif identity.radio_id is not None:
            expected_str = _expected_radio_ids_str()
            self._set_status(
                tr(
                    "connection.status.wrong_device",
                    raw=identity.raw.strip(),
                    expected=expected_str,
                )
            )
            if not silent:
                QMessageBox.warning(
                    self,
                    tr("settings.test.wrong_device.title"),
                    tr(
                        "settings.test.wrong_device.message",
                        raw=identity.raw,
                        expected=expected_str,
                    ),
                )
        else:
            self._set_status(
                tr(
                    "connection.status.invalid_response",
                    raw=identity.raw,
                )
            )
            if not silent:
                QMessageBox.warning(
                    self,
                    tr("connection.test.unexpected_response.title"),
                    tr(
                        "connection.test.unexpected_response.message",
                        raw=identity.raw,
                    ),
                )

        return identity
