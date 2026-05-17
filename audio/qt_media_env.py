"""Qt-Multimedia-Umgebung (Windows-Backend) vor dem ersten Import setzen."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _pyside6_multimedia_plugin_dir() -> Path | None:
    try:
        import PySide6  # noqa: WPS433 — Pfad zur Plugin-DLL

        root = Path(PySide6.__file__).resolve().parent
    except ImportError:
        return None
    plugins = root / "plugins" / "multimedia"
    return plugins if plugins.is_dir() else None


def _has_plugin(name: str) -> bool:
    plugins = _pyside6_multimedia_plugin_dir()
    if plugins is None:
        return False
    return (plugins / f"{name}mediaplugin.dll").is_file()


def default_qt_media_backend() -> str | None:
    """Backend-Wahl fuer Windows (vor dem ersten QtMultimedia-Import).

    Wir bevorzugen IMMER ``windows`` (Media Foundation), wenn das Plugin da
    ist — auf Windows 10/11 kann WMF MP3 sowohl dekodieren als auch
    *encodieren*. Das mit PySide6 gebundelte ``ffmpeg``-Backend kann MP3
    zwar abspielen, aber unter Windows fast nie encodieren (LAME ist aus
    Lizenzgruenden nicht enthalten) — Folge: Recorder produziert eine
    leere/header-only Datei. Deshalb auch im PyInstaller-Bundle Vorrang
    fuer WMF.
    """
    if sys.platform != "win32":
        return None
    if _has_plugin("windows"):
        return "windows"
    if _has_plugin("ffmpeg"):
        return "ffmpeg"
    return "windows"


def ensure_qt_media_backend() -> str | None:
    """``QT_MEDIA_BACKEND`` setzen (vor dem ersten QtMultimedia-Import)."""
    if getattr(sys, "frozen", False):
        if os.environ.get("QT_MEDIA_BACKEND"):
            return os.environ["QT_MEDIA_BACKEND"]
    else:
        # Dev: altes ``ffmpeg`` aus main.py/Shell bricht oft ohne FFmpeg-DLLs.
        # Immer passendes Backend waehlen (typisch ``windows``).
        pass
    backend = default_qt_media_backend()
    if backend:
        os.environ["QT_MEDIA_BACKEND"] = backend
    return backend
