"""GUI-Widget zur Live-Anzeige der CAT-Kommunikation.

Das Widget hängt sich als Observer an einen :class:`CatLog` und stellt die
Einträge als farblich abgesetzten Text dar. Da der Observer aus
Worker-Threads aufgerufen wird, wird jeder Eintrag per Qt-Signal an den
GUI-Thread weitergereicht (queued connection, automatisch thread-sicher).

Performance-Hinweise:
- Einträge werden im GUI-Thread **gepuffert** und alle 100 ms gemeinsam
  gerendert. So bremsen 25+ Log-Events pro Sekunde (Meter-Polling) die GUI
  nicht aus.
- Polling-Befehle (``TX;`` / ``RMn;`` / ``TX0;`` / ``RMn...;``) werden per
  Default **ausgeblendet**, da sie bei aktivem Meter den Log fluten würden.
"""

from __future__ import annotations

import base64
import re
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cat import CatLog, LogEntry, LogLevel
from i18n import tr
from i18n.retranslatable import RetranslatableMixin

from .theme import current_log_colors, is_dark_mode


# Polling-Befehle/-Antworten, die in der Standard-Ansicht ausgeblendet werden.
# Match auf TX/RX-Einträge: ``TX;`` / ``TX0;`` / ``TX1;`` / ``RM1;`` / ``RM1nnn;`` etc.
_POLLING_PATTERN = re.compile(r"^(TX[01]?|RM[1-9](?:\d{3})?);$")


def _colors_for(dark: bool):
    """Liefert das Level-Farb-Mapping für den aktuellen Theme-Zustand."""
    from .theme import LOG_COLORS_DARK, LOG_COLORS_LIGHT
    raw = LOG_COLORS_DARK if dark else LOG_COLORS_LIGHT
    return {
        LogLevel.TX: raw["TX"],
        LogLevel.RX: raw["RX"],
        LogLevel.INFO: raw["INFO"],
        LogLevel.WARN: raw["WARN"],
        LogLevel.ERROR: raw["ERROR"],
        LogLevel.DEBUG: raw["DEBUG"],
    }


