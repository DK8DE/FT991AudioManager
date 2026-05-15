"""Mapping fuer Speicherkanal-Befehle (``MT``, ``MC``, ``VM``).

Der FT-991/991A hat 117 CAT-adressierbare Speicherkanaele:

* **001-099**: regulaere Memorys
* **100-117**: PMS- und Spezial-Slots

Wir lesen die Inhalte mit ``MTnnn;`` (= Memory Tag — liefert zusaetzlich
den 12-stelligen Tag/Namen) und wechseln zur Auswahl mit ``MCnnn;``.
Yaesu liefert fuer leere Slots entweder ``?;`` zurueck oder eine
Antwort mit Frequenz ``0`` und leerem Tag — beides erkennen wir hier
als „leer".

Format der MT-Antwort (FT-991/A, beobachtet)::

    MT <P1:3> <P2:9> <P3:1> <P4:4> <P5:1> <P6:1> <P7:1> <P8:1>
       <P9:5> <P10:12> ;

* P1 — Channel (3 Ziffern)
* P2 — Frequenz in Hz (9 Ziffern)
* P3 — Clarifier-Richtung (``+``/``-``)
* P4 — Clarifier-Offset (4 Ziffern)
* P5 — RX-Clarifier (0/1)
* P6 — TX-Clarifier (0/1)
* P7 — **Mode** (1 Hex-Digit, ``1``=LSB … ``E``=C4FM)
* P8 — CTCSS/Encode-Mode (1 Ziffer)
* P9 — Repeater-Offset / Reserve (5 Ziffern)
* P10 — Tag (12 ASCII-Zeichen, leerzeichen-gepaddet)

Body-Laenge (zwischen ``MT`` und ``;``) ist damit 38 Zeichen. Das Mode-
Feld P7 sitzt bei Body-Position 19 (0-indexiert: 3 + 9 + 5 + 1 + 1 = 19).

Wir lesen tolerant: solange der String mit ``MT`` beginnt, die ersten 3
Zeichen Channel-Ziffern und die naechsten 9 Frequenz-Ziffern sind, sowie
am Ende vor dem ``;`` mindestens 12 Zeichen Tag stehen, akzeptieren wir
die Antwort. Das Mode-Feld lesen wir an Position 19 (Standard-Format)
und fallen sonst auf ``UNKNOWN`` zurueck.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .rx_mapping import RxMode, _MD_CODE_TO_MODE


MEMORY_CHANNEL_MIN: int = 1
MEMORY_CHANNEL_MAX: int = 117
TAG_LENGTH: int = 12


@dataclass(frozen=True)
class MemoryChannel:
    """Ein gelesener Speicherkanal-Inhalt.

    ``tag`` ist bereits trailing-trimmt; leere Slots erkennt der Aufrufer
    daran, dass ``frequency_hz == 0`` und ``tag == ""``.
    """

    channel: int
    frequency_hz: int
    mode: RxMode
    tag: str

    @property
    def is_empty(self) -> bool:
        """``True`` wenn der Slot weder Frequenz noch Tag hat."""
        return self.frequency_hz == 0 and not self.tag


def _channel_to_payload(channel: int) -> str:
    if not (MEMORY_CHANNEL_MIN <= channel <= MEMORY_CHANNEL_MAX):
        raise ValueError(
            f"Memory-Kanal {channel} ausserhalb {MEMORY_CHANNEL_MIN}..{MEMORY_CHANNEL_MAX}"
        )
    return f"{channel:03d}"


# ----------------------------------------------------------------------
# MT (Memory Tag) — Read mit Tag/Name
# ----------------------------------------------------------------------

def format_mt_query(channel: int) -> str:
    """``MTnnn;`` — fordere Inhalt + Tag eines Speicherkanals an."""
    return f"MT{_channel_to_payload(channel)};"


#: Body-Position des Mode-Felds (P7) im FT-991/A-Format:
#: 3 (Channel) + 9 (Frequenz) + 1 (Clarifier-Sign) + 4 (Clarifier-Offset)
#: + 1 (RX-CLAR) + 1 (TX-CLAR) = 19.
_MT_MODE_POS: int = 19


def parse_mt_response(response: str) -> MemoryChannel:
    """Parsen einer ``MT…;``-Antwort.

    Tolerant gegenueber unbekannten Mittel-Feldern: wir nehmen die
    ersten drei Ziffern nach ``MT`` als Kanal, die naechsten 9 als
    Frequenz und die letzten 12 Zeichen vor ``;`` als Tag. Das Mode-Feld
    lesen wir an der festen Position 19 (FT-991/A-Format); ist sie
    unbekannt, suchen wir als Fallback in der Mid-Section vorwaerts
    nach dem ersten gueltigen Hex-Digit nach dem 5-stelligen Clarifier-
    Block.

    Wirft :class:`ValueError`, wenn die Antwort nicht parse-bar ist.
    """
    if not response.startswith("MT") or not response.endswith(";"):
        raise ValueError(f"MT-Antwort hat falsches Format: {response!r}")
    body = response[2:-1]
    # Minimal: 3 (channel) + 9 (frequency) + 0 (flexible mid) + 12 (tag)
    if len(body) < 3 + 9 + 12:
        raise ValueError(
            f"MT-Antwort zu kurz ({len(body) + 3} Zeichen): {response!r}"
        )
    channel_str = body[0:3]
    freq_str = body[3:12]
    if not channel_str.isdigit() or not freq_str.isdigit():
        raise ValueError(
            f"MT-Antwort enthaelt nicht-numerische Felder: {response!r}"
        )
    channel = int(channel_str)
    frequency_hz = int(freq_str)
    tag = body[-TAG_LENGTH:].rstrip()
    # Mode-Code zuerst an der festen FT-991/A-Position lesen.
    mode = RxMode.UNKNOWN
    if len(body) > _MT_MODE_POS + TAG_LENGTH:
        candidate = body[_MT_MODE_POS].upper()
        if candidate in _MD_CODE_TO_MODE:
            mode = _MD_CODE_TO_MODE[candidate]
    # Fallback: vorwaerts in der Mid-Section nach dem ersten validen
    # Hex-Digit nach dem 5-stelligen Clarifier-Block suchen.
    if mode is RxMode.UNKNOWN:
        mid_section = body[12:-TAG_LENGTH]
        # Erstes Zeichen ist Clarifier-Sign (+/-), gefolgt von 4 Ziffern.
        # Danach koennen Mode-Codes auftauchen.
        for ch in mid_section[5:]:
            if ch.upper() in _MD_CODE_TO_MODE:
                mode = _MD_CODE_TO_MODE[ch.upper()]
                break
    return MemoryChannel(
        channel=channel,
        frequency_hz=frequency_hz,
        mode=mode,
        tag=tag,
    )


# ----------------------------------------------------------------------
# MC (Memory Channel) — Aktiven Kanal setzen / lesen
# ----------------------------------------------------------------------

def format_mc_set(channel: int) -> str:
    """``MCnnn;`` — auf Speicherkanal umschalten (aktiviert Memory-Modus)."""
    return f"MC{_channel_to_payload(channel)};"


def format_mc_query() -> str:
    """``MC;`` — fragt den aktuell aktiven Speicherkanal ab.

    Im VFO-Modus antwortet das Funkgeraet mit ``?;``; das uebersetzen die
    hoeheren Schichten in :class:`CatCommandUnsupportedError`.
    """
    return "MC;"


def parse_mc_response(response: str) -> int:
    """``MCnnn;`` -> Kanalnummer als ``int``."""
    if (not response.startswith("MC")
            or not response.endswith(";")
            or len(response) != 6):
        raise ValueError(f"MC-Antwort hat falsches Format: {response!r}")
    payload = response[2:-1]
    if not payload.isdigit():
        raise ValueError(f"MC-Antwort enthaelt nicht-numerische Kanalnummer: {response!r}")
    return int(payload)


# ----------------------------------------------------------------------
# VM (VFO/Memory Mode Switch) — explizit zwischen VFO und MEM wechseln
# ----------------------------------------------------------------------

def format_vm_set(memory_mode: bool) -> str:
    """``VM0;`` (VFO) / ``VM1;`` (MEM).

    Nicht jedes Funkgeraet versteht ``VM`` — die Aufrufschicht muss eine
    ``CatCommandUnsupportedError`` auffangen koennen.
    """
    return "VM1;" if memory_mode else "VM0;"


def parse_mt_or_empty(response: str) -> Optional[MemoryChannel]:
    """Convenience: wie :func:`parse_mt_response`, gibt aber ``None``
    fuer Slots ohne Frequenz und Tag zurueck."""
    channel = parse_mt_response(response)
    if channel.is_empty:
        return None
    return channel
