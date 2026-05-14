"""Datenklassen für EQ-Bänder.

Ein EQ-Set besteht aus drei Bändern (LOW, MID, HIGH). Jedes Band hat
Frequenz, Level und Bandbreite (Q). Die Datenklassen sind serialisierbar
zu JSON und passen zum Format aus der Spec.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Union


FreqValue = Union[int, str]
"""Frequenz: ``int`` (Hz) oder ``"OFF"`` (deaktiviertes Band)."""


@dataclass
class EQBand:
    """Ein parametrischer EQ-Band-Eintrag."""

    freq: FreqValue = "OFF"
    level: int = 0
    bw: int = 5

    def to_dict(self) -> Dict[str, Any]:
        return {"freq": self.freq, "level": int(self.level), "bw": int(self.bw)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EQBand":
        freq = data.get("freq", "OFF")
        if isinstance(freq, str) and freq.strip().upper() == "OFF":
            freq = "OFF"
        elif isinstance(freq, (int, float)):
            freq = int(freq)
        return cls(
            freq=freq,
            level=int(data.get("level", 0)),
            bw=int(data.get("bw", 5)),
        )

    def is_off(self) -> bool:
        return isinstance(self.freq, str) and self.freq.upper() == "OFF"


@dataclass
class EQSettings:
    """Komplettes 3-Band-EQ-Set (z. B. Normal-EQ oder Processor-EQ)."""

    eq1: EQBand
    eq2: EQBand
    eq3: EQBand

    @classmethod
    def default(cls) -> "EQSettings":
        return cls(eq1=EQBand(), eq2=EQBand(), eq3=EQBand())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eq1": self.eq1.to_dict(),
            "eq2": self.eq2.to_dict(),
            "eq3": self.eq3.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EQSettings":
        return cls(
            eq1=EQBand.from_dict(data.get("eq1", {})),
            eq2=EQBand.from_dict(data.get("eq2", {})),
            eq3=EQBand.from_dict(data.get("eq3", {})),
        )

    def bands(self):  # iter helper: (band_index, EQBand)
        yield 0, self.eq1
        yield 1, self.eq2
        yield 2, self.eq3
