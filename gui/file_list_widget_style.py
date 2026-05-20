"""Gemeinsames ``QListWidget``-Styling für Audio-Player und -Recorder.

Unter Windows ohne Dark-Mode schmilzt eine schlichte Liste optisch mit dem
``QGroupBox``-Hintergrund; ``palette()`` sorgt für sichtbare Rahmen und
passende Farben in Hell- und Dunkelmodus.
"""

FILE_LIST_WIDGET_STYLESHEET = """
QListWidget {
    border: 1px solid palette(mid);
    border-radius: 2px;
    background: palette(base);
    padding: 3px;
    outline: none;
}
QListWidget::item {
    border: 1px solid palette(midlight);
    border-radius: 2px;
    padding: 4px 8px;
    margin: 2px;
    background: palette(base);
}
QListWidget::item:selected {
    border: 1px solid palette(highlight);
    background: palette(highlight);
    color: palette(highlighted-text);
}
QListWidget::item:hover:!selected {
    background: palette(alternate-base);
    border: 1px solid palette(mid);
}
"""
