"""MT-Codec für den Speicherkanal-Editor — lesen/schreiben mit Rohdaten-Erhalt.

FT-991/A MT-Body (38 Zeichen, Hamlib ``newcat_get_channel`` / CAT Manual)::

    P1  (3)   Kanal
    P2  (9)   RX-Frequenz Hz
    P3  (1)   ``+`` / ``-`` (Clarifier-Richtung)
    P4  (4)   Ablage-Offset in **kHz** (z. B. ``0600`` = 600 kHz)
    P5  (1)   RX-Clarifier
    P6  (1)   TX-Clarifier
    P7  (1)   Mode (Hex) — Body-Index 19
    P7b (1)   Reserve ``0`` — Body-Index 20 (MW/MT, vor Tonfeldern)
    P8  (1)   Tonmodus — Body-Index **21**
    P9  (2)   Tonnummer 00..49 — Body-Index **22..23**
    P10 (1)   Reserve ``0`` — Body-Index 24
    P11 (1)   Ablage 0/1/2 — Body-Index **25**
    P12 (12)  Tag

``MW`` nutzt dieselbe Mittelstruktur P3..P11 (14 Zeichen ab ``+``).
Zusätzlich setzt Hamlib vor dem Schreiben ``CN`` + ``CT`` (siehe ``memory_tones``).
"""

from __future__ import annotations

from typing import List, Optional

from mapping.memory_tones import (
    ToneMode,
    ctcss_cat_tone_number,
    ctcss_hz_from_cat_tone_number,
    tone_mode_from_p8,
    tone_mode_to_p8,
)
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
MW_MID_LEN: int = 14  # body[12:26] — von ``+`` bis Ende P11
POS_MW_MID = slice(12, 26)

POS_CHANNEL = slice(0, 3)
POS_FREQ = slice(3, 12)
POS_CLAR_SIGN = 12
POS_CLAR_OFFSET_KHZ = slice(13, 17)
POS_RX_CLAR = 17
POS_TX_CLAR = 18
POS_MODE = 19
POS_PRE_TONE_FILLER = 20
POS_TONE_MODE = 21
POS_TONE_INDEX = slice(22, 24)
POS_MID_FILLER = 24
POS_SHIFT_DIR = 25
POS_TAG = slice(26, 38)

# Sonderzeichen in MT-Antworten (z. B. Relais mit DCS)
_P8_EXTRA: dict[str, ToneMode] = {
    "B": ToneMode.DCS_ENC_DEC,
}


def empty_mt_body(channel: int) -> str:
    """Leerer Speicherplatz — exakt 38 Zeichen (wie am FT-991/A für leere Slots."""
    ch = f"{channel:03d}"
    return f"{ch}{'0' * 9}+{'0' * 13}{' ' * TAG_LEN}"


def empty_mw_command(channel: int) -> str:
    """Leerer Speicherplatz als ``MW``-Befehl (ohne Tag-Feld)."""
    body = empty_mt_body(channel)
    return f"MW{body[0:12]}{body[POS_MW_MID]};"


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
    """Ablage-Richtung — am Gerät Index 24 oder 25 (je nach Mittelteil)."""
    for idx in (POS_SHIFT_DIR, 24):
        if len(body) > idx:
            code = body[idx]
            if code == "1":
                return ShiftDirection.PLUS
            if code == "2":
                return ShiftDirection.MINUS
    return ShiftDirection.SIMPLEX


def _shift_to_p10(direction: ShiftDirection) -> str:
    if direction == ShiftDirection.PLUS:
        return "1"
    if direction == ShiftDirection.MINUS:
        return "2"
    return "0"


def _tone_mode_from_body(body: str) -> ToneMode:
    if len(body) <= POS_TONE_MODE:
        return ToneMode.OFF
    ch = body[POS_TONE_MODE]
    if ch in _P8_EXTRA:
        return _P8_EXTRA[ch]
    return tone_mode_from_p8(ch)


def _encode_tone_fields(channel: MemoryEditorChannel, body: List[str]) -> None:
    """P8 = Tonmodus; P9 = ``00`` (fest, Yaesu Manual — Frequenz nur per ``CN``)."""
    body[POS_TONE_INDEX] = list("00")
    if channel.tone_mode == ToneMode.OFF:
        body[POS_TONE_MODE] = "0"
        return
    body[POS_TONE_MODE] = tone_mode_to_p8(channel.tone_mode)


