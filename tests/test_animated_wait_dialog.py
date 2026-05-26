"""Tests für AnimatedWaitDialog."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from gui.animated_wait_dialog import AnimatedWaitDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_animated_wait_dialog_progress_moves(qapp: QApplication) -> None:
    import time

    dlg = AnimatedWaitDialog("Loading…", "Test")
    dlg.start()
    dlg._started = time.monotonic() - 1.5
    dlg._on_tick()
    assert dlg.value() > 0
    assert dlg.value() < 100
    assert "1." in dlg.labelText() or "1," in dlg.labelText()
    dlg.finish()
    assert dlg.value() == 100


def test_animated_wait_dialog_bump(qapp: QApplication) -> None:
    dlg = AnimatedWaitDialog("Loading…", "Test")
    dlg.start()
    dlg.bump(40)
    assert dlg.value() >= 40
    dlg.bump(30)
    assert dlg.value() >= 40
