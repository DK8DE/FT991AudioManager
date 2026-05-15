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


class CatCommandUnsupportedError(CatProtocolError):
    """Das Funkgerät hat ``?;`` zurückgeliefert.

    Yaesu nutzt ``?;`` als generische "command not recognized"-Antwort.
    Wir trennen das von anderen Protokollfehlern, damit hoehere Schichten
    den Befehl gezielt fuer die laufende Sitzung deaktivieren koennen --
    z. B. der FT-991 ohne A versteht ``NR0;`` und ``BC0;`` nicht; ohne
    diese Trennung wuerde der Slow-Path bei jedem Tick erneut versuchen
    und das CAT-Log mit WARN-Meldungen fluten.

    Unterklasse von :class:`CatProtocolError`, sodass bestehende
    ``except CatProtocolError``-Pfade weiterhin greifen.
    """
