"""Einstellungen für den CAT-Audio-Player."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

PlaybackMode = Literal["single", "playlist"]

AUDIO_EXTENSIONS = {".mp3", ".wav"}

DEFAULT_PRE_ROLL_MS = 1000
DEFAULT_GAP_BETWEEN_FILES_MS = 500
DEFAULT_VOLUME_PERCENT = 100
MIN_TIMING_MS = 0
MAX_TIMING_MS = 60_000


@dataclass
class AudioPlayerSettings:
    folder_path: str = ""
    pre_roll_ms: int = DEFAULT_PRE_ROLL_MS
    gap_between_files_ms: int = DEFAULT_GAP_BETWEEN_FILES_MS
    playback_mode: PlaybackMode = "single"
    output_device_id: str = ""
    volume_percent: int = DEFAULT_VOLUME_PERCENT
    playlist_order: list[str] = field(default_factory=list)
    window_geometry: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_path": self.folder_path,
            "pre_roll_ms": int(self.pre_roll_ms),
            "gap_between_files_ms": int(self.gap_between_files_ms),
            "playback_mode": self.playback_mode,
            "output_device_id": self.output_device_id,
            "volume_percent": int(self.volume_percent),
            "playlist_order": list(self.playlist_order),
            "window_geometry": self.window_geometry,
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
        return cls(
            folder_path=str(r.get("folder_path", "") or ""),
            pre_roll_ms=_clamp_ms(r.get("pre_roll_ms"), DEFAULT_PRE_ROLL_MS),
            gap_between_files_ms=_clamp_ms(
                r.get("gap_between_files_ms"), DEFAULT_GAP_BETWEEN_FILES_MS
            ),
            playback_mode=mode,  # type: ignore[arg-type]
            output_device_id=str(r.get("output_device_id", "") or ""),
            volume_percent=_clamp_volume(r.get("volume_percent")),
            playlist_order=order,
            window_geometry=str(r.get("window_geometry", "") or ""),
        )


def _clamp_volume(value: object) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = DEFAULT_VOLUME_PERCENT
    return max(0, min(100, v))


def _clamp_ms(value: object, fallback: int) -> int:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        ms = fallback
    return max(MIN_TIMING_MS, min(MAX_TIMING_MS, ms))


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
    """Bekannte Reihenfolge behalten, neue ans Ende, Fehlende entfernen."""
    discovered_set = set(discovered)
    out = [n for n in saved if n in discovered_set]
    for name in discovered:
        if name not in out:
            out.append(name)
    return out
