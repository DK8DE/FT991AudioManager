"""Globale Tastenkürzel (Windows RegisterHotKey)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

ModifierName = Literal["none", "alt", "control", "shift", "win"]

_ALLOWED_MODIFIERS = frozenset({"none", "alt", "control", "shift", "win"})
_DEFAULT_KEY_CONTEST_PLAY = "P"
_DEFAULT_KEY_LIVE_PTT_LATCH = "X"
_DEFAULT_KEY_LIVE_PTT_MOMENTARY = "Y"


@dataclass
class GlobalShortcutsSettings:
    enabled: bool = True
    modifier_1: ModifierName = "control"
    modifier_2: ModifierName = "shift"
    key_contest_play: str = _DEFAULT_KEY_CONTEST_PLAY
    key_live_ptt_latch: str = _DEFAULT_KEY_LIVE_PTT_LATCH
    key_live_ptt_momentary: str = _DEFAULT_KEY_LIVE_PTT_MOMENTARY

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "modifier_1": self.modifier_1,
            "modifier_2": self.modifier_2,
            "key_contest_play": str(self.key_contest_play),
            "key_live_ptt_latch": str(self.key_live_ptt_latch),
            "key_live_ptt_momentary": str(self.key_live_ptt_momentary),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "GlobalShortcutsSettings":
        r = raw if isinstance(raw, dict) else {}
        return cls(
            enabled=bool(r.get("enabled", True)),
            modifier_1=_parse_modifier(r.get("modifier_1"), "control"),
            modifier_2=_parse_modifier(r.get("modifier_2"), "shift"),
            key_contest_play=_parse_key_token(
                r.get("key_contest_play"), _DEFAULT_KEY_CONTEST_PLAY
            ),
            key_live_ptt_latch=_parse_key_token(
                r.get("key_live_ptt_latch"), _DEFAULT_KEY_LIVE_PTT_LATCH
            ),
            key_live_ptt_momentary=_parse_key_token(
                r.get("key_live_ptt_momentary"), _DEFAULT_KEY_LIVE_PTT_MOMENTARY
            ),
        )


def _parse_modifier(value: object, default: ModifierName) -> ModifierName:
    s = str(value or default).strip().lower()
    if s in _ALLOWED_MODIFIERS:
        return cast(ModifierName, s)
    return default


def _parse_key_token(value: object, default: str) -> str:
    s = str(value if value is not None else default).strip().upper()
    if not s:
        return default
    if len(s) == 1 and ("A" <= s <= "Z" or "0" <= s <= "9"):
        return s
    allowed_tokens = {
        "LEFT",
        "UP",
        "RIGHT",
        "DOWN",
        "PRIOR",
        "NEXT",
        "OEM_PLUS",
        "OEM_MINUS",
        "NUMPAD_ADD",
        "NUMPAD_SUBTRACT",
    }
    if s in allowed_tokens:
        return s
    if len(s) >= 2 and s[0] == "F":
        try:
            n = int(s[1:])
        except ValueError:
            n = 0
        if 1 <= n <= 12:
            return s
    return default
