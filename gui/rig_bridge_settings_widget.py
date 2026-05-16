"""Einstellungsbereich Rig-Bridge (FLRig / Hamlib rigctl)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .settings_layout import fix_spin_width, hint_label, wrap_checkbox
from model.rig_bridge_settings import HamlibListenerSettings, RigBridgeSettings
from rig_bridge.manager import RigBridgeManager

# Typ einer Zeile: (Host, Port, Name, Zeilen-Widget)
_HamlibRow = tuple[QLineEdit, QLineEdit, QLineEdit, QWidget]

_HOST_FIELD_MAX = 130
_PORT_FIELD_W = 64
_NAME_FIELD_MAX = 120


class RigBridgeSettingsWidget(QWidget):
    """FLRig- und Hamlib-Freigabe — wie in RotorTcpBridge."""

    def __init__(
        self,
        settings: RigBridgeSettings,
        *,
        get_bridge: Callable[[], Optional[RigBridgeManager]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._get_bridge = get_bridge
        self._hamlib_rows: list[_HamlibRow] = []
        self._build_ui()
        self._load_from_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(
            hint_label(
                "Stellt das Funkgerät anderen Programmen über TCP bereit — "
                "kompatibel zu FLRig (WSJT-X, fldigi) und Hamlib NET rigctl. "
                "Die CAT-Schnittstelle wird mit dieser App geteilt; zuerst "
                "verbinden, dann die Server starten."
            )
        )

        self.chk_enabled = wrap_checkbox("Rig-Bridge aktiv")
        root.addWidget(self.chk_enabled)

        # --- FLRig -------------------------------------------------------
        flrig_box = QGroupBox("FLRig (XML-RPC / HTTP)")
        flrig_l = QVBoxLayout(flrig_box)
        flrig_l.setSpacing(8)
        self.chk_flrig = wrap_checkbox("FLRig-Server aktiv")
        self.chk_flrig_autostart = wrap_checkbox(
            "Bei CAT-Verbindung automatisch starten"
        )
        self.chk_flrig_log = wrap_checkbox("TCP-Verkehr ins CAT-Log")

        self.ed_flrig_host = QLineEdit()
        self.ed_flrig_host.setPlaceholderText("127.0.0.1")
        self.ed_flrig_host.setMaximumWidth(_HOST_FIELD_MAX)
        self.sp_flrig_port = QSpinBox()
        self.sp_flrig_port.setRange(1, 65535)
        self.sp_flrig_port.setValue(12345)
        fix_spin_width(self.sp_flrig_port, 88)

        flrig_form = QFormLayout()
        flrig_form.setHorizontalSpacing(10)
        flrig_form.setVerticalSpacing(6)
        flrig_form.addRow("Host:", self.ed_flrig_host)
        flrig_form.addRow("Port:", self.sp_flrig_port)

        flrig_l.addWidget(self.chk_flrig)
        flrig_l.addLayout(flrig_form)
        flrig_l.addWidget(self.chk_flrig_autostart)
        flrig_l.addWidget(self.chk_flrig_log)

        flrig_btn_row = QHBoxLayout()
        flrig_btn_row.setSpacing(8)
        self.btn_flrig_start = QPushButton("FLRig starten")
        self.btn_flrig_stop = QPushButton("FLRig stoppen")
        flrig_btn_row.addWidget(self.btn_flrig_start)
        flrig_btn_row.addWidget(self.btn_flrig_stop)
        flrig_btn_row.addStretch(1)
        flrig_l.addLayout(flrig_btn_row)

        self.lbl_flrig_status = hint_label("—")
        self.lbl_flrig_status.setStyleSheet("color: gray;")
        flrig_l.addWidget(self.lbl_flrig_status)

        self.btn_flrig_start.clicked.connect(lambda: self._start_proto("flrig"))
        self.btn_flrig_stop.clicked.connect(lambda: self._stop_proto("flrig"))
        root.addWidget(flrig_box)

        # --- Hamlib ------------------------------------------------------
        ham_box = QGroupBox("Hamlib NET rigctl")
        ham_l = QVBoxLayout(ham_box)
        ham_l.setSpacing(8)
        self.chk_hamlib = wrap_checkbox("Hamlib-rigctl-Server aktiv")
        self.chk_hamlib_autostart = wrap_checkbox(
            "Bei CAT-Verbindung automatisch starten"
        )
        self.chk_hamlib_debug = wrap_checkbox("Ausführliches rigctl-Protokoll")
        self.chk_hamlib_log = wrap_checkbox("TCP-Verkehr ins CAT-Log")
        ham_l.addWidget(self.chk_hamlib)

        ham_l.addWidget(
            hint_label(
                "Mehrere Listener (je Zeile Host/IP + Port). "
                "Leer lassen = Zeile wird ignoriert. "
                "127.0.0.1 = nur lokal; 0.0.0.0 = alle Schnittstellen."
            )
        )

        header = QGridLayout()
        header.setHorizontalSpacing(6)
        header.setColumnStretch(0, 1)
        header.setColumnStretch(2, 1)
        header.addWidget(QLabel("Host/IP"), 0, 0)
        header.addWidget(QLabel("Port"), 0, 1)
        header.addWidget(QLabel("Name"), 0, 2)
        header.addWidget(QLabel(""), 0, 3)
        ham_l.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(140)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._hamlib_rows_host = QWidget()
        self._hamlib_rows_layout = QVBoxLayout(self._hamlib_rows_host)
        self._hamlib_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._hamlib_rows_layout.setSpacing(6)
        scroll.setWidget(self._hamlib_rows_host)
        ham_l.addWidget(scroll)

        self.btn_hamlib_add = QPushButton("Listener hinzufügen")
        self.btn_hamlib_add.clicked.connect(lambda: self._hamlib_add_row("", "", ""))
        ham_l.addWidget(self.btn_hamlib_add, 0, Qt.AlignmentFlag.AlignLeft)

        ham_l.addWidget(self.chk_hamlib_autostart)
        ham_l.addWidget(self.chk_hamlib_debug)
        ham_l.addWidget(self.chk_hamlib_log)

        ham_btn_row = QHBoxLayout()
        ham_btn_row.setSpacing(8)
        self.btn_hamlib_start = QPushButton("Hamlib starten")
        self.btn_hamlib_stop = QPushButton("Hamlib stoppen")
        ham_btn_row.addWidget(self.btn_hamlib_start)
        ham_btn_row.addWidget(self.btn_hamlib_stop)
        ham_btn_row.addStretch(1)
        ham_l.addLayout(ham_btn_row)

        self.lbl_hamlib_status = hint_label("—")
        self.lbl_hamlib_status.setStyleSheet("color: gray;")
        ham_l.addWidget(self.lbl_hamlib_status)

        self.btn_hamlib_start.clicked.connect(lambda: self._start_proto("hamlib"))
        self.btn_hamlib_stop.clicked.connect(lambda: self._stop_proto("hamlib"))
        root.addWidget(ham_box)

        hint = hint_label(
            "WSJT-X: Radio → FLRig (Host/Port oben) oder Hamlib NET rigctl "
            "(Host/Port je Zeile). Mehrere Programme können unterschiedliche "
            "Ports nutzen."
        )
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)
        root.addStretch(1)

    def _hamlib_clear_rows(self) -> None:
        for _h, _p, _n, row_w in self._hamlib_rows:
            self._hamlib_rows_layout.removeWidget(row_w)
            row_w.deleteLater()
        self._hamlib_rows.clear()

    def _hamlib_next_free_port(self, default: int = 4532) -> int:
        used: list[int] = []
        for _h, ed_p, _n, _w in self._hamlib_rows:
            try:
                p = int(ed_p.text().strip())
            except ValueError:
                continue
            if 1 <= p <= 65535:
                used.append(p)
        if not used:
            return default
        return min(65535, max(used) + 1)

    def _hamlib_add_row(
        self,
        host_text: str,
        port_text: str,
        name_text: str,
        *,
        auto_port: bool = True,
    ) -> None:
        if not port_text and auto_port:
            port_text = str(self._hamlib_next_free_port())
        if not host_text and auto_port:
            host_text = "127.0.0.1"
        row_w = QWidget()
        grid = QGridLayout(row_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        ed_host = QLineEdit()
        ed_host.setPlaceholderText("127.0.0.1")
        ed_host.setText(host_text)
        ed_host.setToolTip("Bind-Adresse / IP — z. B. 127.0.0.1 oder 0.0.0.0")
        ed_host.setMaximumWidth(_HOST_FIELD_MAX)

        ed_port = QLineEdit()
        ed_port.setPlaceholderText("4532")
        ed_port.setFixedWidth(_PORT_FIELD_W)
        ed_port.setValidator(QIntValidator(1, 65535, self))
        ed_port.setText(port_text)

        ed_name = QLineEdit()
        ed_name.setPlaceholderText("optional")
        ed_name.setText(name_text)
        ed_name.setMaximumWidth(_NAME_FIELD_MAX)

        btn_del = QPushButton("Löschen")
        btn_del.setToolTip("Diese Zeile entfernen")

        grid.addWidget(ed_host, 0, 0)
        grid.addWidget(ed_port, 0, 1)
        grid.addWidget(ed_name, 0, 2)
        grid.addWidget(btn_del, 0, 3)

        def _remove() -> None:
            self._hamlib_remove_row_and_restart(row_w)

        btn_del.clicked.connect(_remove)
        self._hamlib_rows_layout.addWidget(row_w)
        self._hamlib_rows.append((ed_host, ed_port, ed_name, row_w))

    def _hamlib_remove_row(self, row_w: QWidget) -> None:
        for i, (_h, _p, _n, w) in enumerate(self._hamlib_rows):
            if w == row_w:
                self._hamlib_rows_layout.removeWidget(row_w)
                row_w.setParent(None)
                row_w.deleteLater()
                del self._hamlib_rows[i]
                break
        if not self._hamlib_rows:
            self._hamlib_add_row("", "", "", auto_port=False)

    def _hamlib_remove_row_and_restart(self, row_w: QWidget) -> None:
        """Listener-Zeile entfernen; laufende Hamlib-Server neu binden."""
        bridge = self._get_bridge()
        was_active = False
        if bridge is not None:
            was_active = bool(bridge.protocol_status().get("hamlib_active"))
            if was_active:
                bridge.stop_protocol("hamlib")
                self.refresh_status()

        self._hamlib_remove_row(row_w)
        self.apply_to_settings()

        if was_active and bridge is not None:
            if self._hamlib_listeners_from_form():
                ok, msg = bridge.start_protocol("hamlib")
                if not ok:
                    QMessageBox.warning(self, "Rig-Bridge", msg)
            self.refresh_status()

    def _hamlib_listeners_from_form(self) -> list[HamlibListenerSettings]:
        out: list[HamlibListenerSettings] = []
        for ed_host, ed_port, ed_name, _w in self._hamlib_rows:
            pt = ed_port.text().strip()
            if not pt:
                continue
            try:
                port = int(pt)
            except ValueError:
                continue
            host = ed_host.text().strip() or "127.0.0.1"
            out.append(
                HamlibListenerSettings(
                    host=host,
                    port=max(1, min(65535, port)),
                    name=ed_name.text().strip(),
                )
            )
        return out

    def _load_from_settings(self) -> None:
        s = self._settings
        self.chk_enabled.setChecked(s.enabled)
        self.chk_flrig.setChecked(s.flrig.enabled)
        self.ed_flrig_host.setText(s.flrig.host)
        self.sp_flrig_port.setValue(s.flrig.port)
        self.chk_flrig_autostart.setChecked(s.flrig.autostart)
        self.chk_flrig_log.setChecked(s.flrig.log_tcp_traffic)
        self.chk_hamlib.setChecked(s.hamlib.enabled)
        self.chk_hamlib_autostart.setChecked(s.hamlib.autostart)
        self.chk_hamlib_debug.setChecked(s.hamlib.debug_traffic)
        self.chk_hamlib_log.setChecked(s.hamlib.log_tcp_traffic)
        self._hamlib_clear_rows()
        for li in s.hamlib.listeners:
            self._hamlib_add_row(li.host, str(li.port), li.name, auto_port=False)
        if not self._hamlib_rows:
            self._hamlib_add_row("", "", "", auto_port=False)
        self.refresh_status()

    def apply_to_settings(self) -> RigBridgeSettings:
        s = self._settings
        s.enabled = self.chk_enabled.isChecked()
        s.flrig.enabled = self.chk_flrig.isChecked()
        s.flrig.host = self.ed_flrig_host.text().strip() or "127.0.0.1"
        s.flrig.port = int(self.sp_flrig_port.value())
        s.flrig.autostart = self.chk_flrig_autostart.isChecked()
        s.flrig.log_tcp_traffic = self.chk_flrig_log.isChecked()
        s.hamlib.enabled = self.chk_hamlib.isChecked()
        s.hamlib.autostart = self.chk_hamlib_autostart.isChecked()
        s.hamlib.debug_traffic = self.chk_hamlib_debug.isChecked()
        s.hamlib.log_tcp_traffic = self.chk_hamlib_log.isChecked()
        s.hamlib.listeners = self._hamlib_listeners_from_form()
        bridge = self._get_bridge()
        if bridge is not None:
            bridge.update_config(s.to_dict())
        return s

    def refresh_status(self) -> None:
        bridge = self._get_bridge()
        if bridge is None:
            self.lbl_flrig_status.setText("CAT nicht verbunden")
            self.lbl_hamlib_status.setText("CAT nicht verbunden")
            return
        st = bridge.protocol_status()
        if st["flrig_active"]:
            self.lbl_flrig_status.setText(
                f"Läuft — {st['flrig_clients']} Client(s)"
            )
            self.lbl_flrig_status.setStyleSheet("color: #2e7d32;")
        else:
            self.lbl_flrig_status.setText("Gestoppt")
            self.lbl_flrig_status.setStyleSheet("color: gray;")
        if st["hamlib_active"]:
            parts = st.get("hamlib_bind_status") or []
            if parts:
                self.lbl_hamlib_status.setText("Läuft — " + "; ".join(parts))
            else:
                self.lbl_hamlib_status.setText(
                    f"Läuft — {st['hamlib_clients']} Client(s)"
                )
            self.lbl_hamlib_status.setStyleSheet("color: #2e7d32;")
        else:
            self.lbl_hamlib_status.setText("Gestoppt")
            self.lbl_hamlib_status.setStyleSheet("color: gray;")

    def _start_proto(self, name: str) -> None:
        bridge = self._get_bridge()
        if bridge is None:
            QMessageBox.warning(
                self,
                "Rig-Bridge",
                "Bitte zuerst mit dem Funkgerät verbinden (Datei → Verbinden).",
            )
            return
        self.apply_to_settings()
        ok, msg = bridge.start_protocol(name)
        self.refresh_status()
        if not ok:
            QMessageBox.warning(self, "Rig-Bridge", msg)

    def _stop_proto(self, name: str) -> None:
        bridge = self._get_bridge()
        if bridge is None:
            return
        bridge.stop_protocol(name)
        self.refresh_status()
