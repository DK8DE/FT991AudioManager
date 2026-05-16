"""Qt-Multimedia-Umgebung (Windows-Backend) vor dem ersten Import setzen."""

from __future__ import annotations

import os
import sys


def ensure_qt_media_backend() -> None:
    """Unter Windows Qt 6.7+: QMediaPlayer braucht explizites Backend.

    Ohne ``QT_MEDIA_BACKEND=windows`` erscheint oft:
    „No QtMultimedia backends found“ / „Failed to initialize QMediaPlayer“.
    """
    if os.environ.get("QT_MEDIA_BACKEND"):
        return
    if sys.platform == "win32":
        os.environ["QT_MEDIA_BACKEND"] = "windows"
