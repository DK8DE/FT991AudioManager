"""Live-Monitoring: Mikrofon → DSP-Ausgang (sounddevice, getrennt vom Audio‑Player)."""

from __future__ import annotations

import base64
import ctypes
import sys
from typing import TYPE_CHECKING, Any, Callable, List, Optional, cast

from PySide6.QtCore import (
    QObject,
    QByteArray,
    QEvent,
    QMetaObject,
    QThread,
    Qt,
    QTimer,
    Q_ARG,
    Signal,
    Slot,
)
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui.app_icon import app_icon
from gui.touch_slider import TouchSlider
from gui.momentary_hold_button import MomentaryHoldButton
from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from gui.live_eq_editor_widget import LiveEqEditorWidget
from gui.meter_widget import (
    ScaledMeterBar,
    TxIndicator,
    live_dbfs_peak_to_raw,
    make_live_level_bar,
)
from audio.cat_ptt_worker import CatPttWorker
from audio.player_controller import _invoke_ptt_worker_set_transmit
from audio.radio_playback_setup import data_mode_for_rx_mode
from audio.windows_endpoint_volume import invalidate_windows_audio_device_cache
from cat import SerialCAT
from cat.ft991_cat import FT991CAT
from mapping import TX_STATE_CAT_TX, TX_STATE_MIC_PTT, TX_STATE_RX
from live.live_audio_engine import LiveAudioEngine
from live.live_devices import remap_live_settings_devices
from mapping.rx_mapping import RxMode
from model import AppSettings
from model.live_settings import (
    DEFAULT_BLOCKSIZE,
    DEFAULT_SAMPLERATE,
    LiveCompressorSettings,
    LiveEqBandSettings,
    LiveFunkListenGateSettings,
    LiveGateSettings,
    LiveSettings,
)
from model.live_audio_profile import LiveAudioProfile
from model.live_audio_profile_store import (
    DEFAULT_LIVE_AUDIO_PROFILE_NAME,
    LiveAudioProfileStore,
)
from model.live_volume_curve import (
    live_gain_display_percent,
    live_gain_from_slider,
    live_slider_from_gain,
)

if TYPE_CHECKING:
    from gui.audio_radio_session import AudioRadioSessionHost


_YAESU_GREEN = "#52c41a"

# PTT-Buttons: identische Padding-/Rahmenwerte für Ruhezustand und aktiv, sonst „wächst“
# der Knopf beim Umschalten (Systemstil ≠ explizites QSS).
_LIVE_PTT_BTN_PAD = "6px 16px"
_LIVE_PTT_BTN_RADIUS = "4px"
_LIVE_PTT_BTN_BORDER = "2px"


def _live_ptt_idle_button_style() -> str:
    return (
        "QPushButton {"
        " background-color: palette(button); color: palette(button-text);"
        " font-weight: 600;"
        f" padding: {_LIVE_PTT_BTN_PAD}; border-radius: {_LIVE_PTT_BTN_RADIUS};"
        f" border: {_LIVE_PTT_BTN_BORDER} solid palette(mid);"
        "}"
    )


def _live_ptt_active_button_style(green_hex: str = _YAESU_GREEN) -> str:
    return (
        f"QPushButton {{ background-color:{green_hex}; color:#141414;"
        " font-weight: 600;"
        f" padding: {_LIVE_PTT_BTN_PAD}; border-radius: {_LIVE_PTT_BTN_RADIUS};"
        f" border: {_LIVE_PTT_BTN_BORDER} solid {green_hex}; }}"
    )


def _invoke_setup_worker_slot(receiver: QObject, method_name: str, *args: object) -> None:
    """Queued ``invokeMethod`` für RadioSetupWorker-Slots (PySide6 6.x)."""
    invoke = cast(Any, QMetaObject.invokeMethod)
    invoke(receiver, method_name, Qt.ConnectionType.QueuedConnection, *args)


# Puffer nach frischem DATA-Umschalten, bevor der Audio-Stream startet.
_CAT_LIVE_RADIO_SETTLE_MS = 40


def effective_live_tx_display_state(
    polled_state: int,
    *,
    want_live_transport: bool,
    cat_tx_armed: bool,
    cat_live_start_busy: bool,
    engine_running: bool,
) -> int:
    """TX-Anzeige optimistisch halten, bis der Poll RX meldet obwohl PTT aktiv ist."""
    if (
        int(polled_state) == TX_STATE_RX
        and want_live_transport
        and (cat_tx_armed or cat_live_start_busy or engine_running)
    ):
        return TX_STATE_CAT_TX
    return int(polled_state)

_VK_CONTROL = 0x11
_VK_Y = 0x59


def _ctrl_y_physically_held() -> bool:
    """True solange Strg und Y hardwareseitig gedrückt sind (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        u = ctypes.windll.user32
        ctrl = (u.GetAsyncKeyState(_VK_CONTROL) & 0x8000) != 0
        y_down = (u.GetAsyncKeyState(_VK_Y) & 0x8000) != 0
        return bool(ctrl and y_down)
    except Exception:
        return False


def _live_window_accepts_background_audio(w: "LiveWindow") -> bool:
    """Live-Fenster offen oder minimiert — Audio/PTT soll weiterlaufen."""
    if getattr(w, "_force_close", False):
        return False
    return bool(w.isVisible() or w.isMinimized())


def live_session_holds_data_mode() -> bool:
    """True solange Live offen ist und der Funk im DATA-Modus bleiben soll."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return False
    for w in app.topLevelWidgets():
        if not isinstance(w, LiveWindow):
            continue
        if not _live_window_accepts_background_audio(w):
            continue
        if bool(getattr(w, "_live_data_session_active", False)):
            return True
    return False


def _should_release_ptt_on_window_leave(
    *,
    visible: bool,
    minimized: bool,
    force_close: bool,
) -> bool:
    """PTT nur loslassen wenn das Fenster wirklich verlassen wurde — nicht beim Minimieren."""
    if force_close or minimized or not visible:
        return False
    return True


def _focused_live_window() -> Optional["LiveWindow"]:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return None
    fw = app.focusWidget()
    w = fw if isinstance(fw, QWidget) else None
    while w is not None:
        if isinstance(w, LiveWindow):
            if _live_window_accepts_background_audio(w):
                return w
            return None
        w = w.parentWidget()
    aw = app.activeWindow()
    if isinstance(aw, LiveWindow) and _live_window_accepts_background_audio(aw):
        return aw
    return None


def _maybe_end_ctrl_y_ptt(lw: "LiveWindow") -> None:
    if not lw._kbd_ptt_momentary_engaged:
        return
    if sys.platform == "win32" and _ctrl_y_physically_held():
        return
    lw._kbd_native_apply_momentary_end()


