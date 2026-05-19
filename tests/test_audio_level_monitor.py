"""Tests für Audio-Pegelüberwachung."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from audio.audio_level_monitor import AudioLevelMonitor
from model.global_audio_settings import ROLE_PC, ROLE_SEND


class AudioLevelMonitorTest(unittest.TestCase):
    def test_poll_emits_peak_for_bound_role(self) -> None:
        monitor = AudioLevelMonitor()
        peaks: dict[str, float] = {}

        def capture(role: str, level: float) -> None:
            peaks[role] = level

        monitor.level_changed.connect(capture)
        monitor.set_role_override(ROLE_SEND, "{0.0.0.00000000}.{test}", capture=False)

        fake_peak = mock.Mock()
        fake_peak.bind.return_value = True
        fake_peak.peak_scalar.return_value = 0.42
        monitor._peaks[ROLE_SEND] = fake_peak

        monitor._poll()

        self.assertAlmostEqual(peaks.get(ROLE_SEND, 0.0), 0.42, places=5)
        fake_peak.bind.assert_called_once()

    @unittest.skipUnless(sys.platform == "win32", "Windows-only")
    def test_peak_available_matches_volume(self) -> None:
        from audio.windows_endpoint_volume import (
            windows_endpoint_peak_available,
            windows_endpoint_volume_available,
        )

        self.assertEqual(
            windows_endpoint_peak_available(),
            windows_endpoint_volume_available(),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
