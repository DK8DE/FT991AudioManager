"""Hilfen für Hotkey-Auswahl-Comboboxen (Einstellungen)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QWidget

from i18n import tr
from model.global_shortcuts_settings import GlobalShortcutsSettings

_SPECIAL_HOTKEY_ENTRIES: tuple[tuple[str, str], ...] = (
    ("settings.shortcuts_key_left", "LEFT"),
    ("settings.shortcuts_key_up", "UP"),
    ("settings.shortcuts_key_right", "RIGHT"),
    ("settings.shortcuts_key_down", "DOWN"),
    ("settings.shortcuts_key_page_up", "PRIOR"),
    ("settings.shortcuts_key_page_down", "NEXT"),
    ("settings.shortcuts_key_plus", "OEM_PLUS"),
    ("settings.shortcuts_key_minus", "OEM_MINUS"),
    ("settings.shortcuts_key_numpad_plus", "NUMPAD_ADD"),
    ("settings.shortcuts_key_numpad_minus", "NUMPAD_SUBTRACT"),
)

_FUNCTION_KEY_TOKENS: tuple[str, ...] = tuple(f"F{i}" for i in range(1, 13))
_HOTKEY_SINGLE_CHAR = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def modifier_combo(parent: QWidget, current: str) -> QComboBox:
    cb = QComboBox(parent)
    items = (
        ("settings.shortcuts_mod_none", "none"),
        ("settings.shortcuts_mod_alt", "alt"),
        ("settings.shortcuts_mod_ctrl", "control"),
        ("settings.shortcuts_mod_shift", "shift"),
        ("settings.shortcuts_mod_win", "win"),
    )
    for tr_key, data in items:
        cb.addItem(tr(tr_key), data)
    cur = (current or "none").strip().lower()
    if cur not in ("none", "alt", "control", "shift", "win"):
        cur = "none"
    for i in range(cb.count()):
        if cb.itemData(i) == cur:
            cb.setCurrentIndex(i)
            break
    return cb


def fill_hotkey_combo(cb: QComboBox) -> None:
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        cb.addItem(c, c)
    for tok in _FUNCTION_KEY_TOKENS:
        cb.addItem(tok, tok)
    for tr_key, data in _SPECIAL_HOTKEY_ENTRIES:
        cb.addItem(tr(tr_key), data)
    for d in "0123456789":
        cb.addItem(d, d)


def select_hotkey_combo(cb: QComboBox, saved: str, default: str) -> None:
    s = str(saved if saved is not None else default).strip().upper()
    if len(s) == 1 and s not in _HOTKEY_SINGLE_CHAR:
        s = str(default).strip().upper()
    for i in range(cb.count()):
        if str(cb.itemData(i) or "") == s:
            cb.setCurrentIndex(i)
            return
    d = str(default).strip().upper()
    if len(d) == 1 and d in _HOTKEY_SINGLE_CHAR:
        for i in range(cb.count()):
            if cb.itemData(i) == d:
                cb.setCurrentIndex(i)
                return
    cb.setCurrentIndex(0)


def hotkey_key_combo(parent: QWidget, initial: str) -> QComboBox:
    cb = QComboBox(parent)
    fill_hotkey_combo(cb)
    select_hotkey_combo(cb, initial, initial)
    return cb


def retranslate_hotkey_combo_special_labels(cb: QComboBox) -> None:
    offset = 26 + len(_FUNCTION_KEY_TOKENS)
    for i, (tr_key, _data) in enumerate(_SPECIAL_HOTKEY_ENTRIES):
        cb.setItemText(offset + i, tr(tr_key))


def key_sequence_from_settings(gs: GlobalShortcutsSettings, key_token: str) -> str:
    """Qt-Shortcut-String z. B. ``Ctrl+Shift+X``."""
    parts: list[str] = []
    for mod in (gs.modifier_1, gs.modifier_2):
        if mod == "control":
            parts.append("Ctrl")
        elif mod == "shift":
            parts.append("Shift")
        elif mod == "alt":
            parts.append("Alt")
        elif mod == "win":
            parts.append("Meta")
    parts.append(str(key_token or "A").strip().upper())
    return "+".join(parts)


def qt_key_from_token(token: str) -> Optional[Qt.Key]:
    t = str(token or "").strip().upper()
    if len(t) == 1 and "A" <= t <= "Z":
        return Qt.Key(ord(t))
    if len(t) == 1 and "0" <= t <= "9":
        return Qt.Key(ord(t))
    if len(t) >= 2 and t[0] == "F":
        try:
            n = int(t[1:])
        except ValueError:
            return None
        if 1 <= n <= 12:
            return Qt.Key(int(Qt.Key.Key_F1) + n - 1)
    return None


def qt_modifiers_match_settings(
    gs: GlobalShortcutsSettings, km: Qt.KeyboardModifier
) -> bool:
    """Prüft, ob genau die konfigurierten Modifikatoren aktiv sind."""
    required = {str(gs.modifier_1), str(gs.modifier_2)}
    required.discard("none")
    flags = {
        "control": Qt.KeyboardModifier.ControlModifier,
        "shift": Qt.KeyboardModifier.ShiftModifier,
        "alt": Qt.KeyboardModifier.AltModifier,
        "win": Qt.KeyboardModifier.MetaModifier,
    }
    for name, flag in flags.items():
        active = bool(km & flag)
        needed = name in required
        if active != needed:
            return False
    return True
