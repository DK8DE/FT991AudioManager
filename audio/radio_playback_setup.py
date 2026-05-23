"""Funkgerät für Audio-Wiedergabe vorbereiten (DATA-Mode + EX048/070/072/077/109).

Optional kann nur die Menüumschaltung (048·070·072·077·109) erfolgen, ohne die
Betriebsart sofort auf DATA zu stellen — siehe :meth:`RadioPlaybackSetup.apply_pc_audio_menus_only`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

from cat import CatError, FT991CAT, SerialCAT
from mapping.extended_mapping import (
    AM_PORT_SELECT_MENU,
    DATA_IN_SELECT_MENU,
    DATA_PORT_MENU,
    FM_PKT_PORT_SELECT_MENU,
    FM_PKT_PORT_USB_RAW,
    MicSource,
    PORT_SELECT_USB_RAW,
    SSB_PORT_SELECT_MENU,
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


def data_mode_for_rx_mode(mode: RxMode) -> RxMode:
    """Aktueller Funkmodus → passender DATA-Modus für Audio-Wiedergabe.

    FM → DATA-FM, USB (und übliche USB-Spracharten) → DATA-USB,
    LSB (und übliche LSB-Spracharten) → DATA-LSB. Bereits DATA-* bleibt unverändert.
    """
    if mode in DATA_TO_VOICE:
        return mode
    if mode in (RxMode.LSB, RxMode.CW_L, RxMode.RTTY_LSB):
        return RxMode.DATA_LSB
    if mode in (RxMode.FM, RxMode.FM_N, RxMode.C4FM):
        return RxMode.DATA_FM
    return RxMode.DATA_USB


@dataclass
class RadioAudioSnapshot:
    """Zustand vor dem Audio-Player / -Recorder."""

    rx_mode: RxMode
    am_port_raw: str
    data_in_select_raw: str
    data_port_raw: str
    fm_pkt_port_raw: str
    ssb_port_raw: str


class RadioPlaybackSetup:
    """Schaltet DATA-Mode + EX048/070/072/077/109 (USB bzw. REAR); Restore der Altwerte.

    Optionales Teilen: :meth:`apply_pc_audio_menus_only` legt nur den Schnappschuss
    der Menüs an und schreibt 048/070/072/077/109 — ohne Betriebsart (MD) zu
    wechseln. :meth:`apply` bzw. :meth:`engage_data_mode` schalten später auf DATA.
    """

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

    def align_data_mode_to_rx_mode(self, mode: RxMode) -> None:
        """DATA-Zielmodus aus aktuellem Sprach-/DATA-Modus ableiten (ohne CAT)."""
        self._data_mode = data_mode_for_rx_mode(mode)

    def reconcile_in_data_mode_with_radio(self) -> bool:
        """Abgleich: ``_in_data_mode`` nur True, wenn das Gerät im Ziel-DATA-Mode ist.

        Nach externem Wechsel (Speicherkanal, Drehknopf) kann das Radio in FM/USB
        stehen, während die Session intern noch „in DATA“ gemeldet ist.
        """
        if not self._in_data_mode:
            return False
        if self._snapshot is None or not self._cat.is_connected():
            return self._in_data_mode
        try:
            current = FT991CAT(self._cat).read_rx_mode()
        except CatError:
            return self._in_data_mode
        if current != self._data_mode:
            self._in_data_mode = False
        return self._in_data_mode

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

    def _write_pc_audio_menus(self) -> tuple[bool, str]:
        """EX048/070/072/077/109 für PC-Audio (USB bzw. REAR; FM-PKT USB)."""
        ft = FT991CAT(self._cat)
        try:
            rear = encode_mic_source(MicSource.REAR)
            usb = PORT_SELECT_USB_RAW
            fm_usb = FM_PKT_PORT_USB_RAW
            ft.write_menu(AM_PORT_SELECT_MENU, usb, tx_lock=True)
            ft.write_menu(DATA_IN_SELECT_MENU, rear, tx_lock=True)
            ft.write_menu(DATA_PORT_MENU, rear, tx_lock=True)
            ft.write_menu(FM_PKT_PORT_SELECT_MENU, fm_usb, tx_lock=True)
            ft.write_menu(SSB_PORT_SELECT_MENU, usb, tx_lock=True)
            return (
                True,
                "Menü 048/070/072/077/109 → PC-Audio "
                "(048/109 USB, 077 USB, 070 REAR, 072 USB)",
            )
        except CatError as exc:
            return False, str(exc)

    def apply_pc_audio_menus_only(self) -> tuple[bool, str]:
        """Schnappschuss laden, nur EX-Menüs setzen — RX-Mode (MD) unverändert.

        Beim Schließen der Session :meth:`restore` stellt Menüs und Mode wieder her.
        """
        if not self._cat.is_connected():
            return (
                False,
                "CAT nicht verbunden — Menüs 048/070/072/077/109 werden nicht geändert.",
            )
        if self._snapshot is not None:
            if self._in_data_mode:
                return True, ""
            return self._write_pc_audio_menus()
        ft = FT991CAT(self._cat)
        try:
            current_mode = ft.read_rx_mode()
            am_port_raw = ft.read_menu(AM_PORT_SELECT_MENU)
            data_in_raw = ft.read_menu(DATA_IN_SELECT_MENU)
            data_port_raw = ft.read_menu(DATA_PORT_MENU)
            fm_pkt_raw = ft.read_menu(FM_PKT_PORT_SELECT_MENU)
            ssb_port_raw = ft.read_menu(SSB_PORT_SELECT_MENU)
            self._snapshot = RadioAudioSnapshot(
                rx_mode=current_mode,
                am_port_raw=am_port_raw,
                data_in_select_raw=data_in_raw,
                data_port_raw=data_port_raw,
                fm_pkt_port_raw=fm_pkt_raw,
                ssb_port_raw=ssb_port_raw,
            )
        except CatError as exc:
            self._snapshot = None
            return False, str(exc)
        ok_m, msg_m = self._write_pc_audio_menus()
        if not ok_m:
            self._snapshot = None
            return False, msg_m
        self._in_data_mode = False
        self._needs_plain_verify = False
        return True, msg_m

    def apply(self) -> tuple[bool, str]:
        """Schnappschuss + DATA-Mode; EX048/109→USB, EX077→USB, EX070/072→REAR (048·070·072·077·109)."""
        if not self._cat.is_connected():
            return (
                False,
                "CAT nicht verbunden — Modus und Menüs 048/070/072/077/109 werden nicht geändert.",
            )
        if self._snapshot is not None:
            if not self._in_data_mode:
                return self.engage_data_mode()
            return True, (
                f"Funkgerät bereits auf {self._data_mode.value}: "
                "048/109=USB, 077=USB, 070=REAR, 072=USB."
            )

        ft = FT991CAT(self._cat)
        try:
            current_mode = ft.read_rx_mode()
            am_port_raw = ft.read_menu(AM_PORT_SELECT_MENU)
            data_in_raw = ft.read_menu(DATA_IN_SELECT_MENU)
            data_port_raw = ft.read_menu(DATA_PORT_MENU)
            fm_pkt_raw = ft.read_menu(FM_PKT_PORT_SELECT_MENU)
            ssb_port_raw = ft.read_menu(SSB_PORT_SELECT_MENU)
            self._snapshot = RadioAudioSnapshot(
                rx_mode=current_mode,
                am_port_raw=am_port_raw,
                data_in_select_raw=data_in_raw,
                data_port_raw=data_port_raw,
                fm_pkt_port_raw=fm_pkt_raw,
                ssb_port_raw=ssb_port_raw,
            )
            if not ft.set_rx_mode(self._data_mode):
                self._snapshot = None
                return False, f"Betriebsart {self._data_mode.value} konnte nicht gesetzt werden."
            ok_m, msg_m = self._write_pc_audio_menus()
            if not ok_m:
                self._snapshot = None
                return False, msg_m
            self._in_data_mode = True
            return True, (
                f"Funkgerät: {self._data_mode.value}, "
                "048/109 → USB (AM/SSB-Port), 077 → USB (FM-PKT), 070 → REAR, 072 → USB"
            )
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
            if self._cat.is_connected():
                try:
                    current = FT991CAT(self._cat).read_rx_mode()
                    if current == self._data_mode:
                        self._needs_plain_verify = False
                        return True, ""
                    self._in_data_mode = False
                except CatError:
                    self._in_data_mode = False
            else:
                self._needs_plain_verify = False
                return True, ""
        if not self._cat.is_connected():
            return False, "CAT nicht verbunden — DATA-Mode nicht setzbar."
        # ``_data_mode`` kommt vom Hauptfenster (sync_data_mode_from_main) —
        # nicht aus ``snapshot.rx_mode`` (das ist nur der Wiederherstellungs-Mode).
        ft = FT991CAT(self._cat)
        try:
            if not ft.set_rx_mode(self._data_mode):
                return False, f"DATA-Mode {self._data_mode.value} konnte nicht gesetzt werden."
            ok_m, msg_m = self._write_pc_audio_menus()
            if not ok_m:
                return False, msg_m
            self._in_data_mode = True
            self._needs_plain_verify = False
            return True, f"Funkgerät: {self._data_mode.value} (048/070/072/077/109 für PC-Audio)"
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
            ft.write_menu(AM_PORT_SELECT_MENU, snap.am_port_raw, tx_lock=False)
            ft.write_menu(DATA_IN_SELECT_MENU, snap.data_in_select_raw, tx_lock=False)
            ft.write_menu(DATA_PORT_MENU, snap.data_port_raw, tx_lock=False)
            ft.write_menu(FM_PKT_PORT_SELECT_MENU, snap.fm_pkt_port_raw, tx_lock=False)
            ft.write_menu(SSB_PORT_SELECT_MENU, snap.ssb_port_raw, tx_lock=False)
            if not ft.set_rx_mode(snap.rx_mode):
                return (
                    False,
                    f"Alter Modus {snap.rx_mode.value} konnte nicht wiederhergestellt werden.",
                )
            return True, (
                f"Funkgerät zurück: {snap.rx_mode.value}, "
                "Menü 048/070/072/077/109 wie zuvor"
            )
        except CatError as exc:
            return False, str(exc)


class RadioSetupWorker(QObject):
    """CAT-Umschaltung im Hintergrund (blockiert UI nicht)."""

    apply_finished = Signal(bool, str)
    restore_finished = Signal(bool, str)
    pc_menus_finished = Signal(bool, str)
    data_mode_finished = Signal(bool, str)
    engage_plain_finished = Signal(bool, str)
    engage_data_finished = Signal(bool, str)

    def __init__(self, setup: RadioPlaybackSetup) -> None:
        super().__init__()
        self._setup = setup

    @Slot()
    def run_apply_pc_menus(self) -> None:
        ok, msg = self._setup.apply_pc_audio_menus_only()
        self.pc_menus_finished.emit(ok, msg)

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
