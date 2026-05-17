"""Funkgerät für Audio-Wiedergabe vorbereiten (DATA-USB/LSB/FM + Menü 072)."""

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


#: Zuordnung Data-Mode → korrespondierender Sprach/SSB/FM-Mode (für MIC-PTT).
DATA_TO_VOICE: dict[RxMode, RxMode] = {
    RxMode.DATA_USB: RxMode.USB,
    RxMode.DATA_LSB: RxMode.LSB,
    RxMode.DATA_FM: RxMode.FM,
}


def data_mode_from_string(name: str) -> RxMode:
    """``"DATA-FM"`` etc. → :class:`RxMode`. Fällt auf ``DATA_FM`` zurück."""
    upper = (name or "").upper().strip()
    for mode in (RxMode.DATA_USB, RxMode.DATA_LSB, RxMode.DATA_FM):
        if mode.value.upper() == upper:
            return mode
    return RxMode.DATA_FM


def voice_mode_for_data(mode: RxMode) -> RxMode:
    """Liefert den Sprach-Mode für einen DATA-Mode (Default USB)."""
    return DATA_TO_VOICE.get(mode, RxMode.USB)


@dataclass
class RadioAudioSnapshot:
    """Zustand vor dem Audio-Player."""

    rx_mode: RxMode
    data_port_raw: str


