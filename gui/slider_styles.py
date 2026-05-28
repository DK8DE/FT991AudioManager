"""Einheitlicher Yaesu-Grün-Stil für Slider (grüner Griff, blauer Füllbereich)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider

from gui.theme import ACCENT_BLUE, SLIDER_INACTIVE

YAESU_GREEN = "#52c41a"
YAESU_GREEN_BORDER = "#1e5c16"
YAESU_SLIDER_GROOVE_RADIUS = "5px"
YAESU_SLIDER_HANDLE_RADIUS = "11px"

YAESU_GREEN_SLIDER_STYLE_HORIZONTAL = (
    f"QSlider::groove:horizontal {{ background-color:{SLIDER_INACTIVE}; height:10px; border-radius:{YAESU_SLIDER_GROOVE_RADIUS}; }}"
    f"QSlider::sub-page:horizontal {{ background-color:{ACCENT_BLUE}; height:10px; border-radius:{YAESU_SLIDER_GROOVE_RADIUS}; }}"
    f"QSlider::add-page:horizontal {{ background-color:{SLIDER_INACTIVE}; height:10px; border-radius:{YAESU_SLIDER_GROOVE_RADIUS}; }}"
    "QSlider::handle:horizontal {"
    f" background-color:{YAESU_GREEN};"
    f" border:1px solid {YAESU_GREEN_BORDER};"
    f" border-radius:{YAESU_SLIDER_HANDLE_RADIUS};"
    " min-width:22px; max-width:22px; min-height:22px; margin:-8px 0;"
    "}"
)

YAESU_GREEN_SLIDER_STYLE_VERTICAL = (
    f"QSlider::groove:vertical {{ background-color:{SLIDER_INACTIVE}; width:10px; border-radius:{YAESU_SLIDER_GROOVE_RADIUS}; }}"
    f"QSlider::add-page:vertical {{ background-color:{ACCENT_BLUE}; width:10px; border-radius:{YAESU_SLIDER_GROOVE_RADIUS}; }}"
    f"QSlider::sub-page:vertical {{ background-color:{SLIDER_INACTIVE}; width:10px; border-radius:{YAESU_SLIDER_GROOVE_RADIUS}; }}"
    "QSlider::handle:vertical {"
    f" background-color:{YAESU_GREEN};"
    f" border:1px solid {YAESU_GREEN_BORDER};"
    f" border-radius:{YAESU_SLIDER_HANDLE_RADIUS};"
    " min-height:22px; max-height:22px; min-width:22px; margin:0 -8px;"
    "}"
)


def apply_yaesu_green_slider_style(slider: QSlider) -> None:
    if slider.orientation() == Qt.Orientation.Horizontal:
        slider.setStyleSheet(YAESU_GREEN_SLIDER_STYLE_HORIZONTAL)
    else:
        slider.setStyleSheet(YAESU_GREEN_SLIDER_STYLE_VERTICAL)
