"""Einstellungen für den CAT-Audio-Player."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, cast

PlaybackMode = Literal["single", "playlist"]
DataMode = Literal["DATA-USB", "DATA-LSB", "DATA-FM"]

AUDIO_EXTENSIONS = {".mp3", ".wav"}

DEFAULT_VOLUME_PERCENT = 100
DEFAULT_DATA_MODE: DataMode = "DATA-FM"
ALLOWED_DATA_MODES: tuple[DataMode, ...] = ("DATA-USB", "DATA-LSB", "DATA-FM")
DEFAULT_CONTEST_LISTEN_MS = 5000
MIN_TIMING_MS = 0
MAX_TIMING_MS = 60_000
#: Hörpause im Kontest-Loop darf länger sein (z. B. 30 s).
MAX_CONTEST_LISTEN_MS = 600_000

#: Virtuelle Pausen-Zeilen in ``playlist_order`` (Millisekunden nach dem Präfix).
PAUSE_TOKEN_PREFIX = "__pause_ms__:"

#: Playlist-Pause: 1 s … 10 min (wie früher globales Gap-Maximum sinnvoll)
MIN_PLAYLIST_PAUSE_MS = 1_000
MAX_PLAYLIST_PAUSE_MS = 600_000


def is_pause_token(name: str) -> bool:
    return bool(name) and name.startswith(PAUSE_TOKEN_PREFIX)


def parse_pause_ms_from_token(name: str) -> Optional[int]:
    """Liefert Millisekunden oder ``None`` wenn Token ungültig."""
    if not is_pause_token(name):
        return None
    tail = name[len(PAUSE_TOKEN_PREFIX) :].strip()
    try:
        ms = int(tail)
    except ValueError:
        return None
    if ms < MIN_PLAYLIST_PAUSE_MS or ms > MAX_PLAYLIST_PAUSE_MS:
        return None
    return ms


def encode_pause_token_ms(ms: int) -> str:
    """Persistiertes Token für eine Pausen-Zeile."""
    v = int(ms)
    v = max(MIN_PLAYLIST_PAUSE_MS, min(MAX_PLAYLIST_PAUSE_MS, v))
    return f"{PAUSE_TOKEN_PREFIX}{v}"


def encode_pause_token_seconds(seconds: int) -> str:
    """Pausen-Dauer aus ganzen Sekunden (Eingabe)."""
    s = max(1, min(600, int(seconds)))
    return encode_pause_token_ms(s * 1000)


def pause_label_de(name: str) -> str:
    """Listen-Beschriftung für eine gespeicherte Zeile (Dateiname oder Pause)."""
    ms = parse_pause_ms_from_token(name)
    if ms is not None:
        s = ms // 1000
        if s == 1:
            return "Pause 1 Sekunde"
        return f"Pause {s} Sekunden"
    return name


@dataclass
class AudioPlayerSettings:
    folder_path: str = ""
    playback_mode: PlaybackMode = "single"
    output_device_id: str = ""
    #: Separates PC-Ausgabegerät für lokale Vorhöre (Play PC, kein TX).
    pc_output_device_id: str = ""
    volume_percent: int = DEFAULT_VOLUME_PERCENT
    #: Lautstärke der lokalen PC-Vorhöre (Play PC).
    pc_output_volume_percent: int = DEFAULT_VOLUME_PERCENT
    #: Mithören: CAT-Sendesignal zusätzlich auf dem PC-Wiedergabegerät.
    tx_monitor_to_pc_enabled: bool = True
    #: Kurzton über die PC-Ausgabe in den letzten Sekunden der letzten Playlist-Datei.
    #: Läuft niemals über den CAT-Sendepfad (nur Gerät laut „PC-Ausgabe“-Combobox).
    warn_transmission_end_enabled: bool = True
    playlist_order: list[str] = field(default_factory=list)
    window_geometry: str = ""
    data_mode: DataMode = DEFAULT_DATA_MODE
    #: Kontest-Loop: markierte Datei wiederholen mit Hörpause dazwischen.
    contest_mode: bool = False
    contest_listen_pause_ms: int = DEFAULT_CONTEST_LISTEN_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_path": self.folder_path,
            "playback_mode": self.playback_mode,
            "output_device_id": self.output_device_id,
            "pc_output_device_id": self.pc_output_device_id,
            "volume_percent": int(self.volume_percent),
            "pc_output_volume_percent": int(self.pc_output_volume_percent),
            "tx_monitor_to_pc_enabled": bool(self.tx_monitor_to_pc_enabled),
            "warn_transmission_end_enabled": bool(self.warn_transmission_end_enabled),
            "playlist_order": list(self.playlist_order),
            "window_geometry": self.window_geometry,
            "data_mode": self.data_mode,
            "contest_mode": bool(self.contest_mode),
            "contest_listen_pause_ms": int(self.contest_listen_pause_ms),
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "AudioPlayerSettings":
        r = raw or {}
        mode = str(r.get("playback_mode", "single") or "single")
        if mode not in ("single", "playlist"):
            mode = "single"
        order_raw = r.get("playlist_order")
        order: list[str] = []
        if isinstance(order_raw, list):
            order = [str(x) for x in order_raw if str(x).strip()]
        data_mode = str(r.get("data_mode", DEFAULT_DATA_MODE) or DEFAULT_DATA_MODE)
        if data_mode not in ALLOWED_DATA_MODES:
            data_mode = DEFAULT_DATA_MODE
        return cls(
            folder_path=str(r.get("folder_path", "") or ""),
            playback_mode=mode,  # type: ignore[arg-type]
            output_device_id=str(r.get("output_device_id", "") or ""),
            pc_output_device_id=str(r.get("pc_output_device_id", "") or ""),
            volume_percent=_clamp_volume(r.get("volume_percent")),
            pc_output_volume_percent=_clamp_volume(
                r.get("pc_output_volume_percent", DEFAULT_VOLUME_PERCENT)
            ),
            tx_monitor_to_pc_enabled=bool(r.get("tx_monitor_to_pc_enabled", True)),
            warn_transmission_end_enabled=bool(
                r.get("warn_transmission_end_enabled", True)
            ),
            playlist_order=order,
            window_geometry=str(r.get("window_geometry", "") or ""),
            data_mode=data_mode,  # type: ignore[arg-type]
            contest_mode=bool(r.get("contest_mode", False)),
            contest_listen_pause_ms=_clamp_contest_listen_ms(
                r.get("contest_listen_pause_ms")
            ),
        )


def _clamp_volume(value: object) -> int:
    try:
        v = int(cast(Any, value))
    except (TypeError, ValueError):
        v = DEFAULT_VOLUME_PERCENT
    return max(0, min(100, v))


def _clamp_contest_listen_ms(value: object) -> int:
    try:
        ms = int(cast(Any, value))
    except (TypeError, ValueError):
        ms = DEFAULT_CONTEST_LISTEN_MS
    return max(MIN_TIMING_MS, min(MAX_CONTEST_LISTEN_MS, ms))


def scan_audio_files(folder: Path) -> list[str]:
    """Dateinamen (ohne Pfad) von MP3/WAV direkt im Ordner."""
    if not folder.is_dir():
        return []
    names: list[str] = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            names.append(p.name)
    return names


def merge_playlist_order(saved: list[str], discovered: list[str]) -> list[str]:
    """Bekannte Reihenfolge behalten, neue ans Ende; fehlende Dateien entfernen.

    Pausen-Tokens (``__pause_ms__:…``) bleiben erhalten, solange sie gültig sind.
    """
    discovered_set = set(discovered)
    out: list[str] = []
    for n in saved:
        if is_pause_token(n):
            if parse_pause_ms_from_token(n) is not None:
                out.append(n)
        elif n in discovered_set:
            out.append(n)
    for name in discovered:
        if name not in out:
            out.append(name)
    return out
