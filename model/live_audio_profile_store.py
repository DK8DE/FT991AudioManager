"""Persistente Live-Audioprofile (``live_audio_profiles.json``)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ._app_paths import app_data_dir

from .live_audio_profile import LiveAudioProfile
from .live_settings import LiveSettings

LIVE_AUDIO_PROFILE_FILE_VERSION = 1
DEFAULT_LIVE_AUDIO_PROFILE_NAME = "Default"


def make_default_live_audio_profile() -> LiveAudioProfile:
    return LiveAudioProfile.from_live_settings(
        LiveSettings(),
        DEFAULT_LIVE_AUDIO_PROFILE_NAME,
    )


def _make_default_profiles() -> List[LiveAudioProfile]:
    return [make_default_live_audio_profile()]


@dataclass
class LiveAudioProfileStore:
    path: Path = field(default_factory=lambda: LiveAudioProfileStore.default_path())
    last_profile: str = ""
    profiles: List[LiveAudioProfile] = field(default_factory=list)

    @classmethod
    def default_path(cls) -> Path:
        return app_data_dir() / "live_audio_profiles.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "LiveAudioProfileStore":
        path = path or cls.default_path()
        if not path.exists():
            store = cls(
                path=path,
                profiles=_make_default_profiles(),
                last_profile=DEFAULT_LIVE_AUDIO_PROFILE_NAME,
            )
            store.save()
            return store

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls(
                path=path,
                profiles=_make_default_profiles(),
                last_profile=DEFAULT_LIVE_AUDIO_PROFILE_NAME,
            )

        raw_profiles = data.get("profiles", []) if isinstance(data, dict) else []
        profiles = [
            LiveAudioProfile.from_dict(entry)
            for entry in raw_profiles
            if isinstance(entry, dict)
        ]
        if not profiles:
            profiles = _make_default_profiles()
        last = str(data.get("last_profile", "") or "").strip() if isinstance(data, dict) else ""
        if last and not any(p.name == last for p in profiles):
            last = profiles[0].name
        elif not last and profiles:
            last = profiles[0].name
        return cls(path=path, profiles=profiles, last_profile=last)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": LIVE_AUDIO_PROFILE_FILE_VERSION,
            "last_profile": str(self.last_profile or ""),
            "profiles": [p.to_dict() for p in self.profiles],
        }
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def names(self) -> List[str]:
        return [p.name for p in self.profiles if p.name.strip()]

    def find(self, name: str) -> Optional[LiveAudioProfile]:
        key = str(name or "").strip()
        for profile in self.profiles:
            if profile.name == key:
                return profile
        return None

    def upsert(self, profile: LiveAudioProfile) -> None:
        profile.clamp()
        for index, existing in enumerate(self.profiles):
            if existing.name == profile.name:
                self.profiles[index] = profile
                self.last_profile = profile.name
                self.save()
                return
        self.profiles.append(profile)
        self.last_profile = profile.name
        self.save()

    def remove(self, name: str) -> bool:
        key = str(name or "").strip()
        for index, profile in enumerate(self.profiles):
            if profile.name == key:
                del self.profiles[index]
                if not self.profiles:
                    self.profiles = _make_default_profiles()
                if self.last_profile == key:
                    self.last_profile = self.profiles[0].name
                self.save()
                return True
        return False

    def set_last_profile(self, name: str) -> None:
        key = str(name or "").strip()
        if not key:
            return
        if self.find(key) is None:
            return
        self.last_profile = key
        self.save()
