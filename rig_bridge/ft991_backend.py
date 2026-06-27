"""CAT-Warteschlange für Rig-Bridge — nutzt die bestehende :class:`SerialCAT`-Instanz."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from cat import CatConnectionLostError, CatError, CatNotConnectedError, FT991CAT
from cat.serial_cat import SerialCAT
from mapping.meter_mapping import MeterKind
from mapping.rx_mapping import RxMode

from .flrig_values import (
    agc_mode_to_flrig,
    cat_0_255_to_flrig_percent,
    flrig_percent_to_cat_0_255,
    flrig_sideband_from_mode,
    flrig_to_agc_mode,
    format_flrig_dbm,
    format_flrig_pwrmeter,
    format_flrig_smeter,
    format_flrig_sunits,
    format_flrig_swr,
    notch_hz_from_auto_notch,
    rx_mode_to_flrig_name,
    sh_find_p2_for_hz,
    sh_hz_for_mode_p2,
)

from .cat_commands import _normalize_hamlib_mode_name
from .exceptions import RigConnectionError
from .state import RadioStateCache


def _bridge_mode_to_rx_mode(name: str) -> RxMode:
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
        on_frequency_written: Optional[Callable[..., None]] = None,
    ) -> None:
        self._state = state
        self._get_cat = get_cat
        self._log_write = log_write
        self._on_frequency_written = on_frequency_written
        self._write_q: queue.Queue[_WriteCommand] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._last_setfreq_enqueue_mono = 0.0
        self._last_setfreq_target_hz = 0
        self._readfreq_suppress_until_mono = 0.0
        self._post_setfreq_read_suppress_s = 0.30
        self._readfreq_min_interval_s = 0.05
        self._last_readfreq_cat_mono = 0.0
        #: FLRig ``main.get_frequency`` (read_frequency_sync): nicht öfter als das hier vom TRX lesen,
        #: sonst blockiert jedes ``FA;`` den Worker + Meter-Poller auf derselben RLock.
        self._sync_f_min_cat_s = 0.10
        self._last_sync_f_cat_mono = 0.0
        self._sync_meter_min_cat_s = 0.15
        self._last_sync_meter_mono = 0.0

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

    def pending_write_count(self) -> int:
        """Anzahl noch nicht abgearbeiteter Bridge-Befehle (SETFREQ/PTT/…)."""
        return int(self._write_q.qsize())

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
        elif up == "READFREQ":
            # Mehrere asynchrone Abfragen (FLRig/UI) zusammenfallen lassen — sonst
            # staut die Schlange und SETFREQ/SETMODE warten Sekunden.
            self._drop_pending_readfreq()
        elif up == "READSTATE":
            self._drop_pending_readstate()
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

    def _drop_pending_readfreq(self) -> None:
        pending: list[_WriteCommand] = []
        while True:
            try:
                pending.append(self._write_q.get_nowait())
            except queue.Empty:
                break
        for item in pending:
            if str(item.command).strip().upper() != "READFREQ":
                self._write_q.put(item)

    def _drop_pending_readstate(self) -> None:
        pending: list[_WriteCommand] = []
        while True:
            try:
                pending.append(self._write_q.get_nowait())
            except queue.Empty:
                break
        for item in pending:
            if str(item.command).strip().upper() != "READSTATE":
                self._write_q.put(item)

    def refresh_frequency_from_cat(self) -> None:
        """Liest VFO-A sofort und aktualisiert den Bridge-State (FLRig-Abfragen)."""
        if not self.is_serial_connected():
            return
        now = time.monotonic()
        if now < self._readfreq_suppress_until_mono:
            return
        try:
            ft = FT991CAT(self._get_cat())
            hz = ft.read_frequency()
            self._state.update(frequency_hz=hz)
            self._state.mark_success()
            self._last_readfreq_cat_mono = now
            self._last_sync_f_cat_mono = now
        except Exception as exc:
            self._state.set_error(str(exc))

    def read_frequency_sync(self) -> int:
        """Liest VFO-A sofort per CAT (SerialCAT-RLock — serialisiert mit Bridge-Worker)."""
        snap = self._state.snapshot()
        cached = int(snap.get("frequency_hz", 0) or 0)
        if not self.is_serial_connected():
            return max(0, cached)
        now = time.monotonic()
        if now < self._readfreq_suppress_until_mono:
            return max(0, cached)
        # Kurz nach SETFREQ: Cache ist bereits Zielwert — kein zusätzliches ``FA;``,
        # das den Worker am Senden hindert (viele Clients fragen ``f`` kurz nach ``F``).
        if (now - self._last_setfreq_enqueue_mono) < 0.18 and cached > 0:
            return max(0, cached)
        if (now - self._last_sync_f_cat_mono) < self._sync_f_min_cat_s and cached > 0:
            return max(0, cached)
        try:
            cat = self._get_cat()
            if not cat.is_connected():
                return max(0, cached)
            ft = FT991CAT(cat)
            hz = ft.read_frequency()
            if now < self._readfreq_suppress_until_mono:
                if self._last_setfreq_target_hz > 0:
                    hz = self._last_setfreq_target_hz
            self._state.update(frequency_hz=hz)
            self._state.mark_success()
            self._last_readfreq_cat_mono = now
            self._last_sync_f_cat_mono = time.monotonic()
            return hz
        except Exception as exc:
            self._state.set_error(str(exc))
            if isinstance(exc, (CatConnectionLostError, CatNotConnectedError)):
                self._state.update(connected=False)
            return max(0, cached)

    def _ft(self) -> FT991CAT:
        return FT991CAT(self._get_cat())

    def _apply_rx_mode_to_state(self, ft: FT991CAT) -> str:
        rx_mode = ft.read_rx_mode()
        name = rx_mode_to_flrig_name(rx_mode)
        self._state.update(
            mode=name,
            sideband=flrig_sideband_from_mode(name),
        )
        return name

    def _apply_meter_snapshot(self, ft: FT991CAT, *, freq_hz: int) -> None:
        sm = ft.read_smeter()
        try:
            swr = ft.read_meter(MeterKind.SWR)
        except Exception:
            swr = 0
        try:
            po = ft.read_meter(MeterKind.PO)
        except Exception:
            po = 0
        self._state.update(
            smeter_raw=int(sm),
            swr_raw=int(swr),
            po_raw=int(po),
        )

    def _apply_levels_snapshot(self, ft: FT991CAT) -> None:
        vol = cat_0_255_to_flrig_percent(ft.read_af_gain())
        rfg = cat_0_255_to_flrig_percent(ft.read_rf_gain())
        mic = int(ft.get_mic_gain())
        pc = int(ft.read_pc_power_watts())
        agc = agc_mode_to_flrig(ft.read_agc())
        notch_on = ft.read_auto_notch()
        rx_mode = ft.read_rx_mode()
        p2 = ft.read_tx_bandwidth_sh()
        bw_hz = sh_hz_for_mode_p2(rx_mode, p2)
        ptt = ft.get_tx_status()
        try:
            shift = ft.read_if_shift_direction()
            split = int(shift) != 0
        except Exception:
            split = bool(self._state.split)
        self._state.update(
            volume=vol,
            rfgain=rfg,
            micgain=mic,
            power_pc=pc,
            agc=agc,
            notch_hz=notch_hz_from_auto_notch(notch_on),
            bandwidth_hz=bw_hz,
            ptt=bool(ptt),
            split=split,
            sideband=flrig_sideband_from_mode(self._state.mode),
        )

    def sync_refresh_for_flrig(self, method: str) -> None:
        """Synchroner CAT-Lesezyklus für eine FLRig-XML-RPC-Methode."""
        if not self.is_serial_connected():
            return
        m = (method or "").strip()
        now = time.monotonic()
        ft = self._ft()
        patch: dict[str, object] = {}
        try:
            if m in (
                "rig.get_vfoA",
                "main.get_frequency",
                "main.get_freq",
                "rig.get_vfo",
            ):
                if m == "rig.get_vfo" and str(self._state.vfo).upper() == "B":
                    hz_b = ft.read_frequency_b()
                    patch["frequency_hz_b"] = hz_b
                else:
                    hz = ft.read_frequency()
                    patch["frequency_hz"] = hz
            elif m == "rig.get_vfoB":
                patch["frequency_hz_b"] = ft.read_frequency_b()
            elif m.startswith("rig.get_mode") or m == "rig.get_sideband":
                name = self._apply_rx_mode_to_state(ft)
                patch["mode"] = name
                patch["sideband"] = flrig_sideband_from_mode(name)
            elif m in (
                "rig.get_DBM",
                "rig.get_smeter",
                "rig.get_swrmeter",
                "rig.get_SWR",
                "rig.get_Sunits",
                "rig.get_pwrmeter",
            ):
                if (now - self._last_sync_meter_mono) >= self._sync_meter_min_cat_s:
                    freq = int(self._state.frequency_hz or 0)
                    if freq <= 0:
                        freq = ft.read_frequency()
                        patch["frequency_hz"] = freq
                    self._apply_meter_snapshot(ft, freq_hz=freq)
                    self._last_sync_meter_mono = time.monotonic()
            elif m in (
                "rig.get_volume",
                "rig.get_rfgain",
                "rig.get_micgain",
                "rig.get_power",
                "rig.get_agc",
                "rig.get_notch",
            ):
                self._apply_levels_snapshot(ft)
                patch.update(
                    {
                        "volume": self._state.volume,
                        "rfgain": self._state.rfgain,
                        "micgain": self._state.micgain,
                        "power_pc": self._state.power_pc,
                        "agc": self._state.agc,
                        "notch_hz": self._state.notch_hz,
                    }
                )
            elif m in ("rig.get_bwA", "rig.get_bwB", "rig.get_bw"):
                rx_mode = ft.read_rx_mode()
                p2 = ft.read_tx_bandwidth_sh()
                bw_hz = sh_hz_for_mode_p2(rx_mode, p2)
                patch["bandwidth_hz"] = bw_hz
            elif m == "rig.get_split":
                shift = ft.read_if_shift_direction()
                patch["split"] = int(shift) != 0
            elif m == "rig.get_ptt":
                patch["ptt"] = bool(ft.get_tx_status())
            elif m == "rig.get_AB":
                patch["vfo"] = str(self._state.vfo or "A")
            elif m == "rig.cat_string":
                patch["cat_string_response"] = str(self._state.cat_string_response or "")
            if patch:
                self._state.update(**patch)
                self._state.mark_success()
        except Exception as exc:
            self._state.set_error(str(exc))
            if isinstance(exc, (CatConnectionLostError, CatNotConnectedError)):
                self._state.update(connected=False)

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
            # Mehrere nacheinander eingetroffene SETFREQ (Vfo-Ziehen) in einem
            # RLock-Zyklus zusammenfassen — sonst sendet das TRX jeden Zwischenwert.
            merged = item
            while True:
                try:
                    nxt = self._write_q.get_nowait()
                except queue.Empty:
                    break
                nup = str(nxt.command).strip().upper()
                if nup.startswith("SETFREQ "):
                    merged = nxt
                    continue
                self._write_q.put(nxt)
                break
            hz = int(str(merged.command).split(None, 1)[1])
            ctx = (merged.log_ctx or "").strip()
            ft.write_frequency(hz)
            self._readfreq_suppress_until_mono = (
                time.monotonic() + self._post_setfreq_read_suppress_s
            )
            if merged.enqueue_mono >= self._last_setfreq_enqueue_mono:
                self._last_setfreq_target_hz = hz
                self._state.update(frequency_hz=hz)
                self._state.mark_success()
            cb = self._on_frequency_written
            if cb is not None:
                try:
                    cb(int(hz), False)
                except TypeError:
                    try:
                        cb(int(hz))
                    except Exception:
                        pass
                except Exception:
                    pass
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETFREQ {hz} Hz — {ctx}")
            return
        if up.startswith("SETMODE "):
            mode_name = item.command.split(None, 1)[1].strip()
            rx_mode = _bridge_mode_to_rx_mode(mode_name)
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
            if now < self._readfreq_suppress_until_mono:
                if self._last_setfreq_target_hz > 0:
                    hz = self._last_setfreq_target_hz
            self._state.update(frequency_hz=hz)
            self._state.mark_success()
            return
        if up == "READSTATE":
            hz = ft.read_frequency()
            hz_b = ft.read_frequency_b()
            self._apply_rx_mode_to_state(ft)
            self._apply_levels_snapshot(ft)
            self._apply_meter_snapshot(ft, freq_hz=hz)
            self._state.update(frequency_hz=hz, frequency_hz_b=hz_b)
            self._state.mark_success()
            return
        if up.startswith("SETFREQB "):
            hz = int(str(item.command).split(None, 1)[1])
            ft.write_frequency_b(hz)
            self._state.update(frequency_hz_b=hz)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETFREQB {hz} Hz — {ctx}")
            return
        if up.startswith("SETVOL "):
            pct = max(0, min(100, int(item.command.split(None, 1)[1])))
            ft.write_af_gain(flrig_percent_to_cat_0_255(pct))
            self._state.update(volume=pct)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETVOL {pct}% — {ctx}")
            return
        if up.startswith("SETRFGAIN "):
            pct = max(0, min(100, int(item.command.split(None, 1)[1])))
            ft.write_rf_gain(flrig_percent_to_cat_0_255(pct))
            self._state.update(rfgain=pct)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETRFGAIN {pct}% — {ctx}")
            return
        if up.startswith("SETMICGAIN "):
            pct = max(0, min(100, int(item.command.split(None, 1)[1])))
            ft.set_mic_gain(pct, tx_lock=True)
            self._state.update(micgain=pct)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETMICGAIN {pct}% — {ctx}")
            return
        if up.startswith("SETPOWER "):
            watts = max(0, int(item.command.split(None, 1)[1]))
            ft.set_pc_power_watts(watts, tx_lock=True)
            self._state.update(power_pc=watts)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETPOWER {watts} W — {ctx}")
            return
        if up.startswith("SETAGC "):
            agc_i = int(item.command.split(None, 1)[1])
            ft.write_agc(flrig_to_agc_mode(agc_i))
            self._state.update(agc=agc_i)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETAGC {agc_i} — {ctx}")
            return
        if up.startswith("SETNOTCH "):
            hz_n = int(item.command.split(None, 1)[1])
            ft.write_auto_notch(hz_n > 0)
            self._state.update(notch_hz=notch_hz_from_auto_notch(hz_n > 0))
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETNOTCH {hz_n} — {ctx}")
            return
        if up.startswith("SETBW "):
            bw = int(item.command.split(None, 1)[1])
            rx_mode = ft.read_rx_mode()
            p2 = sh_find_p2_for_hz(rx_mode, bw)
            ft.write_tx_bandwidth_sh(p2)
            self._state.update(bandwidth_hz=sh_hz_for_mode_p2(rx_mode, p2))
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETBW {bw} Hz — {ctx}")
            return
        if up.startswith("SETSPLIT "):
            on = item.command.split(None, 1)[1].strip() in ("1", "on", "true", "yes")
            if on:
                ft.try_set_repeater_shift_minus()
            else:
                ft.try_set_repeater_shift_simplex()
            self._state.update(split=on)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SETSPLIT {'ON' if on else 'OFF'} — {ctx}")
            return
        if up == "SWAPVFO":
            ft.swap_vfo_a_and_b()
            hz_a = ft.read_frequency()
            hz_b = ft.read_frequency_b()
            self._state.update(frequency_hz=hz_a, frequency_hz_b=hz_b)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge SWAPVFO — {ctx}")
            return
        if up == "VFOA2B":
            ft._cat.send_command("AB;", read_response=False)
            hz_b = ft.read_frequency_b()
            self._state.update(frequency_hz_b=hz_b)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge VFOA2B — {ctx}")
            return
        if up == "FREQA2B":
            hz = ft.read_frequency()
            ft.write_frequency_b(hz)
            self._state.update(frequency_hz_b=hz)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge FREQA2B — {ctx}")
            return
        if up == "MODEA2B":
            self._apply_rx_mode_to_state(ft)
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge MODEA2B (State) — {ctx}")
            return
        if up == "TUNE":
            ft.start_antenna_tuner()
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge TUNE — {ctx}")
            return
        if up.startswith("CATSTRING "):
            raw = item.command.split(None, 1)[1]
            cat = self._get_cat()
            resp = cat.send_command(raw if raw.endswith(";") else raw + ";")
            self._state.update(cat_string_response=str(resp or ""))
            self._state.mark_success()
            if ctx:
                self._log_write("INFO", f"Rig-Bridge CATSTRING {raw!r} — {ctx}")
            return
        if up.startswith("MODVOL "):
            delta = int(item.command.split(None, 1)[1])
            pct = max(0, min(100, int(self._state.volume) + delta))
            ft.write_af_gain(flrig_percent_to_cat_0_255(pct))
            self._state.update(volume=pct)
            self._state.mark_success()
            return
        if up.startswith("MODPWR "):
            delta = int(item.command.split(None, 1)[1])
            watts = max(0, int(self._state.power_pc) + delta)
            ft.set_pc_power_watts(watts, tx_lock=True)
            self._state.update(power_pc=watts)
            self._state.mark_success()
            return
        if up.startswith("MODRFG "):
            delta = int(item.command.split(None, 1)[1])
            pct = max(0, min(100, int(self._state.rfgain) + delta))
            ft.write_rf_gain(flrig_percent_to_cat_0_255(pct))
            self._state.update(rfgain=pct)
            self._state.mark_success()
            return
        if up.startswith("MODBW "):
            delta = int(item.command.split(None, 1)[1])
            rx_mode = ft.read_rx_mode()
            cur = sh_find_p2_for_hz(rx_mode, int(self._state.bandwidth_hz))
            p2 = max(0, min(21, cur + delta))
            ft.write_tx_bandwidth_sh(p2)
            self._state.update(bandwidth_hz=sh_hz_for_mode_p2(rx_mode, p2))
            self._state.mark_success()
            return
        raise RigConnectionError(f"Unbekannter Bridge-Befehl: {item.command!r}")
