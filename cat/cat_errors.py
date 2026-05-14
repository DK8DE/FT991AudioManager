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
