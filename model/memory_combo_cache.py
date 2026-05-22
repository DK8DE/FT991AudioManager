"""Lokaler Zwischenspeicher für die Speicherkanal-Dropdownliste (optional).

Nach dem ersten vollständigen MT-Scan (Hauptfenster oder Editor-Sync)
werden besetzte Slots als kleine JSON-Datei gespeichert — Folge-Verbindungen
können die Combo ohne Funkgerät-Burst wieder befüllen.

Der Pfad zur Editor-Bank liegt parallel (``memory_editor_bank_cache.json``);
Lesen/Schreiben dort erfolgt über :mod:`gui.memory_editor_io`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from mapping.memory_mapping import MemoryChannel
from mapping.rx_mapping import RxMode

from ._app_paths import app_data_dir
from .memory_editor_channel import MemoryChannelBank


COMBO_CACHE_VERSION = 1
COMBO_CACHE_FILENAME = "memory_combo_cache.json"
EDITOR_BANK_CACHE_FILENAME = "memory_editor_bank_cache.json"


def memory_combo_cache_path() -> Path:
    return app_data_dir() / COMBO_CACHE_FILENAME


def memory_editor_bank_cache_path() -> Path:
    return app_data_dir() / EDITOR_BANK_CACHE_FILENAME


def _rx_mode_from_str(raw: object) -> RxMode:
    s = str(raw or "").strip()
    if not s:
        return RxMode.FM
    try:
        return RxMode(s)
    except ValueError:
        return RxMode.FM


def memory_channel_to_payload(mem: MemoryChannel) -> Dict[str, Any]:
    return {
        "channel": int(mem.channel),
        "frequency_hz": int(mem.frequency_hz),
        "tag": mem.tag.strip(),
        "mode": mem.mode.value,
    }


def memory_channel_from_payload(data: Dict[str, Any]) -> Optional[MemoryChannel]:
    try:
        ch = int(data["channel"])
        hz = int(data["frequency_hz"])
    except (KeyError, TypeError, ValueError):
        return None
    tag = str(data.get("tag", "") or "").strip()
    mode = _rx_mode_from_str(data.get("mode"))
    if hz <= 0 and not tag:
        return None
    return MemoryChannel(channel=ch, frequency_hz=hz, mode=mode, tag=tag)


def memory_channels_from_editor_bank(bank: MemoryChannelBank) -> List[MemoryChannel]:
    """Belegte Plätze 001..100 → :class:`~mapping.memory_mapping.MemoryChannel`."""
    out: List[MemoryChannel] = []
    for ch in bank.channels:
        if ch.is_empty:
            continue
        out.append(
            MemoryChannel(
                channel=int(ch.number),
                frequency_hz=int(ch.rx_frequency_hz),
                mode=ch.mode,
                tag=str(ch.name or "").strip(),
            )
        )
    return sorted(out, key=lambda m: int(m.channel))


def save_memory_combo_cache(channels: List[MemoryChannel]) -> None:
    path = memory_combo_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(channels, key=lambda m: int(m.channel))
    payload = {
        "version": COMBO_CACHE_VERSION,
        "channels": [memory_channel_to_payload(m) for m in ordered],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_memory_combo_cache() -> Optional[List[MemoryChannel]]:
    path = memory_combo_cache_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    clist = raw.get("channels")
    if not isinstance(clist, list):
        return None
    channels: List[MemoryChannel] = []
    for item in clist:
        if not isinstance(item, dict):
            continue
        item_dict = cast(Dict[str, Any], item)
        mem = memory_channel_from_payload(item_dict)
        if mem is not None:
            channels.append(mem)
    return channels
