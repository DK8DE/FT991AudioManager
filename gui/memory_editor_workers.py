"""Hintergrund-Worker für Lesen/Schreiben der Speicherkanäle."""

from __future__ import annotations

import time
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from cat import CatConnectionLostError, CatError, FT991CAT, SerialCAT
from gui.memory_editor_io import backup_path, save_backup_json
from mapping.memory_editor_codec import should_write_cleared
from model._app_paths import app_data_dir
from model.memory_editor_channel import (
    MEMORY_EDITOR_MAX,
    MEMORY_EDITOR_MIN,
    MemoryChannelBank,
    MemoryEditorChannel,
)

# Pause zwischen MT-Befehlen — leere Slots brauchen etwas mehr Zeit am FT-991/A.
_INTER_CMD_DELAY_S: float = 0.05
_EMPTY_SLOT_WRITE_DELAY_S: float = 0.08


class _ReadWorker(QObject):
    progressed = Signal(int, int)
    channel_read = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    connection_lost = Signal()

    def __init__(self, serial_cat: SerialCAT) -> None:
        super().__init__()
        self._cat = serial_cat
        self._stop = False

    @Slot()
    def stop(self) -> None:
        self._stop = True

    @Slot()
    def run(self) -> None:
        ft = FT991CAT(self._cat)
        self.progressed.emit(0, MEMORY_EDITOR_MAX)
        if not ft.switch_to_vfo_mode():
            self.failed.emit(
                "VFO-Modus konnte nicht gesetzt werden — Speicher lesen "
                "funktioniert nur außerhalb des Memory-Modus."
            )
            return
        time.sleep(0.1)
        bank = MemoryChannelBank()
        total = MEMORY_EDITOR_MAX - MEMORY_EDITOR_MIN + 1
        try:
            for offset, num in enumerate(
                range(MEMORY_EDITOR_MIN, MEMORY_EDITOR_MAX + 1)
            ):
                if self._stop:
                    break
                try:
                    ch = ft.read_memory_editor_channel(num)
                except CatConnectionLostError:
                    self.connection_lost.emit()
                    return
                except CatError:
                    ch = MemoryEditorChannel.empty_slot(num)
                bank.channels[offset] = ch
                self.channel_read.emit(ch)
                self.progressed.emit(offset + 1, total)
                time.sleep(_INTER_CMD_DELAY_S)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(bank)


class _WriteWorker(QObject):
    progressed = Signal(int, int, str)
    finished = Signal()
    failed = Signal(str)
    connection_lost = Signal()

    def __init__(
        self,
        serial_cat: SerialCAT,
        channels: List[MemoryEditorChannel],
    ) -> None:
        super().__init__()
        self._cat = serial_cat
        self._channels = channels
        self._stop = False

    @Slot()
    def stop(self) -> None:
        self._stop = True

    @Slot()
    def run(self) -> None:
        ft = FT991CAT(self._cat)
        total = len(self._channels)
        self.progressed.emit(0, total, "Vorbereitung …")

        if not ft.switch_to_vfo_mode():
            self.failed.emit(
                "VFO-Modus konnte nicht gesetzt werden — Speicher schreiben "
                "funktioniert nur außerhalb des Memory-Modus."
            )
            return
        time.sleep(0.1)

        backup_dir = app_data_dir() / "memory_backups"
        try:
            bank = MemoryChannelBank(channels=list(self._channels))
            save_backup_json(bank, backup_path(backup_dir))
        except Exception:
            pass

        try:
            ordered = sorted(self._channels, key=lambda c: c.number)
            for i, ch in enumerate(ordered):
                if self._stop:
                    self.failed.emit("Schreiben abgebrochen.")
                    return
                self.progressed.emit(i, total, f"Kanal {ch.number:03d} …")
                try:
                    ft.write_memory_editor_channel(ch)
                except CatConnectionLostError:
                    self.connection_lost.emit()
                    return
                except CatError as exc:
                    self.failed.emit(f"Kanal {ch.number:03d}: {exc}")
                    return
                self.progressed.emit(i + 1, total, f"Kanal {ch.number:03d} OK")
                delay = (
                    _EMPTY_SLOT_WRITE_DELAY_S
                    if should_write_cleared(ch)
                    else _INTER_CMD_DELAY_S
                )
                time.sleep(delay)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit()


class MemoryEditorWorkerHost(QObject):
    """Startet Read-/Write-Threads für den Editor."""

    read_progress = Signal(int, int)
    read_channel = Signal(object)
    read_finished = Signal(object)
    write_progress = Signal(int, int, str)
    write_finished = Signal()
    operation_failed = Signal(str)
    connection_lost = Signal()

    def __init__(self, serial_cat: SerialCAT, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cat = serial_cat
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

    def _thread_is_running(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            self._clear_thread_refs()
            return False

    def _clear_thread_refs(self) -> None:
        self._thread = None
        self._worker = None

    @property
    def is_busy(self) -> bool:
        return self._thread_is_running()

    def _start_worker(self, worker: QObject, run_slot: str) -> None:
        if self._thread_is_running():
            self.stop()
        self._clear_thread_refs()
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(getattr(worker, run_slot))
        for sig in ("finished", "failed", "connection_lost"):
            if hasattr(worker, sig):
                getattr(worker, sig).connect(thread.quit)
        thread.finished.connect(self._clear_thread_refs)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start(QThread.HighestPriority)

    def start_read(self) -> None:
        worker = _ReadWorker(self._cat)
        worker.progressed.connect(self.read_progress)
        worker.channel_read.connect(self.read_channel)
        worker.finished.connect(self.read_finished)
        worker.failed.connect(self.operation_failed)
        worker.connection_lost.connect(self.connection_lost)
        self._start_worker(worker, "run")

    def start_write(self, channels: List[MemoryEditorChannel]) -> None:
        worker = _WriteWorker(self._cat, channels)
        worker.progressed.connect(self.write_progress)
        worker.finished.connect(self.write_finished)
        worker.failed.connect(self.operation_failed)
        worker.connection_lost.connect(self.connection_lost)
        self._start_worker(worker, "run")

    def stop(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                if hasattr(worker, "stop"):
                    worker.stop()  # type: ignore[union-attr]
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None:
            try:
                thread.wait(5000)
            except RuntimeError:
                pass
        self._clear_thread_refs()
