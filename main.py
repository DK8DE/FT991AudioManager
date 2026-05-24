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
        import warnings

        from audio.qt_media_env import ensure_qt_media_backend

        ensure_qt_media_backend()
        # pycaw: harmlose COM-Warnungen bei offline-Geräten (HDMI/NVIDIA …).
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message=r"COMError attempting to get property",
        )

    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication

    from gui.app_icon import app_icon
    from gui.theme import apply_theme
    from model import AppSettings

    app = QApplication(sys.argv)
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    _install_german_qt_translations(app)

    from gui.tooltip_text import install_tooltip_line_wrap

    install_tooltip_line_wrap()
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
    app.aboutToQuit.connect(window.shutdown_background_services)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
