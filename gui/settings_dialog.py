"""Zentraler Einstellungsdialog.

Layout wie in RotorTcpBridge: linke Tab-Liste, rechter Inhalt (QStackedWidget).

- **CAT-Verbindung**: Port, Baudrate, Timeout, Auto-Connect, Live-Meter-Polling,
  EQ-Profil-Anzeige.
- **Rig-Bridge**: FLRig.
- **Kalibrierung**: S-Meter (SM0-Rohwerte vs. Anzeige) und PO-Meter (Sendeleistung / 10 m).

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
    QGridLayout,
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
    WrappingCheckBox,
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
from model.app_settings import POLL_MAX_MS, POLL_MIN_MS, TxPollSettings
from rig_bridge.manager import RigBridgeManager
from i18n import tr
from i18n.retranslatable import RetranslatableMixin

from .po_calibration_widget import PoCalibrationWidget
from .rig_bridge_settings_widget import RigBridgeSettingsWidget
from .settings_shortcuts_widget import ShortcutsSettingsWidget
from .settings_wheel_filter import install_settings_no_wheel_filter
from .smeter_calibration_widget import SmeterCalibrationSettingsWidget


COMMON_BAUDRATES = [4800, 9600, 19200, 38400]

_TAB_CAT = 0
_TAB_RIG_BRIDGE = 1
_TAB_SHORTCUTS = 2
_TAB_CALIBRATION = 3

_TX_POLL_I18N: dict[str, str] = {
    "comp": "settings.tx_poll.comp",
    "alc": "settings.tx_poll.alc",
    "po": "settings.tx_poll.po",
    "swr": "settings.tx_poll.swr",
    "frequency_a": "settings.tx_poll.frequency_a",
    "frequency_b": "settings.tx_poll.frequency_b",
    "pc_power": "settings.tx_poll.pc_power",
}


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


class ConnectionSettingsDialog(RetranslatableMixin, QDialog):
    """Modaler Einstellungsdialog (CAT + Rig-Bridge + Kalibrierung)."""

    settings_changed = Signal()
    po_calibration_applied = Signal()
    po_calibration_busy = Signal(bool)

    def __init__(
        self,
        settings: AppSettings,
        serial_cat: SerialCAT,
        *,
        get_rig_bridge: Optional[Callable[[], Optional[RigBridgeManager]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setFixedWidth(580)
        self.resize(580, 600)
        self.setMinimumHeight(400)

        self._settings = settings
        self._cat = serial_cat
        self._get_rig_bridge = get_rig_bridge
        self._form_labels: dict[str, QLabel] = {}
        self._hint_labels: list[QLabel] = []

        self._build_ui()
        self._refresh_ports(preferred_device=settings.cat.port)
        self.retranslate_ui()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("settings.title"))
        for row, key in enumerate(
            (
                "settings.nav.cat",
                "settings.nav.rig_bridge",
                "settings.nav.shortcuts",
                "settings.nav.calibration",
            )
        ):
            item = self._settings_nav.item(row)
            if item is not None:
                item.setText(tr(key))
        self._cat_group.setTitle(tr("settings.group.cat"))
        self._cat_hint.setText(tr("settings.cat.hint"))
        self._form_labels["port"].setText(tr("settings.port"))
        self._form_labels["baudrate"].setText(tr("settings.baudrate"))
        self._form_labels["timeout"].setText(tr("settings.timeout"))
        self.refresh_button.setText(tr("settings.refresh_ports"))
        self._set_wrap_checkbox_text(
            self.auto_connect_check, tr("settings.auto_connect")
        )
        self.test_button.setText(tr("settings.test_connection"))
        self.test_button.setToolTip(tr("settings.test_connection_tooltip"))
        self._poll_group.setTitle(tr("settings.group.live_meter_polling"))
        self._poll_hint.setText(tr("settings.polling.hint"))
        self._form_labels["tx_interval"].setText(tr("settings.polling.tx_interval"))
        self._form_labels["rx_interval"].setText(tr("settings.polling.rx_interval"))
        self.poll_tx_spin.setToolTip(tr("settings.polling.tx_interval_tooltip"))
        self.poll_rx_spin.setToolTip(tr("settings.polling.rx_interval_tooltip"))
        self._tx_poll_group.setTitle(tr("settings.group.tx_polling"))
        self._tx_poll_hint.setText(tr("settings.tx_poll.hint"))
        default_tooltip = tr("settings.tx_poll.default_tooltip")
        freq_tooltip = tr("settings.tx_poll.freq_tooltip")
        for key, chk in self._tx_poll_checks.items():
            label = tr(_TX_POLL_I18N[key])
            self._set_wrap_checkbox_text(chk, label)
            if key in ("frequency_a", "frequency_b"):
                chk.setToolTip(freq_tooltip)
            else:
                chk.setToolTip(
                    tr(
                        "settings.tx_poll.item_tooltip",
                        label=label,
                        default_tooltip=default_tooltip,
                    )
                )
        self._profile_view_group.setTitle(tr("settings.group.eq_profile_view"))
        self._profile_view_hint.setText(tr("settings.eq_view.hint"))
        self._set_wrap_checkbox_text(
            self.hide_extended_ssb_check,
            tr("settings.eq_view.hide_extended_ssb"),
        )
        self.hide_extended_ssb_check.setToolTip(
            tr("settings.eq_view.hide_extended_ssb_tooltip")
        )
        self._smeter_box.setTitle(tr("settings.group.smeter_calibration"))
        self._po_box.setTitle(tr("settings.group.po_calibration"))
        self._shortcuts_widget.retranslate_ui()
        if not self._current_port_device() and self.port_combo.count() == 1:
            self.port_combo.setItemText(0, tr("settings.no_ports_found"))

    @staticmethod
    def _set_wrap_checkbox_text(chk: WrappingCheckBox, text: str) -> None:
        chk._label.setText(text)

    @staticmethod
    def _expected_radio_ids() -> str:
        return tr("joiner.or").join(
            tr("settings.test.expected_id_or", id=f"ID{rid}")
            for rid in FT991_RADIO_IDS
        )

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
        self._settings_nav.addItem("")
        self._settings_nav.addItem("")
        self._settings_nav.addItem("")
        self._settings_nav.addItem("")

        self._settings_stack = QStackedWidget()
        self._settings_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        page_cat = QWidget()
        page_cat.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
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
        self._rig_bridge_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        page_rig = QWidget()
        page_rig.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        rig_layout = QVBoxLayout(page_rig)
        rig_layout.setContentsMargins(0, 0, 0, 0)
        rig_layout.addWidget(self._rig_bridge_widget)
        rig_layout.addStretch(1)

        self._shortcuts_widget = ShortcutsSettingsWidget(
            self._settings.ui.global_shortcuts,
            parent=self,
        )
        page_shortcuts = QWidget()
        page_shortcuts.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        shortcuts_layout = QVBoxLayout(page_shortcuts)
        shortcuts_layout.setContentsMargins(0, 0, 0, 0)
        shortcuts_layout.addWidget(self._shortcuts_widget)
        shortcuts_layout.addStretch(1)

        self._smeter_cal_widget = SmeterCalibrationSettingsWidget(
            self._settings.smeter_calibration,
            parent=self,
        )
        self._smeter_box = QGroupBox()
        smeter_box_layout = QVBoxLayout(self._smeter_box)
        smeter_box_layout.setContentsMargins(10, 14, 10, 10)
        smeter_box_layout.addWidget(self._smeter_cal_widget)

        self._po_cal_widget = PoCalibrationWidget(self._cat, parent=self)
        self._po_cal_widget.calibration_applied.connect(
            self.po_calibration_applied.emit
        )
        self._po_cal_widget.busy_changed.connect(self.po_calibration_busy.emit)

        self._po_box = QGroupBox()
        self._po_box.setMaximumWidth(370)
        po_box_layout = QVBoxLayout(self._po_box)
        po_box_layout.setContentsMargins(10, 14, 10, 10)
        po_box_layout.addWidget(self._po_cal_widget, 0, Qt.AlignmentFlag.AlignLeft)

        page_cal = QWidget()
        page_cal.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        cal_layout = QVBoxLayout(page_cal)
        cal_layout.setContentsMargins(0, 0, 0, 0)
        cal_layout.setSpacing(12)
        cal_layout.addWidget(self._smeter_box)
        cal_layout.addWidget(self._po_box)
        cal_layout.addStretch(1)

        self._settings_stack.addWidget(_scroll_page(page_cat))
        self._settings_stack.addWidget(_scroll_page(page_rig))
        self._settings_stack.addWidget(_scroll_page(page_shortcuts))
        self._settings_stack.addWidget(_scroll_page(page_cal))

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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._wheel_filter = install_settings_no_wheel_filter(self)
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
        self._cat_group = QGroupBox()
        outer = QVBoxLayout(self._cat_group)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(8)

        self._cat_hint = hint_label("")
        self._hint_labels.append(self._cat_hint)
        outer.addWidget(self._cat_hint)

        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.port_combo.setMinimumWidth(160)
        self.port_combo.setMaximumWidth(280)
        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_row.addWidget(self.port_combo, 1)
        self.refresh_button = QPushButton()
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
        self._form_labels["port"] = QLabel()
        self._form_labels["baudrate"] = QLabel()
        self._form_labels["timeout"] = QLabel()
        form.addRow(self._form_labels["port"], port_w)
        form.addRow(self._form_labels["baudrate"], self.baud_combo)
        form.addRow(self._form_labels["timeout"], self.timeout_spin)
        outer.addLayout(form)

        self.auto_connect_check = wrap_checkbox("")
        self.auto_connect_check.setChecked(self._settings.cat.auto_connect)
        outer.addWidget(self.auto_connect_check)

        self.test_button = QPushButton()
        self.test_button.clicked.connect(self._on_test_clicked)
        outer.addWidget(self.test_button, 0, Qt.AlignmentFlag.AlignLeft)

        return self._cat_group

    def _build_polling_group(self) -> QGroupBox:
        self._poll_group = QGroupBox()
        outer = QVBoxLayout(self._poll_group)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        self._poll_hint = hint_label("")
        self._hint_labels.append(self._poll_hint)
        outer.addWidget(self._poll_hint)

        self.poll_tx_spin = QSpinBox()
        self.poll_tx_spin.setRange(POLL_MIN_MS, POLL_MAX_MS)
        self.poll_tx_spin.setSingleStep(50)
        self.poll_tx_spin.setSuffix(" ms")
        self.poll_tx_spin.setValue(self._settings.polling.tx_interval_ms)
        self.poll_tx_spin.valueChanged.connect(self._on_tx_spin_changed)
        fix_spin_width(self.poll_tx_spin, 100)

        self.poll_rx_spin = QSpinBox()
        self.poll_rx_spin.setRange(POLL_MIN_MS, POLL_MAX_MS)
        self.poll_rx_spin.setSingleStep(100)
        self.poll_rx_spin.setSuffix(" ms")
        self.poll_rx_spin.setValue(self._settings.polling.rx_interval_ms)
        fix_spin_width(self.poll_rx_spin, 100)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self._form_labels["tx_interval"] = QLabel()
        self._form_labels["rx_interval"] = QLabel()
        form.addRow(self._form_labels["tx_interval"], self.poll_tx_spin)
        form.addRow(self._form_labels["rx_interval"], self.poll_rx_spin)
        outer.addLayout(form)
        outer.addWidget(self._build_tx_poll_diag_group())
        return self._poll_group

    def _build_tx_poll_diag_group(self) -> QGroupBox:
        self._tx_poll_group = QGroupBox()
        outer = QVBoxLayout(self._tx_poll_group)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        self._tx_poll_hint = hint_label("")
        self._hint_labels.append(self._tx_poll_hint)
        outer.addWidget(self._tx_poll_hint)

        tp = self._settings.polling.tx_poll
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        self._tx_poll_checks: dict[str, WrappingCheckBox] = {}
        for idx, key in enumerate(_TX_POLL_I18N):
            chk = wrap_checkbox("")
            chk.setChecked(bool(getattr(tp, key)))
            self._tx_poll_checks[key] = chk
            grid.addWidget(chk, idx // 2, idx % 2)
        outer.addLayout(grid)
        return self._tx_poll_group

    def _read_tx_poll_settings(self) -> TxPollSettings:
        return TxPollSettings(
            **{key: chk.isChecked() for key, chk in self._tx_poll_checks.items()}
        )

    def _build_profile_view_group(self) -> QGroupBox:
        self._profile_view_group = QGroupBox()
        outer = QVBoxLayout(self._profile_view_group)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        self._profile_view_hint = hint_label("")
        self._hint_labels.append(self._profile_view_hint)
        outer.addWidget(self._profile_view_hint)

        self.hide_extended_ssb_check = wrap_checkbox("")
        self.hide_extended_ssb_check.setChecked(
            self._settings.ui.hide_extended_in_ssb
        )
        outer.addWidget(self.hide_extended_ssb_check)

        return self._profile_view_group

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
                self.port_combo.addItem(tr("settings.no_ports_found"), userData=None)
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
            QMessageBox.warning(
                self,
                tr("settings.test.no_port.title"),
                tr("settings.test.no_port.message"),
            )
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
                self._set_status_error(
                    tr("settings.test.port_open_failed.status", error=exc)
                )
                QMessageBox.critical(
                    self,
                    tr("connect.failed.title"),
                    tr("connect.failed.message", port=port, error=exc),
                )
                return

        ft = FT991CAT(self._cat)
        try:
            identity = ft.test_connection()
        except CatTimeoutError as exc:
            self._set_status_error(tr("settings.test.no_response.status"))
            QMessageBox.warning(
                self,
                tr("settings.test.no_response.title"),
                tr("settings.test.no_response.message", error=exc),
            )
            if opened_temporarily:
                self._cat.disconnect()
            return
        except CatError as exc:
            self._set_status_error(
                tr("settings.test.cat_error.status", error=exc)
            )
            QMessageBox.critical(
                self,
                tr("settings.test.cat_error.title"),
                tr("settings.test.cat_error.message", error=exc),
            )
            if opened_temporarily:
                self._cat.disconnect()
            return

        if identity.is_ft991:
            self._set_status_ok(
                tr(
                    "settings.test.connected.status",
                    radio_id=identity.radio_id,
                )
            )
            QMessageBox.information(
                self,
                tr("settings.test.device_found.title"),
                tr("settings.test.device_found.message", raw=identity.raw),
            )
        elif identity.radio_id is not None:
            expected_str = self._expected_radio_ids()
            self._set_status_warn(
                tr(
                    "settings.test.wrong_device.status",
                    raw=identity.raw.strip(),
                    expected=expected_str,
                )
            )
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
            self._set_status_warn(
                tr(
                    "settings.test.invalid_response.status",
                    raw=repr(identity.raw),
                )
            )

        if opened_temporarily:
            self._cat.disconnect()

    # ------------------------------------------------------------------
    # OK / Abbrechen
    # ------------------------------------------------------------------

    def reject(self) -> None:  # type: ignore[override]
        if not self._po_cal_widget.confirm_abort_if_busy():
            return
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._po_cal_widget.confirm_abort_if_busy():
            event.ignore()
            return
        super().closeEvent(event)

    def accept(self) -> None:  # type: ignore[override]
        if not self._po_cal_widget.confirm_abort_if_busy():
            return
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
        self._settings.polling.tx_poll = self._read_tx_poll_settings()

        self._settings.ui.hide_extended_in_ssb = bool(
            self.hide_extended_ssb_check.isChecked()
        )
        self._smeter_cal_widget.apply_to_settings(self._settings.smeter_calibration)
        sc = self._settings.smeter_calibration
        if sc.use_custom and len(sc.effective_points_hf()) < 2 and len(
            sc.effective_points_vhf()
        ) < 2:
            sc.use_custom = False
        self._rig_bridge_widget.apply_to_settings()
        self._shortcuts_widget.apply_to_settings(self._settings.ui.global_shortcuts)

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
