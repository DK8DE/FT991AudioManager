"""Rig-Bridge-Manager für FT-991 Audio Manager (gemeinsame CAT-Verbindung)."""

from __future__ import annotations

import threading
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
    ) -> None:
        self._get_cat = get_cat
        self._log_write = log_write
        self._lock = threading.RLock()
        self._pending_flrig_io = False
        self._cfg = normalize_rig_bridge_config(cfg_dict)
        self._state = RadioStateCache()
        self._backend = Ft991SharedCatBackend(
            self._state, get_cat=get_cat, log_write=self._protocol_log
        )
        self._flrig = FlrigBridgeServer(
            get_state=self._state.snapshot,
            enqueue_write=self._enqueue_radio_write,
            on_clients_changed=self._on_flrig_clients_changed,
            log_write=self._flrig_protocol_log,
            log_client_traffic=bool(self._cfg["flrig"].get("log_tcp_traffic", True)),
            on_state_patch=self._state_patch,
            on_tcp_activity=self._notify_flrig_tcp_activity,
            refresh_frequency_before_read=self.request_cat_refresh_async,
        )

    def update_config(self, cfg_dict: Optional[dict]) -> None:
        with self._lock:
            self._cfg = normalize_rig_bridge_config(cfg_dict)
            self._flrig.set_log_client_traffic(
                bool(self._cfg["flrig"].get("log_tcp_traffic", True))
            )

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
        with self._lock:
            self._pending_flrig_io = True

    def take_bridge_activity_flags(self) -> bool:
        """Verbraucht TCP-Aktivitätspuls FLRig für die Status-LED."""
        with self._lock:
            f = self._pending_flrig_io
            self._pending_flrig_io = False
        return f

    def _enqueue_radio_write(self, command: str, log_ctx: str = "") -> None:
        self._backend.write_command(command, log_ctx=log_ctx)

    def request_cat_refresh_async(self) -> bool:
        if not self._backend.is_serial_connected():
            return False
        self._backend.write_command("READFREQ", log_ctx="Bridge READFREQ")
        return True

    def flrig_refresh_frequency_before_read(self) -> bool:
        return self.request_cat_refresh_async()

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
