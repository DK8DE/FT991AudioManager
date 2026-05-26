"""Tests für Live-Geräte (Qt-GUID persistent, PortAudio zur Laufzeit)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from live.live_devices import (
    _disambiguate_device_rows,
    _looks_like_legacy_pa_index,
    _migrate_legacy_pa_index_to_qt_id,
    _pa_index_from_saved_label,
    physical_same_input,
    physical_same_output,
    remap_live_device_id,
    remap_live_settings_devices,
    resolve_live_pa_index,
)
from model.live_settings import LiveSettings


def test_disambiguate_duplicate_labels() -> None:
    sd = MagicMock()
    entries = [
        ("guid-a", "USB Audio CODEC", "tip-a", 10),
        ("guid-b", "USB Audio CODEC", "tip-b", 22),
    ]
    out = _disambiguate_device_rows(entries, sd)
    assert out[0][1] != out[1][1]
    assert "[PA #10]" in out[0][1]
    assert "[PA #22]" in out[1][1]


def test_legacy_pa_index_detection() -> None:
    assert _looks_like_legacy_pa_index("27") is True
    assert _looks_like_legacy_pa_index("{0.0.1.x}") is False


def test_migrate_legacy_pa_index_by_label() -> None:
    with patch(
        "audio.qt_device_resolve.remap_qt_device_id",
        return_value=("guid-mic", "Mikrofon (USB)"),
    ):
        qid, lbl = _migrate_legacy_pa_index_to_qt_id(
            "27", "Mikrofon (USB)", input_device=True
        )
    assert qid == "guid-mic"
    assert lbl == "Mikrofon (USB)"


def test_remap_live_uses_qt_resolver() -> None:
    with patch(
        "audio.qt_device_resolve.remap_qt_device_id",
        return_value=("guid-out", "Out 1-2 (MOTU)"),
    ):
        dev_id, label = remap_live_device_id(
            "guid-old",
            "Out 1-2 (MOTU)",
            input_device=False,
        )
    assert dev_id == "guid-out"
    assert label == "Out 1-2 (MOTU)"


def test_remap_legacy_numeric_to_qt_guid() -> None:
    with patch(
        "live.live_devices._migrate_legacy_pa_index_to_qt_id",
        return_value=("guid-new", "In 1-2 (MOTU)"),
    ):
        dev_id, label = remap_live_device_id(
            "27",
            "In 1-2 (MOTU)",
            input_device=True,
        )
    assert dev_id == "guid-new"
    assert label == "In 1-2 (MOTU)"


def test_resolve_live_pa_index_from_qt_guid() -> None:
    with patch(
        "live.live_devices.remap_live_device_id",
        return_value=("guid-in", "In 1-2 (MOTU)"),
    ), patch(
        "live.live_devices._qt_to_pa_map",
        return_value={"guid-in": 27},
    ):
        pa = resolve_live_pa_index("guid-in", "In 1-2 (MOTU)", input_device=True)
    assert pa == 27


def test_remap_live_settings_migrates_numeric_ids() -> None:
    live = LiveSettings(
        input_device_id="33",
        input_device_label="Mikrofon (USB Audio CODEC)",
        output_device_id="24",
        output_device_label="Kopfhörer (USB Audio CODEC)",
    )

    def _fake_remap(saved_id: str, saved_label: str, *, input_device: bool):
        if saved_id.isdigit():
            return (
                f"guid-{'in' if input_device else 'out'}-{saved_id}",
                saved_label,
            )
        return saved_id, saved_label

    with patch("live.live_devices.remap_live_device_id", side_effect=_fake_remap):
        changed = remap_live_settings_devices(live)

    assert changed is True
    assert live.input_device_id == "guid-in-33"
    assert live.output_device_id == "guid-out-24"


def test_physical_same_output_only_by_pa_index() -> None:
    """Ähnliche Gerätenamen dürfen Funk- und Monitor-Ausgang nicht zusammenlegen."""
    with patch(
        "live.live_devices.coerce_output_pa_index",
        side_effect=lambda x: x,
    ):
        assert physical_same_output(16, 17) is False
        assert physical_same_output(16, 16) is True


def test_physical_same_input_only_by_pa_index() -> None:
    with patch(
        "live.live_devices.coerce_input_pa_index",
        side_effect=lambda x: x,
    ):
        assert physical_same_input(20, 21) is False
        assert physical_same_input(20, 20) is True


def test_pa_index_from_saved_label() -> None:
    assert _pa_index_from_saved_label("USB Audio CODEC [PA #22, WASAPI]") == 22
    assert _pa_index_from_saved_label("USB Audio CODEC") is None


def test_resolve_live_pa_index_uses_pa_hint_from_label() -> None:
    with patch(
        "live.live_devices.remap_live_device_id",
        return_value=("guid-missing", "USB Audio CODEC"),
    ), patch(
        "live.live_devices._qt_to_pa_map",
        return_value={},
    ), patch(
        "live.live_devices._validate_pa_index_for_direction",
        return_value=True,
    ):
        pa = resolve_live_pa_index(
            "guid-missing",
            "USB Audio CODEC [PA #22, WASAPI]",
            input_device=False,
        )
    assert pa == 22
