"""Einstellungen für FLRig- und Hamlib-rigctl-Freigabe (Rig-Bridge)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FlrigBridgeSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 12345
    autostart: bool = False
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
            enabled=bool(r.get("enabled", False)),
            host=str(r.get("host", "127.0.0.1") or "127.0.0.1"),
            port=max(1, min(65535, port)),
            autostart=bool(r.get("autostart", False)),
            log_tcp_traffic=bool(r.get("log_tcp_traffic", True)),
        )


@dataclass
class HamlibListenerSettings:
    """Ein Hamlib-rigctl-Listener (Host/IP + Port + optionaler Name)."""

    host: str = "127.0.0.1"
    port: int = 4532
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": int(self.port),
            "name": self.name,
        }

    @classmethod
    def from_dict(
        cls,
        raw: Optional[dict],
        *,
        default_host: str = "127.0.0.1",
    ) -> "HamlibListenerSettings":
        r = raw or {}
        try:
            port = int(r.get("port", 4532))
        except (TypeError, ValueError):
            port = 4532
        host = str(r.get("host", default_host) or default_host).strip() or default_host
        return cls(
            host=host,
            port=max(1, min(65535, port)),
            name=str(r.get("name", "") or ""),
        )


@dataclass
class HamlibBridgeSettings:
    enabled: bool = False
    autostart: bool = False
    debug_traffic: bool = False
    log_tcp_traffic: bool = False
    listeners: list[HamlibListenerSettings] = field(
        default_factory=lambda: [HamlibListenerSettings()]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "listeners": [li.to_dict() for li in self.listeners],
            "autostart": self.autostart,
            "debug_traffic": self.debug_traffic,
            "log_tcp_traffic": self.log_tcp_traffic,
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "HamlibBridgeSettings":
        r = raw or {}
        default_host = str(r.get("host", "127.0.0.1") or "127.0.0.1")
        listeners_raw = r.get("listeners")
        listeners: list[HamlibListenerSettings] = []
        if isinstance(listeners_raw, list):
            for it in listeners_raw:
                if isinstance(it, dict):
                    listeners.append(
                        HamlibListenerSettings.from_dict(
                            it, default_host=default_host
                        )
                    )
        elif listeners_raw is None and ("port" in r or "host" in r):
            try:
                legacy_port = int(r.get("port", 4532))
            except (TypeError, ValueError):
                legacy_port = 4532
            listeners.append(
                HamlibListenerSettings(
                    host=default_host,
                    port=max(1, min(65535, legacy_port)),
                )
            )
        elif listeners_raw is None and not listeners:
            listeners.append(HamlibListenerSettings())
        return cls(
            enabled=bool(r.get("enabled", False)),
            autostart=bool(r.get("autostart", False)),
            debug_traffic=bool(r.get("debug_traffic", False)),
            log_tcp_traffic=bool(r.get("log_tcp_traffic", False)),
            listeners=listeners,
        )


@dataclass
class RigBridgeSettings:
    """Globale Rig-Bridge (teilt die CAT-Leitung der App)."""

    enabled: bool = False
    flrig: FlrigBridgeSettings = field(default_factory=FlrigBridgeSettings)
    hamlib: HamlibBridgeSettings = field(default_factory=HamlibBridgeSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "flrig": self.flrig.to_dict(),
            "hamlib": self.hamlib.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "RigBridgeSettings":
        r = raw or {}
        return cls(
            enabled=bool(r.get("enabled", False)),
            flrig=FlrigBridgeSettings.from_dict(r.get("flrig")),
            hamlib=HamlibBridgeSettings.from_dict(r.get("hamlib")),
        )
