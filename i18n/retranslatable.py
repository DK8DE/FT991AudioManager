"""Mixin für Widgets/Fenster mit dynamischer Sprachumschaltung."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class RetranslatableMixin:
    """Registriert ``retranslate_ui`` beim globalen ``language_changed``-Signal."""

    def _register_retranslate(self) -> None:
        from i18n import language_manager

        language_manager().language_changed.connect(self._on_language_changed)

    def _on_language_changed(self, _lang: str) -> None:
        retranslate = getattr(self, "retranslate_ui", None)
        if callable(retranslate):
            retranslate()
