"""Tests für MomentaryHoldButton (PTT halten)."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gui.momentary_hold_button import MomentaryHoldButton


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_momentary_hold_mouse_press_release(qapp: QApplication) -> None:
    btn = MomentaryHoldButton("PTT")
    btn.show()
    presses: list[bool] = []
    releases: list[bool] = []
    btn.pressed.connect(lambda: presses.append(True))
    btn.released.connect(lambda: releases.append(True))

    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert btn.is_held()
    assert presses == [True]
    assert releases == []

    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not btn.is_held()
    assert releases == [True]
