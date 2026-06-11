"""Eigenes Fenster für Equalizer- und Profil-Bearbeitung."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMainWindow, QMessageBox, QVBoxLayout, QWidget

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from mapping.rx_mapping import coarse_mode_group_for, eq_profile_supported_for_mode_group

from .app_icon import app_icon
from .profile_widget import ProfileWidget
from .window_lifecycle import application_exit_close_requested


class EqualizerWindow(RetranslatableMixin, QMainWindow):
    """Zeigt den Equalizer-Editor (:attr:`ProfileWidget.editor_panel`)."""

    closed = Signal()

    #: Mindestgröße — Kopfzeile/Toolbar/Status + sichtbarer Scroll-Inhalt.
    MIN_WIDTH = 680
    MIN_HEIGHT = 480

    def __init__(
        self,
        profile_widget: ProfileWidget,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._profile_widget = profile_widget
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resize(920, 780)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(profile_widget.editor_panel)
        self.setCentralWidget(central)
        self.retranslate_ui()
        self._register_retranslate()

        profile_widget.eq_mode_locked.connect(self._on_eq_mode_locked)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("equalizer.window.title"))

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._profile_widget._refresh_mode_combo_eq_colors()
        # Beim Öffnen des Fensters: wenn der aktuelle Modus EQ nicht
        # unterstützt, direkt einen Hinweis anzeigen.
        mode_text = self._profile_widget.mode_combo_eq.currentText()
        mg = coarse_mode_group_for(mode_text)
        if not eq_profile_supported_for_mode_group(mg):
            self._show_eq_locked_dialog(mode_text)

    def _on_eq_mode_locked(self, mode_text: str) -> None:
        """Slot: ProfileWidget meldet, dass ein User-Moduswechsel den EQ gesperrt hat."""
        if self.isVisible():
            self._show_eq_locked_dialog(mode_text)

    def _show_eq_locked_dialog(self, mode_text: str) -> None:
        """Zeigt einen Hinweis-Dialog, dass der EQ in diesem Modus nicht wirkt."""
        QMessageBox.information(
            self,
            tr("equalizer.eq_locked.title"),
            tr("equalizer.eq_locked.text", mode=mode_text),
        )

    def force_close(self) -> None:
        """Beendet das Fenster endgültig (z. B. beim App-Beenden)."""
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if application_exit_close_requested(self):
            if not getattr(self, "_force_close", False):
                self.force_close()
                event.accept()
                return
        if getattr(self, "_force_close", False):
            super().closeEvent(event)
            self.closed.emit()
            return
        self.hide()
        event.ignore()
        self.closed.emit()
