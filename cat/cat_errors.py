"""Eigene Fehlerklassen für die CAT-Schicht."""

from __future__ import annotations


class CatError(Exception):
    """Basisklasse für alle CAT-bezogenen Fehler."""


class CatNotConnectedError(CatError):
    """Wird ausgelöst, wenn ein Kommando ohne offene Verbindung gesendet werden soll."""


class CatConnectionLostError(CatNotConnectedError):
    """Verbindung war offen, ist aber während eines Roundtrips weggebrochen.

    Typischer Auslöser: USB-Kabel gezogen, Gerät ausgeschaltet, Treiber
    durchgereicht ``SerialException`` oder ``OSError`` beim Schreiben/Lesen.
    Wird vom :class:`SerialCAT` erkannt, das die Verbindung selbst sauber
    schließt; obere Schichten müssen nur ihre UI auf "nicht verbunden"
    setzen und ggf. einen Reconnect-Watcher anstoßen.

    Unterklasse von :class:`CatNotConnectedError`, damit bestehende
    ``except CatNotConnectedError``-Pfade automatisch funktionieren.
    """


class CatTimeoutError(CatError):
    """Wird ausgelöst, wenn auf eine Antwort gewartet wird und der Timeout abläuft."""


class CatProtocolError(CatError):
    """Antwort vom Funkgerät entspricht nicht dem erwarteten Format."""


def is_cat_protocol_error(exc: BaseException) -> bool:
    """True bei Antwort-/Paket-Problemen — nur ins CAT-Log, nicht in die Haupt-GUI."""
    return isinstance(exc, CatProtocolError)


def is_cat_protocol_error_message(text: str) -> bool:
    """Wie :func:`is_cat_protocol_error`, für bereits formatierte Fehlertexte."""
    if not text:
        return False
    lower = text.casefold()
    markers = (
        "stale-frame",
        "keine passende antwort",
        "kennt befehl",
        "antwort '?;'",
        "unerwarteter rohwert",
        "ungültige antwort",
        "entspricht nicht dem format",
        "nicht-parsebar",
        "unerwartete rohwerte",
        "unerwartete antwort",
    )
    return any(marker in lower for marker in markers)


class CatCommandUnsupportedError(CatProtocolError):
    """Das Funkgerät hat ``?;`` zurückgeliefert. \n\n

    Yaesu nutzt ``?;`` als generische "command not recognized"-Antwort. \n
    Wir trennen das von anderen Protokollfehlern, damit hoehere Schichten \n
    den Befehl gezielt fuer die laufende Sitzung deaktivieren koennen -- \n
    z. B. der FT-991 ohne A versteht ``NR0;`` und ``BC0;`` nicht; ohne \n
    diese Trennung wuerde der Slow-Path bei jedem Tick erneut versuchen \n
    und das CAT-Log mit WARN-Meldungen fluten. \n

    Unterklasse von :class:`CatProtocolError`, sodass bestehende \n
    ``except CatProtocolError``-Pfade weiterhin greifen. \n
    """
