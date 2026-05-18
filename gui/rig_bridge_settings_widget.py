"""Einstellungsbereich Rig-Bridge (FLRig)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .settings_layout import fix_spin_width, hint_label, wrap_checkbox
from model.rig_bridge_settings import RigBridgeSettings
from rig_bridge.manager import RigBridgeManager


class RigBridgeSettingsWidget(QWidget):
    """FLRig-Freigabe über die gemeinsame CAT-Leitung."""

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
        self._build_ui()
        self._load_from_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(
            hint_label(
                "Stellt das Funkgerät anderen Programmen über TCP bereit — "
                "kompatibel zu FLRig (WSJT-X, fldigi, …). "
                "Die CAT-Schnittstelle wird mit dieser App geteilt; zuerst "
                "verbinden, dann den Server starten."
            )
        )

        self.chk_enabled = wrap_checkbox("Rig-Bridge aktiv")
        root.addWidget(self.chk_enabled)

        flrig_box = QGroupBox("FLRig (XML-RPC / HTTP)")
        flrig_l = QVBoxLayout(flrig_box)
        # Wie „CAT-Verbindung“ / „Live-Meter“ im Tab daneben
        flrig_l.setContentsMargins(10, 14, 10, 10)
        flrig_l.setSpacing(8)
        self.chk_flrig = wrap_checkbox("FLRig-Server aktiv")
        self.chk_flrig_autostart = wrap_checkbox(
            "Bei CAT-Verbindung automatisch starten"
        )
        self.chk_flrig_log = wrap_checkbox("TCP-Verkehr ins CAT-Log")

        self.ed_flrig_host = QLineEdit()
        self.ed_flrig_host.setPlaceholderText("127.0.0.1")
        self.ed_flrig_host.setMinimumWidth(160)
        self.sp_flrig_port = QSpinBox()
        self.sp_flrig_port.setRange(1, 65535)
        self.sp_flrig_port.setValue(12345)
        fix_spin_width(self.sp_flrig_port, 100)

        flrig_form = QFormLayout()
        flrig_form.setHorizontalSpacing(10)
        flrig_form.setVerticalSpacing(8)
        flrig_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
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

        hint = hint_label(
            "WSJT-X u. a.: Radio → FLRig, Host/Port wie oben eingetragen."
        )
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)
        root.addStretch(1)

    def _load_from_settings(self) -> None:
        s = self._settings
        self.chk_enabled.setChecked(s.enabled)
        self.chk_flrig.setChecked(s.flrig.enabled)
        self.ed_flrig_host.setText(s.flrig.host)
        self.sp_flrig_port.setValue(s.flrig.port)
        self.chk_flrig_autostart.setChecked(s.flrig.autostart)
        self.chk_flrig_log.setChecked(s.flrig.log_tcp_traffic)
        self.refresh_status()

    def apply_to_settings(self) -> RigBridgeSettings:
        s = self._settings
        s.enabled = self.chk_enabled.isChecked()
        s.flrig.enabled = self.chk_flrig.isChecked()
        s.flrig.host = self.ed_flrig_host.text().strip() or "127.0.0.1"
        s.flrig.port = int(self.sp_flrig_port.value())
        s.flrig.autostart = self.chk_flrig_autostart.isChecked()
        s.flrig.log_tcp_traffic = self.chk_flrig_log.isChecked()
        bridge = self._get_bridge()
        if bridge is not None:
            bridge.update_config(s.to_dict())
        return s

    def refresh_status(self) -> None:
        bridge = self._get_bridge()
        if bridge is None:
            self.lbl_flrig_status.setText("CAT nicht verbunden")
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
