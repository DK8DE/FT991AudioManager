"""Persistente App-Einstellungen (``settings.json`` im User-Datenordner).

Format (Auszug, wie in der Spezifikation)::

    {
      "cat": {
        "port": "COM5",
        "baudrate": 38400,
        "timeout_ms": 1000
      },
      "ui": {
        "last_profile": "Default",
        "auto_apply_profile": false,
        "show_advanced": false
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from ._app_paths import app_data_dir
from .audio_player_settings import AudioPlayerSettings
from .rig_bridge_settings import RigBridgeSettings


DEFAULT_BAUDRATE = 38400
DEFAULT_TIMEOUT_MS = 1000

DEFAULT_POLL_TX_MS = 100
DEFAULT_POLL_RX_MS = 100
POLL_MIN_MS = 100
POLL_MAX_MS = 5000


@dataclass
class CatSettings:
    port: Optional[str] = None
    baudrate: int = DEFAULT_BAUDRATE
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    #: Beim Programmstart automatisch verbinden — und nach einem
    #: Verbindungsverlust (Kabel gezogen, Gerät aus) im Hintergrund
    #: solange neu probieren, bis der Port wieder erreichbar ist.
    auto_connect: bool = False


@dataclass
class PollingSettings:
    """Polling-Intervalle für die Live-Meter.

    Polling läuft im Hintergrund automatisch, sobald CAT verbunden ist.
    Im RX-Modus reicht ein langes Intervall (nur ``TX;`` wird abgefragt),
    bei TX wird auf das kürzere TX-Intervall umgeschaltet, damit die
    Meter flüssig laufen.
    """

    tx_interval_ms: int = DEFAULT_POLL_TX_MS
    rx_interval_ms: int = DEFAULT_POLL_RX_MS


@dataclass
class UiSettings:
    last_profile: Optional[str] = None
    auto_apply_profile: bool = False
    show_advanced: bool = False
    #: Dark Mode ist der Standard für eine frische Installation — sowohl
    #: hier (kein Settings-File) als auch im ``load()``-Fallback unten
    #: (Key fehlt). Wer das nicht mag, schaltet im View-Menü um; der
    #: nächste ``save()`` schreibt dann ``false`` persistent in die JSON.
    force_dark_mode: bool = True
    #: Ob das CAT-Log-Fenster beim Start angezeigt werden soll.
    show_cat_log: bool = False
    #: Optionale gespeicherte Geometrie des Log-Fensters (Base64-Encoded
    #: ``QByteArray`` von ``QWidget.saveGeometry()``). Leer = Default.
    log_window_geometry: str = ""
    #: Wenn ``True``, wird der "Erweiterte Einstellungen"-Bereich im
    #: Profil-Tab bei SSB-Modus komplett ausgeblendet. In anderen Modi
    #: (AM/FM/DATA/RTTY) bleibt er sichtbar. Default ist ``True``, weil
    #: SSB-„Erweitert" für die meisten Anwender uninteressante Werte
    #: enthält (TX-Bandfilter sitzt schon prominent in den Grundwerten).
    hide_extended_in_ssb: bool = True


@dataclass
class AppSettings:
    cat: CatSettings = field(default_factory=CatSettings)
    polling: PollingSettings = field(default_factory=PollingSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    rig_bridge: RigBridgeSettings = field(default_factory=RigBridgeSettings)
    audio_player: AudioPlayerSettings = field(default_factory=AudioPlayerSettings)

    # ------------------------------------------------------------------
    # Laden / Speichern
    # ------------------------------------------------------------------

    @classmethod
    def default_path(cls) -> Path:
        """Pfad zur ``settings.json``.

        - Entwicklung: ``<Projekt-Root>/data/settings.json``
        - Installierte EXE: ``%APPDATA%\\FT991AudioManager\\settings.json`` (bzw. XDG/macOS-Äquivalent)
        """
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppSettings":
        path = path or cls.default_path()
        if not path.exists():
            return cls()
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls()

        cat_raw = data.get("cat", {}) or {}
        polling_raw = data.get("polling", {}) or {}
        ui_raw = data.get("ui", {}) or {}
        rig_bridge_raw = data.get("rig_bridge", {}) or {}
        audio_player_raw = data.get("audio_player", {}) or {}

        cat = CatSettings(
            port=cat_raw.get("port"),
            baudrate=int(cat_raw.get("baudrate", DEFAULT_BAUDRATE) or DEFAULT_BAUDRATE),
            timeout_ms=int(cat_raw.get("timeout_ms", DEFAULT_TIMEOUT_MS) or DEFAULT_TIMEOUT_MS),
            auto_connect=bool(cat_raw.get("auto_connect", False)),
        )
        polling = PollingSettings(
            tx_interval_ms=_clamp_poll(
                polling_raw.get("tx_interval_ms", DEFAULT_POLL_TX_MS),
                DEFAULT_POLL_TX_MS,
            ),
            rx_interval_ms=_clamp_poll(
                polling_raw.get("rx_interval_ms", DEFAULT_POLL_RX_MS),
                DEFAULT_POLL_RX_MS,
            ),
        )
        # RX-Intervall darf nicht kürzer als TX-Intervall sein.
        if polling.rx_interval_ms < polling.tx_interval_ms:
            polling.rx_interval_ms = polling.tx_interval_ms
        ui = UiSettings(
            last_profile=ui_raw.get("last_profile"),
            auto_apply_profile=bool(ui_raw.get("auto_apply_profile", False)),
            show_advanced=bool(ui_raw.get("show_advanced", False)),
            force_dark_mode=bool(ui_raw.get("force_dark_mode", True)),
            show_cat_log=bool(ui_raw.get("show_cat_log", False)),
            log_window_geometry=str(ui_raw.get("log_window_geometry") or ""),
            hide_extended_in_ssb=bool(ui_raw.get("hide_extended_in_ssb", True)),
        )
        rig_bridge = RigBridgeSettings.from_dict(rig_bridge_raw)
        audio_player = AudioPlayerSettings.from_dict(audio_player_raw)
        return cls(
            cat=cat,
            polling=polling,
            ui=ui,
            rig_bridge=rig_bridge,
            audio_player=audio_player,
        )

    def save(self, path: Optional[Path] = None) -> None:
        path = path or self.default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cat": asdict(self.cat),
            "polling": asdict(self.polling),
            "ui": asdict(self.ui),
            "rig_bridge": self.rig_bridge.to_dict(),
            "audio_player": self.audio_player.to_dict(),
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")


def _clamp_poll(value: object, fallback: int) -> int:
    """Robust gegen Müll im JSON: erzwingt int und klemmt auf den erlaubten Bereich."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        ms = fallback
    return max(POLL_MIN_MS, min(POLL_MAX_MS, ms))
