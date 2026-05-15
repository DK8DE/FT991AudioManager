"""MT-Codec für den Speicherkanal-Editor — lesen/schreiben mit Rohdaten-Erhalt.

FT-991/A MT-Body (38 Zeichen, beobachtet + CAT Manual 1711-D)::

    P1  (3)   Kanal
    P2  (9)   RX-Frequenz Hz
    P3  (1)   ``+`` / ``-`` (Ablage-Richtung *und* Clarifier auf dem Gerät)
    P4  (4)   Ablage-Offset in **kHz** (z. B. ``0600`` = 600 kHz) — nicht Hz!
    P5  (1)   RX-Clarifier
    P6  (1)   TX-Clarifier
    P7  (1)   Mode
    P8  (1)   CTCSS/DCS
    P9  (2)   Position 21–22, meist ``00``
    P10 (1)   **Position 24** (nicht 23): 0=Simplex, 1=Plus, 2=Minus
    P11 (2)   Position 25, meist ``0``
    P12 (12)  Tag

Wichtig: **P3** ist nur Clarifier (+/−), nicht die Ablage-Richtung.
Die Ziffern 21–25 (z. B. ``00020``) sind **kein** Offset — bei P4=``0000``
gilt der Band-Standard: 2 m → 600 kHz, 70 cm → 7600 kHz (jeweils Minus).
"""

from __future__ import annotations

from typing import Optional

from mapping.memory_tones import ToneMode, tone_mode_from_p8, tone_mode_to_p8
from mapping.rx_mapping import MODE_TO_CODE, RxMode, _MD_CODE_TO_MODE
from model.memory_editor_channel import (
    DEFAULT_EMPTY_FREQ_HZ,
    DEFAULT_EMPTY_NAME,
    MEMORY_EDITOR_MAX,
    MEMORY_EDITOR_MIN,
    MemoryEditorChannel,
    ShiftDirection,
)

MT_BODY_LEN: int = 38
TAG_LEN: int = 12

POS_CHANNEL = slice(0, 3)
POS_FREQ = slice(3, 12)
POS_CLAR_SIGN = 12
POS_CLAR_OFFSET_KHZ = slice(13, 17)
POS_RX_CLAR = 17
POS_TX_CLAR = 18
POS_MODE = 19
POS_TONE_MODE = 20
POS_FIXED_P9 = slice(21, 23)
# P10 sitzt auf dem FT-991/A faktisch an Index 24 (Index 23 ist meist 0).
POS_SHIFT_DIR = 24
POS_FIXED_P11 = 25
POS_TAG = slice(26, 38)


def empty_mt_body(channel: int) -> str:
    """Leerer Speicherplatz — exakt 38 Zeichen (wie am FT-991/A für leere Slots).

    Beobachtetes Format: ``nnn000000000+0000000000000`` + 12 Leerzeichen Tag.
    Wichtig: **kein** Mode ``4`` (FM) in der Mitte — sonst bleibt der Kanal
    am Gerät belegt.
    """
    ch = f"{channel:03d}"
    return f"{ch}{'0' * 9}+{'0' * 13}{' ' * TAG_LEN}"


def empty_mw_command(channel: int) -> str:
    """Leerer Speicherplatz als ``MW``-Befehl (ohne Tag-Feld)."""
    return f"MW{channel:03d}{'0' * 9}+{'0' * 13};"


def should_write_cleared(channel: MemoryEditorChannel) -> bool:
    """Kanal soll als gelöscht/leer ins Funkgerät (keine alten Rohdaten)."""
    if channel.rx_frequency_hz <= 0 and not channel.name.strip():
        return True
    return False


def normalize_channel_for_write(channel: MemoryEditorChannel) -> None:
    """Modelldaten für einen leeren Speicherplatz zurücksetzen."""
    if not should_write_cleared(channel):
        return
    channel.enabled = False
    channel.rx_frequency_hz = 0
    channel.name = ""
    channel.shift_direction = ShiftDirection.SIMPLEX
    channel.shift_offset_hz = 0
    channel.tone_mode = ToneMode.OFF
    channel.raw_mt_body = ""
    channel.raw_cat_response = ""


def _shift_from_p10(body: str) -> ShiftDirection:
    if len(body) <= POS_SHIFT_DIR:
        return ShiftDirection.SIMPLEX
    code = body[POS_SHIFT_DIR]
    if code == "1":
        return ShiftDirection.PLUS
    if code == "2":
        return ShiftDirection.MINUS
    return ShiftDirection.SIMPLEX


def _shift_from_body(body: str) -> ShiftDirection:
    """Ablage-Richtung nur aus P10 (Index 24). P3 ist Clarifier, nicht RPT."""
    return _shift_from_p10(body)


def _shift_to_p10(direction: ShiftDirection) -> str:
    if direction == ShiftDirection.PLUS:
        return "1"
    if direction == ShiftDirection.MINUS:
        return "2"
    return "0"


def _parse_offset_khz(body: str) -> int:
    """Ablage-Offset in kHz aus P4 (4 Ziffern). Werte < 100 ignorieren (Artefakt)."""
    if len(body) >= 17:
        p4 = body[POS_CLAR_OFFSET_KHZ]
        if p4.isdigit():
            value = int(p4)
            # 00020 als Ganzes ist kein Offset; echte Werte: 0600, 7600, …
            if value >= 100:
                return value
    return 0


def _mode_from_body(body: str) -> RxMode:
    if len(body) <= POS_MODE:
        return RxMode.FM
    code = body[POS_MODE].upper()
    return _MD_CODE_TO_MODE.get(code, RxMode.UNKNOWN)


