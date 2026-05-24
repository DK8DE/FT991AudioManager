"""Editor für die TX-Audio-Grundwerte (Version 0.3).

Enthält:

- MIC Gain Slider (0..100)             — immer sichtbar
- Parametric MIC EQ Checkbox           — immer sichtbar
- Speech Processor Checkbox + Level    — nur SSB (gegen Normal-EQ verriegelt)
- SSB-TX-Bandbreite (EX112)            — nur SSB

Die SSB-spezifischen Zeilen liegen in eigenen Container-Widgets, damit
``setVisible(False)`` sauber den Platz freigibt (statt eine leere Zeile
im Grid zu hinterlassen).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from mapping.rx_mapping import mic_gain_slider_visible_for_mode_group
from mapping.audio_mapping import (
    MIC_GAIN_DEFAULT,
    MIC_GAIN_MAX,
    MIC_GAIN_MIN,
    PROCESSOR_LEVEL_DEFAULT,
    PROCESSOR_LEVEL_MAX,
    PROCESSOR_LEVEL_MIN,
    SSB_BPF_DEFAULT_KEY,
    SSB_BPF_TABLE,
)


def _ssb_bpf_label(key: str) -> str:
    i18n_key = f"audio_basics.ssb_bpf.{key.replace('-', '_')}"
    return tr(i18n_key)


@dataclass
class AudioBasicsValues:
    mic_gain: int = MIC_GAIN_DEFAULT
    mic_eq_enabled: bool = True
    speech_processor_enabled: bool = False
    speech_processor_level: int = PROCESSOR_LEVEL_DEFAULT
    ssb_tx_bpf: str = SSB_BPF_DEFAULT_KEY


class AudioBasicsWidget(RetranslatableMixin, QGroupBox):
    """GroupBox mit den TX-Audio-Grundwerten."""

    changed = Signal()
    #: Nach programmatischem :meth:`set_values` (z. B. Profil laden).
    mic_gain_synced = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mutual_updating = False
        self._build_ui()
        self._apply_processor_enabled_state()
        self.apply_mode_relevance("SSB")
        self._register_retranslate()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setTitle(tr("audio_basics.title"))
        self._mic_gain_title.setText(tr("audio_basics.mic_gain"))
        self.mic_gain_slider.setToolTip(tr("audio_basics.mic_gain_tooltip"))
        self.mic_gain_label.setToolTip(tr("audio_basics.mic_gain_display_tooltip"))
        self.mic_eq_check.setText(tr("audio_basics.normal_eq"))
        self.mic_eq_check.setToolTip(tr("audio_basics.normal_eq_tooltip"))
        self.processor_check.setText(tr("audio_basics.speech_processor"))
        self.processor_check.setToolTip(tr("audio_basics.speech_processor_tooltip"))
        self._processor_level_title.setText(tr("audio_basics.processor_level"))
        self._bpf_title.setText(tr("audio_basics.ssb_tx_bpf"))
        current_bpf = str(self.ssb_bpf_combo.currentData() or SSB_BPF_DEFAULT_KEY)
        self.ssb_bpf_combo.blockSignals(True)
        try:
            self.ssb_bpf_combo.clear()
            for _i, key, _label in SSB_BPF_TABLE:
                self.ssb_bpf_combo.addItem(_ssb_bpf_label(key), userData=key)
            idx = self.ssb_bpf_combo.findData(current_bpf)
            if idx < 0:
                self.ssb_bpf_combo.addItem(current_bpf, userData=current_bpf)
                idx = self.ssb_bpf_combo.findData(current_bpf)
            self.ssb_bpf_combo.setCurrentIndex(max(0, idx))
        finally:
            self.ssb_bpf_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(6)

        # === Universelle Werte (immer sichtbar) ============================
        self._universal_container = QWidget()
        universal = QGridLayout(self._universal_container)
        universal.setContentsMargins(0, 0, 0, 0)
        universal.setHorizontalSpacing(8)
        universal.setVerticalSpacing(6)
        outer.addWidget(self._universal_container)

        self._mic_gain_title = QLabel()
        universal.addWidget(self._mic_gain_title, 0, 0)
        self.mic_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gain_slider.setMinimum(MIC_GAIN_MIN)
        self.mic_gain_slider.setMaximum(MIC_GAIN_MAX)
        self.mic_gain_slider.setSingleStep(1)
        self.mic_gain_slider.setPageStep(5)
        self.mic_gain_slider.setValue(MIC_GAIN_DEFAULT)
        self.mic_gain_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.mic_gain_slider.setTickInterval(10)
        self.mic_gain_slider.setMinimumWidth(220)
        universal.addWidget(self.mic_gain_slider, 0, 1)
        self.mic_gain_label = QLabel(str(MIC_GAIN_DEFAULT))
        self.mic_gain_label.setMinimumWidth(28)
        self.mic_gain_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        universal.addWidget(self.mic_gain_label, 0, 2)
        self.mic_gain_slider.valueChanged.connect(self._on_mic_gain_slider_changed)

        self.mic_eq_check = QCheckBox()
        self.mic_eq_check.setChecked(True)
        self.mic_eq_check.toggled.connect(self._on_mic_eq_toggled)
        universal.addWidget(self.mic_eq_check, 1, 0, 1, 3)

        universal.setColumnStretch(1, 1)

        # === Speech Processor (nur SSB) ===================================
        self._processor_container = QWidget()
        processor_layout = QGridLayout(self._processor_container)
        processor_layout.setContentsMargins(0, 0, 0, 0)
        processor_layout.setHorizontalSpacing(8)
        processor_layout.setVerticalSpacing(6)
        outer.addWidget(self._processor_container)

        self.processor_check = QCheckBox()
        self.processor_check.toggled.connect(self._on_processor_toggled)
        processor_layout.addWidget(self.processor_check, 0, 0, 1, 3)

        self._processor_level_title = QLabel()
        processor_layout.addWidget(self._processor_level_title, 1, 0)
        self.processor_level_slider = QSlider(Qt.Orientation.Horizontal)
        self.processor_level_slider.setRange(PROCESSOR_LEVEL_MIN, PROCESSOR_LEVEL_MAX)
        self.processor_level_slider.setSingleStep(1)
        self.processor_level_slider.setPageStep(5)
        self.processor_level_slider.setValue(PROCESSOR_LEVEL_DEFAULT)
        processor_layout.addWidget(self.processor_level_slider, 1, 1)
        self.processor_level_label = QLabel(str(PROCESSOR_LEVEL_DEFAULT))
        self.processor_level_label.setMinimumWidth(36)
        self.processor_level_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        processor_layout.addWidget(self.processor_level_label, 1, 2)
        self.processor_level_slider.valueChanged.connect(self._on_processor_level_changed)

        processor_layout.setColumnStretch(1, 1)

        # === SSB TX-Bandbreite (nur SSB) ==================================
        self._bpf_container = QWidget()
        bpf_layout = QHBoxLayout(self._bpf_container)
        bpf_layout.setContentsMargins(0, 0, 0, 0)
        bpf_layout.setSpacing(8)
        outer.addWidget(self._bpf_container)

        self._bpf_title = QLabel()
        bpf_layout.addWidget(self._bpf_title)
        self.ssb_bpf_combo = QComboBox()
        for _i, key, _label in SSB_BPF_TABLE:
            self.ssb_bpf_combo.addItem(_ssb_bpf_label(key), userData=key)
        index = self.ssb_bpf_combo.findData(SSB_BPF_DEFAULT_KEY)
        if index >= 0:
            self.ssb_bpf_combo.setCurrentIndex(index)
        self.ssb_bpf_combo.currentIndexChanged.connect(self._emit_changed)
        bpf_layout.addWidget(self.ssb_bpf_combo, stretch=1)

    # ------------------------------------------------------------------
    # Signal-Handling
    # ------------------------------------------------------------------

    def _on_mic_gain_slider_changed(self, value: int) -> None:
        self.mic_gain_label.setText(str(int(value)))
        self._emit_changed()

    def _on_processor_level_changed(self, value: int) -> None:
        self.processor_level_label.setText(str(value))
        self._emit_changed()

    def _on_mic_eq_toggled(self, checked: bool) -> None:
        if self._mutual_updating:
            return
        if checked and self.processor_check.isChecked():
            self._mutual_updating = True
            try:
                self.processor_check.setChecked(False)
            finally:
                self._mutual_updating = False
            self._apply_processor_enabled_state()
        self._emit_changed()

    def _on_processor_toggled(self, on: bool) -> None:
        if self._mutual_updating:
            self._apply_processor_enabled_state()
            return
        if on and self.mic_eq_check.isChecked():
            self._mutual_updating = True
            try:
                self.mic_eq_check.setChecked(False)
            finally:
                self._mutual_updating = False
        self._apply_processor_enabled_state()
        self._emit_changed()

    def _apply_processor_enabled_state(self) -> None:
        on = self.processor_check.isChecked()
        self.processor_level_slider.setEnabled(on)
        self.processor_level_label.setEnabled(on)

    def apply_mode_relevance(self, mode_group: str) -> None:
        """Versteckt Speech Processor und SSB-BPF in nicht-SSB Modes."""
        is_ssb = mode_group.upper() == "SSB"
        self._processor_container.setVisible(is_ssb)
        self._bpf_container.setVisible(is_ssb)
        show_mic = mic_gain_slider_visible_for_mode_group(mode_group)
        self._mic_gain_title.setVisible(show_mic)
        self.mic_gain_slider.setVisible(show_mic)
        self.mic_gain_label.setVisible(show_mic)

    def _emit_changed(self, *_args: object) -> None:
        self.changed.emit()

    # ------------------------------------------------------------------
    # Get / Set
    # ------------------------------------------------------------------

    def get_values(self) -> AudioBasicsValues:
        return AudioBasicsValues(
            mic_gain=int(self.mic_gain_slider.value()),
            mic_eq_enabled=bool(self.mic_eq_check.isChecked()),
            speech_processor_enabled=bool(self.processor_check.isChecked()),
            speech_processor_level=int(self.processor_level_slider.value()),
            ssb_tx_bpf=str(self.ssb_bpf_combo.currentData() or SSB_BPF_DEFAULT_KEY),
        )

    def set_mic_gain_value(self, value: int, *, emit_sync: bool = True) -> None:
        """MIC Gain setzen (0–100); optional ohne ``mic_gain_synced``."""
        v = max(MIC_GAIN_MIN, min(MIC_GAIN_MAX, int(value)))
        self.mic_gain_slider.blockSignals(True)
        try:
            self.mic_gain_slider.setValue(v)
            self.mic_gain_label.setText(str(v))
        finally:
            self.mic_gain_slider.blockSignals(False)
        if emit_sync:
            self.mic_gain_synced.emit(v)

    def set_values(self, values: AudioBasicsValues) -> None:
        # Während wir programmatisch setzen, keine ``changed``-Signale.
        widgets = (
            self.mic_eq_check,
            self.processor_check,
            self.processor_level_slider,
            self.ssb_bpf_combo,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self.set_mic_gain_value(int(values.mic_gain), emit_sync=False)
            mic_on = bool(values.mic_eq_enabled)
            sp_on = bool(values.speech_processor_enabled)
            if mic_on and sp_on:
                mic_on = False
            self.mic_eq_check.setChecked(mic_on)
            self.processor_check.setChecked(sp_on)
            self.processor_level_slider.setValue(int(values.speech_processor_level))
            self.processor_level_label.setText(str(int(values.speech_processor_level)))
            idx = self.ssb_bpf_combo.findData(values.ssb_tx_bpf)
            if idx < 0:
                # Unbekannter BPF-Key — als zusätzlichen Eintrag anhängen, damit
                # nichts verloren geht, wenn ein Profil exotische Werte enthält.
                self.ssb_bpf_combo.addItem(values.ssb_tx_bpf, userData=values.ssb_tx_bpf)
                idx = self.ssb_bpf_combo.findData(values.ssb_tx_bpf)
            self.ssb_bpf_combo.setCurrentIndex(max(0, idx))
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._apply_processor_enabled_state()
        self.mic_gain_synced.emit(int(self.mic_gain_slider.value()))
