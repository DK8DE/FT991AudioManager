"""Update-Prüfung gegen GitHub-Releases (ohne Auto-Installer).

Die öffentliche GitHub-Releases-API liefert das neueste Tag; wir vergleichen
numerisch mit :data:`version.APP_VERSION`. Bei Fehlern (Netzwerk, Rate-Limit)
wird eine verständliche Meldung zurückgegeben — ohne die Installation als
„aktuell“ zu bezeichnen.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, QThread, Signal

from version import APP_NAME, APP_VERSION

#: REST: neuestes Release (öffentlich, ohne Token; niedriges Rate-Limit).
_GITHUB_API_LATEST = (
    "https://api.github.com/repos/DK8DE/FT991AudioManager/releases/latest"
)
#: Fallback, falls ``html_url`` in der API-Antwort fehlt.
RELEASES_PAGE_URL = "https://github.com/DK8DE/FT991AudioManager/releases"


class ReleaseCheckError(Exception):
    """API nicht erreichbar oder Antwort nicht auswertbar."""


def _version_tuple(version: str) -> Tuple[int, ...]:
    """``1.5.3`` / ``v1.5.10`` → Vergleichstupel (nur führende Ziffernblöcke)."""
    s = version.strip().lstrip("vV")
    parts: list[int] = []
    for segment in s.split("."):
        segment = segment.strip()
        if not segment:
            continue
        m = re.match(r"^(\d+)", segment)
        if m:
            parts.append(int(m.group(1)))
        else:
            parts.append(0)
    if not parts:
        raise ValueError(f"Keine Versionsnummer erkennbar: {version!r}")
    return tuple(parts)


def remote_version_is_newer(remote: str, installed: str) -> bool:
    """True, wenn ``remote`` strikt größer als ``installed`` ist."""
    tr, ti = _version_tuple(remote), _version_tuple(installed)
    n = max(len(tr), len(ti))
    tr = tr + (0,) * (n - len(tr))
    ti = ti + (0,) * (n - len(ti))
    return tr > ti


def fetch_latest_release(
    *,
    timeout_s: float = 15.0,
    user_agent: str,
) -> tuple[str, str]:
    """Liest neuestes Release von der GitHub-API.

    Returns:
        (versionsstring ohne „v“, ``html_url`` der Release-Seite)
    """
    req = urllib.request.Request(
        _GITHUB_API_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ReleaseCheckError(
                "Auf GitHub wurde kein Release gefunden (404)."
            ) from exc
        if exc.code in (403, 429):
            raise ReleaseCheckError(
                "GitHub hat die Anfrage abgelehnt (zu viele Abfragen oder "
                "Rate-Limit). Bitte später erneut versuchen."
            ) from exc
        raise ReleaseCheckError(
            f"GitHub-API-Fehler: HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise ReleaseCheckError(
            f"Netzwerkfehler: {exc.reason!s}"
        ) from exc
    except TimeoutError as exc:
        raise ReleaseCheckError(
            "Zeitüberschreitung — Server antwortet nicht rechtzeitig."
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseCheckError("Ungültige JSON-Antwort von GitHub.") from exc

    tag = (data.get("tag_name") or "").strip()
    url = (data.get("html_url") or "").strip() or RELEASES_PAGE_URL
    ver = tag.lstrip("vV").strip() if tag else ""
    if not ver:
        raise ReleaseCheckError("Release enthält kein auswertbares Tag (tag_name).")
    return ver, url


@dataclass(frozen=True)
class UpdateCheckOutcome:
    """Ergebnis einer Update-Prüfung (für UI)."""

    current: str
    ok: bool
    update_available: bool = False
    latest: str = ""
    release_url: str = ""
    error_message: str = ""


class UpdateCheckThread(QThread):
    """Lädt die neueste Release-Version im Hintergrund (GUI bleibt reaktionfähig)."""

    outcome = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        ua = f"{APP_NAME.replace('/', '-')} {APP_VERSION}"
        try:
            latest, url = fetch_latest_release(user_agent=ua)
            newer = remote_version_is_newer(latest, APP_VERSION)
            self.outcome.emit(
                UpdateCheckOutcome(
                    current=APP_VERSION,
                    ok=True,
                    update_available=newer,
                    latest=latest,
                    release_url=url,
                )
            )
        except ReleaseCheckError as exc:
            self.outcome.emit(
                UpdateCheckOutcome(
                    current=APP_VERSION,
                    ok=False,
                    error_message=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.outcome.emit(
                UpdateCheckOutcome(
                    current=APP_VERSION,
                    ok=False,
                    error_message=f"Unerwarteter Fehler: {exc}",
                )
            )
