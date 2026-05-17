"""Gemeinsame CAT-Session für Audio-Player und Audio-Recorder.

Solange mindestens eines der Fenster sichtbar/offen ist, bleiben die
Menüs für PC-Audio (EX048/070/072/077/109) auf USB/Rear. Erst wenn
beides zu ist, wird per ``restore()`` der Funkzustand vom ersten
``apply()`` wiederhergestellt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QThread, QMetaObject, Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from audio.radio_playback_setup import RadioPlaybackSetup, RadioSetupWorker, data_mode_from_string

if TYPE_CHECKING:
    from cat import SerialCAT
    from model import AppSettings


class AudioRadioSessionHost(QObject):
    """Ein :class:`RadioPlaybackSetup` + Worker-Thread für Player und Recorder."""

    def __init__(
        self,
        settings: AppSettings,
        serial_cat: SerialCAT,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cat = serial_cat
        initial = data_mode_from_string(settings.audio_player.data_mode)
        self.setup = RadioPlaybackSetup(serial_cat, initial)
        self._thread = QThread(self)
        self.worker = RadioSetupWorker(self.setup)
        self.worker.moveToThread(self._thread)
        self.worker.apply_finished.connect(self._on_apply_finished_warn)
        self.worker.restore_finished.connect(self._on_restore_finished_warn)
        self._thread.start(QThread.HighestPriority)
        self._open_ids: set[int] = set()

    def _on_apply_finished_warn(self, ok: bool, message: str) -> None:
        if ok or not message:
            return
        parent = self.parent()
        if isinstance(parent, QWidget):
            QMessageBox.warning(parent, "Audio / CAT", message)

    def _on_restore_finished_warn(self, ok: bool, message: str) -> None:
        if ok or not message:
            return
        parent = self.parent()
        if isinstance(parent, QWidget):
            QMessageBox.warning(parent, "Audio / CAT", message)

    @property
    def thread(self) -> QThread:
        return self._thread

    def reload_data_mode_from_settings(self) -> None:
        """Nach Settings-Laden die gewünschte DATA-Art syncen (ohne CAT)."""
        target = data_mode_from_string(self._settings.audio_player.data_mode)
        self.setup.set_data_mode(target)

    def on_window_shown(self, window: QWidget) -> None:
        self._open_ids.add(id(window))
        if not self._cat.is_connected():
            return
        if not self.setup.is_applied:
            QMetaObject.invokeMethod(
                self.worker,
                "run_apply",
                Qt.ConnectionType.QueuedConnection,
            )

    def on_window_closed_hidden(self, window: QWidget) -> None:
        """Aufruf aus ``closeEvent``, wenn das Fenster nur versteckt wird."""
        key = id(window)
        self._open_ids.discard(key)
        if len(self._open_ids) == 0:
            self._invoke_restore_if_applied()

    def detach_for_force_close(self, window: QWidget) -> None:
        """Wie ``on_window_closed_hidden`` — für ``force_close`` / App-Exit."""
        self.on_window_closed_hidden(window)

    def _invoke_restore_if_applied(self) -> None:
        if not self.setup.is_applied:
            return
        QMetaObject.invokeMethod(
            self.worker,
            "run_restore",
            Qt.ConnectionType.QueuedConnection,
        )

    def shutdown(self) -> None:
        """App-Ende: Thread beenden (nachdem Fenster ``detach`` + Restore ausgelöst haben)."""
        self._open_ids.clear()
        self._thread.quit()
        self._thread.wait(4000)
