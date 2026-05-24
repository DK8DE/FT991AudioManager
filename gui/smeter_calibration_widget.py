"""Einstellungsblock: S-Meter-Kalibrierung — Kurzwelle vs. 2 m / 70 cm."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from mapping.meter_mapping import SMETER_CALIB_VHF_MIN_HZ

from .settings_layout import fix_spin_width, hint_label
from model.smeter_calibration_settings import (
    SmeterCalibrationPoint,
    SmeterCalibrationSettings,
    default_smeter_calibration_points_hf,
    default_smeter_calibration_points_vhf,
)


def smeter_stage_choices() -> List[Tuple[str, float]]:
    """Anzeige-Labels und zugehöriges dB über S9 (S0..S9, dann S9+1 .. S9+60)."""
    rows: List[Tuple[str, float]] = []
    for s in range(10):
        db = float((s - 9) * 6)
        rows.append((f"S{s}", db))
    for db in range(1, 61):
        rows.append((f"S9+{db}", float(db)))
    return rows


def _combo_index_for_db(combo: QComboBox, db: float) -> int:
    best_i = 0
    best_err = 1e9
    for i in range(combo.count()):
        d = combo.itemData(i)
        if not isinstance(d, (int, float)):
            continue
        err = abs(float(d) - db)
        if err < best_err:
            best_err = err
            best_i = i
    return best_i


class _SmeterBandPointEditor(QWidget):
    """Vier Stützpunkte (Rohwert + S-Stufe) für eine Bandgruppe."""

    def __init__(
        self,
        *,
        points: List[SmeterCalibrationPoint],
        suggested_points: List[SmeterCalibrationPoint],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._suggested = list(suggested_points)
        self._rows: List[Tuple[QSpinBox, QComboBox, QLabel]] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for idx in range(4):
            raw_spin = QSpinBox()
            raw_spin.setRange(0, 255)
            fix_spin_width(raw_spin, 60)
            stage = QComboBox()
            stage.setMaxVisibleItems(20)
            for lab, db in smeter_stage_choices():
                stage.addItem(lab, userData=db)
            stage.setMinimumWidth(90)
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(8)
            row_l.addWidget(raw_spin)
            row_l.addWidget(stage, stretch=1)
            point_lbl = QLabel()
            form.addRow(point_lbl, row_w)
            self._rows.append((raw_spin, stage, point_lbl))
        outer.addLayout(form)
        self._form = form
        self.load_points(points)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        for idx, (raw_spin, _stage, point_lbl) in enumerate(self._rows):
            point_lbl.setText(tr("smeter_cal.point", n=idx + 1))
            raw_spin.setToolTip(tr("smeter_cal.raw_tooltip"))

    def load_points(self, pts: List[SmeterCalibrationPoint]) -> None:
        defaults = self._suggested
        if len(pts) >= 4:
            fill = pts[:4]
        else:
            fill = [pts[i] if i < len(pts) else defaults[i] for i in range(4)]
        for (raw_spin, stage, _lbl), pt in zip(self._rows, fill):
            raw_spin.setValue(max(0, min(255, int(pt.raw))))
            stage.setCurrentIndex(_combo_index_for_db(stage, float(pt.db_over_s9)))

    def collect_points(self) -> List[SmeterCalibrationPoint]:
        out: List[SmeterCalibrationPoint] = []
        for raw_spin, stage, _lbl in self._rows:
            r = int(raw_spin.value())
            db_data = stage.currentData()
            db = float(db_data) if isinstance(db_data, (int, float)) else 0.0
            out.append(SmeterCalibrationPoint(raw=r, db_over_s9=db))
        return out

    def set_enabled_editor(self, on: bool) -> None:
        for raw_spin, stage, _lbl in self._rows:
            raw_spin.setEnabled(on)
            stage.setEnabled(on)

    def fill_suggested(self) -> None:
        for (raw_spin, stage, _lbl), pt in zip(self._rows, self._suggested):
            raw_spin.setValue(pt.raw)
            stage.setCurrentIndex(_combo_index_for_db(stage, pt.db_over_s9))


class SmeterCalibrationSettingsWidget(RetranslatableMixin, QWidget):
    """Zwei Tabs: HF &lt; 50 MHz und VHF/UHF ab 50 MHz (2 m / 70 cm)."""

    def __init__(
        self,
        settings: SmeterCalibrationSettings,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._mhz = SMETER_CALIB_VHF_MIN_HZ // 1_000_000
        self._build_ui()
        self._register_retranslate()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self._hint = hint_label("")
        outer.addWidget(self._hint)
        self._enable = QCheckBox()
        self._enable.setChecked(self._settings.use_custom)
        self._enable.toggled.connect(self._on_toggled)
        outer.addWidget(self._enable)

        self._tabs = QTabWidget()
        self._editor_hf = _SmeterBandPointEditor(
            points=list(self._settings.points_hf),
            suggested_points=default_smeter_calibration_points_hf(),
        )
        self._editor_vhf = _SmeterBandPointEditor(
            points=list(self._settings.points_vhf),
            suggested_points=default_smeter_calibration_points_vhf(),
        )
        self._tabs.addTab(self._editor_hf, "")
        self._tabs.addTab(self._editor_vhf, "")
        outer.addWidget(self._tabs)

        btn_row = QHBoxLayout()
        self._btn_hf = QPushButton()
        self._btn_hf.clicked.connect(self._editor_hf.fill_suggested)
        self._btn_vhf = QPushButton()
        self._btn_vhf.clicked.connect(self._editor_vhf.fill_suggested)
        btn_row.addWidget(self._btn_hf)
        btn_row.addWidget(self._btn_vhf)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)
        self._on_toggled(self._enable.isChecked())

    def retranslate_ui(self) -> None:
        self._hint.setText(tr("smeter_cal.hint", mhz=self._mhz))
        self._enable.setText(tr("smeter_cal.enable"))
        self._tabs.setTabText(0, tr("smeter_cal.tab_hf", mhz=self._mhz))
        self._tabs.setTabText(1, tr("smeter_cal.tab_vhf", mhz=self._mhz))
        self._btn_hf.setText(tr("smeter_cal.suggest_hf"))
        self._btn_hf.setToolTip(tr("smeter_cal.suggest_hf_tooltip"))
        self._btn_vhf.setText(tr("smeter_cal.suggest_vhf"))
        self._btn_vhf.setToolTip(tr("smeter_cal.suggest_vhf_tooltip"))
        self._editor_hf.retranslate_ui()
        self._editor_vhf.retranslate_ui()

    def _on_toggled(self, on: bool) -> None:
        self._editor_hf.set_enabled_editor(on)
        self._editor_vhf.set_enabled_editor(on)

    def apply_to_settings(self, target: SmeterCalibrationSettings) -> None:
        target.use_custom = bool(self._enable.isChecked())
        target.points_hf = self._editor_hf.collect_points()
        target.points_vhf = self._editor_vhf.collect_points()
