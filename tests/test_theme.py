"""Tests für gui.theme — braucht eine QApplication."""

from __future__ import annotations

import sys
import unittest

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from gui.theme import (
    DARK_COLORS,
    LOG_COLORS_DARK,
    LOG_COLORS_LIGHT,
    apply_theme,
    current_log_colors,
    is_dark_mode,
    make_dark_palette,
)


_app: QApplication | None = None


def _ensure_app() -> QApplication:
    global _app
    existing = QApplication.instance()
    if existing is not None:
        return existing
    _app = QApplication(sys.argv[:1])
    return _app


class DarkPaletteTest(unittest.TestCase):
    def test_palette_has_required_colors(self) -> None:
        palette = make_dark_palette()
        self.assertEqual(
            palette.color(QPalette.Window).name().upper(), DARK_COLORS["Window"]
        )
        self.assertEqual(
            palette.color(QPalette.Highlight).name().upper(),
            DARK_COLORS["Highlight"],
        )
        self.assertEqual(
            palette.color(QPalette.HighlightedText).name().upper(),
            DARK_COLORS["HighlightedText"],
        )
        self.assertEqual(
            palette.color(QPalette.BrightText).name().upper(),
            DARK_COLORS["BrightText"],
        )

    def test_dark_colors_match_spec(self) -> None:
        # Sicherstellen, dass die genau spezifizierten Werte erhalten bleiben.
        self.assertEqual(DARK_COLORS["Window"], "#1C1C1C")
        self.assertEqual(DARK_COLORS["WindowText"], "#E1E1E1")
        self.assertEqual(DARK_COLORS["Base"], "#2D2D2D")
        self.assertEqual(DARK_COLORS["AlternateBase"], "#202020")
        self.assertEqual(DARK_COLORS["ToolTipBase"], "#242424")
        self.assertEqual(DARK_COLORS["Button"], "#262626")
        self.assertEqual(DARK_COLORS["BrightText"], "#FF5050")
        self.assertEqual(DARK_COLORS["Link"], "#4496EB")
        self.assertEqual(DARK_COLORS["Highlight"], "#4496EB")
        self.assertEqual(DARK_COLORS["HighlightedText"], "#121212")


class ApplyThemeTest(unittest.TestCase):
    def test_apply_dark_then_light(self) -> None:
        app = _ensure_app()
        apply_theme(app, dark=True)
        self.assertTrue(is_dark_mode())
        self.assertEqual(
            app.palette().color(QPalette.Window).name().upper(),
            DARK_COLORS["Window"],
        )
        self.assertEqual(current_log_colors(), LOG_COLORS_DARK)

        apply_theme(app, dark=False)
        self.assertFalse(is_dark_mode())
        self.assertEqual(current_log_colors(), LOG_COLORS_LIGHT)

    def test_apply_dark_twice_is_idempotent(self) -> None:
        app = _ensure_app()
        apply_theme(app, dark=True)
        apply_theme(app, dark=True)
        self.assertTrue(is_dark_mode())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
