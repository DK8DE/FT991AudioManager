"""Tests für PO-Kalibrierungs-Persistenz und TX-Leistungs-Kodierung."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mapping.meter_mapping import (
    CALIB_BAND_HF,
    PO_WATTS_CALIB_HF_DEFAULT,
    apply_po_calibration_watt_raw,
    calib_band_id_for_freq,
    format_po_watts,
    po_raw_to_watts,
)
from mapping.tx_power_mapping import encode_tx_power_menu, power_steps_watts
from model.po_calibration_store import (
    CalPoint,
    PoCalibrationFile,
    merge_band_points,
    save_po_calibration,
)


class TxPowerMappingTest(unittest.TestCase):
    def test_encode_power(self) -> None:
        self.assertEqual(encode_tx_power_menu(5), "005")
        self.assertEqual(encode_tx_power_menu(100), "100")

    def test_power_steps_hf(self) -> None:
        self.assertEqual(power_steps_watts(max_w=100), [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100])

    def test_power_steps_vhf(self) -> None:
        self.assertEqual(power_steps_watts(max_w=50)[-1], 50)


class PoCalibrationStoreTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        cal = PoCalibrationFile()
        cal = merge_band_points(
            cal,
            band_id="hf_10m",
            label="10 m",
            freq_hz=28_500_000,
            mode="FM",
            points=[CalPoint(5, 34), CalPoint(100, 207)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "po_calibration.json"
            with patch(
                "model.po_calibration_store.calibration_json_path",
                return_value=path,
            ):
                save_po_calibration(cal)
                data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("hf_10m", data["bands"])
        self.assertEqual(data["bands"]["hf_10m"]["points"][0]["watts"], 5)

    def test_apply_to_meter(self) -> None:
        apply_po_calibration_watt_raw({"hf_10m": [(10, 50), (20, 100), (50, 149)]})
        self.assertEqual(format_po_watts(50, freq_hz=14_000_000), "10 W")
        self.assertEqual(format_po_watts(50, freq_hz=145_500_000), "10 W")
        self.assertEqual(format_po_watts(149, freq_hz=432_100_000), "50 W")
        self.assertEqual(calib_band_id_for_freq(432_100_000), CALIB_BAND_HF)
        d = [(w, r) for r, w in PO_WATTS_CALIB_HF_DEFAULT if w > 0]
        apply_po_calibration_watt_raw({"hf_10m": d})
        self.assertAlmostEqual(po_raw_to_watts(207, freq_hz=28_500_000), 100.0, places=1)
        self.assertAlmostEqual(po_raw_to_watts(207, freq_hz=432_100_000), 50.0, places=1)

    def test_hf_curve_used_on_vhf(self) -> None:
        from model.po_calibration_store import load_po_calibration

        cal = load_po_calibration()
        pairs = cal.watt_raw_pairs("hf_10m")
        if len(pairs) < 2:
            pairs = [(w, r) for r, w in PO_WATTS_CALIB_HF_DEFAULT if w > 0]
        apply_po_calibration_watt_raw({"hf_10m": pairs})
        w_116 = po_raw_to_watts(116, freq_hz=432_100_000)
        self.assertGreater(w_116, 15.0)
        self.assertLess(w_116, 45.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
