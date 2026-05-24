"""Tests für PortAudio-Live-Geräte-Remapping."""

from __future__ import annotations

from unittest.mock import patch

from live.live_devices import remap_live_device_id, remap_live_settings_devices
from model.live_settings import LiveSettings


def test_remap_live_exact_id_still_valid() -> None:
    rows = [
        ("", "System-Standard", ""),
        ("24", "Kopfhörer (USB Audio CODEC)", ""),
    ]
    with patch("live.live_devices.list_output_devices", return_value=rows):
        dev_id, label = remap_live_device_id(
            "24",
            "Kopfhörer (USB Audio CODEC)",
            input_device=False,
        )
    assert dev_id == "24"
    assert label == "Kopfhörer (USB Audio CODEC)"


def test_remap_live_by_saved_label_after_index_shift() -> None:
    rows = [
        ("", "System-Standard", ""),
        ("32", "Lautsprecher (2- USB Audio CODEC)", ""),
    ]
    with patch("live.live_devices.list_output_devices", return_value=rows):
        dev_id, label = remap_live_device_id(
            "33",
            "Lautsprecher (USB Audio CODEC)",
            input_device=False,
        )
    assert dev_id == "32"
    assert "USB Audio CODEC" in label


def test_remap_live_clears_stale_id_without_match() -> None:
    rows = [("", "System-Standard", ""), ("5", "Realtek Audio", "")]
    with patch("live.live_devices.list_input_devices", return_value=rows):
        dev_id, label = remap_live_device_id(
            "99",
            "",
            input_device=True,
        )
    assert dev_id == ""
    assert label == ""


def test_remap_live_settings_updates_all_roles() -> None:
    live = LiveSettings(
        input_device_id="33",
        input_device_label="Mikrofon (USB Audio CODEC)",
        output_device_id="24",
        output_device_label="Kopfhörer (USB Audio CODEC)",
        funk_output_device_id="32",
        funk_output_device_label="Lautsprecher (USB Audio CODEC)",
        funk_listen_input_device_id="25",
        funk_listen_input_device_label="Line In (USB Audio CODEC)",
    )
    in_rows = [
        ("", "System-Standard", ""),
        ("10", "Mikrofon (2- USB Audio CODEC)", ""),
        ("11", "Line In (2- USB Audio CODEC)", ""),
    ]
    out_rows = [
        ("", "System-Standard", ""),
        ("20", "Kopfhörer (2- USB Audio CODEC)", ""),
        ("21", "Lautsprecher (2- USB Audio CODEC)", ""),
    ]

    def _in() -> list[tuple[str, str, str]]:
        return in_rows

    def _out() -> list[tuple[str, str, str]]:
        return out_rows

    with patch("live.live_devices.list_input_devices", side_effect=_in), patch(
        "live.live_devices.list_output_devices", side_effect=_out
    ):
        changed = remap_live_settings_devices(live)

    assert changed is True
    assert live.input_device_id == "10"
    assert live.funk_listen_input_device_id == "11"
    assert live.output_device_id == "20"
    assert live.funk_output_device_id == "21"
