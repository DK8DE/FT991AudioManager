"""Tests für optimistische Live-PTT-Anzeige."""

from __future__ import annotations

from gui.live_window import effective_live_tx_display_state
from mapping import TX_STATE_CAT_TX, TX_STATE_RX


def test_effective_tx_display_keeps_tx_while_ptt_pending() -> None:
    state = effective_live_tx_display_state(
        TX_STATE_RX,
        want_live_transport=True,
        cat_tx_armed=True,
        cat_live_start_busy=False,
        engine_running=False,
    )
    assert state == TX_STATE_CAT_TX


def test_effective_tx_display_follows_poll_when_idle() -> None:
    state = effective_live_tx_display_state(
        TX_STATE_RX,
        want_live_transport=False,
        cat_tx_armed=False,
        cat_live_start_busy=False,
        engine_running=False,
    )
    assert state == TX_STATE_RX
