"""PO-Meter-Kalibrierung (Sendeleistung / RM5 auf 10 m) als Einstellungs-Widget."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_PO_CAL_MAX_WIDTH_PX = 380

from cat import SerialCAT
from gui.calibration_worker import CalibrationWorker, TuneOnlyWorker

_CalibrationWorkerLike = CalibrationWorker | TuneOnlyWorker
from mapping.calibration_bands import CAL_BAND_HF_10M, DEFAULT_HF_TEST_HZ
from model.po_calibration_store import CalPoint, load_po_calibration

_WARNING_HTML = """
<p><b>Kalibrierung nur auf Kurzwelle (10&nbsp;m)</b></p>
<ul>
<li>Antenne an der <b>KW-Buchse</b>, für den Test auf 10&nbsp;m abgestimmt und
    <b>100&nbsp;W</b> belastbar.</li>
<li>Frequenz im 10-m-Band wählen mit
    <b>"QRG setzen Tuner starten"</b>.</li>
<li>Der Test sendet per CAT-TX je 2&nbsp;s in 5-W-Schritten von 5–100&nbsp;W
    (FM, Leistung über <b>PC</b>, Deckel über <b>EX137</b>).</li>
<li>Die gespeicherte Kurve gilt für alle die <b>POWER-Anzeige auf allen KW-Bändern.</b></li>
</ul><p> </p>
"""


class PoCalibrationWidget(QWidget):
    """Kalibrierung des POWER-TX-Meters (RM5) auf 10 m — für Einstellungen eingebettet."""

    calibration_applied = Signal()
    busy_changed = Signal(bool)

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._cat = serial_cat
        self._thread: Optional[QThread] = None
        self._worker: Optional[_CalibrationWorkerLike] = None
        self._points: List[CalPoint] = []
        self._build_ui()
        self._load_existing_into_table()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        warn = QLabel(_WARNING_HTML)
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setStyleSheet(
            "QLabel { background: #3d2f00; color: #ffe082; padding: 10px; "
            "border: 1px solid #806000; border-radius: 4px; }"
        )
        root.addWidget(warn)

        self._confirm_antennas = QCheckBox(
            "Ich habe eine passende KW-Antenne auf 10 m am KW-Anschluss angeschlossen."
        )
        root.addWidget(self._confirm_antennas)

        hf_box = QGroupBox("10 m — Test-QRG Abstimmen")
        hf_layout = QHBoxLayout(hf_box)
        hf_layout.addWidget(QLabel("Frequenz (MHz):"))
        self._hf_freq_mhz = QDoubleSpinBox()
        self._hf_freq_mhz.setRange(28.0, 29.7)
        self._hf_freq_mhz.setDecimals(4)
        self._hf_freq_mhz.setSingleStep(0.005)
        self._hf_freq_mhz.setValue(DEFAULT_HF_TEST_HZ / 1e6)
        hf_layout.addWidget(self._hf_freq_mhz)
        self._tune_btn = QPushButton("QRG setzen Tuner starten")
        self._tune_btn.clicked.connect(self._on_tune_hf)
        hf_layout.addWidget(self._tune_btn)
        hf_layout.addStretch()
        root.addWidget(hf_box)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Kalibrierung 10 m starten")
        self._start_btn.clicked.connect(self._on_start_calibration)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stopp")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        root.addWidget(self._progress)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Watt", "Rohwert"])
        self._table.horizontalHeader().setStretchLastSection(True)
        vh = self._table.verticalHeader()
        row_px = vh.defaultSectionSize()
        header_px = self._table.horizontalHeader().height()
        table_h = row_px * 5 + header_px + 4
        self._table.setMinimumHeight(table_h)
        self._table.setMaximumHeight(table_h)
        root.addWidget(self._table)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(400)
        self._log.setPlaceholderText("Protokoll …")
        self._log.setMaximumHeight(90)
        root.addWidget(self._log)

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def confirm_abort_if_busy(self) -> bool:
        """``True`` wenn Schließen/Abbrechen erlaubt ist."""
        if not self.is_busy():
            return True
        reply = QMessageBox.question(
            self.window(),
            "Kalibrierung läuft",
            "Die Kalibrierung läuft noch. Wirklich abbrechen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        self._on_stop()
        return True

    def _hf_freq_hz(self) -> int:
        return int(round(self._hf_freq_mhz.value() * 1_000_000))

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line)

    def _load_existing_into_table(self) -> None:
        cal = load_po_calibration()
        band = cal.bands.get(CAL_BAND_HF_10M)
        if band:
            self._points = list(band.points)
        self._refresh_table()

    def _refresh_table(self) -> None:
        self._table.setRowCount(len(self._points))
        for i, pt in enumerate(self._points):
            self._table.setItem(i, 0, QTableWidgetItem(str(pt.watts)))
            self._table.setItem(i, 1, QTableWidgetItem(str(pt.raw)))

    def _set_busy(self, busy: bool) -> None:
        self._start_btn.setEnabled(not busy)
        self._tune_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self.busy_changed.emit(busy)

    def _ensure_ready(self) -> bool:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self.window(),
                "Nicht verbunden",
                "Bitte zuerst eine CAT-Verbindung herstellen "
                "(Tab „CAT-Verbindung“ oder Datei → Verbinden).",
            )
            return False
        if not self._confirm_antennas.isChecked():
            QMessageBox.warning(
                self.window(),
                "Bestätigung fehlt",
                "Bitte bestätigen Sie die KW-Antenne für 10 m.",
            )
            return False
        if self.is_busy():
            QMessageBox.information(
                self.window(), "Läuft", "Es läuft bereits eine Kalibrierung."
            )
            return False
        return True

    def _on_tune_hf(self) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self.window(), "Nicht verbunden", "Keine CAT-Verbindung."
            )
            return
        if self.is_busy():
            return
        self._set_busy(True)
        self._append_log("--- Tune 10 m ---")
        worker = TuneOnlyWorker(self._cat, self._hf_freq_hz())
        self._start_worker(worker, tune_only=True)

    def _on_start_calibration(self) -> None:
        if not self._ensure_ready():
            return
        reply = QMessageBox.question(
            self.window(),
            "Kalibrierung starten",
            (
                "Kalibrierung auf 10 m (KW) starten?\n\n"
                "Das Funkgerät sendet automatisch per CAT-TX "
                "(5–100 W in 5-W-Schritten)."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._progress.setValue(0)
        self._append_log("--- Start Kalibrierung 10 m (KW) ---")
        worker = CalibrationWorker(self._cat, hf_freq_hz=self._hf_freq_hz())
        self._start_worker(worker, tune_only=False)

    def _start_worker(self, worker: _CalibrationWorkerLike, *, tune_only: bool) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log_line.connect(self._append_log)
        if tune_only:
            worker.finished_ok.connect(self._on_tune_done)
        elif isinstance(worker, CalibrationWorker):
            worker.progress.connect(self._on_progress)
            worker.band_points.connect(self._on_band_points)
            worker.finished_ok.connect(self._on_calibration_done)
        worker.failed.connect(self._on_failed)
        worker.connection_lost.connect(self._on_connection_lost)
        for sig in ("finished_ok", "failed", "connection_lost"):
            getattr(worker, sig).connect(thread.quit)
        thread.finished.connect(lambda: self._clear_thread(thread, worker))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _clear_thread(self, thread: QThread, worker: _CalibrationWorkerLike) -> None:
        if self._thread is thread:
            self._thread = None
            self._worker = None
        worker.deleteLater()
        thread.deleteLater()
        self._set_busy(False)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self._progress.setValue(int(100 * current / total))
        self._progress.setFormat(f"{message} ({current}/{total})")

    def _on_band_points(self, _band_id: str, points: object) -> None:
        if isinstance(points, list):
            self._points = list(points)
            self._refresh_table()

    def _on_calibration_done(self, _cal: object) -> None:
        self._progress.setValue(100)
        self._append_log("Kalibrierung abgeschlossen — POWER-Skala aktualisiert.")
        self.calibration_applied.emit()
        QMessageBox.information(
            self.window(),
            "Fertig",
            "10-m-Kalibrierung gespeichert.\n"
            "Die POWER-Anzeige nutzt diese Kurve (auf VHF/UHF Skala 0–50 W).",
        )

    def _on_tune_done(self) -> None:
        QMessageBox.information(
            self.window(),
            "Tune",
            "Test-QRG gesetzt und Tune-Befehl gesendet.\n"
            "Bitte SWR am Funkgerät prüfen, dann die Kalibrierung starten.",
        )

    def _on_failed(self, message: str) -> None:
        self._append_log(f"FEHLER: {message}")
        QMessageBox.critical(self.window(), "Kalibrierung", message)

    def _on_connection_lost(self) -> None:
        self._append_log("Verbindung verloren.")
        QMessageBox.warning(self.window(), "Verbindung", "CAT-Verbindung verloren.")

    def _on_stop(self) -> None:
        worker = self._worker
        if worker is not None and isinstance(worker, CalibrationWorker):
            worker.stop()
        self._append_log("Abbruch angefordert …")
