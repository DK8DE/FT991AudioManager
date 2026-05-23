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
)
from model.live_settings import LiveEqBandSettings, LiveSettings

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
    from live.live_dsp import LiveDSPChain


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
        self._stream_idle_mon_out: Optional[_SdStream] = None
        self._boxed: LiveSettings = LiveSettings()
        self._dsp: Optional["LiveDSPChain"] = None
        self._running = False
        self._idle_listen_running = False
        self._idle_listen_snap = LiveSettings()

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

    def push_idle_listen_settings(self, live: LiveSettings) -> None:
        """Lautheit (Lin‑Mit) / SR-Anpassungen ohne Streams neu zu starten."""
        if not self._idle_listen_running:
            return
        live.clamp_recursive()
        self._idle_listen_snap = LiveSettings.from_dict(live.to_dict())

    def stop_idle_listen_monitor(self) -> None:
        """Nur Idle‑Monitor stoppen (:meth:`stop` lässt das unberührt)."""
        self._idle_listen_running = False
        self.funk_listen_meter_db = float(-120.0)
        self.monitor_meter_db = float(-120.0)
        objs: list[_SdStream] = []
        for attr in ("_stream_idle_mon_out", "_stream_idle_listen_in"):
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
    #: Peakanzeige Mikro‑Pfad zum Monitor (inkl. Stummschaltung beim Mithören‑Aus).
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
            mic_trim = LiveAudioEngine._mono_mic_after_input_gain_fit(
                safe_in, ls, nf,
            )
            weak_self.mic_meter_db = LiveAudioEngine._mono_peak_dbfs(mic_trim)

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
            samplerate=float(live.samplerate),
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

    def _start_split_streams(
        self,
        live: LiveSettings,
        *,
        in_dev: Optional[int],
        in_listen: Optional[int],
        out_mon: Optional[int],
        out_funk: Optional[int],
    ) -> None:
        """Eingang‑PC‑Mic (+ optional zweiter Aufnahmegang) → Monitor/Funk‑Ausgang."""
        assert sd is not None
        import numpy as np

        sr = float(live.samplerate)
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
            fit_trim = LiveAudioEngine._mono_mic_after_input_gain_fit(
                safe_in, ls, nf_f,
            )
            weak_self.mic_meter_db = LiveAudioEngine._mono_peak_dbfs(fit_trim)
            y_dsp = weak_self._dsp_mono_without_output_gain(ls, safe_in)
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
            gv_l = np.float32(max(0.0, min(2.0, float(ls.funk_listen_gain))))
            if _portaudio_cb_status_problematic(status):
                raw = np.zeros((nf_i, listen_in_ch), dtype=np.float32)
            else:
                raw = np.asarray(indata, dtype=np.float32)
            if raw.ndim == 2 and raw.shape[1] >= 2:
                mono_rx = np.mean(raw.astype(np.float32), axis=1).astype(np.float32)
            else:
                mono_rx = raw[..., 0].astype(np.float32)
            yr = mono_rx.reshape(-1) * gv_l
            fit_r = LiveAudioEngine._fit_mono_to_frames(yr, nf_i)
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
            latency="low",
            callback=input_cb_main,
        )
        o_mon = sd.OutputStream(
            device=out_mon,
            channels=out_ch_mon,
            dtype="float32",
            samplerate=sr,
            blocksize=bs,
            latency="low",
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
                latency="low",
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
        """Nur Lin‑Mit → Monitor‑Ausgang (DSP/EQ ohne Live‑Mic).

        Wird automatisch beim Wechsel in :meth:`start` gestoppt.
        """
        ok0, msg0 = self.prerequisites_ok()
        if not ok0:
            return False, msg0
        assert sd is not None

        live.clamp_recursive()
        listen_sid = str(live.funk_listen_input_device_id or "").strip()
        mon_sid = str(live.output_device_id or "").strip()
        if not listen_sid or not mon_sid:
            self.stop_idle_listen_monitor()
            return True, ""

        allowed_in = tuple(x for x, _ in self._enumerate_input_indices())
        allowed_out = tuple(x for x, _ in self._enumerate_output_indices())
        in_listen = LiveAudioEngine._parse_input_device_index(
            listen_sid, allowed_in
        )
        out_mon = LiveAudioEngine._parse_output_device_index(mon_sid, allowed_out)
        if in_listen is None or out_mon is None:
            self.stop_idle_listen_monitor()
            return False, (
                "Lin‑Mit Idle‑Monitor: ungültiges Aufnahme- oder Ausgangsgerät."
            )

        self.stop_idle_listen_monitor()

        in_ch_l = max(1, int(self._in_channels_hint(in_listen)))
        out_ch_mon = max(1, int(self._out_channels_hint(out_mon)))
        bs = int(live.blocksize)
        sr = self._resolve_samplerate(
            float(live.samplerate),
            in_dev=in_listen,
            in_ch=in_ch_l,
            out_dev=out_mon,
            out_ch=out_ch_mon,
            blocksize=bs,
        )

        import numpy as np

        weak_self = self
        lock = Lock()
        q_listen: deque[Any] = deque(maxlen=_SPLIT_QUEUE_BLOCKS)

        def input_cb_idle(
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
            gv_l = np.float32(max(0.0, min(2.0, float(ls.funk_listen_gain))))
            if _portaudio_cb_status_problematic(status):
                raw = np.zeros((nf_i, in_ch_l), dtype=np.float32)
            else:
                raw = np.asarray(indata, dtype=np.float32)
            if raw.ndim == 2 and raw.shape[1] >= 2:
                mono_rx = np.mean(raw.astype(np.float32), axis=1).astype(np.float32)
            else:
                mono_rx = raw[..., 0].astype(np.float32)
            yr = mono_rx.reshape(-1) * gv_l
            fit_r = LiveAudioEngine._fit_mono_to_frames(yr, nf_i)
            weak_self.funk_listen_meter_db = LiveAudioEngine._mono_peak_dbfs(fit_r)
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
                chunk_r = (
                    q_listen.popleft()
                    if q_listen
                    else np.zeros(nf, dtype=np.float32)
                )
            ls = weak_self._idle_listen_snap
            gv_mon = np.float32(max(0.0, min(2.0, float(ls.output_gain))))
            yrx = LiveAudioEngine._fit_mono_to_frames(chunk_r, nf)
            y_out = yrx * gv_mon
            np.clip(y_out, -1.0, 1.0, out=y_out)
            weak_self.mic_meter_db = float(-120.0)
            weak_self.funk_meter_db = float(-120.0)
            weak_self.monitor_meter_db = LiveAudioEngine._mono_peak_dbfs(y_out)
            LiveAudioEngine._stereo_fill_mono(outdata, y_out)

        try:
            istream = sd.InputStream(
                device=in_listen,
                channels=in_ch_l,
                dtype="float32",
                samplerate=sr,
                blocksize=bs,
                latency="low",
                callback=input_cb_idle,
            )
            ostream = sd.OutputStream(
                device=out_mon,
                channels=out_ch_mon,
                dtype="float32",
                samplerate=sr,
                blocksize=bs,
                latency="low",
                callback=output_cb_idle,
            )
            self._idle_listen_snap = LiveSettings.from_dict(live.to_dict())
            self._stream_idle_listen_in = istream
            self._stream_idle_mon_out = ostream
            self._idle_listen_running = True
            istream.start()
            ostream.start()
            return True, ""
        except BaseException as exc:
            self.stop_idle_listen_monitor()
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

        live.clamp_recursive()
        allowed_in = tuple(x for x, _ in self._enumerate_input_indices())
        allowed_out = tuple(x for x, _ in self._enumerate_output_indices())
        in_dev = LiveAudioEngine._parse_input_device_index(
            str(live.input_device_id or ""), allowed_in
        )
        out_mon = LiveAudioEngine._parse_output_device_index(
            str(live.output_device_id or ""), allowed_out
        )
        funk_sid = str(live.funk_output_device_id or "").strip()
        out_funk = (
            LiveAudioEngine._parse_output_device_index(funk_sid, allowed_out)
            if funk_sid
            else None
        )
        if physical_same_output(out_funk, out_mon):
            out_funk = None
        listen_sid = str(live.funk_listen_input_device_id or "").strip()
        listen_on = bool(live.funk_listen_enabled)
        in_listen = (
            LiveAudioEngine._parse_input_device_index(listen_sid, allowed_in)
            if (listen_on and listen_sid)
            else None
        )
        if in_listen is not None and physical_same_input(in_listen, in_dev):
            in_listen = None
        want_duplex = out_funk is None and in_listen is None

        if self._running:
            self.stop()

        dsp = self._get_dsp()
        tpl = tuple(
            LiveEqBandSettings.from_dict(b.to_dict()) for b in live.eq_bands
        )
        dsp.reset(float(live.samplerate), tpl)

        self._boxed = LiveSettings.from_dict(live.to_dict())

        duplex_err: Optional[BaseException] = None

        self._running = True

        if want_duplex:
            rin, rout = resolve_duplex_device_indices(in_dev, out_mon)
            try:
                self._start_duplex(live, in_dev=rin, out_dev=rout)
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
    def _parse_input_device_index(raw_sid: str, allowed_ids: Sequence[str]) -> Optional[int]:
        """Gespeicherte Mic‑IDs remappen; Fallback ohne Allowlist statt Std‑Gerät durch ``None``.

        Veraltete PortAudio‑Indizes (nach Dedupe/OS‑Updates) können sonst nicht in der
        „erlaubten“ Liste liefern — dann wählt PortAudio fälschlich den Host‑Standard.
        """
        rem = remap_live_device_id(str(raw_sid or "").strip(), input_device=True).strip()
        sid = rem or str(raw_sid or "").strip()
        tup = tuple(allowed_ids)
        d = parse_device_id(sid, tup)
        if d is None and sid:
            d = parse_device_id(sid, None)
        return d

    @staticmethod
    def _parse_output_device_index(raw_sid: str, allowed_ids: Sequence[str]) -> Optional[int]:
        rem = remap_live_device_id(str(raw_sid or "").strip(), input_device=False).strip()
        sid = rem or str(raw_sid or "").strip()
        tup = tuple(allowed_ids)
        d = parse_device_id(sid, tup)
        if d is None and sid:
            d = parse_device_id(sid, None)
        return d

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
        try:
            if in_dev is not None:
                sd.check_input_settings(
                    device=in_dev,
                    channels=in_ch,
                    dtype="float32",
                    samplerate=float(sr),
                    blocksize=int(blocksize),
                )
            if out_dev is not None:
                sd.check_output_settings(
                    device=out_dev,
                    channels=out_ch,
                    dtype="float32",
                    samplerate=float(sr),
                    blocksize=int(blocksize),
                )
            return True
        except Exception:
            return False

    @classmethod
    def _resolve_samplerate(
        cls,
        preferred: float,
        *,
        in_dev: Optional[int],
        in_ch: int,
        out_dev: Optional[int],
        out_ch: int,
        blocksize: int,
    ) -> float:
        """Erste Samplerate, die für Ein- und Ausgangsgerät gültig ist."""
        seen: set[float] = set()
        candidates: list[float] = []

        def _add(value: float) -> None:
            vf = float(value)
            if vf <= 0 or vf in seen:
                return
            seen.add(vf)
            candidates.append(vf)

        _add(preferred)
        d_in = cls._device_default_samplerate(in_dev)
        if d_in is not None:
            _add(d_in)
        d_out = cls._device_default_samplerate(out_dev)
        if d_out is not None:
            _add(d_out)
        for fallback in (
            48000.0,
            44100.0,
            96000.0,
            32000.0,
            22050.0,
            16000.0,
            8000.0,
        ):
            _add(fallback)

        for sr in candidates:
            if cls._stream_params_ok(
                sr,
                in_dev=in_dev,
                in_ch=in_ch,
                out_dev=out_dev,
                out_ch=out_ch,
                blocksize=blocksize,
            ):
                return sr
        return float(preferred)

    @staticmethod
    def _friendly_sd_error(exc: BaseException) -> str:
        s = str(exc).strip() or type(exc).__name__
        low = s.lower()
        if "samplerate" in low or "sample rate" in low:
            return f"Gerät unterstützt diese Samplerate/Blockkonfiguration nicht: {s}"
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
        info = sd.query_devices(device=device, kind="input")
        mx = max(1, int(info.get("max_input_channels") or 1))
        return min(mx, 2)

    @staticmethod
    def _out_channels_hint(device: Optional[int]) -> int:
        if not _HAVE_SD or sd is None:
            return 2
        info = sd.query_devices(device=device, kind="output")
        mx = max(1, int(info.get("max_output_channels") or 2))
        return min(mx, 2)

    def stop(self) -> None:
        self._running = False
        self._stop_open_streams_safe()
        self._reset_live_strip_meters()


__all__ = ["LiveAudioEngine"]
