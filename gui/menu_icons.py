"""Standard-Icons für Hauptmenü-Aktionen (Qt / Freedesktop mit Fallback)."""

from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle


def menu_action_icon(
    standard: QStyle.StandardPixmap,
    *,
    theme_name: str = "",
) -> QIcon:
    """Freedesktop-Icon, falls vorhanden; sonst ``QStyle.standardIcon``."""
    if theme_name:
        themed = QIcon.fromTheme(theme_name)
        if not themed.isNull():
            return themed
    app = QApplication.instance()
    style = app.style() if app is not None else None
    if style is not None:
        return style.standardIcon(standard)
    return QIcon()
