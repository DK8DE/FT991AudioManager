"""Thread-sicheres Logging der CAT-Kommunikation.

Pures Python — kennt **kein** PySide6, damit der CAT-Layer wie bisher
Qt-frei und gut testbar bleibt. Die GUI hängt sich über ein Observer-
Callback an. Das Callback wird häufig aus einem Worker-Thread aufgerufen;
in der GUI muss es daher per Qt-Signal an den Hauptthread weitergereicht
werden (siehe :class:`gui.log_widget.LogBridge`).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, List, Optional


class LogLevel(str, Enum):
    TX = "TX"
    RX = "RX"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


@dataclass(frozen=True)
class LogEntry:
    """Ein einzelner Log-Eintrag."""

    timestamp: float
    level: LogLevel
    text: str

    def formatted(self) -> str:
        local = time.localtime(self.timestamp)
        ms = int((self.timestamp - int(self.timestamp)) * 1000)
        ts = f"{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d}.{ms:03d}"
        return f"{ts}  {self.level.value:<5}  {self.text}"


Observer = Callable[[LogEntry], None]


class CatLog:
    """Sammelt CAT-Log-Einträge und benachrichtigt Observer.

    - Thread-safe (alle internen Operationen sind unter einem Lock).
    - Begrenzte Kapazität (älteste Einträge fallen heraus).
    - Observer dürfen aus beliebigen Threads angemeldet/abgemeldet werden.
    """

    def __init__(self, max_entries: int = 4000) -> None:
        self._lock = threading.Lock()
        self._entries: Deque[LogEntry] = deque(maxlen=max_entries)
        self._observers: List[Observer] = []
        self._cleared_observers: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Observer-Verwaltung
    # ------------------------------------------------------------------

    def add_observer(self, observer: Observer) -> None:
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def remove_observer(self, observer: Observer) -> None:
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

    def add_cleared_observer(self, observer: Callable[[], None]) -> None:
        with self._lock:
            if observer not in self._cleared_observers:
                self._cleared_observers.append(observer)

    # ------------------------------------------------------------------
    # Einträge hinzufügen
    # ------------------------------------------------------------------

    def add(self, level: LogLevel, text: str) -> LogEntry:
        entry = LogEntry(timestamp=time.time(), level=level, text=text)
        with self._lock:
            self._entries.append(entry)
            observers = list(self._observers)
        for obs in observers:
            try:
                obs(entry)
            except Exception:
                # Observer-Fehler dürfen den CAT-Pfad nicht stören.
                pass
        return entry

    def log_tx(self, command: str) -> LogEntry:
        # Anzeige inkl. Semikolon — auch wenn der Anwender es vergessen hat.
        cmd = command if command.endswith(";") else command + ";"
        return self.add(LogLevel.TX, cmd)

    def log_rx(self, response: str) -> LogEntry:
        return self.add(LogLevel.RX, response)

    def log_info(self, text: str) -> LogEntry:
        return self.add(LogLevel.INFO, text)

    def log_warn(self, text: str) -> LogEntry:
        return self.add(LogLevel.WARN, text)

    def log_error(self, text: str) -> LogEntry:
        return self.add(LogLevel.ERROR, text)

    def log_debug(self, text: str) -> LogEntry:
        return self.add(LogLevel.DEBUG, text)

    # ------------------------------------------------------------------
    # Lese-Operationen
    # ------------------------------------------------------------------

    def snapshot(self) -> List[LogEntry]:
        with self._lock:
            return list(self._entries)

    def dump_text(self) -> str:
        return "\n".join(e.formatted() for e in self.snapshot())

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            observers = list(self._cleared_observers)
        for obs in observers:
            try:
                obs()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Komfort
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Standardmäßiger globaler Log — bequem für Stellen, an denen kein
# explizites Log gereicht werden kann (Tests benutzen lokale Instanzen).
default_log: Optional[CatLog] = None
