"""Audio-Profil: Sammelt alle Audio-Einstellungen einer Betriebsart.

Ein Profil enthält ab Version 0.3 **immer** den vollständigen Satz an
Audio-Werten:

- MIC Gain
- Parametric MIC EQ an/aus
- Speech Processor an/aus + Level
- SSB-TX-Bandbreite
- Normal-EQ (Parametric MIC EQ)
- Processor-EQ (eigenes 3-Band-Set, wirkt bei aktivem Processor)

Beim Laden eines Profils, das einzelne Felder noch nicht hat, werden
Defaults eingesetzt — das hält die JSON-Dateien aus Version 0.2
kompatibel. Beim ersten erneuten Speichern werden alle Felder geschrieben.

Version 0.5 ergänzt zusätzlich noch erweiterte Audioeinstellungen unter
``advanced`` (frei strukturiert).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from mapping.audio_mapping import (
    MIC_GAIN_DEFAULT,
    PROCESSOR_LEVEL_DEFAULT,
    SSB_BPF_DEFAULT_KEY,
)

from .eq_band import EQSettings
from .extended_settings import ExtendedSettings


VALID_MODE_GROUPS = ("SSB", "AM", "FM", "DATA", "C4FM")


@dataclass
class AudioProfile:
    name: str
    mode_group: str = "SSB"

    # Parametric MIC EQ (Normal-EQ, ohne Processor)
    normal_eq: EQSettings = field(default_factory=EQSettings.default)
    # Processor-EQ (wirkt bei eingeschaltetem Speech Processor)
    processor_eq: EQSettings = field(default_factory=EQSettings.default)

    # Grundwerte
    mic_gain: int = MIC_GAIN_DEFAULT
    mic_eq_enabled: bool = True
    speech_processor_enabled: bool = False
    speech_processor_level: int = PROCESSOR_LEVEL_DEFAULT
    ssb_tx_bpf: str = SSB_BPF_DEFAULT_KEY  # z. B. "100-2900"

    # Erweiterte Einstellungen (Version 0.5): typisierte Felder.
    extended: ExtendedSettings = field(default_factory=ExtendedSettings)

    # Reserviert für noch undefinierte Zukunfts-Settings (lose Key/Value).
    advanced: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialisierung
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "mode_group": self.mode_group,
            "mic_gain": int(self.mic_gain),
            "mic_eq_enabled": bool(self.mic_eq_enabled),
            "speech_processor_enabled": bool(self.speech_processor_enabled),
            "speech_processor_level": int(self.speech_processor_level),
            "ssb_tx_bpf": self.ssb_tx_bpf,
            "normal_eq": self.normal_eq.to_dict(),
            "processor_eq": self.processor_eq.to_dict(),
            "extended": self.extended.to_dict(),
        }
        if self.advanced:
            payload["advanced"] = dict(self.advanced)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioProfile":
        name = str(data.get("name") or "Unbenannt")
        mode_group = str(data.get("mode_group") or "SSB").upper()
        if mode_group not in VALID_MODE_GROUPS:
            mode_group = "SSB"

        normal_eq_data = data.get("normal_eq")
        normal_eq = (
            EQSettings.from_dict(normal_eq_data)
            if isinstance(normal_eq_data, dict)
            else EQSettings.default()
        )

        proc_eq_data = data.get("processor_eq")
        processor_eq = (
            EQSettings.from_dict(proc_eq_data)
            if isinstance(proc_eq_data, dict)
            else EQSettings.default()
        )

        ext_data = data.get("extended")
        extended = (
            ExtendedSettings.from_dict(ext_data) if isinstance(ext_data, dict)
            else ExtendedSettings()
        )

        return cls(
            name=name,
            mode_group=mode_group,
            normal_eq=normal_eq,
            processor_eq=processor_eq,
            mic_gain=_coerce_int(data.get("mic_gain"), MIC_GAIN_DEFAULT),
            mic_eq_enabled=_coerce_bool(data.get("mic_eq_enabled"), True),
            speech_processor_enabled=_coerce_bool(data.get("speech_processor_enabled"), False),
            speech_processor_level=_coerce_int(data.get("speech_processor_level"), PROCESSOR_LEVEL_DEFAULT),
            ssb_tx_bpf=_coerce_str(data.get("ssb_tx_bpf"), SSB_BPF_DEFAULT_KEY),
            extended=extended,
            advanced=dict(data.get("advanced", {}) or {}),
        )


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _coerce_str(value: Any, default: str) -> str:
    if value is None or not isinstance(value, (str, int)):
        return default
    return str(value)
