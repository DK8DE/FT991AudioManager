"""Hintergrund-Worker zum Einlesen der Speicherkanaele.

Liest sequentiell alle 117 Speicherkanaele des FT-991/991A mit
``MTnnn;`` aus und emittiert pro nicht-leerem Kanal ein Signal.

Design-Punkte:

* Worker laeuft in einem eigenen :class:`QThread`, damit die GUI
  waehrend des ~2-4 s langen Roundtrips reagierbar bleibt.
* Die ``SerialCAT``-Instanz serialisiert Zugriffe per ``RLock`` —
  unser Loader teilt sich die Leitung problemlos mit dem ``MeterPoller``.
  Damit Live-Polling nicht zu sehr ruckelt, pausieren wir alle paar
  Reads kurz (siehe ``_PAUSE_EVERY``).
* Leere Kanaele (``parse_mt_or_empty`` liefert ``None``) oder Kanaele,
  die das Funkgeraet mit ``?;`` ablehnt, werden uebersprungen.
* Bei Connection-Lost feuert :attr:`connection_lost` und der Worker
  beendet sich.

Verwendung::

    loader = MemoryChannelLoader(serial_cat)
    loader.channel_loaded.connect(self._on_channel)
    loader.finished.connect(self._on_done)
    loader.start()
    ...
    loader.stop()   # vorzeitig
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from cat import (
    CatCommandUnsupportedError,
    CatConnectionLostError,
    CatError,
    FT991CAT,
    SerialCAT,
)
from mapping.memory_mapping import (
    MEMORY_CHANNEL_MAX,
    MEMORY_CHANNEL_MIN,
    MemoryChannel,
)


class _MemoryLoaderWorker(QObject):
    """Eigentlicher Worker — laeuft im Background-Thread.

    Schluerft die Speicherkanaele so schnell wie der serielle Port liefert.
    Wir bauen *keine* Pausen ein — das Orchestrieren mit dem MeterPoller
    macht das MainWindow, indem es den Poller waehrend des Loads
    pausiert. So bekommt der Loader die volle Bandbreite (38400 Baud
    schaffen ~30 Reads/s).
    """

    channel_loaded = Signal(object)   # MemoryChannel
    progressed = Signal(int, int)     # (current, total)
    finished = Signal(int)            # Anzahl gefundener Kanaele
    failed = Signal(str)
    connection_lost = Signal()

    def __init__(self, serial_cat: SerialCAT) -> None:
        super().__init__()
        self._cat = serial_cat
        self._stop_requested = False

    @Slot()
    def stop(self) -> None:
        self._stop_requested = True

    @Slot()
    def run(self) -> None:
        ft = FT991CAT(self._cat)
        total = MEMORY_CHANNEL_MAX - MEMORY_CHANNEL_MIN + 1
        found = 0
        log = self._cat.get_log()
        if log is not None:
            log.log_info(
                f"=== Speicherkanaele laden ({MEMORY_CHANNEL_MIN}..."
                f"{MEMORY_CHANNEL_MAX}) ==="
            )
        try:
            for offset, channel in enumerate(
                range(MEMORY_CHANNEL_MIN, MEMORY_CHANNEL_MAX + 1)
            ):
                if self._stop_requested:
                    if log is not None:
                        log.log_info(
                            f"Speicherkanal-Load abgebrochen bei #{channel}"
                        )
                    break
                try:
                    result: Optional[MemoryChannel] = ft.read_memory_channel_tag(channel)
                except CatCommandUnsupportedError:
                    # Funkgeraet kennt den Kanal nicht — z. B. ueber 99,
                    # wenn die PMS-Bereich-Slots leer sind.
                    result = None
                except CatConnectionLostError:
                    self.connection_lost.emit()
                    return
                except CatError as exc:
                    if log is not None:
                        log.log_warn(
                            f"Speicherkanal {channel:03d} uebersprungen: {exc}"
                        )
                    result = None

                self.progressed.emit(offset + 1, total)
                if result is not None:
                    found += 1
                    self.channel_loaded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Speicherkanal-Load fehlgeschlagen: {exc}")
            return
        if log is not None:
            log.log_info(
                f"Speicherkanal-Load fertig: {found} belegte Kanaele "
                f"von {total} geprueft"
            )
        self.finished.emit(found)


class MemoryChannelLoader(QObject):
    """Komfort-Wrapper um Worker + Thread + Lifecycle.

    Re-emittiert die Worker-Signale, ohne dass die GUI die Thread-Mechanik
    sehen muss. Mehrfach-Start ist erlaubt — laufende Loads werden vorher
    gestoppt.
    """

    channel_loaded = Signal(object)   # MemoryChannel
    progressed = Signal(int, int)
    finished = Signal(int)
    failed = Signal(str)
    connection_lost = Signal()

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cat = serial_cat
        self._thread: Optional[QThread] = None
        self._worker: Optional[_MemoryLoaderWorker] = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            # Qt-Objekt wurde inzwischen geloescht (deleteLater).
            return False

    def start(self) -> None:
        """Startet einen neuen Lade-Vorgang. Vorhandene Loads werden
        kooperativ gestoppt und auf Beendigung gewartet."""
        if self.is_running:
            self.stop(wait=True)
        thread = QThread(self)
        worker = _MemoryLoaderWorker(self._cat)
        worker.moveToThread(thread)

        worker.channel_loaded.connect(self.channel_loaded)
        worker.progressed.connect(self.progressed)
        worker.finished.connect(self.finished)
        worker.failed.connect(self.failed)
        worker.connection_lost.connect(self.connection_lost)

        thread.started.connect(worker.run)
        for sig in (worker.finished, worker.failed, worker.connection_lost):
            sig.connect(thread.quit)
        # Wir uebergeben das Aufraeumen vollstaendig an Qts deleteLater()
        # und setzen die Python-Referenzen *erst* beim naechsten
        # ``start()`` neu — so wandern wir nicht in einen Double-Delete-
        # Race zwischen Pythons GC und Qts deleteLater().
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        # HighestPriority: dem Loader-Thread maximalen Vortritt geben,
        # damit der serielle Roundtrip ohne Scheduler-Latenz fuer das
        # GUI-Polling laeuft. Wirkt im Verbund mit dem
        # ``MeterPoller``-Pausenmechanismus im MainWindow.
        thread.start(QThread.HighestPriority)

    def stop(self, *, wait: bool = False) -> None:
        # Sicherheits-Snapshots: zwischen ``stop()`` und tatsaechlicher
        # Thread-Beendigung kann Qt die Objekte schon weg-deleteLatered
        # haben. Wir kapseln die Zugriffe deshalb in try/except RuntimeError.
        worker = self._worker
        thread = self._thread
        if worker is not None:
            try:
                worker.stop()
            except RuntimeError:
                pass
        if wait and thread is not None:
            try:
                # max. 3 s — der Worker bricht spaetestens nach dem
                # aktuellen Lese-Roundtrip ab.
                thread.wait(3000)
            except RuntimeError:
                pass
