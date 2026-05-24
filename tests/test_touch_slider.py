"""Tests für TouchSlider."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gui.touch_slider import TouchSlider


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_touch_slider_click_sets_value(qapp: QApplication) -> None:
    slider = TouchSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.resize(240, 32)
    slider.show()
    qapp.processEvents()

    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=slider.rect().center())
    qapp.processEvents()
    assert 0 <= slider.value() <= 100


def test_touch_slider_minimum_height_for_touch(qapp: QApplication) -> None:
    slider = TouchSlider(Qt.Orientation.Horizontal)
    assert slider.minimumHeight() >= 32

    vertical = TouchSlider(Qt.Orientation.Vertical)
    assert vertical.minimumWidth() >= 32
