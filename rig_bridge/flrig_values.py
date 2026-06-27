"""FLRig-Werteskalen und FT-991-CAT-Mapping für die Rig-Bridge."""

from __future__ import annotations

from mapping.meter_mapping import (
    MeterKind,
    _format_swr,
    format_po_watts,
    format_smeter_label,
    smeter_raw_to_db,
    smeter_set_calibration_frequency_hz,
)
from mapping.rx_mapping import AgcMode, RxMode
from mapping.sh_width_mapping import sh_display_hz, sh_supported_p2_indices

# FLRig AGC: 0=OFF, 1=FAST, 2=MID, 3=SLOW, 4=AUTO (Hamlib-flrig üblich)
_AGC_TO_FLRIG: dict[AgcMode, int] = {
    AgcMode.OFF: 0,
    AgcMode.FAST: 1,
    AgcMode.MID: 2,
    AgcMode.SLOW: 3,
    AgcMode.AUTO: 4,
}
_FLRIG_TO_AGC: dict[int, AgcMode] = {
    0: AgcMode.OFF,
    1: AgcMode.FAST,
    2: AgcMode.MID,
    3: AgcMode.SLOW,
    4: AgcMode.AUTO,
}

_RX_TO_FLRIG_MODE: dict[RxMode, str] = {
    RxMode.LSB: "LSB",
    RxMode.USB: "USB",
    RxMode.CW_U: "CW",
    RxMode.CW_L: "CWL",
    RxMode.FM: "FM",
    RxMode.AM: "AM",
    RxMode.AM_N: "AMN",
    RxMode.RTTY_LSB: "RTTY-L",
    RxMode.RTTY_USB: "RTTY-U",
    RxMode.DATA_LSB: "DATA-LSB",
    RxMode.DATA_USB: "DATA-USB",
    RxMode.DATA_FM: "FM",
    RxMode.FM_N: "FMN",
    RxMode.C4FM: "C4FM",
}


def cat_0_255_to_flrig_percent(raw: int) -> int:
    return max(0, min(100, int(round(int(raw) * 100 / 255))))


def flrig_percent_to_cat_0_255(pct: int) -> int:
    return max(0, min(255, int(round(int(pct) * 255 / 100))))


def agc_mode_to_flrig(mode: AgcMode) -> int:
    return _AGC_TO_FLRIG.get(mode, 4)


def flrig_to_agc_mode(index: int) -> AgcMode:
    return _FLRIG_TO_AGC.get(int(index), AgcMode.AUTO)


def rx_mode_to_flrig_name(mode: RxMode) -> str:
    return _RX_TO_FLRIG_MODE.get(mode, "USB")


def flrig_sideband_from_mode(mode_name: str) -> str:
    m = (mode_name or "").upper()
    if m in ("LSB", "CWL", "RTTY-L", "DATA-LSB", "LSB-D1", "PKT-L", "DIGL"):
        return "L"
    return "U"


def sh_find_p2_for_hz(mode: RxMode, hz: int) -> int:
    valid = sh_supported_p2_indices(mode)
    if not valid:
        return 0
    best_p2 = min(valid)
    best_diff = 10_000
    for p2 in valid:
        h = sh_display_hz(mode, p2)
        if h is None:
            continue
        d = abs(h - int(hz))
        if d < best_diff:
            best_diff = d
            best_p2 = p2
    return best_p2


def sh_hz_for_mode_p2(mode: RxMode, p2: int) -> int:
    h = sh_display_hz(mode, p2)
    return int(h or 3000)


def format_flrig_smeter(raw: int) -> str:
    return format_smeter_label(int(raw))


def format_flrig_sunits(raw: int) -> str:
    db = smeter_raw_to_db(int(raw))
    if db >= -3.0:
        if db < 0.5:
            return "S9"
        return f"S9+{round(db)}"
    s = max(0, min(9, round((db + 54.0) / 6.0)))
    return f"S{s}"


def format_flrig_swr(raw: int) -> str:
    return _format_swr(int(raw))


def format_flrig_dbm(raw: int) -> str:
    return f"{smeter_raw_to_db(int(raw)):.1f}"


def format_flrig_pwrmeter(raw: int, freq_hz: int) -> str:
    smeter_set_calibration_frequency_hz(freq_hz)
    w = format_po_watts(int(raw), freq_hz=freq_hz)
    return w.replace(" W", "").strip()


def notch_hz_from_auto_notch(on: bool) -> int:
    return 600 if on else 0
