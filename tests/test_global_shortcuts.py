"""Tests für globale Tastenkürzel-Einstellungen."""

from __future__ import annotations

import unittest

from gui.global_hotkeys_win import modifiers_mask_from_config, vk_from_key_spec
from model.global_shortcuts_settings import GlobalShortcutsSettings


class GlobalShortcutsSettingsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        s = GlobalShortcutsSettings()
        self.assertTrue(s.enabled)
        self.assertEqual(s.modifier_1, "control")
        self.assertEqual(s.modifier_2, "shift")
        self.assertEqual(s.key_contest_play, "P")
        self.assertEqual(s.key_live_ptt_latch, "X")
        self.assertEqual(s.key_live_ptt_momentary, "Y")

    def test_roundtrip_dict(self) -> None:
        s = GlobalShortcutsSettings(
            enabled=False,
            modifier_1="alt",
            modifier_2="none",
            key_contest_play="F5",
        )
        restored = GlobalShortcutsSettings.from_dict(s.to_dict())
        self.assertEqual(restored.enabled, False)
        self.assertEqual(restored.modifier_1, "alt")
        self.assertEqual(restored.modifier_2, "none")
        self.assertEqual(restored.key_contest_play, "F5")

    def test_invalid_key_falls_back(self) -> None:
        s = GlobalShortcutsSettings.from_dict({"key_contest_play": "INVALID"})
        self.assertEqual(s.key_contest_play, "P")


class GlobalHotkeysWinTest(unittest.TestCase):
    def test_vk_from_key_spec(self) -> None:
        self.assertEqual(vk_from_key_spec("P"), ord("P"))
        self.assertEqual(vk_from_key_spec("F1"), 0x70)
        self.assertEqual(vk_from_key_spec("PRIOR"), 0x21)

    def test_modifiers_mask(self) -> None:
        mask = modifiers_mask_from_config(
            {"modifier_1": "control", "modifier_2": "shift"}
        )
        self.assertTrue(mask & 0x0002)
        self.assertTrue(mask & 0x0004)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
