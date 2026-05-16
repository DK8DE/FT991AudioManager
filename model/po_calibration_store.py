"""Persistenz der PO-Kalibrierung (``po_calibration.json`` im User-Datenordner)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from model._app_paths import app_data_dir


@dataclass
class CalPoint:
    watts: int
    raw: int


@dataclass
class BandCalibration:
    band_id: str
    label: str
    freq_hz: int
    mode: str
    points: List[CalPoint] = field(default_factory=list)
    measured_at: Optional[str] = None


@dataclass
class PoCalibrationFile:
    version: int = 1
    bands: Dict[str, BandCalibration] = field(default_factory=dict)

    def watt_raw_pairs(self, band_id: str) -> List[Tuple[int, int]]:
        """Kalibrierpunkte als ``(eingestellte_watt, rm5_rohwert)``."""
        band = self.bands.get(band_id)
        if band is None or not band.points:
            return []
        return sorted((p.watts, p.raw) for p in band.points)

    def to_meter_points(self, band_id: str) -> List[Tuple[int, int]]:
        """Legacy: ``(rohwert, watt)`` — nur noch für alte Aufrufer."""
        return [(0, 0)] + sorted(
            (r, w) for w, r in self.watt_raw_pairs(band_id) if w > 0
        )

    def to_meter_hf_points(self) -> List[Tuple[int, int]]:
        return self.to_meter_points("hf_10m")

    def to_meter_vhf_points(self) -> List[Tuple[int, int]]:
        return self.to_meter_points("vhf_2m")

    def to_meter_uhf_points(self) -> List[Tuple[int, int]]:
        return self.to_meter_points("uhf_70cm")


def calibration_json_path() -> Path:
    return app_data_dir() / "po_calibration.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_po_calibration() -> PoCalibrationFile:
    path = calibration_json_path()
    if not path.is_file():
        return PoCalibrationFile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PoCalibrationFile()
    return _decode_file(data)


def save_po_calibration(cal: PoCalibrationFile) -> Path:
    path = calibration_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_encode_file(cal), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def merge_band_points(
    cal: PoCalibrationFile,
    *,
    band_id: str,
    label: str,
    freq_hz: int,
    mode: str,
    points: List[CalPoint],
) -> PoCalibrationFile:
    cal.bands[band_id] = BandCalibration(
        band_id=band_id,
        label=label,
        freq_hz=freq_hz,
        mode=mode,
        points=list(points),
        measured_at=_utc_now_iso(),
    )
    return cal


def _encode_file(cal: PoCalibrationFile) -> Dict[str, Any]:
    return {
        "version": cal.version,
        "bands": {
            bid: {
                "band_id": b.band_id,
                "label": b.label,
                "freq_hz": b.freq_hz,
                "mode": b.mode,
                "measured_at": b.measured_at,
                "points": [{"watts": p.watts, "raw": p.raw} for p in b.points],
            }
            for bid, b in cal.bands.items()
        },
    }


def _decode_file(data: Any) -> PoCalibrationFile:
    if not isinstance(data, dict):
        return PoCalibrationFile()
    bands_raw = data.get("bands")
    bands: Dict[str, BandCalibration] = {}
    if isinstance(bands_raw, dict):
        for bid, entry in bands_raw.items():
            if not isinstance(entry, dict):
                continue
            pts_raw = entry.get("points")
            points: List[CalPoint] = []
            if isinstance(pts_raw, list):
                for pt in pts_raw:
                    if not isinstance(pt, dict):
                        continue
                    try:
                        points.append(
                            CalPoint(
                                watts=int(pt["watts"]),
                                raw=int(pt["raw"]),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
            bands[str(bid)] = BandCalibration(
                band_id=str(entry.get("band_id", bid)),
                label=str(entry.get("label", bid)),
                freq_hz=int(entry.get("freq_hz", 0) or 0),
                mode=str(entry.get("mode", "")),
                points=points,
                measured_at=entry.get("measured_at"),
            )
    return PoCalibrationFile(
        version=int(data.get("version", 1)),
        bands=bands,
    )
