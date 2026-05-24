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

from i18n import tr


class FavoritesPanelWidget(QWidget):
    """Inhalt der Favoriten-Steuerung (ohne Umriss — den setzt das Eltern-``QFrame``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._lbl_favorites = QLabel(tr("favorites.label"))
        row.addWidget(self._lbl_favorites)
        self.combo = QComboBox(self)
        self.combo.setMinimumWidth(220)
        self.combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.combo.setToolTip(tr("favorites.combo_tooltip"))
        row.addWidget(self.combo, stretch=1)

        self.btn_save = QPushButton(tr("favorites.btn_save"), self)
        self.btn_delete = QPushButton(tr("favorites.btn_delete"), self)
        self.btn_edit = QPushButton(tr("favorites.btn_edit"), self)
        self.btn_save.setToolTip(tr("favorites.btn_save_tooltip"))
        self.btn_delete.setToolTip(tr("favorites.btn_delete_tooltip"))
        self.btn_edit.setToolTip(tr("favorites.btn_edit_tooltip"))
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_delete)
        row.addWidget(self.btn_edit)

    def retranslate_ui(self) -> None:
        self._lbl_favorites.setText(tr("favorites.label"))
        self.combo.setToolTip(tr("favorites.combo_tooltip"))
        self.btn_save.setText(tr("favorites.btn_save"))
        self.btn_delete.setText(tr("favorites.btn_delete"))
        self.btn_edit.setText(tr("favorites.btn_edit"))
        self.btn_save.setToolTip(tr("favorites.btn_save_tooltip"))
        self.btn_delete.setToolTip(tr("favorites.btn_delete_tooltip"))
        self.btn_edit.setToolTip(tr("favorites.btn_edit_tooltip"))
