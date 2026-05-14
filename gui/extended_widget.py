"""Editor für die erweiterten Audio-Einstellungen (Version 0.5).

Gruppiert die Settings thematisch:

- SSB-Klangformung (Low Cut / High Cut Freq + Slope)
- AM-Einstellungen (Carrier-Level, Mikrofon)
- FM-Einstellungen (Carrier-Level, Mikrofon)
- DATA TX-Level

Die für die aktuelle Mode-Gruppe **nicht** relevanten Sub-Gruppen werden
ausgegraut, aber die Werte bleiben editierbar — so kann ein User ein
Profil zukünftig auch in einer anderen Mode wiederverwenden, ohne zu
verlieren, was er für diesen Mode konfiguriert hat.

EX106 (SSB MIC SELECT) und EX107 (SSB OUT LEVEL) werden bewusst nicht
verwaltet — der Block hat in der Praxis nichts an MIC GAIN / Speech
Processor / Front-vs-Rear Setup beigetragen und wurde entfernt.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mapping.extended_mapping import (
    AM_CARRIER_MENU,
    AM_MIC_SEL_MENU,
    CARRIER_LEVEL_DEFAULT,
    CARRIER_LEVEL_MAX,
    CARRIER_LEVEL_MIN,
    DATA_TX_LEVEL_DEFAULT,
    DATA_TX_LEVEL_MAX,
    DATA_TX_LEVEL_MENU,
    DATA_TX_LEVEL_MIN,
    FM_CARRIER_MENU,
    FM_MIC_SEL_MENU,
    MicSource,
    SSB_HCUT_FREQ_MENU,
    SSB_HCUT_FREQS,
    SSB_HCUT_SLOPE_MENU,
    SSB_LCUT_FREQ_MENU,
    SSB_LCUT_FREQS,
    SSB_LCUT_SLOPE_MENU,
    SsbSlope,
)
from model import ExtendedSettings


# ----------------------------------------------------------------------
# Helfer
# ----------------------------------------------------------------------


def _freq_label(value) -> str:  # type: ignore[no-untyped-def]
    if isinstance(value, str):
        return "Aus"
    return f"{int(value)} Hz"


def _make_slider_row(
    label_text: str,
    minimum: int,
    maximum: int,
    default: int,
    tooltip: str,
) -> tuple[QLabel, QSlider, QLabel]:
    label = QLabel(label_text)
    slider = QSlider(Qt.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(default)
    slider.setSingleStep(1)
    slider.setPageStep(5)
    slider.setToolTip(tooltip)
    value_label = QLabel(str(default))
    value_label.setMinimumWidth(36)
    value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    slider.valueChanged.connect(lambda v, lbl=value_label: lbl.setText(str(v)))
    return label, slider, value_label


# ----------------------------------------------------------------------
# Editor
# ----------------------------------------------------------------------


class ExtendedSettingsWidget(QGroupBox):
    """Editor für die erweiterten Audio-Einstellungen."""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Erweiterte Einstellungen", parent)
        self._current_mode = "SSB"
        self._build_ui()
        self.apply_mode_relevance("SSB")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(10)

        outer.addWidget(self._build_ssb_group())
        outer.addWidget(self._build_am_group())
        outer.addWidget(self._build_fm_group())
        outer.addWidget(self._build_data_group())

    def _build_ssb_group(self) -> QGroupBox:
        self.ssb_box = QGroupBox("SSB-Klangformung (RX-Cut)")
        layout = QGridLayout(self.ssb_box)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        # Low Cut Freq
        layout.addWidget(QLabel("Low Cut Freq:"), 0, 0)
        self.lcut_freq = QComboBox()
        for value in SSB_LCUT_FREQS:
            self.lcut_freq.addItem(_freq_label(value), userData=value)
        self.lcut_freq.setToolTip(
            f"EX{SSB_LCUT_FREQ_MENU:03d} — SSB Low-Cut Filter Frequenz (50-Hz-Schritte)"
        )
        self.lcut_freq.currentIndexChanged.connect(self._emit_changed)
        layout.addWidget(self.lcut_freq, 0, 1)

        # Low Cut Slope
        layout.addWidget(QLabel("Low Cut Slope:"), 0, 2)
        self.lcut_slope = self._make_slope_combo()
        self.lcut_slope.setToolTip(f"EX{SSB_LCUT_SLOPE_MENU:03d} — Low-Cut Slope")
        layout.addWidget(self.lcut_slope, 0, 3)

        # High Cut Freq
        layout.addWidget(QLabel("High Cut Freq:"), 1, 0)
        self.hcut_freq = QComboBox()
        for value in SSB_HCUT_FREQS:
            self.hcut_freq.addItem(_freq_label(value), userData=value)
        self.hcut_freq.setToolTip(
            f"EX{SSB_HCUT_FREQ_MENU:03d} — SSB High-Cut Filter Frequenz (50-Hz-Schritte)"
        )
        self.hcut_freq.currentIndexChanged.connect(self._emit_changed)
        layout.addWidget(self.hcut_freq, 1, 1)

        # High Cut Slope
        layout.addWidget(QLabel("High Cut Slope:"), 1, 2)
        self.hcut_slope = self._make_slope_combo()
        self.hcut_slope.setToolTip(f"EX{SSB_HCUT_SLOPE_MENU:03d} — High-Cut Slope")
        layout.addWidget(self.hcut_slope, 1, 3)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        return self.ssb_box

    def _make_slope_combo(self) -> QComboBox:
        combo = QComboBox()
        for slope in (SsbSlope.DB6, SsbSlope.DB18):
            combo.addItem(slope.value, userData=slope.value)
        combo.currentIndexChanged.connect(self._emit_changed)
        return combo

    def _build_am_group(self) -> QGroupBox:
        self.am_box = QGroupBox("AM-Einstellungen")
        layout = QGridLayout(self.am_box)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        # AM Carrier Level (EX046 AM OUT LEVEL)
        lbl, slider, value_label = _make_slider_row(
            "AM Carrier:", CARRIER_LEVEL_MIN, CARRIER_LEVEL_MAX,
            CARRIER_LEVEL_DEFAULT,
            f"EX{AM_CARRIER_MENU:03d} — AM OUT LEVEL (0..100)",
        )
        self.am_carrier_slider = slider
        slider.valueChanged.connect(self._emit_changed)
        layout.addWidget(lbl, 0, 0)
        layout.addWidget(slider, 0, 1)
        layout.addWidget(value_label, 0, 2)

        # AM Mic Sel
        layout.addWidget(QLabel("AM Mikrofon:"), 1, 0)
        self.am_mic_combo = self._make_mic_combo()
        self.am_mic_combo.setToolTip(f"EX{AM_MIC_SEL_MENU:03d} — AM Mikrofon-Quelle")
        layout.addWidget(self.am_mic_combo, 1, 1, 1, 2)

        layout.setColumnStretch(1, 1)
        return self.am_box

    def _build_fm_group(self) -> QGroupBox:
        self.fm_box = QGroupBox("FM / C4FM-Einstellungen")
        layout = QGridLayout(self.fm_box)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        # FM Carrier Level (EX075 FM OUT LEVEL)
        lbl, slider, value_label = _make_slider_row(
            "FM Carrier:", CARRIER_LEVEL_MIN, CARRIER_LEVEL_MAX,
            CARRIER_LEVEL_DEFAULT,
            f"EX{FM_CARRIER_MENU:03d} — FM OUT LEVEL (0..100)",
        )
        self.fm_carrier_slider = slider
        slider.valueChanged.connect(self._emit_changed)
        layout.addWidget(lbl, 0, 0)
        layout.addWidget(slider, 0, 1)
        layout.addWidget(value_label, 0, 2)

        # FM Mic Sel
        layout.addWidget(QLabel("FM Mikrofon:"), 1, 0)
        self.fm_mic_combo = self._make_mic_combo()
        self.fm_mic_combo.setToolTip(f"EX{FM_MIC_SEL_MENU:03d} — FM Mikrofon-Quelle")
        layout.addWidget(self.fm_mic_combo, 1, 1, 1, 2)

        layout.setColumnStretch(1, 1)
        return self.fm_box

    def _make_mic_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Front-MIC", userData=MicSource.MIC.value)
        combo.addItem("Rear-DATA", userData=MicSource.REAR.value)
        combo.currentIndexChanged.connect(self._emit_changed)
        return combo

    def _build_data_group(self) -> QGroupBox:
        self.data_box = QGroupBox("DATA-Modus")
        layout = QHBoxLayout(self.data_box)
        layout.setSpacing(8)

        lbl, slider, value_label = _make_slider_row(
            "DATA TX-Level:", DATA_TX_LEVEL_MIN, DATA_TX_LEVEL_MAX,
            DATA_TX_LEVEL_DEFAULT,
            f"EX{DATA_TX_LEVEL_MENU:03d} — DATA OUT LEVEL (0..100)",
        )
        self.data_tx_slider = slider
        slider.valueChanged.connect(self._emit_changed)
        layout.addWidget(lbl)
        layout.addWidget(slider, 1)
        layout.addWidget(value_label)
        return self.data_box

    # ------------------------------------------------------------------
    # Get / Set
    # ------------------------------------------------------------------

    def get_values(self) -> ExtendedSettings:
        return ExtendedSettings(
            ssb_lcut_freq=self.lcut_freq.currentData(),
            ssb_lcut_slope=str(self.lcut_slope.currentData()),
            ssb_hcut_freq=self.hcut_freq.currentData(),
            ssb_hcut_slope=str(self.hcut_slope.currentData()),
            am_carrier_level=int(self.am_carrier_slider.value()),
            fm_carrier_level=int(self.fm_carrier_slider.value()),
            am_mic_sel=str(self.am_mic_combo.currentData()),
            fm_mic_sel=str(self.fm_mic_combo.currentData()),
            data_tx_level=int(self.data_tx_slider.value()),
        )

    def set_values(self, ext: ExtendedSettings) -> None:
        widgets = (
            self.lcut_freq, self.lcut_slope, self.hcut_freq, self.hcut_slope,
            self.am_carrier_slider, self.fm_carrier_slider,
            self.am_mic_combo, self.fm_mic_combo, self.data_tx_slider,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self._select_combo_by_data(self.lcut_freq, ext.ssb_lcut_freq, SSB_LCUT_FREQS[0])
            self._select_combo_by_data(self.lcut_slope, ext.ssb_lcut_slope, SsbSlope.DB6.value)
            self._select_combo_by_data(self.hcut_freq, ext.ssb_hcut_freq, SSB_HCUT_FREQS[0])
            self._select_combo_by_data(self.hcut_slope, ext.ssb_hcut_slope, SsbSlope.DB6.value)
            self.am_carrier_slider.setValue(int(ext.am_carrier_level))
            self.fm_carrier_slider.setValue(int(ext.fm_carrier_level))
            self._select_combo_by_data(self.am_mic_combo, ext.am_mic_sel, MicSource.MIC.value)
            self._select_combo_by_data(self.fm_mic_combo, ext.fm_mic_sel, MicSource.MIC.value)
            self.data_tx_slider.setValue(int(ext.data_tx_level))
            # Slider-Wert-Labels manuell aktualisieren (Signale waren blockiert)
            self._refresh_slider_labels()
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _refresh_slider_labels(self) -> None:
        # Slider/Label-Paare neu synchronisieren — die Verbindung war blockiert.
        for slider in (
            self.am_carrier_slider,
            self.fm_carrier_slider,
            self.data_tx_slider,
        ):
            slider.valueChanged.emit(slider.value())

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, data, fallback) -> None:  # type: ignore[no-untyped-def]
        idx = combo.findData(data)
        if idx < 0:
            # Falls ein exotischer Wert (z. B. aus einer Firmware-Diskrepanz)
            # nicht in der Tabelle ist, fügen wir ihn als zusätzlichen Eintrag
            # an, damit der Wert nicht still verloren geht.
            combo.addItem(f"{data!r}", userData=data)
            idx = combo.findData(data)
        if idx < 0:
            idx = combo.findData(fallback)
        combo.setCurrentIndex(max(0, idx))

    # ------------------------------------------------------------------
    # Mode-Relevanz
    # ------------------------------------------------------------------

    def apply_mode_relevance(self, mode_group: str) -> None:
        """Versteckt Sub-Gruppen, die für ``mode_group`` nicht relevant sind.

        Die Werte bleiben im UI-Zustand erhalten — wechselt der User später
        wieder in eine passende Mode-Gruppe, ist alles noch da.
        """
        mg = mode_group.upper()
        self._current_mode = mg
        self.ssb_box.setVisible(mg in ("SSB", "DATA"))
        self.am_box.setVisible(mg == "AM")
        self.fm_box.setVisible(mg in ("FM", "C4FM"))
        self.data_box.setVisible(mg == "DATA")

    # ------------------------------------------------------------------
    # Signal-Handling
    # ------------------------------------------------------------------

    def _emit_changed(self, *_args) -> None:  # type: ignore[no-untyped-def]
        self.changed.emit()
