"""Auflösung der Pfade für User-Daten und mitgelieferte Ressourcen.

User-Daten (``settings.json``, ``presets.json``, Kalibrierung, Backups …)
-------------------------------------------------------------------------
* **Entwicklung** (nicht gefroren): ``<Projekt-Root>/data/``
* **Installierte EXE** (PyInstaller, ``sys.frozen``): Benutzerverzeichnis,
  damit unter ``Program Files`` keine Schreibrechte nötig sind:

  - Windows: ``%APPDATA%\\FT991AudioManager``
  - macOS: ``~/Library/Application Support/FT991AudioManager``
  - Linux: ``$XDG_CONFIG_HOME/FT991AudioManager`` bzw.
    ``~/.config/FT991AudioManager``

Beim ersten Start nach einem Update wird ein früherer Ordner
``<EXE-Verzeichnis>/data`` (falls vorhanden und beschreibbar) nach
AppData **kopiert** — vorhandene Dateien dort werden nicht überschrieben.

Ressourcen (Logo, nicht änderbar)
---------------------------------
* Gefroren: ``sys._MEIPASS`` (PyInstaller ``--add-data``)
* Source: Projekt-Root
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Muss mit Installationsordner / GitHub-Repo-Namen übereinstimmen.
_USER_DATA_DIR_NAME = "FT991AudioManager"

_legacy_migrated = False


def _user_data_root() -> Path:
    """Basisordner für alle persistenten Anwenderdaten (ohne ``data/``-Suffix)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / _USER_DATA_DIR_NAME
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / _USER_DATA_DIR_NAME
        )
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / _USER_DATA_DIR_NAME
    return Path.home() / ".config" / _USER_DATA_DIR_NAME


def _legacy_frozen_data_dir() -> Path | None:
    """Früherer Speicherort neben der EXE (vor AppData-Umstellung)."""
    if not getattr(sys, "frozen", False):
        return None
    legacy = Path(sys.executable).resolve().parent / "data"
    return legacy if legacy.is_dir() else None


def _migrate_legacy_data(target: Path) -> None:
    """Kopiert Dateien aus ``<EXE>/data`` nach *target*, falls noch nicht vorhanden."""
    legacy = _legacy_frozen_data_dir()
    if legacy is None:
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in legacy.iterdir():
        dest = target / item.name
        if dest.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        except OSError:
            # Keine Schreibrechte auf Quelle/Ziel — Migration überspringen.
            continue


def app_data_dir() -> Path:
    """Liefert den Ordner für persistente User-Daten (wird angelegt, falls nötig)."""
    global _legacy_migrated
    if getattr(sys, "frozen", False):
        root = _user_data_root()
        if not _legacy_migrated:
            _migrate_legacy_data(root)
            _legacy_migrated = True
        root.mkdir(parents=True, exist_ok=True)
        return root
    root = Path(__file__).resolve().parent.parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def installed_icon_path() -> Path | None:
    """Pfad zur ``logo.ico`` für Shell-Verknüpfungen und Qt (Windows).

    - Installiert: ``<EXE-Verzeichnis>/logo.ico`` (vom Installer neben die EXE gelegt)
    - Entwicklung / Fallback: ``resource_dir()/logo.ico`` in ``_internal`` bzw. Projekt-Root
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "logo.ico")
    root = resource_dir()
    candidates.append(root / "logo.ico")
    for path in candidates:
        if path.is_file():
            return path
    return None


def resource_dir() -> Path:
    """Liefert den Wurzelpfad für **mitgelieferte, nicht änderbare** Assets."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent / "_internal"
    return Path(__file__).resolve().parent.parent
