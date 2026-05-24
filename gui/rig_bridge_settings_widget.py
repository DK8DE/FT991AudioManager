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

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from .settings_layout import fix_spin_width, hint_label, wrap_checkbox
from model.rig_bridge_settings import RigBridgeSettings
from rig_bridge.manager import RigBridgeManager


class RigBridgeSettingsWidget(RetranslatableMixin, QWidget):
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
        self._register_retranslate()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self._intro = hint_label("")
        root.addWidget(self._intro)

        self.chk_enabled = wrap_checkbox("")
        root.addWidget(self.chk_enabled)

        self._flrig_box = QGroupBox()
        flrig_l = QVBoxLayout(self._flrig_box)
        flrig_l.setContentsMargins(10, 14, 10, 10)
        flrig_l.setSpacing(8)
        self.chk_flrig = wrap_checkbox("")
        self.chk_flrig_autostart = wrap_checkbox("")
        self.chk_flrig_log = wrap_checkbox("")
        self.chk_flrig_log.toggled.connect(self._on_flrig_log_toggled)

        self.ed_flrig_host = QLineEdit()
        self.ed_flrig_host.setPlaceholderText("127.0.0.1")
        self.ed_flrig_host.setMinimumWidth(160)
        self.sp_flrig_port = QSpinBox()
        self.sp_flrig_port.setRange(1, 65535)
        self.sp_flrig_port.setValue(12345)
        fix_spin_width(self.sp_flrig_port, 100)

        self._flrig_form = QFormLayout()
        self._flrig_form.setHorizontalSpacing(10)
        self._flrig_form.setVerticalSpacing(8)
        self._flrig_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self._host_lbl = hint_label("")
        self._port_lbl = hint_label("")
        self._flrig_form.addRow(self._host_lbl, self.ed_flrig_host)
        self._flrig_form.addRow(self._port_lbl, self.sp_flrig_port)

        flrig_l.addWidget(self.chk_flrig)
        flrig_l.addLayout(self._flrig_form)
        flrig_l.addWidget(self.chk_flrig_autostart)
        flrig_l.addWidget(self.chk_flrig_log)

        flrig_btn_row = QHBoxLayout()
        flrig_btn_row.setSpacing(8)
        self.btn_flrig_start = QPushButton()
        self.btn_flrig_stop = QPushButton()
        flrig_btn_row.addWidget(self.btn_flrig_start)
        flrig_btn_row.addWidget(self.btn_flrig_stop)
        flrig_btn_row.addStretch(1)
        flrig_l.addLayout(flrig_btn_row)

        self.lbl_flrig_status = hint_label(tr("common.dash"))
        self.lbl_flrig_status.setStyleSheet("color: gray;")
        flrig_l.addWidget(self.lbl_flrig_status)

        self.btn_flrig_start.clicked.connect(lambda: self._start_proto("flrig"))
        self.btn_flrig_stop.clicked.connect(lambda: self._stop_proto("flrig"))
        root.addWidget(self._flrig_box)

        self._wsjt_hint = hint_label("")
        self._wsjt_hint.setStyleSheet("color: gray;")
        root.addWidget(self._wsjt_hint)
        root.addStretch(1)

    def retranslate_ui(self) -> None:
        self._intro.setText(tr("rig_bridge.intro"))
        self.chk_enabled.set_text(tr("rig_bridge.enabled"))
        self._flrig_box.setTitle(tr("rig_bridge.flrig_group"))
        self.chk_flrig.set_text(tr("rig_bridge.flrig_enabled"))
        self.chk_flrig_autostart.set_text(tr("rig_bridge.flrig_autostart"))
        self.chk_flrig_log.set_text(tr("rig_bridge.flrig_log"))
        self._host_lbl.setText(tr("common.host"))
        self._port_lbl.setText(tr("common.port"))
        self.btn_flrig_start.setText(tr("rig_bridge.flrig_start"))
        self.btn_flrig_stop.setText(tr("rig_bridge.flrig_stop"))
        self._wsjt_hint.setText(tr("rig_bridge.wsjt_hint"))
        self.refresh_status()

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
            self.lbl_flrig_status.setText(tr("rig_bridge.status_cat_disconnected"))
            return
        st = bridge.protocol_status()
        if st["flrig_active"]:
            self.lbl_flrig_status.setText(
                tr("rig_bridge.status_running", clients=st["flrig_clients"])
            )
            self.lbl_flrig_status.setStyleSheet("color: #2e7d32;")
        else:
            self.lbl_flrig_status.setText(tr("rig_bridge.status_stopped"))
            self.lbl_flrig_status.setStyleSheet("color: gray;")

    def _on_flrig_log_toggled(self, _checked: bool) -> None:
        """TCP-Logging sofort aktivieren, ohne Dialog zu schließen."""
        bridge = self._get_bridge()
        if bridge is not None:
            self._settings.flrig.log_tcp_traffic = self.chk_flrig_log.isChecked()
            bridge.update_config(self._settings.to_dict())

    def _start_proto(self, name: str) -> None:
        bridge = self._get_bridge()
        if bridge is None:
            QMessageBox.warning(
                self,
                tr("rig_bridge.msgbox.title"),
                tr("rig_bridge.msgbox.connect_first"),
            )
            return
        self.apply_to_settings()
        ok, msg = bridge.start_protocol(name)
        self.refresh_status()
        if not ok:
            QMessageBox.warning(self, tr("rig_bridge.msgbox.title"), msg)

    def _stop_proto(self, name: str) -> None:
        bridge = self._get_bridge()
        if bridge is None:
            return
        bridge.stop_protocol(name)
        self.refresh_status()
