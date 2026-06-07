"""GitHub-Handbuch-PDFs (DE/EN) fuer Hilfe -> Anleitung."""

from __future__ import annotations

_GITHUB_REPO = "DK8DE/FT991AudioManager"


def _normalize_version(version: str) -> str:
    return version.strip().lstrip("vV")


def manual_pdf_filename(version: str, lang: str) -> str:
    """Dateiname des Release-Assets fuer die gewaehlte Sprache."""
    ver = _normalize_version(version)
    if lang == "en":
        return f"FT991AudioManager-UserManual-{ver}.pdf"
    return f"FT991AudioManager-Bedienungsanleitung-{ver}.pdf"


def manual_pdf_download_url(version: str, lang: str) -> str:
    """Direkt-Download-URL des Handbuchs fuer die installierte Version."""
    ver = _normalize_version(version)
    tag = f"v{ver}"
    name = manual_pdf_filename(ver, lang)
    return f"https://github.com/{_GITHUB_REPO}/releases/download/{tag}/{name}"
