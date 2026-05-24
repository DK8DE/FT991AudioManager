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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cat import SerialCAT
from gui.calibration_worker import CalibrationWorker, TuneOnlyWorker
from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from mapping.calibration_bands import CAL_BAND_HF_10M, DEFAULT_HF_TEST_HZ
from model.po_calibration_store import CalPoint, load_po_calibration

_CalibrationWorkerLike = CalibrationWorker | TuneOnlyWorker


class PoCalibrationWidget(RetranslatableMixin, QWidget):
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
        self._register_retranslate()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._warn = QLabel()
        self._warn.setWordWrap(True)
        self._warn.setTextFormat(Qt.TextFormat.RichText)
        self._warn.setStyleSheet(
            "QLabel { background: #3d2f00; color: #ffe082; padding: 10px; "
            "border: 1px solid #806000; border-radius: 4px; }"
        )
        root.addWidget(self._warn)

        self._confirm_antennas = QCheckBox()
        root.addWidget(self._confirm_antennas)

        self._hf_box = QGroupBox()
        hf_layout = QHBoxLayout(self._hf_box)
        self._freq_lbl = QLabel()
        hf_layout.addWidget(self._freq_lbl)
        self._hf_freq_mhz = QDoubleSpinBox()
        self._hf_freq_mhz.setRange(28.0, 29.7)
        self._hf_freq_mhz.setDecimals(4)
        self._hf_freq_mhz.setSingleStep(0.005)
        self._hf_freq_mhz.setValue(DEFAULT_HF_TEST_HZ / 1e6)
        hf_layout.addWidget(self._hf_freq_mhz)
        self._tune_btn = QPushButton()
        self._tune_btn.clicked.connect(self._on_tune_hf)
        hf_layout.addWidget(self._tune_btn)
        hf_layout.addStretch()
        root.addWidget(self._hf_box)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton()
        self._start_btn.clicked.connect(self._on_start_calibration)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton()
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
        self._log.setMaximumHeight(90)
        root.addWidget(self._log)

    def retranslate_ui(self) -> None:
        self._warn.setText(tr("po_cal.warning_html"))
        self._confirm_antennas.setText(tr("po_cal.confirm_antenna"))
        self._hf_box.setTitle(tr("po_cal.tune_group"))
        self._freq_lbl.setText(tr("common.frequency_mhz"))
        self._tune_btn.setText(tr("po_cal.tune_btn"))
        self._start_btn.setText(tr("po_cal.start_btn"))
        self._stop_btn.setText(tr("common.stop"))
        self._table.setHorizontalHeaderLabels(
            [tr("common.watt"), tr("common.raw_value")]
        )
        self._log.setPlaceholderText(tr("po_cal.log_placeholder"))

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def confirm_abort_if_busy(self) -> bool:
        """``True`` wenn Schließen/Abbrechen erlaubt ist."""
        if not self.is_busy():
            return True
        reply = QMessageBox.question(
            self.window(),
            tr("po_cal.msgbox.calibration_running_title"),
            tr("po_cal.msgbox.calibration_running_text"),
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
                tr("po_cal.msgbox.not_connected_title"),
                tr("po_cal.msgbox.not_connected_text"),
            )
            return False
        if not self._confirm_antennas.isChecked():
            QMessageBox.warning(
                self.window(),
                tr("po_cal.msgbox.confirm_missing_title"),
                tr("po_cal.msgbox.confirm_missing_text"),
            )
            return False
        if self.is_busy():
            QMessageBox.information(
                self.window(),
                tr("common.running"),
                tr("po_cal.msgbox.already_running_text"),
            )
            return False
        return True

    def _on_tune_hf(self) -> None:
        if not self._cat.is_connected():
            QMessageBox.warning(
                self.window(),
                tr("po_cal.msgbox.not_connected_title"),
                tr("po_cal.msgbox.not_connected_short"),
            )
            return
        if self.is_busy():
            return
        self._set_busy(True)
        self._append_log(tr("po_cal.log.tune_start"))
        worker = TuneOnlyWorker(self._cat, self._hf_freq_hz())
        self._start_worker(worker, tune_only=True)

    def _on_start_calibration(self) -> None:
        if not self._ensure_ready():
            return
        reply = QMessageBox.question(
            self.window(),
            tr("po_cal.msgbox.start_title"),
            tr("po_cal.msgbox.start_text"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._progress.setValue(0)
        self._append_log(tr("po_cal.log.cal_start"))
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
        self._progress.setFormat(
            tr("po_cal.progress_format", message=message, current=current, total=total)
        )

    def _on_band_points(self, _band_id: str, points: object) -> None:
        if isinstance(points, list):
            self._points = list(points)
            self._refresh_table()

    def _on_calibration_done(self, _cal: object) -> None:
        self._progress.setValue(100)
        self._append_log(tr("po_cal.log.cal_done"))
        self.calibration_applied.emit()
        QMessageBox.information(
            self.window(),
            tr("common.finished"),
            tr("po_cal.msgbox.done_text"),
        )

    def _on_tune_done(self) -> None:
        QMessageBox.information(
            self.window(),
            tr("common.tune"),
            tr("po_cal.msgbox.tune_done_text"),
        )

    def _on_failed(self, message: str) -> None:
        self._append_log(tr("po_cal.log.error", message=message))
        QMessageBox.critical(
            self.window(), tr("common.calibration"), message
        )

    def _on_connection_lost(self) -> None:
        self._append_log(tr("po_cal.log.connection_lost"))
        QMessageBox.warning(
            self.window(),
            tr("common.connection"),
            tr("po_cal.log.connection_lost_msgbox"),
        )

    def _on_stop(self) -> None:
        worker = self._worker
        if worker is not None and isinstance(worker, CalibrationWorker):
            worker.stop()
        self._append_log(tr("po_cal.log.abort_requested"))
