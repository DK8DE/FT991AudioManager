"""Tests für FavoritesStore / favorites.json."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from model.favorites_store import (
    FavoritesStore,
    RadioFavorite,
    format_favorite_combo_label,
)


class FavoritesStoreTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "favorites.json"
            a = RadioFavorite(
                name="Relais A",
                frequency_hz=145_600_000,
                mode="FM",
                eq_profile_name="FM",
                squelch=15,
                af_gain=128,
                rf_gain=120,
                pc_power_watts=25,
            )
            b = RadioFavorite(
                name="USB",
                frequency_hz=14_229_000,
                mode="USB",
                eq_profile_name="SSB-Default",
                squelch=0,
                af_gain=200,
                rf_gain=255,
                pc_power_watts=100,
            )
            store = FavoritesStore(path=path, favorites=[a, b])
            store.save()

            loaded = FavoritesStore.load(path)
            self.assertEqual(len(loaded.favorites), 2)
            self.assertEqual(loaded.favorites[0].name, "Relais A")
            self.assertEqual(loaded.favorites[1].pc_power_watts, 100)

    def test_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "favorites.json"
            path.write_text('{"version": 1, "favorites": []}', encoding="utf-8")
            loaded = FavoritesStore.load(path)
            self.assertEqual(loaded.favorites, [])

    def test_duplicate_name_on_upsert(self) -> None:
        path = Path("__noop_favorites__")
        store = FavoritesStore(
            path=path,
            favorites=[
                RadioFavorite(
                    name="A",
                    frequency_hz=100,
                    mode="USB",
                    eq_profile_name="",
                    squelch=0,
                    af_gain=0,
                    rf_gain=0,
                    pc_power_watts=0,
                ),
            ],
        )
        dup = RadioFavorite(
            name="A",
            frequency_hz=200,
            mode="LSB",
            eq_profile_name="",
            squelch=0,
            af_gain=0,
            rf_gain=0,
            pc_power_watts=0,
        )
        with self.assertRaises(ValueError):
            store.upsert(dup)

    def test_replace_index(self) -> None:
        path = Path("__noop_favorites__")
        store = FavoritesStore(
            path=path,
            favorites=[
                RadioFavorite(
                    name="A",
                    frequency_hz=100,
                    mode="USB",
                    eq_profile_name="",
                    squelch=0,
                    af_gain=0,
                    rf_gain=0,
                    pc_power_watts=0,
                ),
                RadioFavorite(
                    name="B",
                    frequency_hz=200,
                    mode="LSB",
                    eq_profile_name="",
                    squelch=0,
                    af_gain=0,
                    rf_gain=0,
                    pc_power_watts=0,
                ),
            ],
        )
        new = RadioFavorite(
            name="A",
            frequency_hz=999,
            mode="FM",
            eq_profile_name="x",
            squelch=5,
            af_gain=1,
            rf_gain=2,
            pc_power_watts=50,
        )
        store.upsert(new, replace_index=0)
        self.assertEqual(store.favorites[0].frequency_hz, 999)
        self.assertEqual(store.favorites[1].name, "B")

    def test_format_combo_label(self) -> None:
        fav = RadioFavorite(
            name="Test",
            frequency_hz=145_600_000,
            mode="FM",
            eq_profile_name="",
            squelch=0,
            af_gain=0,
            rf_gain=0,
            pc_power_watts=0,
        )
        self.assertEqual(format_favorite_combo_label(fav), "Test (145.600 MHz)")


if __name__ == "__main__":
    unittest.main()
