"""Einstellungen für S-Meter-Kurven (SM0-Rohwert → Skala), getrennt HF / VHF."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SmeterCalibrationPoint:
    """Ein Stützpunkt: ``SM0``-Rohwert und zugehörige Anzeige als dB über S9."""

    raw: int
    db_over_s9: float


def default_smeter_calibration_points_hf() -> List[SmeterCalibrationPoint]:
    """Programm-Standard Kurzwelle (VFO unter 50 MHz): S3/S9/S9+20/S9+60 als SM0-Rohwerte."""
    return [
        SmeterCalibrationPoint(60, -36.0),  # S3
        SmeterCalibrationPoint(133, 0.0),  # S9
        SmeterCalibrationPoint(160, 20.0),  # S9+20
        SmeterCalibrationPoint(170, 60.0),  # S9+60
    ]


def default_smeter_calibration_points_vhf() -> List[SmeterCalibrationPoint]:
    """Programm-Standard 2 m / 70 cm (≥ 50 MHz): bisherige Werte."""
    return [
        SmeterCalibrationPoint(44, -36.0),  # S3
        SmeterCalibrationPoint(124, 0.0),  # S9
        SmeterCalibrationPoint(134, 20.0),  # S9+20
        SmeterCalibrationPoint(165, 60.0),  # S9+60
    ]


def _parse_point_list(raw: Any) -> List[SmeterCalibrationPoint]:
    out: List[SmeterCalibrationPoint] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            r = int(item["raw"])
            db = float(item["db_over_s9"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(SmeterCalibrationPoint(raw=r, db_over_s9=db))
    return out


def _normalize_points(points: List[SmeterCalibrationPoint]) -> List[SmeterCalibrationPoint]:
    by_raw: Dict[int, float] = {}
    for p in points:
        r = max(0, min(255, int(p.raw)))
        by_raw[r] = float(p.db_over_s9)
    return [
        SmeterCalibrationPoint(raw=r, db_over_s9=by_raw[r])
        for r in sorted(by_raw.keys())
    ]


@dataclass
class SmeterCalibrationSettings:
    """Zwei Kurven: Kurzwelle (unter Grenzfrequenz) und 2 m / 70 cm (ab Grenzfrequenz).

    Die Grenzfrequenz liegt in :data:`~mapping.meter_mapping.SMETER_CALIB_VHF_MIN_HZ`
    (50 MHz): darunter HF-Kurve, ab 50 MHz die VHF/UHF-Kurve.
    """

    use_custom: bool = True
    points_hf: List[SmeterCalibrationPoint] = field(
        default_factory=default_smeter_calibration_points_hf
    )
    points_vhf: List[SmeterCalibrationPoint] = field(
        default_factory=default_smeter_calibration_points_vhf
    )

    def effective_points_hf(self) -> List[SmeterCalibrationPoint]:
        pts = _normalize_points(self.points_hf)
        return pts if len(pts) >= 2 else []

    def effective_points_vhf(self) -> List[SmeterCalibrationPoint]:
        pts = _normalize_points(self.points_vhf)
        return pts if len(pts) >= 2 else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_custom": self.use_custom,
            "points_hf": [
                {"raw": p.raw, "db_over_s9": p.db_over_s9} for p in self.points_hf
            ],
            "points_vhf": [
                {"raw": p.raw, "db_over_s9": p.db_over_s9} for p in self.points_vhf
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> SmeterCalibrationSettings:
        if not isinstance(data, dict):
            return cls()
        if data == {}:
            return cls()
        use_custom = bool(data.get("use_custom", False))
        hf = _parse_point_list(data.get("points_hf"))
        vhf = _parse_point_list(data.get("points_vhf"))
        legacy = _parse_point_list(data.get("points"))
        if legacy and not hf and not vhf:
            hf = list(legacy)
            vhf = list(legacy)
        elif legacy:
            if not hf:
                hf = list(legacy)
            if not vhf:
                vhf = list(legacy)
        if not hf:
            hf = list(default_smeter_calibration_points_hf())
        if not vhf:
            vhf = list(default_smeter_calibration_points_vhf())
        return cls(use_custom=use_custom, points_hf=hf, points_vhf=vhf)
