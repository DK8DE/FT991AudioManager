"""Tests für Rig-Bridge-Einstellungen."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model import AppSettings
from model.rig_bridge_settings import RigBridgeSettings
from rig_bridge.manager import normalize_rig_bridge_config


class RigBridgeSettingsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = RigBridgeSettings()
        self.assertTrue(cfg.enabled)
        self.assertTrue(cfg.flrig.enabled)
        self.assertTrue(cfg.flrig.autostart)
        self.assertEqual(cfg.flrig.port, 12345)

    def test_normalize_flrig(self) -> None:
        raw = {"enabled": True, "flrig": {"host": "0.0.0.0", "port": 12346}}
        n = normalize_rig_bridge_config(raw)
        self.assertTrue(n["enabled"])
        self.assertEqual(n["flrig"]["port"], 12346)
        self.assertEqual(n["flrig"]["host"], "0.0.0.0")

    def test_legacy_hamlib_in_json_ignored(self) -> None:
        loaded = RigBridgeSettings.from_dict(
            {"enabled": True, "hamlib": {"enabled": True, "port": 4532}}
        )
        self.assertTrue(loaded.flrig.enabled)

    def test_app_settings_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = AppSettings()
            s.rig_bridge.enabled = True
            s.rig_bridge.flrig.port = 12346
            s.save(path)
            loaded = AppSettings.load(path)
            self.assertTrue(loaded.rig_bridge.enabled)
            self.assertEqual(loaded.rig_bridge.flrig.port, 12346)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("rig_bridge", data)
            self.assertIn("flrig", data["rig_bridge"])
            self.assertNotIn("hamlib", data["rig_bridge"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
