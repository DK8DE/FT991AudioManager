"""Rig-Bridge-Manager für FT-991 Audio Manager (gemeinsame CAT-Verbindung)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from cat.serial_cat import SerialCAT

from .ft991_backend import Ft991SharedCatBackend
from .protocol_flrig import FlrigBridgeServer
from .protocol_hamlib_net_rigctl import HamlibNetRigctlServer
from .state import RadioStateCache


_DEFAULT_FLRIG: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 12345,
    "autostart": False,
    "log_tcp_traffic": True,
}
_DEFAULT_HAMLIB: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "listeners": [{"host": "127.0.0.1", "port": 4532, "name": ""}],
    "autostart": False,
    "debug_traffic": False,
    "log_tcp_traffic": False,
}


def normalize_rig_bridge_config(raw: Optional[dict]) -> dict[str, Any]:
    src = dict(raw or {})
    flrig = dict(_DEFAULT_FLRIG)
    flrig.update(src.get("flrig") or {})
    hamlib = dict(_DEFAULT_HAMLIB)
    hamlib.update(src.get("hamlib") or {})
    if not isinstance(hamlib.get("listeners"), list):
        hamlib["listeners"] = [{"host": "127.0.0.1", "port": 4532, "name": ""}]
    return {
        "enabled": bool(src.get("enabled", False)),
        "flrig": flrig,
        "hamlib": hamlib,
    }


class RigBridgeManager:
    """FLRig- und Hamlib-rigctl-Server über die App-CAT-Leitung."""

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
            refresh_frequency_before_read=self.request_cat_refresh_async,
        )
        self._hamlib_servers: dict[int, HamlibNetRigctlServer] = {}
        self._hamlib_client_counts: dict[int, int] = {}

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

    def _hamlib_protocol_log(self, level: str, msg: str) -> None:
        self._log_write(level, msg)

    def _state_patch(self, patch: dict[str, Any]) -> None:
        if patch:
            self._state.update(**patch)

    def _on_flrig_clients_changed(self, n: int) -> None:
        self._state.set_protocol_clients("flrig", max(0, int(n)))

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
        if self._cfg["hamlib"].get("enabled") and self._cfg["hamlib"].get("autostart"):
            self.start_protocol("hamlib")

    def _flrig_port(self) -> int:
        try:
            return max(1, min(65535, int(self._cfg["flrig"].get("port", 12345))))
        except (TypeError, ValueError):
            return 12345

    def _hamlib_listener_entries(self) -> list[tuple[str, int, str]]:
        """(host, port, name) — nur Zeilen mit gültigem Port."""
        default_host = str(
            self._cfg["hamlib"].get("host", "127.0.0.1") or "127.0.0.1"
        )
        out: list[tuple[str, int, str]] = []
        for it in self._cfg["hamlib"].get("listeners") or []:
            if not isinstance(it, dict):
                continue
            if it.get("port") in (None, ""):
                continue
            try:
                port = max(1, min(65535, int(it["port"])))
            except (TypeError, ValueError):
                continue
            host = str(it.get("host", default_host) or default_host).strip()
            if not host:
                host = default_host
            name = str(it.get("name", "") or "")
            out.append((host, port, name))
        return out

    def _hamlib_ports(self) -> list[int]:
        return [p for _h, p, _n in self._hamlib_listener_entries()]

    def start_protocol(self, name: str) -> tuple[bool, str]:
        if not self._backend.is_serial_connected():
            return False, "CAT nicht verbunden — zuerst mit dem Funkgerät verbinden."
        try:
            if name == "flrig":
                fp = self._flrig_port()
                if fp in self._hamlib_ports():
                    return False, "FLRig-Port kollidiert mit einem Hamlib-Port."
                host = str(self._cfg["flrig"].get("host", "127.0.0.1")).strip() or "127.0.0.1"
                self._flrig.start(host, fp)
            elif name == "hamlib":
                fp = self._flrig_port()
                entries = self._hamlib_listener_entries()
                if not entries:
                    return False, "Hamlib: mindestens einen gültigen Port eintragen."
                ports = [p for _h, p, _n in entries]
                if fp in ports:
                    return False, "Hamlib-Port kollidiert mit FLRig."
                if len(ports) != len(set(ports)):
                    return False, "Hamlib: jeder Port darf nur einmal vorkommen."
                self._stop_hamlib_servers()
                for host, port, label in entries:
                    srv = HamlibNetRigctlServer(
                        get_state=self._state.snapshot,
                        enqueue_write=self._enqueue_radio_write,
                        on_clients_changed=lambda n, pp=port: self._on_hamlib_clients(pp, n),
                        log_write=self._hamlib_protocol_log,
                        on_state_patch=self._state_patch,
                        debug_traffic=bool(self._cfg["hamlib"].get("debug_traffic", False)),
                        log_serial_traffic=False,
                        log_tcp_traffic=bool(
                            self._cfg["hamlib"].get("log_tcp_traffic", False)
                        ),
                        log_label=label or f"{host}:{port}",
                        refresh_frequency_for_read=self.request_cat_refresh_async,
                    )
                    srv.start(host, port)
                    self._hamlib_servers[port] = srv
            else:
                return False, f"Unbekanntes Protokoll: {name}"
            self._state.set_protocol_active(name, True)
            return True, f"{name} gestartet"
        except Exception as exc:
            self._state.set_protocol_active(name, False)
            return False, str(exc)

    def stop_protocol(self, name: str) -> None:
        if name == "flrig":
            self._flrig.stop()
        elif name == "hamlib":
            self._stop_hamlib_servers()
        self._state.set_protocol_active(name, False)

    def _stop_hamlib_servers(self) -> None:
        for srv in list(self._hamlib_servers.values()):
            srv.stop()
        self._hamlib_servers.clear()
        self._hamlib_client_counts.clear()
        self._state.set_protocol_clients("hamlib", 0)

    def _on_hamlib_clients(self, port: int, n: int) -> None:
        self._hamlib_client_counts[port] = int(n)
        total = sum(self._hamlib_client_counts.values())
        self._state.set_protocol_clients("hamlib", total)

    def stop_all_protocols(self) -> None:
        self.stop_protocol("flrig")
        self.stop_protocol("hamlib")

    def protocol_status(self) -> dict[str, Any]:
        snap = self._state.snapshot()
        bind_parts: list[str] = []
        for host, port, name in self._hamlib_listener_entries():
            if port not in self._hamlib_servers:
                continue
            n = int(self._hamlib_client_counts.get(port, 0))
            tag = name.strip() if name else f"{host}:{port}"
            bind_parts.append(f"{tag} ({n} Client(s))")
        return {
            "flrig_active": bool(snap["protocol_active"].get("flrig")),
            "flrig_clients": int(snap["protocol_clients"].get("flrig", 0)),
            "hamlib_active": bool(snap["protocol_active"].get("hamlib")),
            "hamlib_clients": int(snap["protocol_clients"].get("hamlib", 0)),
            "hamlib_bind_status": bind_parts,
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
