"""Liefert das App-Icon plattformübergreifend.

Windows (installierte EXE)
    ``logo.ico`` liegt neben ``FT991AudioManager.exe`` (Inno Setup) und
    zusätzlich in ``_internal/`` (PyInstaller). Für Taskleiste und
    Titelleiste wird die ``.ico`` bevorzugt — kein SVG (liefert dort oft
    eine leere/weiße Fläche).

Linux / macOS
    ``logo.svg`` aus dem Projekt-Root bzw. Bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QIcon

from model._app_paths import installed_icon_path, resource_dir


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    ico = installed_icon_path()
    if ico is not None:
        paths.append(ico)
    if sys.platform.startswith("win"):
        return paths
    root = resource_dir()
    paths.append(root / "logo.svg")
    paths.append(root / "logo.ico")
    return paths


_CACHED_ICON: Optional[QIcon] = None


def app_icon() -> QIcon:
    """Gibt das App-Icon zurück (memoisiert)."""
    global _CACHED_ICON
    if _CACHED_ICON is not None:
        return _CACHED_ICON

    for path in _candidate_paths():
        if not path.is_file():
            continue
        loaded = QIcon(str(path))
        if not loaded.isNull():
            _CACHED_ICON = loaded
            return _CACHED_ICON

    _CACHED_ICON = QIcon()
    return _CACHED_ICON


__all__ = ["app_icon"]
