"""Einstellungen für die FLRig-Rig-Bridge (TCP-Freigabe der CAT-Leitung)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FlrigBridgeSettings:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 12345
    autostart: bool = True
    log_tcp_traffic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": int(self.port),
            "autostart": self.autostart,
            "log_tcp_traffic": self.log_tcp_traffic,
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "FlrigBridgeSettings":
        r = raw or {}
        try:
            port = int(r.get("port", 12345))
        except (TypeError, ValueError):
            port = 12345
        return cls(
            enabled=bool(r.get("enabled", True)),
            host=str(r.get("host", "127.0.0.1") or "127.0.0.1"),
            port=max(1, min(65535, port)),
            autostart=bool(r.get("autostart", True)),
            log_tcp_traffic=bool(r.get("log_tcp_traffic", True)),
        )


@dataclass
class RigBridgeSettings:
    """Globale Rig-Bridge (teilt die CAT-Leitung der App — nur FLRig)."""

    enabled: bool = True
    flrig: FlrigBridgeSettings = field(default_factory=FlrigBridgeSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "flrig": self.flrig.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "RigBridgeSettings":
        r = raw or {}
        # Legacy-Schlüssel „hamlib“ aus älteren settings.json werden ignoriert.
        return cls(
            enabled=bool(r.get("enabled", True)),
            flrig=FlrigBridgeSettings.from_dict(r.get("flrig")),
        )
