"""Echtzeit-DSP für Live-Audio (blockweise NumPy/scipy.signal)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    import scipy.signal as signal

    _HAVE_SCIPY = True
except ImportError:
    signal = None  # type: ignore[assignment]
    _HAVE_SCIPY = False

from model.live_settings import (
    LiveCompressorSettings,
    LiveEqBandSettings,
    LiveGateSettings,
)


def block_rms_linear(x_mono: np.ndarray) -> float:
    if x_mono.size <= 0:
        return 0.0
    acc = np.mean(x_mono * x_mono)
    if not math.isfinite(acc) or acc < 1e-20:
        return 0.0
    return float(math.sqrt(acc))


def linear_to_db(lin: float) -> float:
    if lin <= 1e-10:
        return -120.0
    return float(20.0 * math.log10(lin))


def _smooth_coef_for_block(ms_tau: float, block_duration_s: float) -> float:
    tau = max(float(ms_tau) / 1000.0, 1e-6)
    bd = max(float(block_duration_s), 1e-9)
    return float(math.exp(-bd / tau))


def rbj_peaking_sos(freq_hz: float, gain_db: float, q: float, sr: float) -> np.ndarray:
    """Ein Second-Order-Section (Zeile Form (1,6)) — Robert Bristow-Johnson Peaking EQ."""
    f0 = max(1.0, float(freq_hz))
    fs = max(1000.0, float(sr))
    q_eff = max(0.5, float(q))
    dbg = float(gain_db)

    omega0 = 2.0 * math.pi * (f0 / fs)
    cos_w0 = math.cos(omega0)
    sin_w0 = math.sin(omega0)
    alpha = sin_w0 / (2.0 * q_eff)
    a_lin = math.sqrt(10.0 ** (dbg / 40.0))
    inv_a = 1.0 / max(a_lin, 1e-9)

    b0 = 1.0 + alpha * a_lin
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a_lin
    a0 = 1.0 + alpha * inv_a
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha * inv_a

    # SciPy _validate_sos verlangt sos[:, 3] == 1 exakt — nicht (a0 / a0) per Float‑Mul,
    # das kann bei Float64 vom Literal 1.0 abweichen.
    d = 1.0 / a0 if abs(a0) > 1e-18 else 1.0
    return np.array([[b0 * d, b1 * d, b2 * d, 1.0, a1 * d, a2 * d]], dtype=np.float64)


def block_peak_linear(x_mono: np.ndarray) -> float:
    if x_mono.size <= 0:
        return 0.0
    peak = float(np.max(np.abs(x_mono.reshape(-1))))
    if not math.isfinite(peak) or peak < 1e-20:
        return 0.0
    return peak


@dataclass
class FunkListenNoiseGateState:
    """Rauschgate Funk-Rückweg: Peak zum Öffnen, volle Lautheit wenn offen.

    Das Mic-Gate (:class:`NoiseGateState`) nutzt RMS und skaliert den Pegel
    sanft — am Funk-Eingang würde das echtes RX-Signal zu leise machen.
    """

    hold_remain_s: float = 0.0
    open_latched: bool = False
    close_gain: float = 0.0

    def process(
        self,
        x_mono: np.ndarray,
        fs: float,
        cfg: LiveGateSettings,
    ) -> np.ndarray:
        if x_mono.size == 0 or not cfg.enabled:
            return x_mono.astype(np.float32, copy=False)

        x = x_mono.reshape(-1).astype(np.float32, copy=False)
        peak_db = linear_to_db(block_peak_linear(x))
        dt_block = len(x) / max(fs, 1.0)
        open_db = float(cfg.threshold_db)
        close_db = open_db - 8.0
        hold_ms = float(cfg.hold_ms)
        rel_ms = max(float(cfg.release_ms), 1.0)

        if peak_db >= open_db:
            self.open_latched = True
            self.hold_remain_s = hold_ms / 1000.0
        elif peak_db < close_db:
            self.hold_remain_s = max(0.0, self.hold_remain_s - dt_block)
            if self.hold_remain_s <= 0.0:
                self.open_latched = False

        if self.open_latched or self.hold_remain_s > 0.0:
            self.close_gain = 1.0
            return x

        cr = _smooth_coef_for_block(rel_ms, dt_block)
        self.close_gain *= cr
        if self.close_gain < 1e-4:
            self.close_gain = 0.0
            return np.zeros_like(x)
        return (x * np.float32(self.close_gain)).astype(np.float32)


@dataclass
class NoiseGateState:
    smoothed_gate: float = 0.0
    envelope_db_smoothed: float = -120.0
    hold_remain_s: float = 0.0

    def process(self, x_mono: np.ndarray, fs: float, cfg: LiveGateSettings) -> np.ndarray:
        if x_mono.size == 0 or not cfg.enabled:
            return x_mono.astype(np.float32, copy=False)
        thresh = float(cfg.threshold_db)
        hold_ms = float(cfg.hold_ms)
        atk = float(cfg.attack_ms)
        rel = float(cfg.release_ms)
        atk = max(atk, 0.001)
        rel = max(rel, 0.001)

        block_rms_db = linear_to_db(block_rms_linear(x_mono.reshape(-1)))
        dt_block = len(x_mono) / max(fs, 1.0)
        env_smooth = _smooth_coef_for_block(50.0, dt_block)
        self.envelope_db_smoothed = self.envelope_db_smoothed * env_smooth + block_rms_db * (
            1.0 - env_smooth
        )
        env = self.envelope_db_smoothed

        target_open = 1.0 if env >= thresh else 0.0

        if target_open > 0.5:
            self.hold_remain_s = hold_ms / 1000.0
        else:
            if self.hold_remain_s > 0.0:
                self.hold_remain_s -= dt_block
                self.hold_remain_s = max(0.0, self.hold_remain_s)

        # Nur Pegel + Hold — nicht smoothed_gate einbeziehen: sonst bleibt
        # want==1 solange das Gate noch >0 ist → Gate schließt nie (Deadlock).
        gate_open = target_open > 0.5 or self.hold_remain_s > 0.0

        want = 1.0 if gate_open else 0.0
        if want > self.smoothed_gate:
            ca = _smooth_coef_for_block(atk, dt_block)
            self.smoothed_gate = self.smoothed_gate * ca + want * (1.0 - ca)
        else:
            cr = _smooth_coef_for_block(rel, dt_block)
            self.smoothed_gate = self.smoothed_gate * cr + want * (1.0 - cr)

        gain = np.float32(max(0.0, min(1.0, self.smoothed_gate)))
        return (x_mono * gain).astype(np.float32)


@dataclass
class CompressorState:
    envelope_db: float = -80.0
    gr_db_smooth: float = 0.0

    def process(
        self,
        x_mono: np.ndarray,
        fs: float,
        cfg: LiveCompressorSettings,
    ) -> np.ndarray:
        if x_mono.size == 0 or not cfg.enabled:
            return x_mono.astype(np.float32, copy=False)

        atk_ms = float(cfg.attack_ms)
        rel_ms = float(cfg.release_ms)
        atk_ms = max(atk_ms, 1.0)
        rel_ms = max(rel_ms, 1.0)
        dt_block = len(x_mono) / max(fs, 1.0)
        atk_c = _smooth_coef_for_block(atk_ms, dt_block)
        rel_c = _smooth_coef_for_block(rel_ms, dt_block)

        rms = block_rms_linear(x_mono.reshape(-1))
        inst_db = linear_to_db(rms)
        if inst_db > self.envelope_db:
            self.envelope_db = self.envelope_db * atk_c + inst_db * (1.0 - atk_c)
        else:
            self.envelope_db = self.envelope_db * rel_c + inst_db * (1.0 - rel_c)

        thr = float(cfg.threshold_db)
        ratio = max(1.0, float(cfg.ratio))
        over_db = max(0.0, self.envelope_db - thr)
        if over_db <= 0.0:
            want_gr = 0.0
        else:
            want_gr = -over_db * (1.0 - 1.0 / ratio)

        if want_gr < self.gr_db_smooth:
            self.gr_db_smooth = self.gr_db_smooth * atk_c + want_gr * (1.0 - atk_c)
        else:
            self.gr_db_smooth = self.gr_db_smooth * rel_c + want_gr * (1.0 - rel_c)

        makeup_lin = math.pow(10.0, float(cfg.makeup_db) / 20.0)
        gain_lin = math.pow(10.0, self.gr_db_smooth / 20.0) * makeup_lin
        clip_gain = gain_lin if gain_lin <= 128.0 else 128.0
        out = x_mono.astype(np.float32) * np.float32(clip_gain)
        return out


class SevenBandEQ:
    """Sieben serielle Peaking-Bände mit persistentem sosfilt-Zustand."""

    def __init__(
        self,
        sample_rate: float,
        bands: list[LiveEqBandSettings],
    ) -> None:
        self.sample_rate = float(sample_rate)
        self._bands_key: tuple[float, ...] = ()
        self._sos: Optional[np.ndarray] = None
        self._zi: Optional[np.ndarray] = None
        self._bands = bands
        self._rebuild(bands)

    def set_sample_rate(self, sr: float) -> None:
        self.sample_rate = float(sr)
        self._rebuild(list(self._bands))

    def _band_key(self, bands: list[LiveEqBandSettings]) -> tuple[float, ...]:
        key_list: list[float] = []
        # Dieselbe Schweelle wie _concat_sos (ansonsten gleicher Schlüssel, andere SOS‑Koeff.).
        eps = 1e-6
        for b in bands:
            use_gain = bool(b.enabled) and abs(float(b.gain_db)) >= eps
            ga = float(b.gain_db) if use_gain else 0.0
            active = (
                float(b.freq_hz),
                ga,
                float(b.q),
            )
            key_list.extend(active if b.enabled else (float(b.freq_hz), -9999.0, float(b.q)))
        return tuple(key_list)

    def _concat_sos(self, bands: list[LiveEqBandSettings]) -> np.ndarray:
        sos_list = []
        for b in bands:
            if not b.enabled or abs(b.gain_db) < 1e-6:
                continue
            sos_row = rbj_peaking_sos(float(b.freq_hz), float(b.gain_db), float(b.q), self.sample_rate)
            sos_list.append(sos_row)
        if not sos_list:
            return np.tile(np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]]), (1, 1))
        return np.vstack(sos_list)

    def _rebuild(self, bands: list[LiveEqBandSettings]) -> None:
        nk = self._band_key(bands)
        self._bands = list(bands)
        self._bands_key = nk
        self._sos = self._concat_sos(bands)
        if signal is None or not _HAVE_SCIPY:
            self._zi = None
        else:
            zi = signal.sosfilt_zi(self._sos)
            self._zi = (zi * 0.0).astype(np.float64)

    def apply(self, x_mono: np.ndarray, bands: list[LiveEqBandSettings], enabled: bool) -> np.ndarray:
        if x_mono.size == 0:
            return x_mono.astype(np.float32)
        self._bands = list(bands)
        nk = self._band_key(list(bands))
        if not enabled or signal is None or not _HAVE_SCIPY:
            # Schlüssel mitführen, SOS invalidieren — sonst kann nach EQ‑Aus /
            # Regleränderung ein veraltetes sosfilt mit neuem nk laufen.
            self._bands_key = nk
            self._sos = None
            self._zi = None
            return x_mono.astype(np.float32, copy=False)
        if nk != self._bands_key or self._sos is None or self._zi is None:
            self._rebuild(list(bands))
        assert self._sos is not None
        assert self._zi is not None
        y, self._zi = signal.sosfilt(
            self._sos,
            x_mono.astype(np.float64, copy=False),
            zi=self._zi,
        )
        return y.astype(np.float32)


def limiter_peak_dbfs(x_mono: np.ndarray, ceiling_db: float = -1.0) -> np.ndarray:
    """Weicher Kopf mit linearer Kopplung gegen Clipping bei > ceiling."""
    if x_mono.size == 0:
        return x_mono.astype(np.float32)
    lim = math.pow(10.0, float(ceiling_db) / 20.0)
    peak = float(np.max(np.abs(x_mono)))
    if peak <= lim:
        return x_mono.astype(np.float32, copy=False)
    scl = np.float32(lim / peak)
    return (x_mono * scl).astype(np.float32)


@dataclass
class LiveDSPChain:
    gate: NoiseGateState = field(default_factory=NoiseGateState)
    comp: CompressorState = field(default_factory=CompressorState)
    eq: Optional[SevenBandEQ] = None
    #: Letzte RMS-Werte zur Anzeige (Callback → GUI)
    last_in_db_before_output: float = -120.0
    last_out_db: float = -120.0

    def __post_init__(self) -> None:
        self.eq = SevenBandEQ(48000.0, [])  # Rebuild später

    def reset(self, sample_rate: float, bands_tpl: tuple[LiveEqBandSettings, ...]) -> None:
        self.gate = NoiseGateState()
        self.comp = CompressorState()
        self.eq = SevenBandEQ(float(sample_rate), list(bands_tpl))

    def process_block_mono(
        self,
        mono: np.ndarray,
        fs: float,
        *,
        gate: LiveGateSettings,
        comp: LiveCompressorSettings,
        eq_enabled: bool,
        eq_bands: list[LiveEqBandSettings],
    ) -> np.ndarray:
        x = mono.reshape(-1).astype(np.float32, copy=False)
        if self.eq is not None and abs(float(self.eq.sample_rate) - float(fs)) > 0.01:
            self.eq.set_sample_rate(fs)
        pref = linear_to_db(block_rms_linear(x))
        self.last_in_db_before_output = pref
        assert self.eq is not None
        x = self.gate.process(x, fs, gate)
        assert self.eq is not None
        x = self.eq.apply(x, eq_bands, eq_enabled)
        x = self.comp.process(x, fs, comp)
        x = limiter_peak_dbfs(x)
        po = linear_to_db(block_rms_linear(x))
        self.last_out_db = po
        return x


__all__ = [
    "CompressorState",
    "FunkListenNoiseGateState",
    "LiveDSPChain",
    "NoiseGateState",
    "SevenBandEQ",
    "block_rms_linear",
    "linear_to_db",
    "limiter_peak_dbfs",
]
