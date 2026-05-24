"""Einstiegspunkt für den FT-991/A Audiomanager."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Stellt sicher, dass das Projektverzeichnis im PYTHONPATH liegt, auch wenn
# main.py per Doppelklick gestartet wird.
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


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

    from PySide6.QtWidgets import QApplication

    from gui.app_icon import app_icon
    from gui.theme import apply_theme
    from i18n import init_language, install_qt_translations
    from model import AppSettings

    settings = AppSettings.load()
    init_language(settings.ui.language)

    app = QApplication(sys.argv)
    install_qt_translations(app, settings.ui.language)

    from live.live_devices import remap_live_settings_devices

    if remap_live_settings_devices(settings.live):
        settings.save()

    from gui.tooltip_text import install_tooltip_line_wrap

    install_tooltip_line_wrap()
    # GUI erst nach QApplication importieren (QtMultimedia braucht das).
    from gui import MainWindow
    app.setApplicationName("FT-991/A Audiomanager")
    app.setOrganizationName("DK8DE Jörg Körner")
    # App-Icon zentral setzen: vererbt sich auf alle Top-Level-Fenster
    # (Title-Bar + Windows-Taskbar / macOS-Dock / Linux-Panel).
    app.setWindowIcon(app_icon())
    apply_theme(app, dark=settings.ui.force_dark_mode)

    window = MainWindow(settings)
    app.aboutToQuit.connect(window.shutdown_background_services)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