def _apply_tone_from_body(ch: MemoryEditorChannel, body: str) -> None:
    """Nur P8 aus MT — Tonfrequenz steht nicht zuverlässig in P9 (meist ``00``)."""
    if len(body) > POS_TONE_MODE:
        ch.tone_mode = _tone_mode_from_body(body)


def _parse_offset_khz(body: str) -> int:
    """Ablage-Offset in kHz aus P4 (4 Ziffern). Werte < 100 ignorieren (Artefakt)."""
    if len(body) >= 17:
        p4 = body[POS_CLAR_OFFSET_KHZ]
        if p4.isdigit():
            value = int(p4)
            if value >= 100:
                return value
    return 0


def _mode_from_body(body: str) -> RxMode:
    if len(body) <= POS_MODE:
        return RxMode.FM
    code = body[POS_MODE].upper()
    return _MD_CODE_TO_MODE.get(code, RxMode.UNKNOWN)


def _build_mt_body_list(channel: MemoryEditorChannel) -> List[str]:
    """Erzeugt den 38-stelligen MT-Body (Liste einzelner Zeichen)."""
    if channel.raw_mt_body and len(channel.raw_mt_body) >= MT_BODY_LEN:
        body = list(channel.raw_mt_body[:MT_BODY_LEN])
    else:
        body = list(empty_mt_body(channel.number))

    body[POS_CHANNEL.start:POS_CHANNEL.stop] = list(f"{channel.number:03d}")
    body[POS_FREQ.start:POS_FREQ.stop] = list(
        f"{max(0, channel.rx_frequency_hz):09d}"
    )
    body[POS_MODE] = MODE_TO_CODE.get(channel.mode, "4")
    body[POS_RX_CLAR] = "0"
    body[POS_TX_CLAR] = "0"
    body[POS_PRE_TONE_FILLER] = "0"

    khz = max(0, min(9999, channel.shift_offset_hz // 1000))
    if not channel.raw_mt_body:
        body[POS_CLAR_SIGN] = "+"
    body[POS_CLAR_OFFSET_KHZ] = list(f"{khz:04d}")
    _encode_tone_fields(channel, body)
    body[POS_MID_FILLER] = "0"
    body[POS_SHIFT_DIR] = _shift_to_p10(channel.shift_direction)

    return body


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
            tone_mode=_tone_mode_from_body(body),
            raw_cat_response=response,
            raw_mt_body=empty_mt_body(requested_channel),
        )

    shift_direction = _shift_from_p10(body)
    offset_hz = _parse_offset_khz(body) * 1000
    tone_mode = _tone_mode_from_body(body)

    ch = MemoryEditorChannel(
        number=requested_channel,
        enabled=enabled,
        name=tag,
        rx_frequency_hz=frequency_hz,
        mode=_mode_from_body(body),
        shift_direction=shift_direction,
        shift_offset_hz=offset_hz,
        tone_mode=tone_mode,
        ctcss_tone_hz=88.5,
        dcs_code=23,
        raw_cat_response=response,
        raw_mt_body=body[:MT_BODY_LEN].ljust(MT_BODY_LEN)[:MT_BODY_LEN],
    )
    _apply_tone_from_body(ch, body)
    if shift_direction != ShiftDirection.SIMPLEX and ch.shift_offset_hz == 0:
        ch.shift_offset_hz = ch.suggest_shift_offset_hz()
    return ch


def build_mt_command(channel: MemoryEditorChannel) -> str:
    """Baut ``MT…;`` inkl. P8/P9 (Tonmodus + 2-stellige Tonnummer)."""
    normalize_channel_for_write(channel)
    if should_write_cleared(channel):
        return f"MT{empty_mt_body(channel.number)};"

    body = _build_mt_body_list(channel)
    tag = channel.name.encode("ascii", errors="ignore").decode("ascii")
    tag = tag[:TAG_LEN].ljust(TAG_LEN)
    body[POS_TAG.start:POS_TAG.stop] = list(tag)

    return f"MT{''.join(body)};"


def build_mw_command(channel: MemoryEditorChannel) -> str:
    """Baut ``MW…;`` — gleiche Mittelfelder wie ``MT`` (P3..P11), ohne Tag."""
    normalize_channel_for_write(channel)
    if should_write_cleared(channel):
        return empty_mw_command(channel.number)

    body = _build_mt_body_list(channel)
    return f"MW{''.join(body[0:12])}{''.join(body[POS_MW_MID])};"


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
