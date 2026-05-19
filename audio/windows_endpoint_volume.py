"""Windows-Core-Audio: Lautstärke/Stumm pro Geräte-Endpunkt (WASAPI)."""

from __future__ import annotations

import sys
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, TypeVar

_T = TypeVar("_T")

_PYCAW_OK = False
_AudioDeviceState = None
_DEVICE_STATE_ACTIVE = None

if sys.platform == "win32":
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.constants import AudioDeviceState, DEVICE_STATE
        from pycaw.pycaw import (
            AudioUtilities,
            IAudioEndpointVolume,
            IAudioMeterInformation,
        )

        _PYCAW_OK = True
        _IAudioMeterInformation = IAudioMeterInformation
        _AudioDeviceState = AudioDeviceState
        _DEVICE_STATE_ACTIVE = DEVICE_STATE.ACTIVE
    except ImportError:
        _IAudioMeterInformation = None  # type: ignore[misc, assignment]
else:
    _IAudioMeterInformation = None  # type: ignore[misc, assignment]


def windows_endpoint_volume_available() -> bool:
    return _PYCAW_OK


def windows_endpoint_peak_available() -> bool:
    return _PYCAW_OK and _IAudioMeterInformation is not None


@contextmanager
def _suppress_pycaw_property_warnings() -> Iterator[None]:
    """pycaw warnt bei defekten Geräte-Properties (HDMI/NVIDIA offline)."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message=r"COMError attempting to get property",
        )
        yield


def _pycaw_call(func: Callable[[], _T]) -> _T:
    with _suppress_pycaw_property_warnings():
        return func()


def _scalar_to_percent(scalar: float) -> int:
    return max(0, min(100, int(round(float(scalar) * 100.0))))


def _percent_to_scalar(percent: int) -> float:
    return max(0.0, min(1.0, int(percent) / 100.0))


def _normalize_device_id(device_id: str) -> str:
    return str(device_id or "").strip().lower()


def _device_is_active(dev: Any) -> bool:
    """pycaw liefert ``AudioDeviceState``-Enums, nicht rohe Integer."""
    if not _PYCAW_OK:
        return False
    state = getattr(dev, "state", None)
    if state is None:
        return True
    if state == _AudioDeviceState.Active:  # type: ignore[union-attr]
        return True
    if isinstance(state, int):
        return state == int(_AudioDeviceState.Active)  # type: ignore[union-attr]
    return False


def _device_is_capture(dev: Any) -> bool:
    """Render = {0.0.0.…}, Capture = {0.0.1.…} (Qt- und MMDevice-ID)."""
    dev_id = _normalize_device_id(getattr(dev, "id", "") or "")
    if dev_id.startswith("{0.0.1."):
        return True
    if dev_id.startswith("{0.0.0."):
        return False
    return False


def _get_imm_device_by_id(device_id: str) -> Any | None:
    """Direkter MMDevice-Lookup — ohne ``GetAllDevices`` (keine Massen-Warnungen)."""
    if not _PYCAW_OK or not device_id:
        return None
    try:
        enumerator = AudioUtilities.GetDeviceEnumerator()
        return enumerator.GetDevice(device_id)
    except Exception:
        return None


def _enumerate_active_pycaw_devices() -> list[Any]:
    if not _PYCAW_OK:
        return []

    def _fetch() -> list[Any]:
        assert _DEVICE_STATE_ACTIVE is not None
        return list(
            AudioUtilities.GetAllDevices(device_state=_DEVICE_STATE_ACTIVE.value)
        )

    return _pycaw_call(_fetch)


def _find_pycaw_device(qt_device_id: str, *, capture: bool) -> Any | None:
    if not _PYCAW_OK:
        return None
    dev_id = str(qt_device_id or "").strip()
    if dev_id:
        if _device_is_capture_id(dev_id) != capture:
            return None
        direct = _get_imm_device_by_id(dev_id)
        if direct is not None:
            return direct
        return _match_device_by_description(dev_id, capture=capture)

    if capture:
        raw = AudioUtilities.GetMicrophone()
        return raw
    return _pycaw_call(AudioUtilities.GetSpeakers)


def _device_is_capture_id(device_id: str) -> bool:
    return _normalize_device_id(device_id).startswith("{0.0.1.")


def _activate_meter(dev: Any) -> Any | None:
    """``IMMDevice`` / ``AudioDevice`` → ``IAudioMeterInformation``."""
    if not _PYCAW_OK or dev is None or _IAudioMeterInformation is None:
        return None
    try:
        inner = getattr(dev, "_dev", dev)
        iface = inner.Activate(_IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        return iface.QueryInterface(_IAudioMeterInformation)
    except Exception:
        return None


def _activate_endpoint(dev: Any) -> Any | None:
    """``AudioDevice`` (pycaw) oder rohes ``IMMDevice`` → ``IAudioEndpointVolume``."""
    if not _PYCAW_OK or dev is None:
        return None
    try:
        if hasattr(dev, "EndpointVolume"):
            return dev.EndpointVolume
        inner = getattr(dev, "_dev", dev)
        iface = inner.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return iface.QueryInterface(IAudioEndpointVolume)
    except Exception:
        return None


class WindowsEndpointVolume:
    """Steuert Master-Lautstärke und Stumm eines MMDevice-Endpunkts."""

    def __init__(self) -> None:
        self._last_device_id: Optional[str] = None
        self._last_capture: Optional[bool] = None
        self._endpoint = None

    def reset_bind(self) -> None:
        """Bindung verwerfen (z. B. vor erneutem Anwenden der App-Einstellungen)."""
        self._endpoint = None
        self._last_device_id = None
        self._last_capture = None

    def bind(self, qt_device_id: str, *, capture: bool) -> bool:
        if not _PYCAW_OK:
            return False
        dev_id = qt_device_id or ""
        if dev_id == self._last_device_id and capture == self._last_capture:
            return self._endpoint is not None

        self._endpoint = None
        self._last_device_id = dev_id
        self._last_capture = capture

        matched = _find_pycaw_device(dev_id, capture=capture)
        self._endpoint = _activate_endpoint(matched)
        return self._endpoint is not None

    def volume_percent(self) -> Optional[int]:
        if self._endpoint is None:
            return None
        try:
            return _scalar_to_percent(self._endpoint.GetMasterVolumeLevelScalar())
        except Exception:
            return None

    def set_volume_percent(self, percent: int) -> bool:
        if self._endpoint is None:
            return False
        try:
            self._endpoint.SetMasterVolumeLevelScalar(
                _percent_to_scalar(percent), None
            )
            return True
        except Exception:
            return False

    def is_muted(self) -> Optional[bool]:
        if self._endpoint is None:
            return None
        try:
            return bool(self._endpoint.GetMute())
        except Exception:
            return None

    def set_muted(self, muted: bool) -> bool:
        if self._endpoint is None:
            return False
        try:
            self._endpoint.SetMute(1 if muted else 0, None)
            return True
        except Exception:
            return False


class WindowsEndpointPeak:
    """Liest den Peak-Pegel eines MMDevice-Endpunkts (WASAPI)."""

    def __init__(self) -> None:
        self._last_device_id: Optional[str] = None
        self._last_capture: Optional[bool] = None
        self._meter = None

    def reset_bind(self) -> None:
        self._meter = None
        self._last_device_id = None
        self._last_capture = None

    def bind(self, qt_device_id: str, *, capture: bool) -> bool:
        if not windows_endpoint_peak_available():
            return False
        dev_id = qt_device_id or ""
        if dev_id == self._last_device_id and capture == self._last_capture:
            return self._meter is not None

        self._meter = None
        self._last_device_id = dev_id
        self._last_capture = capture

        matched = _find_pycaw_device(dev_id, capture=capture)
        self._meter = _activate_meter(matched)
        return self._meter is not None

    def peak_scalar(self) -> Optional[float]:
        if self._meter is None:
            return None
        try:
            return max(0.0, min(1.0, float(self._meter.GetPeakValue())))
        except Exception:
            return None


def _match_device_by_description(qt_device_id: str, *, capture: bool):
    """Fallback: Qt-Geräte-ID enthält oft den Anzeigenamen."""
    if not _PYCAW_OK or not qt_device_id:
        return None
    needle = qt_device_id.lower()
    for dev in _enumerate_active_pycaw_devices():
        if not _device_is_active(dev):
            continue
        if _device_is_capture(dev) != capture:
            continue
        name = str(getattr(dev, "FriendlyName", "") or "").lower()
        if not name:
            continue
        if name in needle or needle in name:
            inner = getattr(dev, "_dev", None)
            return inner if inner is not None else dev
    return None
