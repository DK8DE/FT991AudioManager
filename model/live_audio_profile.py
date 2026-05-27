"""Live-Audioprofil: EQ, Noise Gate, Kompressor und Funk-Rauschgate."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, List

from model.live_settings import (
    DEFAULT_LIVE_EQ_FREQ_HZ,
    LiveCompressorSettings,
    LiveEqBandSettings,
    LiveFunkListenGateSettings,
    LiveGateSettings,
    LiveSettings,
    _default_eq_bands,
)


@dataclass
class LiveAudioProfile:
    name: str
    eq_enabled: bool = True
    eq_bands: List[LiveEqBandSettings] = field(default_factory=_default_eq_bands)
    gate: LiveGateSettings = field(default_factory=LiveGateSettings)
    compressor: LiveCompressorSettings = field(default_factory=LiveCompressorSettings)
    funk_listen_gate: LiveFunkListenGateSettings = field(
        default_factory=lambda: LiveFunkListenGateSettings.from_dict(None)
    )
    input_gain: float = 1.0
    output_gain: float = 1.0
    funk_output_gain: float = 1.0
    funk_listen_gain: float = 1.0

    def clamp(self) -> None:
        self.eq_enabled = bool(self.eq_enabled)
        want = len(DEFAULT_LIVE_EQ_FREQ_HZ)
        bands = list(self.eq_bands)
        if len(bands) < want:
            for fi in DEFAULT_LIVE_EQ_FREQ_HZ[len(bands) :]:
                bands.append(LiveEqBandSettings(freq_hz=fi))
        elif len(bands) > want:
            bands = bands[:want]
        self.eq_bands = bands
        for band in self.eq_bands:
            band.clamp()
        self.gate.clamp()
        self.compressor.clamp()
        self.funk_listen_gate.clamp()
        self.input_gain = max(0.0, min(2.0, float(self.input_gain)))
        self.output_gain = max(0.0, min(2.0, float(self.output_gain)))
        self.funk_output_gain = max(0.0, min(2.0, float(self.funk_output_gain)))
        self.funk_listen_gain = max(0.0, min(2.0, float(self.funk_listen_gain)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": str(self.name or "").strip(),
            "eq_enabled": bool(self.eq_enabled),
            "eq_bands": [b.to_dict() for b in self.eq_bands],
            "gate": self.gate.to_dict(),
            "compressor": self.compressor.to_dict(),
            "funk_listen_gate": self.funk_listen_gate.to_dict(),
            "input_gain": float(self.input_gain),
            "output_gain": float(self.output_gain),
            "funk_output_gain": float(self.funk_output_gain),
            "funk_listen_gain": float(self.funk_listen_gain),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "LiveAudioProfile":
        if not isinstance(raw, dict):
            return cls(name="")
        raw_bands = raw.get("eq_bands")
        bands: List[LiveEqBandSettings] = []
        if isinstance(raw_bands, list):
            for idx, entry in enumerate(raw_bands):
                fh = DEFAULT_LIVE_EQ_FREQ_HZ[
                    min(idx, len(DEFAULT_LIVE_EQ_FREQ_HZ) - 1)
                ]
                if isinstance(entry, dict):
                    xd = dict(entry)
                    xd.setdefault("freq_hz", fh)
                    bands.append(LiveEqBandSettings.from_dict(xd))
                else:
                    bands.append(LiveEqBandSettings(freq_hz=float(fh)))
            if len(bands) < len(DEFAULT_LIVE_EQ_FREQ_HZ):
                for fi in DEFAULT_LIVE_EQ_FREQ_HZ[len(bands) :]:
                    bands.append(LiveEqBandSettings(freq_hz=fi))
            bands = bands[: len(DEFAULT_LIVE_EQ_FREQ_HZ)]
        profile = cls(
            name=str(raw.get("name", "") or "").strip(),
            eq_enabled=bool(raw.get("eq_enabled", True)),
            eq_bands=bands if bands else _default_eq_bands(),
            gate=LiveGateSettings.from_dict(raw.get("gate")),
            compressor=LiveCompressorSettings.from_dict(raw.get("compressor")),
            funk_listen_gate=LiveFunkListenGateSettings.from_dict(
                raw.get("funk_listen_gate")
            ),
            input_gain=float(raw.get("input_gain", 1.0)),
            output_gain=float(raw.get("output_gain", 1.0)),
            funk_output_gain=float(raw.get("funk_output_gain", 1.0)),
            funk_listen_gain=float(raw.get("funk_listen_gain", 1.0)),
        )
        profile.clamp()
        return profile

    @classmethod
    def from_live_settings(cls, live: LiveSettings, name: str) -> "LiveAudioProfile":
        liv = LiveSettings.from_dict(live.to_dict())
        profile = cls(
            name=str(name or "").strip(),
            eq_enabled=bool(liv.eq_enabled),
            eq_bands=[
                LiveEqBandSettings.from_dict(b.to_dict()) for b in liv.eq_bands
            ],
            gate=LiveGateSettings.from_dict(liv.gate.to_dict()),
            compressor=LiveCompressorSettings.from_dict(liv.compressor.to_dict()),
            funk_listen_gate=LiveFunkListenGateSettings.from_dict(
                liv.funk_listen_gate.to_dict()
            ),
            input_gain=float(liv.input_gain),
            output_gain=float(liv.output_gain),
            funk_output_gain=float(liv.funk_output_gain),
            funk_listen_gain=float(liv.funk_listen_gain),
        )
        profile.clamp()
        return profile

    def apply_to(self, live: LiveSettings) -> None:
        live.eq_enabled = bool(self.eq_enabled)
        live.eq_bands = [
            LiveEqBandSettings.from_dict(b.to_dict()) for b in self.eq_bands
        ]
        live.gate = LiveGateSettings.from_dict(self.gate.to_dict())
        live.compressor = LiveCompressorSettings.from_dict(self.compressor.to_dict())
        live.funk_listen_gate = LiveFunkListenGateSettings.from_dict(
            self.funk_listen_gate.to_dict()
        )
        live.input_gain = float(self.input_gain)
        live.output_gain = float(self.output_gain)
        live.funk_output_gain = float(self.funk_output_gain)
        live.funk_listen_gain = float(self.funk_listen_gain)
        live.clamp_recursive()
