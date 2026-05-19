"""Peak-Pegelüberwachung für Windows-Audio-Endpunkte (pro Rolle)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from model.global_audio_settings import AUDIO_ROLES, ROLE_INPUT

from .windows_endpoint_volume import (
    WindowsEndpointPeak,
    windows_endpoint_peak_available,
)

if TYPE_CHECKING:
    from .audio_settings_hub import AudioSettingsHub


class AudioLevelMonitor(QObject):
    """Pollt WASAPI-Peaks und meldet sie pro Rolle (``input`` / ``send`` / ``pc``)."""

    level_changed = Signal(str, float)

    def __init__(
        self,
        hub: Optional["AudioSettingsHub"] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._hub = hub
        self._peaks = {role: WindowsEndpointPeak() for role in AUDIO_ROLES}
        self._overrides: dict[str, tuple[str, bool]] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        if windows_endpoint_peak_available():
            self._timer.start()

    def set_hub(self, hub: Optional["AudioSettingsHub"]) -> None:
        self._hub = hub

    def set_role_override(
        self, role: str, device_id: str, *, capture: bool
    ) -> None:
        self._overrides[role] = (str(device_id or ""), bool(capture))

    def clear_role_override(self, role: str) -> None:
        self._overrides.pop(role, None)

    def stop(self) -> None:
        self._timer.stop()

    def start(self) -> None:
        if windows_endpoint_peak_available():
            self._timer.start()

    def _poll(self) -> None:
        for role in AUDIO_ROLES:
            device_id, capture = self._device_for_role(role)
            if device_id is None:
                continue
            peak_ctl = self._peaks[role]
            if not peak_ctl.bind(device_id, capture=capture):
                self.level_changed.emit(role, 0.0)
                continue
            scalar = peak_ctl.peak_scalar()
            if scalar is None:
                continue
            self.level_changed.emit(role, scalar)

    def _device_for_role(self, role: str) -> tuple[Optional[str], bool]:
        capture = role == ROLE_INPUT
        if role in self._overrides:
            dev_id, cap = self._overrides[role]
            return dev_id, cap
        if self._hub is not None:
            return self._hub.device_id(role), capture
        return None, capture
