"""Tests für Rig-Bridge-Einstellungen."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model import AppSettings
from model.rig_bridge_settings import (
    HamlibBridgeSettings,
    HamlibListenerSettings,
    RigBridgeSettings,
)
from rig_bridge.manager import normalize_rig_bridge_config


class RigBridgeSettingsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = RigBridgeSettings()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.flrig.port, 12345)
        self.assertEqual(len(cfg.hamlib.listeners), 1)
        self.assertEqual(cfg.hamlib.listeners[0].port, 4532)

    def test_hamlib_multiple_listeners_roundtrip(self) -> None:
        ham = HamlibBridgeSettings(
            enabled=True,
            listeners=[
                HamlibListenerSettings(host="127.0.0.1", port=4532, name="A"),
                HamlibListenerSettings(host="0.0.0.0", port=4533, name="B"),
            ],
        )
        raw = ham.to_dict()
        loaded = HamlibBridgeSettings.from_dict(raw)
        self.assertEqual(len(loaded.listeners), 2)
        self.assertEqual(loaded.listeners[1].host, "0.0.0.0")
        self.assertEqual(loaded.listeners[1].port, 4533)

    def test_empty_listeners_list_preserved(self) -> None:
        ham = HamlibBridgeSettings(enabled=True, listeners=[])
        loaded = HamlibBridgeSettings.from_dict(ham.to_dict())
        self.assertEqual(loaded.listeners, [])

    def test_legacy_single_port_migrates(self) -> None:
        loaded = HamlibBridgeSettings.from_dict(
            {"enabled": True, "host": "192.168.1.10", "port": 4999}
        )
        self.assertEqual(len(loaded.listeners), 1)
        self.assertEqual(loaded.listeners[0].host, "192.168.1.10")
        self.assertEqual(loaded.listeners[0].port, 4999)

    def test_normalize_hamlib_listeners(self) -> None:
        raw = {
            "enabled": True,
            "hamlib": {
                "listeners": [
                    {"host": "127.0.0.1", "port": 4532},
                    {"host": "::1", "port": 4533, "name": "Test"},
                ]
            },
        }
        n = normalize_rig_bridge_config(raw)
        self.assertTrue(n["enabled"])
        self.assertEqual(n["hamlib"]["listeners"][1]["port"], 4533)
        self.assertEqual(n["hamlib"]["listeners"][1]["host"], "::1")

    def test_app_settings_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = AppSettings()
            s.rig_bridge.enabled = True
            s.rig_bridge.flrig.port = 12346
            s.rig_bridge.hamlib.listeners = [
                HamlibListenerSettings("127.0.0.1", 4532, "Lokal"),
                HamlibListenerSettings("0.0.0.0", 4534, "LAN"),
            ]
            s.save(path)
            loaded = AppSettings.load(path)
            self.assertTrue(loaded.rig_bridge.enabled)
            self.assertEqual(loaded.rig_bridge.flrig.port, 12346)
            self.assertEqual(len(loaded.rig_bridge.hamlib.listeners), 2)
            self.assertEqual(loaded.rig_bridge.hamlib.listeners[1].port, 4534)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("rig_bridge", data)
            self.assertEqual(
                data["rig_bridge"]["hamlib"]["listeners"][0]["name"],
                "Lokal",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
