"""Gemeinsames Layout für den Einstellungsdialog (schmale Inhaltsspalte)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

# Max. Breite des rechten Inhalts (Nav-Leiste + Rand abziehen)
SETTINGS_PANEL_MAX_WIDTH = 460


def hint_label(text: str, *, parent: QWidget | None = None) -> QLabel:
    lb = QLabel(text, parent)
    lb.setWordWrap(True)
    lb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lb


class _ClickableLabel(QLabel):
    def __init__(self, text: str, on_click, *, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._on_click = on_click
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


class WrappingCheckBox(QWidget):
    """Checkbox mit umbrechendem Beschriftungstext (QCheckBox hat kein setWordWrap)."""

    def __init__(self, text: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._box = QCheckBox(self)
        self._label = _ClickableLabel(text, self._box.toggle, parent=self)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        lay.addWidget(self._box, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._label, 1)

    def isChecked(self) -> bool:
        return self._box.isChecked()

    def setChecked(self, checked: bool) -> None:
        self._box.setChecked(checked)

    def setToolTip(self, tip: str) -> None:
        self._box.setToolTip(tip)
        self._label.setToolTip(tip)


def wrap_checkbox(text: str, *, parent: QWidget | None = None) -> WrappingCheckBox:
    return WrappingCheckBox(text, parent=parent)


def narrow_panel(inner: QWidget) -> QWidget:
    """Inhalt linksbündig auf feste Maximalbreite begrenzen."""
    panel = QWidget()
    lay = QHBoxLayout(panel)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    inner.setMaximumWidth(SETTINGS_PANEL_MAX_WIDTH)
    inner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    lay.addWidget(inner, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    lay.addStretch(1)
    return panel


def fix_spin_width(spin: QSpinBox, width: int = 100) -> None:
    spin.setMinimumWidth(width)
    spin.setMaximumWidth(width)
