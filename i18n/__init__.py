"""Internationalisierung für den FT-991/A Audiomanager.

Alle nutzer sichtbaren GUI-Texte liegen in :mod:`i18n.de` und :mod:`i18n.en`.
Zum Abrufen: ``from i18n import tr`` bzw. ``tr("menu.file")``.

Sprache wechseln: ``set_language("en")`` — emittiert ``language_changed``.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

from . import de, en

_SUPPORTED = frozenset({"de", "en"})
_current: str = "de"


class _LanguageManager(QObject):
    language_changed = Signal(str)


_manager = _LanguageManager()


def language_manager() -> _LanguageManager:
    return _manager


def current_language() -> str:
    return _current


def set_language(lang: str) -> None:
    """Setzt die UI-Sprache und benachrichtigt alle Listener."""
    global _current
    if lang not in _SUPPORTED:
        return
    if lang == _current:
        return
    _current = lang
    _manager.language_changed.emit(lang)


def init_language(lang: str) -> None:
    """Setzt die Sprache beim Start ohne Signal (noch keine Listener)."""
    global _current
    if lang in _SUPPORTED:
        _current = lang


def _table() -> dict[str, str]:
    return en.STRINGS if _current == "en" else de.STRINGS


def tr(key: str, **kwargs: Any) -> str:
    """Übersetzt *key* in die aktuelle Sprache.

    Fehlende Keys geben den Key zurück (Entwickler-Hinweis).
    Platzhalter: ``tr("status.mode", mode_value="USB")``.
    """
    text = _table().get(key)
    if text is None:
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return text
    return text


def install_qt_translations(app: Any, lang: str) -> None:
    """Lädt Qt-Standardübersetzungen (OK/Abbrechen/Ja/Nein) für *lang*."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    if lang == "en":
        QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        return

    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for prefix in ("qtbase", "qt"):
        translator = QTranslator()
        if translator.load(f"{prefix}_de", path):
            app.installTranslator(translator)
