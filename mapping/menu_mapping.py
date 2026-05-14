"""Generische Helfer für das ``EX``-Menü-Kommando des FT-991/FT-991A.

Die Wire-Format ist::

    Lesen:    EXmmm;
    Antwort:  EXmmmVVV...;
    Schreiben: EXmmmVVV...;

Dabei ist ``mmm`` immer eine **dreistellige** Menünummer (000–999) und
``VVV...`` ein menü-spezifischer Wert, dessen Länge je Menü unterschiedlich
ist (typisch 1–3 Zeichen, gelegentlich mehr).

Dieses Modul kennt **keine** menüspezifischen Tabellen — das übernimmt
:mod:`mapping.eq_mapping` u. ä. Es wirft bewusst nur ``ValueError`` und
hat **keine Abhängigkeit auf das cat-Package**, um zirkuläre Imports zu
verhindern. Der Aufrufer in :mod:`cat.ft991_cat` konvertiert ``ValueError``
bei Bedarf in :class:`cat.cat_errors.CatProtocolError`.
"""

from __future__ import annotations


def format_ex_read(menu_number: int) -> str:
    """Bildet das CAT-Kommando zum Lesen eines EX-Menüs.

    >>> format_ex_read(121)
    'EX121;'
    """
    if not 0 <= menu_number <= 999:
        raise ValueError(f"Menünummer ausserhalb 0..999: {menu_number}")
    return f"EX{menu_number:03d};"


def format_ex_write(menu_number: int, raw_value: str) -> str:
    """Bildet das CAT-Kommando zum Setzen eines EX-Menüs.

    Der ``raw_value`` ist die ASCII-Repräsentation, die der FT-991A erwartet
    (Nullen-aufgefüllt, ohne ``;``).

    >>> format_ex_write(121, "03")
    'EX12103;'
    """
    if not 0 <= menu_number <= 999:
        raise ValueError(f"Menünummer ausserhalb 0..999: {menu_number}")
    if not raw_value or ";" in raw_value:
        raise ValueError(f"Ungültiger Rohwert: {raw_value!r}")
    return f"EX{menu_number:03d}{raw_value};"


def parse_ex_response(raw_response: str, menu_number: int) -> str:
    """Holt den Wert-Anteil aus einer EX-Antwort.

    >>> parse_ex_response('EX12103;', 121)
    '03'

    Wirft :class:`ValueError`, wenn die Antwort nicht zum erwarteten
    Menü passt oder das Format kaputt ist.
    """
    if not raw_response.endswith(";"):
        raise ValueError(f"EX-Antwort ohne Terminator: {raw_response!r}")
    body = raw_response[:-1]
    expected_prefix = f"EX{menu_number:03d}"
    if not body.startswith(expected_prefix):
        raise ValueError(
            f"EX-Antwort hat falsches Präfix (erwartet {expected_prefix}, "
            f"erhalten {body[:5]!r}): {raw_response!r}"
        )
    value = body[len(expected_prefix):]
    if not value:
        raise ValueError(f"EX-Antwort ohne Wertanteil: {raw_response!r}")
    return value
