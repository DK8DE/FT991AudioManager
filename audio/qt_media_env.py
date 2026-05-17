"""Qt-Multimedia-Umgebung (Windows-Backend) vor dem ersten Import setzen.

Zusaetzlich zur Backend-Wahl per ``QT_MEDIA_BACKEND``-Env stellen wir auf
Windows sicher, dass der Loader die FFmpeg-Shared-Libs des PySide6-Wheels
(``avformat-*.dll``, ``avcodec-*.dll`` etc.) findet — ohne das laedt der
``ffmpegmediaplugin.dll`` still in den Out-of-DLL-Fallback-Pfad und das
ganze Backend meldet "No QtMultimedia backends found".

Hintergrund: Auf Microsoft-Store-Python (``PythonSoftwareFoundation.
Python.*``) ignoriert der Windows-DLL-Loader die ``PATH``-Variable
komplett — Abhaengigkeiten muessen explizit per
:func:`os.add_dll_directory` registriert werden. PySide6 macht das normal
selbst im ``__init__``, aber unter dem Store-Python greift das nicht
zuverlaessig fuer Sub-Plugin-Abhaengigkeiten (Plugin laedt sich selbst,
aber seine FFmpeg-Libs nicht).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_pyside6_dll_dir_added = False


def _pyside6_root() -> Path | None:
    try:
        import PySide6  # noqa: WPS433 — Pfad zur Plugin-DLL

        return Path(PySide6.__file__).resolve().parent
    except ImportError:
        return None


def _pyside6_multimedia_plugin_dir() -> Path | None:
    root = _pyside6_root()
    if root is None:
        return None
    plugins = root / "plugins" / "multimedia"
    return plugins if plugins.is_dir() else None


def _has_plugin(name: str) -> bool:
    plugins = _pyside6_multimedia_plugin_dir()
    if plugins is None:
        return False
    return (plugins / f"{name}mediaplugin.dll").is_file()


def _ensure_pyside6_dll_search_path() -> None:
    """PySide6-Root in den DLL-Suchpfad eintragen (idempotent).

    Muss VOR dem ersten ``from PySide6.QtMultimedia import ...`` laufen,
    sonst kann der Backend-Plugin-Loader die FFmpeg-Shared-Libs nicht
    aufloesen (siehe Modul-Docstring).
    """
    global _pyside6_dll_dir_added
    if _pyside6_dll_dir_added:
        return
    if sys.platform != "win32":
        _pyside6_dll_dir_added = True
        return
    root = _pyside6_root()
    if root is None or not root.is_dir():
        return
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:  # pragma: no cover — nur < 3.8
        return
    try:
        add_dll_directory(str(root))
    except (OSError, FileNotFoundError):
        return
    _pyside6_dll_dir_added = True


def default_qt_media_backend() -> str | None:
    """Backend-Wahl fuer Windows (vor dem ersten QtMultimedia-Import).

    Wir bevorzugen ``ffmpeg``, weil der Recorder uncompressed **WAV/PCM**
    schreibt (anschliessend encoden wir per ``lameenc`` in MP3 — so haben
    wir vollen Pegel-Kontrolle). Der Windows-Media-Foundation-Backend kann
    zwar MP3 encodieren, **kein WAV** — er produziert sonst stillschweigend
    AAC mit ``.wav``-Endung ("file does not start with RIFF id"-Fehler im
    Post-Processing). Der mit PySide6 gebuendelte ``ffmpeg``-Backend kennt
    WAV/PCM-Encoding zuverlaessig.

    Reihenfolge:

    1. ``ffmpeg``  — bevorzugt (WAV-Encoding fuer den Recorder).
    2. ``windows`` — Fallback fuer Maschinen ohne ffmpeg-Plugin (Recorder
       wird dann beim Start einen klaren Fehler liefern, der Audio-Player
       funktioniert aber weiter, weil WMF MP3-Decoding beherrscht).
    """
    if sys.platform != "win32":
        return None
    if _has_plugin("ffmpeg"):
        return "ffmpeg"
    if _has_plugin("windows"):
        return "windows"
    return "ffmpeg"


#: Logging-Kategorien des Qt-Multimedia-FFmpeg-Backends, die wir im
#: Normalbetrieb stumm haben wollen — sonst spammt jede Aufnahme/
#: Wiedergabe stderr voll ("Recording new media with muxer …",
#: "Input #0, mp3, from …" usw.). Beim Debuggen kann der User per
#: ``$env:QT_LOGGING_RULES="qt.multimedia.*=true"`` die wieder einschalten.
_QT_MULTIMEDIA_QUIET_RULES = "qt.multimedia.*=false"


def _ensure_qt_multimedia_logging_quiet() -> None:
    """``QT_LOGGING_RULES`` so erweitern, dass Multimedia-Backend nicht spammt.

    Eine bereits vorhandene User-``QT_LOGGING_RULES`` lassen wir
    unangetastet — der User hat dann offensichtlich gezielt etwas
    eingestellt und unsere Default-Stummschaltung waere unerwartet.
    """
    if os.environ.get("QT_LOGGING_RULES"):
        return
    os.environ["QT_LOGGING_RULES"] = _QT_MULTIMEDIA_QUIET_RULES


def ensure_qt_media_backend() -> str | None:
    """``QT_MEDIA_BACKEND`` setzen + DLL-Suchpfad fuer FFmpeg vorbereiten.

    Muss VOR dem ersten ``from PySide6.QtMultimedia import ...`` laufen.

    Eine bereits gesetzte ``QT_MEDIA_BACKEND``-Umgebungsvariable
    respektieren wir immer — so kann der User per Shell gezielt einen
    anderen Backend erzwingen, z. B. zum Debuggen.
    """
    _ensure_pyside6_dll_search_path()
    _ensure_qt_multimedia_logging_quiet()
    explicit = os.environ.get("QT_MEDIA_BACKEND")
    if explicit:
        return explicit
    backend = default_qt_media_backend()
    if backend:
        os.environ["QT_MEDIA_BACKEND"] = backend
    return backend
