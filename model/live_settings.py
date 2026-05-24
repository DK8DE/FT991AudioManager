"""Persistierte Einstellungen für Live-Monitoring (sounddevice DSP-Pfad).

Von Audio-Player/Recorder-QMediaDevices getrennt; Geräte-IDs entsprechen
PortAudio-Host-API-Indices (Zeichenketten ganzer Zahlen) oder „“ für Geräte-
Standard.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, List

# Sprach-/Kopfhörer-optimierte 7-Bänder (Hz)
DEFAULT_LIVE_EQ_FREQ_HZ = (80.0, 160.0, 315.0, 630.0, 1250.0, 2500.0, 5000.0)

# Live-DSP (PortAudio) — symmetrisch ±15 dB; unabhängig vom CAT-EQ (+10 dB Deckel).
LIVE_EQ_GAIN_DB_MIN = -15.0
LIVE_EQ_GAIN_DB_MAX = 15.0

DEFAULT_SAMPLERATE = 48000
DEFAULT_BLOCKSIZES_ALLOWED = frozenset({128, 256, 512})
DEFAULT_BLOCKSIZE = 256


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _default_eq_bands() -> List["LiveEqBandSettings"]:
    return [
        LiveEqBandSettings(freq_hz=f, gain_db=0.0, q=2.0, enabled=False)
        for f in DEFAULT_LIVE_EQ_FREQ_HZ
    ]


@dataclass
class LiveGateSettings:
    enabled: bool = False
    threshold_db: float = -45.0
    attack_ms: float = 3.0
    hold_ms: float = 50.0
    release_ms: float = 200.0

    def clamp(self) -> None:
        self.threshold_db = _clamp(self.threshold_db, -80.0, -20.0)
        self.attack_ms = _clamp(self.attack_ms, 1.0, 50.0)
        self.hold_ms = _clamp(self.hold_ms, 10.0, 300.0)
        self.release_ms = _clamp(self.release_ms, 20.0, 1000.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "LiveGateSettings":
        if not isinstance(raw, dict):
            return cls()
        g = cls(
            enabled=bool(raw.get("enabled", False)),
            threshold_db=float(raw.get("threshold_db", -45.0)),
            attack_ms=float(raw.get("attack_ms", 3.0)),
            hold_ms=float(raw.get("hold_ms", 50.0)),
            release_ms=float(raw.get("release_ms", 200.0)),
        )
        g.clamp()
        return g


@dataclass
class LiveFunkListenGateSettings:
    """Einfaches Rauschgate nur am Funk-Rückweg (Monitor-Mithören)."""

    enabled: bool = True
    #: Über leises Zirpen/Rauschen, darunter geschlossen; echtes RX-Signal öffnet leicht.
    threshold_db: float = -34.3
    attack_ms: float = 1.5
    hold_ms: float = 80.0
    release_ms: float = 80.0

    def clamp(self) -> None:
        self.threshold_db = _clamp(self.threshold_db, -70.0, -1.0)
        self.attack_ms = _clamp(self.attack_ms, 0.5, 20.0)
        self.hold_ms = _clamp(self.hold_ms, 5.0, 200.0)
        self.release_ms = _clamp(self.release_ms, 20.0, 500.0)

    def to_gate_settings(self) -> LiveGateSettings:
        g = LiveGateSettings(
            enabled=bool(self.enabled),
            threshold_db=float(self.threshold_db),
            attack_ms=float(self.attack_ms),
            hold_ms=float(self.hold_ms),
            release_ms=float(self.release_ms),
        )
        g.clamp()
        return g

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "LiveFunkListenGateSettings":
        if not isinstance(raw, dict):
            return cls()
        g = cls(
            enabled=bool(raw.get("enabled", True)),
            threshold_db=float(raw.get("threshold_db", -34.3)),
            attack_ms=float(raw.get("attack_ms", 1.5)),
            hold_ms=float(raw.get("hold_ms", 80.0)),
            release_ms=float(raw.get("release_ms", 80.0)),
        )
        g.clamp()
        return g

    @classmethod
    def effective(cls, raw: object) -> "LiveFunkListenGateSettings":
        """Persistierte Werte (Test-UI) oder fest verdrahtete Konstanten."""
        from live.funk_listen_gate import SHOW_TUNING_UI, fixed_funk_listen_gate_settings

        if not SHOW_TUNING_UI:
            return fixed_funk_listen_gate_settings()
        return cls.from_dict(raw)


@dataclass
class LiveCompressorSettings:
    enabled: bool = True
    threshold_db: float = -18.0
    ratio: float = 3.0
    attack_ms: float = 10.0
    release_ms: float = 120.0
    makeup_db: float = 3.0

    def clamp(self) -> None:
        self.threshold_db = _clamp(self.threshold_db, -40.0, 0.0)
        self.ratio = _clamp(self.ratio, 1.0, 10.0)
        self.attack_ms = _clamp(self.attack_ms, 1.0, 50.0)
        self.release_ms = _clamp(self.release_ms, 20.0, 500.0)
        self.makeup_db = _clamp(self.makeup_db, 0.0, 12.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "LiveCompressorSettings":
        if not isinstance(raw, dict):
            return cls()
        c = cls(
            enabled=bool(raw.get("enabled", True)),
            threshold_db=float(raw.get("threshold_db", -18.0)),
            ratio=float(raw.get("ratio", 3.0)),
            attack_ms=float(raw.get("attack_ms", 10.0)),
            release_ms=float(raw.get("release_ms", 120.0)),
            makeup_db=float(raw.get("makeup_db", 3.0)),
        )
        c.clamp()
        return c


@dataclass
class LiveEqBandSettings:
    freq_hz: float
    enabled: bool = True
    gain_db: float = 0.0
    q: float = 2.0

    def clamp(self) -> None:
        self.freq_hz = max(20.0, min(float(self.freq_hz), 20000.0))
        self.gain_db = _clamp(
            self.gain_db,
            float(LIVE_EQ_GAIN_DB_MIN),
            float(LIVE_EQ_GAIN_DB_MAX),
        )
        self.q = _clamp(self.q, 0.5, 10.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "LiveEqBandSettings":
        if not isinstance(raw, dict):
            return cls(freq_hz=float(DEFAULT_LIVE_EQ_FREQ_HZ[0]))
        b = cls(
            freq_hz=float(raw.get("freq_hz", DEFAULT_LIVE_EQ_FREQ_HZ[0]) or 800.0),
            enabled=bool(raw.get("enabled", True)),
            gain_db=float(raw.get("gain_db", 0.0)),
            q=float(raw.get("q", 2.0)),
        )
        b.clamp()
        return b


@dataclass
class LiveSettings:
    input_device_id: str = ""
    input_device_label: str = ""
    #: PortAudio‑Ausgang „Monitor“ / Kopfhörer (wie bisher ``output_device_id`` in älteren Dateien).
    output_device_id: str = ""
    output_device_label: str = ""
    #: Zweiter Ausgang („Funk«): gleiches DSP‑Signal wie Monitor, eigener Mixer‑Ausgang optional.
    funk_output_device_id: str = ""
    funk_output_device_label: str = ""
    #: Zweites Aufnahmegerät (z. B. Funk‑Rückweg/Lin): Rohsignal auf Monitor mischen.
    funk_listen_input_device_id: str = ""
    funk_listen_input_device_label: str = ""
    funk_listen_enabled: bool = True
    #: Verstärkung 0–2,0 (linear); UI-Slider 0–200 mit logarithmischer Kurve
    #: (:mod:`model.live_volume_curve`, 100 % Regler = 1,0).
    input_gain: float = 1.0
    #: Lautheit nur für den Monitor‑Ausgang
    output_gain: float = 1.0
    #: Separate Lautheit für den Funk‑Ausgang
    funk_output_gain: float = 1.0
    #: Lautheit des zweiten Eingangs beim Mischen auf den Monitor‑Ausgang.
    funk_listen_gain: float = 1.0
    samplerate: int = DEFAULT_SAMPLERATE
    blocksize: int = DEFAULT_BLOCKSIZE
    #: Optional gespeicherte Fenstergeometrie (Base64 QByteArray wie andere Fenster).
    window_geometry: str = ""
    gate: LiveGateSettings = field(default_factory=LiveGateSettings)
    funk_listen_gate: LiveFunkListenGateSettings = field(
        default_factory=lambda: LiveFunkListenGateSettings.effective(None)
    )
    compressor: LiveCompressorSettings = field(default_factory=LiveCompressorSettings)
    eq_bands: List[LiveEqBandSettings] = field(default_factory=_default_eq_bands)
    #: EQ-Master aktiv (wie „Parametric MIC EQ“ Checkbox am Funkgerät)
    eq_enabled: bool = True
    #: Wahr: Live‑Bearbeitetes PC‑Mic nicht auf Monitor‑Ausgabe schicken
    #(Funk / Funk‑Mithör weiter).
    suppress_live_monitor_mic: bool = False

    def __post_init__(self) -> None:
        self._normalize_blocksize_and_sr()
        self._normalize_eq_band_count()
        self._clamp_gain()

    def _clamp_gain(self) -> None:
        self.input_gain = _clamp(self.input_gain, 0.0, 2.0)
        self.output_gain = _clamp(self.output_gain, 0.0, 2.0)
        self.funk_output_gain = _clamp(self.funk_output_gain, 0.0, 2.0)
        self.funk_listen_gain = _clamp(self.funk_listen_gain, 0.0, 2.0)
        self.funk_listen_enabled = True

    def _normalize_blocksize_and_sr(self) -> None:
        sr = int(self.samplerate)
        if sr <= 0:
            sr = DEFAULT_SAMPLERATE
        self.samplerate = sr

        bs = int(self.blocksize)
        if bs not in DEFAULT_BLOCKSIZES_ALLOWED:
            bs = DEFAULT_BLOCKSIZE
        self.blocksize = bs

    def _normalize_eq_band_count(self) -> None:
        want = len(DEFAULT_LIVE_EQ_FREQ_HZ)
        bands = list(self.eq_bands)
        if len(bands) < want:
            for fi in DEFAULT_LIVE_EQ_FREQ_HZ[len(bands) :]:
                bands.append(LiveEqBandSettings(freq_hz=fi))
        elif len(bands) > want:
            bands = bands[:want]
        self.eq_bands = bands
        for b in self.eq_bands:
            b.clamp()

    def clamp_recursive(self) -> None:
        self._normalize_blocksize_and_sr()
        self._clamp_gain()
        self._normalize_eq_band_count()
        self.gate.clamp()
        self.funk_listen_gate.clamp()
        self.compressor.clamp()
        for b in self.eq_bands:
            b.clamp()

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_device_id": str(self.input_device_id or ""),
            "input_device_label": str(self.input_device_label or ""),
            "output_device_id": str(self.output_device_id or ""),
            "output_device_label": str(self.output_device_label or ""),
            "funk_output_device_id": str(self.funk_output_device_id or ""),
            "funk_output_device_label": str(self.funk_output_device_label or ""),
            "funk_listen_input_device_id": str(
                self.funk_listen_input_device_id or ""
            ),
            "funk_listen_input_device_label": str(
                self.funk_listen_input_device_label or ""
            ),
            "funk_listen_enabled": bool(self.funk_listen_enabled),
            "input_gain": float(self.input_gain),
            "output_gain": float(self.output_gain),
            "funk_output_gain": float(self.funk_output_gain),
            "funk_listen_gain": float(self.funk_listen_gain),
            "samplerate": int(self.samplerate),
            "blocksize": int(self.blocksize),
            "window_geometry": str(self.window_geometry or ""),
            "gate": self.gate.to_dict(),
            "compressor": self.compressor.to_dict(),
            "eq_bands": [b.to_dict() for b in self.eq_bands],
            "eq_enabled": bool(self.eq_enabled),
            "suppress_live_monitor_mic": bool(self.suppress_live_monitor_mic),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "LiveSettings":
        if not isinstance(raw, dict):
            return cls()

        gates = LiveGateSettings.from_dict(raw.get("gate"))
        funk_gate = LiveFunkListenGateSettings.effective(raw.get("funk_listen_gate"))
        comps = LiveCompressorSettings.from_dict(raw.get("compressor"))
        raw_bands = raw.get("eq_bands")
        bands_e: List[LiveEqBandSettings] = []
        if isinstance(raw_bands, list):
            for idx, x in enumerate(raw_bands):
                fh = DEFAULT_LIVE_EQ_FREQ_HZ[min(idx, len(DEFAULT_LIVE_EQ_FREQ_HZ) - 1)]
                if isinstance(x, dict):
                    xd = dict(x)
                    xd.setdefault("freq_hz", fh)
                    bands_e.append(LiveEqBandSettings.from_dict(xd))
                else:
                    bands_e.append(LiveEqBandSettings(freq_hz=float(fh)))
            if len(bands_e) < len(DEFAULT_LIVE_EQ_FREQ_HZ):
                for fi in DEFAULT_LIVE_EQ_FREQ_HZ[len(bands_e) :]:
                    bands_e.append(LiveEqBandSettings(freq_hz=fi))
            bands_e = bands_e[: len(DEFAULT_LIVE_EQ_FREQ_HZ)]

        raw_in = raw.get("funk_listen_input_device_id")
        if isinstance(raw_in, str) and raw_in.strip():
            fl_in = raw_in.strip()
        else:
            fl_in = ""

        out = cls(
            input_device_id=str(raw.get("input_device_id", "") or ""),
            input_device_label=str(raw.get("input_device_label", "") or ""),
            output_device_id=str(raw.get("output_device_id", "") or ""),
            output_device_label=str(raw.get("output_device_label", "") or ""),
            funk_output_device_id=str(raw.get("funk_output_device_id", "") or ""),
            funk_output_device_label=str(
                raw.get("funk_output_device_label", "") or ""
            ),
            funk_listen_input_device_id=str(fl_in or ""),
            funk_listen_input_device_label=str(
                raw.get("funk_listen_input_device_label", "") or ""
            ),
            funk_listen_enabled=True,
            input_gain=float(raw.get("input_gain", 1.0)),
            output_gain=float(raw.get("output_gain", 1.0)),
            funk_output_gain=float(raw.get("funk_output_gain", 1.0)),
            funk_listen_gain=float(raw.get("funk_listen_gain", 1.0)),
            samplerate=int(raw.get("samplerate", DEFAULT_SAMPLERATE)),
            blocksize=int(raw.get("blocksize", DEFAULT_BLOCKSIZE)),
            window_geometry=str(raw.get("window_geometry", "") or ""),
            gate=gates,
            funk_listen_gate=funk_gate,
            compressor=comps,
            eq_bands=bands_e if bands_e else _default_eq_bands(),
            eq_enabled=bool(raw.get("eq_enabled", True)),
            suppress_live_monitor_mic=bool(
                raw.get("suppress_live_monitor_mic", False)
            ),
        )
        out.clamp_recursive()
        return out


__all__ = [
    "DEFAULT_BLOCKSIZE",
    "DEFAULT_SAMPLERATE",
    "DEFAULT_BLOCKSIZES_ALLOWED",
    "DEFAULT_LIVE_EQ_FREQ_HZ",
    "LiveCompressorSettings",
    "LiveEqBandSettings",
    "LiveFunkListenGateSettings",
    "LiveGateSettings",
    "LiveSettings",
]
