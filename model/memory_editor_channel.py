"""Datenmodell fuer den Speicherkanal-Editor (Kanaele 001..100)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from mapping.memory_tones import ToneMode
from mapping.rx_mapping import RxMode

MEMORY_EDITOR_MIN: int = 1
MEMORY_EDITOR_MAX: int = 100
TAG_MAX_LEN: int = 12
DEFAULT_EMPTY_NAME: str = "NON"
DEFAULT_EMPTY_FREQ_HZ: int = 133_000_000

# Typische Ablage-Offsets (MHz) für das Dropdown in der Tabelle.
SHIFT_OFFSET_PRESETS_MHZ: tuple[str, ...] = ("0", "0.600", "7.600")

# FT-991/A CAT-Frequenzbereich (Hz), grob laut Handbuch.
FREQ_MIN_HZ: int = 30_000
FREQ_MAX_HZ: int = 540_000_000


class ShiftDirection(str, Enum):
    SIMPLEX = "Simplex"
    PLUS = "Plus"
    MINUS = "Minus"


class ChangeStatus(str, Enum):
    UNCHANGED = ""
    CHANGED = "geändert"
    NEW = "neu"
    MOVED = "verschoben"
    DELETED = "gelöscht"


# Betriebsarten in der GUI (Reihenfolge der Dropdown-Liste).
EDITOR_MODES: tuple[RxMode, ...] = (
    RxMode.FM,
    RxMode.FM_N,
    RxMode.AM,
    RxMode.AM_N,
    RxMode.USB,
    RxMode.LSB,
    RxMode.C4FM,
    RxMode.DATA_FM,
    RxMode.DATA_USB,
    RxMode.DATA_LSB,
    RxMode.CW_U,
    RxMode.CW_L,
    RxMode.RTTY_LSB,
    RxMode.RTTY_USB,
)

_EDITOR_MODE_LABELS: dict[RxMode, str] = {
    RxMode.FM: "FM",
    RxMode.FM_N: "FM-N",
    RxMode.AM: "AM",
    RxMode.AM_N: "AM-N",
    RxMode.USB: "USB",
    RxMode.LSB: "LSB",
    RxMode.C4FM: "C4FM",
    RxMode.DATA_FM: "DATA-FM",
    RxMode.DATA_USB: "DATA-USB",
    RxMode.DATA_LSB: "DATA-LSB",
    RxMode.CW_U: "CW",
    RxMode.CW_L: "CW-R",
    RxMode.RTTY_LSB: "RTTY-LSB",
    RxMode.RTTY_USB: "RTTY-USB",
}

_LABEL_TO_EDITOR_MODE: dict[str, RxMode] = {
    v: k for k, v in _EDITOR_MODE_LABELS.items()
}


def editor_mode_label(mode: RxMode) -> str:
    return _EDITOR_MODE_LABELS.get(mode, mode.value)


def editor_mode_from_label(label: str) -> RxMode:
    return _LABEL_TO_EDITOR_MODE.get(label, RxMode.FM)


_ASCII_TAG_RE = re.compile(r"^[\x20-\x7E]*$")


@dataclass
class MemoryEditorChannel:
    """Ein bearbeitbarer Speicherkanal (001..100)."""

    number: int
    enabled: bool = False
    name: str = ""
    rx_frequency_hz: int = 0
    mode: RxMode = RxMode.FM
    shift_direction: ShiftDirection = ShiftDirection.SIMPLEX
    shift_offset_hz: int = 600_000
    tone_mode: ToneMode = ToneMode.OFF
    ctcss_tone_hz: float = 88.5
    dcs_code: int = 23
    raw_cat_response: str = ""
    raw_mt_body: str = ""
    changed: bool = False
    moved: bool = False
    local_note: str = ""

    @property
    def is_placeholder_empty(self) -> bool:
        return (
            self.rx_frequency_hz == DEFAULT_EMPTY_FREQ_HZ
            and self.name.strip().upper() == DEFAULT_EMPTY_NAME
        )

    @property
    def is_empty(self) -> bool:
        return (
            (self.rx_frequency_hz == 0 and not self.name.strip())
            or self.is_placeholder_empty
        )

    @property
    def change_status(self) -> ChangeStatus:
        if self.is_empty and self.changed:
            return ChangeStatus.DELETED
        if self.moved:
            return ChangeStatus.MOVED
        if self.changed:
            return ChangeStatus.CHANGED
        return ChangeStatus.UNCHANGED

    @property
    def rx_frequency_mhz(self) -> float:
        return self.rx_frequency_hz / 1_000_000.0

    @rx_frequency_mhz.setter
    def rx_frequency_mhz(self, mhz: float) -> None:
        self.rx_frequency_hz = int(round(mhz * 1_000_000))

    @property
    def shift_offset_mhz(self) -> float:
        return self.shift_offset_hz / 1_000_000.0

    @shift_offset_mhz.setter
    def shift_offset_mhz(self, mhz: float) -> None:
        self.shift_offset_hz = int(round(mhz * 1_000_000))

    def mark_changed(self) -> None:
        self.changed = True

    def sanitize_name(self) -> str:
        """ASCII-Tag auf 12 Zeichen begrenzen."""
        name = self.name.encode("ascii", errors="ignore").decode("ascii")
        if len(name) > TAG_MAX_LEN:
            name = name[:TAG_MAX_LEN]
        self.name = name
        return name

    def validate_name(self) -> Optional[str]:
        if not _ASCII_TAG_RE.match(self.name):
            return "Nur ASCII-Zeichen im Namen erlaubt."
        if len(self.name) > TAG_MAX_LEN:
            return f"Name darf maximal {TAG_MAX_LEN} Zeichen haben."
        return None

    def validate_frequency(self) -> Optional[str]:
        if not self.enabled:
            return None
        if self.rx_frequency_hz == 0:
            return None
        if self.rx_frequency_hz < FREQ_MIN_HZ or self.rx_frequency_hz > FREQ_MAX_HZ:
            return (
                f"Frequenz ausserhalb {FREQ_MIN_HZ/1e6:.3f}.."
                f"{FREQ_MAX_HZ/1e6:.3f} MHz."
            )
        return None

    def suggest_shift_offset_hz(self) -> int:
        """Typische Ablage: 2 m 600 kHz, 70 cm 7,6 MHz."""
        mhz = self.rx_frequency_mhz
        if 144.0 <= mhz <= 146.0:
            return 600_000
        if 430.0 <= mhz <= 440.0:
            return 7_600_000
        return self.shift_offset_hz

    def detect_band_label(self) -> str:
        mhz = self.rx_frequency_mhz
        if self.is_empty:
            return "leer"
        if 144.0 <= mhz <= 148.0:
            return "2m"
        if 430.0 <= mhz <= 440.0:
            return "70cm"
        if mhz < 30.0:
            return "HF"
        return "sonst"

    def looks_like_repeater(self) -> bool:
        tag = self.name.upper()
        if any(k in tag for k in ("RPT", "REL", "DB0", "SR", "RV")):
            return True
        return self.shift_direction != ShiftDirection.SIMPLEX

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "enabled": self.enabled,
            "name": self.name,
            "rx_frequency_hz": self.rx_frequency_hz,
            "mode": self.mode.value,
            "shift_direction": self.shift_direction.value,
            "shift_offset_hz": self.shift_offset_hz,
            "tone_mode": self.tone_mode.value,
            "ctcss_tone_hz": self.ctcss_tone_hz,
            "dcs_code": self.dcs_code,
            "raw_cat_response": self.raw_cat_response,
            "raw_mt_body": self.raw_mt_body,
            "local_note": self.local_note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEditorChannel":
        mode_raw = data.get("mode", RxMode.FM.value)
        try:
            mode = editor_mode_from_label(str(mode_raw))
        except Exception:
            mode = RxMode(str(mode_raw)) if str(mode_raw) in RxMode._value2member_map_ else RxMode.FM  # noqa: SLF001
        shift = ShiftDirection(
            data.get("shift_direction", ShiftDirection.SIMPLEX.value)
        )
        tone = ToneMode(data.get("tone_mode", ToneMode.OFF.value))
        ch = cls(
            number=int(data["number"]),
            enabled=bool(data.get("enabled", False)),
            name=str(data.get("name", "")),
            rx_frequency_hz=int(data.get("rx_frequency_hz", 0)),
            mode=mode,
            shift_direction=shift,
            shift_offset_hz=int(data.get("shift_offset_hz", 600_000)),
            tone_mode=tone,
            ctcss_tone_hz=float(data.get("ctcss_tone_hz", 88.5)),
            dcs_code=int(data.get("dcs_code", 23)),
            raw_cat_response=str(data.get("raw_cat_response", "")),
            raw_mt_body=str(data.get("raw_mt_body", "")),
            local_note=str(data.get("local_note", "")),
        )
        return ch

    @classmethod
    def empty_slot(cls, number: int) -> "MemoryEditorChannel":
        return cls(
            number=number,
            enabled=False,
            rx_frequency_hz=DEFAULT_EMPTY_FREQ_HZ,
            name=DEFAULT_EMPTY_NAME,
            shift_direction=ShiftDirection.SIMPLEX,
            shift_offset_hz=0,
            tone_mode=ToneMode.OFF,
        )


@dataclass
class MemoryChannelBank:
    """100 Speicherplaetze mit Bearbeitungslogik."""

    channels: list[MemoryEditorChannel] = field(default_factory=list)
    layout_changed: bool = False

    def __post_init__(self) -> None:
        if not self.channels:
            self.channels = [
                MemoryEditorChannel.empty_slot(n)
                for n in range(MEMORY_EDITOR_MIN, MEMORY_EDITOR_MAX + 1)
            ]

    def renumber(self) -> None:
        for i, ch in enumerate(self.channels, start=MEMORY_EDITOR_MIN):
            ch.number = i

    def any_layout_change(self) -> bool:
        return self.layout_changed or any(ch.moved for ch in self.channels)

    def changed_channels(self) -> list[MemoryEditorChannel]:
        return [ch for ch in self.channels if ch.changed or ch.moved]

    def last_filled_row_index(self) -> int:
        """Letzte Tabellenzeile mit belegtem Kanal, sonst -1."""
        for i in range(len(self.channels) - 1, -1, -1):
            if not self.channels[i].is_empty:
                return i
        return -1

    def channels_for_radio_write(self) -> list[MemoryEditorChannel]:
        """Kanäle, die beim Speichern ans Gerät müssen.

        - Layout geändert / Kanäle verschoben: alle 100 Plätze
        - sonst: nur vom Benutzer geänderte Kanäle (inkl. bewusst geleerter)
        """
        if self.layout_changed or any(ch.moved for ch in self.channels):
            return list(self.channels)

        return sorted(self.changed_channels(), key=lambda c: c.number)

    def move_up(self, row: int) -> None:
        if row <= 0:
            return
        self.channels[row], self.channels[row - 1] = (
            self.channels[row - 1],
            self.channels[row],
        )
        self.channels[row].moved = True
        self.channels[row - 1].moved = True
        self.layout_changed = True
        self.renumber()

    def move_down(self, row: int) -> None:
        if row >= len(self.channels) - 1:
            return
        self.channels[row], self.channels[row + 1] = (
            self.channels[row + 1],
            self.channels[row],
        )
        self.channels[row].moved = True
        self.channels[row + 1].moved = True
        self.layout_changed = True
        self.renumber()

    def insert_at(self, row: int) -> None:
        """Fuegt einen leeren Kanal ein; letzter Slot faellt weg."""
        new_ch = MemoryEditorChannel.empty_slot(0)
        new_ch.enabled = True
        new_ch.changed = True
        self.channels.insert(row, new_ch)
        if len(self.channels) > MEMORY_EDITOR_MAX:
            self.channels = self.channels[:MEMORY_EDITOR_MAX]
        self.layout_changed = True
        self.renumber()

    def clear_at(self, row: int) -> None:
        ch = self.channels[row]
        # Wunschverhalten: nicht löschen, sondern auf Standardwerte setzen.
        ch.enabled = True
        ch.name = DEFAULT_EMPTY_NAME
        ch.rx_frequency_hz = DEFAULT_EMPTY_FREQ_HZ
        ch.mode = RxMode.FM
        ch.shift_direction = ShiftDirection.SIMPLEX
        ch.shift_offset_hz = 0
        ch.tone_mode = ToneMode.OFF
        ch.raw_mt_body = ""
        ch.raw_cat_response = ""
        ch.changed = True
        self.layout_changed = True

    def duplicate_at(self, row: int) -> None:
        if row >= len(self.channels) - 1:
            return
        import copy

        dup = copy.deepcopy(self.channels[row])
        dup.changed = True
        dup.moved = True
        self.channels.insert(row + 1, dup)
        self.channels = self.channels[:MEMORY_EDITOR_MAX]
        self.layout_changed = True
        self.renumber()

    def close_gaps(self) -> None:
        filled = [c for c in self.channels if not c.is_empty]
        empty = [
            MemoryEditorChannel.empty_slot(0)
            for _ in range(MEMORY_EDITOR_MAX - len(filled))
        ]
        for ch in empty:
            ch.changed = True
        self.channels = filled + empty
        self.layout_changed = True
        self.renumber()

    def empty_slot_count(self) -> int:
        return sum(1 for ch in self.channels if ch.is_empty)

    @staticmethod
    def count_nonempty_imported(
        imported: list[MemoryEditorChannel],
    ) -> int:
        return sum(1 for ch in imported if not ch.is_empty)

    def append_imported(self, imported: list[MemoryEditorChannel]) -> tuple[int, int]:
        """Belegte Import-Kanäle in freie Slots einfügen.

        Returns:
            (angehängt, übersprungen wegen voller Liste)
        """
        import copy

        to_add = [copy.deepcopy(ch) for ch in imported if not ch.is_empty]
        if not to_add:
            return 0, 0

        empty_rows = [i for i, ch in enumerate(self.channels) if ch.is_empty]
        appended = 0
        skipped = 0
        for ch in to_add:
            if not empty_rows:
                skipped += 1
                continue
            row = empty_rows.pop(0)
            ch.changed = True
            ch.moved = True
            self.channels[row] = ch
            appended += 1

        if appended:
            self.layout_changed = True
            self.renumber()
        return appended, skipped

    def duplicate_frequency_hz(self) -> set[int]:
        """Frequenzen (Hz), die in mehr als einem belegten Kanal vorkommen."""
        counts: dict[int, int] = {}
        for ch in self.channels:
            if ch.is_empty or ch.rx_frequency_hz <= 0:
                continue
            hz = ch.rx_frequency_hz
            counts[hz] = counts.get(hz, 0) + 1
        return {hz for hz, count in counts.items() if count > 1}

    def duplicate_frequencies(self) -> list[tuple[int, int]]:
        seen: dict[int, int] = {}
        dups: list[tuple[int, int]] = []
        for ch in self.channels:
            if ch.is_empty or ch.rx_frequency_hz <= 0:
                continue
            if ch.rx_frequency_hz in seen:
                dups.append((seen[ch.rx_frequency_hz], ch.number))
            else:
                seen[ch.rx_frequency_hz] = ch.number
        return dups
