"""Typisierter Container für die erweiterten Audio-Einstellungen (Version 0.5).

Wird als Feld ``extended`` in :class:`AudioProfile` geführt. Defaults
liegen bewusst auf "neutral", damit ein altes Profil ohne Extended-Block
nach Defaultisierung nichts Unerwartetes ans Gerät schreibt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Union

from mapping.extended_mapping import (
    CARRIER_LEVEL_DEFAULT,
    DATA_TX_LEVEL_DEFAULT,
    MicSource,
    SsbSlope,
)


FreqValue = Union[str, int]


@dataclass
class ExtendedSettings:
    # SSB-Cut (RX-Klangformung)
    ssb_lcut_freq: FreqValue = "OFF"
    ssb_lcut_slope: str = SsbSlope.DB6.value
    ssb_hcut_freq: FreqValue = "OFF"
    ssb_hcut_slope: str = SsbSlope.DB6.value

    # Carrier-Level
    am_carrier_level: int = CARRIER_LEVEL_DEFAULT
    fm_carrier_level: int = CARRIER_LEVEL_DEFAULT

    # Mikrofon-Wahl
    am_mic_sel: str = MicSource.MIC.value
    fm_mic_sel: str = MicSource.MIC.value

    # DATA
    data_tx_level: int = DATA_TX_LEVEL_DEFAULT

    # ------------------------------------------------------------------
    # Serialisierung
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ssb_lcut_freq": _serialize_freq(self.ssb_lcut_freq),
            "ssb_lcut_slope": str(self.ssb_lcut_slope),
            "ssb_hcut_freq": _serialize_freq(self.ssb_hcut_freq),
            "ssb_hcut_slope": str(self.ssb_hcut_slope),
            "am_carrier_level": int(self.am_carrier_level),
            "fm_carrier_level": int(self.fm_carrier_level),
            "am_mic_sel": str(self.am_mic_sel),
            "fm_mic_sel": str(self.fm_mic_sel),
            "data_tx_level": int(self.data_tx_level),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtendedSettings":
        if not isinstance(data, dict):
            return cls()
        return cls(
            ssb_lcut_freq=_parse_freq(data.get("ssb_lcut_freq"), "OFF"),
            ssb_lcut_slope=str(data.get("ssb_lcut_slope") or SsbSlope.DB6.value),
            ssb_hcut_freq=_parse_freq(data.get("ssb_hcut_freq"), "OFF"),
            ssb_hcut_slope=str(data.get("ssb_hcut_slope") or SsbSlope.DB6.value),
            am_carrier_level=_coerce_int(data.get("am_carrier_level"), CARRIER_LEVEL_DEFAULT),
            fm_carrier_level=_coerce_int(data.get("fm_carrier_level"), CARRIER_LEVEL_DEFAULT),
            am_mic_sel=_normalize_mic(data.get("am_mic_sel")),
            fm_mic_sel=_normalize_mic(data.get("fm_mic_sel")),
            data_tx_level=_coerce_int(data.get("data_tx_level"), DATA_TX_LEVEL_DEFAULT),
        )

    # ------------------------------------------------------------------
    # Bequemer Zugriff für Worker
    # ------------------------------------------------------------------

    def as_keyed_dict(self) -> Dict[str, Any]:
        """Liefert das ``key -> value``-Dict, das der Worker an
        :func:`FT991CAT.write_extended` weiterreichen kann."""
        return {
            "ssb_lcut_freq": self.ssb_lcut_freq,
            "ssb_lcut_slope": self.ssb_lcut_slope,
            "ssb_hcut_freq": self.ssb_hcut_freq,
            "ssb_hcut_slope": self.ssb_hcut_slope,
            "am_carrier_level": self.am_carrier_level,
            "fm_carrier_level": self.fm_carrier_level,
            "am_mic_sel": self.am_mic_sel,
            "fm_mic_sel": self.fm_mic_sel,
            "data_tx_level": self.data_tx_level,
        }

    def apply_keyed_dict(self, values: Dict[str, Any]) -> None:
        """Umgekehrte Richtung — bekommt das vom Worker gelesene Dict."""
        for key, value in values.items():
            if not hasattr(self, key):
                continue
            setattr(self, key, value)


# ----------------------------------------------------------------------
# Helfer
# ----------------------------------------------------------------------


def _serialize_freq(value: FreqValue) -> Any:
    if isinstance(value, str):
        return value.upper()
    return int(value)


def _parse_freq(value: Any, default: FreqValue) -> FreqValue:
    if value is None:
        return default
    if isinstance(value, str):
        return value.upper() or default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_mic(value: Any) -> str:
    if value is None:
        return MicSource.MIC.value
    s = str(value).upper()
    if s in (MicSource.MIC.value, MicSource.REAR.value):
        return s
    return MicSource.MIC.value
