"""Tests für automatische Tooltip-Zeilenumbrüche."""

from __future__ import annotations

import unittest

from gui.tooltip_text import TOOLTIP_MAX_LINE_LENGTH, format_tooltip


class FormatTooltipTest(unittest.TestCase):
    def test_empty_unchanged(self) -> None:
        self.assertEqual(format_tooltip(""), "")
        self.assertEqual(format_tooltip(None), "")

    def test_short_text_unchanged(self) -> None:
        text = "Kurzer Hinweis"
        self.assertEqual(format_tooltip(text), text)

    def test_wraps_long_line_at_spaces(self) -> None:
        text = (
            "PC-Mikrofon / Line-In — welches Windows-Audiogerät "
            "für die Aufnahme zum Funkgerät genutzt wird."
        )
        wrapped = format_tooltip(text)
        for line in wrapped.split("\n"):
            self.assertLessEqual(len(line), TOOLTIP_MAX_LINE_LENGTH, line)

    def test_preserves_manual_newlines(self) -> None:
        text = "Erste Zeile manuell.\nZweite Zeile manuell mit etwas mehr Text als vierzig."
        wrapped = format_tooltip(text)
        self.assertTrue(wrapped.startswith("Erste Zeile manuell.\n"))
        self.assertGreater(wrapped.count("\n"), 1)

    def test_preserves_blank_line_between_paragraphs(self) -> None:
        text = "Absatz eins.\n\nAbsatz zwei mit mehr Inhalt der umgebrochen werden sollte."
        wrapped = format_tooltip(text)
        self.assertIn("\n\n", wrapped)

    def test_breaks_very_long_word(self) -> None:
        word = "A" * 55
        wrapped = format_tooltip(word)
        for line in wrapped.split("\n"):
            self.assertLessEqual(len(line), TOOLTIP_MAX_LINE_LENGTH)
        self.assertGreater(wrapped.count("\n"), 0)

    def test_idempotent_on_already_wrapped(self) -> None:
        text = (
            "PC-Mikrofon / Line-In — welches Windows-Audiogerät "
            "für die Aufnahme zum Funkgerät genutzt wird."
        )
        once = format_tooltip(text)
        twice = format_tooltip(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