class LogBridge(QObject):
    """Thread-sichere Brücke zwischen :class:`CatLog` und Qt-Slots.

    Der :class:`CatLog`-Observer wird *im jeweiligen Producer-Thread*
    aufgerufen. Wir feuern daraus ein Qt-Signal, das per Standard-
    AutoConnection in den GUI-Thread queued wird.

    Die Anbindung an :class:`CatLog` erfolgt erst über :meth:`attach` —
    :meth:`detach` beim Ausblenden des Fensters, damit Polling-Einträge
    nicht weiter in die GUI gepuffert werden.
    """

    entry = Signal(object)  # LogEntry
    cleared = Signal()

    def __init__(self, log: CatLog, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._log = log
        self._attached = False

    def attach(self) -> None:
        if self._attached:
            return
        self._log.add_observer(self._on_entry)
        self._log.add_cleared_observer(self._on_cleared)
        self._attached = True

    def detach(self) -> None:
        if not self._attached:
            return
        self._log.remove_observer(self._on_entry)
        self._log.remove_cleared_observer(self._on_cleared)
        self._attached = False

    def _on_entry(self, entry: LogEntry) -> None:
        # Achtung: kann aus Worker-Thread kommen.
        self.entry.emit(entry)

    def _on_cleared(self) -> None:
        self.cleared.emit()


class LogPanel(RetranslatableMixin, QWidget):
    """Eigentliche Log-Ansicht mit Toolbar.

    Eingehende Log-Einträge werden in einem internen Puffer gesammelt und
    alle ~100 ms in einem einzigen HTML-Block ins ``QTextEdit`` geschrieben.
    Das vermeidet zig ``insertHtml``-Aufrufe pro Sekunde, die sonst die GUI
    spürbar ausbremsen würden.
    """

    _FLUSH_INTERVAL_MS = 100
    _MAX_BUFFER = 500

    def __init__(self, log: CatLog, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._log = log
        self._auto_scroll = True
        self._level_color = _colors_for(is_dark_mode())
        self._hide_polling = True
        self._pending: List[LogEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        layout.addLayout(toolbar)

        self._heading_label = QLabel()
        toolbar.addWidget(self._heading_label)

        self.copy_button = QPushButton()
        self.copy_button.clicked.connect(self._on_copy)
        toolbar.addWidget(self.copy_button)

        self.save_button = QPushButton()
        self.save_button.clicked.connect(self._on_save)
        toolbar.addWidget(self.save_button)

        self.clear_button = QPushButton()
        self.clear_button.clicked.connect(self._on_clear)
        toolbar.addWidget(self.clear_button)

        toolbar.addSpacing(12)

        self.autoscroll_check = QCheckBox()
        self.autoscroll_check.setChecked(True)
        self.autoscroll_check.toggled.connect(self._on_autoscroll_toggled)
        toolbar.addWidget(self.autoscroll_check)

        self.hide_polling_check = QCheckBox()
        self.hide_polling_check.setChecked(True)
        self.hide_polling_check.toggled.connect(self._on_hide_polling_toggled)
        toolbar.addWidget(self.hide_polling_check)

        toolbar.addStretch(1)

        # --- Anzeige ---
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # Maximalblöcke begrenzen — bei sehr viel Logging bleibt das TextEdit
        # responsiv. (0 = unbegrenzt; 5000 reicht für ~30 min Polling.)
        self.view.document().setMaximumBlockCount(5000)
        font = QFont()
        font.setFamily("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)
        layout.addWidget(self.view, stretch=1)

        # --- Flush-Timer (alle 100 ms ausstehende Einträge gemeinsam rendern) ---
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self._FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)

        # --- Brücke (Anbindung erst bei activate()) ---
        self._observing = False
        self._bridge = LogBridge(log, parent=self)
        self._bridge.entry.connect(self._on_entry)
        self._bridge.cleared.connect(self._clear_view)

        self.retranslate_ui()
        self._register_retranslate()

    def activate(self) -> None:
        """Live-Anzeige starten — beim Einblenden des Log-Fensters."""
        if self._observing:
            return
        self._bridge.attach()
        self._flush_timer.start()
        self._observing = True
        self._reload_snapshot()

    def deactivate(self) -> None:
        """Live-Anzeige stoppen — beim Ausblenden des Log-Fensters."""
        if not self._observing:
            return
        self._flush_timer.stop()
        self._bridge.detach()
        self._pending.clear()
        self.view.clear()
        self._observing = False

    def _reload_snapshot(self) -> None:
        self._pending.clear()
        for entry in self._log.snapshot():
            if self._hide_polling and self._is_polling_entry(entry):
                continue
            self._pending.append(entry)
        self._flush_pending()

    def retranslate_ui(self) -> None:
        self._heading_label.setText(tr("log.heading"))
        self.copy_button.setText(tr("log.btn.copy"))
        self.copy_button.setToolTip(tr("log.btn.copy.tooltip"))
        self.save_button.setText(tr("log.btn.save"))
        self.save_button.setToolTip(tr("log.btn.save.tooltip"))
        self.clear_button.setText(tr("log.btn.clear"))
        self.autoscroll_check.setText(tr("log.check.autoscroll"))
        self.hide_polling_check.setText(tr("log.check.hide_polling"))
        self.hide_polling_check.setToolTip(tr("log.check.hide_polling.tooltip"))

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_entry(self, entry: LogEntry) -> None:
        if self._hide_polling and self._is_polling_entry(entry):
            return
        self._pending.append(entry)
        # Pufferdeckel — sehr extreme Bursts werfen das Älteste raus.
        if len(self._pending) > self._MAX_BUFFER:
            del self._pending[: len(self._pending) - self._MAX_BUFFER]

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        # Wir bauen einen einzigen HTML-String zusammen und feuern EINEN
        # insertHtml-Call. Das ist um ein Vielfaches schneller als ein
        # Insert pro Eintrag.
        parts: List[str] = []
        for entry in self._pending:
            color = self._level_color.get(entry.level, "#888888")
            text = entry.formatted()
            text_html = (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace(" ", "&nbsp;")
            )
            parts.append(
                f'<span style="color:{color}; font-family:Consolas, monospace;">'
                f"{text_html}</span>"
            )
        self._pending.clear()

        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        prefix = "<br>" if not self.view.document().isEmpty() else ""
        cursor.insertHtml(prefix + "<br>".join(parts))

        if self._auto_scroll:
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    @staticmethod
    def _is_polling_entry(entry: LogEntry) -> bool:
        if entry.level not in (LogLevel.TX, LogLevel.RX):
            return False
        return _POLLING_PATTERN.match(entry.text) is not None

    def _clear_view(self) -> None:
        self._pending.clear()
        self.view.clear()

    # ------------------------------------------------------------------
    # Theme-Anbindung
    # ------------------------------------------------------------------

    def set_dark_mode(self, dark: bool) -> None:
        """Wird beim Theme-Wechsel gerufen: aktualisiert das Farbschema
        und rendert das bereits sichtbare Log neu, damit alte Einträge
        zur neuen Optik passen."""
        self._level_color = _colors_for(dark)
        self._rerender_all()

    def _rerender_all(self) -> None:
        self.view.clear()
        self._pending.clear()
        for entry in self._log.snapshot():
            if self._hide_polling and self._is_polling_entry(entry):
                continue
            self._pending.append(entry)
        self._flush_pending()

    def _on_clear(self) -> None:
        self._log.clear()
        # _clear_view wird über das cleared-Signal getriggert.

    def _on_hide_polling_toggled(self, checked: bool) -> None:
        self._hide_polling = bool(checked)
        # Re-Rendern mit neuem Filter.
        self._rerender_all()

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("log.file.save.title"),
            tr("log.file.save.default"),
            tr("log.file.filter.txt"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.view.toPlainText())
        except OSError as exc:
            QMessageBox.critical(self, tr("log.msg.save_failed.title"), str(exc))

    def _on_autoscroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = bool(checked)


class LogDockWidget(RetranslatableMixin, QDockWidget):
    """Andockbares Log-Fenster (Legacy — wird nicht mehr im Hauptfenster genutzt).

    Behalten für Tests/Smoke-Checks und Rückwärtskompatibilität. Das produktive
    Hauptfenster verwendet stattdessen ein eigenständiges :class:`LogWindow`.
    """

    def __init__(self, log: CatLog, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CatLogDock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        self.panel = LogPanel(log)
        self.setWidget(self.panel)
        self.panel.activate()
        self.retranslate_ui()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("log.dock.title"))
        self.panel.retranslate_ui()


# ----------------------------------------------------------------------
# Eigenständiges Log-Fenster
# ----------------------------------------------------------------------


class LogWindow(RetranslatableMixin, QWidget):
    """Eigenständiges Toplevel-Fenster für das CAT-Log.

    Wird über das Ansicht-Menü ein-/ausgeblendet. Beim Schließen oder
    Ausblenden wird die Live-Anzeige gestoppt (kein weiteres Puffern
    im Hintergrund). Das MainWindow hört auf ``closed`` und persistiert
    den Sichtbarkeits-Zustand.

    Geometrie wird in den App-Settings als Base64-encodiertes
    ``QByteArray`` gespeichert.
    """

    closed = Signal()

    def __init__(self, log: CatLog) -> None:
        # Bewusst KEIN parent — das Fenster soll wirklich eigenständig
        # in der Taskleiste erscheinen und sich frei bewegen lassen.
        super().__init__(None)
        from .app_icon import app_icon
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(900, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = LogPanel(log)
        layout.addWidget(self.panel)
        self.retranslate_ui()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("log.window.title"))
        self.panel.retranslate_ui()

    # ------------------------------------------------------------------
    # Theme-Anbindung (Durchreichen ans LogPanel)
    # ------------------------------------------------------------------

    def set_dark_mode(self, dark: bool) -> None:
        self.panel.set_dark_mode(dark)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.panel.activate()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self.panel.deactivate()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Geometrie-Persistenz
    # ------------------------------------------------------------------

    def restore_geometry_from_base64(self, b64: str) -> None:
        """Stellt die Geometrie aus einem Base64-encodierten String wieder her.

        Leere oder ungültige Strings werden ignoriert.
        """
        if not b64:
            return
        from PySide6.QtCore import QByteArray
        try:
            data = QByteArray.fromBase64(b64.encode("ascii"))
        except Exception:
            return
        if data.size():
            self.restoreGeometry(data)

    def geometry_to_base64(self) -> str:
        data = self.saveGeometry()
        try:
            return base64.b64encode(data.data()).decode("ascii")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # closeEvent
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.closed.emit()
        event.accept()
