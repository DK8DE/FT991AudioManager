"""Persistenter Speicher für Audioprofile (``data/presets.json``).

Format::

    {
      "version": 1,
      "profiles": [
        { "name": "Default", ... },
        ...
      ]
    }

Beim ersten Start (keine Datei) und wenn die Liste leer wird, wird genau
ein neutrales Profil ``Default`` angelegt (EQ aus, Processor aus).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from ._app_paths import app_data_dir
from mapping.audio_mapping import MIC_GAIN_DEFAULT, PROCESSOR_LEVEL_DEFAULT, SSB_BPF_DEFAULT_KEY

from .audio_profile import AudioProfile
from .eq_band import EQSettings
from .extended_settings import ExtendedSettings


PRESET_FILE_VERSION = 1
DEFAULT_PROFILE_NAME = "Default"


def make_flat_default_profile() -> AudioProfile:
    """Ein neutrales Startprofil: EQ/Processor aus, Bänder OFF, Level 0."""
    return AudioProfile(
        name=DEFAULT_PROFILE_NAME,
        normal_eq=EQSettings.default(),
        processor_eq=EQSettings.default(),
        mic_gain=MIC_GAIN_DEFAULT,
        mic_eq_enabled=False,
        speech_processor_enabled=False,
        speech_processor_level=PROCESSOR_LEVEL_DEFAULT,
        ssb_tx_bpf=SSB_BPF_DEFAULT_KEY,
        extended=ExtendedSettings(),
    )


def _make_default_profiles() -> List[AudioProfile]:
    return [make_flat_default_profile()]


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
                if not self.profiles:
                    self.profiles = _make_default_profiles()
                self.save()
                return True
        return False

    def ensure_defaults(self) -> bool:
        """Legt Standard-Profile an, wenn die Liste leer ist."""
        if self.profiles:
            return False
        self.profiles = _make_default_profiles()
        self.save()
        return True

    def rename(self, old_name: str, new_name: str) -> bool:
        """Benennt ein Profil um (Name muss eindeutig sein)."""
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return False
        if self.find(new_name) is not None:
            return False
        for i, p in enumerate(self.profiles):
            if p.name == old_name:
                updated = AudioProfile.from_dict({**p.to_dict(), "name": new_name})
                self.profiles[i] = updated
                self.save()
                return True
        return False

    def replace_all(self, profiles: Iterable[AudioProfile]) -> None:
        self.profiles = list(profiles)
        if not self.profiles:
            self.profiles = _make_default_profiles()
        self.save()

    def export_to_file(self, path: Path) -> None:
        """Schreibt alle Profile nach ``path`` (JSON, gleiches Format wie ``presets.json``)."""
        payload = {
            "version": PRESET_FILE_VERSION,
            "profiles": [p.to_dict() for p in self.profiles],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")

    @staticmethod
    def profiles_from_export_file(path: Path) -> List[AudioProfile]:
        """Liest Profile aus einer Export-JSON-Datei."""
        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        raw_profiles = data.get("profiles", []) if isinstance(data, dict) else []
        profiles: List[AudioProfile] = []
        for entry in raw_profiles:
            if isinstance(entry, dict):
                profiles.append(AudioProfile.from_dict(entry))
        if not profiles:
            raise ValueError("Die Datei enthält keine gültigen EQ-Profile.")
        return profiles

    def import_replace_all_from_file(self, path: Path) -> int:
        """Ersetzt alle lokalen Profile durch den Inhalt der Export-Datei."""
        profiles = self.profiles_from_export_file(path)
        self.profiles = profiles
        self.save()
        return len(profiles)
