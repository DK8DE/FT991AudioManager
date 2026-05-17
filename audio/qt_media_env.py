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
    """Backend-Wahl fuer Windows (vor dem ersten QtMultimedia-Import)."""
    if sys.platform != "win32":
        return None
    # PyInstaller-Bundle: ffmpeg (kleiner/zuverlaessiger fuer MP3 im Dist).
    if getattr(sys, "frozen", False):
        if _has_plugin("ffmpeg"):
            return "ffmpeg"
        if _has_plugin("windows"):
            return "windows"
        return "ffmpeg"
    # Dev (pip): ffmpeg-Plugin liegt oft da, laedt aber ohne FFmpeg-DLLs nicht —
    # dann „No QtMultimedia backends found“. windows (WMF) funktioniert typisch.
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
