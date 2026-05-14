"""Persistenter Speicher für Audioprofile (``data/presets.json``).

Format::

    {
      "version": 1,
      "profiles": [
        { "name": "SSB Sprache", "mode_group": "SSB", ... },
        ...
      ]
    }

Die Klasse :class:`PresetStore` hält die Liste der Profile, kennt Standard-
Vorlagen für SSB/AM/FM und schreibt jede Änderung sofort auf Platte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from ._app_paths import app_data_dir
from .audio_profile import AudioProfile
from .eq_band import EQBand, EQSettings


PRESET_FILE_VERSION = 1


def _make_default_profiles() -> List[AudioProfile]:
    """Liefert eine Hand voll vernünftiger Start-Presets."""
    return [
        AudioProfile(
            name="SSB Sprache",
            mode_group="SSB",
            normal_eq=EQSettings(
                eq1=EQBand(freq=300, level=-3, bw=5),
                eq2=EQBand(freq=1200, level=2, bw=4),
                eq3=EQBand(freq=2500, level=4, bw=3),
            ),
        ),
        AudioProfile(
            name="SSB DX",
            mode_group="SSB",
            normal_eq=EQSettings(
                eq1=EQBand(freq=400, level=-6, bw=5),
                eq2=EQBand(freq=1300, level=4, bw=3),
                eq3=EQBand(freq=2500, level=6, bw=3),
            ),
        ),
        AudioProfile(
            name="AM Sprache",
            mode_group="AM",
            normal_eq=EQSettings(
                eq1=EQBand(freq=200, level=0, bw=5),
                eq2=EQBand(freq=1000, level=2, bw=4),
                eq3=EQBand(freq=2100, level=2, bw=3),
            ),
        ),
        AudioProfile(
            name="FM Relais",
            mode_group="FM",
            normal_eq=EQSettings(
                eq1=EQBand(freq=300, level=0, bw=5),
                eq2=EQBand(freq=1200, level=0, bw=4),
                eq3=EQBand(freq=2300, level=0, bw=3),
            ),
        ),
    ]


@dataclass
class PresetStore:
    path: Path = field(default_factory=lambda: PresetStore.default_path())
    profiles: List[AudioProfile] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Pfade / Laden / Speichern
    # ------------------------------------------------------------------

    @classmethod
    def default_path(cls) -> Path:
        """Pfad zur ``presets.json`` — folgt der gleichen Logik wie
        :meth:`AppSettings.default_path` (Source vs. gefrozener Build)."""
        return app_data_dir() / "presets.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PresetStore":
        path = path or cls.default_path()
        if not path.exists():
            store = cls(path=path, profiles=_make_default_profiles())
            store.save()
            return store

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls(path=path, profiles=_make_default_profiles())

        raw_profiles = data.get("profiles", []) if isinstance(data, dict) else []
        profiles = [AudioProfile.from_dict(p) for p in raw_profiles if isinstance(p, dict)]
        if not profiles:
            profiles = _make_default_profiles()
        return cls(path=path, profiles=profiles)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": PRESET_FILE_VERSION,
            "profiles": [p.to_dict() for p in self.profiles],
        }
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # ------------------------------------------------------------------
    # Zugriff
    # ------------------------------------------------------------------

    def names(self) -> List[str]:
        return [p.name for p in self.profiles]

    def find(self, name: str) -> Optional[AudioProfile]:
        for p in self.profiles:
            if p.name == name:
                return p
        return None

    def upsert(self, profile: AudioProfile) -> None:
        """Aktualisiert oder hängt ein Profil an."""
        for i, p in enumerate(self.profiles):
            if p.name == profile.name:
                self.profiles[i] = profile
                self.save()
                return
        self.profiles.append(profile)
        self.save()

    def remove(self, name: str) -> bool:
        for i, p in enumerate(self.profiles):
            if p.name == name:
                del self.profiles[i]
                self.save()
                return True
        return False

    def replace_all(self, profiles: Iterable[AudioProfile]) -> None:
        self.profiles = list(profiles)
        self.save()
