"""Tests für CAT-Fehlerklassifikation (GUI vs. Log)."""

from __future__ import annotations

import unittest

from cat.cat_errors import (
    CatCommandUnsupportedError,
    CatProtocolError,
    CatTimeoutError,
    is_cat_protocol_error,
    is_cat_protocol_error_message,
)


class CatErrorClassificationTest(unittest.TestCase):
    def test_protocol_error_detection(self) -> None:
        self.assertTrue(is_cat_protocol_error(CatProtocolError("x")))
        self.assertTrue(
            is_cat_protocol_error(
                CatCommandUnsupportedError("Befehl '?;'")
            )
        )
        self.assertFalse(is_cat_protocol_error(CatTimeoutError("timeout")))

    def test_protocol_message_markers(self) -> None:
        self.assertTrue(
            is_cat_protocol_error_message(
                "Keine passende Antwort fuer 'SM0;' nach 5 verworfenen Stale-Frames."
            )
        )
        self.assertFalse(is_cat_protocol_error_message("Timeout beim Lesen"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
