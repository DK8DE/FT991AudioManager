#!/usr/bin/env python3
"""CAT-Probe: Kanal 91 — Ton/Sequenz finden (Hamlib + Yaesu-Manual).

App schließen, dann::

    python scripts/probe_memory_tone_ch91.py
    python scripts/probe_memory_tone_ch91.py --quick
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cat.serial_cat import SerialCAT  # noqa: E402
from mapping.memory_editor_codec import (  # noqa: E402
    POS_SHIFT_DIR,
    POS_TONE_INDEX,
    POS_TONE_MODE,
    build_mw_command,
    build_mt_command,
)
from mapping.memory_tones import (  # noqa: E402
    CTCSS_TONES_HZ,
    DCS_CODES,
    ToneMode,
    ctcss_cat_tone_number,
    format_cn_set,
    format_ct_set,
    tone_mode_to_p8,
)
from mapping.rx_mapping import RxMode  # noqa: E402
from model._app_paths import app_data_dir  # noqa: E402
from model.memory_editor_channel import (  # noqa: E402
    MemoryEditorChannel,
    ShiftDirection,
)

CHANNEL = 91
TAG = "XYZ"
FREQ_HZ = 438_975_000
SHIFT_OFFSET_HZ = 7_600_000
DELAY_S = 0.15


@dataclass
class Case:
    name: str
    tone_mode: ToneMode
    ctcss_hz: float = 88.5
    dcs_code: int = 23


@dataclass
class Attempt:
    strategy: str
    case: str
    commands: List[str]
    responses: List[str]
    mt_response: str
    p8: str
    p9: str
    shift: str
    success: bool
    note: str


def _load_settings() -> Tuple[str, int, int]:
    data = json.loads((app_data_dir() / "settings.json").read_text(encoding="utf-8"))
    cat = data.get("cat", {})
    return str(cat.get("port", "COM8")), int(cat.get("baudrate", 38400)), int(
        cat.get("timeout_ms", 1000)
    )


def _tx(cat: SerialCAT, cmd: str) -> str:
    try:
        if cmd.endswith(";") and len(cmd) > 3:
            prefix = cmd[:2]
            if prefix in {"MT", "MW", "FA", "CN", "CT", "MC"}:
                return cat.send_command(cmd, expected_prefix=prefix)
        return cat.send_command(cmd, read_response=True)
    except Exception as exc:  # noqa: BLE001
        return f"? ({exc})"


def _tx_write(cat: SerialCAT, cmd: str) -> str:
    return _tx(cat, cmd)


def _switch_vfo(cat: SerialCAT) -> None:
    reply = _tx(cat, "FA;")
    if reply.startswith("FA") and reply[2:-1].isdigit():
        cat.send_command(reply, read_response=False)
    time.sleep(DELAY_S)


def _read_mt(cat: SerialCAT) -> str:
    return _tx(cat, f"MT{CHANNEL:03d};")


def _mid(body: str) -> str:
    return body[12:26] if len(body) >= 26 else body[12:]


def _build_hamlib_mw(ch: MemoryEditorChannel) -> str:
    """Hamlib ``newcat_set_channel``-Format (kompaktes MW)."""
    if ch.tone_mode in (ToneMode.CTCSS_ENC, ToneMode.CTCSS_ENC_DEC):
        c_tone = "2" if ch.tone_mode == ToneMode.CTCSS_ENC else "1"
        tone_i = ctcss_cat_tone_number(ch.ctcss_tone_hz)
    elif ch.tone_mode in (ToneMode.DCS_ENC, ToneMode.DCS_ENC_DEC):
        c_tone = "4" if ch.tone_mode == ToneMode.DCS_ENC else "3"
        tone_i = min(99, int(ch.dcs_code))
    else:
        c_tone = "0"
        tone_i = 0
    shift = {"0": "0", "1": "1", "2": "2"}[
        {"Simplex": "0", "Plus": "1", "Minus": "2"}[ch.shift_direction.value]
    ]
    mode = "4"
    return (
        f"MW{CHANNEL:03d}{FREQ_HZ:09d}+0000"
        f"004{c_tone}{tone_i:02d}{shift};"
    )


def _score(case: Case, body: str) -> Tuple[bool, str]:
    want_p8 = tone_mode_to_p8(case.tone_mode)
    if case.tone_mode == ToneMode.DCS_ENC_DEC:
        want_p8_also = ("3", "B")
    else:
        want_p8_also = (want_p8,)
    got_p8 = body[POS_TONE_MODE] if len(body) > POS_TONE_MODE else "?"
    got_p9 = body[POS_TONE_INDEX] if len(body) >= POS_TONE_INDEX.stop else "??"
    if case.tone_mode in (ToneMode.CTCSS_ENC, ToneMode.CTCSS_ENC_DEC):
        want_p9 = f"{ctcss_cat_tone_number(case.ctcss_hz):02d}"
    elif case.tone_mode in (ToneMode.DCS_ENC, ToneMode.DCS_ENC_DEC):
        want_p9 = f"{min(99, case.dcs_code):02d}"
    else:
        want_p9 = "00"
    ok_p8 = got_p8 in (want_p8, *want_p8_also)
    ok_p9 = got_p9 == want_p9
    ok_shift = len(body) > 24 and (body[POS_SHIFT_DIR] == "2" or body[24] == "2")
    ok = ok_p8 and ok_p9 and ok_shift
    note = (
        f"want P8={want_p8} P9={want_p9} shift=2 | "
        f"got P8={got_p8} P9={got_p9} shift={body[POS_SHIFT_DIR] if len(body) > POS_SHIFT_DIR else '?'}"
    )
    return ok, note


def _cases(quick: bool) -> List[Case]:
    base = [
        Case("off", ToneMode.OFF),
        Case("ctcss_enc_885", ToneMode.CTCSS_ENC, ctcss_hz=88.5),
        Case("ctcss_enc_1188", ToneMode.CTCSS_ENC, ctcss_hz=118.8),
        Case("ctcss_encdec_67", ToneMode.CTCSS_ENC_DEC, ctcss_hz=67.0),
        Case("dcs_enc_23", ToneMode.DCS_ENC, dcs_code=23),
        Case("dcs_encdec_23", ToneMode.DCS_ENC_DEC, dcs_code=23),
    ]
    if quick:
        return base
    for hz in CTCSS_TONES_HZ:
        base.append(
            Case(f"ctcss_{hz:.1f}".replace(".", "_"), ToneMode.CTCSS_ENC, ctcss_hz=hz)
        )
    for code in DCS_CODES[:: max(1, len(DCS_CODES) // 15)]:
        base.append(Case(f"dcs_{code}", ToneMode.DCS_ENC, dcs_code=code))
    return base


def _channel(case: Case) -> MemoryEditorChannel:
    return MemoryEditorChannel(
        number=CHANNEL,
        enabled=True,
        name=TAG,
        rx_frequency_hz=FREQ_HZ,
        mode=RxMode.FM,
        shift_direction=ShiftDirection.MINUS,
        shift_offset_hz=SHIFT_OFFSET_HZ,
        tone_mode=case.tone_mode,
        ctcss_tone_hz=case.ctcss_hz,
        dcs_code=case.dcs_code,
    )


StrategyFn = Callable[[SerialCAT, MemoryEditorChannel], Tuple[List[str], List[str]]]


def _run_cmds(cat: SerialCAT, cmds: List[str]) -> List[str]:
    responses: List[str] = []
    for cmd in cmds:
        responses.append(_tx_write(cat, cmd))
        time.sleep(DELAY_S)
    return responses


def _strategies() -> List[Tuple[str, StrategyFn]]:
    def cn_ct_mw(cat: SerialCAT, ch: MemoryEditorChannel) -> Tuple[List[str], List[str]]:
        cmds: List[str] = []
        if ch.tone_mode != ToneMode.OFF:
            cmds.append(format_cn_set(ch.tone_mode, ctcss_hz=ch.ctcss_tone_hz, dcs_code=ch.dcs_code))
            cmds.append(format_ct_set(ch.tone_mode))
        cmds.append(build_mw_command(ch))
        return cmds, _run_cmds(cat, cmds)

    def cn_ct_mw_mt(cat: SerialCAT, ch: MemoryEditorChannel) -> Tuple[List[str], List[str]]:
        cmds, _ = cn_ct_mw(cat, ch)
        cmds.append(build_mt_command(ch))
        return cmds, _run_cmds(cat, cmds)

    def mc_cn_ct_mw(cat: SerialCAT, ch: MemoryEditorChannel) -> Tuple[List[str], List[str]]:
        cmds = [f"MC{CHANNEL:03d};"]
        if ch.tone_mode != ToneMode.OFF:
            cmds.append(format_cn_set(ch.tone_mode, ctcss_hz=ch.ctcss_tone_hz, dcs_code=ch.dcs_code))
            cmds.append(format_ct_set(ch.tone_mode))
        cmds.append(build_mw_command(ch))
        cmds.append("FA133000000;")
        return cmds, _run_cmds(cat, cmds)

    def mw_only(cat: SerialCAT, ch: MemoryEditorChannel) -> Tuple[List[str], List[str]]:
        cmds = [build_mw_command(ch)]
        return cmds, _run_cmds(cat, cmds)

    def hamlib_mw(cat: SerialCAT, ch: MemoryEditorChannel) -> Tuple[List[str], List[str]]:
        cmds = [_build_hamlib_mw(ch)]
        return cmds, _run_cmds(cat, cmds)

    def mc_hamlib(cat: SerialCAT, ch: MemoryEditorChannel) -> Tuple[List[str], List[str]]:
        cmds = [f"MC{CHANNEL:03d};", _build_hamlib_mw(ch), "FA133000000;"]
        return cmds, _run_cmds(cat, cmds)

    return [
        ("cn_ct_mw", cn_ct_mw),
        ("cn_ct_mw_mt", cn_ct_mw_mt),
        ("mc_cn_ct_mw", mc_cn_ct_mw),
        ("mw_only", mw_only),
        ("hamlib_mw", hamlib_mw),
        ("mc_hamlib", mc_hamlib),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Nur 6 Kernfälle")
    args = parser.parse_args()

    port, baud, timeout_ms = _load_settings()
    print(f"Verbinde mit {port} @ {baud} …")
    cat = SerialCAT()
    try:
        cat.connect(port, baudrate=baud, timeout_ms=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        print(f"Verbindung fehlgeschlagen: {exc}")
        return 1

    baseline = _read_mt(cat)
    baseline_body = baseline[2:-1]
    print(f"Baseline: {_mid(baseline_body)}")

    results: List[Attempt] = []
    _switch_vfo(cat)

    for strat_name, strat_fn in _strategies():
        print(f"\n=== {strat_name} ===")
        for case in _cases(args.quick):
            ch = _channel(case)
            _switch_vfo(cat)
            try:
                cmds, responses = strat_fn(cat, ch)
                time.sleep(DELAY_S)
                mt = _read_mt(cat)
                body = mt[2:-1]
                ok, note = _score(case, body)
                att = Attempt(
                    strategy=strat_name,
                    case=case.name,
                    commands=cmds,
                    responses=responses,
                    mt_response=mt.strip(),
                    p8=body[POS_TONE_MODE] if len(body) > POS_TONE_MODE else "?",
                    p9=body[POS_TONE_INDEX] if len(body) >= POS_TONE_INDEX.stop else "??",
                    shift=body[POS_SHIFT_DIR] if len(body) > POS_SHIFT_DIR else "?",
                    success=ok,
                    note=note,
                )
                results.append(att)
                mark = "OK" if ok else "—"
                print(f"  [{mark}] {case.name}: {note}")
                if responses and responses[-1].startswith("?"):
                    print(f"       letzte Antwort: {responses[-1]!r}")
            except Exception as exc:  # noqa: BLE001
                results.append(
                    Attempt(
                        strategy=strat_name,
                        case=case.name,
                        commands=[],
                        responses=[],
                        mt_response="",
                        p8="?",
                        p9="??",
                        shift="?",
                        success=False,
                        note=str(exc),
                    )
                )
                print(f"  [ERR] {case.name}: {exc}")

    # Kanal 91 aus Baseline wiederherstellen
    print("\nStelle Baseline wieder her …")
    _switch_vfo(cat)
    restore_mw = f"MW{baseline_body[0:12]}{baseline_body[12:26]};"
    restore_mt = f"MT{baseline_body}{';'}"
    _run_cmds(cat, [restore_mw, restore_mt])

    out = app_data_dir() / "memory_tone_probe_results.json"
    winners = [a for a in results if a.success]
    out.write_text(
        json.dumps(
            {
                "baseline_mt": baseline.strip(),
                "attempts": [asdict(a) for a in results],
                "winners": [asdict(a) for a in winners],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(winners)} Treffer -> {out}")
    if winners:
        w = winners[0]
        print(f"Erste Lösung: {w.strategy} / {w.case}")
        for c, r in zip(w.commands, w.responses):
            print(f"  TX {c}  RX {r}")
    cat.disconnect()
    return 0 if winners else 2


if __name__ == "__main__":
    raise SystemExit(main())
