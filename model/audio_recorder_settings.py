"""Einstellungen für den MP3-Audio-Recorder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

#: Auswählbare MP3-Bitraten (kbps). 512 ist außerhalb des klassischen
#: MP3-Standards (Max 320), wird hier trotzdem angeboten — moderne Encoder
#: (z. B. LAME, Windows Media Foundation) clippen ggf. auf 320.
ALLOWED_BITRATES_KBPS: tuple[int, ...] = (
    64, 96, 128, 160, 192, 256, 320, 512,
)
DEFAULT_BITRATE_KBPS = 64

#: Unterordner unter Documents/, in den per Default Aufnahmen geschrieben werden.
DEFAULT_FOLDER_NAME = "FT991_Recordings"

RECORDING_EXTENSION = ".mp3"


@dataclass
class AudioRecorderSettings:
    folder_path: str = ""
    input_device_id: str = ""        # Eingangsgerät (USB CODEC etc.)
    output_device_id: str = ""       # Wiedergabe-Gerät für Replay
    window_geometry: str = ""
    mp3_bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    selected_filename: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_path": self.folder_path,
            "input_device_id": self.input_device_id,
            "output_device_id": self.output_device_id,
            "window_geometry": self.window_geometry,
            "mp3_bitrate_kbps": int(self.mp3_bitrate_kbps),
            "selected_filename": self.selected_filename,
        }

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "AudioRecorderSettings":
        r = raw or {}
        return cls(
            folder_path=str(r.get("folder_path", "") or ""),
            input_device_id=str(r.get("input_device_id", "") or ""),
            output_device_id=str(r.get("output_device_id", "") or ""),
            window_geometry=str(r.get("window_geometry", "") or ""),
            mp3_bitrate_kbps=_clamp_bitrate(r.get("mp3_bitrate_kbps")),
            selected_filename=str(r.get("selected_filename", "") or ""),
        )


def _clamp_bitrate(value: object) -> int:
    """Klemmt auf eine erlaubte MP3-Bitrate, Fallback Default."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BITRATE_KBPS
    if v in ALLOWED_BITRATES_KBPS:
        return v
    # Auf nächst-niedrigeren erlaubten Wert runden (oder Default).
    lower = [b for b in ALLOWED_BITRATES_KBPS if b <= v]
    return lower[-1] if lower else DEFAULT_BITRATE_KBPS


def build_recording_filename(now: Optional[datetime] = None) -> str:
    """``Record_YYYY_MM_DD_Stunde_HH_MM_SS.mp3`` aus dem aktuellen Zeitpunkt."""
    t = now or datetime.now()
    return t.strftime("Record_%Y_%m_%d_Stunde_%H_%M_%S") + RECORDING_EXTENSION


def default_recordings_folder() -> Path:
    """Vorschlag für den Aufnahme-Ordner: User-Documents/FT991_Recordings."""
    try:
        documents = Path.home() / "Documents"
    except Exception:
        return Path.cwd() / DEFAULT_FOLDER_NAME
    return documents / DEFAULT_FOLDER_NAME


def scan_recordings(folder: Path) -> list[str]:
    """``.mp3``-Dateien im Ordner, neueste zuerst (alphabetisch absteigend = chronologisch)."""
    if not folder.is_dir():
        return []
    names: list[str] = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower(), reverse=True):
        if p.is_file() and p.suffix.lower() == RECORDING_EXTENSION:
            names.append(p.name)
    return names
