"""Gemeinsame CAT-Session für Audio-Player, Audio-Recorder und Live-Monitoring.

Beim Öffnen eines Fensters werden die Menüs EX048/070/072/077/109 sofort
für PC-Audio gesetzt und der Vormerkzustand für :meth:`restore` geladen; die
Betriebsart (DATA-FM / …) wechselt erst bei Start von Wiedergabe, Replay,
Aufnahme oder Live‑Sendung. Sind alle zugehörigen Fenster zu, wird per :meth:`RadioPlaybackSetup.restore`
wiederhergestellt.
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
    """Ein :class:`RadioPlaybackSetup` + Worker-Thread für Player, Recorder und Live."""

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
        self.worker.pc_menus_finished.connect(self._on_pc_menus_finished_warn)
        self.worker.restore_finished.connect(self._on_restore_finished_warn)
        self._thread.start(QThread.HighestPriority)
        self._open_ids: set[int] = set()

    def _on_pc_menus_finished_warn(self, ok: bool, message: str) -> None:
        if ok or not message:
            return
        parent = self.parent()
        if isinstance(parent, QWidget):
            QMessageBox.warning(parent, "Audio / CAT", message)

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

    @property
    def has_open_audio_windows(self) -> bool:
        """True, wenn Player, Recorder oder Live die Session nutzt."""
        return bool(self._open_ids)

    def reload_data_mode_from_settings(self) -> None:
        """Nach Settings-Laden die gewünschte DATA-Art syncen (ohne CAT)."""
        target = data_mode_from_string(self._settings.audio_player.data_mode)
        self.setup.set_data_mode(target)

    def on_window_shown(self, window: QWidget) -> None:
        self._open_ids.add(id(window))
        if not self._cat.is_connected():
            return
        self.setup.set_data_mode(data_mode_from_string(self._settings.audio_player.data_mode))
        QMetaObject.invokeMethod(
            self.worker,
            "run_apply_pc_menus",
            Qt.QueuedConnection,
        )

    def on_window_hidden(self, window: QWidget) -> None:
        """Fenster aus der offenen Liste nehmen (ohne CAT-Restore)."""
        self._open_ids.discard(id(window))

    def request_restore_if_no_windows(self) -> None:
        """CAT-Zustand wiederherstellen, wenn kein Audio-Fenster mehr offen ist."""
        if len(self._open_ids) == 0:
            self._invoke_restore_if_applied()

    def on_window_closed_hidden(self, window: QWidget) -> None:
        """Aufruf aus ``closeEvent``, wenn das Fenster nur versteckt wird."""
        self.on_window_hidden(window)
        self.request_restore_if_no_windows()

    def detach_for_force_close(self, window: QWidget) -> None:
        """Fenster abmelden und ggf. CAT wiederherstellen (App-Exit)."""
        self.on_window_hidden(window)
        self.request_restore_if_no_windows()

    def _invoke_restore_if_applied(self) -> None:
        if not self.setup.is_applied:
            return
        QMetaObject.invokeMethod(
            self.worker,
            "run_restore",
            Qt.QueuedConnection,
        )

    def shutdown(self) -> None:
        """App-Ende: Thread beenden (nachdem Fenster ``detach`` + Restore ausgelöst haben)."""
        self._open_ids.clear()
        self.worker.blockSignals(True)
        self._thread.quit()
        if not self._thread.wait(4000):
            self._thread.terminate()
            self._thread.wait(1000)
