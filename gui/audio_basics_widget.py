"""Editor für die TX-Audio-Grundwerte (Version 0.3).

Enthält:

- MIC Gain Slider (0..100)             — immer sichtbar
- Parametric MIC EQ Checkbox           — immer sichtbar
- Speech Processor Checkbox + Level    — nur SSB
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

from mapping.audio_mapping import (
    MIC_GAIN_DEFAULT,
    MIC_GAIN_MAX,
    MIC_GAIN_MIN,
    PROCESSOR_LEVEL_DEFAULT,
    PROCESSOR_LEVEL_MAX,
    PROCESSOR_LEVEL_MIN,
    SSB_BPF_DEFAULT_KEY,
    ssb_bpf_choices,
)


@dataclass
class AudioBasicsValues:
    mic_gain: int = MIC_GAIN_DEFAULT
    mic_eq_enabled: bool = True
    speech_processor_enabled: bool = False
    speech_processor_level: int = PROCESSOR_LEVEL_DEFAULT
    ssb_tx_bpf: str = SSB_BPF_DEFAULT_KEY


class AudioBasicsWidget(QGroupBox):
    """GroupBox mit den TX-Audio-Grundwerten."""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Grundwerte", parent)
        self._build_ui()
        self._apply_processor_enabled_state()
        self.apply_mode_relevance("SSB")

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

        universal.addWidget(QLabel("MIC Gain:"), 0, 0)
        self.mic_gain_slider = QSlider(Qt.Horizontal)
        self.mic_gain_slider.setRange(MIC_GAIN_MIN, MIC_GAIN_MAX)
        self.mic_gain_slider.setSingleStep(1)
        self.mic_gain_slider.setPageStep(5)
        self.mic_gain_slider.setValue(MIC_GAIN_DEFAULT)
        self.mic_gain_slider.setMinimumWidth(220)
        universal.addWidget(self.mic_gain_slider, 0, 1)
        self.mic_gain_label = QLabel(str(MIC_GAIN_DEFAULT))
        self.mic_gain_label.setMinimumWidth(36)
        self.mic_gain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        universal.addWidget(self.mic_gain_label, 0, 2)
        self.mic_gain_slider.valueChanged.connect(self._on_mic_gain_changed)

        self.mic_eq_check = QCheckBox("Normal-EQ verwenden (wenn Processor aus)")
        self.mic_eq_check.setToolTip(
            "Steuert das Menü PR1 — entspricht „Parametric MIC EQ ein/aus“ "
            "im FT-991A. Wirkt nur, solange der Speech Processor aus ist; "
            "ist er an, kommt der Processor-EQ zum Einsatz."
        )
        self.mic_eq_check.setChecked(True)
        self.mic_eq_check.toggled.connect(self._emit_changed)
        universal.addWidget(self.mic_eq_check, 1, 0, 1, 3)

        universal.setColumnStretch(1, 1)

        # === Speech Processor (nur SSB) ===================================
        self._processor_container = QWidget()
        processor_layout = QGridLayout(self._processor_container)
        processor_layout.setContentsMargins(0, 0, 0, 0)
        processor_layout.setHorizontalSpacing(8)
        processor_layout.setVerticalSpacing(6)
        outer.addWidget(self._processor_container)

        self.processor_check = QCheckBox("Speech Processor einschalten")
        self.processor_check.setToolTip(
            "Wirkt nur in SSB — wird in anderen Modes ausgeblendet."
        )
        self.processor_check.toggled.connect(self._on_processor_toggled)
        processor_layout.addWidget(self.processor_check, 0, 0, 1, 3)

        processor_layout.addWidget(QLabel("Processor Level:"), 1, 0)
        self.processor_level_slider = QSlider(Qt.Horizontal)
        self.processor_level_slider.setRange(PROCESSOR_LEVEL_MIN, PROCESSOR_LEVEL_MAX)
        self.processor_level_slider.setSingleStep(1)
        self.processor_level_slider.setPageStep(5)
        self.processor_level_slider.setValue(PROCESSOR_LEVEL_DEFAULT)
        processor_layout.addWidget(self.processor_level_slider, 1, 1)
        self.processor_level_label = QLabel(str(PROCESSOR_LEVEL_DEFAULT))
        self.processor_level_label.setMinimumWidth(36)
        self.processor_level_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        processor_layout.addWidget(self.processor_level_label, 1, 2)
        self.processor_level_slider.valueChanged.connect(self._on_processor_level_changed)

        processor_layout.setColumnStretch(1, 1)

        # === SSB TX-Bandbreite (nur SSB) ==================================
        self._bpf_container = QWidget()
        bpf_layout = QHBoxLayout(self._bpf_container)
        bpf_layout.setContentsMargins(0, 0, 0, 0)
        bpf_layout.setSpacing(8)
        outer.addWidget(self._bpf_container)

        bpf_layout.addWidget(QLabel("SSB TX-Bandbreite:"))
        self.ssb_bpf_combo = QComboBox()
        for key, label in ssb_bpf_choices():
            self.ssb_bpf_combo.addItem(label, userData=key)
        index = self.ssb_bpf_combo.findData(SSB_BPF_DEFAULT_KEY)
        if index >= 0:
            self.ssb_bpf_combo.setCurrentIndex(index)
        self.ssb_bpf_combo.currentIndexChanged.connect(self._emit_changed)
        bpf_layout.addWidget(self.ssb_bpf_combo, stretch=1)

    # ------------------------------------------------------------------
    # Signal-Handling
    # ------------------------------------------------------------------

    def _on_mic_gain_changed(self, value: int) -> None:
        self.mic_gain_label.setText(str(value))
        self._emit_changed()

    def _on_processor_level_changed(self, value: int) -> None:
        self.processor_level_label.setText(str(value))
        self._emit_changed()

    def _on_processor_toggled(self, _on: bool) -> None:
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

    def set_values(self, values: AudioBasicsValues) -> None:
        # Während wir programmatisch setzen, keine ``changed``-Signale.
        widgets = (
            self.mic_gain_slider,
            self.mic_eq_check,
            self.processor_check,
            self.processor_level_slider,
            self.ssb_bpf_combo,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self.mic_gain_slider.setValue(int(values.mic_gain))
            self.mic_gain_label.setText(str(int(values.mic_gain)))
            self.mic_eq_check.setChecked(bool(values.mic_eq_enabled))
            self.processor_check.setChecked(bool(values.speech_processor_enabled))
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
