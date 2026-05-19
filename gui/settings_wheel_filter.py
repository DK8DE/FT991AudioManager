"""Mausrad in Spinboxen/Combos im Einstellungsdialog unterbinden (Scrollen bleibt möglich)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QComboBox,
    QWidget,
)


class SettingsNoWheelFilter(QObject):
    """Verhindert Mausrad-Wertänderung; leitet Scrollen an die umgebende ScrollArea weiter."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel:
            return False
        if not isinstance(event, QWheelEvent):
            return False
        if isinstance(obj, QComboBox):
            view = obj.view()
            if view is not None and view.isVisible() and view.underMouse():
                return False
        if isinstance(obj, (QAbstractSpinBox, QComboBox)):
            _scroll_enclosing_area(obj, event)
            return True
        return False


def _scroll_enclosing_area(widget: QWidget, event: QWheelEvent) -> None:
    """Scrollt die nächste QScrollArea — ohne sendEvent (keine Filter-Rekursion)."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            bar = parent.verticalScrollBar()
            if bar is not None:
                delta = event.angleDelta().y()
                if delta == 0:
                    delta = event.pixelDelta().y()
                if delta != 0:
                    step = max(1, bar.singleStep())
                    bar.setValue(bar.value() - (delta * step) // 120)
            return
        parent = parent.parentWidget()


def install_settings_no_wheel_filter(root: QWidget) -> SettingsNoWheelFilter:
    """Filter nur auf Spinboxen/Dropdowns unter *root* (kein App-weiter Filter)."""
    filt = SettingsNoWheelFilter(root)
    for w in root.findChildren(QAbstractSpinBox):
        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        w.installEventFilter(filt)
    for w in root.findChildren(QComboBox):
        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        w.installEventFilter(filt)
    return filt
