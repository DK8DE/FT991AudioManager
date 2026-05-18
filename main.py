"""Einstiegspunkt für den FT-991/A Audiomanager."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication

# Stellt sicher, dass das Projektverzeichnis im PYTHONPATH liegt, auch wenn
# main.py per Doppelklick gestartet wird.
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _install_german_qt_translations(app: QCoreApplication) -> None:
    """Lädt die mitgelieferten Qt-Übersetzungen (u. a. Ja/Nein/Abbrechen, OK)."""
    from PySide6.QtCore import QLibraryInfo, QTranslator

    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for prefix in ("qtbase", "qt"):
        translator = QTranslator()
        if translator.load(f"{prefix}_de", path):
            app.installTranslator(translator)


def main() -> int:
    if sys.platform == "win32":
        from audio.qt_media_env import ensure_qt_media_backend

        ensure_qt_media_backend()

    from PySide6.QtWidgets import QApplication

    from gui.app_icon import app_icon
    from gui.theme import apply_theme
    from model import AppSettings

    app = QApplication(sys.argv)
    _install_german_qt_translations(app)
    # GUI erst nach QApplication importieren (QtMultimedia braucht das).
    from gui import MainWindow
    app.setApplicationName("FT-991/A Audiomanager")
    app.setOrganizationName("DK8DE Jörg Körner")
    # App-Icon zentral setzen: vererbt sich auf alle Top-Level-Fenster
    # (Title-Bar + Windows-Taskbar / macOS-Dock / Linux-Panel).
    app.setWindowIcon(app_icon())

    settings = AppSettings.load()
    apply_theme(app, dark=settings.ui.force_dark_mode)

    window = MainWindow(settings)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
