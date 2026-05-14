"""Auflösung der Wurzel für User-Daten (``settings.json``, ``presets.json``).

Hintergrund
-----------
Bei einem mit PyInstaller gebauten ``onedir``-Bundle liegt die EXE neben
einem ``_internal\\``-Ordner mit allen Python-Modulen. Würden die
``default_path()``-Methoden weiter ``Path(__file__).parent.parent``
verwenden, landeten Settings & Profile in ``_internal\\data\\`` — also
mitten in den Bibliotheken. Das ist aus zwei Gründen schlecht:

1. Bei einem Update des Bundles würde ``_internal\\`` ersetzt — alle
   gespeicherten Profile wären weg.
2. Es widerspricht der Konvention "Code & Daten getrennt halten".

Wir wollen deshalb in gefrozenen Builds die User-Daten **neben** der EXE
ablegen (``<EXE-Verzeichnis>\\data\\``). Im Source-Layout bleibt es bei
``<Projekt-Root>\\data\\``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_data_dir() -> Path:
    """Liefert den Wurzelpfad für persistente User-Daten.

    - Gefrorener Build (``sys.frozen``): ``<EXE-Verzeichnis>/data``
    - Source-Layout: ``<Projekt-Root>/data``
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    # ``__file__`` liegt in ``model/_app_paths.py`` — zwei Ebenen hoch
    # landen wir im Projekt-Root.
    return Path(__file__).resolve().parent.parent / "data"
