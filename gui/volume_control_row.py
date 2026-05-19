"""Slider + Prozent-Anzeige + Stumm-Toggle für Soundeinstellungen."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QWidget,
)

from .menu_icons import menu_action_icon


class VolumeControlRow(QWidget):
    """Horizontale Zeile: Lautstärke-Slider, Prozent, Stumm-Button."""

    value_changed = Signal(int)
    mute_toggled = Signal(bool)

    def __init__(
        self,
        *,
        tooltip: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(100)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(10)
        self._slider.setPageStep(10)
        if tooltip:
            self._slider.setToolTip(tooltip)
        self._slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self._slider, 1)

        self._lbl_percent = QLabel("100 %")
        self._lbl_percent.setMinimumWidth(40)
        layout.addWidget(self._lbl_percent)

        self._btn_mute = QPushButton()
        self._btn_mute.setCheckable(True)
        self._btn_mute.setFixedSize(28, 28)
        self._btn_mute.setToolTip(
            "Stumm am Windows-Gerät ein/aus (überschreibt System-Stumm)"
        )
        self._btn_mute.toggled.connect(self._on_mute_toggled)
        self._update_mute_icon(False)
        layout.addWidget(self._btn_mute)

    def value(self) -> int:
        return int(self._slider.value())

    def set_value(self, percent: int, *, block_signals: bool = True) -> None:
        v = max(0, min(100, int(percent)))
        if block_signals:
            self._slider.blockSignals(True)
        try:
            self._slider.setValue(v)
            self._lbl_percent.setText(f"{v} %")
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
        self._lbl_percent.setText(f"{value} %")
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