class RadioPlaybackSetup:
    """Schaltet Audio-Modus + DATA-Port (EX072) und stellt zurück."""

    def __init__(
        self,
        serial_cat: SerialCAT,
        data_mode: RxMode = RxMode.DATA_FM,
    ) -> None:
        self._cat = serial_cat
        self._snapshot: Optional[RadioAudioSnapshot] = None
        self._data_mode = data_mode if data_mode in DATA_TO_VOICE else RxMode.DATA_FM
        #: True, wenn aktuell ``data_mode`` aktiv ist (Audio läuft / pausiert).
        #: False, wenn wir wegen MIC-PTT auf den Sprach-Mode geschaltet haben.
        self._in_data_mode = False
        #: True, wenn das letzte ``engage_plain_mode(force=True)`` ohne Verify
        #: lief — beim nächsten TX→RX-Übergang muss noch verifiziert werden,
        #: ob der Mode-Wechsel tatsächlich angekommen ist.
        self._needs_plain_verify = False

    @property
    def is_applied(self) -> bool:
        return self._snapshot is not None

    @property
    def data_mode(self) -> RxMode:
        return self._data_mode

    @property
    def voice_mode(self) -> RxMode:
        return voice_mode_for_data(self._data_mode)

    @property
    def in_data_mode(self) -> bool:
        return self._in_data_mode

    @property
    def needs_plain_verify(self) -> bool:
        return self._needs_plain_verify

    def set_data_mode(self, mode: RxMode) -> tuple[bool, str]:
        """Wechselt den gewünschten Data-Mode.

        Ist gerade ein DATA-Mode aktiv, wird live umgeschaltet. Andernfalls
        wird die Einstellung nur gemerkt und beim nächsten ``apply()``
        bzw. ``engage_data_mode()`` verwendet.
        """
        if mode not in DATA_TO_VOICE:
            return False, f"Unbekannter Data-Mode: {mode}"
        if mode == self._data_mode:
            return True, ""
        self._data_mode = mode
        if not self._snapshot or not self._in_data_mode:
            return True, ""
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — Mode-Wechsel nicht möglich."
        ft = FT991CAT(self._cat)
        try:
            if not ft.set_rx_mode(self._data_mode):
                return False, f"Betriebsart {self._data_mode.value} konnte nicht gesetzt werden."
            return True, f"Funkgerät: {self._data_mode.value}"
        except CatError as exc:
            return False, str(exc)

    def apply(self) -> tuple[bool, str]:
        """Schnappschuss + DATA-Mode + DATA-Port = USB (EX072)."""
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — Modus/072 werden nicht geändert."
        if self._snapshot is not None:
            if not self._in_data_mode:
                return self.engage_data_mode()
            return True, f"Funkgerät bereits auf {self._data_mode.value} / USB (072)."

        ft = FT991CAT(self._cat)
        try:
            current_mode = ft.read_rx_mode()
            data_port_raw = ft.read_menu(DATA_PORT_MENU)
            self._snapshot = RadioAudioSnapshot(
                rx_mode=current_mode,
                data_port_raw=data_port_raw,
            )
            if not ft.set_rx_mode(self._data_mode):
                self._snapshot = None
                return False, f"Betriebsart {self._data_mode.value} konnte nicht gesetzt werden."
            ft.write_menu(
                DATA_PORT_MENU,
                encode_mic_source(MicSource.REAR),
                tx_lock=True,
            )
            self._in_data_mode = True
            return True, f"Funkgerät: {self._data_mode.value}, Menü 072 → USB (Rear-DATA)"
        except CatError as exc:
            self._snapshot = None
            return False, str(exc)

    def engage_plain_mode(self, *, force: bool = False) -> tuple[bool, str]:
        """Schaltet vom DATA-Mode auf den Sprach-Mode (USB/LSB/FM).

        Bei ``force=True`` wird der TX-Lock übersprungen und der Schreibvorgang
        ohne Verifikation gesendet. Das ist nötig, wenn der User MIC-PTT hält
        — das Radio sendet dann, ``MD0X;`` würde sonst mit ``TxLockError``
        blockiert. Der Mode-Wechsel wird auf vielen FT-991(A) trotz TX
        angenommen; falls nicht, schaltet das Audio-Fenster beim TX→RX-
        Übergang nochmal sauber verifiziert nach.
        """
        if self._snapshot is None:
            return True, ""
        if not self._in_data_mode:
            return True, ""
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — Sprach-Mode nicht setzbar."
        voice = self.voice_mode
        ft = FT991CAT(self._cat)
        try:
            if force:
                # Während aktiver TX (MIC PTT) ohne Lock und ohne Verify
                # absetzen — Verify würde fehlschlagen, da der MD-Read
                # während TX häufig den alten Wert liefert.
                ft.set_rx_mode(voice, tx_lock=False, verify=False)
                self._in_data_mode = False
                self._needs_plain_verify = True
                return True, f"Funkgerät: {voice.value} (MIC PTT, forced)"
            if not ft.set_rx_mode(voice):
                return False, f"Sprach-Mode {voice.value} konnte nicht gesetzt werden."
            self._in_data_mode = False
            self._needs_plain_verify = False
            return True, f"Funkgerät: {voice.value}"
        except CatError as exc:
            return False, str(exc)

    def verify_plain_mode(self) -> tuple[bool, str]:
        """Liest den Mode und schaltet bei Bedarf nochmal verifiziert nach.

        Wird nach einem ``engage_plain_mode(force=True)`` aufgerufen, sobald
        das Radio wieder im RX ist (User hat MIC-PTT losgelassen).
        """
        if not self._needs_plain_verify:
            return True, ""
        if self._snapshot is None:
            self._needs_plain_verify = False
            return True, ""
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — Mode-Verify nicht möglich."
        voice = self.voice_mode
        ft = FT991CAT(self._cat)
        try:
            current = ft.read_rx_mode()
            if current == voice:
                self._needs_plain_verify = False
                return True, ""
            if not ft.set_rx_mode(voice):
                return False, f"Sprach-Mode {voice.value} konnte nicht verifiziert werden."
            self._needs_plain_verify = False
            return True, f"Funkgerät verifiziert: {voice.value}"
        except CatError as exc:
            return False, str(exc)

    def engage_data_mode(self) -> tuple[bool, str]:
        """Schaltet (zurück) auf den konfigurierten DATA-Mode."""
        if self._snapshot is None:
            return False, "Audio-Setup nicht aktiv — bitte erst Apply."
        if self._in_data_mode:
            self._needs_plain_verify = False
            return True, ""
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — DATA-Mode nicht setzbar."
        ft = FT991CAT(self._cat)
        try:
            if not ft.set_rx_mode(self._data_mode):
                return False, f"DATA-Mode {self._data_mode.value} konnte nicht gesetzt werden."
            self._in_data_mode = True
            self._needs_plain_verify = False
            return True, f"Funkgerät: {self._data_mode.value}"
        except CatError as exc:
            return False, str(exc)

    def restore(self) -> tuple[bool, str]:
        if self._snapshot is None:
            return True, ""
        if not self._cat.is_connected():
            self._snapshot = None
            self._in_data_mode = False
            return False, "CAT nicht verbunden — alter Modus konnte nicht wiederhergestellt werden."

        snap = self._snapshot
        self._snapshot = None
        self._in_data_mode = False
        self._needs_plain_verify = False
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
    data_mode_finished = Signal(bool, str)
    engage_plain_finished = Signal(bool, str)
    engage_data_finished = Signal(bool, str)

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

    @Slot(str)
    def run_set_data_mode(self, mode_name: str) -> None:
        ok, msg = self._setup.set_data_mode(data_mode_from_string(mode_name))
        self.data_mode_finished.emit(ok, msg)

    @Slot()
    def run_engage_plain(self) -> None:
        ok, msg = self._setup.engage_plain_mode()
        self.engage_plain_finished.emit(ok, msg)

    @Slot()
    def run_engage_plain_forced(self) -> None:
        ok, msg = self._setup.engage_plain_mode(force=True)
        self.engage_plain_finished.emit(ok, msg)

    @Slot()
    def run_engage_data(self) -> None:
        ok, msg = self._setup.engage_data_mode()
        self.engage_data_finished.emit(ok, msg)

    @Slot()
    def run_verify_plain(self) -> None:
        ok, msg = self._setup.verify_plain_mode()
        self.engage_plain_finished.emit(ok, msg)
