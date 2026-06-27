"""Tests für Amateurband-Erkennung."""

from __future__ import annotations

import unittest

from mapping.amateur_bands import (
    VFO_BAND_CHOICE,
    amateur_band_for_hz,
    cb_all_channel_frequencies_hz,
    cb_channel_at_hz,
    cb_channel_frequency_hz,
    combo_entries_high_to_low,
    display_band_at_hz,
    display_band_for_hz,
    display_band_label_at_hz,
    freenet_channel_at_hz,
    is_cb_block_hz,
    is_cb_channels_41_80_hz,
    is_in_amateur_band,
    preferred_voice_rx_mode_for_amateur_hz,
)
from mapping.rx_mapping import RxMode


class AmateurBandsTest(unittest.TestCase):
    def test_hf_in_band(self) -> None:
        self.assertEqual(amateur_band_for_hz(14_250_000), "20 m")
        self.assertTrue(is_in_amateur_band(7_100_000))

    def test_vhf_in_band(self) -> None:
        self.assertEqual(amateur_band_for_hz(145_500_000), "2 m")
        self.assertEqual(amateur_band_for_hz(432_000_000), "70 cm")

    def test_out_of_band(self) -> None:
        self.assertIsNone(amateur_band_for_hz(55_999_400))
        self.assertFalse(is_in_amateur_band(100_000_000))
        self.assertIsNone(display_band_at_hz(55_999_400))

    def test_cb_band(self) -> None:
        band = display_band_at_hz(27_000_000)
        self.assertIsNotNone(band)
        assert band is not None
        self.assertEqual(band.name, "11 m (CB)")
        self.assertEqual(display_band_for_hz(26_965_000), "11 m (CB)")
        self.assertIsNone(amateur_band_for_hz(27_000_000))

    def test_cb_80_channel_block(self) -> None:
        self.assertTrue(is_cb_block_hz(26_855_000))
        self.assertTrue(is_cb_channels_41_80_hz(26_855_000))
        band = display_band_at_hz(26_855_000)
        self.assertIsNotNone(band)
        assert band is not None
        self.assertEqual(band.name, "11 m (CB)")
        self.assertEqual(cb_channel_at_hz(26_565_000), 41)
        self.assertEqual(cb_channel_at_hz(26_955_000), 80)
        self.assertEqual(display_band_label_at_hz(26_865_000), "CB 71")

    def test_cb_all_channels_order(self) -> None:
        freqs = cb_all_channel_frequencies_hz()
        self.assertEqual(len(freqs), 80)
        self.assertEqual(freqs[0], cb_channel_frequency_hz(41))
        self.assertEqual(freqs[39], cb_channel_frequency_hz(80))
        self.assertEqual(freqs[40], cb_channel_frequency_hz(1))
        self.assertEqual(freqs[-1], cb_channel_frequency_hz(40))

    def test_freenet_band(self) -> None:
        band = display_band_at_hz(149_050_000)
        self.assertIsNotNone(band)
        assert band is not None
        self.assertEqual(band.name, "Freenet")
        self.assertEqual(display_band_for_hz(149_087_500), "Freenet")
        self.assertIsNone(amateur_band_for_hz(149_050_000))

    def test_cb_channel_label(self) -> None:
        self.assertEqual(cb_channel_at_hz(26_965_000), 1)
        self.assertEqual(display_band_label_at_hz(26_965_000), "CB 1")
        self.assertEqual(cb_channel_at_hz(27_075_000), 10)
        self.assertEqual(display_band_label_at_hz(27_075_000), "CB 10")
        self.assertEqual(cb_channel_at_hz(27_055_000), 8)
        self.assertEqual(cb_channel_at_hz(27_405_000), 40)

    def test_freenet_channel_label(self) -> None:
        self.assertEqual(freenet_channel_at_hz(149_025_000), 1)
        self.assertEqual(freenet_channel_at_hz(149_062_500), 4)
        self.assertEqual(display_band_label_at_hz(149_025_000), "Freenet 1")
        self.assertEqual(display_band_label_at_hz(149_062_500), "Freenet 4")

    def test_special_band_without_channel_shows_name_only(self) -> None:
        self.assertIsNone(cb_channel_at_hz(27_000_005))
        self.assertEqual(display_band_label_at_hz(27_000_005), "11 m (CB)")

    def test_combo_order_high_to_low(self) -> None:
        entries = combo_entries_high_to_low()
        self.assertEqual(entries[0][1], VFO_BAND_CHOICE)
        centers = [e[1] for e in entries[1:]]
        self.assertEqual(centers, sorted(centers, reverse=True))
        labels = [e[0] for e in entries]
        self.assertIn("70 cm", labels[1])
        self.assertIn("160 m", labels[-1])
        self.assertTrue(any("11 m" in lbl or "CB" in lbl for lbl in labels))
        self.assertTrue(any("Freenet" in lbl for lbl in labels))

    def test_band_combo_target_cb_snaps_to_channel(self) -> None:
        from mapping.amateur_bands import (
            band_at_center_hz,
            band_combo_target_frequency_hz,
            cb_channel_at_hz,
        )

        cb = display_band_at_hz(27_000_000)
        assert cb is not None
        hz = band_combo_target_frequency_hz(cb)
        self.assertIsNotNone(cb_channel_at_hz(hz))
        self.assertEqual(band_at_center_hz(cb.center_hz), cb)

    def test_preferred_voice_mode_cb_freenet_fm(self) -> None:
        from mapping.amateur_bands import preferred_voice_rx_mode_for_hz

        self.assertEqual(preferred_voice_rx_mode_for_hz(27_185_000), RxMode.FM)
        self.assertEqual(preferred_voice_rx_mode_for_hz(149_025_000), RxMode.FM)

    def test_combo_label_format(self) -> None:
        from mapping.amateur_bands import AMATEUR_BANDS_HIGH_TO_LOW

        label = AMATEUR_BANDS_HIGH_TO_LOW[0].combo_label()
        self.assertIn("430.000", label)
        self.assertIn("440.000", label)
        self.assertIn("(70 cm)", label)

    def test_preferred_voice_mode_hf_lsb_usb_fm(self) -> None:
        self.assertEqual(
            preferred_voice_rx_mode_for_amateur_hz(3_600_000), RxMode.LSB
        )
        self.assertEqual(
            preferred_voice_rx_mode_for_amateur_hz(14_200_000), RxMode.USB
        )
        self.assertEqual(
            preferred_voice_rx_mode_for_amateur_hz(51_500_000), RxMode.FM
        )
        self.assertEqual(
            preferred_voice_rx_mode_for_amateur_hz(145_500_000), RxMode.FM
        )
        self.assertIsNone(preferred_voice_rx_mode_for_amateur_hz(99_000_000))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
