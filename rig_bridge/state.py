"""Zentraler, thread-sicherer State-Cache für Rig-Bridge."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .utils import now_ts


@dataclass
class RadioStateCache:
    """Zentrale Zustandsdaten des Funkgeräts."""

    connected: bool = False
    selected_rig: str = ""
    com_port: str = ""
    #: Profil-ID (aus ``rig_bridge.rigs``), dessen Funkgeraet aktuell verbunden ist.
    active_rig_id: str = ""
    #: Anzeigename des aktiven Profils (fuer UI + CAT-Simulation).
    active_rig_name: str = ""
    #: 0 = noch keine Frequenz aus Software/CAT bekannt (kein erzwungenes 2-m-Band).
    frequency_hz: int = 0
    frequency_hz_b: int = 0
    mode: str = "USB"
    ptt: bool = False
    vfo: str = "A"
    split: bool = False
    #: FLRig-Skalen (0..100) bzw. Anzeige-Rohwerte — aus CAT gelesen.
    volume: int = 0
    rfgain: int = 0
    micgain: int = 0
    power_pc: int = 0
    agc: int = 0
    smeter_raw: int = 0
    swr_raw: int = 0
    po_raw: int = 0
    notch_hz: int = 0
    bandwidth_hz: int = 3000
    sideband: str = "U"
    cw_wpm: int = 20
    cat_string_response: str = ""
    last_error: str = ""
    last_success_ts: float = 0.0
    protocol_active: dict[str, bool] = field(
        default_factory=lambda: {"flrig": False}
    )
    protocol_clients: dict[str, int] = field(
        default_factory=lambda: {"flrig": 0}
    )

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        """Thread-sicheren Snapshot liefern."""
        with self._lock:
            return {
                "connected": bool(self.connected),
                "selected_rig": str(self.selected_rig),
                "com_port": str(self.com_port),
                "active_rig_id": str(self.active_rig_id),
                "active_rig_name": str(self.active_rig_name),
                "frequency_hz": int(self.frequency_hz),
                "frequency_hz_b": int(self.frequency_hz_b),
                "mode": str(self.mode),
                "ptt": bool(self.ptt),
                "vfo": str(self.vfo),
                "split": bool(self.split),
                "volume": int(self.volume),
                "rfgain": int(self.rfgain),
                "micgain": int(self.micgain),
                "power_pc": int(self.power_pc),
                "agc": int(self.agc),
                "smeter_raw": int(self.smeter_raw),
                "swr_raw": int(self.swr_raw),
                "po_raw": int(self.po_raw),
                "notch_hz": int(self.notch_hz),
                "bandwidth_hz": int(self.bandwidth_hz),
                "sideband": str(self.sideband),
                "cw_wpm": int(self.cw_wpm),
                "cat_string_response": str(self.cat_string_response),
                "last_error": str(self.last_error),
                "last_success_ts": float(self.last_success_ts),
                "protocol_active": dict(self.protocol_active),
                "protocol_clients": dict(self.protocol_clients),
            }

    def update(self, **kwargs: Any) -> None:
        """Mehrere Felder atomar aktualisieren."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def set_error(self, msg: str) -> None:
        """Letzten Fehler setzen."""
        with self._lock:
            self.last_error = str(msg or "")

    def mark_success(self) -> None:
        """Kommunikationserfolg markieren."""
        with self._lock:
            self.last_success_ts = now_ts()
            self.last_error = ""

    def set_protocol_active(self, protocol: str, active: bool) -> None:
        """Aktivstatus eines Protokolls setzen."""
        with self._lock:
            self.protocol_active[str(protocol)] = bool(active)

    def set_protocol_clients(self, protocol: str, clients: int) -> None:
        """Client-Anzahl eines Protokolls setzen."""
        with self._lock:
            self.protocol_clients[str(protocol)] = max(0, int(clients))
