"""Qt Multimedia erst nach QApplication lazy laden.

Ein Import von ``PySide6.QtMultimedia`` vor ``QApplication(...)`` fuehrt unter
Windows oft zu ``QMediaPlayer.Error.ResourceError`` / „Not available“ — auch
wenn PySide6-Addons installiert sind.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple, Type

from .qt_media_env import ensure_qt_media_backend

# (QAudioOutput, QMediaDevices, QMediaPlayer) oder False nach fehlgeschlagenem Import
_mm_types: Optional[Tuple[Type[Any], Type[Any], Type[Any]]] | bool = None

# (QAudioInput, QMediaCaptureSession, QMediaRecorder, QMediaFormat)
_rec_types: (
    Optional[Tuple[Type[Any], Type[Any], Type[Any], Type[Any]]] | bool
) = None


def qt_multimedia_types() -> Optional[Tuple[Type[Any], Type[Any], Type[Any]]]:
    """Multimedia-Klassen laden, sobald eine QApplication laeuft."""
    global _mm_types
    if _mm_types is False:
        return None
    if isinstance(_mm_types, tuple):
        return _mm_types

    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        return None

    ensure_qt_media_backend()
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

        _mm_types = (QAudioOutput, QMediaDevices, QMediaPlayer)
        return _mm_types
    except ImportError:
        _mm_types = False
        return None


def multimedia_import_ok() -> bool:
    return qt_multimedia_types() is not None


def qt_recorder_types() -> Optional[Tuple[Type[Any], Type[Any], Type[Any], Type[Any]]]:
    """Recorder-Klassen laden, sobald eine QApplication laeuft."""
    global _rec_types
    if _rec_types is False:
        return None
    if isinstance(_rec_types, tuple):
        return _rec_types

    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        return None

    ensure_qt_media_backend()
    try:
        from PySide6.QtMultimedia import (  # noqa: WPS433 — lazy by design
            QAudioInput,
            QMediaCaptureSession,
            QMediaFormat,
            QMediaRecorder,
        )

        _rec_types = (QAudioInput, QMediaCaptureSession, QMediaRecorder, QMediaFormat)
        return _rec_types
    except ImportError:
        _rec_types = False
        return None


def recorder_import_ok() -> bool:
    return qt_recorder_types() is not None
