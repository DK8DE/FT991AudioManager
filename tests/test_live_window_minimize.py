"""Live-Fenster: Audio beim Minimieren nicht beenden."""

from __future__ import annotations

from gui.live_window import (
    _live_window_accepts_background_audio,
    _should_release_ptt_on_window_leave,
)


class _FakeLiveWindow:
    def __init__(
        self,
        *,
        visible: bool = True,
        minimized: bool = False,
        force_close: bool = False,
    ) -> None:
        self._visible = visible
        self._minimized = minimized
        self._force_close = force_close

    def isVisible(self) -> bool:
        return self._visible

    def isMinimized(self) -> bool:
        return self._minimized


def test_should_not_release_ptt_when_minimized() -> None:
    assert (
        _should_release_ptt_on_window_leave(
            visible=False,
            minimized=True,
            force_close=False,
        )
        is False
    )


def test_should_release_ptt_when_leaving_to_other_app() -> None:
    assert (
        _should_release_ptt_on_window_leave(
            visible=True,
            minimized=False,
            force_close=False,
        )
        is True
    )


def test_live_window_accepts_background_audio_when_minimized() -> None:
    w = _FakeLiveWindow(visible=False, minimized=True)
    assert _live_window_accepts_background_audio(w) is True


def test_live_window_rejects_audio_when_hidden() -> None:
    w = _FakeLiveWindow(visible=False, minimized=False)
    assert _live_window_accepts_background_audio(w) is False
