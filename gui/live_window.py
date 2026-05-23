"""Live-Monitoring: Mikrofon → DSP-Ausgang (sounddevice, getrennt vom Audio‑Player)."""

from __future__ import annotations

import base64
import math
from typing import TYPE_CHECKING, Callable, List, Optional

from PySide6.QtCore import QByteArray, QMetaObject, QThread, Qt, QTimer, Q_ARG
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
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
from gui.live_eq_editor_widget import LiveEqEditorWidget
from gui.meter_widget import ScaledMeterBar, live_dbfs_peak_to_raw, make_live_level_bar
from audio.cat_ptt_worker import CatPttWorker
from audio.player_controller import _invoke_ptt_worker_set_transmit
from audio.radio_playback_setup import data_mode_for_rx_mode
from cat import SerialCAT
from cat.ft991_cat import FT991CAT
from mapping import TX_STATE_MIC_PTT, TX_STATE_RX
from live.live_audio_engine import LiveAudioEngine
from live.live_devices import (
    list_input_devices,
    list_output_devices,
    remap_live_device_id,
)
from mapping.rx_mapping import RxMode
from model import AppSettings
from model.live_settings import LiveEqBandSettings, LiveSettings

if TYPE_CHECKING:
    from gui.audio_radio_session import AudioRadioSessionHost


_YAESU_GREEN = "#52c41a"
# Kurzer Puffer nach DATA/Menüs, bevor Audio‑Stream und TX1; kommen —
# kleiner als beim Player/Rekorder, Latenz beim „Start Live“ senken.
_CAT_LIVE_RADIO_SETTLE_MS = 75


def _fmt_db(raw: float) -> str:
    if not math.isfinite(raw):
        return "—"
    return f"{raw:+.1f}"


