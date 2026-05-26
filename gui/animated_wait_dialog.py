"""Fortschrittsdialog mit Animation und Wartezeit-Anzeige."""

from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

from i18n import tr


class AnimatedWaitDialog(QProgressDialog):
    """Animierter Fortschritt (0–95 %) plus Anzeige der verstrichenen Wartezeit."""

    def __init__(
        self,
        message: str,
        title: str,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(message, "", 0, 100, parent)
        self._base_message = message
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumDuration(0)
        self.setCancelButton(None)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setValue(0)
        self._started = 0.0
        self._display_value = 0
        self._target_cap = 95
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        self._started = time.monotonic()
        self._display_value = 0
        self.setValue(0)
        self._update_label()
        self._timer.start()
        self.show()
        QApplication.processEvents()

    def bump(self, value: int) -> None:
        """Mindest-Fortschritt nach Teilschritten (z. B. Modul importiert)."""
        capped = max(0, min(int(value), self._target_cap))
        if capped > self._display_value:
            self._display_value = capped
            self.setValue(self._display_value)
        QApplication.processEvents()

    def finish(self) -> None:
        self._timer.stop()
        self.setValue(self.maximum())
        QApplication.processEvents()

    def _on_tick(self) -> None:
        elapsed = time.monotonic() - self._started
        exp_progress = int(self._target_cap * (1.0 - math.exp(-elapsed / 10.0)))
        linear_progress = min(self._target_cap - 1, int(elapsed * 12))
        animated = max(exp_progress, linear_progress)
        if animated > self._display_value:
            self._display_value = animated
            self.setValue(self._display_value)
        self._update_label(elapsed)
        QApplication.processEvents()

    def _update_label(self, elapsed: float | None = None) -> None:
        if elapsed is None:
            elapsed = time.monotonic() - self._started
        self.setLabelText(
            f"{self._base_message}\n\n"
            f"{tr('live.loading.elapsed', seconds=float(elapsed))}"
        )