class _LiveCtrlYKeyFilter(QObject):
    """Strg+Y Push-to-talk: KeyPress startet, KeyRelease nur bei echt losgelassenen Tasten."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: ARG002
        if not isinstance(event, QKeyEvent):
            return False
        if event.type() == QEvent.Type.KeyPress:
            return self._on_key_press(event)
        if event.type() == QEvent.Type.KeyRelease:
            self._on_key_release(event)
            return False
        return False

    @staticmethod
    def _on_key_press(event: QKeyEvent) -> bool:
        if event.key() != Qt.Key.Key_Y or event.isAutoRepeat():
            return False
        km = event.modifiers() | QApplication.keyboardModifiers()
        blocked = (
            Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if not bool(km & Qt.KeyboardModifier.ControlModifier) or bool(km & blocked):
            return False
        lw = _focused_live_window()
        if lw is None:
            return False
        QTimer.singleShot(0, lw._kbd_native_apply_momentary_start)
        event.accept()
        return True

    @staticmethod
    def _on_key_release(event: QKeyEvent) -> None:
        if event.key() not in (
            Qt.Key.Key_Y,
            Qt.Key.Key_Control,
            Qt.Key.Key_Meta,
        ):
            return
        lw = _live_window_with_kbd_ptt_engaged()
        if lw is None:
            return
        QTimer.singleShot(0, lambda lw=lw: _maybe_end_ctrl_y_ptt(lw))


_live_ctrl_y_filter: Optional[_LiveCtrlYKeyFilter] = None
_live_ctrl_y_filter_refcount = 0


def _live_window_with_kbd_ptt_engaged() -> Optional["LiveWindow"]:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return None
    for w in app.topLevelWidgets():
        if (
            isinstance(w, LiveWindow)
            and _live_window_accepts_background_audio(w)
            and w._kbd_ptt_momentary_engaged
        ):
            return w
    return None


def _live_ctrl_y_filter_acquire() -> None:
    global _live_ctrl_y_filter
    global _live_ctrl_y_filter_refcount

    if _live_ctrl_y_filter_refcount == 0:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            _live_ctrl_y_filter = _LiveCtrlYKeyFilter(app)
            app.installEventFilter(_live_ctrl_y_filter)
    _live_ctrl_y_filter_refcount += 1


def _live_ctrl_y_filter_release() -> None:
    global _live_ctrl_y_filter
    global _live_ctrl_y_filter_refcount

    if _live_ctrl_y_filter_refcount <= 0:
        return
    _live_ctrl_y_filter_refcount -= 1
    if _live_ctrl_y_filter_refcount != 0:
        return
    app = QApplication.instance()
    if isinstance(app, QApplication) and _live_ctrl_y_filter is not None:
        app.removeEventFilter(_live_ctrl_y_filter)
    _live_ctrl_y_filter = None


class _LiveStreamParamsPreviewWorker(QObject):
    """Ermittelt Samplerate + Blockgröße im Hintergrund (WASAPI/COM/PortAudio)."""

    preview_requested = Signal(object, int)
    finished = Signal(int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.preview_requested.connect(
            self._run_preview,
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot(object, int)
    def _run_preview(self, live_dict: object, request_id: int) -> None:
        data = live_dict if isinstance(live_dict, dict) else {}
        try:
            liv = LiveSettings.from_dict(data)
            sr, bs = LiveAudioEngine.preview_stream_params(liv)
        except Exception:
            raw_sr = data.get("samplerate", DEFAULT_SAMPLERATE)
            raw_bs = data.get("blocksize", DEFAULT_BLOCKSIZE)
            try:
                sr = int(raw_sr)
            except (TypeError, ValueError):
                sr = int(DEFAULT_SAMPLERATE)
            try:
                bs = int(raw_bs)
            except (TypeError, ValueError):
                bs = int(DEFAULT_BLOCKSIZE)
        self.finished.emit(request_id, sr, bs)


class LiveWindow(QMainWindow, RetranslatableMixin):
    """Regler spiegeln Settings; Engine liest kopiertes ``LiveSettings`` im Callback."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        persist_settings: Callable[[], None],
        open_sound_settings: Optional[Callable[[], None]] = None,
        serial_cat: Optional[SerialCAT] = None,
        audio_radio_session: Optional["AudioRadioSessionHost"] = None,
        operating_mode_provider: Optional[Callable[[], RxMode]] = None,
        other_audio_blocking: Optional[Callable[[], str]] = None,
        request_cat_tx_poll: Optional[Callable[[], None]] = None,
        profile_widget: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        del parent
        super().__init__(None)
        self._settings = settings
        self._persist = persist_settings
        self._open_sound_settings = open_sound_settings
        self._profile_widget = profile_widget
        self._live_snapshot = LiveSettings.from_dict(settings.live.to_dict())
        self._live_audio_profile_store = LiveAudioProfileStore.load()
        self._live_profile_loading = False
        self._cat = serial_cat
        self._audio_radio_session = audio_radio_session
        self._operating_mode_provider = operating_mode_provider
        self._other_audio_blocking_fn = other_audio_blocking
        self._request_cat_tx_poll_fn = request_cat_tx_poll
        self._radio_setup = (
            audio_radio_session.setup if audio_radio_session is not None else None
        )
        self._setup_worker = (
            audio_radio_session.worker if audio_radio_session is not None else None
        )
        self._pending_live_after_pc_then_engage = False
        self._live_cat_waiting_engage_finish = False
        self._cat_live_start_busy = False
        self._mic_ptt_interrupted_live = False
        self._ptt_worker: Optional[CatPttWorker] = None
        self._ptt_thread: Optional[QThread] = None

        self.setWindowTitle(tr("live.window.title"))
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # Kein Parent — frei beweglich, MainWindow kann darüber liegen (s. LogWindow).
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(980, 720)

        self._engine = LiveAudioEngine(on_error=lambda _msg: None)

        self._sr_preview_thread = QThread(self)
        self._sr_preview_worker = _LiveStreamParamsPreviewWorker()
        self._sr_preview_worker.moveToThread(self._sr_preview_thread)
        self._sr_preview_worker.finished.connect(self._on_stream_params_preview_finished)
        self._sr_preview_thread.start()
        self._sr_preview_req = 0
        self._sr_preview_pending: Optional[int] = None
        self._sr_refresh_monitors_after = False

        if self._cat is not None and self._radio_setup is not None:
            if self._setup_worker is not None:
                self._setup_worker.pc_menus_finished.connect(
                    self._on_live_radio_pc_menus_for_start,
                )
                self._setup_worker.pc_menus_finished.connect(
                    self._on_live_session_pc_menus_finished,
                )
                self._setup_worker.engage_data_finished.connect(
                    self._on_live_radio_engage_finished,
                )
            self._ptt_thread = QThread(self)
            self._ptt_worker = CatPttWorker(
                self._cat,
                fast_tx_on_no_wait=True,
            )
            self._ptt_worker.moveToThread(self._ptt_thread)
            self._ptt_worker.failed.connect(self._on_live_ptt_failed)
            self._ptt_thread.start()

        self._live_cat_settle = QTimer(self)
        self._live_cat_settle.setSingleShot(True)
        self._live_cat_settle.timeout.connect(self._on_live_radio_settled_start_engine)

        self._build_ui()
        self._sync_live_devices_from_settings(refresh_monitors=False)
        self._apply_live_to_ui()
        self._reload_live_audio_profile_combo(select_last=True)

        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(60)
        self._meter_timer.timeout.connect(self._meter_tick)
        self._meter_timer.start()

        self._restore_geometry()

        self._suppress_idle_listen_monitor = False
        self._idle_monitor_fp_key: Optional[tuple[object, ...]] = None
        self._mic_preview_fp_key: Optional[tuple[object, ...]] = None
        self._vol_persist_timer = QTimer(self)
        self._vol_persist_timer.setSingleShot(True)
        self._vol_persist_timer.setInterval(400)
        self._vol_persist_timer.timeout.connect(self._persist)
        self._suppress_funk_listen_while_live_tx_active = False
        self._live_cat_tx_armed = False
        #: Fester Taste „PTT“ (gedrückt = Live‑Transport an).
        self._live_ptt_momentary_held = False
        #: Nur wenn Momentary‑PTT durch Strg+Y (Hotkey) aktiv ist.
        self._kbd_ptt_momentary_engaged = False
        #: Globaler KeyRelease‑Filter für Strg+Y (Refcount über sichtbare Live‑Fenster).
        self._live_ctrl_y_filter_acquired = False
        self._live_footer_error: Optional[str] = None
        self._last_synced_tx_state: Optional[int] = None
        self._resume_live_transport_after_reconnect = False
        self._resume_live_engine_after_reconnect = False
        self._resume_idle_monitor_after_reconnect = False
        self._reconnect_engine_resume_pending = False
        self._live_data_session_active = False
        self._pending_open_window_data_engage = False
        self._engage_data_session_only = False
        self._refresh_ptt_button_appearance()
        self._refresh_ptt_controls_enabled()
        self._install_live_keyboard_shortcuts()
        self._ensure_live_ctrl_y_filter_acquired()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("live.window.title"))
        self._btn_audio_routing.setText(tr("live.btn.audio_routing"))
        self._btn_audio_routing.setToolTip(tr("live.open_sound_settings.tooltip"))
        self._b_afl.setText(tr("live.btn.afl"))
        self._b_afl.setToolTip(tr("live.btn.afl.tooltip"))
        self._refresh_afl_button_appearance()
        self._group_eq.setTitle(tr("live.group.eq"))
        self._chk_eq_master.setText(tr("live.check.eq_master"))
        self._cap_strip_mic.setText(tr("live.strip.mic_send"))
        self._cap_strip_mon.setText(tr("live.strip.monitor"))
        self._cap_strip_funk_in.setText(tr("live.strip.funk_in"))
        self._cap_strip_funk_out.setText(tr("live.strip.funk_out"))
        self._group_noise_gate.setTitle(tr("live.group.noise_gate"))
        self._g_en.setText(tr("live.gate.enabled"))
        self._gate_thr_lbl_caption.setText(tr("live.gate.threshold"))
        self._gate_att_lbl_caption.setText(tr("live.gate.attack"))
        self._gate_hld_lbl_caption.setText(tr("live.gate.hold"))
        self._gate_rel_lbl_caption.setText(tr("live.gate.release"))
        self._group_rx_noise_gate.setTitle(tr("live.group.rx_noise_gate"))
        self._fg_en.setText(tr("live.funk_gate.enabled"))
        self._fg_thr_lbl_caption.setText(tr("live.funk_gate.threshold"))
        self._fg_att_lbl_caption.setText(tr("live.funk_gate.attack"))
        self._fg_hld_lbl_caption.setText(tr("live.funk_gate.hold"))
        self._fg_rel_lbl_caption.setText(tr("live.funk_gate.release"))
        self._group_compressor.setTitle(tr("live.group.compressor"))
        self._c_en.setText(tr("live.comp.enabled"))
        self._comp_thr_lbl_caption.setText(tr("live.comp.threshold"))
        self._comp_rat_lbl_caption.setText(tr("live.comp.ratio"))
        self._comp_att_lbl_caption.setText(tr("live.comp.attack"))
        self._comp_rel_lbl_caption.setText(tr("live.comp.release"))
        self._comp_mk_lbl_caption.setText(tr("live.comp.makeup"))
        self._b_ptt.setText(tr("live.btn.ptt"))
        self._b_ptt.setToolTip(tr("live.btn.ptt.tooltip"))
        self._b_ptt_latch.setText(tr("live.btn.ptt_latch"))
        self._b_ptt_latch.setToolTip(tr("live.btn.ptt_latch.tooltip"))
        self._live_profile_combo.setToolTip(tr("live.profile.combo.tooltip"))
        self._b_live_profile_save.setText(tr("live.profile.btn.save"))
        self._b_live_profile_save.setToolTip(tr("live.profile.btn.save.tooltip"))
        self._b_live_profile_update.setText(tr("live.profile.btn.update"))
        self._b_live_profile_update.setToolTip(tr("live.profile.btn.update.tooltip"))
        self._b_live_profile_delete.setText(tr("live.profile.btn.delete"))
        self._b_live_profile_delete.setToolTip(tr("live.profile.btn.delete.tooltip"))
        self._tx_label.setToolTip(tr("live.tx_label.tooltip"))
        self._sync_live_eq_master_look()
        self._refresh_gate_comp_readouts()
        self._refresh_live_footer()

    def _refresh_live_footer(self) -> None:
        lbl = getattr(self, "_lbl_live_footer", None)
        if lbl is None:
            return
        err = getattr(self, "_live_footer_error", None)
        if err:
            err_one_line = " ".join(str(err).split())
            lbl.setText(err_one_line)
            lbl.setStyleSheet("color:#ffb74d;font-size:11px;")
            lbl.setToolTip(err_one_line)
            return
        liv = self._live_snapshot
        cached_sr = int(liv.samplerate)
        cached_bs = int(liv.blocksize)
        if cached_sr > 0:
            sr_text = tr("live.label.samplerate.value", sr=cached_sr)
        else:
            sr_text = tr("live.label.samplerate.pending")
        bs_text = str(cached_bs) if cached_bs > 0 else tr("common.dash")
        lbl.setText(
            f"{tr('live.label.samplerate')} {sr_text}   ·   "
            f"{tr('live.label.block')} {bs_text}"
        )
        lbl.setStyleSheet("color:#9a9a9a;font-size:11px;")
        lbl.setToolTip(tr("live.label.samplerate.tooltip"))

    def _live_engine_runtime_settings_overlay(self, liv: LiveSettings) -> LiveSettings:
        """Übergabe an Audio-Engine — Funk-Eing.-Mithören während Sendung ausblenden."""
        out = LiveSettings.from_dict(liv.to_dict())
        if getattr(self, "_suppress_funk_listen_while_live_tx_active", False):
            out.funk_listen_enabled = False
        return out

    def _push_live_engine_runtime_settings(self, liv: LiveSettings) -> None:
        self._engine.push_settings(self._live_engine_runtime_settings_overlay(liv))

    def _sync_live_funk_listen_mute_while_cat_tx(self, transmitting: bool) -> None:
        """Während Start Live + Sendung (TX≠RX) Funk-Eingang auf Monitor stumm wenn Mithören an."""
        if not self._engine.is_running():
            if getattr(self, "_suppress_funk_listen_while_live_tx_active", False):
                self._suppress_funk_listen_while_live_tx_active = False
            return

        hold = getattr(self, "_suppress_funk_listen_while_live_tx_active", False)
        if transmitting:
            if not hold:
                self._suppress_funk_listen_while_live_tx_active = True
                self._push_live_engine_runtime_settings(self._gather_live_from_ui())
            return

        if hold:
            self._suppress_funk_listen_while_live_tx_active = False
            self._push_live_engine_runtime_settings(self._gather_live_from_ui())

    # --- PTT: Live‑Transport („gedrückt halten“ / „PTT halten“) -----------------

    def _desired_live_transport_on(self) -> bool:
        latch_on = bool(
            getattr(self, "_b_ptt_latch", None)
            and self._b_ptt_latch.isChecked(),
        )
        return bool(self._live_ptt_momentary_held or latch_on)

    def _refresh_ptt_button_appearance(self) -> None:
        latched = bool(
            getattr(self, "_b_ptt_latch", None)
            and self._b_ptt_latch.isChecked(),
        )
        active_style = _live_ptt_active_button_style()
        idle_style = _live_ptt_idle_button_style()
        if getattr(self, "_b_ptt", None):
            self._b_ptt.setStyleSheet(
                active_style if self._live_ptt_momentary_held else idle_style
            )
        if getattr(self, "_b_ptt_latch", None):
            self._b_ptt_latch.setStyleSheet(active_style if latched else idle_style)

    def _refresh_afl_button_appearance(self) -> None:
        btn = getattr(self, "_b_afl", None)
        if btn is None:
            return
        btn.setStyleSheet(
            _live_ptt_active_button_style()
            if btn.isChecked()
            else _live_ptt_idle_button_style()
        )

    def _refresh_ptt_controls_enabled(self) -> None:
        """PTT-Knopf nicht während CAT-Start ausgrauen — wirkt sonst „hängend“."""
        return

    def _apply_immediate_ptt_feedback(self) -> None:
        """PTT-Optik und TX-LED sofort — nicht auf den nächsten CAT-Poll warten."""
        self._refresh_ptt_button_appearance()
        if self._desired_live_transport_on():
            self._update_tx_rx_led(TX_STATE_CAT_TX)
            if (
                not self._cat_live_start_busy
                and not self._live_cat_waiting_engage_finish
                and not self._pending_live_after_pc_then_engage
            ):
                fn = self._request_cat_tx_poll_fn
                if callable(fn):
                    fn()
            return
        if (
            not self._engine.is_running()
            and not self._cat_live_start_busy
            and not self._live_cat_tx_armed
        ):
            self._update_tx_rx_led(TX_STATE_RX)

    def _clear_live_ptt_wants(self) -> None:
        self._live_ptt_momentary_held = False
        if getattr(self, "_b_ptt_latch", None) is None:
            return
        self._b_ptt_latch.blockSignals(True)
        self._b_ptt_latch.setChecked(False)
        self._b_ptt_latch.blockSignals(False)
        self._refresh_ptt_button_appearance()
        self._refresh_ptt_controls_enabled()

    def _clear_live_transport_pending_flags(self) -> None:
        """Läufiger Funk/CAT‑Anlauf abbrechen (ohne zweifelsfrei bereits laufenden Stream)."""
        self._live_cat_settle.stop()
        self._suppress_funk_listen_while_live_tx_active = False
        self._live_cat_tx_armed = False
        self._cat_live_start_busy = False
        self._pending_live_after_pc_then_engage = False
        self._live_cat_waiting_engage_finish = False

    def _request_live_cat_tx_on(self) -> None:
        """CAT‑TX sofort anfordern — vor Audio‑Stream‑Start (subjektiv schnelleres PTT)."""
        if self._live_cat_tx_armed:
            return
        if self._ptt_worker is None or self._cat is None or not self._cat.is_connected():
            return
        if not self._desired_live_transport_on():
            return
        self._live_cat_tx_armed = True
        self._suppress_funk_listen_while_live_tx_active = True
        _invoke_ptt_worker_set_transmit(self._ptt_worker, True)

    def _sync_ptt_live_transport(self) -> None:
        want = self._desired_live_transport_on()
        running = self._engine.is_running()
        pending = bool(self._cat_live_start_busy)

        if want:
            self._apply_immediate_ptt_feedback()

        if not want:
            self._stop_live_via_ptt(clear_ptt_wants=False)
        elif want and (not pending) and (not running):
            self._start_live_via_ptt()

        self._refresh_ptt_controls_enabled()
        self._refresh_ptt_button_appearance()
        if not want:
            self._apply_immediate_ptt_feedback()

    def _on_live_ptt_momentary_pressed(self) -> None:
        self._live_ptt_momentary_held = True
        self._sync_ptt_live_transport()

    def _on_live_ptt_momentary_released(self) -> None:
        self._live_ptt_momentary_held = False
        self._sync_ptt_live_transport()

    def _on_live_ptt_latch_toggled(self, _checked: bool) -> None:
        del _checked
        self._sync_ptt_live_transport()

    def _release_keyboard_ptt_momentary(self) -> None:
        """Tastatur‑PTT loslassen (deaktivieren / minimieren / schließen).

        Wird die PTT‑Taste weiter mit der Maus gehalten, bleibt der Live‑Pfad an.
        """
        if not self._kbd_ptt_momentary_engaged:
            return
        self._kbd_ptt_momentary_engaged = False
        b = getattr(self, "_b_ptt", None)
        if b is not None and (b.isDown() or getattr(b, "is_held", lambda: False)()):
            return
        self._on_live_ptt_momentary_released()

    def _toggle_ptt_latch_from_keyboard(self) -> None:
        self._release_keyboard_ptt_momentary()
        btn = getattr(self, "_b_ptt_latch", None)
        if btn is None or not btn.isEnabled():
            return
        btn.toggle()

    def _install_live_keyboard_shortcuts(self) -> None:
        """Strg+Y: EventFilter (halten/loslassen); Strg+X: Shortcut PTT halten."""
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        latch = QShortcut(QKeySequence("Ctrl+X"), self)
        latch.setContext(ctx)
        latch.setAutoRepeat(False)
        latch.activated.connect(self._shortcut_ctrl_x_ptt_latch)
        self._sc_live_ptt_latch = latch

    def _ensure_live_ctrl_y_filter_acquired(self) -> None:
        if self._live_ctrl_y_filter_acquired:
            return
        _live_ctrl_y_filter_acquire()
        self._live_ctrl_y_filter_acquired = True

    def _ensure_live_ctrl_y_filter_released(self) -> None:
        if not self._live_ctrl_y_filter_acquired:
            return
        _live_ctrl_y_filter_release()
        self._live_ctrl_y_filter_acquired = False

    def _shortcut_ctrl_x_ptt_latch(self) -> None:
        if not _live_window_accepts_background_audio(self):
            return
        self._toggle_ptt_latch_from_keyboard()

    def _kbd_native_apply_momentary_start(self) -> None:
        if not _live_window_accepts_background_audio(self):
            return
        btn = getattr(self, "_b_ptt", None)
        if btn is None or not btn.isEnabled():
            return
        if self._kbd_ptt_momentary_engaged:
            return
        self._kbd_ptt_momentary_engaged = True
        self._on_live_ptt_momentary_pressed()

    def _kbd_native_apply_momentary_end(self) -> None:
        """Nur wenn der Hotkey PTT aktiv war — Maus gedrückt PTT erhält Vorrecht."""
        if not self._kbd_ptt_momentary_engaged:
            return
        self._kbd_ptt_momentary_engaged = False
        b = getattr(self, "_b_ptt", None)
        if b is not None and (b.isDown() or getattr(b, "is_held", lambda: False)()):
            return
        self._on_live_ptt_momentary_released()

    def changeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        if event.type() == QEvent.Type.WindowDeactivate:
            # Nach Minimieren ist isMinimized() erst im nächsten Event-Loop-Tick gesetzt.
            QTimer.singleShot(0, self._maybe_release_ptt_on_deactivate)
        super().changeEvent(event)

    def _maybe_release_ptt_on_deactivate(self) -> None:
        if not _should_release_ptt_on_window_leave(
            visible=self.isVisible(),
            minimized=self.isMinimized(),
            force_close=bool(getattr(self, "_force_close", False)),
        ):
            return
        self._release_keyboard_ptt_momentary()
        b = getattr(self, "_b_ptt", None)
        if b is not None and hasattr(b, "release_hold"):
            b.release_hold()

    def _stop_live_via_ptt(
        self,
        *,
        clear_ptt_wants: bool = False,
        invoke_release_voice_after_live: bool = True,
        refresh_idle_monitor_after: bool = True,
    ) -> None:
        self._clear_live_transport_pending_flags()
        self._mic_ptt_interrupted_live = False
        self._push_live_engine_runtime_settings(
            LiveSettings.from_dict(self._live_snapshot.to_dict()),
        )
        if self._engine.is_running():
            self._engine.stop()
        self._safe_live_cat_tx_off()
        if invoke_release_voice_after_live:
            self._release_voice_plain_after_stop_live_if_not_mithoren()
        if refresh_idle_monitor_after:
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
        if clear_ptt_wants:
            self._clear_live_ptt_wants()
        else:
            self._refresh_ptt_controls_enabled()
            self._refresh_ptt_button_appearance()
        self._apply_immediate_ptt_feedback()

    def _start_live_via_ptt(self) -> None:
        self._pull_sliders_into_snapshot()
        liv = LiveSettings.from_dict(self._gather_live_from_ui().to_dict())
        self._live_snapshot = liv
        self._settings.live = liv
        self._vol_persist_timer.start()

        prereq_ok, err = self._engine.prerequisites_ok()
        if not prereq_ok:
            QMessageBox.warning(self, tr("live.msgbox.title"), err)
            self._clear_live_ptt_wants()
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
            self._refresh_ptt_controls_enabled()
            self._refresh_ptt_button_appearance()
            return

        if self._radio_setup is None or self._setup_worker is None:
            ok, msg = self._engine.start(LiveSettings.from_dict(liv.to_dict()))
            if not ok:
                self._safe_live_cat_tx_off()
                QMessageBox.warning(
                    self,
                    tr("live.msgbox.start_failed.title"),
                    msg,
                )
                self._clear_live_ptt_wants()
                QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
                self._refresh_ptt_controls_enabled()
                self._refresh_ptt_button_appearance()
                return
            self._push_live_engine_runtime_settings(
                LiveSettings.from_dict(liv.to_dict())
            )
            self._refresh_ptt_controls_enabled()
            self._refresh_ptt_button_appearance()
            return

        self._cat_live_start_busy = True
        self._live_cat_waiting_engage_finish = False
        self._pending_live_after_pc_then_engage = False
        self._refresh_ptt_controls_enabled()
        self._refresh_ptt_button_appearance()
        self._begin_live_cat_radio_path()

    def _build_ui(self) -> None:
        cen = QWidget()
        root = QVBoxLayout(cen)

        self._chk_eq_master = QCheckBox(tr("live.check.eq_master"))
        self._chk_eq_master.toggled.connect(self._on_chk_eq_master)
        self._chk_eq_master.setStyleSheet(f"color:{_YAESU_GREEN};")

        self._group_eq = QGroupBox(tr("live.group.eq"))
        self._group_eq.setStyleSheet(
            "QGroupBox { background:#161616; color:#e0e0e0;"
            "border:1px solid #2c2c2c; border-radius:4px;"
            "padding-top:6px;}"
        )
        ego_lay = QVBoxLayout(self._group_eq)
        head = QHBoxLayout()
        head.addWidget(self._chk_eq_master)
        head.addStretch(1)
        ego_lay.addLayout(head)

        eq_row = QHBoxLayout()
        self._live_eq = LiveEqEditorWidget()
        self._live_eq.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._live_eq.changed.connect(self._on_live_eq_editor_changed)
        eq_row.addWidget(self._live_eq, stretch=1)

        strip = QWidget()
        strip.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        srl = QHBoxLayout(strip)
        srl.setContentsMargins(4, 0, 0, 10)
        srl.setSpacing(12)

        def _mk_v_col(caption: str, peak_bar: ScaledMeterBar) -> tuple[QSlider, QLabel, QLabel]:
            col = QWidget()
            col.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Expanding,
            )
            vl = QVBoxLayout(col)
            vl.setContentsMargins(0, 4, 0, 16)
            vl.setSpacing(4)
            t = QLabel(caption)
            t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            t.setWordWrap(False)
            t.setStyleSheet(
                "color:#bdbdbd;font-size:10px;font-weight:600;"
            )
            sl = TouchSlider(Qt.Orientation.Vertical)
            sl.setRange(0, 200)
            sl.setMinimumHeight(120)
            sl.setTracking(True)
            sl.setMinimumWidth(28)
            sl.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Expanding,
            )
            lb = QLabel(tr("live.strip.percent", pct=100))
            lb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lb.setMinimumWidth(42)
            lb.setStyleSheet(
                "color:#dcdcdc;font-size:11px;padding-top:10px;"
            )
            row = QWidget()
            row.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Expanding,
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            rl.addWidget(sl, 1)
            rl.addWidget(peak_bar, 1)
            peak_bar.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Expanding,
            )
            vl.addWidget(t, 0, Qt.AlignmentFlag.AlignHCenter)
            vl.addWidget(row, 1)
            vl.addWidget(lb, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            srl.addWidget(col, 0)
            return sl, lb, t

        _pm = make_live_level_bar(bar_min_height=120)
        _pmon = make_live_level_bar(bar_min_height=120)
        _pf = make_live_level_bar(bar_min_height=120)
        _pfl = make_live_level_bar(bar_min_height=120)
        self._live_level_bars = [_pm, _pmon, _pf, _pfl]

        self._sl_mic_v, self._lb_mic_pct, self._cap_strip_mic = _mk_v_col(
            tr("live.strip.mic_send"), _pm
        )
        self._sl_mon_v, self._lb_mon_pct, self._cap_strip_mon = _mk_v_col(
            tr("live.strip.monitor"), _pmon
        )
        self._sl_funk_v, self._lb_funk_pct, self._cap_strip_funk_in = _mk_v_col(
            tr("live.strip.funk_in"), _pf
        )
        self._sl_flisten_v, self._lb_flisten_pct, self._cap_strip_funk_out = _mk_v_col(
            tr("live.strip.funk_out"), _pfl
        )
        self._sl_mic_v.valueChanged.connect(self._pull_vol_sliders)
        self._sl_mon_v.valueChanged.connect(self._pull_vol_sliders)
        self._sl_funk_v.valueChanged.connect(self._pull_vol_sliders)
        self._sl_flisten_v.valueChanged.connect(self._pull_vol_sliders)
        for sl in (
            self._sl_mic_v,
            self._sl_mon_v,
            self._sl_funk_v,
            self._sl_flisten_v,
        ):
            sl.sliderReleased.connect(self._flush_vol_persist)

        srl.addStretch(0)
        eq_row.addWidget(strip, 0)

        ego_lay.addLayout(eq_row, stretch=1)

        root.addWidget(self._group_eq, stretch=1)

        def _mk_read_lbl() -> QLabel:
            v = QLabel(tr("common.dash"))
            v.setMinimumWidth(86)
            v.setAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
            )
            v.setStyleSheet("color:#c8c8c8;font-size:11px;font-weight:600;")
            return v

        # Gate
        self._group_noise_gate = QGroupBox(tr("live.group.noise_gate"))
        gv = QGridLayout(self._group_noise_gate)
        gv.setHorizontalSpacing(8)
        gv.setColumnStretch(1, 1)
        self._g_en = QCheckBox(tr("live.gate.enabled"))
        self._g_en.toggled.connect(self._pull_chk_into_snapshot)
        self._g_thr = TouchSlider(Qt.Orientation.Horizontal)
        self._g_thr.setRange(-800, -200)
        self._g_thr.valueChanged.connect(self._pull_slider_gate_comp)
        self._g_att = TouchSlider(Qt.Orientation.Horizontal)
        self._g_att.setRange(1, 50)
        self._g_att.valueChanged.connect(self._pull_slider_gate_comp)
        self._g_hld = TouchSlider(Qt.Orientation.Horizontal)
        self._g_hld.setRange(10, 300)
        self._g_hld.valueChanged.connect(self._pull_slider_gate_comp)
        self._g_rel = TouchSlider(Qt.Orientation.Horizontal)
        self._g_rel.setRange(20, 1000)
        self._g_rel.valueChanged.connect(self._pull_slider_gate_comp)

        gv.addWidget(self._g_en, 0, 0, 1, 3)
        r = 1
        self._gate_thr_lbl = _mk_read_lbl()
        self._gate_att_lbl = _mk_read_lbl()
        self._gate_hld_lbl = _mk_read_lbl()
        self._gate_rel_lbl = _mk_read_lbl()
        self._gate_thr_lbl_caption = QLabel(tr("live.gate.threshold"))
        self._gate_att_lbl_caption = QLabel(tr("live.gate.attack"))
        self._gate_hld_lbl_caption = QLabel(tr("live.gate.hold"))
        self._gate_rel_lbl_caption = QLabel(tr("live.gate.release"))
        for cap_lbl, slid, read_lbl in (
            (self._gate_thr_lbl_caption, self._g_thr, self._gate_thr_lbl),
            (self._gate_att_lbl_caption, self._g_att, self._gate_att_lbl),
            (self._gate_hld_lbl_caption, self._g_hld, self._gate_hld_lbl),
            (self._gate_rel_lbl_caption, self._g_rel, self._gate_rel_lbl),
        ):
            gv.addWidget(cap_lbl, r, 0)
            gv.addWidget(slid, r, 1)
            gv.addWidget(read_lbl, r, 2)
            r += 1

        # Compressor
        self._group_compressor = QGroupBox(tr("live.group.compressor"))
        cgrid = QGridLayout(self._group_compressor)
        cgrid.setHorizontalSpacing(8)
        cgrid.setColumnStretch(1, 1)
        self._c_en = QCheckBox(tr("live.comp.enabled"))
        self._c_en.toggled.connect(self._pull_chk_into_snapshot)
        self._c_thr = TouchSlider(Qt.Orientation.Horizontal)
        self._c_thr.setRange(-400, 0)
        self._c_thr.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_rat = TouchSlider(Qt.Orientation.Horizontal)
        self._c_rat.setRange(100, 1000)
        self._c_rat.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_att = TouchSlider(Qt.Orientation.Horizontal)
        self._c_att.setRange(1, 50)
        self._c_att.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_rel = TouchSlider(Qt.Orientation.Horizontal)
        self._c_rel.setRange(20, 500)
        self._c_rel.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_mk = TouchSlider(Qt.Orientation.Horizontal)
        self._c_mk.setRange(0, 120)
        self._c_mk.valueChanged.connect(self._pull_slider_gate_comp)

        cgrid.addWidget(self._c_en, 0, 0, 1, 3)
        cr = 1
        self._comp_thr_lbl = _mk_read_lbl()
        self._comp_rat_lbl = _mk_read_lbl()
        self._comp_att_lbl = _mk_read_lbl()
        self._comp_rel_lbl = _mk_read_lbl()
        self._comp_mk_lbl = _mk_read_lbl()
        self._comp_thr_lbl_caption = QLabel(tr("live.comp.threshold"))
        self._comp_rat_lbl_caption = QLabel(tr("live.comp.ratio"))
        self._comp_att_lbl_caption = QLabel(tr("live.comp.attack"))
        self._comp_rel_lbl_caption = QLabel(tr("live.comp.release"))
        self._comp_mk_lbl_caption = QLabel(tr("live.comp.makeup"))
        pairs = (
            (self._comp_thr_lbl_caption, self._c_thr, self._comp_thr_lbl),
            (self._comp_rat_lbl_caption, self._c_rat, self._comp_rat_lbl),
            (self._comp_att_lbl_caption, self._c_att, self._comp_att_lbl),
            (self._comp_rel_lbl_caption, self._c_rel, self._comp_rel_lbl),
            (self._comp_mk_lbl_caption, self._c_mk, self._comp_mk_lbl),
        )
        for cap_lbl, sl, rlbl in pairs:
            cgrid.addWidget(cap_lbl, cr, 0)
            cgrid.addWidget(sl, cr, 1)
            cgrid.addWidget(rlbl, cr, 2)
            cr += 1

        # RX Noise Gate (Funk-Mithör / Funk-Rückweg)
        self._group_rx_noise_gate = QGroupBox(tr("live.group.rx_noise_gate"))
        fgv = QGridLayout(self._group_rx_noise_gate)
        fgv.setHorizontalSpacing(8)
        fgv.setColumnStretch(1, 1)
        self._fg_en = QCheckBox(tr("live.funk_gate.enabled"))
        self._fg_en.toggled.connect(self._pull_rx_noise_gate)
        self._fg_thr = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_thr.setRange(-700, -10)
        self._fg_thr.valueChanged.connect(self._pull_rx_noise_gate)
        self._fg_att = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_att.setRange(1, 20)
        self._fg_att.valueChanged.connect(self._pull_rx_noise_gate)
        self._fg_hld = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_hld.setRange(5, 200)
        self._fg_hld.valueChanged.connect(self._pull_rx_noise_gate)
        self._fg_rel = TouchSlider(Qt.Orientation.Horizontal)
        self._fg_rel.setRange(20, 500)
        self._fg_rel.valueChanged.connect(self._pull_rx_noise_gate)

        fgv.addWidget(self._fg_en, 0, 0, 1, 3)
        fr = 1
        self._fg_thr_lbl = _mk_read_lbl()
        self._fg_att_lbl = _mk_read_lbl()
        self._fg_hld_lbl = _mk_read_lbl()
        self._fg_rel_lbl = _mk_read_lbl()
        self._fg_thr_lbl_caption = QLabel(tr("live.funk_gate.threshold"))
        self._fg_att_lbl_caption = QLabel(tr("live.funk_gate.attack"))
        self._fg_hld_lbl_caption = QLabel(tr("live.funk_gate.hold"))
        self._fg_rel_lbl_caption = QLabel(tr("live.funk_gate.release"))
        for cap_lbl, slid, read_lbl in (
            (self._fg_thr_lbl_caption, self._fg_thr, self._fg_thr_lbl),
            (self._fg_att_lbl_caption, self._fg_att, self._fg_att_lbl),
            (self._fg_hld_lbl_caption, self._fg_hld, self._fg_hld_lbl),
            (self._fg_rel_lbl_caption, self._fg_rel, self._fg_rel_lbl),
        ):
            fgv.addWidget(cap_lbl, fr, 0)
            fgv.addWidget(slid, fr, 1)
            fgv.addWidget(read_lbl, fr, 2)
            fr += 1

        gc_row = QWidget()
        gc_lay = QHBoxLayout(gc_row)
        gc_lay.setContentsMargins(0, 0, 0, 0)
        gc_lay.setSpacing(10)
        gc_lay.addWidget(self._group_noise_gate, 1)
        gc_lay.addWidget(self._group_compressor, 1)
        gc_lay.addWidget(self._group_rx_noise_gate, 1)
        root.addWidget(gc_row)

        row_btn = QHBoxLayout()
        self._tx_led = TxIndicator()
        self._tx_label = QLabel(tr("common.dash"))
        tx_lbl_font = self._tx_label.font()
        tx_lbl_font.setBold(True)
        self._tx_label.setFont(tx_lbl_font)
        self._tx_label.setMinimumWidth(34)
        self._tx_label.setToolTip(tr("live.tx_label.tooltip"))
        row_btn.addWidget(
            self._tx_led, 0, Qt.AlignmentFlag.AlignVCenter
        )
        row_btn.addWidget(
            self._tx_label, 0, Qt.AlignmentFlag.AlignVCenter
        )
        row_btn.addSpacing(8)
        self._b_ptt = MomentaryHoldButton(tr("live.btn.ptt"))
        self._b_ptt.setToolTip(tr("live.btn.ptt.tooltip"))
        self._b_ptt.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self._b_ptt.pressed.connect(self._on_live_ptt_momentary_pressed)
        self._b_ptt.released.connect(self._on_live_ptt_momentary_released)

        self._b_ptt_latch = QPushButton(tr("live.btn.ptt_latch"))
        self._b_ptt_latch.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self._b_ptt_latch.setCheckable(True)
        self._b_ptt_latch.setToolTip(tr("live.btn.ptt_latch.tooltip"))
        self._b_ptt_latch.toggled.connect(self._on_live_ptt_latch_toggled)
        row_btn.addWidget(self._b_ptt)
        row_btn.addWidget(self._b_ptt_latch)
        row_btn.addSpacing(8)
        self._live_profile_combo = QComboBox()
        self._live_profile_combo.setMinimumWidth(150)
        self._live_profile_combo.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self._live_profile_combo.currentIndexChanged.connect(
            self._on_live_audio_profile_selected
        )
        self._b_live_profile_save = QPushButton(tr("live.profile.btn.save"))
        self._b_live_profile_save.setToolTip(tr("live.profile.btn.save.tooltip"))
        self._b_live_profile_save.clicked.connect(self._on_live_audio_profile_save)
        self._b_live_profile_update = QPushButton(tr("live.profile.btn.update"))
        self._b_live_profile_update.setToolTip(tr("live.profile.btn.update.tooltip"))
        self._b_live_profile_update.clicked.connect(self._on_live_audio_profile_update)
        self._b_live_profile_delete = QPushButton(tr("live.profile.btn.delete"))
        self._b_live_profile_delete.setToolTip(tr("live.profile.btn.delete.tooltip"))
        self._b_live_profile_delete.clicked.connect(self._on_live_audio_profile_delete)
        for btn in (
            self._b_live_profile_save,
            self._b_live_profile_update,
            self._b_live_profile_delete,
        ):
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        row_btn.addWidget(self._live_profile_combo, 1)
        row_btn.addWidget(self._b_live_profile_save)
        row_btn.addWidget(self._b_live_profile_update)
        row_btn.addWidget(self._b_live_profile_delete)
        row_btn.addStretch(1)
        self._b_afl = QPushButton(tr("live.btn.afl"))
        self._b_afl.setToolTip(tr("live.btn.afl.tooltip"))
        self._b_afl.setCheckable(True)
        self._b_afl.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        self._b_afl.toggled.connect(self._on_afl_toggled)
        self._btn_audio_routing = QPushButton(tr("live.btn.audio_routing"))
        self._btn_audio_routing.setToolTip(tr("live.open_sound_settings.tooltip"))
        self._btn_audio_routing.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        if self._open_sound_settings is not None:
            self._btn_audio_routing.clicked.connect(self._open_sound_settings)
        else:
            self._btn_audio_routing.setEnabled(False)
        row_btn.addWidget(self._b_afl)
        row_btn.addWidget(self._btn_audio_routing)
        self._refresh_afl_button_appearance()
        self._lbl_live_footer = QLabel()
        self._lbl_live_footer.setWordWrap(False)
        root.addLayout(row_btn)
        root.addWidget(self._lbl_live_footer)
        self._refresh_live_footer()
        self.setCentralWidget(cen)

    def reload_from_app_settings(self) -> None:
        """Nach Änderungen in den Soundeinstellungen Live-Geräte neu laden."""
        self._sync_live_devices_from_settings(refresh_monitors=True)
        self._apply_live_to_ui()

    def handle_cat_disconnected(self) -> None:
        """CAT/USB weg — Live-Audio stoppen, CAT-Pending abbrechen, UI offline."""
        self._live_cat_settle.stop()
        engine_running = self._engine.is_running()
        idle_running = self._engine.is_idle_listen_monitor_running()
        transport_wanted = self._desired_live_transport_on()
        window_active = _live_window_accepts_background_audio(self)
        self._resume_live_transport_after_reconnect = bool(transport_wanted)
        self._resume_live_engine_after_reconnect = bool(engine_running)
        self._resume_idle_monitor_after_reconnect = bool(
            window_active and (idle_running or engine_running or transport_wanted)
        )
        self._clear_live_transport_pending_flags()
        self._reconnect_engine_resume_pending = False
        self._mic_ptt_interrupted_live = False
        if engine_running or self._live_cat_tx_armed or self._cat_live_start_busy:
            self._stop_live_via_ptt(
                clear_ptt_wants=False,
                invoke_release_voice_after_live=False,
                refresh_idle_monitor_after=False,
            )
        else:
            self._safe_live_cat_tx_off()
        self._engine.stop_idle_listen_monitor()
        self._engine.stop_mic_preview_monitor()
        self._idle_monitor_fp_key = None
        self._mic_preview_fp_key = None
        self._update_tx_rx_led(self._last_synced_tx_state or TX_STATE_RX)

    def handle_cat_reconnected(self) -> None:
        """CAT wieder da — nach kurzer Wartezeit Audio/Geräte wieder anbinden."""
        if not self.isVisible():
            return
        if self._cat is None or not self._cat.is_connected():
            return
        # USB-Audio-Geräte brauchen nach Einstecken oft ein paar 100 ms.
        QTimer.singleShot(700, self._apply_cat_reconnected_resume)

    def _apply_cat_reconnected_resume(self, attempt: int = 0) -> None:
        if not self.isVisible() or self._cat is None or not self._cat.is_connected():
            return
        invalidate_windows_audio_device_cache()
        self._sync_live_devices_from_settings(refresh_monitors=False)
        self._update_device_summary_labels()
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_shown(self)
        QTimer.singleShot(0, self._ensure_live_data_mode_for_session)
        last = self._last_synced_tx_state
        self._update_tx_rx_led(last if last is not None else TX_STATE_RX)

        if self._resume_live_transport_after_reconnect and self._desired_live_transport_on():
            self._resume_live_transport_after_reconnect = False
            self._resume_live_engine_after_reconnect = False
            self._resume_idle_monitor_after_reconnect = False
            self._sync_ptt_live_transport()
            return

        if self._resume_live_engine_after_reconnect:
            outcome = self._restart_live_engine_after_reconnect(attempt)
            if outcome == "started":
                self._resume_live_engine_after_reconnect = False
                self._resume_idle_monitor_after_reconnect = False
                return
            if outcome in ("pending", "retry"):
                return

        if self._resume_idle_monitor_after_reconnect or attempt == 0:
            prereq_ok, _ = self._engine.prerequisites_ok()
            if prereq_ok:
                self._resume_idle_monitor_after_reconnect = False
                self._defer_refresh_idle_listen_monitor()
                return
            if attempt < 10:
                QTimer.singleShot(
                    400,
                    lambda: self._apply_cat_reconnected_resume(attempt + 1),
                )
                return
            self._resume_idle_monitor_after_reconnect = False

    def _restart_live_engine_after_reconnect(self, attempt: int) -> str:
        """Live-Stream ohne erneutes PTT-Drücken fortsetzen.

        Rückgabe: ``started`` | ``pending`` (CAT-Worker) | ``retry`` | ``failed``
        """
        if not self._cat.is_connected():
            return "failed"
        liv = LiveSettings.from_dict(self._live_snapshot.to_dict())
        prereq_ok, _ = self._engine.prerequisites_ok()
        if not prereq_ok:
            if attempt < 10:
                QTimer.singleShot(
                    400,
                    lambda: self._apply_cat_reconnected_resume(attempt + 1),
                )
                return "retry"
            return "failed"

        if self._radio_setup is not None and self._setup_worker is not None:
            self._reconnect_engine_resume_pending = True
            if not self._radio_setup.is_applied:
                self._cat_live_start_busy = True
                self._pending_live_after_pc_then_engage = True
                _invoke_setup_worker_slot(self._setup_worker, "run_apply_pc_menus")
                return "pending"
            if not self._radio_setup.in_data_mode:
                self._cat_live_start_busy = True
                self._invoke_worker_engage_data_for_live()
                return "pending"
            if self._desired_live_transport_on():
                self._request_live_cat_tx_on()
            self._cat_live_start_busy = True
            self._resolve_live_target_data_mode()
            ok, txt = self._engine.start(LiveSettings.from_dict(liv.to_dict()))
            if not ok:
                self._safe_live_cat_tx_off()
                self._cat_live_start_busy = False
                self._reconnect_engine_resume_pending = False
                if attempt < 10:
                    QTimer.singleShot(
                        400,
                        lambda: self._apply_cat_reconnected_resume(attempt + 1),
                    )
                    return "retry"
                return "failed"
            self._push_live_engine_runtime_settings(
                LiveSettings.from_dict(liv.to_dict())
            )
            self._cat_live_start_busy = False
            self._reconnect_engine_resume_pending = False
            fn = self._request_cat_tx_poll_fn
            if callable(fn):
                fn()
            return "started"

        ok, txt = self._engine.start(LiveSettings.from_dict(liv.to_dict()))
        if not ok:
            if attempt < 10:
                QTimer.singleShot(
                    400,
                    lambda: self._apply_cat_reconnected_resume(attempt + 1),
                )
                return "retry"
            return "failed"
        self._push_live_engine_runtime_settings(
            LiveSettings.from_dict(liv.to_dict())
        )
        return "started"

    def apply_devices_from_settings(self, *, notify_if_live_stopped: bool = True) -> None:
        """Soundeinstellungen haben Live-Geräte geändert — sofort übernehmen."""
        was_running = self._engine.is_running()
        self._sync_live_devices_from_settings(refresh_monitors=not was_running)
        self._update_device_summary_labels()
        if was_running:
            self._stop_live_via_ptt(clear_ptt_wants=True)
            if notify_if_live_stopped:
                QMessageBox.information(
                    self,
                    tr("live.msgbox.device_changed.title"),
                    tr("live.msgbox.device_changed.text"),
                )
        elif _live_window_accepts_background_audio(self):
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def _sync_live_devices_from_settings(
        self,
        *,
        refresh_monitors: bool,
    ) -> None:
        invalidate_windows_audio_device_cache()
        liv = LiveSettings.from_dict(self._settings.live.to_dict())
        if remap_live_settings_devices(liv):
            self._settings.live = LiveSettings.from_dict(liv.to_dict())
            self._settings.save()
        self._live_snapshot = LiveSettings.from_dict(liv.to_dict())
        self._idle_monitor_fp_key = None
        self._mic_preview_fp_key = None
        self._push_live_engine_runtime_settings(liv)
        self._update_device_summary_labels()
        self._schedule_samplerate_label_update(liv, refresh_monitors_after=refresh_monitors)

    def _update_device_summary_labels(self, error: Optional[str] = None) -> None:
        if error is not None:
            self._live_footer_error = str(error).strip() or None
        else:
            self._live_footer_error = None
        self._refresh_live_footer()

    def _resolve_live_target_data_mode(self, mode: Optional[RxMode] = None) -> None:
        """DATA‑Ziel aus Funkmodus ableiten und Session‑Flag mit Gerät abgleichen."""
        if self._radio_setup is None:
            return
        if mode is None:
            if self._cat is not None and self._cat.is_connected():
                try:
                    mode = FT991CAT(self._cat).read_rx_mode()
                except Exception:
                    mode = None
            if mode is None and self._operating_mode_provider is not None:
                try:
                    mode = self._operating_mode_provider()
                except Exception:
                    mode = None
        if mode is None:
            return
        data_mode = data_mode_for_rx_mode(mode)
        self._settings.audio_player.data_mode = data_mode.value  # type: ignore[assignment]
        self._radio_setup.align_data_mode_to_rx_mode(mode)
        self._radio_setup.reconcile_in_data_mode_with_radio()

    def _radio_matches_target_data_mode(self) -> bool:
        if self._radio_setup is None:
            return False
        if not self._radio_setup.in_data_mode:
            return False
        if self._cat is None or not self._cat.is_connected():
            return True
        try:
            return FT991CAT(self._cat).read_rx_mode() == self._radio_setup.data_mode
        except Exception:
            return self._radio_setup.in_data_mode

    def _ensure_live_data_mode_for_session(self) -> None:
        """DATA-Modus für die Live-Session — bleibt bis Fensterende aktiv."""
        if not _live_window_accepts_background_audio(self):
            return
        if self._cat is None or not self._cat.is_connected():
            return
        if self._radio_setup is None or self._setup_worker is None:
            return
        self._resolve_live_target_data_mode()
        if not self._radio_setup.is_applied:
            self._pending_open_window_data_engage = True
            _invoke_setup_worker_slot(self._setup_worker, "run_apply_pc_menus")
            return
        self._pending_open_window_data_engage = False
        if self._radio_matches_target_data_mode():
            self._live_data_session_active = True
            return
        self._live_data_session_active = True
        self._invoke_worker_engage_data_for_live(session_only=True)

    def _on_live_session_pc_menus_finished(self, ok: bool, message: str) -> None:
        if not self._pending_open_window_data_engage:
            return
        if not ok:
            self._pending_open_window_data_engage = False
            return
        QTimer.singleShot(0, self._ensure_live_data_mode_for_session)

    def sync_data_mode_from_main(self, mode: Optional[RxMode] = None) -> None:
        """DATA‑Ziel aus Hauptfenster — während Live offen ist DATA-Variante mitführen."""
        if self._radio_setup is None:
            return
        self._resolve_live_target_data_mode(mode)
        if not self._live_data_session_active:
            return
        if not self._radio_setup.is_applied:
            return
        if self._cat is None or not self._cat.is_connected():
            return
        if self._setup_worker is None:
            return
        data_mode = self._radio_setup.data_mode
        try:
            current = FT991CAT(self._cat).read_rx_mode()
            if current == data_mode:
                return
        except Exception:
            pass
        _invoke_setup_worker_slot(
            self._setup_worker,
            "run_set_data_mode",
            Q_ARG(str, data_mode.value),
        )

    def _sync_live_eq_profile_for_session(self, *, entering: bool) -> None:
        pw = self._profile_widget
        if pw is None:
            return
        if entering:
            fn = getattr(pw, "enter_live_eq_session", None)
        else:
            fn = getattr(pw, "exit_live_eq_session", None)
        if callable(fn):
            fn()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_live_eq_profile_for_session(entering=True)
        self._ensure_live_ctrl_y_filter_acquired()
        if self._last_synced_tx_state is not None:
            self._update_tx_rx_led(self._last_synced_tx_state)
        else:
            self._update_tx_rx_led(TX_STATE_RX)
        self._suppress_idle_listen_monitor = False
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_shown(self)
        QTimer.singleShot(0, self._ensure_live_data_mode_for_session)
        QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._ensure_live_ctrl_y_filter_released()
        self._release_keyboard_ptt_momentary()
        self._engine.stop_idle_listen_monitor()
        self._engine.stop_mic_preview_monitor()
        self._idle_monitor_fp_key = None
        self._mic_preview_fp_key = None
        super().hideEvent(event)
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)

    def _update_tx_rx_led(self, state: int) -> None:
        """RX/TX‑Kreis wie im Hauptfenster (grün=RX, rot=TX, grau=kein CAT)."""
        led = getattr(self, "_tx_led", None)
        lbl = getattr(self, "_tx_label", None)
        if led is None or lbl is None:
            return
        cat_ok = self._cat is not None and self._cat.is_connected()
        if not cat_ok:
            led.set_state(TxIndicator.STATE_OFF)
            lbl.setText(tr("common.dash"))
            self._last_synced_tx_state = int(state)
            return
        transmitting = bool(state != TX_STATE_RX)
        led.set_active(transmitting)
        lbl.setText(tr("common.tx") if transmitting else tr("common.rx"))
        self._last_synced_tx_state = int(state)

    def handle_tx_state_changed(self, state: int) -> None:
        """MIC‑PTT sowie Funk‑Mithören‑Stummschalter während Live‑TX."""
        display_state = effective_live_tx_display_state(
            state,
            want_live_transport=self._desired_live_transport_on(),
            cat_tx_armed=bool(self._live_cat_tx_armed),
            cat_live_start_busy=bool(self._cat_live_start_busy),
            engine_running=self._engine.is_running(),
        )
        self._update_tx_rx_led(display_state)
        transmitting = bool(display_state != TX_STATE_RX)
        self._sync_live_funk_listen_mute_while_cat_tx(transmitting)

        if (
            self._radio_setup is None
            or not self._radio_setup.is_applied
            or self._setup_worker is None
        ):
            return
        if state == TX_STATE_MIC_PTT:
            if self._cat_live_start_busy or self._live_cat_waiting_engage_finish:
                return
            if not self._engine.is_running() and not self._radio_setup.in_data_mode:
                return
            self._stop_live_via_ptt(clear_ptt_wants=True)
            self._mic_ptt_interrupted_live = True
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
            return
        if state == TX_STATE_RX and self._mic_ptt_interrupted_live:
            self._mic_ptt_interrupted_live = False
            QTimer.singleShot(0, self._ensure_live_data_mode_for_session)

    def _on_live_ptt_failed(self, message: str) -> None:
        if (
            message.strip()
            and self._cat is not None
            and self._cat.is_connected()
        ):
            QMessageBox.warning(self, tr("live.msgbox.ptt.title"), message)

    def _on_live_radio_pc_menus_for_start(self, ok: bool, message: str) -> None:
        if self._cat is None or not self._cat.is_connected():
            self._clear_live_transport_pending_flags()
            return
        if not getattr(self, "_pending_live_after_pc_then_engage", False):
            return
        self._pending_live_after_pc_then_engage = False
        if not self._cat_live_start_busy or self._radio_setup is None:
            return
        if not ok:
            self._abort_live_cat_start(message or tr("live.error.pc_menus_failed"))
            return
        self._invoke_worker_engage_data_for_live()

    def _on_live_radio_engage_finished(self, ok: bool, message: str) -> None:
        if self._cat is None or not self._cat.is_connected():
            self._clear_live_transport_pending_flags()
            self._reconnect_engine_resume_pending = False
            self._engage_data_session_only = False
            return
        session_only = bool(self._engage_data_session_only)
        self._engage_data_session_only = False
        was_waiting = bool(self._live_cat_waiting_engage_finish)
        reconnect_resume = bool(self._reconnect_engine_resume_pending)
        self._live_cat_waiting_engage_finish = False
        if session_only:
            if ok:
                self._live_data_session_active = True
            else:
                self._live_data_session_active = False
                if message.strip() and self._cat.is_connected():
                    QMessageBox.warning(self, tr("live.msgbox.title"), message)
            return
        if (
            not was_waiting
            and not self._desired_live_transport_on()
            and not reconnect_resume
        ):
            return
        if self._radio_setup is None:
            return
        if not ok:
            if was_waiting or self._cat_live_start_busy:
                self._reconnect_engine_resume_pending = False
                self._abort_live_cat_start(
                    message or tr("live.error.data_mode_failed"),
                )
            return
        if not self._desired_live_transport_on() and not reconnect_resume:
            self._clear_live_transport_pending_flags()
            return
        self._cat_live_start_busy = True
        if self._desired_live_transport_on():
            self._request_live_cat_tx_on()
        self._schedule_live_engine_after_radio_settled(fresh_data_engaged=True)

    def _invoke_worker_engage_data_for_live(self, *, session_only: bool = False) -> None:
        """Nur wenn PC‑Menüs und Snapshot bereits da sind."""
        if self._setup_worker is None or self._radio_setup is None:
            if session_only:
                self._live_data_session_active = False
                return
            self._abort_live_cat_start(tr("live.error.internal_no_worker"))
            return
        self._engage_data_session_only = bool(session_only)
        self._live_cat_waiting_engage_finish = True
        _invoke_setup_worker_slot(self._setup_worker, "run_engage_data")

    def _abort_live_cat_start(self, detail: str) -> None:
        self._clear_live_transport_pending_flags()
        self._safe_live_cat_tx_off()
        self._clear_live_ptt_wants()
        if (
            detail.strip()
            and self._cat is not None
            and self._cat.is_connected()
        ):
            QMessageBox.warning(self, tr("live.msgbox.title"), detail)
        QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def _schedule_live_engine_after_radio_settled(self, *, fresh_data_engaged: bool = True) -> None:
        """Nach DATA-Umschaltung kurz warten, dann Audio-Stream — TX1 ist schon aktiv."""
        ms = max(0, _CAT_LIVE_RADIO_SETTLE_MS) if fresh_data_engaged else 0
        if ms <= 0:
            QTimer.singleShot(0, self._on_live_radio_settled_start_engine)
            return
        self._live_cat_settle.start(ms)

    def _on_live_radio_settled_start_engine(self) -> None:
        reconnect_resume = bool(self._reconnect_engine_resume_pending)
        if not self._desired_live_transport_on() and not reconnect_resume:
            self._clear_live_transport_pending_flags()
            return
        if self._engine.is_running():
            self._cat_live_start_busy = False
            self._reconnect_engine_resume_pending = False
            return
        if not self._cat_live_start_busy:
            self._cat_live_start_busy = True
        self._resolve_live_target_data_mode()
        if self._radio_setup is not None and not self._radio_setup.in_data_mode:
            self._invoke_worker_engage_data_for_live()
            return
        liv = LiveSettings.from_dict(self._gather_live_from_ui().to_dict())
        self._live_snapshot = liv
        self._settings.live = liv
        self._vol_persist_timer.start()
        if self._desired_live_transport_on():
            self._request_live_cat_tx_on()
        ok_s, txt = self._engine.start(LiveSettings.from_dict(liv.to_dict()))
        if not ok_s:
            self._safe_live_cat_tx_off()
            self._reconnect_engine_resume_pending = False
            self._abort_live_cat_start(txt or tr("live.error.stream_start_failed"))
            return
        self._push_live_engine_runtime_settings(LiveSettings.from_dict(liv.to_dict()))
        self._cat_live_start_busy = False
        if self._reconnect_engine_resume_pending:
            self._resume_live_engine_after_reconnect = False
            self._resume_idle_monitor_after_reconnect = False
        self._reconnect_engine_resume_pending = False
        self._refresh_ptt_controls_enabled()
        self._refresh_ptt_button_appearance()
        fn = self._request_cat_tx_poll_fn
        if callable(fn):
            fn()

    def _begin_live_cat_radio_path(self) -> None:
        assert self._radio_setup is not None
        rs = self._radio_setup

        blocked = ""
        fn = getattr(self, "_other_audio_blocking_fn", None)
        if callable(fn):
            blocked = str(fn()).strip()
        if blocked:
            self._clear_live_transport_pending_flags()
            self._clear_live_ptt_wants()
            QMessageBox.information(self, tr("live.msgbox.title"), blocked)
            return

        if self._cat is None or not self._cat.is_connected():
            self._abort_live_cat_start(
                tr("live.error.cat_not_connected"),
            )
            return
        # Aktuellen Funkmodus lesen (Speicherkanal kann DATA→FM o. ä. gewechselt haben).
        self._resolve_live_target_data_mode()

        if not rs.is_applied:
            self._pending_live_after_pc_then_engage = True
            if self._setup_worker is None:
                self._abort_live_cat_start(tr("live.error.internal_worker_missing"))
                return
            _invoke_setup_worker_slot(self._setup_worker, "run_apply_pc_menus")
            return

        if not self._radio_matches_target_data_mode():
            self._invoke_worker_engage_data_for_live()
            return

        self._live_data_session_active = True
        self._request_live_cat_tx_on()
        self._schedule_live_engine_after_radio_settled(fresh_data_engaged=False)

    def _shutdown_ptt_thread(self) -> None:
        """CAT-PTT-Worker beenden — nur Fenster-Schließen / App-Ende.

        Bei „Stop Live“ darf dieser Thread weiterlaufen, sonst feuern spätere
        ``TX1``-Aufrufe nicht mehr (:func:`audio.player_controller._invoke_ptt_worker_set_transmit`).
        """
        tt = getattr(self, "_ptt_thread", None)
        if tt is None or not tt.isRunning():
            return
        tw = getattr(self, "_ptt_worker", None)
        if tw is not None:
            tw.blockSignals(True)
        tt.quit()
        if not tt.wait(3000):
            tt.terminate()
            tt.wait(1000)

    def _safe_live_cat_tx_off(self) -> None:
        self._live_cat_tx_armed = False
        self._suppress_funk_listen_while_live_tx_active = False
        if self._ptt_worker is not None:
            _invoke_ptt_worker_set_transmit(self._ptt_worker, False)
        elif self._cat is not None and self._cat.is_connected():
            try:
                FT991CAT(self._cat).set_cat_transmit(False)
            except Exception:
                pass

    def _ensure_live_cat_tx_off_blocking(self, timeout_s: float = 2.5) -> None:
        """CAT-TX vor Funk-Restore synchron beenden (Schließen des Live-Fensters)."""
        self._live_cat_tx_armed = False
        self._suppress_funk_listen_while_live_tx_active = False
        if self._cat is None or not self._cat.is_connected():
            return
        tw = getattr(self, "_ptt_worker", None)
        if tw is not None:
            tw.blockSignals(True)
        try:
            FT991CAT(self._cat).set_cat_transmit(
                False, wait=True, timeout_s=timeout_s
            )
        except Exception:
            pass

    def _release_live_voice_mode_plain(self) -> None:
        if (
            self._radio_setup is not None
            and self._radio_setup.is_applied
            and self._radio_setup.in_data_mode
            and self._setup_worker is not None
        ):
            _invoke_setup_worker_slot(self._setup_worker, "run_engage_plain")

    def _mithoren_keeps_radio_data_until_window_close(self) -> bool:
        """Stop Live gibt Sprachmodus nicht zurück — Funk bleibt in DATA bis Fensterende."""
        return True

    def _release_voice_plain_after_stop_live_if_not_mithoren(self) -> None:
        """Bei aktiv „Mithören“ Datenmodus beim Stop bestehen lassen (siehe closeEvent)."""
        if self._mithoren_keeps_radio_data_until_window_close():
            return
        self._release_live_voice_mode_plain()

    def _on_live_eq_editor_changed(self) -> None:
        self._push_snapshot()

    def _on_chk_eq_master(self, _checked: Optional[bool] = None) -> None:
        del _checked
        self._sync_live_eq_master_look()
        self._push_snapshot()

    def _sync_live_eq_master_look(self) -> None:
        on = bool(self._chk_eq_master.isChecked())
        self._live_eq.set_path_status(
            active=on,
            hint_text=("" if on else tr("live.eq.bypass_hint")),
        )
        self._live_eq.set_read_only(not on)

    def _meter_tick(self) -> None:
        for peak_bar, dbv in zip(
            self._live_level_bars,
            self._engine.peek_live_strip_meters_db(),
        ):
            peak_bar.set_value(live_dbfs_peak_to_raw(dbv))

    def _apply_live_to_ui(self) -> None:
        liv = LiveSettings.from_dict(self._live_snapshot.to_dict())

        sl_in = live_slider_from_gain(liv.input_gain)
        sl_out = live_slider_from_gain(liv.output_gain)
        sl_funk = live_slider_from_gain(liv.funk_output_gain)
        sl_fl = live_slider_from_gain(liv.funk_listen_gain)
        self._sl_mic_v.blockSignals(True)
        self._sl_mon_v.blockSignals(True)
        self._sl_funk_v.blockSignals(True)
        self._sl_flisten_v.blockSignals(True)
        self._sl_mic_v.setValue(sl_in)
        self._sl_mon_v.setValue(sl_out)
        self._sl_funk_v.setValue(sl_funk)
        self._sl_flisten_v.setValue(sl_fl)
        self._sl_mic_v.blockSignals(False)
        self._sl_mon_v.blockSignals(False)
        self._sl_funk_v.blockSignals(False)
        self._sl_flisten_v.blockSignals(False)
        self._update_vol_slider_labels()

        self._b_afl.blockSignals(True)
        self._b_afl.setChecked(not bool(liv.suppress_live_monitor_mic))
        self._b_afl.blockSignals(False)
        self._refresh_afl_button_appearance()

        self._chk_eq_master.blockSignals(True)
        self._chk_eq_master.setChecked(bool(liv.eq_enabled))
        self._chk_eq_master.blockSignals(False)
        self._live_eq.set_bands(list(liv.eq_bands))
        self._sync_live_eq_master_look()

        g = liv.gate
        self._g_en.setChecked(bool(g.enabled))
        th = max(-800, min(-200, int(round(g.threshold_db * 10.0))))
        self._g_thr.setValue(th)
        self._g_att.setValue(int(round(g.attack_ms)))
        self._g_hld.setValue(int(round(g.hold_ms)))
        self._g_rel.setValue(int(round(g.release_ms)))

        c = liv.compressor
        self._c_en.setChecked(bool(c.enabled))
        self._c_thr.setValue(int(round(c.threshold_db * 10)))
        self._c_rat.setValue(int(round(c.ratio * 100)))
        self._c_att.setValue(int(round(c.attack_ms)))
        self._c_rel.setValue(int(round(c.release_ms)))
        self._c_mk.setValue(int(round(c.makeup_db * 10)))

        fg = liv.funk_listen_gate
        self._fg_en.blockSignals(True)
        self._fg_thr.blockSignals(True)
        self._fg_att.blockSignals(True)
        self._fg_hld.blockSignals(True)
        self._fg_rel.blockSignals(True)
        self._fg_en.setChecked(bool(fg.enabled))
        self._fg_thr.setValue(
            max(-700, min(-10, int(round(fg.threshold_db * 10.0))))
        )
        self._fg_att.setValue(int(round(fg.attack_ms)))
        self._fg_hld.setValue(int(round(fg.hold_ms)))
        self._fg_rel.setValue(int(round(fg.release_ms)))
        self._fg_en.blockSignals(False)
        self._fg_thr.blockSignals(False)
        self._fg_att.blockSignals(False)
        self._fg_hld.blockSignals(False)
        self._fg_rel.blockSignals(False)

        self._refresh_gate_comp_readouts()
        self._push_snapshot(persist_disk=False, refresh_monitors=False)
        self._schedule_samplerate_label_update(liv, refresh_monitors_after=True)

    def _gather_live_from_ui(self) -> LiveSettings:
        liv = LiveSettings.from_dict(self._live_snapshot.to_dict())
        liv.input_device_id = str(self._settings.live.input_device_id or "")
        liv.output_device_id = str(self._settings.live.output_device_id or "")
        liv.funk_output_device_id = str(self._settings.live.funk_output_device_id or "")
        liv.funk_listen_input_device_id = str(
            self._settings.live.funk_listen_input_device_id or ""
        )
        liv.funk_listen_enabled = True
        liv.suppress_live_monitor_mic = not bool(self._b_afl.isChecked())
        liv.samplerate = int(self._live_snapshot.samplerate)
        liv.blocksize = int(self._live_snapshot.blocksize)
        liv.input_gain = live_gain_from_slider(int(self._sl_mic_v.value()))
        liv.output_gain = live_gain_from_slider(int(self._sl_mon_v.value()))
        liv.funk_output_gain = live_gain_from_slider(int(self._sl_funk_v.value()))
        liv.funk_listen_gain = live_gain_from_slider(int(self._sl_flisten_v.value()))

        liv.eq_enabled = bool(self._chk_eq_master.isChecked())
        liv.eq_bands = [
            LiveEqBandSettings.from_dict(x.to_dict())
            for x in self._live_eq.get_bands()
        ]

        liv.gate.enabled = bool(self._g_en.isChecked())
        liv.gate.threshold_db = float(self._g_thr.value()) / 10.0
        liv.gate.attack_ms = float(self._g_att.value())
        liv.gate.hold_ms = float(self._g_hld.value())
        liv.gate.release_ms = float(self._g_rel.value())

        liv.compressor.enabled = bool(self._c_en.isChecked())
        liv.compressor.threshold_db = float(self._c_thr.value()) / 10.0
        liv.compressor.ratio = float(self._c_rat.value()) / 100.0
        liv.compressor.attack_ms = float(self._c_att.value())
        liv.compressor.release_ms = float(self._c_rel.value())
        liv.compressor.makeup_db = float(self._c_mk.value()) / 10.0

        liv.funk_listen_gate.enabled = bool(self._fg_en.isChecked())
        liv.funk_listen_gate.threshold_db = float(self._fg_thr.value()) / 10.0
        liv.funk_listen_gate.attack_ms = float(self._fg_att.value())
        liv.funk_listen_gate.hold_ms = float(self._fg_hld.value())
        liv.funk_listen_gate.release_ms = float(self._fg_rel.value())

        liv.clamp_recursive()
        return liv

    def _build_live_audio_profile_from_ui(self, name: str) -> LiveAudioProfile:
        return LiveAudioProfile.from_live_settings(self._gather_live_from_ui(), name)

    def _current_live_audio_profile_name(self) -> str:
        idx = self._live_profile_combo.currentIndex()
        if idx < 0:
            return ""
        data = self._live_profile_combo.itemData(idx)
        if data is None:
            return str(self._live_profile_combo.currentText() or "").strip()
        return str(data).strip()

    def _reload_live_audio_profile_combo(
        self,
        *,
        select_name: Optional[str] = None,
        select_last: bool = False,
    ) -> None:
        store = self._live_audio_profile_store
        want = str(select_name or "").strip()
        if not want and select_last:
            want = str(store.last_profile or "").strip()
        if not want and store.names():
            want = store.names()[0]

        self._live_profile_combo.blockSignals(True)
        self._live_profile_combo.clear()
        for name in store.names():
            self._live_profile_combo.addItem(name, name)
        pick = -1
        if want:
            pick = self._live_profile_combo.findData(want)
        if pick < 0 and self._live_profile_combo.count() > 0:
            pick = 0
        if pick >= 0:
            self._live_profile_combo.setCurrentIndex(pick)
        self._live_profile_combo.blockSignals(False)
        self._refresh_live_audio_profile_actions()

    def _refresh_live_audio_profile_actions(self) -> None:
        has_selection = self._live_profile_combo.currentIndex() >= 0
        self._b_live_profile_update.setEnabled(has_selection)
        self._b_live_profile_delete.setEnabled(has_selection)

    def _apply_live_audio_profile(self, profile: LiveAudioProfile) -> None:
        liv = self._gather_live_from_ui()
        profile.apply_to(liv)
        self._live_snapshot = liv
        merged = LiveSettings.from_dict(self._settings.live.to_dict())
        merged.eq_enabled = liv.eq_enabled
        merged.eq_bands = [
            LiveEqBandSettings.from_dict(b.to_dict()) for b in liv.eq_bands
        ]
        merged.gate = LiveGateSettings.from_dict(liv.gate.to_dict())
        merged.compressor = LiveCompressorSettings.from_dict(liv.compressor.to_dict())
        merged.funk_listen_gate = LiveFunkListenGateSettings.from_dict(
            liv.funk_listen_gate.to_dict()
        )
        merged.input_gain = liv.input_gain
        merged.output_gain = liv.output_gain
        merged.funk_output_gain = liv.funk_output_gain
        merged.funk_listen_gain = liv.funk_listen_gain
        merged.clamp_recursive()
        self._settings.live = merged
        self._apply_live_to_ui()
        self._engine.reset_funk_listen_noise_gate()
        self._push_snapshot(refresh_monitors=True)

    def _on_live_audio_profile_selected(self, index: int) -> None:
        if index < 0 or self._live_profile_loading:
            return
        name = self._current_live_audio_profile_name()
        if not name:
            return
        profile = self._live_audio_profile_store.find(name)
        if profile is None:
            return
        self._live_audio_profile_store.set_last_profile(name)
        self._apply_live_audio_profile(profile)

    def _on_live_audio_profile_save(self) -> None:
        default_name = (
            self._current_live_audio_profile_name()
            or tr("live.profile.default_new_name")
        )
        new_name, ok = QInputDialog.getText(
            self,
            tr("live.profile.dialog.save.title"),
            tr("live.profile.dialog.save.label"),
            text=default_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if self._live_audio_profile_store.find(new_name) is not None:
            answer = QMessageBox.question(
                self,
                tr("live.profile.dialog.overwrite.title"),
                tr("live.profile.dialog.overwrite.text").format(name=new_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            profile = self._build_live_audio_profile_from_ui(new_name)
            self._live_audio_profile_store.upsert(profile)
        except OSError as exc:
            QMessageBox.critical(self, tr("live.profile.msg.save_failed.title"), str(exc))
            return
        self._reload_live_audio_profile_combo(select_name=new_name)

    def _on_live_audio_profile_update(self) -> None:
        name = self._current_live_audio_profile_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            tr("live.profile.dialog.update.title"),
            tr("live.profile.dialog.update.text").format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            profile = self._build_live_audio_profile_from_ui(name)
            self._live_audio_profile_store.upsert(profile)
        except OSError as exc:
            QMessageBox.critical(self, tr("live.profile.msg.save_failed.title"), str(exc))
            return
        self._reload_live_audio_profile_combo(select_name=name)

    def _on_live_audio_profile_delete(self) -> None:
        name = self._current_live_audio_profile_name()
        if not name:
            return
        if (
            name == DEFAULT_LIVE_AUDIO_PROFILE_NAME
            and len(self._live_audio_profile_store.names()) <= 1
        ):
            QMessageBox.information(
                self,
                tr("live.profile.msg.delete_blocked.title"),
                tr("live.profile.msg.delete_blocked.text"),
            )
            return
        answer = QMessageBox.question(
            self,
            tr("live.profile.dialog.delete.title"),
            tr("live.profile.dialog.delete.text").format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._live_audio_profile_store.remove(name)
        self._reload_live_audio_profile_combo(select_last=True)
        name_after = self._current_live_audio_profile_name()
        profile = self._live_audio_profile_store.find(name_after)
        if profile is not None:
            self._live_profile_loading = True
            try:
                self._apply_live_audio_profile(profile)
            finally:
                self._live_profile_loading = False

    def _update_vol_slider_labels(self) -> None:
        pairs = (
            (self._sl_mic_v, self._lb_mic_pct),
            (self._sl_mon_v, self._lb_mon_pct),
            (self._sl_funk_v, self._lb_funk_pct),
            (self._sl_flisten_v, self._lb_flisten_pct),
        )
        for sl, lb in pairs:
            pct = live_gain_display_percent(live_gain_from_slider(int(sl.value())))
            lb.setText(tr("live.strip.percent", pct=pct))

    def _pull_vol_sliders(self, *_v: object) -> None:
        """Lautstärke live an Engine/Monitor — ohne Stream-Neustart oder sofortiges Speichern."""
        self._update_vol_slider_labels()
        liv = LiveSettings.from_dict(self._live_snapshot.to_dict())
        liv.input_gain = live_gain_from_slider(int(self._sl_mic_v.value()))
        liv.output_gain = live_gain_from_slider(int(self._sl_mon_v.value()))
        liv.funk_output_gain = live_gain_from_slider(int(self._sl_funk_v.value()))
        liv.funk_listen_gain = live_gain_from_slider(int(self._sl_flisten_v.value()))
        liv.clamp_recursive()
        self._live_snapshot = liv
        self._settings.live = LiveSettings.from_dict(liv.to_dict())
        self._push_live_engine_runtime_settings(liv)
        if (
            self._engine.is_idle_listen_monitor_running()
            or self._engine.is_mic_preview_running()
        ):
            self._engine.push_idle_listen_settings(liv)
        self._vol_persist_timer.start()

    def _flush_vol_persist(self) -> None:
        if self._vol_persist_timer.isActive():
            self._vol_persist_timer.stop()
            self._persist()

    def _pull_sliders_into_snapshot(self) -> None:
        """Kompatibel für Start‑Button (löst Vol‑Labels mit aus)."""
        self._pull_vol_sliders()

    def _pull_chk_into_snapshot(self, _chk: Optional[bool] = None) -> None:
        del _chk
        self._push_snapshot()

    def _on_afl_toggled(self, _checked: bool) -> None:
        self._refresh_afl_button_appearance()
        self._push_snapshot()

    def _refresh_gate_comp_readouts(self) -> None:
        """Zeigt die aktuellen Gate-/Kompressor-Werte rechts neben den Slidern."""
        self._gate_thr_lbl.setText(tr("live.readout.db", value=self._g_thr.value() / 10.0))
        self._gate_att_lbl.setText(tr("live.readout.ms_nbsp", value=self._g_att.value()))
        self._gate_hld_lbl.setText(tr("live.readout.ms_nbsp", value=self._g_hld.value()))
        self._gate_rel_lbl.setText(tr("live.readout.ms_nbsp", value=self._g_rel.value()))

        self._comp_thr_lbl.setText(tr("live.readout.db", value=self._c_thr.value() / 10.0))
        ratio = self._c_rat.value() / 100.0
        self._comp_rat_lbl.setText(tr("live.comp.ratio_display", ratio=f"{ratio:.2f}".replace(".", ",")))
        self._comp_att_lbl.setText(tr("live.readout.ms_nbsp", value=self._c_att.value()))
        self._comp_rel_lbl.setText(tr("live.readout.ms_nbsp", value=self._c_rel.value()))
        self._comp_mk_lbl.setText(tr("live.readout.db", value=self._c_mk.value() / 10.0))

        self._fg_thr_lbl.setText(tr("live.readout.db", value=self._fg_thr.value() / 10.0))
        self._fg_att_lbl.setText(tr("live.readout.ms_nbsp", value=self._fg_att.value()))
        self._fg_hld_lbl.setText(tr("live.readout.ms_nbsp", value=self._fg_hld.value()))
        self._fg_rel_lbl.setText(tr("live.readout.ms_nbsp", value=self._fg_rel.value()))

    def _pull_rx_noise_gate(self, *_v: object) -> None:
        self._refresh_gate_comp_readouts()
        self._push_snapshot()
        self._engine.reset_funk_listen_noise_gate()

    def _pull_slider_gate_comp(self, _value: Optional[int] = None) -> None:
        del _value
        self._refresh_gate_comp_readouts()
        self._push_snapshot()

    def _push_snapshot(self, *, persist_disk: bool = True, refresh_monitors: bool = True) -> None:
        liv = self._gather_live_from_ui()
        self._live_snapshot = liv
        self._settings.live = LiveSettings.from_dict(liv.to_dict())
        if persist_disk:
            self._persist()
        self._push_live_engine_runtime_settings(liv)
        if refresh_monitors:
            self._refresh_idle_listen_monitor(liv)
            self._refresh_mic_preview_monitor(liv)

    def _schedule_samplerate_label_update(
        self,
        liv: Optional[LiveSettings] = None,
        *,
        refresh_monitors_after: bool = False,
    ) -> None:
        if refresh_monitors_after:
            self._sr_refresh_monitors_after = True
        ref = liv if liv is not None else self._live_snapshot
        self._refresh_live_footer()
        self._sr_preview_req += 1
        req = self._sr_preview_req
        self._sr_preview_pending = req
        self._sr_preview_worker.preview_requested.emit(ref.to_dict(), req)

    def _on_stream_params_preview_finished(
        self, request_id: int, sr: int, bs: int
    ) -> None:
        if request_id != self._sr_preview_pending:
            return
        prev_sr = int(self._live_snapshot.samplerate)
        prev_bs = int(self._live_snapshot.blocksize)
        self._live_snapshot.samplerate = sr
        self._live_snapshot.blocksize = bs
        self._settings.live.samplerate = sr
        self._settings.live.blocksize = bs
        self._refresh_live_footer()
        if self._engine.is_running():
            self._sr_refresh_monitors_after = False
            return
        refresh = (
            self._sr_refresh_monitors_after or prev_sr != sr or prev_bs != bs
        )
        self._sr_refresh_monitors_after = False
        if not refresh:
            return
        liv = self._gather_live_from_ui()
        self._idle_monitor_fp_key = None
        self._mic_preview_fp_key = None
        QTimer.singleShot(0, lambda: self._refresh_idle_listen_monitor(liv))
        QTimer.singleShot(0, lambda: self._refresh_mic_preview_monitor(liv))

    def _shutdown_sr_preview_thread(self) -> None:
        tt = getattr(self, "_sr_preview_thread", None)
        if tt is None or not tt.isRunning():
            return
        tw = getattr(self, "_sr_preview_worker", None)
        if tw is not None:
            tw.blockSignals(True)
        tt.quit()
        if not tt.wait(2000):
            tt.terminate()
            tt.wait(500)

    def _restart_engine(self) -> None:
        if not self._engine.is_running():
            return
        self._safe_live_cat_tx_off()
        self._engine.stop()
        self._request_live_cat_tx_on()
        ok, txt = self._engine.start(LiveSettings.from_dict(self._live_snapshot.to_dict()))
        if not ok:
            self._safe_live_cat_tx_off()
            QMessageBox.warning(self, tr("live.msgbox.title"), txt)
            self._clear_live_ptt_wants()
            self._release_voice_plain_after_stop_live_if_not_mithoren()
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
            return
        self._push_live_engine_runtime_settings(
            LiveSettings.from_dict(self._live_snapshot.to_dict())
        )
        self._refresh_ptt_controls_enabled()
        self._refresh_ptt_button_appearance()

    def _refresh_idle_listen_monitor(self, liv: Optional[LiveSettings] = None) -> None:
        """Offline: Mic‑Send‑Pegel/Abhör, optional Funk‑Eingang → Monitor."""
        if getattr(self, "_suppress_idle_listen_monitor", False):
            return

        ref = liv
        if ref is None:
            ref = LiveSettings.from_dict(self._gather_live_from_ui().to_dict())
        else:
            ref = LiveSettings.from_dict(ref.to_dict())

        if not _live_window_accepts_background_audio(self):
            self._engine.stop_idle_listen_monitor()
            self._engine.stop_mic_preview_monitor()
            self._idle_monitor_fp_key = None
            self._mic_preview_fp_key = None
            return

        if self._engine.is_running():
            self._idle_monitor_fp_key = None
            self._mic_preview_fp_key = None
            return

        prereq_ok, _ = self._engine.prerequisites_ok()
        if not prereq_ok:
            self._engine.stop_idle_listen_monitor()
            self._engine.stop_mic_preview_monitor()
            self._idle_monitor_fp_key = None
            self._mic_preview_fp_key = None
            return

        mic_sid = str(ref.input_device_id or "").strip()
        listen_sid = str(ref.funk_listen_input_device_id or "").strip()
        mon_sid = str(ref.output_device_id or "").strip()
        if not mic_sid and not listen_sid:
            self._engine.stop_idle_listen_monitor()
            self._engine.stop_mic_preview_monitor()
            self._idle_monitor_fp_key = None
            self._mic_preview_fp_key = None
            return

        fp_key: tuple[object, ...] = (
            mic_sid,
            listen_sid,
            mon_sid,
            int(ref.samplerate),
            int(ref.blocksize),
            bool(ref.suppress_live_monitor_mic),
        )
        if fp_key == self._idle_monitor_fp_key and self._engine.is_idle_listen_monitor_running():
            self._engine.push_idle_listen_settings(ref)
            self._update_device_summary_labels()
            return

        self._idle_monitor_fp_key = fp_key
        self._mic_preview_fp_key = fp_key
        ok, msg = self._engine.start_idle_listen_monitor(ref)
        if not ok:
            self._idle_monitor_fp_key = None
            self._mic_preview_fp_key = None
            self._engine.stop_idle_listen_monitor()
            self._update_device_summary_labels(
                error=tr("live.error.monitor_start", msg=msg)
            )
            return
        self._update_device_summary_labels()

    def _refresh_mic_preview_monitor(self, liv: Optional[LiveSettings] = None) -> None:
        """Wird über :meth:`_refresh_idle_listen_monitor` mit abgedeckt."""
        self._refresh_idle_listen_monitor(liv)

    def _defer_refresh_idle_listen_monitor(self) -> None:
        if not _live_window_accepts_background_audio(self):
            return
        try:
            self._refresh_idle_listen_monitor()
            self._refresh_mic_preview_monitor()
        except Exception:
            self._idle_monitor_fp_key = None
            self._mic_preview_fp_key = None
            try:
                self._engine.stop_idle_listen_monitor()
                self._engine.stop_mic_preview_monitor()
            except Exception:
                pass

    def _restore_geometry(self) -> None:
        raw = self._settings.live.window_geometry or self._live_snapshot.window_geometry
        if not raw:
            return
        try:
            b = QByteArray(base64.b64decode(raw.encode("ascii")))
            if not b.isEmpty():
                self.restoreGeometry(b)
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._ensure_live_ctrl_y_filter_released()
        self._release_keyboard_ptt_momentary()
        self._suppress_funk_listen_while_live_tx_active = False
        self._suppress_idle_listen_monitor = True
        self._engine.stop_idle_listen_monitor()
        self._engine.stop_mic_preview_monitor()
        self._idle_monitor_fp_key = None
        self._mic_preview_fp_key = None
        self._save_geo_to_settings()
        self._flush_vol_persist()
        self._live_cat_settle.stop()

        if getattr(self, "_force_close", False):
            self._shutdown_sr_preview_thread()
            self._shutdown_ptt_thread()
            super().closeEvent(event)
            return

        cat_ok = self._cat is not None and self._cat.is_connected()
        self._live_data_session_active = False
        self._pending_open_window_data_engage = False
        self._engage_data_session_only = False
        self._resume_live_transport_after_reconnect = False
        self._resume_live_engine_after_reconnect = False
        self._resume_idle_monitor_after_reconnect = False
        self._reconnect_engine_resume_pending = False

        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)

        if self._engine.is_running() or self._cat_live_start_busy:
            self._stop_live_via_ptt(
                clear_ptt_wants=True,
                invoke_release_voice_after_live=cat_ok,
            )
        else:
            self._clear_live_transport_pending_flags()
            self._clear_live_ptt_wants()
            self._safe_live_cat_tx_off()

        if cat_ok:
            self._ensure_live_cat_tx_off_blocking()
        self._shutdown_sr_preview_thread()
        self._shutdown_ptt_thread()

        if self._audio_radio_session is not None:
            if cat_ok:
                self._audio_radio_session.request_restore_if_no_windows()
            else:
                self._audio_radio_session.discard_state_if_disconnected()

        if cat_ok:
            # Live-Fenster endgültig — DATA/Sprache wie vor Live wiederherstellen,
            # auch wenn „Mithören“ beim Stop die Umschaltung unterdrückt hat.
            self._release_live_voice_mode_plain()
        self._sync_live_eq_profile_for_session(entering=False)
        super().closeEvent(event)

    def _save_geo_to_settings(self) -> None:
        geo = self.saveGeometry()
        if not geo.isEmpty():
            b64 = base64.b64encode(geo.data()).decode("ascii")
            self._settings.live.window_geometry = b64

    def force_close(self) -> None:
        """App-Ende oder erzwungenes Schließen: Stream stoppen, Funk‑Restore wie andere Audio‑Fenster."""
        self._ensure_live_ctrl_y_filter_released()
        self._release_keyboard_ptt_momentary()
        self._suppress_funk_listen_while_live_tx_active = False
        self._suppress_idle_listen_monitor = True
        self._live_cat_settle.stop()
        self._cat_live_start_busy = False
        self._pending_live_after_pc_then_engage = False
        self._live_cat_waiting_engage_finish = False
        self._mic_ptt_interrupted_live = False
        self._live_data_session_active = False
        self._pending_open_window_data_engage = False
        self._engage_data_session_only = False
        if self._engine.is_running():
            self._engine.stop()
        self._safe_live_cat_tx_off()
        if self._cat is not None and self._cat.is_connected():
            self._ensure_live_cat_tx_off_blocking()
        self._shutdown_sr_preview_thread()
        self._shutdown_ptt_thread()
        self._release_live_voice_mode_plain()
        if self._audio_radio_session is not None:
            self._audio_radio_session.detach_for_force_close(self)
        self._sync_live_eq_profile_for_session(entering=False)
