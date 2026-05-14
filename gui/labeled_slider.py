"""Einheitlicher Slider mit Name links und Wertanzeige rechts.

Layout: ``[Name]  [============= QSlider =============]  [Wert]``.

Kann auf zwei Arten betrieben werden:

1. **Numerisch** — ``minimum``, ``maximum``, ``default`` + optional ein
   ``value_formatter`` (z. B. ``lambda v: f"{v:+d} dB"``).
2. **Index-basiert** — ``choices`` mit einer Liste beliebiger Werte. Der
   Slider geht intern von ``0..len-1`` und ``choice()`` / ``set_choice()``
   tauschen sich gegen den eigentlichen Listenwert aus. Mit
   ``value_formatter`` wird der angezeigte Text aus dem Listenwert
   gebildet (Default: ``str(item)``).

Wird im EQ-Editor (Frequenz / Level / Bandbreite) konsequent verwendet,
damit alle Einstellungen einheitlich als Slider erscheinen.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


class LabeledSlider(QWidget):
    """Slider mit Name links und formatiertem Wert rechts."""

    #: Wird gefeuert, wenn der Wert sich ändert. Liefert den **internen**
    #: Slider-Wert (bei Index-Mode den Index, bei numerischem Mode den Wert).
    valueChanged = Signal(int)

    def __init__(
        self,
        label_text: str,
        *,
        choices: Optional[Sequence[Any]] = None,
        minimum: int = 0,
        maximum: int = 100,
        default: int = 0,
        value_formatter: Optional[Callable[[Any], str]] = None,
        page_step: int = 5,
        label_min_width: int = 90,
        value_min_width: int = 72,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._choices: Optional[List[Any]] = (
            list(choices) if choices is not None else None
        )
        if self._choices is not None:
            minimum = 0
            maximum = max(0, len(self._choices) - 1)
            if not (minimum <= default <= maximum):
                default = minimum
        self._value_formatter = value_formatter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._name_label = QLabel(label_text)
        self._name_label.setMinimumWidth(label_min_width)
        layout.addWidget(self._name_label)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(page_step)
        self._slider.setValue(default)
        layout.addWidget(self._slider, stretch=1)

        self._value_label = QLabel("")
        self._value_label.setMinimumWidth(value_min_width)
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._value_label)

        self._slider.valueChanged.connect(self._on_internal_change)
        self._refresh_value_label(default)

    # ------------------------------------------------------------------
    # Slider / Werte
    # ------------------------------------------------------------------

    def value(self) -> int:
        """Aktueller Slider-Rohwert (Index bei choices, sonst direkter Wert)."""
        return int(self._slider.value())

    def setValue(self, value: int) -> None:
        v = max(self._slider.minimum(), min(self._slider.maximum(), int(value)))
        if v != self._slider.value():
            self._slider.setValue(v)
        else:
            self._refresh_value_label(v)

    def slider(self) -> QSlider:
        """Zugriff auf den internen ``QSlider`` (z. B. für Tick-Konfiguration)."""
        return self._slider

    def label_widget(self) -> QLabel:
        """Das Name-Label links."""
        return self._name_label

    def value_widget(self) -> QLabel:
        """Das Wert-Label rechts (z. B. zum Anpassen der Mindestbreite)."""
        return self._value_label

    # ------------------------------------------------------------------
    # Index-Mode (choices)
    # ------------------------------------------------------------------

    def choice(self) -> Any:
        """Aktueller Listenwert bei Index-Mode, sonst ``None``."""
        if self._choices is None:
            return None
        idx = self._slider.value()
        if 0 <= idx < len(self._choices):
            return self._choices[idx]
        return None

    def set_choice(self, value: Any) -> None:
        """Setzt den Slider auf den passenden Index für ``value``.

        Ist der Wert nicht in der Liste, wird er als zusätzlicher Eintrag
        angefügt und ausgewählt (analog zum bisherigen Combo-Verhalten,
        damit exotische Profile keine Daten verlieren).
        """
        if self._choices is None:
            return
        try:
            idx = self._choices.index(value)
        except ValueError:
            self._choices.append(value)
            idx = len(self._choices) - 1
            self._slider.setMaximum(len(self._choices) - 1)
        self.setValue(idx)

    def set_choices(self, choices: Sequence[Any]) -> None:
        """Tauscht die Choice-Liste komplett aus."""
        self._choices = list(choices)
        new_max = max(0, len(self._choices) - 1)
        self._slider.blockSignals(True)
        try:
            self._slider.setMaximum(new_max)
            if self._slider.value() > new_max:
                self._slider.setValue(0)
        finally:
            self._slider.blockSignals(False)
        self._refresh_value_label(self._slider.value())

    # ------------------------------------------------------------------
    # Interna
    # ------------------------------------------------------------------

    def _on_internal_change(self, value: int) -> None:
        self._refresh_value_label(value)
        self.valueChanged.emit(value)

    def _refresh_value_label(self, value: int) -> None:
        text = self._format_for_label(value)
        self._value_label.setText(text)

    def _format_for_label(self, value: int) -> str:
        if self._choices is not None:
            if 0 <= value < len(self._choices):
                item = self._choices[value]
                if self._value_formatter is not None:
                    return self._value_formatter(item)
                return str(item)
            return ""
        if self._value_formatter is not None:
            return self._value_formatter(value)
        return str(value)