def editor_channel_from_mt_response(
    response: str,
    *,
    requested_channel: int,
) -> MemoryEditorChannel:
    """Parst ``MT…;`` in ein :class:`MemoryEditorChannel`."""
    if not response.startswith("MT") or not response.endswith(";"):
        raise ValueError(f"MT-Antwort ungültig: {response!r}")
    body = response[2:-1]
    if len(body) < MT_BODY_LEN:
        raise ValueError(f"MT-Body zu kurz: {response!r}")

    freq_str = body[POS_FREQ]
    frequency_hz = int(freq_str) if freq_str.isdigit() else 0
    tag = body[POS_TAG].rstrip()
    enabled = frequency_hz > 0 or bool(tag)

    if frequency_hz == 0 and not tag:
        return MemoryEditorChannel(
            number=requested_channel,
            enabled=False,
            name=DEFAULT_EMPTY_NAME,
            rx_frequency_hz=DEFAULT_EMPTY_FREQ_HZ,
            mode=RxMode.FM,
            shift_direction=ShiftDirection.SIMPLEX,
            shift_offset_hz=0,
            tone_mode=tone_mode_from_p8(
                body[POS_TONE_MODE] if len(body) > POS_TONE_MODE else "0"
            ),
            raw_cat_response=response,
            raw_mt_body=empty_mt_body(requested_channel),
        )

    shift_direction = _shift_from_body(body)
    offset_hz = _parse_offset_khz(body) * 1000

    ch = MemoryEditorChannel(
        number=requested_channel,
        enabled=enabled,
        name=tag,
        rx_frequency_hz=frequency_hz,
        mode=_mode_from_body(body),
        shift_direction=shift_direction,
        shift_offset_hz=offset_hz,
        tone_mode=tone_mode_from_p8(
            body[POS_TONE_MODE] if len(body) > POS_TONE_MODE else "0"
        ),
        ctcss_tone_hz=88.5,
        dcs_code=23,
        raw_cat_response=response,
        raw_mt_body=body[:MT_BODY_LEN].ljust(MT_BODY_LEN)[:MT_BODY_LEN],
    )
    # P4 oft 0000 bei Relais → Band-Default (600 kHz / 7,6 MHz)
    if shift_direction != ShiftDirection.SIMPLEX and ch.shift_offset_hz == 0:
        ch.shift_offset_hz = ch.suggest_shift_offset_hz()
    return ch


def build_mt_command(channel: MemoryEditorChannel) -> str:
    """Baut ``MT…;`` (Set) — Ablage in P4 (kHz) und P10 (Index 24)."""
    normalize_channel_for_write(channel)
    if should_write_cleared(channel):
        return f"MT{empty_mt_body(channel.number)};"
    if channel.raw_mt_body and len(channel.raw_mt_body) >= MT_BODY_LEN:
        body = list(channel.raw_mt_body[:MT_BODY_LEN])
    else:
        body = list(empty_mt_body(channel.number))

    body[POS_CHANNEL.start:POS_CHANNEL.stop] = list(f"{channel.number:03d}")
    body[POS_FREQ.start:POS_FREQ.stop] = list(
        f"{max(0, channel.rx_frequency_hz):09d}"
    )
    body[POS_MODE] = MODE_TO_CODE.get(channel.mode, "4")
    body[POS_TONE_MODE] = tone_mode_to_p8(channel.tone_mode)

    khz = max(0, min(9999, channel.shift_offset_hz // 1000))
    if not channel.raw_mt_body:
        body[POS_CLAR_SIGN] = "+"
    body[POS_CLAR_OFFSET_KHZ] = list(f"{khz:04d}")
    body[POS_FIXED_P9] = list("00")
    if len(body) > POS_SHIFT_DIR:
        body[POS_SHIFT_DIR] = _shift_to_p10(channel.shift_direction)
    if len(body) > POS_FIXED_P11:
        body[POS_FIXED_P11] = "0"

    tag = channel.name.encode("ascii", errors="ignore").decode("ascii")
    tag = tag[:TAG_LEN].ljust(TAG_LEN)
    body[POS_TAG.start:POS_TAG.stop] = list(tag)

    return f"MT{''.join(body)};"


def build_mw_command(channel: MemoryEditorChannel) -> str:
    """Baut ``MW…;`` (Memory Channel Write, ohne Tag-Feld)."""
    normalize_channel_for_write(channel)
    if should_write_cleared(channel):
        return empty_mw_command(channel.number)

    khz = max(0, min(9999, channel.shift_offset_hz // 1000))
    sign = "+"
    mode = MODE_TO_CODE.get(channel.mode, "4")
    tone = tone_mode_to_p8(channel.tone_mode)
    shift = _shift_to_p10(channel.shift_direction)
    return (
        f"MW{channel.number:03d}{max(0, channel.rx_frequency_hz):09d}"
        f"{sign}{khz:04d}00{mode}00{tone}00{shift};"
    )


def parse_mt_or_empty_editor(
    response: str,
    *,
    requested_channel: int,
) -> Optional[MemoryEditorChannel]:
    ch = editor_channel_from_mt_response(
        response, requested_channel=requested_channel
    )
    if not ch.enabled:
        return None
    return ch


def validate_channel_range(channel: int) -> None:
    if not (MEMORY_EDITOR_MIN <= channel <= MEMORY_EDITOR_MAX):
        raise ValueError(
            f"Kanal {channel} außerhalb "
            f"{MEMORY_EDITOR_MIN}..{MEMORY_EDITOR_MAX}"
        )
