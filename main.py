"""Einstiegspunkt für den FT-991A Audio-Profilmanager."""

from __future__ import annotations

import sys
from pathlib import Path

# Stellt sicher, dass das Projektverzeichnis im PYTHONPATH liegt, auch wenn
# main.py per Doppelklick gestartet wird.
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from gui import MainWindow
    from gui.theme import apply_theme
    from model import AppSettings

    app = QApplication(sys.argv)
    app.setApplicationName("FT-991A Audio-Profilmanager")
    app.setOrganizationName("FT991-Audio-Manager")

    settings = AppSettings.load()
    apply_theme(app, dark=settings.ui.force_dark_mode)

    window = MainWindow(settings)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
