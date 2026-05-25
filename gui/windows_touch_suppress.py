"""Windows: Touch-„Drücken und Halten = Rechtsklick“ für Halte-Tasten reduzieren."""

from __future__ import annotations

import sys
from typing import Optional

_SPI_GET_GESTURE_VIS = 0x201C
_SPI_SET_GESTURE_VIS = 0x201B
_SPIGV_TOUCH = 0
_TOUCH_PRESSANDHOLD = 0x0008
_TOUCH_RIGHTTAP = 0x0010

_saved_touch_gesture_flags: Optional[int] = None


def suppress_windows_touch_press_and_hold(*, disable_all_touch_feedback: bool = True) -> None:
    """Touch-Press-and-Hold (Rechtsklick-Kreis) temporär abschwächen/deaktivieren."""
    global _saved_touch_gesture_flags
    if sys.platform != "win32" or _saved_touch_gesture_flags is not None:
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    current = wintypes.DWORD()
    if not user32.SystemParametersInfoW(
        _SPI_GET_GESTURE_VIS,
        _SPIGV_TOUCH,
        ctypes.byref(current),
        0,
    ):
        return

    _saved_touch_gesture_flags = int(current.value)
    if disable_all_touch_feedback:
        new_flags = 0
    else:
        new_flags = _saved_touch_gesture_flags & ~(_TOUCH_PRESSANDHOLD | _TOUCH_RIGHTTAP)
    user32.SystemParametersInfoW(_SPI_SET_GESTURE_VIS, _SPIGV_TOUCH, new_flags, 0)


def restore_windows_touch_press_and_hold() -> None:
    """Vorherige Windows-Touch-Gesten-Einstellung wiederherstellen."""
    global _saved_touch_gesture_flags
    if sys.platform != "win32" or _saved_touch_gesture_flags is None:
        return

    import ctypes

    user32 = ctypes.windll.user32
    user32.SystemParametersInfoW(
        _SPI_SET_GESTURE_VIS,
        _SPIGV_TOUCH,
        _saved_touch_gesture_flags,
        0,
    )
    _saved_touch_gesture_flags = None
