"""Zentrale Soundeinstellungen: global, Windows-Sync, UI-Signale."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from model import AppSettings
from model.global_audio_settings import (
    AUDIO_ROLES,
    ROLE_INPUT,
    ROLE_PC,
    ROLE_SEND,
    sync_global_to_legacy,
)

from .audio_level_monitor import AudioLevelMonitor
from .windows_endpoint_volume import (
    WindowsEndpointVolume,
    windows_endpoint_volume_available,
)


class AudioSettingsHub(QObject):
    """Eine Quelle für Geräte/Lautstärke/Mithören im gesamten Programm."""

    device_changed = Signal(str, str)
    volume_changed = Signal(str, int)
    mute_changed = Signal(str, bool)
    tx_monitor_changed = Signal(bool)
  # role, device_id / percent / muted

    def __init__(self, settings: AppSettings, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._win_input = WindowsEndpointVolume()
        self._win_send = WindowsEndpointVolume()
        self._win_pc = WindowsEndpointVolume()
        self._suppress_poll = 0
        self._last_polled: dict[str, tuple[int, bool]] = {}

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_windows)
        if windows_endpoint_volume_available():
            self._poll_timer.start()

        self._level_monitor = AudioLevelMonitor(self, parent=self)

    @property
    def level_monitor(self) -> AudioLevelMonitor:
        return self._level_monitor

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def global_audio(self):
        return self._settings.global_audio

    def uses_windows_volume(self) -> bool:
        return windows_endpoint_volume_available()

    def device_id(self, role: str) -> str:
        return self.global_audio.device_id_for(role)

    def set_device_id(self, role: str, device_id: str) -> None:
        dev = str(device_id or "")
        g = self.global_audio
        if g.device_id_for(role) == dev:
            return
        g.set_device_id_for(role, dev)
        sync_global_to_legacy(g, self._settings.audio_player, self._settings.audio_recorder)
        self._apply_windows_for_role(role)
        self.device_changed.emit(role, dev)

    def volume_percent(self, role: str) -> int:
        return self.global_audio.volume_percent_for(role)

    def set_volume_percent(self, role: str, percent: int) -> None:
        v = max(0, min(100, int(percent)))
        g = self.global_audio
        if g.volume_percent_for(role) == v:
            return
        g.set_volume_percent_for(role, v)
        sync_global_to_legacy(g, self._settings.audio_player, self._settings.audio_recorder)
        self._apply_windows_volume(role, v)
        self._last_polled[role] = (v, g.muted_for(role))
        self.volume_changed.emit(role, v)

    def is_muted(self, role: str) -> bool:
        return self.global_audio.muted_for(role)

    def set_muted(self, role: str, muted: bool) -> None:
        m = bool(muted)
        g = self.global_audio
        if g.muted_for(role) == m:
            return
        g.set_muted_for(role, m)
        sync_global_to_legacy(g, self._settings.audio_player, self._settings.audio_recorder)
        self._apply_windows_mute(role, m)
        self._last_polled[role] = (g.volume_percent_for(role), m)
        self.mute_changed.emit(role, m)

    def tx_monitor_to_pc_enabled(self) -> bool:
        return bool(self.global_audio.tx_monitor_to_pc_enabled)

    def set_tx_monitor_to_pc_enabled(self, enabled: bool) -> None:
        en = bool(enabled)
        g = self.global_audio
        if g.tx_monitor_to_pc_enabled == en:
            return
        g.tx_monitor_to_pc_enabled = en
        sync_global_to_legacy(g, self._settings.audio_player, self._settings.audio_recorder)
        self.tx_monitor_changed.emit(en)

    def stop_polling(self) -> None:
        self._poll_timer.stop()
        self._level_monitor.stop()

    def sync_from_windows(self) -> None:
        """Windows-Mixer → App (beim Start; danach über Poll)."""
        if not self.uses_windows_volume():
            return
        g = self.global_audio
        self._suppress_poll += 1
        try:
            for role in AUDIO_ROLES:
                ctl = self._win_ctl(role)
                if not ctl.bind(
                    g.device_id_for(role), capture=self._capture_for_role(role)
                ):
                    continue
                vol = ctl.volume_percent()
                muted = ctl.is_muted()
                if vol is None or muted is None:
                    continue
                self._last_polled[role] = (vol, muted)
                if g.volume_percent_for(role) != vol:
                    g.set_volume_percent_for(role, vol)
                    self.volume_changed.emit(role, vol)
                if g.muted_for(role) != muted:
                    g.set_muted_for(role, muted)
                    self.mute_changed.emit(role, muted)
            sync_global_to_legacy(
                g, self._settings.audio_player, self._settings.audio_recorder
            )
        finally:
            self._suppress_poll -= 1

    def apply_all_windows(self) -> None:
        """App → Windows-Mixer (nach expliziter Änderung in der App)."""
        if not self.uses_windows_volume():
            return
        for role in AUDIO_ROLES:
            self._apply_windows_for_role(role)

    def push_role_to_windows(self, role: str, *, unmute: bool = False) -> bool:
        """App-Einstellungen für eine Rolle an den Windows-Mixer schreiben.

        Liefert ``True``, wenn der Endpunkt gebunden werden konnte.
        """
        if not self.uses_windows_volume():
            return True
        g = self.global_audio
        ctl = self._win_ctl(role)
        self._suppress_poll += 1
        try:
            ctl.reset_bind()
            if not ctl.bind(
                g.device_id_for(role), capture=self._capture_for_role(role)
            ):
                return False
            self._apply_windows_volume(role, g.volume_percent_for(role))
            if unmute:
                self._apply_windows_mute(role, False)
            else:
                self._apply_windows_mute(role, g.muted_for(role))
            self._last_polled[role] = (
                g.volume_percent_for(role),
                False if unmute else g.muted_for(role),
            )
            return True
        finally:
            self._suppress_poll -= 1

    def windows_role_bound(self, role: str) -> bool:
        if not self.uses_windows_volume():
            return True
        ctl = self._win_ctl(role)
        return ctl.bind(
            self.global_audio.device_id_for(role),
            capture=self._capture_for_role(role),
        )

    def qt_volume_percent(self, role: str) -> int:
        """Qt-interne Lautstärke: bei Windows-Steuerung immer 100 %."""
        if self.uses_windows_volume():
            return 100
        return self.volume_percent(role)

    def _win_ctl(self, role: str) -> WindowsEndpointVolume:
        if role == ROLE_INPUT:
            return self._win_input
        if role == ROLE_SEND:
            return self._win_send
        if role == ROLE_PC:
            return self._win_pc
        raise ValueError(role)

    def _capture_for_role(self, role: str) -> bool:
        return role == ROLE_INPUT

    def _apply_windows_for_role(self, role: str) -> None:
        if not self.uses_windows_volume():
            return
        g = self.global_audio
        ctl = self._win_ctl(role)
        self._suppress_poll += 1
        try:
            ctl.bind(g.device_id_for(role), capture=self._capture_for_role(role))
            self._apply_windows_volume(role, g.volume_percent_for(role))
            self._apply_windows_mute(role, g.muted_for(role))
            self._last_polled[role] = (
                g.volume_percent_for(role),
                g.muted_for(role),
            )
        finally:
            self._suppress_poll -= 1

    def _apply_windows_volume(self, role: str, percent: int) -> None:
        if not self.uses_windows_volume():
            return
        ctl = self._win_ctl(role)
        if ctl.bind(
            self.global_audio.device_id_for(role),
            capture=self._capture_for_role(role),
        ):
            ctl.set_volume_percent(percent)

    def _apply_windows_mute(self, role: str, muted: bool) -> None:
        if not self.uses_windows_volume():
            return
        ctl = self._win_ctl(role)
        if ctl.bind(
            self.global_audio.device_id_for(role),
            capture=self._capture_for_role(role),
        ):
            ctl.set_muted(muted)

    def _poll_windows(self) -> None:
        if not self.uses_windows_volume() or self._suppress_poll > 0:
            return
        g = self.global_audio
        for role in AUDIO_ROLES:
            ctl = self._win_ctl(role)
            if not ctl.bind(
                g.device_id_for(role), capture=self._capture_for_role(role)
            ):
                continue
            vol = ctl.volume_percent()
            muted = ctl.is_muted()
            if vol is None or muted is None:
                continue
            last = self._last_polled.get(role)
            if last == (vol, muted):
                continue
            self._last_polled[role] = (vol, muted)
            changed = False
            if g.volume_percent_for(role) != vol:
                g.set_volume_percent_for(role, vol)
                changed = True
                self.volume_changed.emit(role, vol)
            if g.muted_for(role) != muted:
                g.set_muted_for(role, muted)
                changed = True
                self.mute_changed.emit(role, muted)
            if changed:
                sync_global_to_legacy(
                    g, self._settings.audio_player, self._settings.audio_recorder
                )
