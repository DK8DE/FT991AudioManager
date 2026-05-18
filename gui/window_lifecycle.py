"""Hilfen für Hilfsfenster, die normalerweise nur versteckt werden."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


def application_exit_close_requested(window: QWidget) -> bool:
    """True, wenn das Fenster beim Schließen wirklich zerstört werden soll."""
    if getattr(window, "_force_close", False):
        return True
    parent = window.parent()
    return parent is not None and bool(
        getattr(parent, "_application_shutting_down", False)
    )
