"""Fenster für CAT-Audio-Player (MP3/WAV + PTT)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QByteArray, QMetaObject, Qt, QThread, QTimer, Q_ARG, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from audio.player_controller import (
    PlayerController,
    PlayerState,
    list_audio_output_devices,
    multimedia_available,
)
from audio.radio_playback_setup import (
    RadioPlaybackSetup,
    RadioSetupWorker,
    data_mode_from_string,
)
from cat import SerialCAT
from mapping import TX_STATE_MIC_PTT, TX_STATE_RX
from mapping.rx_mapping import RxMode
from model import AppSettings
from model.audio_player_settings import (
    ALLOWED_DATA_MODES,
    DataMode,
    MAX_CONTEST_LISTEN_MS,
    merge_playlist_order,
    scan_audio_files,
)

if TYPE_CHECKING:
    from .audio_radio_session import AudioRadioSessionHost


def _format_ms(ms: int) -> str:
    ms = max(0, int(ms))
    s = ms // 1000
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


def _double_font(base: QFont) -> QFont:
    f = QFont(base)
    f.setPointSizeF(f.pointSizeF() * 2)
    return f


_REMAINING_WARN_MS = 10_000
_REMAINING_STYLE_NORMAL = ""
_REMAINING_STYLE_WARN = "color: #ff4444; font-weight: bold;"


class AudioPlayerWindow(QMainWindow):
    """Audio-Player mit Sendeliste und CAT-PTT."""

    closed = Signal()

    def __init__(
        self,
        settings: AppSettings,
        serial_cat: SerialCAT,
        *,
        audio_radio_session: Optional[AudioRadioSessionHost] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cat = serial_cat
        self._audio_radio_session = audio_radio_session
        self._folder = Path(settings.audio_player.folder_path or "")
        self._playlist_names: list[str] = list(settings.audio_player.playlist_order)

        self.setWindowTitle("FT-991/A Audio-Player")
        self.setWindowIcon(app_icon())
        self.resize(520, 560)

        self._controller = PlayerController(self._cat, self)
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
        self._setup_worker.data_mode_finished.connect(self._on_radio_data_mode_finished)
        self._setup_worker.engage_plain_finished.connect(
            self._on_radio_engage_plain_finished
        )
        self._setup_worker.engage_data_finished.connect(
            self._on_radio_engage_data_finished
        )
        if self._owns_radio_thread:
            self._setup_thread.start()
        self._radio_apply_pending = False
        #: Wenn True, hat MIC-PTT die Wiedergabe unterbrochen — beim nächsten
        #: Play muss erst der DATA-Mode zurückgeschaltet werden.
        self._mic_ptt_interrupted = False

        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.position_changed.connect(self._on_position_changed)
        self._controller.current_file_changed.connect(self._on_current_file)
        self._controller.error.connect(self._on_error)
        self._controller.status_message.connect(self._on_status)
        self._controller.voice_mode_requested.connect(self._on_voice_mode_requested)

        self._duration_ms = 0
        self._seek_dragging = False
        self._remaining_warn_active = False
        self._remaining_blink_on = True
        self._last_player_state: Optional[PlayerState] = None

        self._build_ui()
        self._load_settings_to_ui()
        self._refresh_file_list()
        self._restore_geometry()
        self._update_transport_buttons()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        row = QHBoxLayout()
        self.btn_folder = QPushButton("Ordner wählen …")
        self.btn_folder.clicked.connect(self._on_pick_folder)
        self.btn_refresh = QPushButton("Aktualisieren")
        self.btn_refresh.clicked.connect(self._refresh_file_list)
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        root.addLayout(row)

        self.lbl_folder = QLabel("")
        self.lbl_folder.setWordWrap(True)
        self.lbl_folder.setStyleSheet("color: gray;")
        root.addWidget(self.lbl_folder)

        list_box = QGroupBox("Sendeliste (Reihenfolge per Drag & Drop)")
        list_l = QVBoxLayout(list_box)
        self.list_files = QListWidget()
        self.list_files.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_files.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_files.currentRowChanged.connect(self._on_list_row_changed)
        self.list_files.itemDoubleClicked.connect(self._on_item_double_clicked)
        model = self.list_files.model()
        model.rowsMoved.connect(self._on_list_reordered)
        model.layoutChanged.connect(self._on_list_reordered)
        list_l.addWidget(self.list_files)
        root.addWidget(list_box, stretch=1)

        mode_box = QGroupBox("Wiedergabe")
        mode_l = QVBoxLayout(mode_box)

        data_row = QHBoxLayout()
        data_row.addWidget(QLabel("Sende-Mode:"))
        self.radio_data_usb = QRadioButton("DATA-USB")
        self.radio_data_lsb = QRadioButton("DATA-LSB")
        self.radio_data_fm = QRadioButton("DATA-FM")
        self._data_mode_group = QButtonGroup(self)
        self._data_mode_group.setExclusive(True)
        self._data_mode_buttons: dict[str, QRadioButton] = {
            "DATA-USB": self.radio_data_usb,
            "DATA-LSB": self.radio_data_lsb,
            "DATA-FM": self.radio_data_fm,
        }
        for name, btn in self._data_mode_buttons.items():
            self._data_mode_group.addButton(btn)
            btn.setToolTip(
                f"Audio-Wiedergabe in Betriebsart {name} (DATA-Port = USB / Rear)"
            )
            btn.toggled.connect(self._on_data_mode_toggled)
            data_row.addWidget(btn)
        data_row.addStretch(1)
        mode_l.addLayout(data_row)

        self.radio_single = QRadioButton("Nach jeder Datei stoppen (RX)")
        self.radio_playlist = QRadioButton("Alle nacheinander")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.radio_single)
        self._mode_group.addButton(self.radio_playlist)
        self.radio_single.toggled.connect(self._sync_mode_to_controller)
        mode_l.addWidget(self.radio_single)
        mode_l.addWidget(self.radio_playlist)

        contest_row = QHBoxLayout()
        self.check_contest = QCheckBox("Kontest-Loop")
        self.check_contest.setToolTip(
            "Markierte Datei dauerhaft wiederholen (Auto-Ruf für Contests). "
            "Nach jeder Wiedergabe folgt die eingestellte Hörpause im "
            "Sprach-Mode (USB/LSB/FM), damit Stationen antworten können. "
            "MIC-PTT bricht den Loop ab — kein automatischer Neustart."
        )
        self.check_contest.toggled.connect(self._on_contest_toggled)
        contest_row.addWidget(self.check_contest)
        contest_row.addWidget(QLabel("Hörpause:"))
        self.spin_contest_listen = QSpinBox()
        self.spin_contest_listen.setRange(0, MAX_CONTEST_LISTEN_MS)
        self.spin_contest_listen.setSuffix(" ms")
        self.spin_contest_listen.setSingleStep(500)
        self.spin_contest_listen.setToolTip(
            "Dauer der Hörpause zwischen den Wiederholungen (Sprach-Mode)."
        )
        self.spin_contest_listen.valueChanged.connect(self._sync_contest_to_controller)
        contest_row.addWidget(self.spin_contest_listen)
        contest_row.addStretch(1)
        mode_l.addLayout(contest_row)

        timing = QHBoxLayout()
        timing.addWidget(QLabel("Vorlauf:"))
        self.spin_pre_roll = QSpinBox()
        self.spin_pre_roll.setRange(0, 60_000)
        self.spin_pre_roll.setSuffix(" ms")
        self.spin_pre_roll.valueChanged.connect(self._sync_timing)
        timing.addWidget(self.spin_pre_roll)
        timing.addWidget(QLabel("Pause zwischen Dateien:"))
        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(0, 60_000)
        self.spin_gap.setSuffix(" ms")
        self.spin_gap.valueChanged.connect(self._sync_timing)
        timing.addWidget(self.spin_gap)
        timing.addStretch(1)
        mode_l.addLayout(timing)

        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("Ausgabe:"))
        self.combo_output = QComboBox()
        self._fill_output_devices()
        self.combo_output.currentIndexChanged.connect(self._on_output_changed)
        dev_row.addWidget(self.combo_output, 1)
        mode_l.addLayout(dev_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Lautstärke:"))
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_volume.setTickInterval(10)
        self.slider_volume.setPageStep(10)
        self.slider_volume.setToolTip("Wiedergabe-Lautstärke des gewählten Ausgabegeräts")
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.slider_volume, 1)
        self.lbl_volume = QLabel("100 %")
        self.lbl_volume.setMinimumWidth(40)
        vol_row.addWidget(self.lbl_volume)
        mode_l.addLayout(vol_row)

        root.addWidget(mode_box)

        transport = QHBoxLayout()
        self.btn_play = QPushButton("Start")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stopp")
        self.btn_play.clicked.connect(self._on_play)
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        self.btn_stop.clicked.connect(self._controller.stop)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_pause)
        transport.addWidget(self.btn_stop)
        transport.addStretch(1)
        root.addLayout(transport)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setPageStep(50)
        self.progress.setToolTip("Position — ziehen zum Spulen")
        self.progress.setTracking(True)
        self.progress.sliderPressed.connect(self._on_seek_pressed)
        self.progress.sliderReleased.connect(self._on_seek_released)
        self.progress.sliderMoved.connect(self._on_seek_slider_change)
        self.progress.valueChanged.connect(self._on_seek_slider_change)
        root.addWidget(self.progress)

        self._remaining_blink_timer = QTimer(self)
        self._remaining_blink_timer.setInterval(500)
        self._remaining_blink_timer.timeout.connect(self._on_remaining_blink_tick)

        time_row = QHBoxLayout()
        self.lbl_elapsed = QLabel("0:00")
        self.lbl_remaining = QLabel("-0:00")
        time_font = _double_font(self.lbl_elapsed.font())
        self.lbl_elapsed.setFont(time_font)
        self.lbl_remaining.setFont(time_font)
        time_row.addWidget(self.lbl_elapsed)
        time_row.addStretch(1)
        time_row.addWidget(self.lbl_remaining)
        root.addLayout(time_row)

        self.lbl_status = QLabel("Bereit")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        if not multimedia_available():
            self.lbl_status.setText(
                "Audio-Wiedergabe nicht verfügbar. "
                "pip install PySide6-Addons — App danach neu starten."
            )
            self.btn_play.setEnabled(False)

        self.setCentralWidget(central)

    def _fill_output_devices(self) -> None:
        self.combo_output.blockSignals(True)
        try:
            self.combo_output.clear()
            saved = self._settings.audio_player.output_device_id
            select_idx = 0
            for i, (dev_id, label) in enumerate(list_audio_output_devices()):
                self.combo_output.addItem(label, dev_id)
                if dev_id == saved:
                    select_idx = i
            self.combo_output.setCurrentIndex(select_idx)
        finally:
            self.combo_output.blockSignals(False)

    def _load_settings_to_ui(self) -> None:
        ap = self._settings.audio_player
        self.spin_pre_roll.setValue(ap.pre_roll_ms)
        self.spin_gap.setValue(ap.gap_between_files_ms)
        if ap.playback_mode == "playlist":
            self.radio_playlist.setChecked(True)
        else:
            self.radio_single.setChecked(True)
        self.spin_contest_listen.blockSignals(True)
        try:
            self.spin_contest_listen.setValue(ap.contest_listen_pause_ms)
        finally:
            self.spin_contest_listen.blockSignals(False)
        self.check_contest.blockSignals(True)
        try:
            self.check_contest.setChecked(bool(ap.contest_mode))
        finally:
            self.check_contest.blockSignals(False)
        self._refresh_contest_enabled_state()
        chosen = ap.data_mode if ap.data_mode in ALLOWED_DATA_MODES else "DATA-FM"
        for name, btn in self._data_mode_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name == chosen)
            btn.blockSignals(False)
        self._sync_timing()
        self._sync_mode_to_controller()
        self._sync_contest_to_controller()
        self.slider_volume.blockSignals(True)
        try:
            self.slider_volume.setValue(ap.volume_percent)
            self.lbl_volume.setText(f"{ap.volume_percent} %")
        finally:
            self.slider_volume.blockSignals(False)
        self._controller.set_volume_percent(ap.volume_percent)
        # Wichtig: das in der Combo voreingestellte Audio-Gerät auch am
        # Controller anwenden. Ohne diesen Call läuft die Wiedergabe nach
        # dem Öffnen des Fensters auf der Default-Soundkarte, obwohl die
        # Combo das gespeicherte Gerät zeigt.
        saved_device = self.combo_output.currentData()
        if not isinstance(saved_device, str):
            saved_device = ""
        self._controller.set_output_device_id(saved_device)

    def _restore_geometry(self) -> None:
        geo = self._settings.audio_player.window_geometry
        if not geo:
            return
        try:
            ba = QByteArray(base64.b64decode(geo.encode("ascii")))
            self.restoreGeometry(ba)
        except Exception:
            pass

    def _save_geometry(self) -> None:
        self._settings.audio_player.window_geometry = base64.b64encode(
            self.saveGeometry().data()
        ).decode("ascii")

    def _on_pick_folder(self) -> None:
        start = str(self._folder) if self._folder.is_dir() else ""
        path = QFileDialog.getExistingDirectory(self, "Audio-Ordner", start)
        if not path:
            return
        self._folder = Path(path)
        self._settings.audio_player.folder_path = path
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        if self._folder.is_dir():
            discovered = scan_audio_files(self._folder)
            self._playlist_names = merge_playlist_order(
                self._playlist_names, discovered
            )
            self.lbl_folder.setText(str(self._folder))
        else:
            self._playlist_names = []
            self.lbl_folder.setText("(kein Ordner gewählt)")
        self._rebuild_list_widget()
        self._push_playlist_to_controller()

    def _rebuild_list_widget(self) -> None:
        self.list_files.blockSignals(True)
        try:
            self.list_files.clear()
            for name in self._playlist_names:
                self.list_files.addItem(QListWidgetItem(name))
        finally:
            self.list_files.blockSignals(False)

    def _sync_playlist_from_list(self) -> None:
        """Liste -> Namen -> Controller (immer vor play() aufrufen)."""
        self._playlist_names = [
            self.list_files.item(i).text()
            for i in range(self.list_files.count())
            if self.list_files.item(i) is not None
        ]
        self._settings.audio_player.playlist_order = list(self._playlist_names)
        self._push_playlist_to_controller()

    def _on_list_reordered(self, *args) -> None:
        self._sync_playlist_from_list()

    def _on_list_row_changed(self, row: int) -> None:
        if row >= 0:
            self._sync_playlist_from_list()
            self._controller.load_track(row)

    def _push_playlist_to_controller(self) -> None:
        if not self._folder.is_dir():
            self._controller.set_playlist([])
            return
        paths = [self._folder / n for n in self._playlist_names]
        self._controller.set_playlist(paths)

    def _sync_timing(self) -> None:
        self._controller.set_timing(
            self.spin_pre_roll.value(),
            self.spin_gap.value(),
        )
        self._settings.audio_player.pre_roll_ms = self.spin_pre_roll.value()
        self._settings.audio_player.gap_between_files_ms = self.spin_gap.value()

    def _sync_mode_to_controller(self) -> None:
        mode = "playlist" if self.radio_playlist.isChecked() else "single"
        self._controller.set_playback_mode(mode)  # type: ignore[arg-type]
        self._settings.audio_player.playback_mode = mode  # type: ignore[assignment]

    def _sync_contest_to_controller(self) -> None:
        enabled = self.check_contest.isChecked()
        listen_ms = int(self.spin_contest_listen.value())
        self._controller.set_contest_mode(enabled, listen_ms)
        self._settings.audio_player.contest_mode = enabled
        self._settings.audio_player.contest_listen_pause_ms = listen_ms

    def _on_contest_toggled(self, _checked: bool) -> None:
        self._refresh_contest_enabled_state()
        self._sync_contest_to_controller()

    def _refresh_contest_enabled_state(self) -> None:
        on = self.check_contest.isChecked()
        self.spin_contest_listen.setEnabled(on)
        # Single/Playlist haben im Kontest-Modus keine Bedeutung — der Loop
        # spielt immer die markierte Datei. Wir deaktivieren sie sichtbar.
        self.radio_single.setEnabled(not on)
        self.radio_playlist.setEnabled(not on)

    def _on_output_changed(self) -> None:
        dev_id = self.combo_output.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._controller.set_output_device_id(dev_id)
        self._settings.audio_player.output_device_id = dev_id

    def _on_volume_changed(self, value: int) -> None:
        self.lbl_volume.setText(f"{value} %")
        self._controller.set_volume_percent(value)
        self._settings.audio_player.volume_percent = value

    def _on_pause_clicked(self) -> None:
        if self._controller.state == PlayerState.PAUSED_RX:
            self._on_play()
        else:
            self._controller.pause()

    def _on_play(self) -> None:
        self._sync_playlist_from_list()
        row = self.list_files.currentRow()
        if row < 0 and self.list_files.count() > 0:
            row = 0
            self.list_files.setCurrentRow(0)
        # Falls MIC-PTT die Wiedergabe zuvor unterbrochen hat (oder das Setup
        # vom Voice-Mode kommt), erst DATA-Mode wieder einschalten.
        if self._radio_setup.is_applied and not self._radio_setup.in_data_mode:
            self.lbl_status.setText(
                f"Schalte zurück auf {self._radio_setup.data_mode.value} …"
            )
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_engage_data",
                Qt.ConnectionType.QueuedConnection,
            )
        self._mic_ptt_interrupted = False
        self._controller.play(row if row >= 0 else None)

    def _on_data_mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        chosen: Optional[str] = None
        for name, btn in self._data_mode_buttons.items():
            if btn.isChecked():
                chosen = name
                break
        if chosen is None:
            return
        self._settings.audio_player.data_mode = chosen  # type: ignore[assignment]
        if not self._radio_setup.is_applied:
            return
        if self._controller.is_busy():
            self.lbl_status.setText(
                "Mode-Wechsel nicht möglich während aktiver TX — bitte stoppen."
            )
            return
        self.lbl_status.setText(f"Funkgerät wird auf {chosen} geschaltet …")
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_set_data_mode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, chosen),
        )

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self._sync_playlist_from_list()
        row = self.list_files.row(item)
        if row >= 0:
            self.list_files.setCurrentRow(row)
            self._controller.play(row)

    def _on_state_changed(self, state: PlayerState) -> None:
        if state != PlayerState.PLAYING:
            self._set_remaining_warn(False)
        self._handle_contest_state_transition(state)
        self._last_player_state = state
        self._update_transport_buttons()

    def _on_voice_mode_requested(self) -> None:
        """Stopp oder Einzeldatei-Ende: Funkgerät auf Sprach-Mode (MIC vorne)."""
        if not self._radio_setup.is_applied:
            return
        if not self._radio_setup.in_data_mode:
            return
        voice = self._radio_setup.voice_mode.value
        self.lbl_status.setText(f"Schalte auf {voice} …")
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_engage_plain",
            Qt.ConnectionType.QueuedConnection,
        )

    def _handle_contest_state_transition(self, state: PlayerState) -> None:
        """Mode-Wechsel bei Kontest-Loop und Hörpause.

        - Eintritt in ``LISTEN_PAUSE``: Funkgerät auf Sprach-Mode schalten.
        - Verlassen von ``LISTEN_PAUSE`` Richtung ``PRE_ROLL``: zurück auf
          DATA-Mode (vor dem Pre-Roll, damit der TX im DATA-Mode startet).
        """
        previous = getattr(self, "_last_player_state", None)
        if not self._radio_setup.is_applied:
            return
        if state == PlayerState.LISTEN_PAUSE and previous != PlayerState.LISTEN_PAUSE:
            if self._radio_setup.in_data_mode:
                voice = self._radio_setup.voice_mode.value
                self.lbl_status.setText(
                    f"Kontest-Hörpause — schalte auf {voice} …"
                )
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_engage_plain",
                    Qt.ConnectionType.QueuedConnection,
                )
            return
        if (
            state == PlayerState.PRE_ROLL
            and previous == PlayerState.LISTEN_PAUSE
            and not self._radio_setup.in_data_mode
        ):
            target = self._radio_setup.data_mode.value
            self.lbl_status.setText(f"Loop-Restart — schalte auf {target} …")
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_engage_data",
                Qt.ConnectionType.QueuedConnection,
            )

    def _update_transport_buttons(self) -> None:
        st = self._controller.state
        busy = self._controller.is_busy()
        self.btn_play.setEnabled(
            multimedia_available()
            and bool(self._playlist_names)
            and st in (PlayerState.IDLE, PlayerState.PAUSED_RX)
        )
        self.btn_pause.setEnabled(st in (PlayerState.PLAYING, PlayerState.PAUSED_RX))
        self.btn_pause.setText(
            "Fortsetzen" if st == PlayerState.PAUSED_RX else "Pause"
        )
        self.btn_stop.setEnabled(
            st
            not in (
                PlayerState.IDLE,
            )
        )
        self.list_files.setEnabled(not busy)
        self.btn_folder.setEnabled(not busy)
        contest_on = self.check_contest.isChecked()
        self.radio_single.setEnabled(not busy and not contest_on)
        self.radio_playlist.setEnabled(not busy and not contest_on)
        self.check_contest.setEnabled(not busy)
        self.spin_contest_listen.setEnabled(contest_on and not busy)
        self.slider_volume.setEnabled(multimedia_available())
        self.progress.setEnabled(
            multimedia_available() and self._duration_ms > 0 and not busy
        )

    def _on_position_changed(self, pos_ms: int, dur_ms: int) -> None:
        self._duration_ms = max(0, dur_ms)
        if not self._seek_dragging:
            if dur_ms > 0:
                self.progress.blockSignals(True)
                try:
                    self.progress.setValue(int(1000 * pos_ms / dur_ms))
                finally:
                    self.progress.blockSignals(False)
            else:
                self.progress.setValue(0)
            self.lbl_elapsed.setText(_format_ms(pos_ms))
            rem = max(0, dur_ms - pos_ms)
            self.lbl_remaining.setText(f"-{_format_ms(rem)}")
            self._update_remaining_warn(rem)
        self._update_transport_buttons()

    def _update_remaining_warn(self, rem_ms: int) -> None:
        playing = self._controller.state == PlayerState.PLAYING
        self._set_remaining_warn(playing and rem_ms < _REMAINING_WARN_MS)

    def _set_remaining_warn(self, active: bool) -> None:
        if active == self._remaining_warn_active:
            if active:
                self.lbl_remaining.setStyleSheet(
                    _REMAINING_STYLE_WARN if self._remaining_blink_on else _REMAINING_STYLE_NORMAL
                )
            return
        self._remaining_warn_active = active
        if active:
            self._remaining_blink_on = True
            self.lbl_remaining.setStyleSheet(_REMAINING_STYLE_WARN)
            self._remaining_blink_timer.start()
        else:
            self._remaining_blink_timer.stop()
            self.lbl_remaining.setStyleSheet(_REMAINING_STYLE_NORMAL)
            self.lbl_remaining.setVisible(True)

    def _on_remaining_blink_tick(self) -> None:
        if not self._remaining_warn_active:
            return
        self._remaining_blink_on = not self._remaining_blink_on
        self.lbl_remaining.setStyleSheet(
            _REMAINING_STYLE_WARN if self._remaining_blink_on else _REMAINING_STYLE_NORMAL
        )

    def _on_seek_pressed(self) -> None:
        self._seek_dragging = True

    def _on_seek_released(self) -> None:
        self._seek_dragging = False
        self._apply_seek_from_slider()

    def _on_seek_slider_change(self, value: int) -> None:
        if self.progress.signalsBlocked() or self._duration_ms <= 0:
            return
        if self.progress.isSliderDown():
            self._seek_dragging = True
        pos_ms = int(self._duration_ms * value / 1000)
        self.lbl_elapsed.setText(_format_ms(pos_ms))
        rem = max(0, self._duration_ms - pos_ms)
        self.lbl_remaining.setText(f"-{_format_ms(rem)}")
        self._update_remaining_warn(rem)

    def _apply_seek_from_slider(self) -> None:
        if self._duration_ms <= 0:
            return
        pos_ms = int(self._duration_ms * self.progress.value() / 1000)
        self._controller.seek_position_ms(pos_ms)

    def _on_current_file(self, name: str) -> None:
        for i in range(self.list_files.count()):
            item = self.list_files.item(i)
            if item and item.text() == name:
                self.list_files.setCurrentRow(i)
                break

    def _on_error(self, message: str) -> None:
        QMessageBox.warning(self, "Audio-Player", message)
        self._update_transport_buttons()

    def _on_status(self, message: str) -> None:
        self.lbl_status.setText(message)

    def persist_settings(self) -> None:
        self._sync_playlist_from_list()
        self._settings.audio_player.folder_path = (
            str(self._folder) if self._folder.is_dir() else ""
        )
        self._save_geometry()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_shown(self)
            return
        if not self._radio_setup.is_applied and not self._radio_apply_pending:
            self._request_radio_apply()

    def _request_radio_apply(self) -> None:
        self._radio_apply_pending = True
        target = self._radio_setup.data_mode.value
        self.lbl_status.setText(
            f"Funkgerät wird auf {target} / 048+077+109→USB, 070→REAR, 072→USB geschaltet …"
        )
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_apply",
            Qt.ConnectionType.QueuedConnection,
        )

    def _request_radio_restore(self) -> None:
        if not self._radio_setup.is_applied:
            return
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_restore",
            Qt.ConnectionType.QueuedConnection,
        )

    def _on_radio_apply_finished(self, ok: bool, message: str) -> None:
        self._radio_apply_pending = False
        if message:
            self.lbl_status.setText(message)
        if not ok and message and self._audio_radio_session is None:
            QMessageBox.warning(self, "Audio-Player", message)

    def _on_radio_restore_finished(self, ok: bool, message: str) -> None:
        if message and not ok and self._audio_radio_session is None:
            QMessageBox.warning(self, "Audio-Player", message)

    def _on_radio_data_mode_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Player", message)

    def _on_radio_engage_plain_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Player", message)

    def _on_radio_engage_data_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Player", message)

    def handle_tx_state_changed(self, state: int) -> None:
        """MainWindow-Brücke: TX-Status (0/1/2) vom Meter-Poller.

        - ``TX_STATE_MIC_PTT`` während Wiedergabe → CAT-TX aus, Mode
          erzwungen auf Sprach-Mode (User hält PTT, daher ``force=True``).
        - ``TX_STATE_RX`` nach einer MIC-PTT-Unterbrechung → nochmal
          sauber verifiziert nachschalten, falls der Force-Write während
          TX vom Radio verworfen wurde.
        """
        if not self._radio_setup.is_applied:
            return
        if state == TX_STATE_MIC_PTT:
            if not self._controller.is_busy() and not self._radio_setup.in_data_mode:
                return
            self._mic_ptt_interrupted = True
            if self._controller.is_busy():
                self._controller.stop()
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
            # User hat MIC PTT losgelassen — Mode jetzt verifizieren und
            # ggf. nochmal sauber setzen (Force-Write während TX wird vom
            # FT-991 nicht immer angenommen).
            if self._radio_setup.needs_plain_verify:
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_verify_plain",
                    Qt.ConnectionType.QueuedConnection,
                )
            elif self._radio_setup.in_data_mode:
                QMetaObject.invokeMethod(
                    self._setup_worker,
                    "run_engage_plain",
                    Qt.ConnectionType.QueuedConnection,
                )

    def force_close(self) -> None:
        self._force_close = True
        if self._controller.is_busy():
            self._controller.stop()
        if self._audio_radio_session is not None:
            self._audio_radio_session.detach_for_force_close(self)
        elif self._radio_setup.is_applied:
            self._radio_setup.restore()
        if self._owns_radio_thread:
            self._setup_thread.quit()
            self._setup_thread.wait(2000)
        self._controller.shutdown()
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.persist_settings()
        if getattr(self, "_force_close", False):
            super().closeEvent(event)
            self.closed.emit()
            return
        if self._controller.is_busy():
            self._controller.stop()
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_closed_hidden(self)
        elif self._radio_setup.is_applied:
            ok, msg = self._radio_setup.restore()
            if msg and not ok:
                QMessageBox.warning(self, "Audio-Player", msg)
        self.hide()
        event.ignore()
        self.closed.emit()
