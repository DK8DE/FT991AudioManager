"""Favoriten-Zeile: Dropdown + Speichern / Löschen / Ändern.

Liegt im Hauptfenster in einem eigenen ``QFrame`` mit ``panelFrame`` — kein
zusätzlicher innerer Rahmen, damit der Bereich wie andere Hauptpanels wirkt.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class FavoritesPanelWidget(QWidget):
    """Inhalt der Favoriten-Steuerung (ohne Umriss — den setzt das Eltern-``QFrame``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        row.addWidget(QLabel("Favoriten:"))
        self.combo = QComboBox(self)
        self.combo.setMinimumWidth(220)
        self.combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.combo.setToolTip(
            "Gespeicherte Soll-Vorgaben (Frequenz, Mode, EQ-Profil, SQL, AF, RF, Power). "
            "Erste Zeile „Favoriten“ bedeutet keine Auswahl; nach Wechsel des "
            "Speicherkanals oder VFO erscheint sie wieder."
        )
        row.addWidget(self.combo, stretch=1)

        self.btn_save = QPushButton("Speichern…", self)
        self.btn_delete = QPushButton("Löschen", self)
        self.btn_edit = QPushButton("Ändern", self)
        self.btn_save.setToolTip("Aktuellen Funkzustand als Favorit speichern")
        self.btn_delete.setToolTip("Gewählten Favoriten entfernen")
        self.btn_edit.setToolTip(
            "Gewählten Favorit mit dem aktuellen Funkzustand überschreiben"
        )
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_delete)
        row.addWidget(self.btn_edit)
