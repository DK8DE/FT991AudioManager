"""Liefert das App-Icon plattformübergreifend.

Strategie:

* **Windows**: ``logo.ico`` enthält mehrere Auflösungen (16/32/48/256 px)
  und ist das offiziell von Windows erwartete Format für EXE-Icon,
  Taskbar und Title-Bar. Wir laden bevorzugt die ``.ico``.
* **Linux / macOS**: SVG-Icons werden vom System (und von Qt) sauber
  in beliebige Größen skaliert. Wir laden ``logo.svg``.
* In beiden Fällen wird das jeweils andere Format als Fallback gemerged
  -- die ``QIcon``-Klasse darf mehrere Pixmap-Quellen tragen, Qt wählt
  beim Zeichnen die passendste Größe selbst.

Resource-Pfade werden über :func:`model._app_paths.resource_dir`
aufgelöst -- das deckt Source-Layout und PyInstaller-Bundle gemeinsam ab.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QIcon

from model._app_paths import resource_dir


def _candidate_paths() -> list[Path]:
    """Bevorzugte Reihenfolge der Icon-Dateien je nach Plattform."""
    root = resource_dir()
    ico = root / "logo.ico"
    svg = root / "logo.svg"
    if sys.platform.startswith("win"):
        return [ico, svg]
    return [svg, ico]


_CACHED_ICON: Optional[QIcon] = None


def app_icon() -> QIcon:
    """Gibt das App-Icon zurück (memoisiert).

    Wenn keine der Icon-Dateien gefunden wird, liefert die Funktion ein
    leeres ``QIcon`` -- die Anwendung läuft dann ohne Icon, statt mit
    einem Stacktrace abzubrechen.
    """
    global _CACHED_ICON
    if _CACHED_ICON is not None:
        return _CACHED_ICON

    icon = QIcon()
    for path in _candidate_paths():
        if path.is_file():
            # addFile fügt eine weitere Pixmap-Quelle hinzu, ohne die
            # vorherige zu verdrängen -- so kann Windows die .ico und
            # andere Plattformen die .svg nutzen, beide aus demselben
            # QIcon-Objekt heraus.
            icon.addFile(str(path))
    _CACHED_ICON = icon
    return icon


__all__ = ["app_icon"]
