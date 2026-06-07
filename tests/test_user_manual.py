"""Tests fuer GitHub-Handbuch-PDF-URLs."""

from gui.user_manual import manual_pdf_download_url, manual_pdf_filename


def test_manual_pdf_filename_de() -> None:
    assert manual_pdf_filename("1.9.5", "de") == "FT991AudioManager-Bedienungsanleitung-1.9.5.pdf"


def test_manual_pdf_filename_en() -> None:
    assert manual_pdf_filename("1.9.5", "en") == "FT991AudioManager-UserManual-1.9.5.pdf"


def test_manual_pdf_download_url_de() -> None:
    url = manual_pdf_download_url("v1.9.5", "de")
    assert url == (
        "https://github.com/DK8DE/FT991AudioManager/releases/download/v1.9.5/"
        "FT991AudioManager-Bedienungsanleitung-1.9.5.pdf"
    )


def test_manual_pdf_download_url_en() -> None:
    url = manual_pdf_download_url("1.9.5", "en")
    assert url == (
        "https://github.com/DK8DE/FT991AudioManager/releases/download/v1.9.5/"
        "FT991AudioManager-UserManual-1.9.5.pdf"
    )
