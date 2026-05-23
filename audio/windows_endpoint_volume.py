"""Windows-Core-Audio: Lautstärke/Stumm pro Geräte-Endpunkt (WASAPI)."""

from __future__ import annotations

import sys
import time
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, Sequence, TypeVar

_T = TypeVar("_T")

_PYCAW_OK = False
_AudioDeviceState: Any = None
_DEVICE_STATE_ACTIVE: Any = None
_IAudioMeterInformation: Any = None
AudioUtilities: Any = None
CLSCTX_ALL: Any = None
IAudioEndpointVolume: Any = None

if sys.platform == "win32":
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.constants import AudioDeviceState, DEVICE_STATE
        from pycaw.pycaw import (
            AudioUtilities,
            IAudioClient,
            IAudioEndpointVolume,
            IAudioMeterInformation,
        )

        _PYCAW_OK = True
        _IAudioMeterInformation = IAudioMeterInformation
        _AudioDeviceState = AudioDeviceState
        _DEVICE_STATE_ACTIVE = DEVICE_STATE.ACTIVE
    except ImportError:
        CLSCTX_ALL = None
        AudioUtilities = None
        IAudioEndpointVolume = None
        _IAudioMeterInformation = None
else:
    _IAudioMeterInformation = None

_DEVICES_CACHE: tuple[list[Any], float] | None = None
_DEVICES_CACHE_TTL_S = 10.0
_MIX_SR_CACHE: dict[tuple[str, bool], int] = {}


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
        # Stubs für COM/pycaw kennen ``GetDevice`` am Enumerator oft nicht.
        enumerator: Any = AudioUtilities.GetDeviceEnumerator()
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


def _cached_active_pycaw_devices() -> list[Any]:
    global _DEVICES_CACHE
    now = time.monotonic()
    if _DEVICES_CACHE is not None:
        devs, ts = _DEVICES_CACHE
        if now - ts < _DEVICES_CACHE_TTL_S:
            return devs
    devs = _enumerate_active_pycaw_devices()
    _DEVICES_CACHE = (devs, now)
    return devs


def invalidate_windows_audio_device_cache() -> None:
    """Geräte- und Mixformat-Cache leeren (z. B. nach „Geräte neu laden“)."""
    global _DEVICES_CACHE
    _DEVICES_CACHE = None
    _MIX_SR_CACHE.clear()


def _find_device_in_list(
    display_name: str,
    *,
    capture: bool,
    devices: Sequence[Any],
) -> Any | None:
    needle = str(display_name or "").strip().lower()
    if not needle:
        return None
    for dev in devices:
        if not _device_is_active(dev):
            continue
        if _device_is_capture(dev) != capture:
            continue
        name = str(getattr(dev, "FriendlyName", "") or "").lower()
        if not name:
            continue
        if name == needle or name in needle or needle in name:
            inner = getattr(dev, "_dev", None)
            return inner if inner is not None else dev
    return None


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


def _find_pycaw_device_by_display_name(display_name: str, *, capture: bool) -> Any | None:
    """Endpunkt anhand des Windows-/Qt-Anzeigenamens (Live-Geräteliste)."""
    if not _PYCAW_OK:
        return None
    return _find_device_in_list(
        display_name,
        capture=capture,
        devices=_cached_active_pycaw_devices(),
    )


def _activate_audio_client(dev: Any) -> Any | None:
    """``IMMDevice`` → ``IAudioClient``."""
    if not _PYCAW_OK or dev is None:
        return None
    try:
        inner = getattr(dev, "_dev", dev)
        iface = inner.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
        return iface.QueryInterface(IAudioClient)
    except Exception:
        return None


def _mix_samplerate_from_device(dev: Any) -> Optional[int]:
    client = _activate_audio_client(dev)
    if client is None:
        return None
    try:
        wf = client.GetMixFormat()
        rate = int(wf.contents.nSamplesPerSec)
        if rate > 0:
            return rate
    except Exception:
        pass
    return None


def windows_mix_samplerate_available() -> bool:
    return _PYCAW_OK


def windows_mix_samplerate_for_display_name(
    display_name: str,
    *,
    capture: bool,
) -> Optional[int]:
    """Shared-Mode-Samplerate laut Windows-WASAPI für den Endpunkt."""
    if not _PYCAW_OK:
        return None
    key = (str(display_name or "").strip().lower(), bool(capture))
    if key[0] and key in _MIX_SR_CACHE:
        return _MIX_SR_CACHE[key]
    dev = _find_pycaw_device_by_display_name(display_name, capture=capture)
    if dev is None:
        return None
    rate = _mix_samplerate_from_device(dev)
    if rate is not None and key[0]:
        _MIX_SR_CACHE[key] = rate
    return rate


def windows_mix_samplerates_for_labels(
    labels: Sequence[tuple[str, bool]],
) -> list[int]:
    """Mehrere Endpunkte mit **einer** Geräte-Enumeration (schneller als Einzelaufrufe)."""
    if not _PYCAW_OK:
        return []
    devices = _cached_active_pycaw_devices()
    hints: list[int] = []
    seen: set[int] = set()
    for display_name, capture in labels:
        key = (str(display_name or "").strip().lower(), bool(capture))
        if not key[0]:
            continue
        if key in _MIX_SR_CACHE:
            rate = _MIX_SR_CACHE[key]
        else:
            dev = _find_device_in_list(
                display_name,
                capture=capture,
                devices=devices,
            )
            rate = _mix_samplerate_from_device(dev) if dev is not None else None
            if rate is not None:
                _MIX_SR_CACHE[key] = rate
        if rate is None or rate <= 0 or rate in seen:
            continue
        seen.add(rate)
        hints.append(rate)
    return hints
