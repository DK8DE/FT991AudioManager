"""Eigenes Fenster für Equalizer- und Profil-Bearbeitung."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from .app_icon import app_icon
from .profile_widget import ProfileWidget


class EqualizerWindow(QMainWindow):
    """Zeigt den Equalizer-Editor (:attr:`ProfileWidget.editor_panel`)."""

    closed = Signal()

    def __init__(
        self,
        profile_widget: ProfileWidget,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._profile_widget = profile_widget
        self.setWindowTitle("FT-991A EQ-Profil")
        self.setWindowIcon(app_icon())
        self.resize(920, 780)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(profile_widget.editor_panel)
        self.setCentralWidget(central)

    def force_close(self) -> None:
        """Beendet das Fenster endgültig (z. B. beim App-Beenden)."""
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if getattr(self, "_force_close", False):
            super().closeEvent(event)
            self.closed.emit()
            return
        self.hide()
        event.ignore()
        self.closed.emit()
