"""Einstellungsblock: S-Meter-Kalibrierung — Kurzwelle vs. 2 m / 70 cm."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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
        self._rows: List[Tuple[QSpinBox, QComboBox]] = []
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
            raw_spin.setToolTip("Rohwert aus der CAT-Antwort SM0nnn; (000…255)")
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
            form.addRow(f"Punkt {idx + 1}:", row_w)
            self._rows.append((raw_spin, stage))
        outer.addLayout(form)
        self.load_points(points)

    def load_points(self, pts: List[SmeterCalibrationPoint]) -> None:
        defaults = self._suggested
        if len(pts) >= 4:
            fill = pts[:4]
        else:
            fill = [pts[i] if i < len(pts) else defaults[i] for i in range(4)]
        for (raw_spin, stage), pt in zip(self._rows, fill):
            raw_spin.setValue(max(0, min(255, int(pt.raw))))
            stage.setCurrentIndex(_combo_index_for_db(stage, float(pt.db_over_s9)))

    def collect_points(self) -> List[SmeterCalibrationPoint]:
        out: List[SmeterCalibrationPoint] = []
        for raw_spin, stage in self._rows:
            r = int(raw_spin.value())
            db_data = stage.currentData()
            db = float(db_data) if isinstance(db_data, (int, float)) else 0.0
            out.append(SmeterCalibrationPoint(raw=r, db_over_s9=db))
        return out

    def set_enabled_editor(self, on: bool) -> None:
        for raw_spin, stage in self._rows:
            raw_spin.setEnabled(on)
            stage.setEnabled(on)

    def fill_suggested(self) -> None:
        for (raw_spin, stage), pt in zip(self._rows, self._suggested):
            raw_spin.setValue(pt.raw)
            stage.setCurrentIndex(_combo_index_for_db(stage, pt.db_over_s9))


class SmeterCalibrationSettingsWidget(QWidget):
    """Zwei Tabs: HF &lt; 50 MHz und VHF/UHF ab 50 MHz (2 m / 70 cm)."""

    def __init__(
        self,
        settings: SmeterCalibrationSettings,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        mhz = SMETER_CALIB_VHF_MIN_HZ // 1_000_000
        outer.addWidget(
            hint_label(
                f"Zwei getrennte Kurven: Unter {mhz} MHz (Kurzwelle inkl. 6 m) und ab "
                f"{mhz} MHz (2 m / 70 cm). Die App wählt anhand der aktuellen VFO-A-Frequenz. "
                "Je vier Stützpunkte mit steigendem SM0-Rohwert (mindestens zwei gültige "
                "Rohwerte pro Band, sonst Werkstabelle für dieses Band)."
            )
        )
        self._enable = QCheckBox("Eigene S-Meter-Kalibrierung verwenden")
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
        self._tabs.addTab(self._editor_hf, f"Kurzwelle (< {mhz} MHz)")
        self._tabs.addTab(self._editor_vhf, f"2 m / 70 cm (≥ {mhz} MHz)")
        outer.addWidget(self._tabs)

        btn_row = QHBoxLayout()
        btn_hf = QPushButton("Vorschläge (Kurzwelle)")
        btn_hf.setToolTip("Programm-Standard nur im Tab Kurzwelle einsetzen.")
        btn_hf.clicked.connect(self._editor_hf.fill_suggested)
        btn_vhf = QPushButton("Vorschläge (2 m / 70 cm)")
        btn_vhf.setToolTip("Programm-Standard nur im Tab 2 m / 70 cm einsetzen.")
        btn_vhf.clicked.connect(self._editor_vhf.fill_suggested)
        btn_row.addWidget(btn_hf)
        btn_row.addWidget(btn_vhf)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)
        self._on_toggled(self._enable.isChecked())

    def _on_toggled(self, on: bool) -> None:
        self._editor_hf.set_enabled_editor(on)
        self._editor_vhf.set_enabled_editor(on)

    def apply_to_settings(self, target: SmeterCalibrationSettings) -> None:
        target.use_custom = bool(self._enable.isChecked())
        target.points_hf = self._editor_hf.collect_points()
        target.points_vhf = self._editor_vhf.collect_points()
