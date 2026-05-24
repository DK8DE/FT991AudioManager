"""Kompakte CAT-Steuerung unter den Meter-Anzeigen (Tune, Simp/RPT±, REV, Audio)."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from mapping.repeater_offset import SHIFT_MINUS, SHIFT_PLUS, SHIFT_SIMPLEX

from .led_widget import Led
from .menu_icons import (
    control_bar_icon_size,
    control_bar_live_green_led_icon,
    control_bar_play_green_icon,
    control_bar_record_red_icon,
    control_bar_speaker_white_icon,
)

# Kurze rot/grün-Sequenz bei TCP-Daten (FLRig) — wie RotorTcpBridge.
_RIG_IO_BLINK_SEQ = (True, False, True, False, True, False, True, False)


class RadioControlBar(QWidget, RetranslatableMixin):
    """Tune, Repeater-Shift (Simp / RPT+ / RPT-), REV, Audio und FLRig."""

    repeater_minus_toggled = Signal(bool)
    tune_clicked = Signal()
    rev_toggled = Signal(bool)
    t_call_pressed = Signal()
    t_call_released = Signal()
    audio_player_clicked = Signal()
    audio_recorder_clicked = Signal()
    sound_settings_clicked = Signal()
    live_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._flrig_blink_active = False
        self._flrig_blink_phase = 0
        # Letzter Shift aus ``IF;`` P10 (0 Simplex, 1 Plus, 2 Minus) — für Button-Text.
        self._repeater_shift_dir: int = SHIFT_SIMPLEX

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._rpt_minus_btn = QPushButton(self._repeater_shift_caption(SHIFT_SIMPLEX))
        self._rpt_minus_btn.setCheckable(True)
        self._rpt_minus_btn.setMinimumWidth(56)
        self._rpt_minus_btn.toggled.connect(self._on_repeater_minus_toggled)

        self._tune_btn = QPushButton(tr("radio_control.tune"))
        self._tune_btn.setMinimumWidth(72)
        self._tune_btn.clicked.connect(self.tune_clicked.emit)

        self._rev_btn = QPushButton(tr("radio_control.rev"))
        self._rev_btn.setCheckable(True)
        self._rev_btn.setMinimumWidth(52)
        self._rev_btn.toggled.connect(self.rev_toggled.emit)

        self._tcall_btn = QPushButton(tr("radio_control.tcall"))
        self._tcall_btn.setMinimumWidth(58)
        self._tcall_btn.setStyleSheet(
            "QPushButton:pressed {"
            "  background-color: #5ddc7a;"
            "  color: #101010;"
            "  font-weight: bold;"
            "  border: 1px solid #2f8a47;"
            "}"
        )
        self._tcall_btn.pressed.connect(self.t_call_pressed.emit)
        self._tcall_btn.released.connect(self.t_call_released.emit)

        icon_size = control_bar_icon_size()

        self._audio_btn = QPushButton(tr("radio_control.audio_player"))
        self._audio_btn.setMinimumWidth(96)
        self._audio_btn.setIcon(control_bar_play_green_icon())
        self._audio_btn.setIconSize(icon_size)
        self._audio_btn.clicked.connect(self.audio_player_clicked.emit)

        self._recorder_btn = QPushButton(tr("radio_control.audio_recorder"))
        self._recorder_btn.setMinimumWidth(110)
        self._recorder_btn.setIcon(control_bar_record_red_icon())
        self._recorder_btn.setIconSize(icon_size)
        self._recorder_btn.clicked.connect(self.audio_recorder_clicked.emit)

        self._sound_btn = QPushButton(tr("radio_control.sound"))
        self._sound_btn.setMinimumWidth(72)
        self._sound_btn.setIcon(control_bar_speaker_white_icon())
        self._sound_btn.setIconSize(icon_size)
        self._sound_btn.clicked.connect(self.sound_settings_clicked.emit)

        self._live_btn = QPushButton(tr("radio_control.live"))
        self._live_btn.setMinimumWidth(70)
        self._live_btn.setIcon(control_bar_live_green_led_icon())
        self._live_btn.setIconSize(icon_size)
        self._live_btn.clicked.connect(self.live_clicked.emit)

        self._rpt_minus_btn.setStyleSheet(
            "QPushButton:checked {"
            "  background-color: #5aa9ff;"
            "  color: #101010;"
            "  font-weight: bold;"
            "  border: 1px solid #2a6aaa;"
            "}"
        )
        self._rev_btn.setStyleSheet(
            "QPushButton:checked {"
            "  background-color: #5ddc7a;"
            "  color: #101010;"
            "  font-weight: bold;"
            "  border: 1px solid #2f8a47;"
            "}"
        )

        ctrl_frame = QFrame(self)
        ctrl_frame.setObjectName("panelFrame")
        ctrl_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(8, 6, 8, 6)
        ctrl_layout.setSpacing(8)
        ctrl_layout.addWidget(self._tune_btn)
        ctrl_layout.addWidget(self._rpt_minus_btn)
        ctrl_layout.addWidget(self._rev_btn)
        ctrl_layout.addWidget(self._tcall_btn)
        ctrl_layout.addWidget(self._audio_btn)
        ctrl_layout.addWidget(self._recorder_btn)
        ctrl_layout.addWidget(self._live_btn)
        ctrl_layout.addWidget(self._sound_btn)
        ctrl_layout.addStretch(1)

        flrig_frame = QFrame(self)
        flrig_frame.setObjectName("panelFrame")
        flrig_frame.setFrameShape(QFrame.Shape.StyledPanel)
        flrig_layout = QHBoxLayout(flrig_frame)
        flrig_layout.setContentsMargins(10, 6, 10, 6)
        flrig_layout.setSpacing(8)

        self._lbl_flrig_title = QLabel(tr("radio_control.flrig.title"))
        lf = self._lbl_flrig_title.font()
        lf.setBold(True)
        self._lbl_flrig_title.setFont(lf)
        self._led_flrig = Led(9, flrig_frame)
        self._lbl_flrig_clients = QLabel(tr("common.dash"))
        self._lbl_flrig_clients.setMinimumWidth(22)
        self._lbl_flrig_clients.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        flrig_layout.addWidget(self._lbl_flrig_title)
        flrig_layout.addWidget(self._led_flrig)
        flrig_layout.addWidget(self._lbl_flrig_clients)

        root.addWidget(ctrl_frame, 1)
        root.addWidget(flrig_frame, 0, Qt.AlignmentFlag.AlignRight)
        self.set_controls_enabled(False)

        self._register_retranslate()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        bridge_tip = tr("radio_control.flrig.tooltip")
        self._rpt_minus_btn.setToolTip(tr("radio_control.repeater.tooltip"))
        self._apply_repeater_shift_button()
        self._tune_btn.setText(tr("radio_control.tune"))
        self._tune_btn.setToolTip(tr("radio_control.tune.tooltip"))
        self._rev_btn.setText(tr("radio_control.rev"))
        self._rev_btn.setToolTip(tr("radio_control.rev.tooltip"))
        self._tcall_btn.setText(tr("radio_control.tcall"))
        self._tcall_btn.setToolTip(tr("radio_control.tcall.tooltip"))
        self._audio_btn.setText(tr("radio_control.audio_player"))
        self._audio_btn.setToolTip(tr("radio_control.audio_player.tooltip"))
        self._recorder_btn.setText(tr("radio_control.audio_recorder"))
        self._recorder_btn.setToolTip(tr("radio_control.audio_recorder.tooltip"))
        self._live_btn.setText(tr("radio_control.live"))
        self._live_btn.setToolTip(tr("radio_control.live.tooltip"))
        self._sound_btn.setText(tr("radio_control.sound"))
        self._sound_btn.setToolTip(tr("radio_control.sound.tooltip"))
        self._lbl_flrig_title.setText(tr("radio_control.flrig.title"))
        self._lbl_flrig_title.setToolTip(bridge_tip)
        self._led_flrig.setToolTip(bridge_tip)
        self._lbl_flrig_clients.setToolTip(tr("radio_control.flrig.clients_tooltip"))

    def refresh_rig_bridge_indicators(
        self,
        rig_bridge_cfg: dict[str, Any],
        proto_status: dict[str, Any],
        flrig_io: bool,
    ) -> None:
        """Aktualisiert LED und Client-Zähler (periodisch vom Hauptfenster)."""
        rb: dict[str, Any] = rig_bridge_cfg if isinstance(rig_bridge_cfg, dict) else {}
        rb_on = bool(rb.get("enabled", True))
        raw_flrig = rb.get("flrig")
        fl_cfg: dict[str, Any] = raw_flrig if isinstance(raw_flrig, dict) else {}

        proto: dict[str, Any] = (
            proto_status if isinstance(proto_status, dict) else {}
        )

        fl_want = rb_on and bool(fl_cfg.get("enabled", True))

        fl_on = bool(proto.get("flrig_active"))
        n_fl = int(proto.get("flrig_clients", 0) or 0)

        seq = _RIG_IO_BLINK_SEQ
        if flrig_io and fl_want and fl_on:
            self._flrig_blink_active = True
            self._flrig_blink_phase = 0
        if self._flrig_blink_active:
            if self._flrig_blink_phase < len(seq):
                self._led_flrig.set_state(bool(seq[self._flrig_blink_phase]))
                self._flrig_blink_phase += 1
            else:
                self._flrig_blink_active = False
        if not self._flrig_blink_active:
            self._led_flrig.set_state(bool(fl_want and fl_on))
        if fl_want and fl_on:
            self._lbl_flrig_clients.setText(str(n_fl))
        else:
            self._lbl_flrig_clients.setText(tr("common.dash"))

    def set_rev_checked(self, checked: bool) -> None:
        self._rev_btn.blockSignals(True)
        self._rev_btn.setChecked(checked)
        self._rev_btn.blockSignals(False)

    @staticmethod
    def _repeater_shift_caption(direction: int) -> str:
        d = int(direction)
        if d == SHIFT_PLUS:
            return tr("radio_control.repeater.rpt_plus")
        if d == SHIFT_MINUS:
            return tr("radio_control.repeater.rpt_minus")
        return tr("radio_control.repeater.simp")

    def _apply_repeater_shift_button(self) -> None:
        """Setzt Text; Checked nur bei Minus (Taste = Minus ein/aus)."""
        d = int(self._repeater_shift_dir)
        self._rpt_minus_btn.setText(self._repeater_shift_caption(d))

    def sync_repeater_shift_from_if(self, direction: int) -> None:
        """TRX-Stand aus ``IF;`` P10 — Beschriftung Simp / RPT+ / RPT-, Checked nur bei Minus."""
        d = int(direction)
        if d not in (SHIFT_SIMPLEX, SHIFT_PLUS, SHIFT_MINUS):
            d = SHIFT_SIMPLEX
        self._repeater_shift_dir = d
        self._rpt_minus_btn.blockSignals(True)
        self._rpt_minus_btn.setChecked(d == SHIFT_MINUS)
        self._rpt_minus_btn.blockSignals(False)
        self._apply_repeater_shift_button()

    def _on_repeater_minus_toggled(self, checked: bool) -> None:
        if checked:
            self._repeater_shift_dir = SHIFT_MINUS
        else:
            self._repeater_shift_dir = SHIFT_SIMPLEX
        self._apply_repeater_shift_button()
        self.repeater_minus_toggled.emit(bool(checked))

    def set_repeater_minus_checked(self, checked: bool) -> None:
        chk = bool(checked)
        self._repeater_shift_dir = SHIFT_MINUS if chk else SHIFT_SIMPLEX
        self._rpt_minus_btn.blockSignals(True)
        self._rpt_minus_btn.setChecked(chk)
        self._rpt_minus_btn.blockSignals(False)
        self._apply_repeater_shift_button()

    def is_repeater_minus_checked(self) -> bool:
        return bool(self._rpt_minus_btn.isChecked())

    def is_t_call_pressed(self) -> bool:
        return self._tcall_btn.isDown()

    def set_t_call_active(self, active: bool) -> None:
        self._tcall_btn.setDown(bool(active))

    def set_controls_enabled(self, enabled: bool) -> None:
        self._rpt_minus_btn.setEnabled(enabled)
        self._tune_btn.setEnabled(enabled)
        self._rev_btn.setEnabled(enabled)
        self._tcall_btn.setEnabled(enabled)
        if not enabled:
            self.set_rev_checked(False)
            self.set_t_call_active(False)
            self.set_repeater_minus_checked(False)
        self._audio_btn.setEnabled(True)
        self._recorder_btn.setEnabled(True)
        self._live_btn.setEnabled(True)
        self._sound_btn.setEnabled(True)
