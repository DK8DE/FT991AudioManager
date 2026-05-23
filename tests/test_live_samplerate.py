"""Tests für automatische Live-Samplerate und Blockgröße."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from live.live_audio_engine import LiveAudioEngine
from model.live_settings import DEFAULT_BLOCKSIZE, LiveSettings


class LiveSamplerateTest(unittest.TestCase):
    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_streams_ok_at", return_value=True)
    @patch(
        "live.live_audio_engine.windows_samplerate_hints_for_live",
        return_value=[44100.0],
    )
    @patch.object(LiveAudioEngine, "_device_default_samplerate", return_value=48000.0)
    def test_resolve_uses_highest_working_rate(
        self,
        _mock_pa: MagicMock,
        _mock_win: MagicMock,
        _mock_streams: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
    ) -> None:
        live = LiveSettings()
        live.samplerate = 48000
        sr = LiveAudioEngine._resolve_samplerate(
            live,
            mic_dev=1,
            monitor_dev=2,
        )
        self.assertEqual(sr, 96000.0)

    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_streams_ok_at")
    @patch(
        "live.live_audio_engine.windows_samplerate_hints_for_live",
        return_value=[],
    )
    @patch.object(LiveAudioEngine, "_device_default_samplerate", return_value=48000.0)
    def test_resolve_falls_back_to_portaudio_default(
        self,
        _mock_pa: MagicMock,
        _mock_win: MagicMock,
        mock_ok: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
    ) -> None:
        def _ok(sr: float, _bs: int, *_a: object, **_k: object) -> bool:
            return float(sr) == 48000.0

        mock_ok.side_effect = _ok
        live = LiveSettings()
        live.samplerate = 44100
        sr = LiveAudioEngine._resolve_samplerate(
            live,
            mic_dev=1,
            monitor_dev=2,
        )
        self.assertEqual(sr, 48000.0)


class LiveBtSamplerateTest(unittest.TestCase):
    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_streams_ok_at")
    @patch(
        "live.live_audio_engine.windows_samplerate_hints_for_live",
        return_value=[48000.0, 16000.0],
    )
    @patch.object(LiveAudioEngine, "_device_default_samplerate")
    def test_bt_mic_falls_back_to_16k(
        self,
        _mock_pa: MagicMock,
        _mock_win: MagicMock,
        mock_ok: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
    ) -> None:
        def _ok(sr: float, _bs: int, checks_in: object, checks_out: object) -> bool:
            rate = float(sr)
            if rate == 48000.0:
                return not checks_in
            if rate == 16000.0:
                return True
            return False

        mock_ok.side_effect = _ok
        live = LiveSettings()
        live.samplerate = 48000
        sr = LiveAudioEngine._resolve_samplerate(
            live,
            mic_dev=1,
            monitor_dev=2,
        )
        self.assertEqual(sr, 16000.0)

    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_streams_ok_at")
    @patch(
        "live.live_audio_engine.windows_samplerate_hints_for_live",
        return_value=[44100.0],
    )
    def test_prefers_highest_working_rate(
        self,
        _mock_win: MagicMock,
        mock_ok: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
    ) -> None:
        mock_ok.side_effect = lambda sr, _bs, *_a, **_k: float(sr) in (
            48000.0,
            44100.0,
            16000.0,
        )
        live = LiveSettings()
        sr = LiveAudioEngine._resolve_samplerate(
            live,
            mic_dev=1,
            monitor_dev=2,
        )
        self.assertEqual(sr, 48000.0)


class LiveBtRemapTest(unittest.TestCase):
    @patch.object(LiveAudioEngine, "_same_physical_input_output", return_value=True)
    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_stream_params_ok")
    @patch("live.live_audio_engine._hostapi_name_for_device", return_value="Windows WASAPI")
    @patch("live.live_audio_engine._hostapi_rank", return_value=0)
    @patch("live.live_audio_engine.sd")
    def test_remaps_wasapi_split_to_shared_rate_pair(
        self,
        mock_sd: MagicMock,
        _mock_rank: MagicMock,
        _mock_api: MagicMock,
        mock_ok: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
        _mock_same: MagicMock,
    ) -> None:
        all_devices: list[dict[str, object] | None] = [None] * 40
        for idx, inp, out in ((28, 1, 0), (26, 0, 2), (1, 1, 0), (8, 0, 2)):
            all_devices[idx] = {
                "name": "Kopfhörer (TK-HS004)",
                "hostapi": 0,
                "max_input_channels": inp,
                "max_output_channels": out,
            }

        def _query(dev: object | None = None, kind: object | None = None) -> object:
            del kind
            if dev is None:
                return all_devices
            return all_devices[int(dev)]

        mock_sd.query_devices.side_effect = _query

        def _ok(
            sr: float,
            *,
            in_dev: object,
            in_ch: int,
            out_dev: object,
            out_ch: int,
            blocksize: int,
        ) -> bool:
            del in_ch, out_ch, blocksize
            rate = float(sr)
            if in_dev == 28 and out_dev is None:
                return rate == 16000.0
            if out_dev == 26 and in_dev is None:
                return rate == 48000.0
            if in_dev == 1 and out_dev is None:
                return rate in (48000.0, 44100.0)
            if out_dev == 8 and in_dev is None:
                return rate in (48000.0, 44100.0)
            return False

        mock_ok.side_effect = _ok
        mic, mon = LiveAudioEngine._remap_mic_monitor_indices(28, 26)
        self.assertEqual((mic, mon), (1, 8))


class LiveEndpointRemapRankTest(unittest.TestCase):
    @patch.object(LiveAudioEngine, "_max_working_samplerate")
    @patch.object(LiveAudioEngine, "_pa_endpoint_siblings")
    @patch("live.live_audio_engine._hostapi_name_for_device")
    @patch("live.live_audio_engine.sd")
    def test_prefers_wasapi_over_wdm_when_both_work(
        self,
        mock_sd: MagicMock,
        mock_api: MagicMock,
        mock_siblings: MagicMock,
        mock_max_sr: MagicMock,
    ) -> None:
        mock_siblings.return_value = [5, 12]
        all_devices: list[dict[str, object] | None] = [None] * 13
        all_devices[5] = {
            "name": "Speakers (Realtek)",
            "hostapi": 0,
            "max_output_channels": 2,
        }
        all_devices[12] = {
            "name": "Speakers (Realtek)",
            "hostapi": 1,
            "max_output_channels": 2,
        }

        def _query(dev: object | None = None, kind: object | None = None) -> object:
            del kind
            if dev is None:
                return all_devices
            return all_devices[int(dev)]

        mock_sd.query_devices.side_effect = _query

        def _api(_sd: object, info: dict) -> str:
            return "Windows WASAPI" if int(info.get("hostapi", 0)) == 0 else "Windows WDM-KS"

        mock_api.side_effect = _api

        def _max_sr(_bs: int, _cin: object, cout: object) -> float:
            outs = [d for d, _ch in cout]
            if 5 in outs:
                return 48000.0
            if 12 in outs:
                return 96000.0
            return 0.0

        mock_max_sr.side_effect = _max_sr
        mon = LiveAudioEngine._remap_endpoint_sibling(
            5,
            for_input=False,
            mic_dev=1,
            monitor_dev=5,
        )
        self.assertEqual(mon, 5)


class LiveListenRemapTest(unittest.TestCase):
    @patch.object(LiveAudioEngine, "_max_working_samplerate")
    @patch.object(LiveAudioEngine, "_pa_endpoint_siblings")
    def test_remaps_usb_monitor_for_listen(
        self,
        mock_siblings: MagicMock,
        mock_max_sr: MagicMock,
    ) -> None:
        mock_siblings.side_effect = lambda dev, **_: [dev, dev + 100]

        def _max_sr(_bs: int, cin: object, cout: object) -> float:
            outs = [d for d, _ch in cout]
            if 5 in [d for d, _ch in cin] and 300 in outs:
                return 48000.0
            if 5 in [d for d, _ch in cin] and 200 in outs:
                return 0.0
            return 0.0

        mock_max_sr.side_effect = _max_sr
        _mic, mon, _funk, listen = LiveAudioEngine._apply_stream_device_remapping(
            None,
            200,
            listen_in_dev=5,
        )
        self.assertEqual(listen, 5)
        self.assertEqual(mon, 300)


class LiveBlocksizeTest(unittest.TestCase):
    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_streams_ok_at")
    @patch.object(LiveAudioEngine, "_latency_blocksize_hint", return_value=128)
    def test_resolve_prefers_latency_hint(
        self,
        _mock_hint: MagicMock,
        mock_ok: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
    ) -> None:
        mock_ok.side_effect = lambda _sr, bs, *_a, **_k: bs == 128
        live = LiveSettings()
        bs = LiveAudioEngine._resolve_blocksize(
            live,
            sr=48000.0,
            mic_dev=1,
            monitor_dev=2,
        )
        self.assertEqual(bs, 128)

    @patch.object(LiveAudioEngine, "_in_channels_hint", return_value=1)
    @patch.object(LiveAudioEngine, "_out_channels_hint", return_value=2)
    @patch.object(LiveAudioEngine, "_streams_ok_at")
    @patch.object(LiveAudioEngine, "_latency_blocksize_hint", return_value=None)
    def test_resolve_falls_back_to_default(
        self,
        _mock_hint: MagicMock,
        mock_ok: MagicMock,
        _mock_out_ch: MagicMock,
        _mock_in_ch: MagicMock,
    ) -> None:
        mock_ok.side_effect = lambda _sr, bs, *_a, **_k: bs == DEFAULT_BLOCKSIZE
        live = LiveSettings()
        bs = LiveAudioEngine._resolve_blocksize(
            live,
            sr=48000.0,
            mic_dev=1,
            monitor_dev=2,
        )
        self.assertEqual(bs, DEFAULT_BLOCKSIZE)


if __name__ == "__main__":
    unittest.main()
