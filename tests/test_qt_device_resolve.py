"""Tests für Qt-Geräte-Remapping (USB-Port / Windows-Anzeigename)."""

from __future__ import annotations

from unittest.mock import patch

from audio.qt_device_resolve import remap_qt_device_id


def test_remap_exact_id_still_valid() -> None:
    rows = [
        ("", "System-Standard"),
        ("guid-new", "Lautsprecher (USB Audio CODEC)"),
    ]
    with patch("audio.qt_device_resolve.list_qt_audio_devices", return_value=rows):
        dev_id, label = remap_qt_device_id(
            "guid-new",
            "Lautsprecher (2- USB Audio CODEC)",
            input_device=False,
        )
    assert dev_id == "guid-new"
    assert label == "Lautsprecher (USB Audio CODEC)"


def test_remap_usb_port_suffix_in_name() -> None:
    rows = [
        ("", "System-Standard"),
        ("guid-after-replug", "Lautsprecher (2- USB Audio CODEC)"),
    ]
    with patch("audio.qt_device_resolve.list_qt_audio_devices", return_value=rows):
        dev_id, label = remap_qt_device_id(
            "guid-old",
            "Lautsprecher (USB Audio CODEC)",
            input_device=False,
        )
    assert dev_id == "guid-after-replug"
    assert "USB Audio CODEC" in label


def test_remap_empty_when_no_match() -> None:
    rows = [("", "System-Standard"), ("other", "Realtek Audio")]
    with patch("audio.qt_device_resolve.list_qt_audio_devices", return_value=rows):
        dev_id, label = remap_qt_device_id(
            "missing-guid",
            "Totally Different Device",
            input_device=True,
        )
    assert dev_id == "missing-guid"
    assert label == "Totally Different Device"
