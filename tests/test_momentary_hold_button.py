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


def test_momentary_hold_focus_out_while_held(qapp: QApplication) -> None:
    btn = MomentaryHoldButton("PTT")
    btn.show()
    releases: list[bool] = []
    btn.released.connect(lambda: releases.append(True))

    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert btn.is_held()

    btn.clearFocus()
    qapp.processEvents()
    assert btn.is_held()
    assert releases == []

    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not btn.is_held()
    assert releases == [True]


def test_momentary_hold_no_context_menu(qapp: QApplication) -> None:
    btn = MomentaryHoldButton("PTT")
    assert btn.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu

    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtCore import QPoint

    blocked: list[bool] = []
    btn.customContextMenuRequested.connect(lambda _p: blocked.append(True))
    btn.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            QPoint(4, 4),
        )
    )
    qapp.processEvents()
    assert blocked == []


def test_momentary_hold_no_focus_policy(qapp: QApplication) -> None:
    btn = MomentaryHoldButton("PTT")
    assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_momentary_hold_touch_spurious_release_keeps_hold(qapp: QApplication) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-Touch-Long-Press")
    import time

    btn = MomentaryHoldButton("PTT")
    btn.show()
    releases: list[bool] = []
    btn.released.connect(lambda: releases.append(True))

    btn._handle_touch_point(1, Qt.TouchPointState.TouchPointPressed, None)
    btn._engage_mono = time.monotonic() - 0.5
    qapp.processEvents()
    assert btn.is_held()

    btn._handle_touch_point(1, Qt.TouchPointState.TouchPointReleased, None)
    qapp.processEvents()
    assert btn.is_held()
    assert releases == []

    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not btn.is_held()
    assert releases == [True]


def test_windows_touch_suppress_roundtrip() -> None:
    from gui.windows_touch_suppress import (
        restore_windows_touch_press_and_hold,
        suppress_windows_touch_press_and_hold,
    )

    suppress_windows_touch_press_and_hold()
    restore_windows_touch_press_and_hold()
    restore_windows_touch_press_and_hold()


def test_momentary_hold_right_click_ignored_while_left_held(qapp: QApplication) -> None:
    btn = MomentaryHoldButton("PTT")
    btn.show()
    releases: list[bool] = []
    btn.released.connect(lambda: releases.append(True))

    QTest.mousePress(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert btn.is_held()

    QTest.mousePress(btn, Qt.MouseButton.RightButton)
    qapp.processEvents()
    assert btn.is_held()
    assert releases == []

    QTest.mouseRelease(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not btn.is_held()
