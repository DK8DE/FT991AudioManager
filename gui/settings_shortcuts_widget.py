"""Einstellungen: globale Tastenkürzel."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from model.global_shortcuts_settings import GlobalShortcutsSettings

from .hotkey_combo_utils import (
    hotkey_key_combo,
    modifier_combo,
    retranslate_hotkey_combo_special_labels,
)


class ShortcutsSettingsWidget(QWidget):
    def __init__(self, settings: GlobalShortcutsSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        root = QVBoxLayout(self)

        self.chk_enabled = QCheckBox()
        root.addWidget(self.chk_enabled)

        self._lbl_platform = QLabel()
        self._lbl_platform.setWordWrap(True)
        if sys.platform != "win32":
            self._lbl_platform.setText(tr("settings.shortcuts.win_only"))
        root.addWidget(self._lbl_platform)

        self._g_mod = QGroupBox()
        fm = QFormLayout(self._g_mod)
        self.cb_mod1 = modifier_combo(self, settings.modifier_1)
        self.cb_mod2 = modifier_combo(self, settings.modifier_2)
        self._lbl_mod_slot1 = QLabel()
        self._lbl_mod_slot2 = QLabel()
        fm.addRow(self._lbl_mod_slot1, self.cb_mod1)
        fm.addRow(self._lbl_mod_slot2, self.cb_mod2)
        self._lbl_mod_hint = QLabel()
        self._lbl_mod_hint.setWordWrap(True)
        fm.addRow(self._lbl_mod_hint)
        root.addWidget(self._g_mod)

        self._g_player = QGroupBox()
        fp = QFormLayout(self._g_player)
        self._lbl_contest_play = QLabel()
        self.cb_contest_play = hotkey_key_combo(self, settings.key_contest_play)
        fp.addRow(self._lbl_contest_play, self.cb_contest_play)
        root.addWidget(self._g_player)

        self._g_live = QGroupBox()
        fl = QFormLayout(self._g_live)
        self._lbl_live_latch = QLabel()
        self.cb_live_latch = hotkey_key_combo(self, settings.key_live_ptt_latch)
        self._lbl_live_momentary = QLabel()
        self.cb_live_momentary = hotkey_key_combo(self, settings.key_live_ptt_momentary)
        fl.addRow(self._lbl_live_latch, self.cb_live_latch)
        fl.addRow(self._lbl_live_momentary, self.cb_live_momentary)
        root.addWidget(self._g_live)

        root.addStretch(1)
        self._load_from_settings()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.chk_enabled.setText(tr("settings.shortcuts.enabled"))
        self.chk_enabled.setToolTip(tr("settings.shortcuts.enabled_tooltip"))
        self._g_mod.setTitle(tr("settings.shortcuts.modifiers_group"))
        self.cb_mod1.setToolTip(tr("settings.shortcuts.modifier_tooltip"))
        self.cb_mod2.setToolTip(tr("settings.shortcuts.modifier_tooltip"))
        self._lbl_mod_slot1.setText(tr("settings.shortcuts.modifier_slot1"))
        self._lbl_mod_slot2.setText(tr("settings.shortcuts.modifier_slot2"))
        self._lbl_mod_hint.setText(tr("settings.shortcuts.modifiers_hint"))
        self._g_player.setTitle(tr("settings.shortcuts.group.player"))
        self._lbl_contest_play.setText(tr("settings.shortcuts.contest_play"))
        self.cb_contest_play.setToolTip(tr("settings.shortcuts.contest_play_tooltip"))
        retranslate_hotkey_combo_special_labels(self.cb_contest_play)
        self._g_live.setTitle(tr("settings.shortcuts.group.live"))
        self._lbl_live_latch.setText(tr("settings.shortcuts.live_ptt_latch"))
        self.cb_live_latch.setToolTip(tr("settings.shortcuts.live_ptt_latch_tooltip"))
        self._lbl_live_momentary.setText(tr("settings.shortcuts.live_ptt_momentary"))
        self.cb_live_momentary.setToolTip(tr("settings.shortcuts.live_ptt_momentary_tooltip"))
        retranslate_hotkey_combo_special_labels(self.cb_live_latch)
        retranslate_hotkey_combo_special_labels(self.cb_live_momentary)
        for i, (tr_key, data) in enumerate(
            (
                ("settings.shortcuts_mod_none", "none"),
                ("settings.shortcuts_mod_alt", "alt"),
                ("settings.shortcuts_mod_ctrl", "control"),
                ("settings.shortcuts_mod_shift", "shift"),
                ("settings.shortcuts_mod_win", "win"),
            )
        ):
            self.cb_mod1.setItemText(i, tr(tr_key))
            self.cb_mod2.setItemText(i, tr(tr_key))

    def _load_from_settings(self) -> None:
        s = self._settings
        self.chk_enabled.setChecked(bool(s.enabled))
        for cb, mod in (
            (self.cb_mod1, s.modifier_1),
            (self.cb_mod2, s.modifier_2),
        ):
            cur = str(mod or "none").strip().lower()
            for i in range(cb.count()):
                if cb.itemData(i) == cur:
                    cb.setCurrentIndex(i)
                    break

    def apply_to_settings(self, target: GlobalShortcutsSettings) -> None:
        target.enabled = bool(self.chk_enabled.isChecked())
        target.modifier_1 = str(self.cb_mod1.currentData() or "control")
        target.modifier_2 = str(self.cb_mod2.currentData() or "shift")
        target.key_contest_play = str(self.cb_contest_play.currentData() or "P")
        target.key_live_ptt_latch = str(self.cb_live_latch.currentData() or "X")
        target.key_live_ptt_momentary = str(self.cb_live_momentary.currentData() or "Y")
