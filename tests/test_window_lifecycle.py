"""Tests für Hilfsfenster-Schließen beim App-Exit."""

from __future__ import annotations

from unittest.mock import MagicMock

from gui.window_lifecycle import application_exit_close_requested


def test_force_close_requests_real_destroy() -> None:
    win = MagicMock()
    win._force_close = True
    win.parent.return_value = None
    assert application_exit_close_requested(win) is True


def test_parent_shutting_down_requests_real_destroy() -> None:
    win = MagicMock()
    win._force_close = False
    parent = MagicMock()
    parent._application_shutting_down = True
    win.parent.return_value = parent
    assert application_exit_close_requested(win) is True


def test_normal_hide_close_not_exit() -> None:
    win = MagicMock()
    win._force_close = False
    parent = MagicMock()
    parent._application_shutting_down = False
    win.parent.return_value = parent
    assert application_exit_close_requested(win) is False
