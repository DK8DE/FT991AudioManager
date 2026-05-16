#!/usr/bin/env python3
"""Roundtrip-Probe Kanal 91: Schreiben/Lesen ohne Overlay — nur CAT."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cat.serial_cat import SerialCAT  # noqa: E402
from mapping.memory_editor_codec import (  # noqa: E402
    POS_TONE_INDEX,
    POS_TONE_MODE,
    build_mt_command,
    editor_channel_from_mt_response,
)
from mapping.memory_tones import (  # noqa: E402
    ToneMode,
    apply_cn_read_to_channel,
    ctcss_cat_tone_number,
    ctcss_hz_from_cat_tone_number,
    dcs_cat_index,
    format_cn_query,
    format_cn_set,
    format_ct_set,
    parse_cn_read_response,
)
from mapping.rx_mapping import RxMode  # noqa: E402
from model._app_paths import app_data_dir  # noqa: E402
from model.memory_editor_channel import MemoryEditorChannel, ShiftDirection  # noqa: E402

CHANNEL = 91
DELAY = 0.12


def _load_port() -> tuple[str, int, int]:
    data = json.loads((app_data_dir() / "settings.json").read_text(encoding="utf-8"))
    c = data["cat"]
    return str(c["port"]), int(c["baudrate"]), int(c["timeout_ms"])


def tx(cat: SerialCAT, cmd: str, *, read: bool = True) -> str:
    if not read:
        cat.send_command(cmd, read_response=False)
        return "ok"
    prefix = cmd[:2] if len(cmd) >= 2 else None
    if prefix in {"MC", "CT"}:
        cat.send_command(cmd, read_response=False)
        return "ok"
    return cat.send_command(cmd, expected_prefix=prefix)


def vfo(cat: SerialCAT) -> None:
    r = tx(cat, "FA;")
    if r.startswith("FA") and r[2:-1].isdigit():
        cat.send_command(r, read_response=False)
    time.sleep(DELAY)


def read_mt(cat: SerialCAT) -> str:
    return tx(cat, f"MT{CHANNEL:03d};")


def p9_index(body: str) -> int:
    if len(body) < POS_TONE_INDEX.stop:
        return -1
    return int(body[POS_TONE_INDEX])


def hamlib_mw(ch: MemoryEditorChannel) -> str:
    """Hamlib ``newcat_set_channel`` (9-stellige Frequenz)."""
    if ch.tone_mode in (ToneMode.CTCSS_ENC, ToneMode.CTCSS_ENC_DEC):
        c_tone = "2" if ch.tone_mode == ToneMode.CTCSS_ENC else "1"
        tone_i = ctcss_cat_tone_number(ch.ctcss_tone_hz)
    elif ch.tone_mode in (ToneMode.DCS_ENC, ToneMode.DCS_ENC_DEC):
        c_tone = "4" if ch.tone_mode == ToneMode.DCS_ENC else "3"
        tone_i = dcs_cat_index(ch.dcs_code)
    else:
        c_tone, tone_i = "0", 0
    shift = "2" if ch.shift_direction == ShiftDirection.MINUS else "0"
    return (
        f"MW{CHANNEL:03d}{ch.rx_frequency_hz:09d}+0000"
        f"004{c_tone}{tone_i:02d}{shift};"
    )


def mt_with_p9(ch: MemoryEditorChannel) -> str:
  body = build_mt_command(ch)[2:-1]
  return f"MT{body};"


def try_strategy(
    cat: SerialCAT,
    name: str,
    ch: MemoryEditorChannel,
    cmds: list[str],
) -> dict:
    vfo(cat)
    responses: list[str] = []
    for cmd in cmds:
        read = not (cmd.startswith("MC") and cmd.endswith(";")) or True
        if cmd.startswith(("CN", "CT")) and cmd.endswith(";"):
            try:
                responses.append(tx(cat, cmd, read=False) or "ok")
            except Exception as exc:  # noqa: BLE001
                responses.append(f"? ({exc})")
        elif cmd.endswith(";"):
            try:
                if cmd[:2] in {"MW", "MT", "FA"}:
                    responses.append(tx(cat, cmd, read=cmd.startswith("MT")))
                else:
                    cat.send_command(cmd, read_response=False)
                    responses.append("ok")
            except Exception as exc:  # noqa: BLE001
                responses.append(f"? ({exc})")
        time.sleep(DELAY)
    time.sleep(DELAY)
    mt = read_mt(cat)
    body = mt[2:-1]
    parsed = editor_channel_from_mt_response(mt, requested_channel=CHANNEL)
    p9 = p9_index(body)
    hz_from_p9 = ctcss_hz_from_cat_tone_number(p9) if p9 >= 0 else None
    vfo(cat)
    tx(cat, f"MC{CHANNEL:03d};", read=False)
    time.sleep(DELAY)
    cn_q = format_cn_query(ch.tone_mode)
    cn_r = tx(cat, cn_q)
    p2, num = parse_cn_read_response(cn_r)
    vfo(cat)
    want_hz = ch.ctcss_tone_hz
    want_dcs = ch.dcs_code
    ok_p9 = (
        ch.tone_mode in (ToneMode.CTCSS_ENC, ToneMode.CTCSS_ENC_DEC)
        and p9 == ctcss_cat_tone_number(want_hz)
    )
    if ch.tone_mode in (ToneMode.DCS_ENC, ToneMode.DCS_ENC_DEC) and p2 == 1:
        ok_cn = num == dcs_cat_index(want_dcs)
    elif ch.tone_mode in (ToneMode.CTCSS_ENC, ToneMode.CTCSS_ENC_DEC) and p2 == 0:
        ok_cn = num == ctcss_cat_tone_number(want_hz)
    else:
        ok_cn = False
    return {
        "strategy": name,
        "commands": cmds,
        "responses": responses,
        "mt": mt.strip(),
        "p8": body[POS_TONE_MODE] if len(body) > POS_TONE_MODE else "?",
        "p9": body[POS_TONE_INDEX] if len(body) >= POS_TONE_INDEX.stop else "??",
        "p9_int": p9,
        "hz_from_p9": hz_from_p9,
        "cn_read": cn_r.strip(),
        "cn_num": num,
        "ok_p9": ok_p9,
        "ok_cn": ok_cn,
        "parsed_mode": parsed.tone_mode.value,
        "parsed_hz": parsed.ctcss_tone_hz,
    }


def main() -> int:
    port, baud, timeout = _load_port()
    print(f"COM {port} @ {baud}")
    cat = SerialCAT()
    cat.connect(port, baudrate=baud, timeout_ms=max(timeout, 2000))
    baseline = read_mt(cat)
    print(f"Baseline MT: {baseline.strip()}")
    vfo(cat)

    cases = [
        ("ctcss_1188", MemoryEditorChannel(
            number=CHANNEL, enabled=True, name="XYZ",
            rx_frequency_hz=438_975_000, mode=RxMode.FM,
            shift_direction=ShiftDirection.MINUS, shift_offset_hz=7_600_000,
            tone_mode=ToneMode.CTCSS_ENC, ctcss_tone_hz=118.8,
        )),
        ("ctcss_885", MemoryEditorChannel(
            number=CHANNEL, enabled=True, name="XYZ",
            rx_frequency_hz=438_975_000, mode=RxMode.FM,
            shift_direction=ShiftDirection.MINUS, shift_offset_hz=7_600_000,
            tone_mode=ToneMode.CTCSS_ENC, ctcss_tone_hz=88.5,
        )),
        ("dcs_23", MemoryEditorChannel(
            number=CHANNEL, enabled=True, name="XYZ",
            rx_frequency_hz=438_975_000, mode=RxMode.FM,
            shift_direction=ShiftDirection.MINUS, shift_offset_hz=7_600_000,
            tone_mode=ToneMode.DCS_ENC, dcs_code=23,
        )),
    ]

    results: list[dict] = []
    for case_name, ch in cases:
        cn = format_cn_set(ch.tone_mode, ctcss_hz=ch.ctcss_tone_hz, dcs_code=ch.dcs_code)
        ct = format_ct_set(ch.tone_mode)
        strategies = [
            ("mc_cn_ct_mt_p9", [f"MC{CHANNEL:03d};", cn, ct, mt_with_p9(ch), "FA133000000;"]),
            ("mc_cn_ct_mt_codec", [f"MC{CHANNEL:03d};", cn, ct, build_mt_command(ch), "FA133000000;"]),
            ("mc_cn_ct_hamlib_mw", [f"MC{CHANNEL:03d};", cn, ct, hamlib_mw(ch), "FA133000000;"]),
            ("hamlib_mw_only", [hamlib_mw(ch), "FA133000000;"]),
            ("mt_p9_only", [mt_with_p9(ch), "FA133000000;"]),
        ]
        print(f"\n=== {case_name} ===")
        for sname, cmds in strategies:
            r = try_strategy(cat, sname, ch, cmds)
            r["case"] = case_name
            results.append(r)
            mark = "OK" if r["ok_p9"] or r["ok_cn"] else "—"
            print(
                f"  [{mark}] {sname}: P8={r['p8']} P9={r['p9']} "
                f"CN={r['cn_read']} ok_p9={r['ok_p9']} ok_cn={r['ok_cn']}"
            )

    print("\nRestore baseline …")
    vfo(cat)
    body = baseline[2:-1]
    tx(cat, f"MW{body[0:12]}{body[12:26]};", read=False)
    tx(cat, f"MT{body};", read=False)
    out = app_data_dir() / "memory_tone_roundtrip_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"-> {out}")
    winners = [r for r in results if r["ok_p9"] or r["ok_cn"]]
    print(f"{len(winners)} Treffer von {len(results)}")
    cat.disconnect()
    return 0 if winners else 2


if __name__ == "__main__":
    raise SystemExit(main())
