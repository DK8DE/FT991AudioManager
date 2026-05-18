"""Tests: FLRig/Hamlib-Modusstrings → Yaesu-MD (Rig-Bridge-Normalisierung)."""

from __future__ import annotations

import unittest

from mapping.rx_mapping import RxMode
from rig_bridge.cat_commands import _normalize_hamlib_mode_name
from rig_bridge.ft991_backend import _bridge_mode_to_rx_mode


class FlrigModeAliasTest(unittest.TestCase):
    def test_normalize_flrig_style_names(self) -> None:
        self.assertEqual(_normalize_hamlib_mode_name("USB-D1"), "PKTUSB")
        self.assertEqual(_normalize_hamlib_mode_name("LSB-D1"), "PKTLSB")
        self.assertEqual(_normalize_hamlib_mode_name("DATA-USB"), "PKTUSB")
        self.assertEqual(_normalize_hamlib_mode_name("PKT-U"), "PKTUSB")
        self.assertEqual(_normalize_hamlib_mode_name("PKT-L"), "PKTLSB")
        self.assertEqual(_normalize_hamlib_mode_name("RTTY-U"), "RTTYR")
        self.assertEqual(_normalize_hamlib_mode_name("RTTY-L"), "RTTY")
        self.assertEqual(_normalize_hamlib_mode_name("CWU"), "CW")
        self.assertEqual(_normalize_hamlib_mode_name("CWL"), "CWR")
        self.assertEqual(_normalize_hamlib_mode_name("NFM"), "FMN")

    def test_bridge_maps_to_ft991_rx_modes(self) -> None:
        self.assertIs(_bridge_mode_to_rx_mode("USB-D1"), RxMode.DATA_USB)
        self.assertIs(_bridge_mode_to_rx_mode("LSB-d1"), RxMode.DATA_LSB)
        self.assertIs(_bridge_mode_to_rx_mode("LSB"), RxMode.LSB)
        self.assertIs(_bridge_mode_to_rx_mode("USB"), RxMode.USB)
        self.assertIs(_bridge_mode_to_rx_mode("DATA-USB"), RxMode.DATA_USB)
        self.assertIs(_bridge_mode_to_rx_mode("DIGU"), RxMode.DATA_USB)
        self.assertIs(_bridge_mode_to_rx_mode("DIGL"), RxMode.DATA_LSB)
        self.assertIs(_bridge_mode_to_rx_mode("PKT"), RxMode.DATA_USB)
        self.assertIs(_bridge_mode_to_rx_mode("PKT-U"), RxMode.DATA_USB)
        self.assertIs(_bridge_mode_to_rx_mode("RTTY-U"), RxMode.RTTY_USB)
        self.assertIs(_bridge_mode_to_rx_mode("RTTY-L"), RxMode.RTTY_LSB)
        self.assertIs(_bridge_mode_to_rx_mode("CWU"), RxMode.CW_U)
        self.assertIs(_bridge_mode_to_rx_mode("CWL"), RxMode.CW_L)
        self.assertIs(_bridge_mode_to_rx_mode("NFM"), RxMode.FM_N)
