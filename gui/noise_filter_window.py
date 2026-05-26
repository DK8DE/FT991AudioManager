"""Eigenes Fenster für den Funk-Rückweg-Rauschfilter (Rauschgate)."""

from __future__ import annotations

import base64
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from model.live_settings import LiveFunkListenGateSettings

from .app_icon import app_icon
from .touch_slider import TouchSlider
from .window_lifecycle import application_exit_close_requested


class NoiseFilterWindow(RetranslatableMixin, QMainWindow):
    """Rauschgate am Funk-Mithör-Eingang — Werte werden in ``live.funk_listen_gate`` gespeichert."""

    MIN_WIDTH = 460
    MIN_HEIGHT = 320

    def __init__(
        self,
        *,
        read_gate: Callable[[], LiveFunkListenGateSettings],
        on_gate_changed: Callable[[LiveFunkListenGateSettings], None],
        read_geometry_b64: Callable[[], str],
        write_geometry_b64: Callable[[str], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._read_gate = read_gate
        self._on_gate_changed = on_gate_changed
        self._read_geometry_b64 = read_geometry_b64
        self._write_geometry_b64 = write_geometry_b64

        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resize(520, 360)

        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(400)
        self._persist_timer.timeout.connect(self._emit_gate_changed)

        self._build_ui()
        self._restore_geometry()
        self.reload_from_settings()
        self.retranslate_ui()
        self._register_retranslate()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color:#a8a8a8;font-size:11px;")
        root.addWidget(self._hint)

        self._group = QGroupBox()
        grid = QGridLayout(self._group)
        grid.setHorizontalSpacing(8)
        grid.setColumnStretch(1, 1)

        self._fg_en = QCheckBox()
        self._fg_en.toggled.connect(self._schedule_emit)

        self._fg_thr = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_thr.setRange(-700, -10)
        self._fg_thr.valueChanged.connect(self._on_slider_changed)

        self._fg_att = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_att.setRange(1, 20)
        self._fg_att.valueChanged.connect(self._on_slider_changed)

        self._fg_hld = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_hld.setRange(5, 200)
        self._fg_hld.valueChanged.connect(self._on_slider_changed)

        self._fg_rel = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_rel.setRange(20, 500)
        self._fg_rel.valueChanged.connect(self._on_slider_changed)

        self._fg_thr_lbl = self._mk_read_lbl()
        self._fg_att_lbl = self._mk_read_lbl()
        self._fg_hld_lbl = self._mk_read_lbl()
        self._fg_rel_lbl = self._mk_read_lbl()

        self._fg_thr_cap = QLabel()
        self._fg_att_cap = QLabel()
        self._fg_hld_cap = QLabel()
        self._fg_rel_cap = QLabel()

        grid.addWidget(self._fg_en, 0, 0, 1, 3)
        rows = (
            (self._fg_thr_cap, self._fg_thr, self._fg_thr_lbl),
            (self._fg_att_cap, self._fg_att, self._fg_att_lbl),
            (self._fg_hld_cap, self._fg_hld, self._fg_hld_lbl),
            (self._fg_rel_cap, self._fg_rel, self._fg_rel_lbl),
        )
        for row_idx, (cap, slid, read_lbl) in enumerate(rows, start=1):
            grid.addWidget(cap, row_idx, 0)
            grid.addWidget(slid, row_idx, 1)
            grid.addWidget(read_lbl, row_idx, 2)

        root.addWidget(self._group)
        root.addStretch(1)
        self.setCentralWidget(central)

    @staticmethod
    def _mk_read_lbl() -> QLabel:
        lbl = QLabel(tr("common.dash"))
        lbl.setMinimumWidth(86)
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        lbl.setStyleSheet("color:#c8c8c8;font-size:11px;font-weight:600;")
        return lbl

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("noise_filter.window.title"))
        self._hint.setText(tr("noise_filter.hint"))
        self._group.setTitle(tr("live.group.funk_gate"))
        self._fg_en.setText(tr("live.funk_gate.enabled"))
        self._fg_thr_cap.setText(tr("live.funk_gate.threshold"))
        self._fg_att_cap.setText(tr("live.funk_gate.attack"))
        self._fg_hld_cap.setText(tr("live.funk_gate.hold"))
        self._fg_rel_cap.setText(tr("live.funk_gate.release"))
        self._refresh_readouts()

    def reload_from_settings(self) -> None:
        fg = self._read_gate()
        self._fg_en.blockSignals(True)
        self._fg_thr.blockSignals(True)
        self._fg_att.blockSignals(True)
        self._fg_hld.blockSignals(True)
        self._fg_rel.blockSignals(True)

        self._fg_en.setChecked(bool(fg.enabled))
        self._fg_thr.setValue(max(-700, min(-10, int(round(fg.threshold_db * 10.0)))))
        self._fg_att.setValue(int(round(fg.attack_ms)))
        self._fg_hld.setValue(int(round(fg.hold_ms)))
        self._fg_rel.setValue(int(round(fg.release_ms)))

        self._fg_en.blockSignals(False)
        self._fg_thr.blockSignals(False)
        self._fg_att.blockSignals(False)
        self._fg_hld.blockSignals(False)
        self._fg_rel.blockSignals(False)
        self._refresh_readouts()

    def _gate_from_ui(self) -> LiveFunkListenGateSettings:
        fg = LiveFunkListenGateSettings(
            enabled=bool(self._fg_en.isChecked()),
            threshold_db=float(self._fg_thr.value()) / 10.0,
            attack_ms=float(self._fg_att.value()),
            hold_ms=float(self._fg_hld.value()),
            release_ms=float(self._fg_rel.value()),
        )
        fg.clamp()
        return fg

    def _refresh_readouts(self) -> None:
        self._fg_thr_lbl.setText(tr("live.readout.db", value=self._fg_thr.value() / 10.0))
        self._fg_att_lbl.setText(tr("live.readout.ms_nbsp", value=self._fg_att.value()))
        self._fg_hld_lbl.setText(tr("live.readout.ms_nbsp", value=self._fg_hld.value()))
        self._fg_rel_lbl.setText(tr("live.readout.ms_nbsp", value=self._fg_rel.value()))

    def _on_slider_changed(self, *_v: object) -> None:
        self._refresh_readouts()
        self._schedule_emit()

    def _schedule_emit(self, *_v: object) -> None:
        self._persist_timer.start()

    def _emit_gate_changed(self) -> None:
        self._on_gate_changed(self._gate_from_ui())

    def _save_geometry(self) -> None:
        geo = self.saveGeometry()
        if geo.isEmpty():
            return
        self._write_geometry_b64(
            base64.b64encode(geo.data()).decode("ascii"),
        )

    def _restore_geometry(self) -> None:
        raw = self._read_geometry_b64()
        if not raw:
            return
        try:
            from PySide6.QtCore import QByteArray

            blob = QByteArray(base64.b64decode(raw.encode("ascii")))
            if not blob.isEmpty():
                self.restoreGeometry(blob)
        except Exception:
            pass

    def force_close(self) -> None:
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._persist_timer.isActive():
            self._persist_timer.stop()
            self._emit_gate_changed()
        self._save_geometry()
        if application_exit_close_requested(self):
            if not getattr(self, "_force_close", False):
                self.force_close()
                event.accept()
                return
        if getattr(self, "_force_close", False):
            super().closeEvent(event)
            return
        self.hide()
        event.ignore()
