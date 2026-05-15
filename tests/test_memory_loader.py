"""Tests fuer ``MemoryChannelLoader`` aus ``gui/memory_loader.py``.

Wir starten echte ``QThread``s — die Tests pumpen eine
``QCoreApplication``-Eventloop, bis der Loader die Signale gefeuert hat.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Dict, List

# Headless: Qt soll keinen Display brauchen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:  # pragma: no cover
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from cat import CatCommandUnsupportedError  # noqa: E402
from cat.serial_cat import SerialCAT  # noqa: E402
from gui.memory_loader import MemoryChannelLoader, _MemoryLoaderWorker  # noqa: E402
from mapping.memory_mapping import (  # noqa: E402
    MEMORY_CHANNEL_MAX,
    MemoryChannel,
)


def _ensure_qapp() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


class _ScriptedSerialCAT(SerialCAT):
    """Liefert programmierbare MT-Antworten.

    ``responses`` mappt CAT-Befehl → Antwort. Fehlt ein Eintrag, wird
    eine „leere Slot"-Antwort generiert. Auf Wunsch kann pro Befehl
    eine :class:`CatCommandUnsupportedError` ausgeloest werden, indem
    die Antwort ``"?;"`` ist.
    """

    def __init__(self, responses: Dict[str, str]) -> None:
        super().__init__()
        self.responses = responses
        self.commands: List[str] = []

    def is_connected(self) -> bool:  # type: ignore[override]
        return True

    def send_command(  # type: ignore[override]
        self,
        command: str,
        *,
        read_response: bool = True,
        expected_prefix=None,
    ) -> str:
        self.commands.append(command)
        if not read_response:
            return ""
        # Default: leerer Slot in der gleichen Channel-Nummer wie angefragt.
        if command not in self.responses:
            if command.startswith("MT") and command.endswith(";"):
                channel = command[2:5]
                # Format wie parse_mt_response erwartet:
                # MT<ch:3><freq:9><sign:1><offset:4><rxclar:1><txclar:1>
                #   <mode:1><p8:1><p9:5><tag:12>;
                response = (
                    f"MT{channel}000000000+0000000000000            ;"
                )
                return response
            return ""
        response = self.responses[command]
        if response == "?;":
            raise CatCommandUnsupportedError(
                f"Funkgeraet kennt Befehl {command!r} nicht (Antwort '?;')"
            )
        return response


def _wait_for(predicate, timeout_ms: int = 8000) -> bool:
    """Pumpt die Eventloop, bis ``predicate`` True ist oder Timeout."""
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)

    poll = QTimer()
    poll.setInterval(20)

    def _check():
        if predicate():
            loop.quit()
    poll.timeout.connect(_check)
    poll.start()
    timer.start(timeout_ms)
    loop.exec()
    poll.stop()
    timer.stop()
    return predicate()


class MemoryChannelLoaderTest(unittest.TestCase):

    def setUp(self) -> None:
        _ensure_qapp()
        self._loader: MemoryChannelLoader | None = None

    def tearDown(self) -> None:
        # Sauberer Abbau: laufenden Loader stoppen, auf den Thread warten,
        # pending Qt-Events abarbeiten, damit nichts ueber Test-Grenzen
        # ueberlebt (sonst kann der Worker beim Tear-down der
        # QCoreApplication crashen).
        loader = self._loader
        self._loader = None
        if loader is not None:
            loader.stop(wait=True)
            app = QCoreApplication.instance()
            if app is not None:
                app.processEvents()
                app.processEvents()

    def test_emits_channels_for_filled_slots_only(self) -> None:
        # Nur 3 Kanaele belegen, alle anderen Slots bleiben leer.
        # Format: MT<ch:3><freq:9><sign:1><offset:4><rxclar:1><txclar:1>
        #         <mode:1><p8:1><p9:5><tag:12>;
        responses = {
            "MT001;": "MT001014020000+0000001000000DL0AB       ;",
            "MT012;": "MT012145500000+0000004100000RELAIS DB0XX;",
            # Channel 099 explizit ablehnen (?;)
            "MT099;": "?;",
            # 117 voll
            "MT117;": "MT117007050000+0000001000000P-9U        ;",
        }
        cat = _ScriptedSerialCAT(responses)
        loader = MemoryChannelLoader(cat)
        self._loader = loader
        received: List[MemoryChannel] = []
        found_count: List[int] = []
        loader.channel_loaded.connect(lambda ch: received.append(ch))
        loader.finished.connect(lambda n: found_count.append(n))
        loader.start()

        self.assertTrue(_wait_for(lambda: bool(found_count)))
        self.assertEqual(found_count[0], 3)

        # Die drei gefuellten Slots wurden geliefert
        channels = sorted(c.channel for c in received)
        self.assertEqual(channels, [1, 12, 117])

        # Die richtige Anzahl an MT-Reads wurde ausgefuehrt (1..117).
        mt_reads = [c for c in cat.commands if c.startswith("MT")]
        self.assertEqual(len(mt_reads), MEMORY_CHANNEL_MAX)

    def test_worker_runs_without_pause_calls(self) -> None:
        """Regression: der Loader darf KEINE ``QThread.msleep``-Pausen
        einbauen, sonst dauert das Laden quaelend lang.

        Wir patchen ``msleep`` und stellen sicher, dass es kein einziges
        Mal aufgerufen wird.
        """
        from unittest.mock import patch

        responses = {
            f"MT{i:03d};": f"MT{i:03d}014020000+0000001000000SLOT{i:02d}     ;"
            for i in range(1, MEMORY_CHANNEL_MAX + 1)
        }
        cat = _ScriptedSerialCAT(responses)
        worker = _MemoryLoaderWorker(cat)
        with patch("gui.memory_loader.QThread.msleep") as msleep_mock:
            worker.run()
        msleep_mock.assert_not_called()

    def test_worker_respects_stop_flag(self) -> None:
        """Testet die Stop-Flagge direkt am Worker, ohne QThread-Lifecycle.

        Wir umgehen den ``QThread``-Aufbau und rufen ``run()`` einfach im
        Main-Thread auf. Das ``stop()`` setzen wir VOR dem Aufruf, damit
        die Schleife direkt beim ersten Iterations-Check (``stop_requested``)
        abbricht.
        """
        responses = {
            f"MT{i:03d};": f"MT{i:03d}014020000+0000001000000SLOT{i:02d}     ;"
            for i in range(1, MEMORY_CHANNEL_MAX + 1)
        }
        cat = _ScriptedSerialCAT(responses)
        worker = _MemoryLoaderWorker(cat)
        # Stop SOFORT setzen → der Worker bricht beim ersten
        # Schleifeneintritt ab (max. 1 Read durchgelaufen).
        worker.stop()
        worker.run()
        mt_reads = [c for c in cat.commands if c.startswith("MT")]
        # 0 oder 1 Reads — auf jeden Fall weniger als ein voller Lauf.
        self.assertLess(len(mt_reads), MEMORY_CHANNEL_MAX)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
