"""Echtzeit-Stream über sounddevice; DSP in :mod:`live.live_dsp`.

NumPy/scipy und :class:`LiveDSPChain` werden erst bei ``start()`` geladen,
damit die App ohne diese Pakete startet.

Zwei physische Geräte: PortAudio lehnt oft einen gemeinsamen ``Stream(...)``
ab — dann automatisch zwei Streams (Ein‑Ausgangs‑Queues), damit z. B. USB‑Mic
plus interne Soundkarte funktionieren.
"""

from __future__ import annotations

import sys
from collections import deque
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, Sequence

from live.live_devices import (
    parse_device_id,
    physical_same_input,
    physical_same_output,
    remap_live_device_id,
    resolve_duplex_device_indices,
    sounddevice_available,
    windows_samplerate_hints_for_live,
    _hostapi_name_for_device,
    _hostapi_rank,
    _norm_group_key,
)
from model.live_settings import (
    DEFAULT_BLOCKSIZE,
    DEFAULT_BLOCKSIZES_ALLOWED,
    LiveEqBandSettings,
    LiveSettings,
)

try:
    import sounddevice as sd

    _HAVE_SD = True
except ImportError:
    sd = None  # type: ignore[assignment]
    _HAVE_SD = False

try:
    import scipy.signal  # noqa: F401 — EQ in live_dsp

    _HAVE_DSP = True
except ImportError:
    _HAVE_DSP = False

_SPLIT_QUEUE_BLOCKS = 128
# Getrennte In/Out-Streams: größere Puffer — weniger Drift vs. WASAPI-Treiber.
_SPLIT_STREAM_LATENCY = "high"

# Probed-Reihenfolge absteigend; höchste gemeinsam gültige Rate gewinnt
# (Bluetooth-HFP-Mics sind oft nur 8/16 kHz, Monitor ggf. 48 kHz).
_STANDARD_LIVE_SAMPLERATES: tuple[float, ...] = (
    96000.0,
    88200.0,
    48000.0,
    44100.0,
    32000.0,
    24000.0,
    22050.0,
    16000.0,
    11025.0,
    8000.0,
)


class _SdStream(Protocol):
    def stop(self) -> None: ...
    def close(self) -> None: ...


def _portaudio_cb_status_problematic(status: object) -> bool:
    """True, wenn ``sounddevice`` im Callback Status-Flags setzt (Über-/Unterlauf u. Ä.).

    In solchen Blöcken sind Eingangs-Samples oft **nicht zuverlässig** — siehe Hinweise in
    der ``sounddevice``-Doku zu :class:`~sounddevice.CallbackFlags`.
    """
    return bool(status)
if TYPE_CHECKING:
    from live.live_dsp import FunkListenNoiseGateState, LiveDSPChain


