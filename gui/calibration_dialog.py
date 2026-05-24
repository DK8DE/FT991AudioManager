"""Abwärtskompatibilität: PO-Kalibrierung liegt in :mod:`po_calibration_widget`."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from cat import SerialCAT
from i18n import tr
from i18n.retranslatable import RetranslatableMixin

from .po_calibration_widget import PoCalibrationWidget

__all__ = [
    "CalibrationDialog",
    "PoCalibrationWidget",
    "open_calibration_dialog",
]


class CalibrationDialog(RetranslatableMixin, QDialog):
    """Eigenes Fenster (veraltet — Inhalt liegt in den Einstellungen)."""

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        parent: Optional[QWidget] = None,
        on_closed: Optional[Callable[..., None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_closed = on_closed
        self.setMinimumSize(600, 560)
        self.resize(680, 720)
        layout = QVBoxLayout(self)
        self._widget = PoCalibrationWidget(serial_cat, parent=self)
        layout.addWidget(self._widget)
        self.calibration_applied = self._widget.calibration_applied
        self.retranslate_ui()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("calibration_dialog.title"))

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._widget.confirm_abort_if_busy():
            event.ignore()
            return
        if self._on_closed is not None:
            self._on_closed()
        super().closeEvent(event)


def open_calibration_dialog(
    serial_cat: SerialCAT,
    *,
    parent: Optional[QWidget] = None,
    on_closed: Optional[Callable[..., None]] = None,
) -> CalibrationDialog:
    dlg = CalibrationDialog(serial_cat, parent=parent, on_closed=on_closed)
    dlg.show()
    return dlg
