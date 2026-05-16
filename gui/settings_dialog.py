"""Zentraler Einstellungsdialog.

Layout wie in RotorTcpBridge: linke Tab-Liste, rechter Inhalt (QStackedWidget).

- **CAT-Verbindung**: Port, Baudrate, Timeout, Auto-Connect, Live-Meter-Polling,
  EQ-Profil-Anzeige.
- **Rig-Bridge**: FLRig / Hamlib rigctl.

Beim ``OK`` werden die Werte auf die übergebene :class:`AppSettings`
geschrieben und ``settings_changed`` emittiert.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import serial

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .settings_layout import (
    fix_spin_width,
    hint_label,
    narrow_panel,
    wrap_checkbox,
)

from cat import (
    CatError,
    CatTimeoutError,
    FT991_RADIO_IDS,
    FT991CAT,
    PortInfo,
    SerialCAT,
)
from model import AppSettings
from model.app_settings import POLL_MAX_MS, POLL_MIN_MS
from rig_bridge.manager import RigBridgeManager

from .rig_bridge_settings_widget import RigBridgeSettingsWidget


COMMON_BAUDRATES = [4800, 9600, 19200, 38400]

_TAB_CAT = 0
_TAB_RIG_BRIDGE = 1


class _SettingsScrollArea(QScrollArea):
    """Scroll-Bereich mit begrenzter Mindesthöhe (Dialog bleibt skalierbar)."""

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        return QSize(sh.width(), min(sh.height(), 120))

    def sizeHint(self) -> QSize:
        sh = super().sizeHint()
        return QSize(sh.width(), min(sh.height(), 560))


def _scroll_page(inner: QWidget) -> QScrollArea:
    sc = _SettingsScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.Shape.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setWidget(narrow_panel(inner))
    sc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return sc


class ConnectionSettingsDialog(QDialog):
    """Modaler Einstellungsdialog (CAT + Rig-Bridge)."""

    settings_changed = Signal()

    def __init__(
        self,
        settings: AppSettings,
        serial_cat: SerialCAT,
        *,
        get_rig_bridge: Optional[Callable[[], Optional[RigBridgeManager]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setModal(True)
        self.setFixedWidth(580)
        self.resize(580, 600)
        self.setMinimumHeight(400)

        self._settings = settings
        self._cat = serial_cat
        self._get_rig_bridge = get_rig_bridge

        self._build_ui()
        self._refresh_ports(preferred_device=settings.cat.port)

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # --- Linke Navigation + rechter Inhalt -----------------------------
        self._settings_nav = QListWidget()
        self._settings_nav.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._settings_nav.setWordWrap(True)
        self._settings_nav.setSpacing(0)
        self._settings_nav.setMinimumWidth(100)
        self._settings_nav.setMaximumWidth(150)
        self._settings_nav.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        self._settings_nav.setUniformItemSizes(True)
        self._settings_nav.addItem("CAT-Verbindung")
        self._settings_nav.addItem("Rig-Bridge")

        self._settings_stack = QStackedWidget()
        self._settings_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        page_cat = QWidget()
        cat_layout = QVBoxLayout(page_cat)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(10)
        cat_layout.addWidget(self._build_cat_group())
        cat_layout.addWidget(self._build_polling_group())
        cat_layout.addWidget(self._build_profile_view_group())
        cat_layout.addStretch(1)

        self._rig_bridge_widget = RigBridgeSettingsWidget(
            self._settings.rig_bridge,
            get_bridge=self._bridge_for_widget,
            parent=self,
        )
        page_rig = QWidget()
        rig_layout = QVBoxLayout(page_rig)
        rig_layout.setContentsMargins(0, 0, 0, 0)
        rig_layout.addWidget(self._rig_bridge_widget)
        rig_layout.addStretch(1)

        self._settings_stack.addWidget(_scroll_page(page_cat))
        self._settings_stack.addWidget(_scroll_page(page_rig))

        self._settings_nav.currentRowChanged.connect(self._on_settings_nav_changed)
        self._settings_nav.setCurrentRow(0)

        self._settings_nav_wrap = QWidget()
        nav_lay = QVBoxLayout(self._settings_nav_wrap)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.addWidget(self._settings_nav)
        self._apply_settings_nav_style()

        tabs_body = QWidget()
        tabs_body.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        tabs_h = QHBoxLayout(tabs_body)
        tabs_h.setContentsMargins(0, 0, 0, 0)
        tabs_h.setSpacing(10)
        tabs_h.setAlignment(Qt.AlignmentFlag.AlignTop)
        tabs_h.addWidget(self._settings_nav_wrap, 0)
        tabs_h.addWidget(self._settings_stack, 1)

        outer.addWidget(tabs_body, 1)

        # Status-Label (CAT-Test)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: gray;")
        outer.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        QTimer.singleShot(0, self._apply_settings_nav_style)

    def _on_settings_nav_changed(self, row: int) -> None:
        if row < 0 or row >= self._settings_stack.count():
            return
        self._settings_stack.setCurrentIndex(row)
        if row == _TAB_RIG_BRIDGE:
            self._rig_bridge_widget.refresh_status()

    def _apply_settings_nav_style(self) -> None:
        app = QApplication.instance()
        p = app.palette() if isinstance(app, QApplication) else self.palette()

        def _hex(c: QColor) -> str:
            return c.name(QColor.NameFormat.HexRgb)

        nav_bg = _hex(p.color(QPalette.ColorRole.Window))
        item_bg = _hex(p.color(QPalette.ColorRole.Base))
        sel_bg = _hex(p.color(QPalette.ColorRole.Highlight))
        sel_fg = _hex(p.color(QPalette.ColorRole.HighlightedText))
        fg = _hex(p.color(QPalette.ColorRole.WindowText))
        sep = "#787878"
        row_h = 42
        hover_bg = "#4f4f4f"
        hover_fg = "#eaeaea"

        self._settings_nav_wrap.setStyleSheet(f"background-color: {nav_bg};")
        self._settings_nav.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {nav_bg};
                border: none;
                border-right: 1px solid {sep};
                outline: none;
                padding-right: 8px;
            }}
            QListWidget::item {{
                background-color: {item_bg};
                color: {fg};
                padding: 0 8px;
                margin: 2px 4px;
                border-radius: 3px;
                min-height: {row_h}px;
                max-height: {row_h}px;
            }}
            QListWidget::item:selected {{
                background-color: {sel_bg};
                color: {sel_fg};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {hover_bg};
                color: {hover_fg};
            }}
            """
        )

    def _bridge_for_widget(self) -> Optional[RigBridgeManager]:
        if self._get_rig_bridge is None:
            return None
        return self._get_rig_bridge()

    def _build_cat_group(self) -> QGroupBox:
        box = QGroupBox("CAT-Verbindung")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(
            hint_label(
                "Port, Baudrate und Timeout für die Kommunikation mit dem "
                "FT-991 / FT-991A. Werte werden beim Klick auf OK gespeichert "
                "und ab dem nächsten Verbinden verwendet."
            )
        )

        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.port_combo.setMinimumWidth(160)
        self.port_combo.setMaximumWidth(280)
        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_row.addWidget(self.port_combo, 1)
        self.refresh_button = QPushButton("Aktualisieren")
        self.refresh_button.clicked.connect(lambda: self._refresh_ports())
        port_row.addWidget(self.refresh_button)

        self.baud_combo = QComboBox()
        for b in COMMON_BAUDRATES:
            self.baud_combo.addItem(str(b), userData=b)
        idx = self.baud_combo.findData(self._settings.cat.baudrate)
        if idx >= 0:
            self.baud_combo.setCurrentIndex(idx)
        self.baud_combo.setMaximumWidth(120)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 5000)
        self.timeout_spin.setSingleStep(50)
        self.timeout_spin.setSuffix(" ms")
        self.timeout_spin.setValue(self._settings.cat.timeout_ms)
        fix_spin_width(self.timeout_spin, 100)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        port_w = QWidget()
        port_w.setLayout(port_row)
        form.addRow("Port:", port_w)
        form.addRow("Baudrate:", self.baud_combo)
        form.addRow("Timeout:", self.timeout_spin)
        outer.addLayout(form)

        self.auto_connect_check = wrap_checkbox(
            "Beim Programmstart automatisch verbinden — und nach Verbindungs­"
            "abbruch (Kabel/Strom) im Hintergrund neu versuchen"
        )
        self.auto_connect_check.setChecked(self._settings.cat.auto_connect)
        outer.addWidget(self.auto_connect_check)

        self.test_button = QPushButton("Verbindung testen")
        self.test_button.setToolTip(
            "Öffnet kurz den gewählten Port, sendet ID; und prüft die Antwort"
        )
        self.test_button.clicked.connect(self._on_test_clicked)
        outer.addWidget(self.test_button, 0, Qt.AlignmentFlag.AlignLeft)

        return box

    def _build_polling_group(self) -> QGroupBox:
        box = QGroupBox("Live-Meter — Polling")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        outer.addWidget(
            hint_label(
                "Polling läuft automatisch, sobald die CAT-Verbindung steht. "
                "Im RX-Modus wird nur der TX-Status abgefragt; sobald gesendet "
                "wird, schaltet das Polling auf das kürzere TX-Intervall um."
            )
        )

        self.poll_tx_spin = QSpinBox()
        self.poll_tx_spin.setRange(POLL_MIN_MS, POLL_MAX_MS)
        self.poll_tx_spin.setSingleStep(50)
        self.poll_tx_spin.setSuffix(" ms")
        self.poll_tx_spin.setValue(self._settings.polling.tx_interval_ms)
        self.poll_tx_spin.setToolTip(
            "Wie oft die Meter (ALC/COMP/POWER/SWR) während TX abgefragt werden."
        )
        self.poll_tx_spin.valueChanged.connect(self._on_tx_spin_changed)
        fix_spin_width(self.poll_tx_spin, 100)

        self.poll_rx_spin = QSpinBox()
        self.poll_rx_spin.setRange(POLL_MIN_MS, POLL_MAX_MS)
        self.poll_rx_spin.setSingleStep(100)
        self.poll_rx_spin.setSuffix(" ms")
        self.poll_rx_spin.setValue(self._settings.polling.rx_interval_ms)
        self.poll_rx_spin.setToolTip(
            "Wie oft im Empfangsbetrieb der TX-Status abgefragt wird "
            "(höhere Werte entlasten die CAT-Schnittstelle)."
        )
        fix_spin_width(self.poll_rx_spin, 100)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addRow("TX-Intervall:", self.poll_tx_spin)
        form.addRow("RX-Intervall:", self.poll_rx_spin)
        outer.addLayout(form)
        return box

    def _build_profile_view_group(self) -> QGroupBox:
        box = QGroupBox("EQ-Profil-Anzeige")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        outer.addWidget(
            hint_label(
                "Stellt ein, welche Bereiche im Equalizer-Fenster (EQ-Profil) "
                "sichtbar sind. "
                "Hilfreich, um die Oberfläche kompakter zu halten, wenn "
                "bestimmte Werte selten verändert werden."
            )
        )

        self.hide_extended_ssb_check = wrap_checkbox(
            "„Erweiterte Einstellungen“ bei SSB ausblenden"
        )
        self.hide_extended_ssb_check.setToolTip(
            "Versteckt im Equalizer (EQ-Profil) die Sektion mit SSB Low-/High-Cut, "
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
            expected_str = " oder ".join(f"ID{rid};" for rid in FT991_RADIO_IDS)
            self._set_status_warn(
                f"Antwort {identity.raw.strip()} — kein FT-991(A), "
                f"erwartet {expected_str}"
            )
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
        self._rig_bridge_widget.apply_to_settings()

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
