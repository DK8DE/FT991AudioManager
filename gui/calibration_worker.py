"""Hintergrund-Worker für die PO-Meter-Kalibrierung (nur 10 m / Kurzwelle)."""

from __future__ import annotations

import time
from typing import List, Optional

from PySide6.QtCore import QObject, Signal, Slot

from cat import CatConnectionLostError, CatError, FT991CAT, SerialCAT
from mapping.calibration_bands import (
    HF_10M_BAND,
    PAUSE_BETWEEN_STEPS_S,
    SETTLE_AFTER_POWER_S,
    SETTLE_BEFORE_TX_S,
    TX_ON_SECONDS,
)
from mapping.meter_mapping import (
    CALIB_BAND_HF,
    MeterKind,
    _watt_raw_usable,
    apply_po_calibration_watt_raw,
)
from model.po_calibration_store import (
    CalPoint,
    PoCalibrationFile,
    merge_band_points,
    save_po_calibration,
)


def _points_usable(points: List[CalPoint]) -> bool:
    return _watt_raw_usable([(p.watts, p.raw) for p in points])


class CalibrationWorker(QObject):
    """Kalibriert die POWER-Skala auf 10 m (KW, 5–100 W)."""

    log_line = Signal(str)
    progress = Signal(int, int, str)
    band_points = Signal(str, object)
    finished_ok = Signal(object)
    failed = Signal(str)
    connection_lost = Signal()

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        hf_freq_hz: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._cat = serial_cat
        self._hf_freq_hz = hf_freq_hz
        self._stop = False

    @Slot()
    def stop(self) -> None:
        self._stop = True

    @Slot()
    def run(self) -> None:
        ft = FT991CAT(self._cat)
        spec = HF_10M_BAND
        freq = self._hf_freq_hz if self._hf_freq_hz else spec.freq_hz
        total = len(spec.power_steps())

        try:
            self.log_line.emit(
                f"=== {spec.label}: {freq / 1e6:.4f} MHz, {spec.mode.value} ==="
            )
            points = self._run_band(ft, spec, freq_hz=freq, total=total)
            if self._stop:
                self.failed.emit("Abgebrochen.")
                return
            if not _points_usable(points):
                raise CatError(
                    f"{spec.label}: Kalibrierung fehlgeschlagen — der PO-Rohwert (RM5) "
                    f"ändert sich nicht mit der Leistung."
                )

            cal = PoCalibrationFile()
            cal = merge_band_points(
                cal,
                band_id=spec.band_id,
                label=spec.label,
                freq_hz=freq,
                mode=spec.mode.value,
                points=points,
            )
            self.band_points.emit(spec.band_id, points)
            self.log_line.emit(f"{spec.label}: {len(points)} Messpunkte gespeichert.")

            path = save_po_calibration(cal)
            apply_po_calibration_watt_raw({CALIB_BAND_HF: cal.watt_raw_pairs(CALIB_BAND_HF)})
            self.log_line.emit(f"Kalibrierung gespeichert: {path}")
            self.log_line.emit(
                "Hinweis: Die POWER-Anzeige nutzt diese 10-m-Kurve auf allen Bändern "
                "(VHF/UHF-Skala weiterhin 0–50 W)."
            )
            self.finished_ok.emit(cal)
        except CatConnectionLostError:
            self._safe_tx_off(ft)
            self.connection_lost.emit()
        except CatError as exc:
            self._safe_tx_off(ft)
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self._safe_tx_off(ft)
            self.failed.emit(str(exc))

    def _run_band(
        self,
        ft: FT991CAT,
        spec,
        *,
        freq_hz: int,
        total: int,
    ) -> List[CalPoint]:
        if not ft.set_rx_mode(spec.mode, verify=True):
            raise CatError(f"Modus {spec.mode.value} konnte nicht gesetzt werden.")
        time.sleep(0.15)
        ft.write_frequency(freq_hz)
        time.sleep(0.25)

        ft.set_tx_max_power_watts(spec.power_menu, spec.power_max_w)
        time.sleep(0.2)
        ex_max = ft.read_tx_max_power_watts(spec.power_menu)
        self.log_line.emit(
            f"  EX{spec.power_menu:03d} TX MAX = {ex_max} W (Soll {spec.power_max_w} W)"
        )

        points: List[CalPoint] = []
        for watts in spec.power_steps():
            if self._stop:
                break
            self.progress.emit(
                len(points),
                total,
                f"{spec.label}: {watts} W …",
            )
            ft.set_pc_power_watts(watts, max_watts=spec.power_max_w)
            time.sleep(SETTLE_AFTER_POWER_S)
            pc_read = ft.read_pc_power_watts()
            if pc_read != watts:
                self.log_line.emit(
                    f"  WARNUNG: PC gelesen {pc_read} W, gewünscht {watts} W"
                )

            raw = self._measure_po_at_power(ft)
            points.append(CalPoint(watts=watts, raw=raw))
            self.log_line.emit(
                f"  {watts:3d} W (PC={pc_read}) → RM5-Rohwert {raw}"
            )
            time.sleep(PAUSE_BETWEEN_STEPS_S)

        return points

    def _measure_po_at_power(self, ft: FT991CAT) -> int:
        time.sleep(SETTLE_BEFORE_TX_S)
        ft.set_cat_transmit(True, wait=True, timeout_s=4.0)
        samples: List[int] = []
        deadline = time.monotonic() + TX_ON_SECONDS
        while time.monotonic() < deadline:
            if self._stop:
                break
            try:
                samples.append(ft.read_meter(MeterKind.PO))
            except CatError:
                pass
            time.sleep(0.25)
        ft.set_cat_transmit(False, wait=True, timeout_s=4.0)
        if not samples:
            raise CatError("Kein PO-Rohwert während TX erhalten (RM5;).")
        return max(samples)

    @staticmethod
    def _safe_tx_off(ft: FT991CAT) -> None:
        try:
            ft.set_cat_transmit(False, wait=False)
        except Exception:
            pass


class TuneOnlyWorker(QObject):
    """Setzt Test-QRG auf 10 m und startet den Antennentuner."""

    log_line = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)
    connection_lost = Signal()

    def __init__(self, serial_cat: SerialCAT, freq_hz: int) -> None:
        super().__init__()
        self._cat = serial_cat
        self._freq_hz = freq_hz

    @Slot()
    def run(self) -> None:
        ft = FT991CAT(self._cat)
        spec = HF_10M_BAND
        try:
            if not ft.set_rx_mode(spec.mode, verify=True):
                raise CatError(f"Modus {spec.mode.value} konnte nicht gesetzt werden.")
            ft.write_frequency(self._freq_hz)
            time.sleep(0.2)
            self.log_line.emit(
                f"QRG {self._freq_hz / 1e6:.4f} MHz — starte Antennentuner …"
            )
            ft.start_antenna_tuner()
            deadline = time.monotonic() + 45.0
            while time.monotonic() < deadline:
                time.sleep(0.4)
                try:
                    resp = ft.read_antenna_tuner_status()
                except CatError:
                    continue
                self.log_line.emit(f"Tuner: {resp.strip()}")
                if resp.startswith("AC") and len(resp) >= 5:
                    if resp[4] == "0" and time.monotonic() > deadline - 44.0:
                        break
            self.log_line.emit("Tune-Befehl abgeschlossen — bitte SWR am Gerät prüfen.")
            self.finished_ok.emit()
        except CatConnectionLostError:
            self.connection_lost.emit()
        except CatError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
