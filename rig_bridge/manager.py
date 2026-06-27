"""Rig-Bridge-Manager für FT-991 Audio Manager (gemeinsame CAT-Verbindung)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from cat.serial_cat import SerialCAT

from .ft991_backend import Ft991SharedCatBackend
from .protocol_flrig import FlrigBridgeServer
from .state import RadioStateCache


_DEFAULT_FLRIG: dict[str, Any] = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 12345,
    "autostart": True,
    "log_tcp_traffic": True,
}


def normalize_rig_bridge_config(raw: Optional[dict]) -> dict[str, Any]:
    src = dict(raw or {})
    flrig = dict(_DEFAULT_FLRIG)
    flrig.update(src.get("flrig") or {})
    return {
        "enabled": bool(src.get("enabled", True)),
        "flrig": flrig,
    }


class RigBridgeManager:
    """FLRig-TCP-Server über die App-CAT-Leitung."""

    def __init__(
        self,
        cfg_dict: Optional[dict],
        *,
        get_cat: Callable[[], SerialCAT],
        log_write: Callable[[str, str], None],
        on_frequency_written: Optional[Callable[..., None]] = None,
    ) -> None:
        self._get_cat = get_cat
        self._log_write = log_write
        self._lock = threading.RLock()
        self._pending_flrig_io = False
        #: Poller kurz auslassen, damit Bridge-Worker SETFREQ/PTT/MODE senden kann.
        self._flrig_cat_yield_until_mono = 0.0
        self._flrig_cat_yield_ms = 200
        self._cfg = normalize_rig_bridge_config(cfg_dict)
        self._state = RadioStateCache()
        self._backend = Ft991SharedCatBackend(
            self._state,
            get_cat=get_cat,
            log_write=self._protocol_log,
            on_frequency_written=on_frequency_written,
        )
        self._flrig = FlrigBridgeServer(
            get_state=self._state.snapshot,
            enqueue_write=self._enqueue_radio_write,
            on_clients_changed=self._on_flrig_clients_changed,
            log_write=self._flrig_protocol_log,
            log_client_traffic=bool(self._cfg["flrig"].get("log_tcp_traffic", True)),
            on_state_patch=self._state_patch,
            on_tcp_activity=self._notify_flrig_tcp_activity,
            refresh_before_read=self.refresh_flrig_before_read,
        )

    def set_on_frequency_written(
        self, callback: Optional[Callable[[int], None]]
    ) -> None:
        self._backend._on_frequency_written = callback

    def update_config(self, cfg_dict: Optional[dict]) -> None:
        restart_flrig = False
        with self._lock:
            old = self._cfg
            self._cfg = normalize_rig_bridge_config(cfg_dict)
            self._flrig.set_log_client_traffic(
                bool(self._cfg["flrig"].get("log_tcp_traffic", True))
            )
            snap = self._state.snapshot()
            if snap["protocol_active"].get("flrig"):
                old_raw = old.get("flrig")
                old_fl: dict[str, Any] = (
                    old_raw if isinstance(old_raw, dict) else {}
                )
                new_raw = self._cfg.get("flrig")
                new_fl: dict[str, Any] = (
                    new_raw if isinstance(new_raw, dict) else {}
                )
                if (
                    str(old_fl.get("host", "127.0.0.1")).strip()
                    != str(new_fl.get("host", "127.0.0.1")).strip()
                    or int(old_fl.get("port", 12345)) != int(new_fl.get("port", 12345))
                ):
                    restart_flrig = True
        if restart_flrig:
            self.stop_protocol("flrig")
            self.start_protocol("flrig")

    def bridge_pending_writes(self) -> bool:
        """True, wenn noch CAT-Schreibbefehle aus FLRig in der Warteschlange sind."""
        return self._backend.pending_write_count() > 0

    def flrig_poller_should_yield(self) -> bool:
        """Meter-Poller soll CAT freigeben (FLRig-Traffic oder offene Bridge-Befehle)."""
        if self.flrig_cat_yield_active():
            return True
        return self.bridge_pending_writes()

    def _protocol_log(self, level: str, msg: str) -> None:
        self._log_write(level, msg)

    def _flrig_protocol_log(self, level: str, msg: str) -> None:
        self._log_write(level, msg)

    def _state_patch(self, patch: dict[str, Any]) -> None:
        if patch:
            self._state.update(**patch)

    def _on_flrig_clients_changed(self, n: int) -> None:
        self._state.set_protocol_clients("flrig", max(0, int(n)))

    def _notify_flrig_tcp_activity(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._pending_flrig_io = True
            self._flrig_cat_yield_until_mono = now + self._flrig_cat_yield_ms / 1000.0

    def flrig_cat_yield_active(self) -> bool:
        """True, wenn der Meter-Poller den CAT-Port kurz freigeben soll."""
        return time.monotonic() < self._flrig_cat_yield_until_mono

    def flrig_has_clients(self) -> bool:
        snap = self._state.snapshot()
        return int(snap.get("protocol_clients", {}).get("flrig", 0) or 0) > 0

    def take_bridge_activity_flags(self) -> bool:
        """Verbraucht TCP-Aktivitätspuls FLRig für die Status-LED."""
        with self._lock:
            f = self._pending_flrig_io
            self._pending_flrig_io = False
        return f

    def _enqueue_radio_write(self, command: str, log_ctx: str = "") -> None:
        now = time.monotonic()
        with self._lock:
            until = now + self._flrig_cat_yield_ms / 1000.0
            if until > self._flrig_cat_yield_until_mono:
                self._flrig_cat_yield_until_mono = until
        self._backend.write_command(command, log_ctx=log_ctx)

    def refresh_flrig_before_read(self, method: str) -> None:
        """Synchroner CAT-Lesezyklus für FLRig-Abfragen (XML-RPC-Thread)."""
        if not self._backend.is_serial_connected():
            return
        self._notify_flrig_tcp_activity()
        self._backend.sync_refresh_for_flrig(method)

    def request_cat_refresh_async(self) -> bool:
        """Nicht blockierend — READFREQ in die Bridge-Warteschlange (FLRig-TCP-Thread)."""
        if not self._backend.is_serial_connected():
            return False
        self._notify_flrig_tcp_activity()
        self._backend.write_command("READFREQ", log_ctx="Bridge READFREQ")
        return True

    def flrig_refresh_frequency_before_read(self) -> bool:
        self.refresh_flrig_before_read("main.get_frequency")
        return True

    def on_app_connected(self) -> None:
        self._backend.start()
        self._state.update(connected=True, com_port="")
        if self._cfg.get("enabled"):
            self.start_enabled_protocols()

    def on_app_disconnected(self) -> None:
        self.stop_all_protocols()
        self._backend.stop()
        self._state.update(connected=False)

    def start_enabled_protocols(self) -> None:
        if self._cfg["flrig"].get("enabled") and self._cfg["flrig"].get("autostart"):
            self.start_protocol("flrig")

    def _flrig_port(self) -> int:
        try:
            return max(1, min(65535, int(self._cfg["flrig"].get("port", 12345))))
        except (TypeError, ValueError):
            return 12345

    def start_protocol(self, name: str) -> tuple[bool, str]:
        if not self._backend.is_serial_connected():
            return False, "CAT nicht verbunden — zuerst mit dem Funkgerät verbinden."
        if name != "flrig":
            return False, f"Unbekanntes Protokoll: {name}"
        try:
            fp = self._flrig_port()
            host = str(self._cfg["flrig"].get("host", "127.0.0.1")).strip() or "127.0.0.1"
            self._flrig.start(host, fp)
            self._state.set_protocol_active("flrig", True)
            return True, "flrig gestartet"
        except Exception as exc:
            self._state.set_protocol_active("flrig", False)
            return False, str(exc)

    def stop_protocol(self, name: str) -> None:
        if name == "flrig":
            self._flrig.stop()
            self._state.set_protocol_active("flrig", False)

    def stop_all_protocols(self) -> None:
        self.stop_protocol("flrig")

    def protocol_status(self) -> dict[str, Any]:
        snap = self._state.snapshot()
        return {
            "flrig_active": bool(snap["protocol_active"].get("flrig")),
            "flrig_clients": int(snap["protocol_clients"].get("flrig", 0)),
        }

    def update_from_radio(
        self,
        *,
        frequency_hz: Optional[int] = None,
        mode: Optional[str] = None,
        ptt: Optional[bool] = None,
    ) -> None:
        patch: dict[str, Any] = {}
        if frequency_hz is not None and int(frequency_hz) > 0:
            patch["frequency_hz"] = int(frequency_hz)
        if mode is not None:
            patch["mode"] = str(mode)
        if ptt is not None:
            patch["ptt"] = bool(ptt)
        if patch:
            self._state.update(**patch)
