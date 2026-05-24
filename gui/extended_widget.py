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

from typing import Dict, List, Optional, Tuple

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

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
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
        return tr("common.off")
    return tr("common.freq_hz", value=int(value))


def _make_slider_row(
    label: QLabel,
    minimum: int,
    maximum: int,
    default: int,
    tooltip: str,
) -> tuple[QSlider, QLabel]:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(default)
    slider.setSingleStep(1)
    slider.setPageStep(5)
    slider.setToolTip(tooltip)
    value_label = QLabel(str(default))
    value_label.setMinimumWidth(36)
    value_label.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    slider.valueChanged.connect(lambda v, lbl=value_label: lbl.setText(str(v)))
    return slider, value_label


# ----------------------------------------------------------------------
# Editor
# ----------------------------------------------------------------------


class ExtendedSettingsWidget(RetranslatableMixin, QGroupBox):
    """Editor für die erweiterten Audio-Einstellungen."""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_mode = "SSB"
        self._slider_tooltips: Dict[QSlider, str] = {}
        self._build_ui()
        self.apply_mode_relevance("SSB")
        self._register_retranslate()
        self.retranslate_ui()

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
        self.ssb_box = QGroupBox()
        layout = QGridLayout(self.ssb_box)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self._lcut_freq_lbl = QLabel()
        layout.addWidget(self._lcut_freq_lbl, 0, 0)
        self.lcut_freq = QComboBox()
        for value in SSB_LCUT_FREQS:
            self.lcut_freq.addItem(_freq_label(value), userData=value)
        self.lcut_freq.currentIndexChanged.connect(self._emit_changed)
        layout.addWidget(self.lcut_freq, 0, 1)

        self._lcut_slope_lbl = QLabel()
        layout.addWidget(self._lcut_slope_lbl, 0, 2)
        self.lcut_slope = self._make_slope_combo()
        layout.addWidget(self.lcut_slope, 0, 3)

        self._hcut_freq_lbl = QLabel()
        layout.addWidget(self._hcut_freq_lbl, 1, 0)
        self.hcut_freq = QComboBox()
        for value in SSB_HCUT_FREQS:
            self.hcut_freq.addItem(_freq_label(value), userData=value)
        self.hcut_freq.currentIndexChanged.connect(self._emit_changed)
        layout.addWidget(self.hcut_freq, 1, 1)

        self._hcut_slope_lbl = QLabel()
        layout.addWidget(self._hcut_slope_lbl, 1, 2)
        self.hcut_slope = self._make_slope_combo()
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
        self.am_box = QGroupBox()
        layout = QGridLayout(self.am_box)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self._am_carrier_lbl = QLabel()
        self.am_carrier_slider, am_value_label = _make_slider_row(
            self._am_carrier_lbl,
            CARRIER_LEVEL_MIN,
            CARRIER_LEVEL_MAX,
            CARRIER_LEVEL_DEFAULT,
            "",
        )
        self._slider_tooltips[self.am_carrier_slider] = ""
        self.am_carrier_slider.valueChanged.connect(self._emit_changed)
        layout.addWidget(self._am_carrier_lbl, 0, 0)
        layout.addWidget(self.am_carrier_slider, 0, 1)
        layout.addWidget(am_value_label, 0, 2)

        self._am_mic_lbl = QLabel()
        layout.addWidget(self._am_mic_lbl, 1, 0)
        self.am_mic_combo = self._make_mic_combo()
        layout.addWidget(self.am_mic_combo, 1, 1, 1, 2)

        layout.setColumnStretch(1, 1)
        return self.am_box

    def _build_fm_group(self) -> QGroupBox:
        self.fm_box = QGroupBox()
        layout = QGridLayout(self.fm_box)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self._fm_carrier_lbl = QLabel()
        self.fm_carrier_slider, fm_value_label = _make_slider_row(
            self._fm_carrier_lbl,
            CARRIER_LEVEL_MIN,
            CARRIER_LEVEL_MAX,
            CARRIER_LEVEL_DEFAULT,
            "",
        )
        self._slider_tooltips[self.fm_carrier_slider] = ""
        self.fm_carrier_slider.valueChanged.connect(self._emit_changed)
        layout.addWidget(self._fm_carrier_lbl, 0, 0)
        layout.addWidget(self.fm_carrier_slider, 0, 1)
        layout.addWidget(fm_value_label, 0, 2)

        self._fm_mic_lbl = QLabel()
        layout.addWidget(self._fm_mic_lbl, 1, 0)
        self.fm_mic_combo = self._make_mic_combo()
        layout.addWidget(self.fm_mic_combo, 1, 1, 1, 2)

        layout.setColumnStretch(1, 1)
        return self.fm_box

    def _make_mic_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem("", userData=MicSource.MIC.value)
        combo.addItem("", userData=MicSource.REAR.value)
        combo.currentIndexChanged.connect(self._emit_changed)
        return combo

    def _refresh_mic_combo(self, combo: QComboBox) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(tr("extended.mic.front"), userData=MicSource.MIC.value)
            combo.addItem(tr("extended.mic.rear"), userData=MicSource.REAR.value)
            idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
        finally:
            combo.blockSignals(False)

    def _build_data_group(self) -> QGroupBox:
        self.data_box = QGroupBox()
        layout = QHBoxLayout(self.data_box)
        layout.setSpacing(8)

        self._data_tx_lbl = QLabel()
        self.data_tx_slider, data_value_label = _make_slider_row(
            self._data_tx_lbl,
            DATA_TX_LEVEL_MIN,
            DATA_TX_LEVEL_MAX,
            DATA_TX_LEVEL_DEFAULT,
            "",
        )
        self._slider_tooltips[self.data_tx_slider] = ""
        self.data_tx_slider.valueChanged.connect(self._emit_changed)
        layout.addWidget(self._data_tx_lbl)
        layout.addWidget(self.data_tx_slider, 1)
        layout.addWidget(data_value_label)
        return self.data_box

    def retranslate_ui(self) -> None:
        self.setTitle(tr("extended.title"))
        self.ssb_box.setTitle(tr("extended.ssb_group"))
        self.am_box.setTitle(tr("extended.am_group"))
        self.fm_box.setTitle(tr("extended.fm_group"))
        self.data_box.setTitle(tr("extended.data_group"))

        self._lcut_freq_lbl.setText(tr("extended.lcut_freq"))
        self._lcut_slope_lbl.setText(tr("extended.lcut_slope"))
        self._hcut_freq_lbl.setText(tr("extended.hcut_freq"))
        self._hcut_slope_lbl.setText(tr("extended.hcut_slope"))
        self._am_carrier_lbl.setText(tr("extended.am_carrier"))
        self._am_mic_lbl.setText(tr("extended.am_mic"))
        self._fm_carrier_lbl.setText(tr("extended.fm_carrier"))
        self._fm_mic_lbl.setText(tr("extended.fm_mic"))
        self._data_tx_lbl.setText(tr("extended.data_tx_level"))

        self.lcut_freq.setToolTip(
            tr("extended.tooltip.ssb_lcut_freq", menu=SSB_LCUT_FREQ_MENU)
        )
        self.lcut_slope.setToolTip(
            tr("extended.tooltip.ssb_lcut_slope", menu=SSB_LCUT_SLOPE_MENU)
        )
        self.hcut_freq.setToolTip(
            tr("extended.tooltip.ssb_hcut_freq", menu=SSB_HCUT_FREQ_MENU)
        )
        self.hcut_slope.setToolTip(
            tr("extended.tooltip.ssb_hcut_slope", menu=SSB_HCUT_SLOPE_MENU)
        )
        self.am_mic_combo.setToolTip(
            tr("extended.tooltip.am_mic", menu=AM_MIC_SEL_MENU)
        )
        self.fm_mic_combo.setToolTip(
            tr("extended.tooltip.fm_mic", menu=FM_MIC_SEL_MENU)
        )

        self._slider_tooltips[self.am_carrier_slider] = tr(
            "extended.tooltip.am_carrier", menu=AM_CARRIER_MENU
        )
        self._slider_tooltips[self.fm_carrier_slider] = tr(
            "extended.tooltip.fm_carrier", menu=FM_CARRIER_MENU
        )
        self._slider_tooltips[self.data_tx_slider] = tr(
            "extended.tooltip.data_tx_level", menu=DATA_TX_LEVEL_MENU
        )
        for slider, tip in self._slider_tooltips.items():
            slider.setToolTip(tip)

        self._refresh_freq_combo(self.lcut_freq, SSB_LCUT_FREQS)
        self._refresh_freq_combo(self.hcut_freq, SSB_HCUT_FREQS)
        self._refresh_mic_combo(self.am_mic_combo)
        self._refresh_mic_combo(self.fm_mic_combo)

    def _refresh_freq_combo(self, combo: QComboBox, values: tuple) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        try:
            combo.clear()
            for value in values:
                combo.addItem(_freq_label(value), userData=value)
            idx = combo.findData(current)
            if idx < 0 and current is not None:
                combo.addItem(f"{current!r}", userData=current)
                idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
        finally:
            combo.blockSignals(False)

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
            self._refresh_slider_labels()
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _refresh_slider_labels(self) -> None:
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
            combo.addItem(f"{data!r}", userData=data)
            idx = combo.findData(data)
        if idx < 0:
            idx = combo.findData(fallback)
        combo.setCurrentIndex(max(0, idx))

    # ------------------------------------------------------------------
    # Mode-Relevanz
    # ------------------------------------------------------------------

    def apply_mode_relevance(self, mode_group: str) -> None:
        """Versteckt Sub-Gruppen, die für ``mode_group`` nicht relevant sind."""
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
