"""Parameter für die PO-Meter-Kalibrierung (nur Kurzwelle / 10 m)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from mapping.rx_mapping import RxMode
from mapping.tx_power_mapping import MENU_HF_TX_MAX_POWER, power_steps_watts

# 10 m: FM im SSB-Teil des Bandes (28,0–29,7 MHz)
DEFAULT_HF_TEST_HZ = 28_500_000

TX_ON_SECONDS = 2.0
SETTLE_BEFORE_TX_S = 0.35
SETTLE_AFTER_POWER_S = 0.45
PAUSE_BETWEEN_STEPS_S = 0.4

CAL_BAND_HF_10M = "hf_10m"


@dataclass(frozen=True)
class CalBandSpec:
    band_id: str
    label: str
    freq_hz: int
    mode: RxMode
    power_menu: int
    power_max_w: int

    def power_steps(self) -> List[int]:
        return power_steps_watts(max_w=self.power_max_w)


HF_10M_BAND = CalBandSpec(
    band_id=CAL_BAND_HF_10M,
    label="10 m (KW)",
    freq_hz=DEFAULT_HF_TEST_HZ,
    mode=RxMode.FM,
    power_menu=MENU_HF_TX_MAX_POWER,
    power_max_w=100,
)

CAL_BANDS: Dict[str, CalBandSpec] = {CAL_BAND_HF_10M: HF_10M_BAND}

AUTO_TEST_ORDER: Tuple[str, ...] = (CAL_BAND_HF_10M,)
