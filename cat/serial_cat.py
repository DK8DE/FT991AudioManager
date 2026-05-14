"""Niedrige CAT-Schicht: serielle Verbindung + generisches Senden/Empfangen.

Diese Klasse weiß noch nichts vom FT-991A. Sie kümmert sich nur um:

- Auflisten der verfügbaren seriellen Ports
- Öffnen / Schließen einer seriellen Verbindung
- Senden von ASCII-Kommandos (mit oder ohne ``;`` am Ende)
- Lesen einer einzelnen Antwort, die bei FT-Yaesu-Geräten typischerweise
  mit ``;`` terminiert wird

Threading: Alle öffentlichen Methoden (``connect``, ``disconnect``,
``send_command``) sind über einen internen ``RLock`` synchronisiert.
Dadurch können der Profile-Worker und der Meter-Poller gleichzeitig auf
dieselbe Instanz zugreifen — die CAT-Operationen werden sauber
serialisiert. ``is_connected()`` ist bewusst lock-frei (für sehr häufige
UI-Aufrufe) und liefert eine Momentaufnahme.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import serial
import serial.tools.list_ports

from .cat_errors import CatConnectionLostError, CatNotConnectedError, CatTimeoutError
from .cat_log import CatLog


TERMINATOR = b";"
DEFAULT_BAUDRATE = 38400
DEFAULT_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class PortInfo:
    """Beschreibung eines verfügbaren seriellen Ports."""

    device: str
    """Geräteknoten — unter Windows z. B. ``COM5``, unter Linux ``/dev/ttyUSB0``."""

    description: str
    """Lesbare Beschreibung, z. B. ``Silicon Labs CP210x USB to UART Bridge``."""

    hwid: str
    """Hardware-ID (USB VID:PID etc.), nützlich zur Wiedererkennung."""

    @property
    def display(self) -> str:
        """Anzeige für Dropdowns: ``COM5 — Silicon Labs CP210x ...``."""
        if self.description and self.description != "n/a":
            return f"{self.device} — {self.description}"
        return self.device


class SerialCAT:
    """Synchrone serielle CAT-Schnittstelle für Yaesu-Geräte.

    Verwendung::

        cat = SerialCAT()
        for port in SerialCAT.list_ports():
            print(port.display)

        cat.connect("COM5", baudrate=38400, timeout_ms=1000)
        try:
            reply = cat.send_command("ID;")
            print(reply)  # z. B. "ID0570;"
        finally:
            cat.disconnect()
    """

    def __init__(self, log: Optional[CatLog] = None) -> None:
        self._serial: Optional[serial.Serial] = None
        # RLock, damit connect() intern disconnect() rufen darf, ohne sich
        # selbst zu blockieren.
        self._lock = threading.RLock()
        self._port: Optional[str] = None
        self._baudrate: int = DEFAULT_BAUDRATE
        self._timeout_s: float = DEFAULT_TIMEOUT_MS / 1000.0
        self._log: Optional[CatLog] = log

    # ------------------------------------------------------------------
    # Log-Anbindung
    # ------------------------------------------------------------------

    def set_log(self, log: Optional[CatLog]) -> None:
        """Hängt einen :class:`CatLog` an oder entfernt ihn (``None``)."""
        self._log = log

    def get_log(self) -> Optional[CatLog]:
        return self._log

    # ------------------------------------------------------------------
    # Port-Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_ports() -> List[PortInfo]:
        """Liefert die aktuell verfügbaren seriellen Ports."""
        result: List[PortInfo] = []
        for info in serial.tools.list_ports.comports():
            result.append(
                PortInfo(
                    device=info.device,
                    description=info.description or "",
                    hwid=info.hwid or "",
                )
            )
        result.sort(key=lambda p: p.device.lower())
        return result

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------

    def connect(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        bytesize: int = serial.EIGHTBITS,
        parity: str = serial.PARITY_NONE,
        stopbits: float = serial.STOPBITS_ONE,
    ) -> None:
        """Öffnet die serielle Verbindung. Wirft :class:`serial.SerialException`,
        falls der Port nicht geöffnet werden kann."""
        with self._lock:
            self.disconnect()
            timeout_s = max(0.05, timeout_ms / 1000.0)
            if self._log is not None:
                self._log.log_info(
                    f"connect: port={port} baud={baudrate} timeout={timeout_ms} ms"
                )
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    bytesize=bytesize,
                    parity=parity,
                    stopbits=stopbits,
                    timeout=timeout_s,
                    write_timeout=timeout_s,
                    rtscts=False,
                    dsrdtr=False,
                    xonxoff=False,
                )
            except (serial.SerialException, OSError) as exc:
                if self._log is not None:
                    self._log.log_error(f"connect fehlgeschlagen: {exc}")
                raise
            # Manche USB-UART-Bridges brauchen einen kurzen Moment, bevor sie
            # sauber reagieren. Buffer entleeren, damit alte Daten unsere erste
            # Antwort nicht verfälschen.
            time.sleep(0.05)
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass

            self._serial = ser
            self._port = port
            self._baudrate = baudrate
            self._timeout_s = timeout_s

    def disconnect(self) -> None:
        """Schließt die Verbindung, falls offen."""
        with self._lock:
            ser = self._serial
            was_open = ser is not None and getattr(ser, "is_open", False)
            old_port = self._port
            self._serial = None
            self._port = None
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            if was_open and self._log is not None:
                self._log.log_info(f"disconnect: port={old_port}")

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> Optional[str]:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    # ------------------------------------------------------------------
    # Kommandos
    # ------------------------------------------------------------------

    def send_command(self, command: str, *, read_response: bool = True) -> str:
        """Sendet ein CAT-Kommando.

        - ``command`` darf mit oder ohne ``;`` enden; das Semikolon wird bei
          Bedarf ergänzt.
        - Wenn ``read_response`` ``True`` ist, wird bis zum nächsten ``;``
          gelesen und der gesamte String (inkl. ``;``) zurückgegeben.
        - Bei ``read_response=False`` wird ein leerer String geliefert.

        Wirft :class:`CatNotConnectedError`, wenn keine Verbindung offen ist,
        :class:`CatConnectionLostError`, wenn die Verbindung während des
        Roundtrips wegbricht (USB gezogen, Gerät aus), oder
        :class:`CatTimeoutError`, wenn keine vollständige Antwort innerhalb
        des Timeouts eingeht.
        """
        if not command:
            raise ValueError("command darf nicht leer sein")
        if not command.endswith(";"):
            command = command + ";"

        with self._lock:
            ser = self._serial
            if ser is None or not ser.is_open:
                if self._log is not None:
                    self._log.log_error(
                        f"send_command ohne offene Verbindung: {command!r}"
                    )
                raise CatNotConnectedError("Serielle Verbindung ist nicht offen.")

            try:
                ser.reset_input_buffer()
            except (serial.SerialException, OSError) as exc:
                # Schon hier sieht man, dass der Port weg ist. Aufräumen und
                # mit ConnectionLost weitermachen.
                self._handle_connection_lost(command, exc)
                raise CatConnectionLostError(
                    f"Port {self._port} während reset_input_buffer verloren: {exc}"
                ) from exc
            except Exception:
                # Andere Probleme (Wartezeit, ...) ignorieren wir wie bisher.
                pass

            if self._log is not None:
                self._log.log_tx(command)

            data = command.encode("ascii", errors="replace")
            try:
                ser.write(data)
                ser.flush()
            except (serial.SerialException, OSError) as exc:
                self._handle_connection_lost(command, exc)
                raise CatConnectionLostError(
                    f"Schreibfehler auf {self._port}: {exc}"
                ) from exc

            if not read_response:
                return ""

            try:
                response = self._read_until_terminator(ser)
            except CatTimeoutError:
                if self._log is not None:
                    self._log.log_error(
                        f"Timeout nach {command!r} ({int(self._timeout_s * 1000)} ms)"
                    )
                raise
            except (serial.SerialException, OSError) as exc:
                self._handle_connection_lost(command, exc)
                raise CatConnectionLostError(
                    f"Lesefehler auf {self._port}: {exc}"
                ) from exc

            if self._log is not None:
                self._log.log_rx(response)
            return response

    def _handle_connection_lost(self, command: str, exc: BaseException) -> None:
        """Wird intern aufgerufen, sobald pyserial einen IO-Fehler meldet.

        Schließt die Verbindung und loggt dezent (WARN statt ERROR), damit
        der CAT-Log nicht bei jedem USB-Wackler in Rot blinkt.
        """
        port = self._port
        if self._log is not None:
            self._log.log_warn(
                f"Verbindung zu {port} verloren bei {command!r}: {exc}"
            )
        # ``disconnect`` ist re-entrant über RLock — wir sind hier ohnehin
        # innerhalb des with-self._lock-Blocks.
        ser = self._serial
        self._serial = None
        self._port = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _read_until_terminator(self, ser: "serial.Serial") -> str:
        """Liest bytes vom Port, bis ``;`` empfangen wurde oder der Timeout
        abläuft. ``serial.Serial.read`` respektiert bereits den konfigurierten
        Timeout; ``read_until`` liefert frühzeitig, sobald das Terminator-Byte
        gefunden wurde."""
        deadline = time.monotonic() + self._timeout_s + 0.05
        buffer = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # read_until liest bis zum Terminator ODER bis Timeout.
            chunk = ser.read_until(expected=TERMINATOR, size=256)
            if chunk:
                buffer.extend(chunk)
                if TERMINATOR in chunk:
                    break
            else:
                # Nichts gelesen -> Timeout im Serial-Layer. Schleife endet
                # spätestens über die Deadline-Prüfung.
                break

        if not buffer or TERMINATOR not in buffer:
            raise CatTimeoutError(
                f"Keine vollständige CAT-Antwort innerhalb von "
                f"{int(self._timeout_s * 1000)} ms empfangen."
            )

        # Antwort kann theoretisch mehr als ein Frame enthalten, falls das
        # Gerät unaufgefordert sendet (z. B. AI-Modus). Wir liefern alles bis
        # einschließlich des ersten ``;`` zurück.
        terminator_index = buffer.index(TERMINATOR)
        response = buffer[: terminator_index + 1].decode("ascii", errors="replace")
        return response


# ----------------------------------------------------------------------
# CLI-Smoke-Test
# ----------------------------------------------------------------------


def _print_ports() -> None:
    ports = SerialCAT.list_ports()
    if not ports:
        print("Keine seriellen Ports gefunden.")
        return
    print(f"{len(ports)} Port(s) gefunden:")
    for port in ports:
        desc = f" - {port.description}" if port.description else ""
        print(f"  {port.device}{desc}")
        if port.hwid:
            print(f"    HWID: {port.hwid}")


if __name__ == "__main__":  # pragma: no cover
    _print_ports()
