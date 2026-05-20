"""Fenster für MP3-Audio-Recorder (Aufnahme + Replay über CAT-PTT)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    QByteArray,
    QMetaObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Q_ARG,
)
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audio.audio_recorder import (
    AudioRecorder,
    RecorderState,
    list_audio_input_devices,
)
from audio.audio_settings_hub import AudioSettingsHub
from audio.player_controller import (
    PlayerController,
    PlayerState,
    build_playlist_entries,
    list_audio_output_devices,
    multimedia_available,
)
from audio.qt_multimedia_lazy import qt_multimedia_types, recorder_import_ok
from audio.radio_playback_setup import (
    RadioPlaybackSetup,
    RadioSetupWorker,
    data_mode_for_rx_mode,
    data_mode_from_string,
)
from cat import SerialCAT
from cat.ft991_cat import FT991CAT
from mapping import TX_STATE_MIC_PTT, TX_STATE_RX
from mapping.rx_mapping import RxMode
from model import AppSettings
from model.global_audio_settings import ROLE_INPUT, ROLE_PC, ROLE_SEND
from model.audio_recorder_settings import (
    ALLOWED_BITRATES_KBPS,
    DEFAULT_BITRATE_KBPS,
    default_recordings_folder,
    scan_recordings,
)

from .app_icon import app_icon
from .audio_hub_binding import (
    connect_level_meters,
    connect_recorder_hub,
    load_global_audio_into_combos,
)
from .file_list_widget_style import FILE_LIST_WIDGET_STYLESHEET
from .folder_dialog import pick_audio_recorder_folder
from .menu_icons import (
    control_bar_record_red_icon,
    set_transport_button_icon,
    transport_play_icon,
    transport_replay_icon,
    transport_stop_icon,
    transport_trash_icon,
    transport_button_icon_size,
    volume_role_pc_icon,
    volume_role_record_icon,
    volume_role_send_icon,
)
from .volume_control_row import VolumeControlRow
from .window_lifecycle import application_exit_close_requested

if TYPE_CHECKING:
    from .audio_radio_session import AudioRadioSessionHost


def _format_ms(ms: int) -> str:
    ms = max(0, int(ms))
    s = ms // 1000
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


def _big_font(base: QFont) -> QFont:
    f = QFont(base)
    f.setPointSizeF(f.pointSizeF() * 1.8)
    f.setBold(True)
    return f


def _double_font(base: QFont) -> QFont:
    f = QFont(base)
    f.setPointSizeF(f.pointSizeF() * 2)
    return f


_REMAINING_WARN_MS = 10_000
_REMAINING_STYLE_NORMAL = ""
_REMAINING_STYLE_WARN = "color: #ff4444; font-weight: bold;"


_LED_OFF_STYLE = (
    "QLabel { background-color: #4a0000; border-radius: 11px; "
    "border: 1px solid #2a0000; }"
)
_LED_ON_STYLE = (
    "QLabel { background-color: #ff2020; border-radius: 11px; "
    "border: 1px solid #800000; }"
)

#: Replay-Button leuchtet grün, solange CAT-TX-Wiedergabe läuft — gleicher
#: Farbton wie das VFO-A-Caption / der REV-Button im aktiven Zustand.
_REPLAY_STYLE_ACTIVE = (
    "QPushButton { background-color: #5ddc7a; color: #000000; "
    "font-weight: bold; }"
)
_REPLAY_STYLE_IDLE = ""


class AudioRecorderWindow(QMainWindow):
    """Aufnahme von MP3-Mitschnitten + Replay über CAT-TX (DATA-Mode)."""

    closed = Signal()

    def __init__(
        self,
        settings: AppSettings,
        serial_cat: SerialCAT,
        *,
        audio_radio_session: Optional[AudioRadioSessionHost] = None,
        operating_mode_provider: Optional[Callable[[], RxMode]] = None,
        audio_hub: Optional[AudioSettingsHub] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._audio_hub = audio_hub
        self._cat = serial_cat
        self._audio_radio_session = audio_radio_session
        self._operating_mode_provider = operating_mode_provider

        folder_str = settings.audio_recorder.folder_path
        if folder_str:
            self._folder = Path(folder_str)
        else:
            self._folder = default_recordings_folder()
            self._settings.audio_recorder.folder_path = str(self._folder)

        self.setWindowTitle("FT-991/A Audio-Recorder")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.resize(560, 640)

        # Aufnahme-Komponente
        self._recorder = AudioRecorder(self)
        self._recorder.state_changed.connect(self._on_recorder_state)
        self._recorder.duration_changed.connect(self._on_record_duration)
        self._recorder.error.connect(self._on_recorder_error)
        self._recorder.file_finalized.connect(self._on_file_finalized)

        # Wiedergabe-Komponente (Replay über CAT-TX im DATA-Mode)
        self._player = PlayerController(self._cat, self)
        self._player.state_changed.connect(self._on_player_state)
        self._player.position_changed.connect(self._on_player_position)
        self._player.current_file_changed.connect(self._on_current_file)
        self._player.error.connect(self._on_player_error)
        self._player.status_message.connect(self._on_status)
        self._player.voice_mode_requested.connect(self._on_voice_mode_requested)
        # Recorder ist single-shot, kein Kontest-Loop.
        self._player.set_playback_mode("single")
        self._player.set_contest_mode(False, 0)

        # ------------------------------------------------------------------
        # PC-Player (lokale Vorhöre, KEIN CAT, KEINE PTT)
        # ------------------------------------------------------------------
        # Lazy-Init: QtMultimedia darf erst nach QApplication-Erzeugung
        # geladen werden, sonst zerschießt es den Backend-Auto-Detect.
        # Wir initialisieren beim ersten Play-Klick.
        self._pc_player = None         # type: ignore[var-annotated]  # QMediaPlayer
        self._pc_audio_out = None      # type: ignore[var-annotated]  # QAudioOutput
        self._pc_player_ready = False
        self._pc_is_playing = False

        # CAT-Setup (EX048/109=USB, EX077=USB, EX070=REAR, EX072=USB), Mode wird mit dem Player geteilt.
        if operating_mode_provider is not None:
            initial_data_mode = data_mode_for_rx_mode(operating_mode_provider())
        else:
            initial_data_mode = data_mode_from_string(settings.audio_player.data_mode)
        if audio_radio_session is not None:
            self._radio_setup = audio_radio_session.setup
            self._setup_thread = audio_radio_session.thread
            self._setup_worker = audio_radio_session.worker
            self._owns_radio_thread = False
        else:
            self._radio_setup = RadioPlaybackSetup(self._cat, initial_data_mode)
            self._setup_thread = QThread(self)
            self._setup_worker = RadioSetupWorker(self._radio_setup)
            self._setup_worker.moveToThread(self._setup_thread)
            self._owns_radio_thread = True
        self._setup_worker.apply_finished.connect(self._on_radio_apply_finished)
        self._setup_worker.restore_finished.connect(self._on_radio_restore_finished)
        self._setup_worker.engage_plain_finished.connect(
            self._on_radio_engage_plain_finished
        )
        self._setup_worker.engage_data_finished.connect(
            self._on_radio_engage_data_finished
        )
        self._setup_worker.pc_menus_finished.connect(self._on_pc_menus_finished)
        if self._owns_radio_thread:
            self._setup_thread.start()

        #: Was tun, wenn das nächste ``apply`` fertig ist?
        #: ``"record"`` = Aufnahme starten, ``"replay"`` = Replay starten,
        #: ``""``       = nichts (Status anzeigen).
        self._pending_after_apply: str = ""
        #: True, wenn MIC PTT die Aufnahme/Replay unterbrochen hat.
        self._mic_ptt_interrupted = False

        # LED-Blink
        self._led_on = False
        self._led_timer = QTimer(self)
        self._led_timer.setInterval(500)
        self._led_timer.timeout.connect(self._on_led_blink)

        # Dauer-Tick für laufende Aufnahme (Backup zum Recorder-Signal).
        self._record_started_ms = 0

        self._files: list[str] = []
        self._duration_ms = 0
        self._seek_dragging = False
        self._remaining_warn_active = False
        self._remaining_blink_on = True
        self._last_player_state: Optional[PlayerState] = None
        self._pending_radio_restore_on_close = False
        self._force_close_after_radio_restore = False

        self._build_ui()
        if self._audio_hub is not None:
            connect_recorder_hub(
                hub=self._audio_hub,
                combo_input=self.combo_input,
                combo_send=self.combo_output,
                combo_pc=self.combo_pc_output,
                vol_input=self._vol_input,
                vol_send=self._vol_send,
                vol_pc=self._vol_pc,
                check_tx_monitor=self.check_tx_monitor_pc,
                on_input_device=self._apply_input_device,
                on_send_device=self._apply_send_device,
                on_pc_device=self._apply_pc_output_device,
                on_input_volume=self._apply_input_volume,
                on_send_volume=self._apply_send_volume,
                on_pc_volume=self._apply_pc_volume,
                on_input_mute=self._apply_input_mute,
                on_send_mute=self._apply_send_mute,
                on_pc_mute=self._apply_pc_mute,
                on_tx_monitor=self._apply_tx_monitor,
            )
            connect_level_meters(
                self._audio_hub,
                {
                    ROLE_INPUT: self._vol_input,
                    ROLE_SEND: self._vol_send,
                    ROLE_PC: self._vol_pc,
                },
            )
        self._load_settings_to_ui()
        self._refresh_file_list()
        self._restore_geometry()
        self._update_buttons()

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ---- Ordner-Zeile ----
        folder_row = QHBoxLayout()
        self.btn_folder = QPushButton("Ordner wählen …")
        self.btn_folder.clicked.connect(self._on_pick_folder)
        self.btn_open_folder = QPushButton("Im Explorer öffnen")
        self.btn_open_folder.setToolTip("Aufnahme-Ordner im Datei-Explorer öffnen")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        self.btn_refresh = QPushButton("Aktualisieren")
        self.btn_refresh.clicked.connect(self._refresh_file_list)
        folder_row.addWidget(self.btn_folder)
        folder_row.addWidget(self.btn_open_folder)
        folder_row.addWidget(self.btn_refresh)
        folder_row.addStretch(1)
        root.addLayout(folder_row)

        self.lbl_folder = QLabel("")
        self.lbl_folder.setWordWrap(True)
        self.lbl_folder.setStyleSheet("color: gray;")
        root.addWidget(self.lbl_folder)

        # ---- Datei-Liste ----
        list_box = QGroupBox("Aufnahmen (neueste oben)")
        list_l = QVBoxLayout(list_box)
        self.list_files = QListWidget()
        self.list_files.setStyleSheet(FILE_LIST_WIDGET_STYLESHEET)
        self.list_files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Doppelt so hoch wie die Qt-Default-QListWidget-Höhe (~200 px), damit
        # man mehr Aufnahmen ohne Scrollen sieht.
        self.list_files.setMinimumHeight(200)
        self.list_files.currentRowChanged.connect(self._on_list_row_changed)
        self.list_files.itemDoubleClicked.connect(self._on_item_double_clicked)
        list_l.addWidget(self.list_files)

        # Buttons direkt unter der Liste: PC-Vorhör, dann Löschen (kein TX).
        list_btn_row = QHBoxLayout()
        self.btn_play_pc = QPushButton("Play PC")
        self.btn_play_pc.setToolTip(
            "Markierte Datei lokal über das PC-Ausgabegerät abspielen — "
            "kein CAT, keine PTT, keine Sendung."
        )
        self.btn_play_pc.clicked.connect(self._on_play_pc_clicked)
        set_transport_button_icon(self.btn_play_pc, transport_play_icon())
        list_btn_row.addWidget(self.btn_play_pc)

        self.btn_stop_pc = QPushButton("Stop")
        self.btn_stop_pc.setToolTip("Lokale PC-Wiedergabe stoppen.")
        self.btn_stop_pc.clicked.connect(self._on_stop_pc_clicked)
        set_transport_button_icon(self.btn_stop_pc, transport_stop_icon())
        list_btn_row.addWidget(self.btn_stop_pc)

        self.btn_delete = QPushButton("Datei löschen")
        self.btn_delete.setToolTip(
            "Markierte Datei nach Bestätigung dauerhaft löschen. "
            "Während Aufnahme oder laufender Wiedergabe gesperrt."
        )
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        set_transport_button_icon(self.btn_delete, transport_trash_icon())
        list_btn_row.addWidget(self.btn_delete)

        list_btn_row.addStretch(1)
        list_l.addLayout(list_btn_row)

        root.addWidget(list_box, stretch=1)

        # ---- Aufnahme-Box ----
        rec_box = QGroupBox("Aufnahme")
        rec_l = QVBoxLayout(rec_box)

        rec_row = QHBoxLayout()
        self.led = QLabel()
        self.led.setFixedSize(22, 22)
        self.led.setStyleSheet(_LED_OFF_STYLE)
        self.led.setToolTip("Aufnahme-LED (rot blinkend = REC)")
        rec_row.addWidget(self.led)

        self.btn_record = QPushButton("Aufnahme")
        self.btn_record.setMinimumWidth(120)
        self.btn_record.clicked.connect(self._on_record_clicked)
        self.btn_record.setIcon(control_bar_record_red_icon())
        self.btn_record.setIconSize(transport_button_icon_size())
        rec_row.addWidget(self.btn_record)

        self.btn_stop_rec = QPushButton("Stopp")
        self.btn_stop_rec.setMinimumWidth(96)
        self.btn_stop_rec.clicked.connect(self._on_stop_recording)
        set_transport_button_icon(self.btn_stop_rec, transport_stop_icon())
        rec_row.addWidget(self.btn_stop_rec)

        self.lbl_rec_duration = QLabel("0:00")
        self.lbl_rec_duration.setFont(_big_font(self.lbl_rec_duration.font()))
        self.lbl_rec_duration.setMinimumWidth(80)
        self.lbl_rec_duration.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        rec_row.addWidget(self.lbl_rec_duration, stretch=1)
        rec_l.addLayout(rec_row)

        self.lbl_rec_file = QLabel("(keine Aufnahme aktiv)")
        self.lbl_rec_file.setStyleSheet("color: gray;")
        self.lbl_rec_file.setWordWrap(True)
        rec_l.addWidget(self.lbl_rec_file)

        root.addWidget(rec_box)

        # ---- Replay-Box (sendet über CAT-TX im DATA-Mode) ----
        rep_box = QGroupBox("Replay (sendet über CAT-TX im DATA-Mode)")
        rep_l = QVBoxLayout(rep_box)

        rep_transport = QHBoxLayout()
        self.btn_replay = QPushButton("Replay")
        self.btn_replay.setToolTip(
            "Markierte Aufnahme einmal abspielen (Pre-Roll → CAT-TX → "
            "Datei → zurück auf Sprach-Mode)."
        )
        self.btn_replay.clicked.connect(self._on_replay_clicked)
        set_transport_button_icon(self.btn_replay, transport_replay_icon())
        rep_transport.addWidget(self.btn_replay)

        self.btn_stop_replay = QPushButton("Stopp Replay")
        self.btn_stop_replay.clicked.connect(self._on_stop_replay)
        set_transport_button_icon(self.btn_stop_replay, transport_stop_icon())
        rep_transport.addWidget(self.btn_stop_replay)
        rep_transport.addStretch(1)
        rep_l.addLayout(rep_transport)

        self.progress_replay = QSlider(Qt.Orientation.Horizontal)
        self.progress_replay.setRange(0, 1000)
        self.progress_replay.setValue(0)
        self.progress_replay.setPageStep(50)
        self.progress_replay.setToolTip("Replay-Position — ziehen zum Spulen")
        self.progress_replay.setTracking(True)
        self.progress_replay.sliderPressed.connect(self._on_replay_seek_pressed)
        self.progress_replay.sliderReleased.connect(self._on_replay_seek_released)
        self.progress_replay.sliderMoved.connect(self._on_replay_seek_slider_change)
        self.progress_replay.valueChanged.connect(self._on_replay_seek_slider_change)
        rep_l.addWidget(self.progress_replay)

        self._remaining_blink_timer = QTimer(self)
        self._remaining_blink_timer.setInterval(500)
        self._remaining_blink_timer.timeout.connect(self._on_remaining_blink_tick)

        rep_time_row = QHBoxLayout()
        self.lbl_replay_elapsed = QLabel("0:00")
        self.lbl_replay_remaining = QLabel("-0:00")
        replay_time_font = _double_font(self.lbl_replay_elapsed.font())
        self.lbl_replay_elapsed.setFont(replay_time_font)
        self.lbl_replay_remaining.setFont(replay_time_font)
        rep_time_row.addWidget(self.lbl_replay_elapsed)
        rep_time_row.addStretch(1)
        rep_time_row.addWidget(self.lbl_replay_remaining)
        rep_l.addLayout(rep_time_row)

        root.addWidget(rep_box)

        # ---- Geräte / Format ----
        dev_box = QGroupBox("Geräte & Format")
        dev_l = QVBoxLayout(dev_box)

        # Einheitliche Label-Breite, damit alle Combos/Slider untereinander
        # bündig auf derselben x-Position beginnen. Wert deckt das längste
        # Label ("Wiedergabe-Lautstärke:") komfortabel ab.
        _LABEL_W = 170

        def _form_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setMinimumWidth(_LABEL_W)
            return lbl

        in_row = QHBoxLayout()
        in_row.addWidget(_form_label("Aufnahme-Gerät:"))
        self.combo_input = QComboBox()
        self._fill_input_devices()
        self.combo_input.currentIndexChanged.connect(self._on_input_changed)
        in_row.addWidget(self.combo_input, 1)
        dev_l.addLayout(in_row)

        in_vol_row = QHBoxLayout()
        in_vol_row.addWidget(_form_label("Aufnahme-Lautstärke:"))
        self._vol_input = VolumeControlRow(
            tooltip="Windows-Lautstärke des Aufnahme-Geräts",
            leading_icon=volume_role_record_icon(),
        )
        self._vol_input.value_changed.connect(self._on_input_volume_changed)
        self._vol_input.mute_toggled.connect(self._on_input_mute_toggled)
        in_vol_row.addWidget(self._vol_input, 1)
        dev_l.addLayout(in_vol_row)

        out_row = QHBoxLayout()
        out_row.addWidget(_form_label("Wiedergabe-Gerät:"))
        self.combo_output = QComboBox()
        self._fill_output_devices()
        self.combo_output.currentIndexChanged.connect(self._on_output_changed)
        out_row.addWidget(self.combo_output, 1)
        dev_l.addLayout(out_row)

        out_vol_row = QHBoxLayout()
        out_vol_row.addWidget(_form_label("Wiedergabe-Lautstärke:"))
        self._vol_send = VolumeControlRow(
            tooltip="Windows-Lautstärke der Sende-Soundkarte (CAT-Replay)",
            leading_icon=volume_role_send_icon(),
        )
        self._vol_send.value_changed.connect(self._on_output_volume_changed)
        self._vol_send.mute_toggled.connect(self._on_send_mute_toggled)
        out_vol_row.addWidget(self._vol_send, 1)
        dev_l.addLayout(out_vol_row)

        pc_row = QHBoxLayout()
        pc_row.addWidget(_form_label("PC-Ausgabegerät:"))
        self.combo_pc_output = QComboBox()
        self.combo_pc_output.setToolTip(
            "Soundkarte für die lokale Vorhöre (Play PC) — kein TX, kein CAT."
        )
        self._fill_pc_output_devices()
        self.combo_pc_output.currentIndexChanged.connect(self._on_pc_output_changed)
        pc_row.addWidget(self.combo_pc_output, 1)
        dev_l.addLayout(pc_row)

        pc_vol_row = QHBoxLayout()
        pc_vol_row.addWidget(_form_label("PC-Lautstärke:"))
        self._vol_pc = VolumeControlRow(
            tooltip="Windows-Lautstärke der PC-Ausgabe (Play PC)",
            leading_icon=volume_role_pc_icon(),
        )
        self._vol_pc.value_changed.connect(self._on_pc_volume_changed)
        self._vol_pc.mute_toggled.connect(self._on_pc_mute_toggled)
        pc_vol_row.addWidget(self._vol_pc, 1)
        dev_l.addLayout(pc_vol_row)

        self.check_tx_monitor_pc = QCheckBox("Ausgabe Mithören")
        self.check_tx_monitor_pc.setToolTip(
            "Während des CAT-Replays dieselbe Tonspur wie auf dem Wiedergabe-Gerät "
            "zusätzlich auf dem PC-Ausgabegerät ausgeben."
        )
        self.check_tx_monitor_pc.toggled.connect(self._on_tx_monitor_pc_toggled)
        dev_l.addWidget(self.check_tx_monitor_pc)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(_form_label("MP3-Bitrate:"))
        self.combo_bitrate = QComboBox()
        for kbps in ALLOWED_BITRATES_KBPS:
            self.combo_bitrate.addItem(f"{kbps} kbps", kbps)
        self.combo_bitrate.currentIndexChanged.connect(self._on_bitrate_changed)
        fmt_row.addWidget(self.combo_bitrate)
        fmt_row.addStretch(1)
        dev_l.addLayout(fmt_row)

        root.addWidget(dev_box)

        self.lbl_status = QLabel("Bereit")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        if not recorder_import_ok():
            self.lbl_status.setText(
                "Qt Multimedia-Recorder nicht verfügbar — "
                "pip install PySide6-Addons, App neu starten."
            )

        self.setCentralWidget(central)

    def _fill_input_devices(self) -> None:
        self.combo_input.blockSignals(True)
        try:
            self.combo_input.clear()
            saved = (
                self._audio_hub.device_id(ROLE_INPUT)
                if self._audio_hub
                else self._settings.audio_recorder.input_device_id
            )
            select_idx = 0
            for i, (dev_id, label) in enumerate(list_audio_input_devices()):
                self.combo_input.addItem(label, dev_id)
                if dev_id == saved:
                    select_idx = i
            self.combo_input.setCurrentIndex(select_idx)
        finally:
            self.combo_input.blockSignals(False)

    def _fill_output_devices(self) -> None:
        self.combo_output.blockSignals(True)
        try:
            self.combo_output.clear()
            saved = (
                self._audio_hub.device_id(ROLE_SEND)
                if self._audio_hub
                else self._settings.audio_recorder.output_device_id
            )
            select_idx = 0
            for i, (dev_id, label) in enumerate(list_audio_output_devices()):
                self.combo_output.addItem(label, dev_id)
                if dev_id == saved:
                    select_idx = i
            self.combo_output.setCurrentIndex(select_idx)
        finally:
            self.combo_output.blockSignals(False)

    def _fill_pc_output_devices(self) -> None:
        self.combo_pc_output.blockSignals(True)
        try:
            self.combo_pc_output.clear()
            saved = (
                self._audio_hub.device_id(ROLE_PC)
                if self._audio_hub
                else self._settings.audio_recorder.pc_output_device_id
            )
            select_idx = 0
            for i, (dev_id, label) in enumerate(list_audio_output_devices()):
                self.combo_pc_output.addItem(label, dev_id)
                if dev_id == saved:
                    select_idx = i
            self.combo_pc_output.setCurrentIndex(select_idx)
        finally:
            self.combo_pc_output.blockSignals(False)

    def _load_settings_to_ui(self) -> None:
        ar = self._settings.audio_recorder
        # Bitrate
        bitrate = ar.mp3_bitrate_kbps
        idx = self.combo_bitrate.findData(bitrate)
        if idx < 0:
            idx = self.combo_bitrate.findData(DEFAULT_BITRATE_KBPS)
        if idx >= 0:
            self.combo_bitrate.blockSignals(True)
            self.combo_bitrate.setCurrentIndex(idx)
            self.combo_bitrate.blockSignals(False)
        if self._audio_hub is not None:
            load_global_audio_into_combos(
                self._audio_hub,
                combo_input=self.combo_input,
                combo_send=self.combo_output,
                combo_pc=self.combo_pc_output,
                vol_input=self._vol_input,
                vol_send=self._vol_send,
                vol_pc=self._vol_pc,
                check_tx_monitor=self.check_tx_monitor_pc,
            )
            self._apply_input_device(self._audio_hub.device_id(ROLE_INPUT))
            self._apply_input_volume(self._audio_hub.volume_percent(ROLE_INPUT))
            self._apply_send_device(self._audio_hub.device_id(ROLE_SEND))
            self._apply_send_volume(self._audio_hub.volume_percent(ROLE_SEND))
            self._apply_pc_output_device(self._audio_hub.device_id(ROLE_PC))
            self._apply_pc_volume(self._audio_hub.volume_percent(ROLE_PC))
            self._apply_tx_monitor(self._audio_hub.tx_monitor_to_pc_enabled())
        else:
            saved_out = self.combo_output.currentData()
            if not isinstance(saved_out, str):
                saved_out = ""
            self._apply_send_device(saved_out)
            self._vol_input.set_value(ar.input_volume_percent)
            self._apply_input_volume(ar.input_volume_percent)
            self._vol_send.set_value(ar.output_volume_percent)
            self._apply_send_volume(ar.output_volume_percent)
            saved_pc = self.combo_pc_output.currentData()
            if not isinstance(saved_pc, str):
                saved_pc = ""
            self._pc_pending_device_id = saved_pc
            self._vol_pc.set_value(ar.pc_output_volume_percent)
            self._apply_pc_volume(ar.pc_output_volume_percent)
            self.check_tx_monitor_pc.blockSignals(True)
            try:
                self.check_tx_monitor_pc.setChecked(
                    bool(ar.tx_monitor_to_pc_enabled)
                )
            finally:
                self.check_tx_monitor_pc.blockSignals(False)
            self._apply_tx_monitor_pc_to_player()
        # Soft-Kompressor nach Aufnahme: fest eingeschaltet (keine UI-Option).
        self._settings.audio_recorder.normalize_enabled = True
        self._recorder.set_normalize_enabled(True)

    def _restore_geometry(self) -> None:
        geo = self._settings.audio_recorder.window_geometry
        if not geo:
            return
        try:
            ba = QByteArray(base64.b64decode(geo.encode("ascii")))
            self.restoreGeometry(ba)
        except Exception:
            pass

    def _save_geometry(self) -> None:
        self._settings.audio_recorder.window_geometry = base64.b64encode(
            self.saveGeometry().data()
        ).decode("ascii")

    # ------------------------------------------------------------------
    # Ordner / Datei-Liste
    # ------------------------------------------------------------------

    def _on_pick_folder(self) -> None:
        start = str(self._folder) if self._folder.is_dir() else str(Path.home())
        path = pick_audio_recorder_folder(self, start)
        if not path:
            return
        self._folder = Path(path)
        self._settings.audio_recorder.folder_path = path
        self._refresh_file_list()

    def _on_open_folder(self) -> None:
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                f"Ordner konnte nicht angelegt werden: {exc}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder.resolve())))

    def _refresh_file_list(self) -> None:
        if self._folder.is_dir():
            self._files = scan_recordings(self._folder)
            self.lbl_folder.setText(str(self._folder))
        else:
            self._files = []
            self.lbl_folder.setText(
                f"(Ordner existiert noch nicht — wird bei Aufnahme angelegt: {self._folder})"
            )
        self._rebuild_list_widget()
        self._push_playlist_to_player()
        self._update_buttons()

    def _rebuild_list_widget(self) -> None:
        self.list_files.blockSignals(True)
        try:
            self.list_files.clear()
            for name in self._files:
                self.list_files.addItem(QListWidgetItem(name))
            # Vorher markierte Datei (falls noch da) erneut markieren.
            saved = self._settings.audio_recorder.selected_filename
            if saved:
                for i, name in enumerate(self._files):
                    if name == saved:
                        self.list_files.setCurrentRow(i)
                        break
        finally:
            self.list_files.blockSignals(False)

    def _push_playlist_to_player(self) -> None:
        if not self._folder.is_dir():
            self._player.set_playlist([])
            return
        self._player.set_playlist(
            build_playlist_entries(self._folder, self._files)
        )

    def _on_list_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._files):
            self._settings.audio_recorder.selected_filename = ""
            return
        self._settings.audio_recorder.selected_filename = self._files[row]
        if not self._player.is_busy():
            self._player.load_track(row)
        self._update_buttons()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.list_files.row(item)
        if row < 0:
            return
        self.list_files.setCurrentRow(row)
        self._start_replay()

    # ------------------------------------------------------------------
    # Aufnahme
    # ------------------------------------------------------------------

    def _on_pc_menus_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message and self._audio_radio_session is None:
            QMessageBox.warning(self, "Audio-Recorder", message)

    def _on_record_clicked(self) -> None:
        if self._recorder.is_busy():
            return
        if self._player.is_busy():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Replay läuft — bitte zuerst stoppen.",
            )
            return
        if not recorder_import_ok():
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                "Qt Multimedia-Recorder ist nicht verfügbar.",
            )
            return
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                "CAT nicht verbunden — bitte zuerst verbinden.",
            )
            return

        self._mic_ptt_interrupted = False
        self._pending_after_apply = "record"
        self._apply_or_engage_data()

    def _on_stop_recording(self) -> None:
        if not self._recorder.is_busy():
            return
        self._recorder.stop()

    def _start_recording_now(self) -> None:
        device_id = self.combo_input.currentData()
        if not isinstance(device_id, str):
            device_id = ""
        bitrate = self.combo_bitrate.currentData()
        if not isinstance(bitrate, int):
            bitrate = DEFAULT_BITRATE_KBPS
        target = self._recorder.start(
            folder=self._folder,
            device_id=device_id,
            bitrate_kbps=int(bitrate),
        )
        if target is None:
            # Fehler ist bereits per error-Signal/Box gemeldet → Setup zurück.
            self._request_radio_restore()
            return
        self.lbl_rec_file.setText(target.name)
        self.lbl_status.setText(f"Aufnahme läuft: {target.name}")

    def _on_recorder_state(self, state: RecorderState) -> None:
        if state == RecorderState.RECORDING:
            self._start_led_blink()
        else:
            self._stop_led_blink()
        if state == RecorderState.IDLE:
            self.lbl_rec_duration.setText(_format_ms(0))
        elif state == RecorderState.POST_PROCESSING:
            # WAV ist auf der Platte — wir normalisieren und encoden gerade.
            # Bei einer halben Minute Aufnahme typisch < 200 ms, bei
            # mehreren Minuten kann das kurz sichtbar werden.
            self.lbl_status.setText(
                "Aufnahme wird normalisiert und nach MP3 encodiert …"
            )
        self._update_buttons()
        self._try_complete_radio_restore_after_close()

    def _on_record_duration(self, ms: int) -> None:
        self.lbl_rec_duration.setText(_format_ms(ms))

    def _on_recorder_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Audio-Recorder", msg)
        self._stop_led_blink()
        self.lbl_status.setText(f"Aufnahme-Fehler: {msg}")
        self._update_buttons()
        # Falls die Aufnahme nie startete, aber DATA-Mode schon aktiv ist,
        # auf Sprach-Mode zurück, damit kein TX hängenbleibt.
        if self._radio_setup.is_applied and self._radio_setup.in_data_mode:
            self._request_engage_plain()

    def _on_file_finalized(self, path) -> None:
        try:
            saved = Path(path)
        except Exception:
            saved = None
        if saved is not None:
            self.lbl_status.setText(f"Aufnahme gespeichert: {saved.name}")
            # Datei in Settings vormerken, damit sie nach Refresh markiert wird.
            self._settings.audio_recorder.selected_filename = saved.name
        self.lbl_rec_file.setText("(keine Aufnahme aktiv)")
        self._refresh_file_list()
        # Nach jeder Aufnahme zurück auf Sprach-Mode.
        if self._radio_setup.is_applied:
            self._request_engage_plain()

    # ------------------------------------------------------------------
    # LED-Blink
    # ------------------------------------------------------------------

    def _start_led_blink(self) -> None:
        if self._led_timer.isActive():
            return
        self._led_on = True
        self.led.setStyleSheet(_LED_ON_STYLE)
        self._led_timer.start()

    def _stop_led_blink(self) -> None:
        self._led_timer.stop()
        self._led_on = False
        self.led.setStyleSheet(_LED_OFF_STYLE)

    def _on_led_blink(self) -> None:
        self._led_on = not self._led_on
        self.led.setStyleSheet(_LED_ON_STYLE if self._led_on else _LED_OFF_STYLE)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def _on_replay_clicked(self) -> None:
        self._start_replay()

    def _start_replay(self) -> None:
        if self._recorder.is_busy():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Aufnahme läuft — bitte zuerst stoppen.",
            )
            return
        if self._player.is_busy():
            return
        row = self.list_files.currentRow()
        if row < 0 or row >= len(self._files):
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Bitte eine Aufnahme in der Liste markieren.",
            )
            return
        if not multimedia_available():
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                "Audio-Wiedergabe nicht verfügbar.",
            )
            return
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                "CAT nicht verbunden — bitte zuerst verbinden.",
            )
            return
        self._mic_ptt_interrupted = False
        self.sync_data_mode_from_main()
        self._player.set_playback_mode("single")
        self._pending_after_apply = "replay"
        self._pending_replay_row = row
        self._apply_or_engage_data()

    def _on_stop_replay(self) -> None:
        if not self._player.is_busy():
            return
        self._player.stop()

    def _on_player_state(self, state: PlayerState) -> None:
        self._last_player_state = state
        self._update_buttons()
        self._try_complete_radio_restore_after_close()

    def _on_player_position(self, pos_ms: int, dur_ms: int) -> None:
        if self._is_pc_playing():
            return
        self._update_replay_progress_ui(pos_ms, dur_ms)

    def _slider_replay_position_ms(self) -> int:
        if self._duration_ms <= 0:
            return 0
        return int(self._duration_ms * self.progress_replay.value() / 1000)

    def _update_replay_progress_ui(self, pos_ms: int, dur_ms: int) -> None:
        self._duration_ms = max(0, dur_ms)
        if not self._seek_dragging:
            if dur_ms > 0:
                self.progress_replay.blockSignals(True)
                try:
                    self.progress_replay.setValue(int(1000 * pos_ms / dur_ms))
                finally:
                    self.progress_replay.blockSignals(False)
            else:
                self.progress_replay.setValue(0)
            self.lbl_replay_elapsed.setText(_format_ms(pos_ms))
            rem = max(0, dur_ms - pos_ms)
            self.lbl_replay_remaining.setText(f"-{_format_ms(rem)}")
            self._update_remaining_warn(rem)
        self._update_buttons()

    def _update_remaining_warn(self, rem_ms: int) -> None:
        playing = self._player.state == PlayerState.PLAYING
        self._set_remaining_warn(playing and rem_ms < _REMAINING_WARN_MS)

    def _set_remaining_warn(self, active: bool) -> None:
        if active == self._remaining_warn_active:
            if active:
                self.lbl_replay_remaining.setStyleSheet(
                    _REMAINING_STYLE_WARN
                    if self._remaining_blink_on
                    else _REMAINING_STYLE_NORMAL
                )
            return
        self._remaining_warn_active = active
        if active:
            self._remaining_blink_on = True
            self.lbl_replay_remaining.setStyleSheet(_REMAINING_STYLE_WARN)
            self._remaining_blink_timer.start()
        else:
            self._remaining_blink_timer.stop()
            self.lbl_replay_remaining.setStyleSheet(_REMAINING_STYLE_NORMAL)
            self.lbl_replay_remaining.setVisible(True)

    def _on_remaining_blink_tick(self) -> None:
        if not self._remaining_warn_active:
            return
        self._remaining_blink_on = not self._remaining_blink_on
        self.lbl_replay_remaining.setStyleSheet(
            _REMAINING_STYLE_WARN if self._remaining_blink_on else _REMAINING_STYLE_NORMAL
        )

    def _on_replay_seek_pressed(self) -> None:
        self._seek_dragging = True

    def _on_replay_seek_released(self) -> None:
        self._seek_dragging = False
        self._apply_replay_seek_from_slider()

    def _on_replay_seek_slider_change(self, value: int) -> None:
        if self.progress_replay.signalsBlocked() or self._duration_ms <= 0:
            return
        if self.progress_replay.isSliderDown():
            self._seek_dragging = True
        pos_ms = int(self._duration_ms * value / 1000)
        if self._player.state in (
            PlayerState.IDLE,
            PlayerState.PLAYING,
            PlayerState.PAUSED_RX,
        ):
            self._player.seek_position_ms(pos_ms)
        self.lbl_replay_elapsed.setText(_format_ms(pos_ms))
        rem = max(0, self._duration_ms - pos_ms)
        self.lbl_replay_remaining.setText(f"-{_format_ms(rem)}")
        self._update_remaining_warn(rem)

    def _apply_replay_seek_from_slider(self) -> None:
        if self._duration_ms <= 0:
            return
        pos_ms = self._slider_replay_position_ms()
        if self._player.state in (
            PlayerState.IDLE,
            PlayerState.PLAYING,
            PlayerState.PAUSED_RX,
        ):
            self._player.seek_position_ms(pos_ms)

    def _on_current_file(self, name: str) -> None:
        for i in range(self.list_files.count()):
            item = self.list_files.item(i)
            if item and item.text() == name:
                self.list_files.blockSignals(True)
                try:
                    self.list_files.setCurrentRow(i)
                finally:
                    self.list_files.blockSignals(False)
                break

    def _on_player_error(self, message: str) -> None:
        QMessageBox.warning(self, "Audio-Recorder", message)
        self._update_buttons()

    def _on_status(self, message: str) -> None:
        self.lbl_status.setText(message)

    def _on_voice_mode_requested(self) -> None:
        """Replay zu Ende oder gestoppt → zurück auf Sprach-Mode."""
        if not self._radio_setup.is_applied:
            return
        if not self._radio_setup.in_data_mode:
            return
        self._request_engage_plain()

    # ------------------------------------------------------------------
    # Geräte / Format
    # ------------------------------------------------------------------

    def _on_input_changed(self) -> None:
        dev_id = self.combo_input.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        if self._audio_hub is not None:
            self._audio_hub.set_device_id(ROLE_INPUT, dev_id)
        else:
            self._settings.audio_recorder.input_device_id = dev_id

    def _on_output_changed(self) -> None:
        dev_id = self.combo_output.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        if self._audio_hub is not None:
            self._audio_hub.set_device_id(ROLE_SEND, dev_id)
        else:
            self._apply_send_device(dev_id)

    def _on_input_volume_changed(self, value: int) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_volume_percent(ROLE_INPUT, int(value))
        else:
            self._apply_input_volume(int(value))

    def _on_output_volume_changed(self, value: int) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_volume_percent(ROLE_SEND, int(value))
        else:
            self._apply_send_volume(int(value))

    def _on_pc_output_changed(self) -> None:
        dev_id = self.combo_pc_output.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        if self._audio_hub is not None:
            self._audio_hub.set_device_id(ROLE_PC, dev_id)
        else:
            self._apply_pc_output_device(dev_id)

    def _on_pc_volume_changed(self, value: int) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_volume_percent(ROLE_PC, int(value))
        else:
            self._apply_pc_volume(int(value))

    def _on_tx_monitor_pc_toggled(self, checked: bool) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_tx_monitor_to_pc_enabled(bool(checked))
        else:
            self._apply_tx_monitor(bool(checked))

    def _on_input_mute_toggled(self, muted: bool) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_muted(ROLE_INPUT, bool(muted))

    def _on_send_mute_toggled(self, muted: bool) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_muted(ROLE_SEND, bool(muted))

    def _on_pc_mute_toggled(self, muted: bool) -> None:
        if self._audio_hub is not None:
            self._audio_hub.set_muted(ROLE_PC, bool(muted))

    def _apply_input_device(self, dev_id: str) -> None:
        pass

    def _apply_input_volume(self, percent: int) -> None:
        if self._audio_hub is not None:
            percent = self._audio_hub.qt_volume_percent(ROLE_INPUT)
        self._recorder.set_input_volume_percent(int(percent))

    def _apply_send_device(self, dev_id: str) -> None:
        self._player.set_output_device_id(str(dev_id or ""))

    def _apply_send_volume(self, percent: int) -> None:
        if self._audio_hub is not None:
            percent = self._audio_hub.qt_volume_percent(ROLE_SEND)
        self._player.set_volume_percent(int(percent))

    def _apply_input_mute(self, _muted: bool) -> None:
        pass

    def _apply_send_mute(self, _muted: bool) -> None:
        pass

    def _apply_pc_mute(self, _muted: bool) -> None:
        pass

    def _apply_pc_output_device(self, dev_id: str) -> None:
        dev_id = str(dev_id or "")
        self._pc_pending_device_id = dev_id
        self._player.set_tx_monitor_pc_device_id(dev_id)
        self._apply_pc_output_device_qt(dev_id)

    def _apply_pc_volume(self, percent: int) -> None:
        if self._audio_hub is not None:
            percent = self._audio_hub.qt_volume_percent(ROLE_PC)
        percent = max(0, min(100, int(percent)))
        self._pc_pending_volume_percent = percent
        self._player.set_tx_monitor_pc_volume_percent(percent)
        self._apply_pc_volume_qt(percent)

    def _apply_tx_monitor(self, enabled: bool) -> None:
        self._player.set_tx_monitor_to_pc_enabled(bool(enabled))

    def _apply_tx_monitor_pc_to_player(self) -> None:
        pc_id = self.combo_pc_output.currentData()
        if not isinstance(pc_id, str):
            pc_id = ""
        self._apply_pc_output_device(pc_id)
        self._apply_pc_volume(self._vol_pc.value())
        self._apply_tx_monitor(self.check_tx_monitor_pc.isChecked())

    # ------------------------------------------------------------------
    # Lokale PC-Vorhöre (zweiter Player, kein CAT, keine PTT)
    # ------------------------------------------------------------------

    def _ensure_pc_player(self) -> bool:
        """Lazy-Init des lokalen QMediaPlayer (erst nach erstem User-Klick)."""
        if self._pc_player_ready:
            return True
        mm = qt_multimedia_types()
        if mm is None:
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                "Audio-Wiedergabe nicht verfügbar — bitte PySide6-Addons "
                "installieren und App neu starten.",
            )
            return False
        QAudioOutput, _QMediaDevices, QMediaPlayer = mm
        self._pc_audio_out = QAudioOutput(self)
        self._pc_player = QMediaPlayer(self)
        self._pc_player.setAudioOutput(self._pc_audio_out)
        self._pc_player.playbackStateChanged.connect(
            self._on_pc_playback_state_changed
        )
        self._pc_player.errorOccurred.connect(self._on_pc_player_error)
        self._pc_player_ready = True
        # Beim ersten Play das gemerkte Gerät + Volume anwenden.
        self._apply_pc_output_device(
            getattr(self, "_pc_pending_device_id", "") or ""
        )
        self._apply_pc_volume(
            getattr(self, "_pc_pending_volume_percent", 100)
        )
        return True

    def _apply_pc_volume_qt(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        if not self._pc_player_ready or self._pc_audio_out is None:
            self._pc_pending_volume_percent = percent
            return
        try:
            vol = percent / 100.0
            if self._audio_hub is not None and self._audio_hub.uses_windows_volume():
                vol = 1.0
            self._pc_audio_out.setVolume(vol)
        except (AttributeError, TypeError):
            pass

    def _apply_pc_output_device_qt(self, device_id: str) -> None:
        if not self._pc_player_ready or self._pc_audio_out is None:
            self._pc_pending_device_id = device_id
            return
        mm = qt_multimedia_types()
        if mm is None:
            return
        _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
        if not device_id:
            self._pc_audio_out.setDevice(QMediaDevices.defaultAudioOutput())
            return
        for dev in QMediaDevices.audioOutputs():
            try:
                dev_uid = dev.id().data().decode("utf-8", errors="replace")
            except Exception:
                dev_uid = ""
            if dev_uid == device_id:
                self._pc_audio_out.setDevice(dev)
                return
        self._pc_audio_out.setDevice(QMediaDevices.defaultAudioOutput())

    def _is_pc_playing(self) -> bool:
        return bool(self._pc_is_playing)

    def _on_play_pc_clicked(self) -> None:
        if self._recorder.is_busy():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Aufnahme läuft — bitte zuerst stoppen.",
            )
            return
        if self._player.is_busy():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Replay läuft — bitte zuerst stoppen.",
            )
            return
        row = self.list_files.currentRow()
        if row < 0 or row >= len(self._files):
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Bitte eine Aufnahme in der Liste markieren.",
            )
            return
        target = self._folder / self._files[row]
        if not target.is_file():
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                f"Datei nicht gefunden:\n{target}",
            )
            self._refresh_file_list()
            return
        # Replay-Player kann nach CAT-Stopp die Datei noch gehalten haben.
        if not self._player.is_busy():
            self._player.release_source()
        self._release_pc_source()
        if not self._ensure_pc_player():
            return
        assert self._pc_player is not None
        try:
            self._pc_player.setSource(QUrl.fromLocalFile(str(target.resolve())))
            self._pc_player.play()
        except Exception as exc:  # noqa: BLE001 — Backend kann diverse Fehler werfen
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                f"PC-Wiedergabe konnte nicht gestartet werden:\n{exc}",
            )
            return
        self._pc_is_playing = True
        self.lbl_status.setText(f"PC-Wiedergabe: {target.name}")
        self._update_buttons()

    def _on_stop_pc_clicked(self) -> None:
        if not self._pc_player_ready or self._pc_player is None:
            return
        try:
            self._pc_player.stop()
        except Exception:
            pass
        # state-Signal räumt den Rest auf (_pc_is_playing).

    def _on_pc_playback_state_changed(self, state) -> None:
        mm = qt_multimedia_types()
        if mm is None:
            return
        _QAudioOutput, _QMediaDevices, QMediaPlayer = mm
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._pc_is_playing = True
        else:
            # PausedState wird hier nicht genutzt — Stopped + Idle ⇒ aus.
            self._pc_is_playing = False
        self._update_buttons()

    def _on_pc_player_error(self, _error, message: str = "") -> None:
        msg = message or "PC-Wiedergabe fehlgeschlagen."
        QMessageBox.warning(self, "Audio-Recorder", msg)
        self._pc_is_playing = False
        self._update_buttons()

    # ------------------------------------------------------------------
    # Datei löschen
    # ------------------------------------------------------------------

    def _on_delete_clicked(self) -> None:
        row = self.list_files.currentRow()
        if row < 0 or row >= len(self._files):
            return
        name = self._files[row]
        target = self._folder / name

        # Recorder darf nicht auf die Datei zugreifen.
        if self._recorder.is_busy():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Aufnahme läuft — bitte zuerst stoppen.",
            )
            return
        active_rec = self._recorder.current_path
        if active_rec is not None and active_rec.resolve() == target.resolve():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Diese Datei wird gerade aufgenommen — bitte zuerst stoppen.",
            )
            return
        # CAT-Replay darf nicht laufen.
        if self._player.is_busy():
            QMessageBox.information(
                self,
                "Audio-Recorder",
                "Replay läuft — bitte zuerst stoppen.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Aufnahme löschen",
            f"Datei dauerhaft löschen?\n\n{name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Beide QMediaPlayer-Instanzen halten unter Windows das File-Handle
        # offen, auch nach `stop()` — solange ihre `source()` auf die Datei
        # zeigt, schlaegt `unlink()` mit "Zugriff verweigert" fehl. Erst
        # Quelle freigeben, dann loeschen.
        self._release_pc_source()
        self._player.release_source()

        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Audio-Recorder",
                f"Datei konnte nicht gelöscht werden:\n{exc}",
            )
            return
        # Markierung zurücksetzen, falls sie die gelöschte Datei betraf.
        if self._settings.audio_recorder.selected_filename == name:
            self._settings.audio_recorder.selected_filename = ""
        self.lbl_status.setText(f"Gelöscht: {name}")
        self._refresh_file_list()

    def _release_pc_source(self) -> None:
        """Stoppt PC-Wiedergabe und gibt die Quelle frei (Windows File-Lock)."""
        if not self._pc_player_ready or self._pc_player is None:
            return
        try:
            self._pc_player.stop()
        except Exception:
            pass
        try:
            self._pc_player.setSource(QUrl())
        except Exception:
            pass
        self._pc_is_playing = False

    def _on_bitrate_changed(self) -> None:
        val = self.combo_bitrate.currentData()
        if isinstance(val, int):
            self._settings.audio_recorder.mp3_bitrate_kbps = val

    # ------------------------------------------------------------------
    # CAT-Setup
    # ------------------------------------------------------------------

    def sync_data_mode_from_main(self, mode: Optional[RxMode] = None) -> None:
        """DATA-Modus aus Hauptfenster-Betriebsart (FM→DATA-FM, USB→DATA-USB, …)."""
        if mode is None:
            if self._operating_mode_provider is not None:
                mode = self._operating_mode_provider()
            elif self._cat.is_connected():
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
        if self._player.is_busy() or self._recorder.is_busy():
            self.lbl_status.setText(
                "Betriebsart-Wechsel während Aufnahme/Sendung nicht möglich."
            )
            return
        if self._radio_setup.data_mode == data_mode:
            return
        self.lbl_status.setText(f"Funkgerät wird auf {data_mode.value} geschaltet …")
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_set_data_mode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, data_mode.value),
        )

    def _apply_or_engage_data(self) -> None:
        """Setup anwenden oder vom Voice-Mode zurück in DATA-Mode wechseln."""
        self.sync_data_mode_from_main()
        target = self._radio_setup.data_mode
        if not self._radio_setup.is_applied:
            self.lbl_status.setText(
                f"Funkgerät wird auf {target.value} / 048+077+109→USB, 070→REAR, 072→USB geschaltet …"
            )
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_apply",
                Qt.ConnectionType.QueuedConnection,
            )
            return
        if not self._radio_setup.in_data_mode:
            self.lbl_status.setText(
                f"Schalte zurück auf {self._radio_setup.data_mode.value} …"
            )
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_engage_data",
                Qt.ConnectionType.QueuedConnection,
            )
            return
        # Bereits im richtigen Zustand → direkt weiter.
        self._continue_after_data_mode_ready()

    def _continue_after_data_mode_ready(self) -> None:
        action = self._pending_after_apply
        self._pending_after_apply = ""
        if action == "record":
            self._start_recording_now()
        elif action == "replay":
            row = getattr(self, "_pending_replay_row", None)
            if row is None or row < 0 or row >= len(self._files):
                row = self.list_files.currentRow()
            if row is None or row < 0:
                return
            self._player.play(int(row))

    def _request_engage_plain(self) -> None:
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_engage_plain",
            Qt.ConnectionType.QueuedConnection,
        )

    def _request_radio_restore(self) -> None:
        if self._audio_radio_session is not None:
            return
        if not self._radio_setup.is_applied:
            return
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_restore",
            Qt.ConnectionType.QueuedConnection,
        )

    def _radio_transmit_activity_busy(self) -> bool:
        return self._recorder.is_busy() or self._player.is_busy()

    def _request_radio_restore_on_close(self) -> None:
        if self._audio_radio_session is not None:
            self._audio_radio_session.request_restore_if_no_windows()
        else:
            self._request_radio_restore()

    def _try_complete_radio_restore_after_close(self) -> None:
        if not self._pending_radio_restore_on_close:
            return
        if self._radio_transmit_activity_busy():
            return
        if self._radio_setup.is_applied and self._radio_setup.in_data_mode:
            return
        self._pending_radio_restore_on_close = False
        self._request_radio_restore_on_close()
        if self._force_close_after_radio_restore:
            self._force_close_after_radio_restore = False
            self._finish_force_close()

    def _begin_radio_restore_on_close(self) -> None:
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)
        if self._radio_transmit_activity_busy():
            self._pending_radio_restore_on_close = True
            self.lbl_status.setText(
                "Aktivität wird beendet — Funkgerät wird zurückgestellt …"
            )
            if self._recorder.is_busy():
                self._recorder.stop()
            if self._player.is_busy():
                self._player.stop()
            return
        self._request_radio_restore_on_close()

    def _detach_radio_for_force_close(self) -> None:
        if self._audio_radio_session is not None:
            self._audio_radio_session.detach_for_force_close(self)
        elif self._radio_setup.is_applied:
            self._radio_setup.restore()

    def _finish_force_close(self) -> None:
        self._detach_radio_for_force_close()
        if self._owns_radio_thread:
            self._setup_thread.quit()
            self._setup_thread.wait(2000)
        self._recorder.shutdown()
        self._player.shutdown()
        self.close()

    def _on_radio_apply_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok:
            self._pending_after_apply = ""
            if message and self._audio_radio_session is None:
                QMessageBox.warning(self, "Audio-Recorder", message)
            return
        self._continue_after_data_mode_ready()

    def _on_radio_restore_finished(self, ok: bool, message: str) -> None:
        if message and not ok and self._audio_radio_session is None:
            QMessageBox.warning(self, "Audio-Recorder", message)

    def _on_radio_engage_plain_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Recorder", message)
        self._try_complete_radio_restore_after_close()

    def _on_radio_engage_data_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok:
            self._pending_after_apply = ""
            if message:
                QMessageBox.warning(self, "Audio-Recorder", message)
            return
        self._continue_after_data_mode_ready()

    # ------------------------------------------------------------------
    # MIC-PTT-Brücke
    # ------------------------------------------------------------------

    def handle_tx_state_changed(self, state: int) -> None:
        """MainWindow-Brücke: MIC-PTT bricht Aufnahme/Replay ab."""
        if not self._radio_setup.is_applied:
            return
        if state == TX_STATE_MIC_PTT:
            had_activity = self._recorder.is_busy() or self._player.is_busy()
            if not had_activity and not self._radio_setup.in_data_mode:
                return
            self._mic_ptt_interrupted = True
            if self._recorder.is_busy():
                self._recorder.stop()
            if self._player.is_busy():
                self._player.stop()
            if self._radio_setup.in_data_mode:
                voice = self._radio_setup.voice_mode.value
                self.lbl_status.setText(
                    f"MIC PTT erkannt — schalte auf {voice} …"
                )
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_engage_plain_forced",
                    Qt.ConnectionType.QueuedConnection,
                )
            return
        if state == TX_STATE_RX and self._mic_ptt_interrupted:
            if self._radio_setup.needs_plain_verify:
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_verify_plain",
                    Qt.ConnectionType.QueuedConnection,
                )
            elif self._radio_setup.in_data_mode:
                self._request_engage_plain()

    # ------------------------------------------------------------------
    # Button-Zustände
    # ------------------------------------------------------------------

    def _update_buttons(self) -> None:
        rec_busy = self._recorder.is_busy()
        play_busy = self._player.is_busy()
        pc_busy = self._is_pc_playing()
        any_busy = rec_busy or play_busy or pc_busy
        can_rec = (
            recorder_import_ok()
            and self._folder is not None
            and not any_busy
        )
        self.btn_record.setEnabled(can_rec)
        self.btn_stop_rec.setEnabled(rec_busy)

        has_selection = (
            self.list_files.currentRow() >= 0
            and self.list_files.currentRow() < len(self._files)
        )
        self.btn_replay.setEnabled(
            multimedia_available()
            and has_selection
            and not any_busy
        )
        self.btn_stop_replay.setEnabled(play_busy)
        self.btn_replay.setStyleSheet(
            _REPLAY_STYLE_ACTIVE if play_busy else _REPLAY_STYLE_IDLE
        )
        self.progress_replay.setEnabled(
            multimedia_available()
            and self._duration_ms > 0
            and not pc_busy
            and (
                not play_busy
                or self._player.state
                in (PlayerState.PLAYING, PlayerState.PAUSED_RX)
            )
        )

        # Lokale PC-Vorhöre — exklusiv zu Aufnahme/Replay.
        self.btn_play_pc.setEnabled(
            multimedia_available()
            and has_selection
            and not any_busy
        )
        self.btn_stop_pc.setEnabled(pc_busy)
        self.check_tx_monitor_pc.setEnabled(multimedia_available())
        # Löschen: keine Aktivität, gültige Auswahl.
        self.btn_delete.setEnabled(has_selection and not any_busy)

        # Geräte/Format-Combos während Aufnahme sperren (Live-Wechsel würde Pipeline reissen).
        self.combo_input.setEnabled(not rec_busy)
        self.combo_bitrate.setEnabled(not rec_busy)
        # PC-Output darf während aktiver PC-Wiedergabe nicht gewechselt werden,
        # sonst hört man Audio-Glitches / Backend kann sich verschlucken.
        self.combo_pc_output.setEnabled(not pc_busy)
        self.btn_folder.setEnabled(not any_busy)
        self.btn_refresh.setEnabled(not any_busy)
        self.btn_open_folder.setEnabled(True)
        self.list_files.setEnabled(not any_busy)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def persist_settings(self) -> None:
        self._save_geometry()

    def force_close(self) -> None:
        self._force_close = True
        self._release_pc_source()
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)
        if self._radio_transmit_activity_busy():
            self._pending_radio_restore_on_close = True
            self._force_close_after_radio_restore = True
            if self._recorder.is_busy():
                self._recorder.stop()
            if self._player.is_busy():
                self._player.stop()
            return
        self._finish_force_close()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._cat.is_connected():
            self.sync_data_mode_from_main()
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_shown(self)
            return
        if self._cat.is_connected():
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_apply_pc_menus",
                Qt.ConnectionType.QueuedConnection,
            )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.persist_settings()
        if application_exit_close_requested(self):
            if not getattr(self, "_force_close", False):
                self.force_close()
                event.accept()
                return
        if getattr(self, "_force_close", False):
            super().closeEvent(event)
            self.closed.emit()
            return
        self._release_pc_source()
        self._begin_radio_restore_on_close()
        self.hide()
        event.ignore()
        self.closed.emit()
