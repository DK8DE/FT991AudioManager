"""CAT-Warteschlange für Rig-Bridge — nutzt die bestehende :class:`SerialCAT`-Instanz."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from cat import CatConnectionLostError, CatError, CatNotConnectedError, FT991CAT
from cat.serial_cat import SerialCAT
from mapping.rx_mapping import RxMode

from .cat_commands import _normalize_hamlib_mode_name
from .exceptions import RigConnectionError
from .state import RadioStateCache


def _hamlib_mode_to_rx_mode(name: str) -> RxMode:
    m = _normalize_hamlib_mode_name(name)
    table: dict[str, RxMode] = {
        "LSB": RxMode.LSB,
        "USB": RxMode.USB,
        "CW": RxMode.CW_U,
        "CWR": RxMode.CW_L,
        "FM": RxMode.FM,
        "WFM": RxMode.FM,
        "AM": RxMode.AM,
        "AMN": RxMode.AM_N,
        "RTTY": RxMode.RTTY_LSB,
        "RTTYR": RxMode.RTTY_USB,
        "PKTLSB": RxMode.DATA_LSB,
        "PKTUSB": RxMode.DATA_USB,
        "PKTFM": RxMode.DATA_FM,
        "FMN": RxMode.FM_N,
        "C4FM": RxMode.C4FM,
        "PKTFMN": RxMode.FM_N,
    }
    return table.get(m, RxMode.USB)


@dataclass
class _WriteCommand:
    command: str
    log_ctx: str = ""
    enqueue_mono: float = 0.0


class Ft991SharedCatBackend:
    """Serialisiert Bridge-CAT-Befehle über die App-eigene ``SerialCAT``-Leitung."""

    def __init__(
        self,
        state: RadioStateCache,
        *,
        get_cat: Callable[[], SerialCAT],
        log_write: Callable[[str, str], None],
    ) -> None:
        self._state = state
        self._get_cat = get_cat
        self._log_write = log_write
        self._write_q: queue.Queue[_WriteCommand] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._last_setfreq_enqueue_mono = 0.0
        self._last_setfreq_target_hz = 0
        self._readfreq_suppress_until_mono = 0.0
        self._post_setfreq_read_suppress_s = 0.30
        self._readfreq_min_interval_s = 0.05
        self._last_readfreq_cat_mono = 0.0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="ft991-rig-bridge-cat",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=3.0)
        self._worker = None
        while True:
            try:
                self._write_q.get_nowait()
            except queue.Empty:
                break

    def is_serial_connected(self) -> bool:
        return self._get_cat().is_connected()

    def write_command(self, command: str, *, log_ctx: str = "") -> None:
        if not self.is_serial_connected():
            return
        cmd = str(command).strip()
        up = cmd.upper()
        if up.startswith("SETFREQ "):
            self._drop_pending_setfreq()
            mono = time.monotonic()
            self._last_setfreq_enqueue_mono = mono
            try:
                hz = int(cmd.split(None, 1)[1])
                self._last_setfreq_target_hz = hz
                self._state.update(frequency_hz=hz)
            except (IndexError, ValueError):
                pass
        self._write_q.put(
            _WriteCommand(command=cmd, log_ctx=log_ctx, enqueue_mono=time.monotonic())
        )

    def _drop_pending_setfreq(self) -> None:
        pending: list[_WriteCommand] = []
        while True:
            try:
                pending.append(self._write_q.get_nowait())
            except queue.Empty:
                break
        for item in pending:
            if not str(item.command).strip().upper().startswith("SETFREQ "):
                self._write_q.put(item)

    def _worker_loop(self) -> None:
        while self._running:
            try:
                item = self._write_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._dispatch(item)
            except Exception as exc:
                self._state.set_error(str(exc))
                if isinstance(exc, (CatConnectionLostError, CatNotConnectedError)):
                    self._state.update(connected=False)

    def _dispatch(self, item: _WriteCommand) -> None:
        cat = self._get_cat()
        if not cat.is_connected():
            raise RigConnectionError("CAT nicht verbunden")
        ft = FT991CAT(cat)
        up = item.command.strip().upper()
        ctx = (item.log_ctx or "").strip()
        if up.startswith("SETFREQ "):
            hz = int(item.command.split(None, 1)[1])
            ft.write_frequency(hz)
            self._readfreq_suppress_until_mono = (
                time.monotonic() + self._post_setfreq_read_suppress_s
            )
            if item.enqueue_mono >= self._last_setfreq_enqueue_mono:
                self._state.update(frequency_hz=hz)
                self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETFREQ {hz} Hz — {ctx}")
            return
        if up.startswith("SETMODE "):
            mode_name = item.command.split(None, 1)[1].strip()
            rx_mode = _hamlib_mode_to_rx_mode(mode_name)
            ft.set_rx_mode(rx_mode)
            self._state.update(mode=mode_name)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETMODE {mode_name} — {ctx}")
            return
        if up.startswith("SETPTT "):
            tail = (item.command.split(None, 1)[1] if " " in item.command else "").lower()
            on = tail in ("1", "on", "tx", "true", "yes")
            ft.set_cat_transmit(on, wait=False)
            self._state.update(ptt=on)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETPTT {'TX' if on else 'RX'} — {ctx}")
            return
        if up == "READFREQ":
            now = time.monotonic()
            if now < self._readfreq_suppress_until_mono:
                return
            if (now - self._last_readfreq_cat_mono) < self._readfreq_min_interval_s:
                return
            self._last_readfreq_cat_mono = now
            hz = ft.read_frequency()
            if (
                self._last_setfreq_target_hz > 0
                and abs(hz - self._last_setfreq_target_hz) > 20
                and (now - self._last_setfreq_enqueue_mono)
                < self._post_setfreq_read_suppress_s * 3
            ):
                return
            self._state.update(frequency_hz=hz)
            self._state.mark_success()
            return
        raise RigConnectionError(f"Unbekannter Bridge-Befehl: {item.command!r}")
