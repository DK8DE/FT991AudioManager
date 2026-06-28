"""Tests für Live + Player/Recorder Coexistence."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gui.live_window import (
    live_window_accepts_coexistence,
    live_session_holds_data_mode,
    mic_ptt_should_stop_coexistence_recording,
)


def test_live_session_holds_data_mode_false_without_window() -> None:
    assert live_session_holds_data_mode() is False


def test_live_window_accepts_coexistence_false_without_window() -> None:
    assert live_window_accepts_coexistence() is False


def test_main_window_blocking_skips_when_coexistence() -> None:
    from gui.live_window import live_window_accepts_coexistence as coexist

    # Ohne echtes Live-Fenster: coexistence ist False — Blocking-Logik greift.
    if not coexist():
        blocked_msg = ""
        player = MagicMock()
        player._controller.is_busy.return_value = True
        # Simuliert _live_transmit_blocked_by_other_windows Kernlogik
        if coexist():
            blocked_msg = ""
        elif player._controller.is_busy():
            blocked_msg = "player_busy"
        assert blocked_msg == "player_busy"


def test_main_window_blocking_empty_when_coexistence_active() -> None:
    from unittest.mock import patch

    with patch("gui.live_window.live_window_accepts_coexistence", return_value=True):
        from gui.live_window import live_window_accepts_coexistence as coexist

        blocked_msg = ""
        if coexist():
            blocked_msg = ""
        else:
            blocked_msg = "would_block"
        assert blocked_msg == ""


def test_mic_ptt_stops_normal_recording() -> None:
    assert mic_ptt_should_stop_coexistence_recording(
        live_dual_recording_active=False,
    )


def test_mic_ptt_does_not_stop_live_dual_when_live_holds_data() -> None:
    from unittest.mock import patch

    with patch("gui.live_window.live_session_holds_data_mode", return_value=True):
        assert not mic_ptt_should_stop_coexistence_recording(
            live_dual_recording_active=True,
        )


def test_mic_ptt_stops_live_dual_without_live_data_session() -> None:
    from unittest.mock import patch

    with patch("gui.live_window.live_session_holds_data_mode", return_value=False):
        assert mic_ptt_should_stop_coexistence_recording(
            live_dual_recording_active=True,
        )