class LiveWindow(QMainWindow):
    """Regler spiegeln Settings; Engine liest kopiertes ``LiveSettings`` im Callback."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        persist_settings: Callable[[], None],
        serial_cat: Optional[SerialCAT] = None,
        audio_radio_session: Optional["AudioRadioSessionHost"] = None,
        operating_mode_provider: Optional[Callable[[], RxMode]] = None,
        other_audio_blocking: Optional[Callable[[], str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._persist = persist_settings
        self._live_snapshot = LiveSettings.from_dict(settings.live.to_dict())
        self._cat = serial_cat
        self._audio_radio_session = audio_radio_session
        self._operating_mode_provider = operating_mode_provider
        self._other_audio_blocking_fn = other_audio_blocking
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

        self.setWindowTitle("Live")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.resize(980, 720)

        self._engine = LiveAudioEngine(on_error=lambda _msg: None)

        if self._cat is not None and self._radio_setup is not None:
            if self._setup_worker is not None:
                self._setup_worker.pc_menus_finished.connect(
                    self._on_live_radio_pc_menus_for_start,
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
        self._populate_devices()
        self._apply_live_to_ui()

        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(60)
        self._meter_timer.timeout.connect(self._meter_tick)
        self._meter_timer.start()

        self._c_sr.currentTextChanged.connect(self._on_sr_bs_changed_restart)
        self._c_bs.currentIndexChanged.connect(self._on_sr_bs_changed_restart)

        self._restore_geometry()

        self._suppress_idle_listen_monitor = False
        self._idle_monitor_fp_key: Optional[tuple[object, ...]] = None
        self._suppress_funk_listen_while_live_tx_active = False

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
            if bool(self._chk_funk_listen.isChecked()):
                if not hold:
                    self._suppress_funk_listen_while_live_tx_active = True
                    self._push_live_engine_runtime_settings(self._gather_live_from_ui())
            elif hold:
                self._suppress_funk_listen_while_live_tx_active = False
                self._push_live_engine_runtime_settings(self._gather_live_from_ui())
            return

        if hold:
            self._suppress_funk_listen_while_live_tx_active = False
            self._push_live_engine_runtime_settings(self._gather_live_from_ui())

    def _build_ui(self) -> None:
        cen = QWidget()
        root = QVBoxLayout(cen)

        dev = QGroupBox("Gerät & Konfiguration")
        df = QFormLayout(dev)
        self._c_in = QComboBox()
        self._c_out = QComboBox()
        self._c_funk = QComboBox()
        df.addRow("PC‑Mikrofon:", self._c_in)
        df.addRow("Monitor:", self._c_out)
        df.addRow("Funk‑Ausgang:", self._c_funk)

        funk_listen_row = QWidget()
        funk_listen_lay = QHBoxLayout(funk_listen_row)
        funk_listen_lay.setContentsMargins(0, 0, 0, 0)
        funk_listen_lay.setSpacing(10)
        self._c_funk_listen = QComboBox()
        self._c_funk_listen.setMinimumWidth(220)
        self._chk_funk_listen = QCheckBox("Mithören")
        self._chk_funk_listen.setToolTip(
            "Funkeingabe‑Mithören über Monitor‑Ausgang."
        )
        funk_listen_lay.addWidget(self._c_funk_listen, 1)
        funk_listen_lay.addWidget(self._chk_funk_listen)
        lf_lbl = QLabel("Funk‑Eingang:")
        lf_lbl.setBuddy(self._c_funk_listen)
        df.addRow(lf_lbl, funk_listen_row)
        self._chk_funk_listen.toggled.connect(self._on_funk_listen_toggled_save)

        bf = QHBoxLayout()
        self._c_sr = QComboBox()
        self._c_sr.addItems(["44100", "48000"])
        bf.addWidget(QLabel("Samplerate Hz:"))
        bf.addWidget(self._c_sr)
        bf.addSpacing(14)
        self._c_bs = QComboBox()
        for b in (128, 256, 512):
            self._c_bs.addItem(str(b), b)
        bf.addWidget(QLabel("Block"))
        bf.addWidget(self._c_bs)
        bf.addSpacing(14)
        self._chk_live_mithoren = QCheckBox("Live Mithören")
        self._chk_live_mithoren.setToolTip("Eigene Ausgabe mithören")
        self._chk_live_mithoren.toggled.connect(
            self._on_suppress_live_monitor_toggled
        )
        bf.addWidget(self._chk_live_mithoren)
        bf.addSpacing(12)
        rl = QPushButton("Geräte neu laden")
        rl.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )
        rl.clicked.connect(self._populate_devices)
        bf.addWidget(rl)
        bf.addStretch(1)
        wbf = QWidget()
        wbf.setLayout(bf)
        df.addRow(wbf)
        root.addWidget(dev)

        self._chk_eq_master = QCheckBox("EQ gesamt aktiv")
        self._chk_eq_master.toggled.connect(self._on_chk_eq_master)
        self._chk_eq_master.setStyleSheet(f"color:{_YAESU_GREEN};")

        ego = QGroupBox("Siebenband‑EQ")
        ego.setStyleSheet(
            "QGroupBox { background:#161616; color:#e0e0e0;"
            "border:1px solid #2c2c2c; border-radius:4px;"
            "padding-top:6px;}"
        )
        ego_lay = QVBoxLayout(ego)
        head = QHBoxLayout()
        head.addWidget(self._chk_eq_master)
        head.addStretch(1)
        ego_lay.addLayout(head)

        eq_row = QHBoxLayout()
        self._live_eq = LiveEqEditorWidget()
        self._live_eq.changed.connect(self._on_live_eq_editor_changed)
        eq_row.addWidget(self._live_eq, stretch=1)

        strip = QWidget()
        srl = QHBoxLayout(strip)
        srl.setContentsMargins(4, 0, 0, 10)
        srl.setSpacing(12)

        vs_style = (
            "QSlider::groove:vertical { background-color:#353535; width:7px; }"
            "QSlider::handle:vertical {"
            f" background-color:{_YAESU_GREEN};"
            " border:1px solid #1e5c16;"
            " min-height:15px; max-height:15px; margin:0 -6px;"
            "}"
        )

        def _mk_v_col(caption: str, peak_bar: ScaledMeterBar) -> tuple[QSlider, QLabel]:
            col = QWidget()
            vl = QVBoxLayout(col)
            vl.setContentsMargins(0, 4, 0, 16)
            vl.setSpacing(4)
            t = QLabel(caption)
            t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            t.setWordWrap(False)
            t.setStyleSheet(
                "color:#bdbdbd;font-size:10px;font-weight:600;"
            )
            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(0, 200)
            sl.setMinimumHeight(260)
            sl.setTracking(True)
            sl.setMinimumWidth(28)
            sl.setStyleSheet(vs_style)
            lb = QLabel("100 %")
            lb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lb.setMinimumWidth(42)
            lb.setStyleSheet(
                "color:#dcdcdc;font-size:11px;padding-top:10px;"
            )
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            rl.addStretch(1)
            rl.addWidget(sl, 0, Qt.AlignmentFlag.AlignTop)
            rl.addWidget(peak_bar, 0, Qt.AlignmentFlag.AlignTop)
            rl.addStretch(1)
            vl.addWidget(t, 0, Qt.AlignmentFlag.AlignHCenter)
            vl.addWidget(row, 1, Qt.AlignmentFlag.AlignTop)
            vl.addWidget(lb, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            srl.addWidget(col)
            return sl, lb

        _pm = make_live_level_bar()
        _pmon = make_live_level_bar()
        _pf = make_live_level_bar()
        _pfl = make_live_level_bar()
        self._live_level_bars = [_pm, _pmon, _pf, _pfl]

        self._sl_mic_v, self._lb_mic_pct = _mk_v_col("Mic", _pm)
        self._sl_mon_v, self._lb_mon_pct = _mk_v_col("Monitor", _pmon)
        self._sl_funk_v, self._lb_funk_pct = _mk_v_col("Funk", _pf)
        self._sl_flisten_v, self._lb_flisten_pct = _mk_v_col("Funk‑Eingang", _pfl)
        self._sl_mic_v.valueChanged.connect(self._pull_vol_sliders)
        self._sl_mon_v.valueChanged.connect(self._pull_vol_sliders)
        self._sl_funk_v.valueChanged.connect(self._pull_vol_sliders)
        self._sl_flisten_v.valueChanged.connect(self._pull_vol_sliders)

        srl.addStretch(0)
        eq_row.addWidget(strip, 0, Qt.AlignmentFlag.AlignTop)

        ego_lay.addLayout(eq_row)

        root.addWidget(ego)

        def _mk_read_lbl() -> QLabel:
            v = QLabel("—")
            v.setMinimumWidth(86)
            v.setAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
            )
            v.setStyleSheet("color:#c8c8c8;font-size:11px;font-weight:600;")
            return v

        # Gate
        gb = QGroupBox("Noise Gate")
        gv = QGridLayout(gb)
        gv.setHorizontalSpacing(8)
        gv.setColumnStretch(1, 1)
        self._g_en = QCheckBox("Gate aktiv")
        self._g_en.toggled.connect(self._pull_chk_into_snapshot)
        self._g_thr = QSlider(Qt.Orientation.Horizontal)
        self._g_thr.setRange(-800, -200)
        self._g_thr.valueChanged.connect(self._pull_slider_gate_comp)
        self._g_att = QSlider(Qt.Orientation.Horizontal)
        self._g_att.setRange(1, 50)
        self._g_att.valueChanged.connect(self._pull_slider_gate_comp)
        self._g_hld = QSlider(Qt.Orientation.Horizontal)
        self._g_hld.setRange(10, 300)
        self._g_hld.valueChanged.connect(self._pull_slider_gate_comp)
        self._g_rel = QSlider(Qt.Orientation.Horizontal)
        self._g_rel.setRange(20, 1000)
        self._g_rel.valueChanged.connect(self._pull_slider_gate_comp)

        gv.addWidget(self._g_en, 0, 0, 1, 3)
        r = 1
        self._gate_thr_lbl = _mk_read_lbl()
        self._gate_att_lbl = _mk_read_lbl()
        self._gate_hld_lbl = _mk_read_lbl()
        self._gate_rel_lbl = _mk_read_lbl()
        for lab_txt, slid, suf, read_lbl in (
            ("Schwellwert −80 … −20 dB (Slider÷10)", self._g_thr, "", self._gate_thr_lbl),
            ("Attack", self._g_att, "", self._gate_att_lbl),
            ("Hold", self._g_hld, "", self._gate_hld_lbl),
            ("Release", self._g_rel, "", self._gate_rel_lbl),
        ):
            gv.addWidget(QLabel(lab_txt + suf), r, 0)
            gv.addWidget(slid, r, 1)
            gv.addWidget(read_lbl, r, 2)
            r += 1

        # Compressor
        cb = QGroupBox("Kompressor")
        cgrid = QGridLayout(cb)
        cgrid.setHorizontalSpacing(8)
        cgrid.setColumnStretch(1, 1)
        self._c_en = QCheckBox("Kompressor aktiv")
        self._c_en.toggled.connect(self._pull_chk_into_snapshot)
        self._c_thr = QSlider(Qt.Orientation.Horizontal)
        self._c_thr.setRange(-400, 0)
        self._c_thr.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_rat = QSlider(Qt.Orientation.Horizontal)
        self._c_rat.setRange(100, 1000)
        self._c_rat.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_att = QSlider(Qt.Orientation.Horizontal)
        self._c_att.setRange(1, 50)
        self._c_att.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_rel = QSlider(Qt.Orientation.Horizontal)
        self._c_rel.setRange(20, 500)
        self._c_rel.valueChanged.connect(self._pull_slider_gate_comp)
        self._c_mk = QSlider(Qt.Orientation.Horizontal)
        self._c_mk.setRange(0, 120)
        self._c_mk.valueChanged.connect(self._pull_slider_gate_comp)

        cgrid.addWidget(self._c_en, 0, 0, 1, 3)
        cr = 1
        self._comp_thr_lbl = _mk_read_lbl()
        self._comp_rat_lbl = _mk_read_lbl()
        self._comp_att_lbl = _mk_read_lbl()
        self._comp_rel_lbl = _mk_read_lbl()
        self._comp_mk_lbl = _mk_read_lbl()
        pairs = (
            ("Threshold (×0,1 dB)", self._c_thr, self._comp_thr_lbl),
            ("Ratio (×100)", self._c_rat, self._comp_rat_lbl),
            ("Attack", self._c_att, self._comp_att_lbl),
            ("Release", self._c_rel, self._comp_rel_lbl),
            ("Make‑up (×0,1 dB)", self._c_mk, self._comp_mk_lbl),
        )
        for a, sl, rlbl in pairs:
            cgrid.addWidget(QLabel(a), cr, 0)
            cgrid.addWidget(sl, cr, 1)
            cgrid.addWidget(rlbl, cr, 2)
            cr += 1

        gc_row = QWidget()
        gc_lay = QHBoxLayout(gc_row)
        gc_lay.setContentsMargins(0, 0, 0, 0)
        gc_lay.setSpacing(10)
        gc_lay.addWidget(gb, 1)
        gc_lay.addWidget(cb, 1)
        root.addWidget(gc_row)

        meter = QGroupBox("Pegelanzeige & Latenzhinweis")
        ml = QHBoxLayout(meter)
        self._m_in = QLabel("Eing.: —")
        self._m_out = QLabel("Ausg.: —")
        self._m_lat = QLabel("Latenz: —")
        ml.addWidget(self._m_in)
        ml.addWidget(self._m_out)
        ml.addStretch(1)
        ml.addWidget(self._m_lat)
        root.addWidget(meter)

        row_btn = QHBoxLayout()
        self._b_start = QPushButton("Start Live")
        self._b_stop = QPushButton("Stop Live")
        self._b_stop.setEnabled(False)
        self._b_start.clicked.connect(self._do_start)
        self._b_stop.clicked.connect(self._do_stop)
        row_btn.addWidget(self._b_start)
        row_btn.addWidget(self._b_stop)
        row_btn.addStretch(1)
        root.addLayout(row_btn)
        root.addStretch()
        self.setCentralWidget(cen)

    def reload_from_app_settings(self) -> None:
        """Nach Änderungen z. B. in den Soundeinstellungen die PortAudio-Felder neu laden."""
        self._live_snapshot = LiveSettings.from_dict(self._settings.live.to_dict())
        self._populate_devices()
        self._apply_live_to_ui()

    def sync_data_mode_from_main(self, mode: Optional[RxMode] = None) -> None:
        """DATA‑Ziel wie Audio‑Recorder/‑Player aus der Hauptfenster‑Betriebsart."""
        if self._radio_setup is None:
            return
        if mode is None:
            if self._operating_mode_provider is not None:
                mode = self._operating_mode_provider()
            elif self._cat is not None and self._cat.is_connected():
                try:
                    mode = FT991CAT(self._cat).read_rx_mode()
                except Exception:
                    return
            else:
                return
        data_mode = data_mode_for_rx_mode(mode)
        self._settings.audio_player.data_mode = data_mode.value  # type: ignore[assignment]
        self._radio_setup.align_data_mode_to_rx_mode(mode)
        if not self._radio_setup.is_applied or not self._radio_setup.in_data_mode:
            return
        if self._engine.is_running():
            return
        if self._radio_setup.data_mode == data_mode:
            return
        if self._setup_worker is None:
            return
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_set_data_mode",
            Qt.QueuedConnection,
            Q_ARG(str, data_mode.value),
        )

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._suppress_idle_listen_monitor = False
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_shown(self)
        QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._engine.stop_idle_listen_monitor()
        self._idle_monitor_fp_key = None
        super().hideEvent(event)
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)

    def handle_tx_state_changed(self, state: int) -> None:
        """MIC‑PTT sowie Funk‑Mithören‑Stummschalter während Live‑TX."""
        transmitting = bool(state != TX_STATE_RX)
        self._sync_live_funk_listen_mute_while_cat_tx(transmitting)

        if (
            self._radio_setup is None
            or not self._radio_setup.is_applied
            or self._setup_worker is None
        ):
            return
        if state == TX_STATE_MIC_PTT:
            if not self._engine.is_running() and not self._radio_setup.in_data_mode:
                return
            self._live_cat_settle.stop()
            if self._cat_live_start_busy:
                self._pending_live_after_pc_then_engage = False
                self._live_cat_waiting_engage_finish = False
                self._cat_live_start_busy = False
                self._b_start.setEnabled(True)
                self._b_stop.setEnabled(False)

            self._mic_ptt_interrupted_live = True
            if self._engine.is_running():
                self._engine.stop()
                self._safe_live_cat_tx_off()
            self._b_start.setEnabled(True)
            self._b_stop.setEnabled(False)

            if self._radio_setup.in_data_mode:
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_engage_plain_forced",
                    Qt.QueuedConnection,
                )
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
            return
        if state == TX_STATE_RX and self._mic_ptt_interrupted_live:
            if self._radio_setup.needs_plain_verify and self._setup_worker is not None:
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_verify_plain",
                    Qt.QueuedConnection,
                )
            self._mic_ptt_interrupted_live = False

    def _on_live_ptt_failed(self, message: str) -> None:
        if message.strip():
            QMessageBox.warning(self, "Live — CAT‑PTT", message)

    def _on_live_radio_pc_menus_for_start(self, ok: bool, message: str) -> None:
        if not getattr(self, "_pending_live_after_pc_then_engage", False):
            return
        self._pending_live_after_pc_then_engage = False
        if not self._cat_live_start_busy or self._radio_setup is None:
            return
        if not ok:
            self._abort_live_cat_start(message or "Menü‑Setup für PC‑Audio fehlgeschlagen.")
            return
        self._invoke_worker_engage_data_for_live()

    def _on_live_radio_engage_finished(self, ok: bool, message: str) -> None:
        if not getattr(self, "_live_cat_waiting_engage_finish", False):
            return
        self._live_cat_waiting_engage_finish = False
        if not self._cat_live_start_busy or self._radio_setup is None:
            return
        if not ok:
            self._abort_live_cat_start(message or "DATA‑Modus konnte nicht gesetzt werden.")
            return
        self._schedule_live_engine_after_radio_settled(fresh_data_engaged=True)

    def _invoke_worker_engage_data_for_live(self) -> None:
        """Nur wenn PC‑Menüs und Snapshot bereits da sind."""
        if self._setup_worker is None or self._radio_setup is None:
            self._abort_live_cat_start("Intern: kein Funk‑CAT‑Worker fehlt.")
            return
        self._live_cat_waiting_engage_finish = True
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_engage_data",
            Qt.QueuedConnection,
        )

    def _abort_live_cat_start(self, detail: str) -> None:
        self._live_cat_settle.stop()
        self._suppress_funk_listen_while_live_tx_active = False
        self._cat_live_start_busy = False
        self._pending_live_after_pc_then_engage = False
        self._live_cat_waiting_engage_finish = False
        self._b_start.setEnabled(True)
        self._b_stop.setEnabled(False)
        QMessageBox.warning(self, "Live", detail)
        QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def _schedule_live_engine_after_radio_settled(self, *, fresh_data_engaged: bool = True) -> None:
        """Nach DATA‑Umschaltung oder Menüs kurz Puffer vor Stream/T — „schon DATA“ weniger Latenz."""
        if fresh_data_engaged:
            ms = max(25, _CAT_LIVE_RADIO_SETTLE_MS)
        else:
            ms = 25
        self._live_cat_settle.start(ms)

    def _on_live_radio_settled_start_engine(self) -> None:
        if not self._cat_live_start_busy or self._engine.is_running():
            return
        liv = LiveSettings.from_dict(self._gather_live_from_ui().to_dict())
        self._live_snapshot = liv
        self._settings.live = liv
        self._persist()
        ok_s, txt = self._engine.start(LiveSettings.from_dict(liv.to_dict()))
        if not ok_s:
            self._abort_live_cat_start(txt or "Stream konnte nicht starten.")
            return
        if self._ptt_worker is not None:
            _invoke_ptt_worker_set_transmit(self._ptt_worker, True)
        self._suppress_funk_listen_while_live_tx_active = bool(self._chk_funk_listen.isChecked())
        self._push_live_engine_runtime_settings(LiveSettings.from_dict(liv.to_dict()))
        self._cat_live_start_busy = False
        self._b_start.setEnabled(False)
        self._b_stop.setEnabled(True)

    def _begin_live_cat_radio_path(self) -> None:
        assert self._radio_setup is not None
        rs = self._radio_setup

        blocked = ""
        fn = getattr(self, "_other_audio_blocking_fn", None)
        if callable(fn):
            blocked = str(fn()).strip()
        if blocked:
            self._live_cat_settle.stop()
            self._cat_live_start_busy = False
            self._pending_live_after_pc_then_engage = False
            self._live_cat_waiting_engage_finish = False
            self._b_start.setEnabled(True)
            self._b_stop.setEnabled(False)
            QMessageBox.information(self, "Live", blocked)
            return

        if self._cat is None or not self._cat.is_connected():
            self._abort_live_cat_start(
                "CAT nicht verbunden — bitte zuerst im Hauptfenster verbinden.",
            )
            return
        self.sync_data_mode_from_main()

        if not rs.is_applied:
            self._pending_live_after_pc_then_engage = True
            if self._setup_worker is None:
                self._abort_live_cat_start("Intern: Funk‑CAT‑Worker fehlt.")
                return
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_apply_pc_menus",
                Qt.QueuedConnection,
            )
            return

        if not rs.in_data_mode:
            self._invoke_worker_engage_data_for_live()
            return

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
        if self._ptt_worker is not None:
            _invoke_ptt_worker_set_transmit(self._ptt_worker, False)
        elif self._cat is not None and self._cat.is_connected():
            try:
                FT991CAT(self._cat).set_cat_transmit(False)
            except Exception:
                pass

    def _release_live_voice_mode_plain(self) -> None:
        if (
            self._radio_setup is not None
            and self._radio_setup.is_applied
            and self._radio_setup.in_data_mode
            and self._setup_worker is not None
        ):
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_engage_plain",
                Qt.QueuedConnection,
            )

    def _mithoren_keeps_radio_data_until_window_close(self) -> bool:
        """Wahr: Stop Live gibt Sprachmodus nicht zurück — Funk bleibt in DATA bis Fensterende."""
        return bool(
            LiveSettings.from_dict(self._live_snapshot.to_dict()).funk_listen_enabled
        )

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
            hint_text=(
                ""
                if on
                else "○ aus — „EQ gesamt aktiv“ ist deaktiviert (Bypass)"
            ),
        )
        self._live_eq.set_read_only(not on)

    def _meter_tick(self) -> None:
        indb, outdb = self._engine.peek_meters_db()
        self._m_in.setText(f"Eing.: {_fmt_db(indb)} dBFS")
        self._m_out.setText(f"Ausg.: {_fmt_db(outdb)} dBFS")
        for peak_bar, dbv in zip(
            self._live_level_bars,
            self._engine.peek_live_strip_meters_db(),
        ):
            peak_bar.set_value(live_dbfs_peak_to_raw(dbv))
        try:
            sr = float(self._c_sr.currentText())
            raw_bs = self._c_bs.currentData()
            bs = float(raw_bs) if raw_bs is not None else 256.0
        except Exception:
            sr, bs = 48000.0, 256.0
        latency_ms_est = round(2e3 * bs / sr, 2)
        self._m_lat.setText(f"grobe Latenz≈ {latency_ms_est} ms Duplex‑Block ×2")

    def _populate_devices(self) -> None:
        ids_in: List[str] = []
        ids_out: List[str] = []
        sel_in = self._c_in.currentData()
        sel_out = self._c_out.currentData()
        sel_funk = self._c_funk.currentData()
        sel_listen = self._c_funk_listen.currentData()
        self._c_in.blockSignals(True)
        self._c_out.blockSignals(True)
        self._c_funk.blockSignals(True)
        self._c_funk_listen.blockSignals(True)
        try:
            self._c_in.clear()
            self._c_out.clear()
            self._c_funk.clear()
            self._c_funk_listen.clear()
            tip_role = Qt.ItemDataRole.ToolTipRole
            for did, lbl, tip in list_input_devices():
                for cb_in in (self._c_in, self._c_funk_listen):
                    cb_in.addItem(lbl, did)
                    if tip:
                        cb_in.setItemData(cb_in.count() - 1, tip, tip_role)
                if did:
                    ids_in.append(str(did))
            for did, lbl, tip in list_output_devices():
                for cb in (self._c_out, self._c_funk):
                    cb.addItem(lbl, did)
                    if tip:
                        cb.setItemData(cb.count() - 1, tip, tip_role)
                if did:
                    ids_out.append(str(did))

            fin = remap_live_device_id(
                str(self._live_snapshot.input_device_id), input_device=True
            ) or str(self._live_snapshot.input_device_id or "")
            fout = remap_live_device_id(
                str(self._live_snapshot.output_device_id), input_device=False
            ) or str(self._live_snapshot.output_device_id or "")
            ffunk = remap_live_device_id(
                str(self._live_snapshot.funk_output_device_id), input_device=False
            ) or str(self._live_snapshot.funk_output_device_id or "")
            flisten_in = remap_live_device_id(
                str(self._live_snapshot.funk_listen_input_device_id),
                input_device=True,
            ) or str(self._live_snapshot.funk_listen_input_device_id or "")

            mig = False
            if fin and fin in ids_in and fin != self._live_snapshot.input_device_id:
                self._live_snapshot.input_device_id = fin
                self._settings.live.input_device_id = fin
                mig = True
            if fout and fout in ids_out and fout != self._live_snapshot.output_device_id:
                self._live_snapshot.output_device_id = fout
                self._settings.live.output_device_id = fout
                mig = True
            if ffunk and ffunk in ids_out and ffunk != self._live_snapshot.funk_output_device_id:
                self._live_snapshot.funk_output_device_id = ffunk
                self._settings.live.funk_output_device_id = ffunk
                mig = True
            if (
                flisten_in
                and flisten_in in ids_in
                and flisten_in != self._live_snapshot.funk_listen_input_device_id
            ):
                self._live_snapshot.funk_listen_input_device_id = flisten_in
                self._settings.live.funk_listen_input_device_id = flisten_in
                mig = True
            if mig:
                self._persist()

            self._pick_combo(self._c_in, sel_in, fin, ids_in)
            self._pick_combo(self._c_out, sel_out, fout, ids_out)
            self._pick_combo(self._c_funk, sel_funk, ffunk, ids_out)
            self._pick_combo(
                self._c_funk_listen, sel_listen, flisten_in, ids_in
            )

            if not getattr(self, "_live_dev_signals_wired", False):
                self._c_in.currentIndexChanged.connect(self._on_device_changed_save)
                self._c_out.currentIndexChanged.connect(self._on_device_changed_save)
                self._c_funk.currentIndexChanged.connect(self._on_device_changed_save)
                self._c_funk_listen.currentIndexChanged.connect(
                    self._on_device_changed_save
                )
                self._live_dev_signals_wired = True
        finally:
            self._c_in.blockSignals(False)
            self._c_out.blockSignals(False)
            self._c_funk.blockSignals(False)
            self._c_funk_listen.blockSignals(False)

    @staticmethod
    def _pick_combo(
        cb: QComboBox,
        previous: object,
        fallback: str,
        known_ids: List[str],
    ) -> None:
        want = str(previous if isinstance(previous, str) else "") or str(fallback or "")
        for i in range(cb.count()):
            if str(cb.itemData(i)) == want and want:
                cb.setCurrentIndex(i)
                return
        cb.setCurrentIndex(0)

    def _apply_live_to_ui(self) -> None:
        liv = LiveSettings.from_dict(self._live_snapshot.to_dict())

        pct_in = round(float(liv.input_gain) * 100.0)
        pct_out = round(float(liv.output_gain) * 100.0)
        pct_funk = round(float(liv.funk_output_gain) * 100.0)
        pct_fl = round(float(liv.funk_listen_gain) * 100.0)
        self._sl_mic_v.blockSignals(True)
        self._sl_mon_v.blockSignals(True)
        self._sl_funk_v.blockSignals(True)
        self._sl_flisten_v.blockSignals(True)
        self._sl_mic_v.setValue(min(200, max(0, pct_in)))
        self._sl_mon_v.setValue(min(200, max(0, pct_out)))
        self._sl_funk_v.setValue(min(200, max(0, pct_funk)))
        self._sl_flisten_v.setValue(min(200, max(0, pct_fl)))
        self._sl_mic_v.blockSignals(False)
        self._sl_mon_v.blockSignals(False)
        self._sl_funk_v.blockSignals(False)
        self._sl_flisten_v.blockSignals(False)
        self._lb_mic_pct.setText(f"{pct_in} %")
        self._lb_mon_pct.setText(f"{pct_out} %")
        self._lb_funk_pct.setText(f"{pct_funk} %")
        self._lb_flisten_pct.setText(f"{pct_fl} %")

        self._chk_funk_listen.blockSignals(True)
        self._chk_funk_listen.setChecked(bool(liv.funk_listen_enabled))
        self._chk_funk_listen.blockSignals(False)
        self._refresh_funk_listen_controls()

        self._chk_live_mithoren.blockSignals(True)
        self._chk_live_mithoren.setChecked(not bool(liv.suppress_live_monitor_mic))
        self._chk_live_mithoren.blockSignals(False)

        self._c_sr.blockSignals(True)
        self._c_sr.setCurrentText(str(int(liv.samplerate)))
        self._c_sr.blockSignals(False)

        ix = max(0, self._c_bs.findData(int(liv.blocksize)))
        self._c_bs.blockSignals(True)
        self._c_bs.setCurrentIndex(ix)
        self._c_bs.blockSignals(False)

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

        self._refresh_gate_comp_readouts()
        self._push_snapshot(persist_disk=False)

    def _gather_live_from_ui(self) -> LiveSettings:
        liv = LiveSettings.from_dict(self._live_snapshot.to_dict())
        liv.input_device_id = str(self._c_in.currentData() or "")
        liv.output_device_id = str(self._c_out.currentData() or "")
        liv.funk_output_device_id = str(self._c_funk.currentData() or "")
        liv.funk_listen_input_device_id = str(
            self._c_funk_listen.currentData() or ""
        )
        liv.funk_listen_enabled = bool(self._chk_funk_listen.isChecked())
        liv.suppress_live_monitor_mic = not bool(self._chk_live_mithoren.isChecked())
        liv.samplerate = int(float(self._c_sr.currentText()))
        raw_bs = self._c_bs.currentData()
        liv.blocksize = int(raw_bs) if raw_bs is not None else int(liv.blocksize)
        liv.input_gain = float(self._sl_mic_v.value()) / 100.0
        liv.output_gain = float(self._sl_mon_v.value()) / 100.0
        liv.funk_output_gain = float(self._sl_funk_v.value()) / 100.0
        liv.funk_listen_gain = float(self._sl_flisten_v.value()) / 100.0

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

        liv.clamp_recursive()
        return liv

    def _pull_vol_sliders(self, *_v: object) -> None:
        self._lb_mic_pct.setText(f"{int(self._sl_mic_v.value())} %")
        self._lb_mon_pct.setText(f"{int(self._sl_mon_v.value())} %")
        self._lb_funk_pct.setText(f"{int(self._sl_funk_v.value())} %")
        self._lb_flisten_pct.setText(f"{int(self._sl_flisten_v.value())} %")
        self._push_snapshot()

    def _pull_sliders_into_snapshot(self) -> None:
        """Kompatibel für Start‑Button (löst Vol‑Labels mit aus)."""
        self._pull_vol_sliders()

    def _pull_chk_into_snapshot(self, _chk: Optional[bool] = None) -> None:
        del _chk
        self._push_snapshot()

    def _on_funk_listen_toggled_save(self, _checked: bool) -> None:
        del _checked
        self._refresh_funk_listen_controls()
        self._push_snapshot()
        if self._engine.is_running():
            self._restart_engine()

    def _on_suppress_live_monitor_toggled(self, _checked: bool) -> None:
        del _checked
        self._push_snapshot()

    def _refresh_funk_listen_controls(self) -> None:
        """Checkbox „Mithören“ steuert Live/CAT; Gerätwahl und Funk‑Eingang‑Regler immer aktiv."""
        self._c_funk_listen.setEnabled(True)
        self._sl_flisten_v.setEnabled(True)
        self._lb_flisten_pct.setEnabled(True)

    def _refresh_gate_comp_readouts(self) -> None:
        """Zeigt die aktuellen Gate-/Kompressor-Werte rechts neben den Slidern."""
        self._gate_thr_lbl.setText(f"{self._g_thr.value() / 10.0:+.1f} dB")
        self._gate_att_lbl.setText(f"{self._g_att.value()} ms")
        self._gate_hld_lbl.setText(f"{self._g_hld.value()} ms")
        self._gate_rel_lbl.setText(f"{self._g_rel.value()} ms")

        self._comp_thr_lbl.setText(f"{self._c_thr.value() / 10.0:+.1f} dB")
        ratio = self._c_rat.value() / 100.0
        self._comp_rat_lbl.setText(f"{ratio:.2f}".replace(".", ",") + "\u22361")
        self._comp_att_lbl.setText(f"{self._c_att.value()} ms")
        self._comp_rel_lbl.setText(f"{self._c_rel.value()} ms")
        self._comp_mk_lbl.setText(f"{self._c_mk.value() / 10.0:+.1f} dB")

    def _pull_slider_gate_comp(self, _value: Optional[int] = None) -> None:
        del _value
        self._refresh_gate_comp_readouts()
        self._push_snapshot()

    def _push_snapshot(self, *, persist_disk: bool = True) -> None:
        liv = self._gather_live_from_ui()
        self._live_snapshot = liv
        self._settings.live = LiveSettings.from_dict(liv.to_dict())
        if persist_disk:
            self._persist()
        self._push_live_engine_runtime_settings(liv)
        self._refresh_idle_listen_monitor(liv)

    def _on_device_changed_save(self, *_args: object) -> None:
        liv = self._gather_live_from_ui()
        self._live_snapshot = liv
        self._settings.live = liv
        self._persist()
        self._push_live_engine_runtime_settings(liv)
        if self._engine.is_running():
            self._do_stop()
            QMessageBox.information(
                self,
                "Gerät geändert",
                "Gerätewahl wurde gespeichert. Ein neuer „Start Live“ lädt sie.",
            )
        else:
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def _on_sr_bs_changed_restart(self, *_args: object) -> None:
        liv = self._gather_live_from_ui()
        self._live_snapshot = liv
        self._settings.live = liv
        self._persist()
        self._push_live_engine_runtime_settings(liv)
        if self._engine.is_running():
            self._restart_engine()
            return
        QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def _restart_engine(self) -> None:
        if not self._engine.is_running():
            return
        self._safe_live_cat_tx_off()
        self._engine.stop()
        ok, txt = self._engine.start(LiveSettings.from_dict(self._live_snapshot.to_dict()))
        if not ok:
            QMessageBox.warning(self, "Live", txt)
            self._b_stop.setEnabled(False)
            self._b_start.setEnabled(True)
            self._release_voice_plain_after_stop_live_if_not_mithoren()
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
            return
        if self._ptt_worker is not None:
            _invoke_ptt_worker_set_transmit(self._ptt_worker, True)
            self._suppress_funk_listen_while_live_tx_active = bool(
                self._chk_funk_listen.isChecked()
            )
            self._push_live_engine_runtime_settings(
                LiveSettings.from_dict(self._live_snapshot.to_dict())
            )

    def _do_start(self) -> None:
        self._pull_sliders_into_snapshot()
        liv = LiveSettings.from_dict(self._gather_live_from_ui().to_dict())
        self._live_snapshot = liv
        self._settings.live = liv
        self._persist()

        prereq_ok, err = self._engine.prerequisites_ok()
        if not prereq_ok:
            QMessageBox.warning(self, "Live", err)
            QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
            return

        if self._radio_setup is None or self._setup_worker is None:
            ok, msg = self._engine.start(LiveSettings.from_dict(liv.to_dict()))
            if not ok:
                QMessageBox.warning(
                    self,
                    "Live konnte nicht starten",
                    msg,
                )
                self._b_start.setEnabled(True)
                self._b_stop.setEnabled(False)
                QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)
                return
            self._b_start.setEnabled(False)
            self._b_stop.setEnabled(True)
            if self._ptt_worker is not None:
                _invoke_ptt_worker_set_transmit(self._ptt_worker, True)
                self._suppress_funk_listen_while_live_tx_active = bool(
                    self._chk_funk_listen.isChecked()
                )
                self._push_live_engine_runtime_settings(
                    LiveSettings.from_dict(liv.to_dict())
                )
            return

        self._cat_live_start_busy = True
        self._live_cat_waiting_engage_finish = False
        self._pending_live_after_pc_then_engage = False
        self._b_start.setEnabled(False)
        self._b_stop.setEnabled(False)
        self._begin_live_cat_radio_path()

    def _do_stop(self) -> None:
        self._live_cat_settle.stop()
        self._cat_live_start_busy = False
        self._pending_live_after_pc_then_engage = False
        self._live_cat_waiting_engage_finish = False
        self._mic_ptt_interrupted_live = False
        self._suppress_funk_listen_while_live_tx_active = False
        self._push_live_engine_runtime_settings(
            LiveSettings.from_dict(self._live_snapshot.to_dict())
        )
        if self._engine.is_running():
            self._engine.stop()
        self._safe_live_cat_tx_off()
        self._release_voice_plain_after_stop_live_if_not_mithoren()
        self._b_start.setEnabled(True)
        self._b_stop.setEnabled(False)
        QTimer.singleShot(0, self._defer_refresh_idle_listen_monitor)

    def _refresh_idle_listen_monitor(self, liv: Optional[LiveSettings] = None) -> None:
        """Funk‑Eingang → Monitor nur wenn „Mithören“ an und „Start Live“ aus."""
        if getattr(self, "_suppress_idle_listen_monitor", False):
            return

        ref = liv
        if ref is None:
            ref = LiveSettings.from_dict(self._gather_live_from_ui().to_dict())
        else:
            ref = LiveSettings.from_dict(ref.to_dict())

        if not self.isVisible():
            self._engine.stop_idle_listen_monitor()
            self._idle_monitor_fp_key = None
            return

        if self._engine.is_running():
            self._idle_monitor_fp_key = None
            return

        prereq_ok, _ = self._engine.prerequisites_ok()
        if not prereq_ok:
            self._engine.stop_idle_listen_monitor()
            self._idle_monitor_fp_key = None
            return

        if not bool(ref.funk_listen_enabled):
            self._engine.stop_idle_listen_monitor()
            self._idle_monitor_fp_key = None
            return

        listen_sid = str(ref.funk_listen_input_device_id or "").strip()
        mon_sid = str(ref.output_device_id or "").strip()
        if not listen_sid or not mon_sid:
            self._engine.stop_idle_listen_monitor()
            self._idle_monitor_fp_key = None
            return

        fp_key: tuple[object, ...] = (
            listen_sid,
            mon_sid,
            int(ref.samplerate),
            int(ref.blocksize),
        )
        if fp_key == self._idle_monitor_fp_key and self._engine.is_idle_listen_monitor_running():
            self._engine.push_idle_listen_settings(ref)
            return

        self._idle_monitor_fp_key = fp_key
        ok, _msg = self._engine.start_idle_listen_monitor(ref)
        if not ok:
            self._idle_monitor_fp_key = None
            self._engine.stop_idle_listen_monitor()

    def _defer_refresh_idle_listen_monitor(self) -> None:
        if not self.isVisible():
            return
        self._refresh_idle_listen_monitor()

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
        self._suppress_funk_listen_while_live_tx_active = False
        self._suppress_idle_listen_monitor = True
        self._engine.stop_idle_listen_monitor()
        self._idle_monitor_fp_key = None
        self._save_geo_to_settings()
        self._live_cat_settle.stop()

        if getattr(self, "_force_close", False):
            self._shutdown_ptt_thread()
            super().closeEvent(event)
            return

        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)
            if self._engine.is_running():
                self._do_stop()
            else:
                self._safe_live_cat_tx_off()
            self._audio_radio_session.request_restore_if_no_windows()
        else:
            if self._engine.is_running():
                self._engine.stop()
                self._safe_live_cat_tx_off()

        # Live-Fenster endgültig — DATA/Sprache wie vor Live wiederherstellen,
        # auch wenn „Mithören“ beim Stop die Umschaltung unterdrückt hat.
        self._release_live_voice_mode_plain()
        self._shutdown_ptt_thread()
        super().closeEvent(event)

    def _save_geo_to_settings(self) -> None:
        geo = self.saveGeometry()
        if not geo.isEmpty():
            b64 = base64.b64encode(geo.data()).decode("ascii")
            self._settings.live.window_geometry = b64

    def force_close(self) -> None:
        """App-Ende oder erzwungenes Schließen: Stream stoppen, Funk‑Restore wie andere Audio‑Fenster."""
        self._suppress_funk_listen_while_live_tx_active = False
        self._suppress_idle_listen_monitor = True
        self._live_cat_settle.stop()
        self._cat_live_start_busy = False
        self._pending_live_after_pc_then_engage = False
        self._live_cat_waiting_engage_finish = False
        self._mic_ptt_interrupted_live = False
        if self._engine.is_running():
            self._engine.stop()
        self._safe_live_cat_tx_off()
        self._release_live_voice_mode_plain()
        if self._audio_radio_session is not None:
            self._audio_radio_session.detach_for_force_close(self)
        self._shutdown_ptt_thread()

