"""Slider + Prozent-Anzeige + Stumm-Toggle für Soundeinstellungen."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from audio.windows_endpoint_volume import windows_endpoint_peak_available
from i18n import tr
from i18n.retranslatable import RetranslatableMixin

from .menu_icons import menu_action_icon, volume_role_icon_size
from .meter_widget import (
    ScaledMeterBar,
    live_level_raw_from_linear_peak,
    make_live_level_bar_horizontal,
)


class VolumeControlRow(RetranslatableMixin, QWidget):
    """Horizontaler Live-Pegel + Lautstärke-Slider, Prozent, Stumm-Button."""

    value_changed = Signal(int)
    mute_toggled = Signal(bool)

    def __init__(
        self,
        *,
        tooltip: str = "",
        show_level_meter: bool = True,
        leading_icon: Optional[QIcon] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._level_bar: Optional[ScaledMeterBar] = None
        self._role_lbl: Optional[QLabel] = None
        self._meter_active = False
        self._peak = 0.0
        self._display = 0.0
        self._help_tooltip = str(tooltip or "").strip()
        self._device_name = ""
        if show_level_meter:
            self._level_bar = make_live_level_bar_horizontal()
            self._meter_active = windows_endpoint_peak_available()
            if not self._meter_active:
                self._level_bar.set_enabled_visual(False)
            root.addWidget(self._level_bar)
            self._meter_timer = QTimer(self)
            self._meter_timer.setInterval(40)
            self._meter_timer.timeout.connect(self._meter_tick)
            self._meter_timer.start()

        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        if leading_icon is not None and not leading_icon.isNull():
            self._role_lbl = QLabel()
            self._role_lbl.setFixedSize(volume_role_icon_size())
            self._role_lbl.setPixmap(
                leading_icon.pixmap(
                    volume_role_icon_size(),
                    QIcon.Mode.Normal,
                    QIcon.State.Off,
                )
            )
            layout.addWidget(self._role_lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(100)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(10)
        self._slider.setPageStep(10)
        self._slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self._slider, 1)

        self._lbl_percent = QLabel(tr("common.percent", value=100))
        self._lbl_percent.setMinimumWidth(40)
        layout.addWidget(self._lbl_percent)

        self._btn_mute = QPushButton()
        self._btn_mute.setCheckable(True)
        self._btn_mute.setFixedSize(28, 28)
        self._btn_mute.toggled.connect(self._on_mute_toggled)
        self._update_mute_icon(False)
        layout.addWidget(self._btn_mute)

        root.addWidget(row)
        self._apply_tooltips()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self._lbl_percent.setText(tr("common.percent", value=self.value()))
        self._apply_tooltips()

    def set_assigned_device(self, device_label: str) -> None:
        """Zugeordnetes Gerät — erscheint im Tooltip von Slider, Pegel und Stumm."""
        self._device_name = str(device_label or "").strip()
        self._apply_tooltips()

    def _apply_tooltips(self) -> None:
        dev = self._device_name or tr("common.not_selected")
        dev_line = tr("common.device", dev=dev)
        parts = [p for p in (self._help_tooltip, dev_line) if p]
        slider_tip = "\n".join(parts)
        mute_tip = f"{tr('volume.mute_tooltip')}\n{dev_line}"
        level_tip = f"{tr('volume.level_tooltip')}\n{dev_line}"
        self._slider.setToolTip(slider_tip)
        self._btn_mute.setToolTip(mute_tip)
        if self._level_bar is not None:
            self._level_bar.setToolTip(level_tip)
        if self._role_lbl is not None:
            self._role_lbl.setToolTip(slider_tip)

    def set_peak_level(self, level: float) -> None:
        if self._level_bar is None or not self._meter_active:
            return
        v = max(0.0, min(1.0, float(level)))
        if v >= self._peak:
            self._peak = v
        elif v > self._display:
            self._display = v

    def _meter_tick(self) -> None:
        if self._level_bar is None or not self._meter_active:
            return
        decay = 0.82
        self._display = max(self._peak, self._display * decay)
        self._peak *= decay
        if self._peak < 0.002:
            self._peak = 0.0
        if self._display < 0.002:
            self._display = 0.0
        self._level_bar.set_value(live_level_raw_from_linear_peak(self._display))

    def value(self) -> int:
        return int(self._slider.value())

    def set_value(self, percent: int, *, block_signals: bool = True) -> None:
        v = max(0, min(100, int(percent)))
        if block_signals:
            self._slider.blockSignals(True)
        try:
            self._slider.setValue(v)
            self._lbl_percent.setText(tr("common.percent", value=v))
        finally:
            if block_signals:
                self._slider.blockSignals(False)

    def is_muted(self) -> bool:
        return self._btn_mute.isChecked()

    def set_muted(self, muted: bool, *, block_signals: bool = True) -> None:
        if block_signals:
            self._btn_mute.blockSignals(True)
        try:
            self._btn_mute.setChecked(bool(muted))
            self._update_mute_icon(bool(muted))
        finally:
            if block_signals:
                self._btn_mute.blockSignals(False)

    def _on_slider(self, value: int) -> None:
        self._lbl_percent.setText(tr("common.percent", value=value))
        self.value_changed.emit(int(value))

    def _on_mute_toggled(self, checked: bool) -> None:
        self._update_mute_icon(checked)
        self.mute_toggled.emit(bool(checked))

    def _update_mute_icon(self, muted: bool) -> None:
        if muted:
            icon = menu_action_icon(
                QStyle.StandardPixmap.SP_MediaVolumeMuted,
                theme_name="audio-volume-muted",
            )
        else:
            icon = menu_action_icon(
                QStyle.StandardPixmap.SP_MediaVolume,
                theme_name="audio-volume-high",
            )
        self._btn_mute.setIcon(icon)