class LiveAudioEngine:
    """Start/Stop: zuerst einen Duplex-Stream, bei Bedarf getrennte I/O‑Streams."""

    def __init__(self, on_error: Optional[Callable[[str], None]] = None) -> None:
        self._on_error = on_error
        self._stream_duplex: Optional[_SdStream] = None
        self._stream_in: Optional[_SdStream] = None
        self._stream_out: Optional[_SdStream] = None
        self._stream_out_funk: Optional[_SdStream] = None
        self._stream_in_listen: Optional[_SdStream] = None
        self._stream_idle_listen_in: Optional[_SdStream] = None
        self._stream_idle_mic_in: Optional[_SdStream] = None
        self._stream_idle_mon_out: Optional[_SdStream] = None
        self._stream_idle_duplex: Optional[_SdStream] = None
        self._stream_mic_preview_in: Optional[_SdStream] = None
        self._boxed: LiveSettings = LiveSettings()
        self._dsp: Optional["LiveDSPChain"] = None
        self._running = False
        self._idle_listen_running = False
        self._idle_listen_snap = LiveSettings()
        self._mic_preview_running = False
        self._mic_preview_snap = LiveSettings()
        self._funk_listen_gate: FunkListenNoiseGateState
        self._reset_funk_listen_gate()

    def reset_funk_listen_noise_gate(self) -> None:
        """Gate-Zustand zurücksetzen (z. B. nach Schwellen-Änderung in der Test-UI)."""
        self._reset_funk_listen_gate()

    def _reset_funk_listen_gate(self) -> None:
        from live.live_dsp import FunkListenNoiseGateState

        self._funk_listen_gate = FunkListenNoiseGateState()

    def _process_funk_listen_block(
        self,
        raw: object,
        ls: LiveSettings,
        frames: int,
        in_channels: int,
    ) -> Any:
        """Mono Funk-Rückweg nach Lautheit und optionalem Rauschgate."""
        import numpy as np

        nf_i = int(frames)
        ch = max(1, int(in_channels))
        data = np.asarray(raw, dtype=np.float32)
        if data.ndim == 2 and data.shape[1] >= 2:
            mono_rx = np.mean(data, axis=1).astype(np.float32)
        else:
            mono_rx = data[..., 0].astype(np.float32)
        gv_l = np.float32(max(0.0, min(2.0, float(ls.funk_listen_gain))))
        yr = mono_rx.reshape(-1) * gv_l
        fs = max(1000.0, float(ls.samplerate))
        yr = self._funk_listen_gate.process(
            yr.astype(np.float32, copy=False),
            fs,
            ls.funk_listen_gate.to_gate_settings(),
        )
        return LiveAudioEngine._fit_mono_to_frames(yr, nf_i)

    def _get_dsp(self) -> "LiveDSPChain":
        if self._dsp is None:
            from live.live_dsp import LiveDSPChain

            self._dsp = LiveDSPChain()
        return self._dsp

    def is_running(self) -> bool:
        return bool(self._running)

    def is_idle_listen_monitor_running(self) -> bool:
        """Nur durchreichen Lin‑Mit → Monitor, ohne aktives „Live“."""
        return bool(self._idle_listen_running)

    def is_mic_preview_running(self) -> bool:
        """PC‑Mikrofon nur für Pegelanzeige (ohne PTT / vollen Live‑Pfad)."""
        return bool(self._mic_preview_running)

    def push_idle_listen_settings(self, live: LiveSettings) -> None:
        """Idle‑Monitor: Lin‑Mit, Mic‑Send‑Abhör und Pegel ohne Streams neu zu starten."""
        if not self._idle_listen_running:
            return
        live.clamp_recursive()
        snap = LiveSettings.from_dict(live.to_dict())
        self._idle_listen_snap = snap
        if self._mic_preview_running:
            self._mic_preview_snap = LiveSettings.from_dict(snap.to_dict())

    def push_mic_preview_settings(self, live: LiveSettings) -> None:
        """Mic‑Gain / Gate / Kompressor — Idle‑Monitor oder reine Pegel‑Vorschau."""
        if self._idle_listen_running:
            self.push_idle_listen_settings(live)
            return
        if not self._mic_preview_running:
            return
        live.clamp_recursive()
        self._mic_preview_snap = LiveSettings.from_dict(live.to_dict())

    def stop_mic_preview_monitor(self) -> None:
        """Nur Mic‑Pegel‑Vorschau stoppen."""
        self._mic_preview_running = False
        s = self._stream_mic_preview_in
        self._stream_mic_preview_in = None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if not self._running:
            self.mic_meter_db = float(-120.0)

    def stop_idle_listen_monitor(self) -> None:
        """Idle‑Offline‑Monitor stoppen (:meth:`stop` lässt das unberührt)."""
        self._idle_listen_running = False
        self._mic_preview_running = False
        self.funk_listen_meter_db = float(-120.0)
        self.monitor_meter_db = float(-120.0)
        if not self._running:
            self.mic_meter_db = float(-120.0)
        objs: list[_SdStream] = []
        for attr in (
            "_stream_idle_duplex",
            "_stream_idle_mon_out",
            "_stream_idle_listen_in",
            "_stream_idle_mic_in",
        ):
            s = getattr(self, attr)
            setattr(self, attr, None)
            if s is not None:
                objs.append(s)
        for s in objs:
            try:
                s.stop()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    def push_settings(self, live: LiveSettings) -> None:
        live.clamp_recursive()
        self._boxed = LiveSettings.from_dict(live.to_dict())

    def _read_snap(self) -> LiveSettings:
        return self._boxed

    input_meter_db: float = -120.0
    output_meter_db: float = -120.0
    #: Peakanzeige Sende‑Mic nach Gate/EQ/Kompressor/Limiter (vor Funk‑Gain).
    mic_meter_db: float = -120.0
    #: Signal am Monitor‑Device (DSP‑Mit + Funk‑Eing nach Summe und Monitor‑Gain).
    monitor_meter_db: float = -120.0
    #: Funk‑Ausgangs‑Stream (Post‑„Funk“‑Gain).
    funk_meter_db: float = -120.0
    #: Funk‑Eingangs‑„Mithören“‑Stream (nach Regler).
    funk_listen_meter_db: float = -120.0

    @staticmethod
    def _mono_peak_dbfs(samples: Any) -> float:
        """Übergangsspitzwert eines Mono‑Blocks (~dBFS, Referenz Pegel ±1)."""
        import numpy as np

        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if int(x.shape[0]) <= 0:
            return float(-120.0)
        pk = float(np.max(np.abs(x)))
        if pk <= float(1e-9):
            return float(-120.0)
        return float(max(-120.0, min(0.0, 20.0 * np.log10(pk))))

    @staticmethod
    def _mono_mic_after_input_gain_fit(
        indata: object,
        ls: LiveSettings,
        frames: int,
    ) -> Any:
        """Mono‑Zeichenstrom nach „Mic“-/``input_gain`` — **vor** Gate/EQ/Kompressor/Limiter."""
        import numpy as np

        idata = np.asarray(indata)
        if idata.ndim == 2 and idata.shape[1] >= 2:
            mono_in = np.mean(idata.astype(np.float32), axis=1).astype(np.float32)
        else:
            mono_in = np.asarray(idata[..., 0], dtype=np.float32)
        mono_in = mono_in.reshape(-1).astype(np.float32, copy=False)
        inv = np.float32(max(0.0, min(2.0, float(ls.input_gain))))
        scaled = mono_in * inv
        return LiveAudioEngine._fit_mono_to_frames(scaled, int(frames))

    def _mic_dsp_peak_dbfs(self, indata: object, ls: LiveSettings, frames: int) -> float:
        """Peak dBFS nach Gate/EQ/Kompressor — wie der Sendepfad zum Funkgerät."""
        y = self._dsp_mono_without_output_gain(ls, indata)
        return LiveAudioEngine._mono_peak_dbfs(
            LiveAudioEngine._fit_mono_to_frames(y, int(frames))
        )

    def peek_meters_db(self) -> tuple[float, float]:
        """Kurzüberblick DSP für Textzeile („Eing./Ausg.“) — entspricht Live‑Strip‑Pegeln."""
        return (float(self.mic_meter_db), float(self.monitor_meter_db))

    def peek_live_strip_meters_db(self) -> tuple[float, float, float, float]:
        """Vier Pegel wie im Live‑Fenster neben Mic/Monitor/Funk/Funk‑Eingang."""
        return (
            float(self.mic_meter_db),
            float(self.monitor_meter_db),
            float(self.funk_meter_db),
            float(self.funk_listen_meter_db),
        )

    def _reset_live_strip_meters(self) -> None:
        self.mic_meter_db = float(-120.0)
        self.monitor_meter_db = float(-120.0)
        self.funk_meter_db = float(-120.0)
        self.funk_listen_meter_db = float(-120.0)

    def prerequisites_ok(self) -> tuple[bool, str]:
        try:
            import numpy  # noqa: F401
        except ModuleNotFoundError:
            return (
                False,
                "numpy fehlt. Bitte im gleichen Python installieren:\n"
                f'  "{sys.executable}" -m pip install numpy scipy sounddevice',
            )
        if not _HAVE_SD or not sounddevice_available():
            return False, (
                "sounddevice/PortAudio fehlen. Bitte installieren:\n"
                f'  "{sys.executable}" -m pip install sounddevice'
            )
        if not _HAVE_DSP:
            return (
                False,
                "scipy fehlt (EQ/Filter). Bitte installieren:\n"
                f'  "{sys.executable}" -m pip install scipy',
            )
        return True, ""

    def _dsp_mono_without_output_gain(self, ls: LiveSettings, indata: object) -> Any:
        """Mono‑Block nach Eingangsverstärkung und DSP, **ohne** Monitor/Funk‑Lautheit."""
        import numpy as np

        idata = np.asarray(indata)
        if idata.ndim == 2 and idata.shape[1] >= 2:
            mono_in = np.mean(idata.astype(np.float32), axis=1).astype(np.float32)
        else:
            mono_in = idata[..., 0].astype(np.float32)

        x = mono_in.astype(np.float32)
        inv = np.float32(max(0.0, min(2.0, float(ls.input_gain))))
        x = x * inv

        bands = ls.eq_bands
        chain = self._get_dsp()
        y_tail = chain.process_block_mono(
            x,
            float(ls.samplerate),
            gate=ls.gate,
            comp=ls.compressor,
            eq_enabled=bool(ls.eq_enabled),
            eq_bands=bands,
        )

        g_in_db = (
            float(20.0 * np.log10(float(inv)))
            if float(inv) > 1e-9
            else -120.0
        )
        ov_m = np.float32(max(0.0, min(2.0, float(ls.output_gain))))
        g_mon_db = (
            float(20.0 * np.log10(float(ov_m))) if float(ov_m) > 1e-9 else -120.0
        )
        self.input_meter_db = float(chain.last_in_db_before_output + g_in_db)
        self.output_meter_db = float(chain.last_out_db) + g_mon_db

        return y_tail.astype(np.float32)

    @staticmethod
    def _fit_mono_to_frames(chunk: Any, frames: int) -> Any:
        import numpy as np

        y = np.asarray(chunk, dtype=np.float32).reshape(-1)
        nf = int(frames)
        n = int(y.shape[0])
        if n == nf:
            return y
        if n > nf:
            return y[:nf]
        pad = np.zeros(nf, dtype=np.float32)
        pad[:n] = y
        return pad

    @staticmethod
    def _stereo_fill_mono(outdata: object, mono_y: Any) -> None:
        import numpy as np

        y = np.asarray(mono_y, dtype=np.float32).ravel()
        n = int(y.shape[0])
        buf = np.asarray(outdata)
        if buf.ndim != 2:
            return
        nrows = int(buf.shape[0])
        ncol = min(2, int(buf.shape[1]))
        buf[:, :] = np.float32(0.0)
        fc = min(n, nrows)
        if fc <= 0 or ncol <= 0:
            return
        yy = y[:fc]
        if ncol >= 2:
            buf[:fc, 0] = yy
            buf[:fc, 1] = yy
        else:
            buf[:fc, 0] = yy

    def _stop_open_streams_safe(self) -> None:
        ordered = (
            "_stream_duplex",
            "_stream_out",
            "_stream_out_funk",
            "_stream_in_listen",
            "_stream_in",
        )
        objs: list[_SdStream] = []
        for attr in ordered:
            s = getattr(self, attr)
            setattr(self, attr, None)
            if s is not None:
                objs.append(s)
        for s in objs:
            try:
                s.stop()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    def _start_duplex(
        self,
        live: LiveSettings,
        *,
        in_dev: Optional[int],
        out_dev: Optional[int],
        sr: float,
    ) -> None:
        assert sd is not None

        in_ch = max(1, int(self._in_channels_hint(in_dev)))
        weak_self = self

        def callback(
            indata: object,
            outdata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            import numpy as np

            nf = int(frames)
            if _portaudio_cb_status_problematic(status):
                safe_in = np.zeros((nf, in_ch), dtype=np.float32)
            else:
                safe_in = np.asarray(indata, dtype=np.float32)
            ls = weak_self._read_snap()
            y_dsp = weak_self._dsp_mono_without_output_gain(ls, safe_in)
            gv = np.float32(max(0.0, min(2.0, float(ls.output_gain))))
            weak_self.mic_meter_db = LiveAudioEngine._mono_peak_dbfs(
                LiveAudioEngine._fit_mono_to_frames(
                    np.asarray(y_dsp, dtype=np.float32).reshape(-1), nf
                )
            )

            mic_sig = np.zeros(nf, dtype=np.float32)
            if not bool(ls.suppress_live_monitor_mic):
                mic_sig = np.asarray(y_dsp, dtype=np.float32).reshape(-1)
                mic_sig = LiveAudioEngine._fit_mono_to_frames(mic_sig, nf)
            y_mono = mic_sig.astype(np.float32) * gv
            weak_self.monitor_meter_db = LiveAudioEngine._mono_peak_dbfs(y_mono)
            weak_self.funk_meter_db = float(-120.0)
            weak_self.funk_listen_meter_db = float(-120.0)
            LiveAudioEngine._stereo_fill_mono(outdata, y_mono)
        stream = sd.Stream(
            samplerate=float(sr),
            blocksize=int(live.blocksize),
            device=(in_dev, out_dev),
            channels=(in_ch, 2),
            dtype="float32",
            latency="low",
            callback=callback,
        )
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        self._stream_duplex = stream

    def _start_idle_duplex(
        self,
        live: LiveSettings,
        *,
        in_dev: Optional[int],
        out_dev: Optional[int],
        sr: float,
        bs: int,
    ) -> None:
        """Idle-Mithören: ein Duplex-Stream (Mic → Monitor), ohne Queue-Drift."""
        assert sd is not None

        in_ch = max(1, int(self._in_channels_hint(in_dev)))
        weak_self = self

        def callback(
            indata: object,
            outdata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            import numpy as np

            nf = int(frames)
            if not weak_self._idle_listen_running:
                LiveAudioEngine._stereo_fill_mono(
                    outdata, np.zeros(nf, dtype=np.float32)
                )
                return
            if _portaudio_cb_status_problematic(status):
                safe_in = np.zeros((nf, in_ch), dtype=np.float32)
            else:
                safe_in = np.asarray(indata, dtype=np.float32)
            ls = weak_self._idle_listen_snap
            y_dsp = weak_self._dsp_mono_without_output_gain(ls, safe_in)
            weak_self.mic_meter_db = LiveAudioEngine._mono_peak_dbfs(
                LiveAudioEngine._fit_mono_to_frames(
                    np.asarray(y_dsp, dtype=np.float32).reshape(-1), nf
                )
            )
            gv = np.float32(max(0.0, min(2.0, float(ls.output_gain))))
            mic_sig = np.zeros(nf, dtype=np.float32)
            if not bool(ls.suppress_live_monitor_mic):
                mic_sig = LiveAudioEngine._fit_mono_to_frames(
                    np.asarray(y_dsp, dtype=np.float32).reshape(-1), nf
                )
            y_mono = mic_sig.astype(np.float32) * gv
            weak_self.monitor_meter_db = LiveAudioEngine._mono_peak_dbfs(y_mono)
            weak_self.funk_meter_db = float(-120.0)
            weak_self.funk_listen_meter_db = float(-120.0)
            LiveAudioEngine._stereo_fill_mono(outdata, y_mono)

        stream = sd.Stream(
            samplerate=float(sr),
            blocksize=int(bs),
            device=(in_dev, out_dev),
            channels=(in_ch, 2),
            dtype="float32",
            latency="low",
            callback=callback,
        )
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        self._stream_idle_duplex = stream

    def _start_split_streams(
        self,
        live: LiveSettings,
        *,
        in_dev: Optional[int],
        in_listen: Optional[int],
        out_mon: Optional[int],
        out_funk: Optional[int],
        sr: float,
    ) -> None:
        """Eingang‑PC‑Mic (+ optional zweiter Aufnahmegang) → Monitor/Funk‑Ausgang."""
        assert sd is not None
        import numpy as np

        sr = float(sr)
        bs = int(live.blocksize)
        in_ch = max(1, int(self._in_channels_hint(in_dev)))
        out_ch_mon = max(1, int(self._out_channels_hint(out_mon)))
        dual_funk = out_funk is not None
        dual_listen_in = in_listen is not None
        listen_in_ch = (
            max(1, int(self._in_channels_hint(in_listen)))
            if in_listen is not None
            else 1
        )

        lock = Lock()
        weak_self = self

        q_mon: deque[Any] = deque(maxlen=_SPLIT_QUEUE_BLOCKS)
        q_funk: deque[Any] = deque(maxlen=_SPLIT_QUEUE_BLOCKS)
        q_listen_in: deque[Any] = deque(maxlen=_SPLIT_QUEUE_BLOCKS)

        def input_cb_main(
            indata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            if not weak_self._running:
                return
            nf_f = int(frames)
            if _portaudio_cb_status_problematic(status):
                safe_in = np.zeros((nf_f, in_ch), dtype=np.float32)
            else:
                safe_in = np.asarray(indata, dtype=np.float32)
            ls = weak_self._read_snap()
            y_dsp = weak_self._dsp_mono_without_output_gain(ls, safe_in)
            weak_self.mic_meter_db = LiveAudioEngine._mono_peak_dbfs(
                LiveAudioEngine._fit_mono_to_frames(
                    np.asarray(y_dsp, dtype=np.float32).reshape(-1), nf_f
                )
            )
            gv_f = np.float32(max(0.0, min(2.0, float(ls.funk_output_gain))))
            # Gesamt-Lautheit auf dem Monitor-Device: später in ``output_cb_monitor``
            # (summe Mic + Funk-Eing × ``output_gain``), sonst betrifft der Regler nicht
            # den Funk‑Eing‑Anteil bzw. wirk „tot“, wenn nur dieser auf dem Pfad liegt.
            if bool(ls.suppress_live_monitor_mic):
                ym = np.zeros(nf_f, dtype=np.float32)
            else:
                ym = np.asarray(y_dsp, dtype=np.float32).reshape(-1)
            fit_m = LiveAudioEngine._fit_mono_to_frames(ym, nf_f)

            ff: Optional[np.ndarray] = None
            if dual_funk:
                yf = np.asarray(y_dsp, dtype=np.float32).reshape(-1) * gv_f
                ff = LiveAudioEngine._fit_mono_to_frames(yf, nf_f)

            with lock:
                q_mon.append(fit_m.astype(np.float32, copy=True))
                if dual_funk and ff is not None:
                    q_funk.append(ff.astype(np.float32, copy=True))
            if not dual_funk:
                weak_self.funk_meter_db = float(-120.0)

        def input_cb_listen(
            indata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            if not weak_self._running:
                return
            ls = weak_self._read_snap()
            nf_i = int(frames)
            if not bool(ls.funk_listen_enabled):
                zeros = np.zeros(nf_i, dtype=np.float32)
                fit_r = LiveAudioEngine._fit_mono_to_frames(zeros, nf_i)
                weak_self.funk_listen_meter_db = float(-120.0)
                with lock:
                    q_listen_in.append(fit_r.astype(np.float32, copy=True))
                return
            if _portaudio_cb_status_problematic(status):
                raw = np.zeros((nf_i, listen_in_ch), dtype=np.float32)
            else:
                raw = np.asarray(indata, dtype=np.float32)
            fit_r = weak_self._process_funk_listen_block(
                raw, ls, nf_i, listen_in_ch
            )
            weak_self.funk_listen_meter_db = LiveAudioEngine._mono_peak_dbfs(fit_r)
            with lock:
                q_listen_in.append(fit_r.astype(np.float32, copy=True))

        def output_cb_monitor(
            outdata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info, status
            nf = int(frames)
            if not weak_self._running:
                LiveAudioEngine._stereo_fill_mono(
                    outdata, np.zeros(nf, dtype=np.float32)
                )
                return
            with lock:
                if q_mon:
                    chunk_m = q_mon.popleft()
                else:
                    chunk_m = np.zeros(nf, dtype=np.float32)
                    weak_self.monitor_meter_db = float(-120.0)
                if dual_listen_in:
                    chunk_r = (
                        q_listen_in.popleft()
                        if q_listen_in
                        else np.zeros(nf, dtype=np.float32)
                    )
                else:
                    chunk_r = np.zeros(nf, dtype=np.float32)
                    weak_self.funk_listen_meter_db = float(-120.0)
            ym = LiveAudioEngine._fit_mono_to_frames(chunk_m, nf)
            yrx = LiveAudioEngine._fit_mono_to_frames(chunk_r, nf)
            ls = weak_self._read_snap()
            gv_mon = np.float32(max(0.0, min(2.0, float(ls.output_gain))))
            mix = (ym + yrx) * gv_mon
            np.clip(mix, -1.0, 1.0, out=mix)
            weak_self.monitor_meter_db = LiveAudioEngine._mono_peak_dbfs(mix)
            LiveAudioEngine._stereo_fill_mono(outdata, mix)

        def output_cb_funk(
            outdata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info, status
            nf = int(frames)
            if not weak_self._running:
                LiveAudioEngine._stereo_fill_mono(
                    outdata, np.zeros(nf, dtype=np.float32)
                )
                return
            with lock:
                if q_funk:
                    chunk = q_funk.popleft()
                else:
                    chunk = np.zeros(nf, dtype=np.float32)
            ym = LiveAudioEngine._fit_mono_to_frames(chunk, nf)
            weak_self.funk_meter_db = LiveAudioEngine._mono_peak_dbfs(ym)
            LiveAudioEngine._stereo_fill_mono(outdata, ym)

        istream_main = sd.InputStream(
            device=in_dev,
            channels=in_ch,
            dtype="float32",
            samplerate=sr,
            blocksize=bs,
            latency=_SPLIT_STREAM_LATENCY,
            callback=input_cb_main,
        )
        o_mon = sd.OutputStream(
            device=out_mon,
            channels=out_ch_mon,
            dtype="float32",
            samplerate=sr,
            blocksize=bs,
            latency=_SPLIT_STREAM_LATENCY,
            callback=output_cb_monitor,
        )

        objs: list[Any] = [istream_main, o_mon]
        i_listen_obj: Optional[Any] = None
        if dual_listen_in and in_listen is not None:
            i_listen_obj = sd.InputStream(
                device=in_listen,
                channels=listen_in_ch,
                dtype="float32",
                samplerate=sr,
                blocksize=bs,
                latency=_SPLIT_STREAM_LATENCY,
                callback=input_cb_listen,
            )
            objs.insert(1, i_listen_obj)

        o_fnk_obj: Optional[Any] = None
        if dual_funk and out_funk is not None:
            out_ch_f = max(1, int(self._out_channels_hint(out_funk)))
            o_fnk_obj = sd.OutputStream(
                device=out_funk,
                channels=out_ch_f,
                dtype="float32",
                samplerate=sr,
                blocksize=bs,
                latency="low",
                callback=output_cb_funk,
            )
            objs.append(o_fnk_obj)

        try:
            for ob in objs:
                ob.start()
        except BaseException:
            for ob in objs:
                try:
                    ob.stop()
                except BaseException:
                    pass
                try:
                    ob.close()
                except BaseException:
                    pass
            raise

        self._stream_in = istream_main
        self._stream_in_listen = i_listen_obj
        self._stream_out = o_mon
        self._stream_out_funk = o_fnk_obj

    def start_idle_listen_monitor(self, live: LiveSettings) -> tuple[bool, str]:
        """Offline‑Monitor: Mic‑Send‑Pegel/Abhör (Gate/EQ/Komp.) + optional Funk‑Eingang.

        „Eigene NF abhören“ spielt den Sendepfad (Ende der DSP‑Kette) auf den Monitor,
        auch ohne PTX — gemischt mit Funk‑Eingang wie im Live‑Betrieb.
        """
        ok0, msg0 = self.prerequisites_ok()
        if not ok0:
            return False, msg0
        assert sd is not None

        live.clamp_recursive()
        mic_sid = str(live.input_device_id or "").strip()
        listen_sid = str(live.funk_listen_input_device_id or "").strip()
        mon_sid = str(live.output_device_id or "").strip()
        want_mic_meter = bool(mic_sid)
        want_rx_listen = bool(listen_sid)
        want_mic_monitor = not bool(live.suppress_live_monitor_mic)
        want_monitor_out = bool(mon_sid) and (
            want_rx_listen or (want_mic_meter and want_mic_monitor)
        )

        if not want_mic_meter and not want_rx_listen and not want_monitor_out:
            self.stop_idle_listen_monitor()
            return True, ""

        allowed_in = tuple(x for x, _ in self._enumerate_input_indices())
        allowed_out = tuple(x for x, _ in self._enumerate_output_indices())

        in_mic: Optional[int] = None
        if want_mic_meter:
            in_mic = LiveAudioEngine._parse_input_device_index(
                mic_sid,
                allowed_in,
                saved_label=str(live.input_device_label or ""),
            )
            if in_mic is None:
                self.stop_idle_listen_monitor()
                return False, "Idle‑Monitor: ungültiges PC‑Mikrofon."

        in_listen: Optional[int] = None
        if want_rx_listen:
            in_listen = LiveAudioEngine._parse_input_device_index(
                listen_sid,
                allowed_in,
                saved_label=str(live.funk_listen_input_device_label or ""),
            )
            if in_listen is None:
                self.stop_idle_listen_monitor()
                return False, "Idle‑Monitor: ungültiger Funk‑Eingang."

        out_mon: Optional[int] = None
        if want_monitor_out:
            out_mon = LiveAudioEngine._parse_output_device_index(
                mon_sid,
                allowed_out,
                saved_label=str(live.output_device_label or ""),
            )
            if out_mon is None:
                self.stop_idle_listen_monitor()
                return False, "Idle‑Monitor: ungültiger Monitor‑Ausgang."

        _mic, out_mon, _funk, in_listen = LiveAudioEngine._apply_stream_device_remapping(
            in_mic,
            out_mon,
            listen_in_dev=in_listen,
        )
        if want_mic_meter and in_mic is not None and _mic is not None:
            in_mic = _mic

        self.stop_idle_listen_monitor()
        self.stop_mic_preview_monitor()

        in_ch_mic = (
            max(1, int(self._in_channels_hint(in_mic))) if in_mic is not None else 1
        )
        in_ch_l = (
            max(1, int(self._in_channels_hint(in_listen)))
            if in_listen is not None
            else 1
        )
        out_ch_mon = (
            max(1, int(self._out_channels_hint(out_mon))) if out_mon is not None else 1
        )
        sr, bs = self._resolve_stream_params(
            live,
            mic_dev=in_mic,
            monitor_dev=out_mon,
            listen_in_dev=in_listen,
        )
        live = LiveSettings.from_dict(live.to_dict())
        live.samplerate = int(round(sr))
        live.blocksize = int(bs)

        if want_mic_meter:
            dsp = self._get_dsp()
            tpl = tuple(
                LiveEqBandSettings.from_dict(b.to_dict()) for b in live.eq_bands
            )
            dsp.reset(float(sr), tpl)

        want_idle_duplex = (
            in_listen is None
            and in_mic is not None
            and want_monitor_out
            and want_mic_monitor
            and out_mon is not None
        )
        if want_idle_duplex:
            rin, rout = resolve_duplex_device_indices(in_mic, out_mon)
            try:
                snap = LiveSettings.from_dict(live.to_dict())
                self._idle_listen_snap = snap
                self._mic_preview_snap = LiveSettings.from_dict(snap.to_dict())
                self._reset_funk_listen_gate()
                self._idle_listen_running = True
                self._mic_preview_running = bool(want_mic_meter)
                self._start_idle_duplex(
                    live,
                    in_dev=rin,
                    out_dev=rout,
                    sr=float(sr),
                    bs=int(bs),
                )
                return True, ""
            except BaseException:
                self.stop_idle_listen_monitor()

        import numpy as np

        weak_self = self
        lock = Lock()
        q_mic: deque[Any] = deque(maxlen=_SPLIT_QUEUE_BLOCKS)
        q_listen: deque[Any] = deque(maxlen=_SPLIT_QUEUE_BLOCKS)

        def input_cb_idle_mic(
            indata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            if not weak_self._idle_listen_running:
                return
            nf_i = int(frames)
            if _portaudio_cb_status_problematic(status):
                safe_in = np.zeros((nf_i, in_ch_mic), dtype=np.float32)
            else:
                safe_in = np.asarray(indata, dtype=np.float32)
            ls = weak_self._idle_listen_snap
            y_dsp = weak_self._dsp_mono_without_output_gain(ls, safe_in)
            weak_self.mic_meter_db = LiveAudioEngine._mono_peak_dbfs(
                LiveAudioEngine._fit_mono_to_frames(
                    np.asarray(y_dsp, dtype=np.float32).reshape(-1), nf_i
                )
            )
            if not want_monitor_out or not want_mic_monitor:
                return
            ym = np.asarray(y_dsp, dtype=np.float32).reshape(-1)
            fit_m = LiveAudioEngine._fit_mono_to_frames(ym, nf_i)
            with lock:
                q_mic.append(fit_m.astype(np.float32, copy=True))

        def input_cb_idle_listen(
            indata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            if not weak_self._idle_listen_running:
                return
            ls = weak_self._idle_listen_snap
            nf_i = int(frames)
            if _portaudio_cb_status_problematic(status):
                raw = np.zeros((nf_i, in_ch_l), dtype=np.float32)
            else:
                raw = np.asarray(indata, dtype=np.float32)
            fit_r = weak_self._process_funk_listen_block(raw, ls, nf_i, in_ch_l)
            weak_self.funk_listen_meter_db = LiveAudioEngine._mono_peak_dbfs(fit_r)
            if not want_monitor_out:
                return
            with lock:
                q_listen.append(fit_r.astype(np.float32, copy=True))

        def output_cb_idle(
            outdata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info, status
            nf = int(frames)
            if not weak_self._idle_listen_running:
                LiveAudioEngine._stereo_fill_mono(
                    outdata, np.zeros(nf, dtype=np.float32)
                )
                return
            with lock:
                if want_mic_monitor and q_mic:
                    chunk_m = q_mic.popleft()
                else:
                    chunk_m = np.zeros(nf, dtype=np.float32)
                if want_rx_listen and q_listen:
                    chunk_r = q_listen.popleft()
                else:
                    chunk_r = np.zeros(nf, dtype=np.float32)
            ls = weak_self._idle_listen_snap
            ym = LiveAudioEngine._fit_mono_to_frames(chunk_m, nf)
            yrx = LiveAudioEngine._fit_mono_to_frames(chunk_r, nf)
            gv_mon = np.float32(max(0.0, min(2.0, float(ls.output_gain))))
            mix = (ym + yrx) * gv_mon
            np.clip(mix, -1.0, 1.0, out=mix)
            weak_self.funk_meter_db = float(-120.0)
            weak_self.monitor_meter_db = LiveAudioEngine._mono_peak_dbfs(mix)
            LiveAudioEngine._stereo_fill_mono(outdata, mix)

        try:
            snap = LiveSettings.from_dict(live.to_dict())
            self._idle_listen_snap = snap
            self._mic_preview_snap = LiveSettings.from_dict(snap.to_dict())
            self._reset_funk_listen_gate()
            self._idle_listen_running = True
            self._mic_preview_running = bool(want_mic_meter)

            if in_mic is not None:
                istream_mic = sd.InputStream(
                    device=in_mic,
                    channels=in_ch_mic,
                    dtype="float32",
                    samplerate=sr,
                    blocksize=bs,
                    latency=_SPLIT_STREAM_LATENCY,
                    callback=input_cb_idle_mic,
                )
                self._stream_idle_mic_in = istream_mic
                istream_mic.start()

            if in_listen is not None:
                istream_rx = sd.InputStream(
                    device=in_listen,
                    channels=in_ch_l,
                    dtype="float32",
                    samplerate=sr,
                    blocksize=bs,
                    latency=_SPLIT_STREAM_LATENCY,
                    callback=input_cb_idle_listen,
                )
                self._stream_idle_listen_in = istream_rx
                istream_rx.start()

            if want_monitor_out and out_mon is not None:
                ostream = sd.OutputStream(
                    device=out_mon,
                    channels=out_ch_mon,
                    dtype="float32",
                    samplerate=sr,
                    blocksize=bs,
                    latency=_SPLIT_STREAM_LATENCY,
                    callback=output_cb_idle,
                )
                self._stream_idle_mon_out = ostream
                ostream.start()

            return True, ""
        except BaseException as exc:
            self.stop_idle_listen_monitor()
            return False, self._friendly_sd_error(exc)

    def start_mic_preview_monitor(self, live: LiveSettings) -> tuple[bool, str]:
        """Nur Pegel — wird vom Idle‑Monitor abgedeckt; Fallback ohne Monitor‑Gerät."""
        if self._idle_listen_running:
            return True, ""
        ok0, msg0 = self.prerequisites_ok()
        if not ok0:
            return False, msg0
        assert sd is not None

        live.clamp_recursive()
        mic_sid = str(live.input_device_id or "").strip()
        if not mic_sid:
            self.stop_mic_preview_monitor()
            return True, ""

        allowed_in = tuple(x for x, _ in self._enumerate_input_indices())
        in_dev = LiveAudioEngine._parse_input_device_index(
            mic_sid,
            allowed_in,
            saved_label=str(live.input_device_label or ""),
        )
        if in_dev is None:
            self.stop_mic_preview_monitor()
            return False, "Mic‑Vorschau: ungültiges PC‑Mikrofon."

        self.stop_mic_preview_monitor()

        in_ch = max(1, int(self._in_channels_hint(in_dev)))
        sr, bs = self._resolve_stream_params(
            live,
            mic_dev=in_dev,
            monitor_dev=None,
        )
        live = LiveSettings.from_dict(live.to_dict())
        live.samplerate = int(round(sr))
        live.blocksize = int(bs)

        dsp = self._get_dsp()
        tpl = tuple(
            LiveEqBandSettings.from_dict(b.to_dict()) for b in live.eq_bands
        )
        dsp.reset(float(sr), tpl)

        import numpy as np

        weak_self = self

        def input_cb_preview(
            indata: object,
            frames: int,
            time_info: object,
            status: object,
        ) -> None:
            del time_info
            if not weak_self._mic_preview_running:
                return
            nf_f = int(frames)
            if _portaudio_cb_status_problematic(status):
                safe_in = np.zeros((nf_f, in_ch), dtype=np.float32)
            else:
                safe_in = np.asarray(indata, dtype=np.float32)
            ls = weak_self._mic_preview_snap
            weak_self.mic_meter_db = weak_self._mic_dsp_peak_dbfs(safe_in, ls, nf_f)

        try:
            istream = sd.InputStream(
                device=in_dev,
                channels=in_ch,
                dtype="float32",
                samplerate=sr,
                blocksize=bs,
                latency="low",
                callback=input_cb_preview,
            )
            self._mic_preview_snap = LiveSettings.from_dict(live.to_dict())
            self._stream_mic_preview_in = istream
            self._mic_preview_running = True
            istream.start()
            return True, ""
        except BaseException as exc:
            self.stop_mic_preview_monitor()
            return False, self._friendly_sd_error(exc)

    def start(self, live: LiveSettings) -> tuple[bool, str]:
        ok, msg = self.prerequisites_ok()
        if not ok:
            return False, msg
        assert sd is not None

        try:
            import numpy  # noqa: F401
        except ModuleNotFoundError:
            return (
                False,
                "numpy fehlt. Bitte:\n"
                f'  "{sys.executable}" -m pip install numpy',
            )

        self.stop_idle_listen_monitor()
        self.stop_mic_preview_monitor()

        live.clamp_recursive()
        allowed_in = tuple(x for x, _ in self._enumerate_input_indices())
        allowed_out = tuple(x for x, _ in self._enumerate_output_indices())
        in_dev = LiveAudioEngine._parse_input_device_index(
            str(live.input_device_id or ""),
            allowed_in,
            saved_label=str(live.input_device_label or ""),
        )
        out_mon = LiveAudioEngine._parse_output_device_index(
            str(live.output_device_id or ""),
            allowed_out,
            saved_label=str(live.output_device_label or ""),
        )
        funk_sid = str(live.funk_output_device_id or "").strip()
        out_funk = (
            LiveAudioEngine._parse_output_device_index(
                funk_sid,
                allowed_out,
                saved_label=str(live.funk_output_device_label or ""),
            )
            if funk_sid
            else None
        )
        if physical_same_output(out_funk, out_mon):
            out_funk = None
        listen_sid = str(live.funk_listen_input_device_id or "").strip()
        listen_on = bool(live.funk_listen_enabled)
        in_listen = (
            LiveAudioEngine._parse_input_device_index(
                listen_sid,
                allowed_in,
                saved_label=str(live.funk_listen_input_device_label or ""),
            )
            if (listen_on and listen_sid)
            else None
        )
        if in_listen is not None and physical_same_input(in_listen, in_dev):
            in_listen = None
        in_dev, out_mon, out_funk, in_listen = LiveAudioEngine._apply_stream_device_remapping(
            in_dev,
            out_mon,
            funk_out_dev=out_funk,
            listen_in_dev=in_listen,
        )
        want_duplex = out_funk is None and in_listen is None

        if self._running:
            self.stop()

        sr, bs = self._resolve_stream_params(
            live,
            mic_dev=in_dev,
            monitor_dev=out_mon,
            funk_out_dev=out_funk,
            listen_in_dev=in_listen,
        )
        live = LiveSettings.from_dict(live.to_dict())
        live.samplerate = int(round(sr))
        live.blocksize = int(bs)

        dsp = self._get_dsp()
        tpl = tuple(
            LiveEqBandSettings.from_dict(b.to_dict()) for b in live.eq_bands
        )
        dsp.reset(float(sr), tpl)

        self._boxed = live

        duplex_err: Optional[BaseException] = None

        self._reset_funk_listen_gate()
        self._running = True

        if want_duplex:
            rin, rout = resolve_duplex_device_indices(in_dev, out_mon)
            try:
                self._start_duplex(live, in_dev=rin, out_dev=rout, sr=float(sr))
                return True, ""
            except BaseException as exc:
                duplex_err = exc
                self._stop_open_streams_safe()

        try:
            self._start_split_streams(
                live,
                in_dev=in_dev,
                in_listen=in_listen,
                out_mon=out_mon,
                out_funk=out_funk,
                sr=float(sr),
            )
            return True, ""
        except BaseException as exc_split:
            self._stop_open_streams_safe()
            self._running = False
            dup_ms = ""
            if duplex_err is not None:
                dup_ms = self._friendly_sd_error(duplex_err)
            sp_ms = self._friendly_sd_error(exc_split)
            trail_parts: list[str] = []
            if out_funk is not None:
                trail_parts.append("Funk")
            if in_listen is not None:
                trail_parts.append("2. Aufnahme")
            trail = (
                " / " + " / ".join(trail_parts) + ")."
                if trail_parts
                else ")."
            )
            full = (
                "Live‑Audio konnte nicht starten "
                "(Duplex / getrennte Streams mit Monitor"
                + trail
                + "\n\n"
                + (f"Duplex: {dup_ms or '–'}\n\n" if want_duplex else "")
                + f"Streaming: {sp_ms}"
            )
            if self._on_error is not None:
                try:
                    self._on_error(full)
                except Exception:
                    pass
            return False, full

    @staticmethod
    def _parse_input_device_index(
        raw_sid: str,
        allowed_ids: Sequence[str],
        *,
        saved_label: str = "",
    ) -> Optional[int]:
        rem, _ = remap_live_device_id(
            str(raw_sid or "").strip(),
            saved_label,
            input_device=True,
        )
        sid = rem.strip()
        if not sid:
            return None
        return parse_device_id(sid, tuple(allowed_ids))

    @staticmethod
    def _parse_output_device_index(
        raw_sid: str,
        allowed_ids: Sequence[str],
        *,
        saved_label: str = "",
    ) -> Optional[int]:
        rem, _ = remap_live_device_id(
            str(raw_sid or "").strip(),
            saved_label,
            input_device=False,
        )
        sid = rem.strip()
        if not sid:
            return None
        return parse_device_id(sid, tuple(allowed_ids))

    @classmethod
    def _same_physical_input_output(
        cls,
        in_dev: Optional[int],
        out_dev: Optional[int],
    ) -> bool:
        if in_dev is None or out_dev is None or not _HAVE_SD or sd is None:
            return False
        try:
            di = sd.query_devices(in_dev, "input")
            do = sd.query_devices(out_dev, "output")
            ki = _norm_group_key(str(di.get("name", "")))
            ko = _norm_group_key(str(do.get("name", "")))
            return bool(ki) and ki == ko
        except Exception:
            return False

    @classmethod
    def _same_physical_mic_monitor(
        cls,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
    ) -> bool:
        return cls._same_physical_input_output(mic_dev, monitor_dev)

    @classmethod
    def _pa_endpoint_siblings(cls, dev: int, *, for_input: bool) -> list[int]:
        """Alle PortAudio-Indizes desselben logischen Geräts (Host-API-Varianten)."""
        assert sd is not None
        try:
            if for_input:
                ref = sd.query_devices(dev, "input")
                need = "max_input_channels"
            else:
                ref = sd.query_devices(dev, "output")
                need = "max_output_channels"
            key = _norm_group_key(str(ref.get("name", "")))
        except Exception:
            return [dev]
        if not key:
            return [dev]

        out: list[int] = []
        for i, d in enumerate(sd.query_devices()):
            try:
                if int(d.get(need) or 0) <= 0:
                    continue
                if _norm_group_key(str(d.get("name", ""))) != key:
                    continue
                out.append(i)
            except Exception:
                continue
        return out or [dev]

    @classmethod
    def _max_working_samplerate(
        cls,
        blocksize: int,
        checks_in: Sequence[tuple[Optional[int], int]],
        checks_out: Sequence[tuple[Optional[int], int]],
    ) -> float:
        working = cls._working_samplerates(blocksize, checks_in, checks_out)
        return float(working[0]) if working else 0.0

    @classmethod
    def _remap_endpoint_sibling(
        cls,
        dev: Optional[int],
        *,
        for_input: bool,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> Optional[int]:
        """USB/BT: besseren PortAudio-Index wählen (WASAPI/MME/…), wenn die Rate passt."""
        if dev is None or not _HAVE_SD or sd is None:
            return dev
        bs = int(DEFAULT_BLOCKSIZE)
        siblings = cls._pa_endpoint_siblings(int(dev), for_input=for_input)
        if len(siblings) <= 1:
            return dev

        def _checks_with(candidate: int) -> tuple[
            list[tuple[Optional[int], int]], list[tuple[Optional[int], int]]
        ]:
            cin, cout = cls._stream_checks(
                mic_dev=mic_dev,
                monitor_dev=monitor_dev,
                funk_out_dev=funk_out_dev,
                listen_in_dev=listen_in_dev,
            )
            if for_input:
                cin = [
                    (candidate if d == dev else d, ch)
                    for d, ch in cin
                ]
            else:
                cout = [
                    (candidate if d == dev else d, ch)
                    for d, ch in cout
                ]
            return list(cin), list(cout)

        all_d = sd.query_devices()
        best_dev = dev
        # Host-API zuerst (WASAPI wie Qt/Audio-Player), dann Samplerate, dann
        # ursprüngliche Auswahl — verhindert stumme WDM-KS-Wahl trotz höherer Rate.
        best_score: Optional[tuple[int, float, int]] = None

        for cand in siblings:
            try:
                cin, cout = _checks_with(cand)
            except (OSError, ValueError):
                continue
            max_sr = cls._max_working_samplerate(bs, cin, cout)
            if max_sr <= 0:
                continue
            try:
                info = all_d[cand]
                rank = int(_hostapi_rank(_hostapi_name_for_device(sd, info)))
            except Exception:
                rank = 9
            score = (rank, -max_sr, abs(cand - int(dev)))
            if best_score is None or score < best_score:
                best_score = score
                best_dev = cand

        return best_dev

    @classmethod
    def _remap_same_device_io_pair(
        cls,
        in_dev: Optional[int],
        out_dev: Optional[int],
        *,
        mic_dev: Optional[int] = None,
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[int]]:
        """Gleiches Gerät (BT/USB): Ein-/Ausgangs-PA-Index mit gemeinsamer Samplerate."""
        if not cls._same_physical_input_output(in_dev, out_dev):
            return in_dev, out_dev
        assert sd is not None

        try:
            di = sd.query_devices(in_dev, "input")
            key = _norm_group_key(str(di.get("name", "")))
        except Exception:
            return in_dev, out_dev
        if not key:
            return in_dev, out_dev

        all_d = sd.query_devices()
        ins: list[tuple[int, int]] = []
        outs: list[tuple[int, int]] = []
        for i, d in enumerate(all_d):
            try:
                gk = _norm_group_key(str(d.get("name", "")))
                if gk != key:
                    continue
                if int(d.get("max_input_channels") or 0) > 0:
                    ins.append((i, int(d.get("hostapi", -1))))
                if int(d.get("max_output_channels") or 0) > 0:
                    outs.append((i, int(d.get("hostapi", -1))))
            except Exception:
                continue
        if not ins or not outs:
            return in_dev, out_dev

        bs = int(DEFAULT_BLOCKSIZE)
        best: Optional[tuple[float, int, float, int]] = None
        best_pair: Optional[tuple[int, int]] = None

        extra_ins: list[tuple[Optional[int], int]] = []
        extra_outs: list[tuple[Optional[int], int]] = []
        if mic_dev is not None and mic_dev != in_dev:
            extra_ins.append(
                (mic_dev, max(1, int(cls._in_channels_hint(mic_dev))))
            )
        if listen_in_dev is not None and listen_in_dev != in_dev:
            extra_ins.append(
                (
                    listen_in_dev,
                    max(1, int(cls._in_channels_hint(listen_in_dev))),
                )
            )
        if funk_out_dev is not None:
            extra_outs.append(
                (
                    funk_out_dev,
                    max(1, int(cls._out_channels_hint(funk_out_dev))),
                )
            )

        for i_in, h_in in ins:
            in_ch = max(1, int(cls._in_channels_hint(i_in)))
            for i_out, h_out in outs:
                out_ch = max(1, int(cls._out_channels_hint(i_out)))
                api_rank = min(
                    _hostapi_rank(_hostapi_name_for_device(sd, all_d[i_in])),
                    _hostapi_rank(_hostapi_name_for_device(sd, all_d[i_out])),
                )
                if h_in == h_out:
                    api_rank -= 0.05
                for sr in _STANDARD_LIVE_SAMPLERATES:
                    if not cls._stream_params_ok(
                        sr,
                        in_dev=i_in,
                        in_ch=in_ch,
                        out_dev=None,
                        out_ch=1,
                        blocksize=bs,
                    ):
                        continue
                    if not cls._stream_params_ok(
                        sr,
                        in_dev=None,
                        in_ch=1,
                        out_dev=i_out,
                        out_ch=out_ch,
                        blocksize=bs,
                    ):
                        continue
                    ok = True
                    for dev, ch in extra_ins:
                        if not cls._stream_params_ok(
                            sr,
                            in_dev=dev,
                            in_ch=ch,
                            out_dev=None,
                            out_ch=1,
                            blocksize=bs,
                        ):
                            ok = False
                            break
                    if ok:
                        for dev, ch in extra_outs:
                            if not cls._stream_params_ok(
                                sr,
                                in_dev=None,
                                in_ch=1,
                                out_dev=dev,
                                out_ch=ch,
                                blocksize=bs,
                            ):
                                ok = False
                                break
                    if not ok:
                        continue
                    same_api = 1 if h_in == h_out else 0
                    cand = (
                        float(sr),
                        same_api,
                        -float(api_rank),
                        -(i_in + i_out),
                    )
                    if best is None or cand > best:
                        best = cand
                        best_pair = (i_in, i_out)
                    break

        if best is None or best_pair is None:
            return in_dev, out_dev
        if best_pair[0] == in_dev and best_pair[1] == out_dev:
            return in_dev, out_dev
        return best_pair

    @classmethod
    def _apply_stream_device_remapping(
        cls,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        *,
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """PortAudio-Indizes für gemeinsame Samplerate (BT/USB-Mithören, Monitor, Funk)."""
        mic_dev, monitor_dev = cls._remap_same_device_io_pair(
            mic_dev,
            monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )
        listen_in_dev, monitor_dev = cls._remap_same_device_io_pair(
            listen_in_dev,
            monitor_dev,
            mic_dev=mic_dev,
            funk_out_dev=funk_out_dev,
        )
        for _pass in range(2):
            if listen_in_dev is not None:
                listen_in_dev = cls._remap_endpoint_sibling(
                    listen_in_dev,
                    for_input=True,
                    mic_dev=mic_dev,
                    monitor_dev=monitor_dev,
                    funk_out_dev=funk_out_dev,
                    listen_in_dev=listen_in_dev,
                )
            if monitor_dev is not None:
                monitor_dev = cls._remap_endpoint_sibling(
                    monitor_dev,
                    for_input=False,
                    mic_dev=mic_dev,
                    monitor_dev=monitor_dev,
                    funk_out_dev=funk_out_dev,
                    listen_in_dev=listen_in_dev,
                )
            if funk_out_dev is not None:
                funk_out_dev = cls._remap_endpoint_sibling(
                    funk_out_dev,
                    for_input=False,
                    mic_dev=mic_dev,
                    monitor_dev=monitor_dev,
                    funk_out_dev=funk_out_dev,
                    listen_in_dev=listen_in_dev,
                )
            if mic_dev is not None:
                mic_dev = cls._remap_endpoint_sibling(
                    mic_dev,
                    for_input=True,
                    mic_dev=mic_dev,
                    monitor_dev=monitor_dev,
                    funk_out_dev=funk_out_dev,
                    listen_in_dev=listen_in_dev,
                )
        return mic_dev, monitor_dev, funk_out_dev, listen_in_dev

    @classmethod
    def _remap_mic_monitor_indices(
        cls,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        *,
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[int]]:
        mic_dev, monitor_dev, _, _ = cls._apply_stream_device_remapping(
            mic_dev,
            monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )
        return mic_dev, monitor_dev

    @classmethod
    def _live_device_indices(
        cls,
        live: LiveSettings,
    ) -> dict[str, Optional[int]]:
        allowed_in = tuple(x for x, _ in cls._enumerate_input_indices())
        allowed_out = tuple(x for x, _ in cls._enumerate_output_indices())
        mic_sid = str(live.input_device_id or "").strip()
        mon_sid = str(live.output_device_id or "").strip()
        in_dev = (
            cls._parse_input_device_index(
                mic_sid,
                allowed_in,
                saved_label=str(live.input_device_label or ""),
            )
            if mic_sid
            else None
        )
        out_mon = (
            cls._parse_output_device_index(
                mon_sid,
                allowed_out,
                saved_label=str(live.output_device_label or ""),
            )
            if mon_sid
            else None
        )
        funk_sid = str(live.funk_output_device_id or "").strip()
        out_funk = (
            cls._parse_output_device_index(
                funk_sid,
                allowed_out,
                saved_label=str(live.funk_output_device_label or ""),
            )
            if funk_sid
            else None
        )
        listen_sid = str(live.funk_listen_input_device_id or "").strip()
        in_listen = (
            cls._parse_input_device_index(
                listen_sid,
                allowed_in,
                saved_label=str(live.funk_listen_input_device_label or ""),
            )
            if (bool(live.funk_listen_enabled) and listen_sid)
            else None
        )
        mic_dev, out_mon, out_funk, in_listen = cls._apply_stream_device_remapping(
            in_dev,
            out_mon,
            funk_out_dev=out_funk,
            listen_in_dev=in_listen,
        )
        return {
            "mic_dev": mic_dev,
            "monitor_dev": out_mon,
            "funk_out_dev": out_funk,
            "listen_in_dev": in_listen,
        }

    @classmethod
    def preview_stream_params(cls, live: LiveSettings) -> tuple[int, int]:
        """Effektive Samplerate und Blockgröße für die UI (Windows + PortAudio)."""
        if not _HAVE_SD:
            ref = LiveSettings.from_dict(live.to_dict())
            return int(ref.samplerate), int(ref.blocksize)
        ref = LiveSettings.from_dict(live.to_dict())
        devs = cls._live_device_indices(ref)
        sr, bs = cls._resolve_stream_params(ref, **devs)
        return int(round(sr)), int(bs)

    @classmethod
    def preview_samplerate(cls, live: LiveSettings) -> int:
        """Effektive Samplerate für die UI (Windows-Gerät + PortAudio-Check)."""
        return cls.preview_stream_params(live)[0]

    @staticmethod
    def _device_default_samplerate(device_index: Optional[int]) -> Optional[float]:
        if not _HAVE_SD or sd is None or device_index is None:
            return None
        try:
            info = sd.query_devices(device_index)
            if isinstance(info, dict):
                dsr = float(info.get("default_samplerate", 0) or 0)
                if dsr > 0:
                    return dsr
        except Exception:
            pass
        return None

    @staticmethod
    def _stream_params_ok(
        sr: float,
        *,
        in_dev: Optional[int],
        in_ch: int,
        out_dev: Optional[int],
        out_ch: int,
        blocksize: int,
    ) -> bool:
        if not _HAVE_SD or sd is None:
            return False
        # sounddevice.check_*_settings unterstützt kein blocksize (z. B. 0.5.x).
        _ = blocksize
        try:
            if in_dev is not None:
                sd.check_input_settings(
                    device=in_dev,
                    channels=in_ch,
                    dtype="float32",
                    samplerate=float(sr),
                )
            if out_dev is not None:
                sd.check_output_settings(
                    device=out_dev,
                    channels=out_ch,
                    dtype="float32",
                    samplerate=float(sr),
                )
            return True
        except Exception:
            return False

    @staticmethod
    def _device_low_latency_seconds(device_index: Optional[int], kind: str) -> float:
        if not _HAVE_SD or sd is None or device_index is None:
            return 0.0
        try:
            info = sd.query_devices(device_index, kind)  # type: ignore[arg-type]
            if not isinstance(info, dict):
                return 0.0
            if kind == "input":
                lat = float(info.get("default_low_input_latency") or 0.0)
            else:
                lat = float(info.get("default_low_output_latency") or 0.0)
            return max(0.0, lat)
        except Exception:
            return 0.0

    @classmethod
    def _stream_checks(
        cls,
        *,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> tuple[list[tuple[Optional[int], int]], list[tuple[Optional[int], int]]]:
        checks_in: list[tuple[Optional[int], int]] = []
        checks_out: list[tuple[Optional[int], int]] = []
        if mic_dev is not None:
            checks_in.append((mic_dev, max(1, cls._in_channels_hint(mic_dev))))
        if listen_in_dev is not None:
            checks_in.append(
                (listen_in_dev, max(1, cls._in_channels_hint(listen_in_dev)))
            )
        if monitor_dev is not None:
            checks_out.append(
                (monitor_dev, max(1, cls._out_channels_hint(monitor_dev)))
            )
        if funk_out_dev is not None:
            checks_out.append(
                (funk_out_dev, max(1, cls._out_channels_hint(funk_out_dev)))
            )
        return checks_in, checks_out

    @classmethod
    def _streams_ok_at(
        cls,
        sr: float,
        blocksize: int,
        checks_in: Sequence[tuple[Optional[int], int]],
        checks_out: Sequence[tuple[Optional[int], int]],
    ) -> bool:
        for in_dev, in_ch in checks_in:
            if not cls._stream_params_ok(
                sr,
                in_dev=in_dev,
                in_ch=in_ch,
                out_dev=None,
                out_ch=2,
                blocksize=blocksize,
            ):
                return False
        for out_dev, out_ch in checks_out:
            if not cls._stream_params_ok(
                sr,
                in_dev=None,
                in_ch=1,
                out_dev=out_dev,
                out_ch=out_ch,
                blocksize=blocksize,
            ):
                return False
        return True

    @classmethod
    def _latency_blocksize_hint(
        cls,
        sr: float,
        checks_in: Sequence[tuple[Optional[int], int]],
        checks_out: Sequence[tuple[Optional[int], int]],
    ) -> Optional[int]:
        max_lat = 0.0
        for dev, _ch in checks_in:
            max_lat = max(max_lat, cls._device_low_latency_seconds(dev, "input"))
        for dev, _ch in checks_out:
            max_lat = max(max_lat, cls._device_low_latency_seconds(dev, "output"))
        if max_lat <= 0.0 or sr <= 0.0:
            return None
        raw = int(round(max_lat * float(sr)))
        allowed = sorted(DEFAULT_BLOCKSIZES_ALLOWED)
        return min(allowed, key=lambda b: abs(b - raw))

    @classmethod
    def _resolve_blocksize(
        cls,
        live: LiveSettings,
        *,
        sr: float,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> int:
        """Erste passende Blockgröße: 256 → Treiber-Hint → gespeichert → 512 → 128."""
        checks_in, checks_out = cls._stream_checks(
            mic_dev=mic_dev,
            monitor_dev=monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )
        if not checks_in and not checks_out:
            return int(live.blocksize or DEFAULT_BLOCKSIZE)

        seen: set[int] = set()
        candidates: list[int] = []

        def _add(value: int) -> None:
            bs = int(value)
            if bs not in DEFAULT_BLOCKSIZES_ALLOWED or bs in seen:
                return
            seen.add(bs)
            candidates.append(bs)

        _add(DEFAULT_BLOCKSIZE)
        hint = cls._latency_blocksize_hint(sr, checks_in, checks_out)
        if hint is not None:
            _add(hint)
        saved = int(live.blocksize or 0)
        if saved in DEFAULT_BLOCKSIZES_ALLOWED:
            _add(saved)
        _add(512)
        _add(128)

        for bs in candidates:
            if cls._streams_ok_at(sr, bs, checks_in, checks_out):
                return bs
        return int(DEFAULT_BLOCKSIZE)

    @classmethod
    def _resolve_stream_params(
        cls,
        live: LiveSettings,
        *,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> tuple[float, int]:
        probe = LiveSettings.from_dict(live.to_dict())
        probe.blocksize = int(DEFAULT_BLOCKSIZE)
        sr = cls._resolve_samplerate(
            probe,
            mic_dev=mic_dev,
            monitor_dev=monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )
        bs = cls._resolve_blocksize(
            probe,
            sr=sr,
            mic_dev=mic_dev,
            monitor_dev=monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )
        probe.blocksize = bs
        sr = cls._resolve_samplerate(
            probe,
            mic_dev=mic_dev,
            monitor_dev=monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )
        return float(sr), int(bs)

    @classmethod
    def _working_samplerates(
        cls,
        blocksize: int,
        checks_in: Sequence[tuple[Optional[int], int]],
        checks_out: Sequence[tuple[Optional[int], int]],
        *,
        extra_candidates: Sequence[float] = (),
    ) -> list[float]:
        """Alle Sampleraten (absteigend), die für alle Ein-/Ausgänge gültig sind."""
        seen: set[float] = set()
        probe: list[float] = []

        def _queue(value: float) -> None:
            vf = float(value)
            if vf <= 0 or vf in seen:
                return
            seen.add(vf)
            probe.append(vf)

        for sr in extra_candidates:
            _queue(sr)
        for sr in _STANDARD_LIVE_SAMPLERATES:
            _queue(sr)

        working = [
            sr
            for sr in probe
            if cls._streams_ok_at(sr, blocksize, checks_in, checks_out)
        ]
        working.sort(reverse=True)
        return working

    @classmethod
    def _resolve_samplerate(
        cls,
        live: LiveSettings,
        *,
        mic_dev: Optional[int],
        monitor_dev: Optional[int],
        funk_out_dev: Optional[int] = None,
        listen_in_dev: Optional[int] = None,
    ) -> float:
        """Höchste Samplerate, die für alle gewählten Geräte gültig ist (z. B. 16 kHz BT)."""
        bs = int(live.blocksize)
        checks_in, checks_out = cls._stream_checks(
            mic_dev=mic_dev,
            monitor_dev=monitor_dev,
            funk_out_dev=funk_out_dev,
            listen_in_dev=listen_in_dev,
        )

        extras: list[float] = []
        for wr in windows_samplerate_hints_for_live(live):
            extras.append(float(wr))
        for dev, _ch in checks_in:
            dsr = cls._device_default_samplerate(dev)
            if dsr is not None:
                extras.append(dsr)
        for dev, _ch in checks_out:
            dsr = cls._device_default_samplerate(dev)
            if dsr is not None:
                extras.append(dsr)
        extras.append(float(live.samplerate))

        working = cls._working_samplerates(
            bs, checks_in, checks_out, extra_candidates=extras
        )
        if working:
            return float(working[0])

        if checks_in and not checks_out:
            in_only = [
                sr
                for sr in cls._working_samplerates(
                    bs, checks_in, (), extra_candidates=extras
                )
            ]
            if in_only:
                return float(in_only[0])

        for dev, _ch in checks_in + checks_out:
            dsr = cls._device_default_samplerate(dev)
            if dsr is not None and dsr > 0:
                return float(dsr)

        return float(live.samplerate)

    @staticmethod
    def _friendly_sd_error(exc: BaseException) -> str:
        s = str(exc).strip() or type(exc).__name__
        low = s.lower()
        if "samplerate" in low or "sample rate" in low:
            hint = (
                " Tipp: USB-/Bluetooth-Kopfhörer haben oft mehrere Windows-Profile "
                "(WASAPI/MME) mit unterschiedlichen Sampleraten — Geräte neu laden "
                "oder anderen Monitor-Eintrag wählen."
            )
            return (
                f"Gerät unterstützt diese Samplerate/Blockkonfiguration nicht: {s}"
                f"{hint}"
            )
        if "device" in low or "channels" in low:
            return f"Geräteproblem (Eingang/Ausgang oder Kanäle): {s}"
        return f"Konnte nicht starten: {s}"

    @staticmethod
    def _enumerate_input_indices() -> list[tuple[str, str]]:
        from live.live_devices import list_input_devices

        return [(did, lbl) for did, lbl, _tip in list_input_devices() if did]

    @staticmethod
    def _enumerate_output_indices() -> list[tuple[str, str]]:
        from live.live_devices import list_output_devices

        return [(did, lbl) for did, lbl, _tip in list_output_devices() if did]

    @staticmethod
    def _in_channels_hint(device: Optional[int]) -> int:
        if not _HAVE_SD or sd is None:
            return 1
        try:
            info = sd.query_devices(device=device, kind="input")
            mx = max(1, int(info.get("max_input_channels") or 1))
            return min(mx, 2)
        except Exception:
            return 1

    @staticmethod
    def _out_channels_hint(device: Optional[int]) -> int:
        if not _HAVE_SD or sd is None:
            return 2
        try:
            info = sd.query_devices(device=device, kind="output")
            mx = max(1, int(info.get("max_output_channels") or 2))
            return min(mx, 2)
        except Exception:
            return 2

    def stop(self) -> None:
        self._running = False
        self._stop_open_streams_safe()
        self._reset_live_strip_meters()


__all__ = ["LiveAudioEngine"]
