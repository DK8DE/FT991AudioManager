"""Funkgerät für Audio-Wiedergabe vorbereiten (DATA-FM + Menü 072 USB)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from cat import CatError, FT991CAT, SerialCAT
from mapping.extended_mapping import (
    DATA_PORT_MENU,
    MicSource,
    encode_mic_source,
)
from mapping.rx_mapping import RxMode


@dataclass
class RadioAudioSnapshot:
    """Zustand vor dem Audio-Player."""

    rx_mode: RxMode
    data_port_raw: str


class RadioPlaybackSetup:
    """Schaltet für Over-the-Air-Audio auf DATA-FM + USB (EX072) und stellt zurück."""

    def __init__(self, serial_cat: SerialCAT) -> None:
        self._cat = serial_cat
        self._snapshot: Optional[RadioAudioSnapshot] = None

    @property
    def is_applied(self) -> bool:
        return self._snapshot is not None

    def apply(self) -> tuple[bool, str]:
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — Modus/072 werden nicht geändert."
        if self._snapshot is not None:
            return True, "Funkgerät bereits auf DATA-FM / USB (072)."

        ft = FT991CAT(self._cat)
        try:
            current_mode = ft.read_rx_mode()
            data_port_raw = ft.read_menu(DATA_PORT_MENU)
            self._snapshot = RadioAudioSnapshot(
                rx_mode=current_mode,
                data_port_raw=data_port_raw,
            )
            if not ft.set_rx_mode(RxMode.DATA_FM):
                self._snapshot = None
                return False, "Betriebsart DATA-FM konnte nicht gesetzt werden."
            ft.write_menu(
                DATA_PORT_MENU,
                encode_mic_source(MicSource.REAR),
                tx_lock=True,
            )
            return True, "Funkgerät: DATA-FM, Menü 072 → USB (Rear-DATA)"
        except CatError as exc:
            self._snapshot = None
            return False, str(exc)

    def restore(self) -> tuple[bool, str]:
        if self._snapshot is None:
            return True, ""
        if not self._cat.is_connected():
            self._snapshot = None
            return False, "CAT nicht verbunden — alter Modus konnte nicht wiederhergestellt werden."

        snap = self._snapshot
        self._snapshot = None
        ft = FT991CAT(self._cat)
        try:
            ft.write_menu(DATA_PORT_MENU, snap.data_port_raw, tx_lock=False)
            if not ft.set_rx_mode(snap.rx_mode):
                return (
                    False,
                    f"Alter Modus {snap.rx_mode.value} konnte nicht wiederhergestellt werden.",
                )
            return True, f"Funkgerät zurück: {snap.rx_mode.value}, Menü 072 wie zuvor"
        except CatError as exc:
            return False, str(exc)


class RadioSetupWorker(QObject):
    """CAT-Umschaltung im Hintergrund (blockiert UI nicht)."""

    apply_finished = Signal(bool, str)
    restore_finished = Signal(bool, str)

    def __init__(self, setup: RadioPlaybackSetup) -> None:
        super().__init__()
        self._setup = setup

    @Slot()
    def run_apply(self) -> None:
        ok, msg = self._setup.apply()
        self.apply_finished.emit(ok, msg)

    @Slot()
    def run_restore(self) -> None:
        ok, msg = self._setup.restore()
        self.restore_finished.emit(ok, msg)
