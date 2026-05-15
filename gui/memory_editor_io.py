"""Import/Export und Sicherungen fuer den Speicherkanal-Editor."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from mapping.memory_tones import ToneMode
from mapping.rx_mapping import RxMode
from model.memory_editor_channel import (
    MemoryChannelBank,
    MemoryEditorChannel,
    ShiftDirection,
    editor_mode_from_label,
)


def backup_path(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / f"memory_backup_{stamp}.json"


def save_backup_json(bank: MemoryChannelBank, path: Path) -> None:
    payload = {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "channels": [ch.to_dict() for ch in bank.channels],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_backup_json(path: Path) -> MemoryChannelBank:
    data = json.loads(path.read_text(encoding="utf-8"))
    channels = [MemoryEditorChannel.from_dict(d) for d in data.get("channels", [])]
    bank = MemoryChannelBank(channels=channels[:100])
    while len(bank.channels) < 100:
        bank.channels.append(
            MemoryEditorChannel.empty_slot(len(bank.channels) + 1)
        )
    bank.renumber()
    return bank


def export_json(bank: MemoryChannelBank, path: Path) -> None:
    save_backup_json(bank, path)


def import_json(path: Path) -> MemoryChannelBank:
    return load_backup_json(path)


_CSV_FIELDS = [
    "number",
    "enabled",
    "name",
    "rx_frequency_hz",
    "rx_frequency_mhz",
    "mode",
    "shift_direction",
    "shift_offset_hz",
    "shift_offset_mhz",
    "tone_mode",
    "ctcss_tone_hz",
    "dcs_code",
    "local_note",
]


def export_csv(bank: MemoryChannelBank, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, delimiter=";")
        writer.writeheader()
        for ch in bank.channels:
            writer.writerow(
                {
                    "number": ch.number,
                    "enabled": int(ch.enabled),
                    "name": ch.name,
                    "rx_frequency_hz": ch.rx_frequency_hz,
                    "rx_frequency_mhz": f"{ch.rx_frequency_mhz:.6f}",
                    "mode": ch.mode.value,
                    "shift_direction": ch.shift_direction.value,
                    "shift_offset_hz": ch.shift_offset_hz,
                    "shift_offset_mhz": f"{ch.shift_offset_mhz:.6f}",
                    "tone_mode": ch.tone_mode.value,
                    "ctcss_tone_hz": ch.ctcss_tone_hz,
                    "dcs_code": ch.dcs_code,
                    "local_note": ch.local_note,
                }
            )


def import_csv(path: Path) -> MemoryChannelBank:
    channels: List[MemoryEditorChannel] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            num = int(row.get("number", len(channels) + 1))
            mhz = row.get("rx_frequency_mhz")
            hz = row.get("rx_frequency_hz")
            if mhz:
                freq_hz = int(round(float(mhz) * 1_000_000))
            else:
                freq_hz = int(hz or 0)
            mode_label = str(row.get("mode", "FM"))
            try:
                mode = editor_mode_from_label(mode_label)
            except Exception:
                mode = RxMode(mode_label) if mode_label in RxMode._value2member_map_ else RxMode.FM
            shift = ShiftDirection(
                str(row.get("shift_direction", ShiftDirection.SIMPLEX.value))
            )
            tone = ToneMode(str(row.get("tone_mode", ToneMode.OFF.value)))
            ch = MemoryEditorChannel(
                number=num,
                enabled=bool(int(row.get("enabled", 1))),
                name=str(row.get("name", "")),
                rx_frequency_hz=freq_hz,
                mode=mode,
                shift_direction=shift,
                shift_offset_hz=int(row.get("shift_offset_hz") or 600_000),
                tone_mode=tone,
                ctcss_tone_hz=float(row.get("ctcss_tone_hz") or 88.5),
                dcs_code=int(row.get("dcs_code") or 23),
                local_note=str(row.get("local_note", "")),
            )
            ch.changed = True
            channels.append(ch)
    bank = MemoryChannelBank(channels=channels[:100])
    while len(bank.channels) < 100:
        bank.channels.append(
            MemoryEditorChannel.empty_slot(len(bank.channels) + 1)
        )
    bank.renumber()
    return bank


def channels_to_backup_list(channels: Iterable[MemoryEditorChannel]) -> list[dict]:
    return [ch.to_dict() for ch in channels]
