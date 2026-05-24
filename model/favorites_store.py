"""Radio-Favoriten (Soll-Vorgaben) in ``favorites.json`` (User-Datenordner)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from i18n import tr

from ._app_paths import app_data_dir

FAVORITES_FILE_VERSION = 1

_NAME_SANITIZE = re.compile(r"[\r\n\0]")


@dataclass
class RadioFavorite:
    """Gespeicherter Favorit: Funk- und Audioprofil-Snapshot."""

    name: str
    frequency_hz: int
    mode: str
    eq_profile_name: str
    squelch: int
    af_gain: int
    rf_gain: int
    pc_power_watts: int

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        self.mode = str(self.mode or "").strip()
        self.eq_profile_name = str(self.eq_profile_name or "").strip()

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> Optional["RadioFavorite"]:
        if not raw or not isinstance(raw, dict):
            return None
        try:
            name = str(raw.get("name", "")).strip()
            if not name:
                return None
            return cls(
                name=name,
                frequency_hz=max(0, int(raw.get("frequency_hz", 0))),
                mode=str(raw.get("mode", "USB")),
                eq_profile_name=str(raw.get("eq_profile_name", "")),
                squelch=max(0, min(100, int(raw.get("squelch", 0)))),
                af_gain=max(0, min(255, int(raw.get("af_gain", 0)))),
                rf_gain=max(0, min(255, int(raw.get("rf_gain", 0)))),
                pc_power_watts=max(0, int(raw.get("pc_power_watts", 0))),
            )
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "frequency_hz": int(self.frequency_hz),
            "mode": self.mode,
            "eq_profile_name": self.eq_profile_name,
            "squelch": int(self.squelch),
            "af_gain": int(self.af_gain),
            "rf_gain": int(self.rf_gain),
            "pc_power_watts": int(self.pc_power_watts),
        }

    @staticmethod
    def validate_name(name: str) -> str:
        n = _NAME_SANITIZE.sub("", str(name or "").strip())
        if not n:
            raise ValueError(tr("favorites.error.name_empty"))
        return n


@dataclass
class FavoritesStore:
    path: Path = field(default_factory=lambda: FavoritesStore.default_path())
    favorites: List[RadioFavorite] = field(default_factory=list)

    @classmethod
    def default_path(cls) -> Path:
        return app_data_dir() / "favorites.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "FavoritesStore":
        path = path or cls.default_path()
        if not path.exists():
            return cls(path=path, favorites=[])

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls(path=path, favorites=[])

        raw_list = data.get("favorites", []) if isinstance(data, dict) else []
        out: list[RadioFavorite] = []
        if isinstance(raw_list, list):
            for item in raw_list:
                fav = RadioFavorite.from_dict(item if isinstance(item, dict) else None)
                if fav is not None and not any(x.name == fav.name for x in out):
                    out.append(fav)
        return cls(path=path, favorites=out)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": FAVORITES_FILE_VERSION,
            "favorites": [f.to_dict() for f in self.favorites],
        }
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def find_name(self, name: str) -> Optional[int]:
        """Index des Eintrags mit genau diesem Namen, sonst ``None``."""
        n = str(name or "").strip()
        for i, f in enumerate(self.favorites):
            if f.name == n:
                return i
        return None

    def upsert(self, fav: RadioFavorite, *, replace_index: Optional[int] = None) -> None:
        """Neu anlegen oder an ``replace_index`` ersetzen; wirft bei Namenskollision."""
        RadioFavorite.validate_name(fav.name)
        if replace_index is not None:
            idx = int(replace_index)
            if not 0 <= idx < len(self.favorites):
                raise IndexError(tr("favorites.error.invalid_index"))
            old_name = self.favorites[idx].name
            for i, x in enumerate(self.favorites):
                if i != idx and x.name == fav.name:
                    raise ValueError(tr("favorites.error.name_taken", name=fav.name))
            self.favorites[idx] = fav
            return
        if any(x.name == fav.name for x in self.favorites):
            raise ValueError(tr("favorites.error.name_taken", name=fav.name))
        self.favorites.append(fav)

    def remove_at(self, index: int) -> None:
        if not 0 <= int(index) < len(self.favorites):
            raise IndexError(tr("favorites.error.invalid_index"))
        del self.favorites[int(index)]


def format_favorite_combo_label(fav: RadioFavorite) -> str:
    """Anzeige: ``Name (145.600 MHz)``."""
    mhz = fav.frequency_hz / 1_000_000.0
    return tr("favorites.combo_item_label", name=fav.name, freq_mhz=mhz)
