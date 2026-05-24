"""Smoke-Tests für den CAT-Einstellungsdialog."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from cat import SerialCAT
from gui.settings_dialog import ConnectionSettingsDialog
from model import AppSettings


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_connection_settings_dialog_builds(qapp: QApplication) -> None:
    dialog = ConnectionSettingsDialog(AppSettings(), SerialCAT(), parent=None)
    assert dialog.windowTitle()
