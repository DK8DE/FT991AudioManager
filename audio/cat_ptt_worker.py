"""CAT PTT (TX1;/TX0;) in einem Hintergrund-Thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from cat import CatError, FT991CAT, SerialCAT


class CatPttWorker(QObject):
    """Führt ``set_cat_transmit`` ohne UI-Blockade aus."""

    succeeded = Signal(bool)
    failed = Signal(str)

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        wait_for_tx_confirm: bool = True,
    ) -> None:
        super().__init__()
        self._cat = serial_cat
        self._wait_for_tx_confirm = bool(wait_for_tx_confirm)

    @Slot(bool)
    def set_transmit(self, on: bool) -> None:
        try:
            if not self._cat.is_connected():
                self.failed.emit("CAT nicht verbunden")
                return
            FT991CAT(self._cat).set_cat_transmit(
                on, wait=self._wait_for_tx_confirm
            )
            self.succeeded.emit(on)
        except CatError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
