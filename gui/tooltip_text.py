"""Einheitliche Tooltip-Zeilenumbrüche für die gesamte GUI."""

from __future__ import annotations

import textwrap
from typing import Union

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget

TOOLTIP_MAX_LINE_LENGTH = 40


def format_tooltip(
    text: Union[str, None],
    *,
    max_len: int = TOOLTIP_MAX_LINE_LENGTH,
) -> str:
    """Bricht Tooltip-Text nach *max_len* Zeichen um; manuelle ``\\n`` bleiben erhalten."""
    if text is None:
        return ""
    if not text:
        return text

    paragraphs: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            paragraphs.append("")
            continue
        wrapped = textwrap.wrap(
            paragraph,
            width=max_len,
            break_long_words=True,
            break_on_hyphens=False,
        )
        paragraphs.append("\n".join(wrapped) if wrapped else paragraph)
    return "\n".join(paragraphs)


def install_tooltip_line_wrap(*, max_len: int = TOOLTIP_MAX_LINE_LENGTH) -> None:
    """Patcht ``QWidget.setToolTip`` und ``QAction.setToolTip`` app-weit."""
    if getattr(install_tooltip_line_wrap, "_installed", False):
        return

    _widget_set_tooltip = QWidget.setToolTip
    _action_set_tooltip = QAction.setToolTip

    def _wrap(text: Union[str, None]) -> str:
        return format_tooltip(text, max_len=max_len)

    def _widget_tooltip(self: QWidget, text: Union[str, None]) -> None:
        _widget_set_tooltip(self, _wrap(text))

    def _action_tooltip(self: QAction, text: Union[str, None]) -> None:
        _action_set_tooltip(self, _wrap(text))

    QWidget.setToolTip = _widget_tooltip  # type: ignore[method-assign]
    QAction.setToolTip = _action_tooltip  # type: ignore[method-assign]
    install_tooltip_line_wrap._installed = True  # type: ignore[attr-defined]
