"""Fenster für CAT-Audio-Player (MP3/WAV + PTT)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QByteArray, QMetaObject, Qt, QThread, QTimer, Q_ARG, QUrl, Signal
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
from audio.qt_multimedia_lazy import qt_multimedia_types
from audio.radio_playback_setup import (
    RadioPlaybackSetup,
    RadioSetupWorker,
    data_mode_for_rx_mode,
    data_mode_from_string,
)
from cat.ft991_cat import FT991CAT
from cat import SerialCAT
from mapping import TX_STATE_MIC_PTT, TX_STATE_RX
from mapping.rx_mapping import RxMode
from model import AppSettings
from model.audio_player_settings import (
    MAX_CONTEST_LISTEN_MS,
    merge_playlist_order,
    scan_audio_files,
)

from .app_icon import app_icon
from .window_lifecycle import application_exit_close_requested

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
        operating_mode_provider: Optional[Callable[[], RxMode]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cat = serial_cat
        self._audio_radio_session = audio_radio_session
        self._operating_mode_provider = operating_mode_provider
        self._folder = Path(settings.audio_player.folder_path or "")
        self._playlist_names: list[str] = list(settings.audio_player.playlist_order)

        self.setWindowTitle("FT-991/A Audio-Player")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.resize(520, 560)

        self._controller = PlayerController(self._cat, self)
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
        self._setup_worker.data_mode_finished.connect(self._on_radio_data_mode_finished)
        self._setup_worker.engage_plain_finished.connect(
            self._on_radio_engage_plain_finished
        )
        self._setup_worker.engage_data_finished.connect(
            self._on_radio_engage_data_finished
        )
        self._setup_worker.pc_menus_finished.connect(self._on_pc_menus_finished)
        if self._owns_radio_thread:
            self._setup_thread.start()
        self._radio_apply_pending = False
        #: Erstes Play nach erfolgreichem ``apply()`` (Fenster öffnet nicht mehr CAT).
        self._defer_play_until_radio_ready = False
        self._pending_play_index: Optional[int] = None
        #: Wenn True, hat MIC-PTT die Wiedergabe unterbrochen — beim nächsten
        #: Play muss erst der DATA-Mode zurückgeschaltet werden.
        self._mic_ptt_interrupted = False
        #: Wiedergabe erst nach erfolgreichem DATA-Mode (async CAT vor PTT).
        self._defer_play_until_engage_data = False
        self._pending_play_index_after_engage: Optional[int] = None
        self._defer_contest_pre_roll_until_engage_data = False

        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.position_changed.connect(self._on_position_changed)
        self._controller.current_file_changed.connect(self._on_current_file)
        self._controller.error.connect(self._on_error)
        self._controller.status_message.connect(self._on_status)
        self._controller.voice_mode_requested.connect(self._on_voice_mode_requested)
        self._controller.contest_pre_roll_requested.connect(
            self._on_contest_pre_roll_requested
        )

        self._pc_player = None  # type: ignore[var-annotated]
        self._pc_audio_out = None  # type: ignore[var-annotated]
        self._pc_player_ready = False
        self._pc_is_playing = False
        self._pc_is_paused = False
        #: Zeilenindex der laufenden PC-Vorhör-Datei (None = keine / unbekannt).
        self._pc_preview_row: Optional[int] = None
        #: PC-Vorhör wurde per Einfachklick auf andere Zeile gestoppt — Doppelklick spielt dann PC.
        self._pc_list_click_stopped = False
        #: Startposition für nächste CAT-Sendung (nach PC-Vorhör / Slider).
        self._pending_play_seek_ms: Optional[int] = None

        self._duration_ms = 0
        self._seek_dragging = False
        self._remaining_warn_active = False
        self._remaining_blink_on = True
        self._last_player_state: Optional[PlayerState] = None
        self._pending_radio_restore_on_close = False
        self._force_close_after_radio_restore = False

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
        list_btn_row = QHBoxLayout()
        self.btn_play_pc = QPushButton("Play PC")
        self.btn_play_pc.setToolTip(
            "Markierte Datei lokal auf dem PC-Ausgabegerät abspielen — "
            "kein CAT, keine Sendung."
        )
        self.btn_play_pc.clicked.connect(self._on_play_pc_clicked)
        list_btn_row.addWidget(self.btn_play_pc)
        self.btn_pause_pc = QPushButton("Pause PC")
        self.btn_pause_pc.setToolTip("PC-Vorhör pausieren oder fortsetzen.")
        self.btn_pause_pc.clicked.connect(self._on_pause_pc_clicked)
        list_btn_row.addWidget(self.btn_pause_pc)
        self.btn_stop_pc = QPushButton("Stopp PC")
        self.btn_stop_pc.setToolTip("PC-Vorhör anhalten (stoppen).")
        self.btn_stop_pc.clicked.connect(self._on_stop_pc_clicked)
        list_btn_row.addWidget(self.btn_stop_pc)
        list_btn_row.addStretch(1)
        list_l.addLayout(list_btn_row)
        root.addWidget(list_box, stretch=1)

        playback_box = QGroupBox("Wiedergabe")
        playback_l = QVBoxLayout(playback_box)

        transport = QHBoxLayout()
        self.btn_play = QPushButton("Start")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stopp")
        self.btn_play.clicked.connect(self._on_play)
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_pause)
        transport.addWidget(self.btn_stop)
        transport.addStretch(1)
        playback_l.addLayout(transport)

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
        playback_l.addWidget(self.progress)

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
        playback_l.addLayout(time_row)

        root.addWidget(playback_box)

        mode_box = QGroupBox("Sende-Ausgabe")
        mode_l = QVBoxLayout(mode_box)

        _LABEL_W = 200

        def _form_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setMinimumWidth(_LABEL_W)
            return lbl

        self.radio_single = QRadioButton("Nach jeder Datei stoppen (RX)")
        self.radio_playlist = QRadioButton("Alle nacheinander")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.radio_single)
        self._mode_group.addButton(self.radio_playlist)
        self.radio_single.toggled.connect(self._sync_mode_to_controller)
        mode_l.addWidget(self.radio_single)
        mode_l.addWidget(self.radio_playlist)

        self.check_contest = QCheckBox("Kontest-Loop")
        self.check_contest.setToolTip(
            "Markierte Datei dauerhaft wiederholen (Auto-Ruf für Contests). "
            "Nach jeder Wiedergabe folgt die eingestellte Hörpause im "
            "Sprach-Mode (USB/LSB/FM), damit Stationen antworten können. "
            "MIC-PTT bricht den Loop ab — kein automatischer Neustart."
        )
        self.check_contest.toggled.connect(self._on_contest_toggled)
        mode_l.addWidget(self.check_contest)

        listen_row = QHBoxLayout()
        listen_row.addWidget(_form_label("Hörpause:"))
        self.spin_contest_listen = QSpinBox()
        self.spin_contest_listen.setRange(0, MAX_CONTEST_LISTEN_MS)
        self.spin_contest_listen.setSuffix(" ms")
        self.spin_contest_listen.setSingleStep(500)
        self.spin_contest_listen.setToolTip(
            "Dauer der Hörpause zwischen den Wiederholungen (Sprach-Mode)."
        )
        self.spin_contest_listen.valueChanged.connect(self._sync_contest_to_controller)
        listen_row.addWidget(self.spin_contest_listen)
        listen_row.addStretch(1)
        mode_l.addLayout(listen_row)

        pre_row = QHBoxLayout()
        pre_row.addWidget(_form_label("Vorlauf:"))
        self.spin_pre_roll = QSpinBox()
        self.spin_pre_roll.setRange(0, 60_000)
        self.spin_pre_roll.setSuffix(" ms")
        self.spin_pre_roll.valueChanged.connect(self._sync_timing)
        pre_row.addWidget(self.spin_pre_roll)
        pre_row.addStretch(1)
        mode_l.addLayout(pre_row)

        gap_row = QHBoxLayout()
        gap_row.addWidget(_form_label("Pause zwischen Dateien:"))
        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(0, 60_000)
        self.spin_gap.setSuffix(" ms")
        self.spin_gap.valueChanged.connect(self._sync_timing)
        gap_row.addWidget(self.spin_gap)
        gap_row.addStretch(1)
        mode_l.addLayout(gap_row)

        dev_row = QHBoxLayout()
        dev_row.addWidget(_form_label("Sende-Ausgabe:"))
        self.combo_output = QComboBox()
        self.combo_output.setToolTip(
            "Soundkarte für die CAT-Sendung (TX / Modulation ins Funkgerät)."
        )
        self._fill_output_devices()
        self.combo_output.currentIndexChanged.connect(self._on_output_changed)
        dev_row.addWidget(self.combo_output, 1)
        mode_l.addLayout(dev_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(_form_label("Sende-Lautstärke:"))
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_volume.setTickInterval(10)
        self.slider_volume.setPageStep(10)
        self.slider_volume.setToolTip(
            "Lautstärke für die CAT-Sendung (QAudioOutput der Sendeliste)."
        )
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.slider_volume, 1)
        self.lbl_volume = QLabel("100 %")
        self.lbl_volume.setMinimumWidth(40)
        vol_row.addWidget(self.lbl_volume)
        mode_l.addLayout(vol_row)

        pc_dev_row = QHBoxLayout()
        pc_dev_row.addWidget(_form_label("PC-Ausgabe:"))
        self.combo_pc_output = QComboBox()
        self.combo_pc_output.setToolTip(
            "Zweites Ausgabegerät für lokale Vorhöre (Play PC) — kein CAT, kein TX."
        )
        self._fill_pc_output_devices()
        self.combo_pc_output.currentIndexChanged.connect(self._on_pc_output_changed)
        pc_dev_row.addWidget(self.combo_pc_output, 1)
        mode_l.addLayout(pc_dev_row)

        pc_vol_row = QHBoxLayout()
        pc_vol_row.addWidget(_form_label("PC-Lautstärke:"))
        self.slider_pc_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_pc_volume.setRange(0, 100)
        self.slider_pc_volume.setValue(100)
        self.slider_pc_volume.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_pc_volume.setTickInterval(10)
        self.slider_pc_volume.setPageStep(10)
        self.slider_pc_volume.setToolTip("Lautstärke der lokalen PC-Vorhöre (Play PC).")
        self.slider_pc_volume.valueChanged.connect(self._on_pc_volume_changed)
        pc_vol_row.addWidget(self.slider_pc_volume, 1)
        self.lbl_pc_volume = QLabel("100 %")
        self.lbl_pc_volume.setMinimumWidth(40)
        pc_vol_row.addWidget(self.lbl_pc_volume)
        mode_l.addLayout(pc_vol_row)

        self.check_tx_monitor_pc = QCheckBox("Ausgabe Mithören")
        self.check_tx_monitor_pc.setToolTip(
            "Während der CAT-Sendung dieselbe Tonspur wie auf der Sende-Soundkarte "
            "zusätzlich auf dem PC-Ausgabegerät ausgeben (gleiche Datei, zweiter Player)."
        )
        self.check_tx_monitor_pc.toggled.connect(self._on_tx_monitor_pc_toggled)
        mode_l.addWidget(self.check_tx_monitor_pc)

        root.addWidget(mode_box)

        self.lbl_status = QLabel("Bereit")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        if not multimedia_available():
            self.lbl_status.setText(
                "Audio-Wiedergabe nicht verfügbar. "
                "pip install PySide6-Addons — App danach neu starten."
            )
            self.btn_play.setEnabled(False)
            self.btn_play_pc.setEnabled(False)
            self.btn_pause_pc.setEnabled(False)
            self.check_tx_monitor_pc.setEnabled(False)

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

    def _fill_pc_output_devices(self) -> None:
        self.combo_pc_output.blockSignals(True)
        try:
            self.combo_pc_output.clear()
            saved = self._settings.audio_player.pc_output_device_id
            select_idx = 0
            for i, (dev_id, label) in enumerate(list_audio_output_devices()):
                self.combo_pc_output.addItem(label, dev_id)
                if dev_id == saved:
                    select_idx = i
            self.combo_pc_output.setCurrentIndex(select_idx)
        finally:
            self.combo_pc_output.blockSignals(False)

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
        saved_pc = self.combo_pc_output.currentData()
        if not isinstance(saved_pc, str):
            saved_pc = ""
        self._pc_pending_device_id = saved_pc
        self.slider_pc_volume.blockSignals(True)
        try:
            pct = self._settings.audio_player.pc_output_volume_percent
            self.slider_pc_volume.setValue(pct)
            self.lbl_pc_volume.setText(f"{pct} %")
        finally:
            self.slider_pc_volume.blockSignals(False)
        self._pc_pending_volume_percent = int(
            self._settings.audio_player.pc_output_volume_percent
        )
        self.check_tx_monitor_pc.blockSignals(True)
        try:
            self.check_tx_monitor_pc.setChecked(
                bool(ap.tx_monitor_to_pc_enabled)
            )
        finally:
            self.check_tx_monitor_pc.blockSignals(False)
        self._apply_tx_monitor_pc_to_controller()

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
        if (
            self._is_pc_active()
            and row >= 0
            and self._pc_preview_row is not None
            and row != self._pc_preview_row
        ):
            self._release_pc_source()
            self._pc_list_click_stopped = True
        elif not self._is_pc_active():
            self._pc_list_click_stopped = False

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

    def _on_pc_output_changed(self) -> None:
        dev_id = self.combo_pc_output.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._settings.audio_player.pc_output_device_id = dev_id
        self._pc_pending_device_id = dev_id
        if self._pc_player_ready:
            self._apply_pc_output_device(dev_id)
        self._controller.set_tx_monitor_pc_device_id(dev_id)

    def _on_pc_volume_changed(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.lbl_pc_volume.setText(f"{value} %")
        self._settings.audio_player.pc_output_volume_percent = value
        self._apply_pc_volume(value)
        self._controller.set_tx_monitor_pc_volume_percent(value)

    def _on_tx_monitor_pc_toggled(self, checked: bool) -> None:
        self._settings.audio_player.tx_monitor_to_pc_enabled = bool(checked)
        self._controller.set_tx_monitor_to_pc_enabled(bool(checked))

    def _apply_tx_monitor_pc_to_controller(self) -> None:
        """PC-Gerät/Lautstärke + Checkbox an PlayerController (CAT-Mithören)."""
        pc_id = self.combo_pc_output.currentData()
        if not isinstance(pc_id, str):
            pc_id = ""
        self._controller.set_tx_monitor_pc_device_id(pc_id)
        self._controller.set_tx_monitor_pc_volume_percent(
            int(self.slider_pc_volume.value())
        )
        self._controller.set_tx_monitor_to_pc_enabled(
            self.check_tx_monitor_pc.isChecked()
        )

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
        if self._controller.is_busy():
            self.lbl_status.setText(
                "Betriebsart-Wechsel während Sendung nicht möglich — bitte stoppen."
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

    def _ensure_pc_player(self) -> bool:
        if self._pc_player_ready:
            return True
        mm = qt_multimedia_types()
        if mm is None:
            QMessageBox.warning(
                self,
                "Audio-Player",
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
        self._pc_player.positionChanged.connect(self._on_pc_media_position)
        self._pc_player.durationChanged.connect(self._on_pc_media_duration)
        self._pc_player.errorOccurred.connect(self._on_pc_player_error)
        self._pc_player_ready = True
        self._apply_pc_output_device(
            getattr(self, "_pc_pending_device_id", "") or ""
        )
        self._apply_pc_volume(
            getattr(self, "_pc_pending_volume_percent", 100)
        )
        return True

    def _apply_pc_volume(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        if not self._pc_player_ready or self._pc_audio_out is None:
            self._pc_pending_volume_percent = percent
            return
        try:
            self._pc_audio_out.setVolume(percent / 100.0)
        except (AttributeError, TypeError):
            pass

    def _apply_pc_output_device(self, device_id: str) -> None:
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

    def _is_pc_paused(self) -> bool:
        return bool(self._pc_is_paused)

    def _is_pc_active(self) -> bool:
        """PC-Vorhör läuft oder ist pausiert (nicht vollständig gestoppt)."""
        return self._is_pc_playing() or self._is_pc_paused()

    def _pc_has_loaded_source(self) -> bool:
        if not self._pc_player_ready or self._pc_player is None:
            return False
        try:
            return not self._pc_player.source().isEmpty()
        except Exception:
            return False

    def _use_pc_progress_for_slider(self) -> bool:
        """Fortschritt vom PC-Player anzeigen (nicht während CAT-Sendung)."""
        if self._controller.is_busy():
            return False
        return self._is_pc_active() or (
            self._pc_preview_row is not None and self._pc_has_loaded_source()
        )

    def _slider_position_ms(self) -> int:
        if self._duration_ms <= 0:
            return 0
        return int(self._duration_ms * self.progress.value() / 1000)

    def _read_pc_position_ms(self) -> int:
        if self._pc_player_ready and self._pc_player is not None:
            try:
                return max(0, int(self._pc_player.position() or 0))
            except Exception:
                pass
        return self._slider_position_ms()

    def _update_progress_ui(self, pos_ms: int, dur_ms: int) -> None:
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

    def _seek_pc_player(self, pos_ms: int) -> None:
        if not self._pc_has_loaded_source():
            return
        assert self._pc_player is not None
        try:
            self._pc_player.setPosition(max(0, int(pos_ms)))
        except Exception:
            pass

    def _on_pc_media_position(self, pos_ms: int) -> None:
        if not self._use_pc_progress_for_slider():
            return
        dur = 0
        if self._pc_player is not None:
            try:
                dur = int(self._pc_player.duration() or 0)
            except Exception:
                pass
        if dur <= 0:
            dur = self._duration_ms
        self._update_progress_ui(max(0, int(pos_ms)), dur)

    def _on_pc_media_duration(self, dur_ms: int) -> None:
        if not self._use_pc_progress_for_slider():
            return
        pos = self._read_pc_position_ms()
        self._update_progress_ui(pos, max(0, int(dur_ms)))

    def _stop_pc_playback_only(self) -> None:
        """PC-Wiedergabe anhalten, Sende-Player (Vorladung/Position) behalten."""
        if not self._pc_player_ready or self._pc_player is None:
            self._pc_is_playing = False
            self._pc_is_paused = False
            return
        try:
            self._pc_player.stop()
        except Exception:
            pass
        self._pc_is_playing = False
        self._pc_is_paused = False

    def _apply_pending_seek_before_play(self, idx: Optional[int]) -> None:
        """Sende-Player auf Zeile + Slider-/PC-Position vor CAT-Start."""
        if idx is not None:
            self._controller.load_track(idx)
        seek_ms = self._pending_play_seek_ms
        if seek_ms is not None and seek_ms > 0:
            self._controller.seek_position_ms(seek_ms)
        self._pending_play_seek_ms = None

    def _on_play_pc_clicked(self) -> None:
        self._pc_list_click_stopped = False
        row = self.list_files.currentRow()
        if (
            self._is_pc_paused()
            and self._pc_preview_row is not None
            and row == self._pc_preview_row
        ):
            self._resume_pc_preview()
            return
        self._start_pc_preview_for_row(row)

    def _resume_pc_preview(self) -> None:
        if not self._pc_player_ready or self._pc_player is None:
            return
        try:
            self._pc_player.play()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Audio-Player",
                f"PC-Wiedergabe konnte nicht fortgesetzt werden:\n{exc}",
            )
            return
        self._pc_is_playing = True
        self._pc_is_paused = False
        name = ""
        if self._pc_preview_row is not None and self._pc_preview_row < len(
            self._playlist_names
        ):
            name = self._playlist_names[self._pc_preview_row]
        self.lbl_status.setText(
            f"PC-Wiedergabe fortgesetzt{': ' + name if name else ''}"
        )
        self._update_transport_buttons()

    def _on_pause_pc_clicked(self) -> None:
        if not self._pc_player_ready or self._pc_player is None:
            return
        mm = qt_multimedia_types()
        if mm is None:
            return
        _QAudioOutput, _QMediaDevices, QMediaPlayer = mm
        if self._is_pc_paused():
            self._resume_pc_preview()
            return
        if not self._is_pc_playing():
            return
        try:
            self._pc_player.pause()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Audio-Player",
                f"PC-Wiedergabe konnte nicht pausiert werden:\n{exc}",
            )
            return
        self._pc_is_playing = False
        self._pc_is_paused = True
        self.lbl_status.setText("PC-Wiedergabe pausiert")
        self._update_transport_buttons()

    def _start_pc_preview_for_row(self, row: int) -> bool:
        self._pc_list_click_stopped = False
        if self._controller.is_busy():
            QMessageBox.information(
                self,
                "Audio-Player",
                "Sendung läuft — bitte zuerst „Stopp“ (CAT-Wiedergabe).",
            )
            return False
        self._sync_playlist_from_list()
        if row < 0 or row >= len(self._playlist_names):
            QMessageBox.information(
                self,
                "Audio-Player",
                "Bitte eine Datei in der Liste markieren.",
            )
            return False
        if not self._folder.is_dir():
            return False
        target = self._folder / self._playlist_names[row]
        if not target.is_file():
            QMessageBox.warning(
                self,
                "Audio-Player",
                f"Datei nicht gefunden:\n{target}",
            )
            self._refresh_file_list()
            return False
        self._controller.load_track(row)
        if not self._ensure_pc_player():
            return False
        assert self._pc_player is not None
        try:
            url = QUrl.fromLocalFile(str(target.resolve()))
            self._pc_player.setSource(url)
            start_ms = self._slider_position_ms() if self._duration_ms > 0 else 0
            self._pc_player.setPosition(start_ms)
            self._pc_player.play()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Audio-Player",
                f"PC-Wiedergabe konnte nicht gestartet werden:\n{exc}",
            )
            return False
        self._pc_is_playing = True
        self._pc_is_paused = False
        self._pc_preview_row = row
        self.lbl_status.setText(f"PC-Wiedergabe: {target.name}")
        self._update_transport_buttons()
        return True

    def _on_stop_pc_clicked(self) -> None:
        self._pc_list_click_stopped = False
        self._release_pc_source()
        self._update_transport_buttons()

    def _on_pc_playback_state_changed(self, state: object) -> None:
        mm = qt_multimedia_types()
        if mm is None:
            return
        _QAudioOutput, _QMediaDevices, QMediaPlayer = mm
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._pc_is_playing = True
            self._pc_is_paused = False
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._pc_is_playing = False
            self._pc_is_paused = True
        else:
            self._pc_is_playing = False
            self._pc_is_paused = False
            if state == QMediaPlayer.PlaybackState.StoppedState:
                self._on_pc_media_position(self._read_pc_position_ms())
        self._update_transport_buttons()

    def _on_pc_player_error(self, _error: object, message: str = "") -> None:
        msg = message or "PC-Wiedergabe fehlgeschlagen."
        QMessageBox.warning(self, "Audio-Player", msg)
        self._pc_is_playing = False
        self._pc_is_paused = False
        self._pc_preview_row = None
        self._update_transport_buttons()

    def _release_pc_source(self) -> None:
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
        self._pc_is_paused = False
        self._pc_preview_row = None

    def _on_pause_clicked(self) -> None:
        if self._controller.state == PlayerState.PAUSED_RX:
            self._on_play()
        else:
            self._controller.pause()

    def _cancel_pending_cat_play_defer(self) -> None:
        """Abbruch: kein Play/Kontest-Vorlauf nach noch laufendem ``run_engage_data``."""
        self._defer_play_until_engage_data = False
        self._pending_play_index_after_engage = None
        self._defer_contest_pre_roll_until_engage_data = False

    def _on_stop_clicked(self) -> None:
        self._cancel_pending_cat_play_defer()
        self._controller.stop()

    def _on_play(self) -> None:
        self._pc_list_click_stopped = False
        self.sync_data_mode_from_main()
        if self._controller.state != PlayerState.PAUSED_RX:
            if self._is_pc_playing():
                self._pending_play_seek_ms = self._read_pc_position_ms()
                self._stop_pc_playback_only()
            elif self._is_pc_paused():
                self._pending_play_seek_ms = self._read_pc_position_ms()
            else:
                self._pending_play_seek_ms = self._slider_position_ms()
        elif self._is_pc_active():
            self._stop_pc_playback_only()
        self._sync_playlist_from_list()
        row = self.list_files.currentRow()
        if row < 0 and self.list_files.count() > 0:
            row = 0
            self.list_files.setCurrentRow(0)
        idx = row if row >= 0 else None

        if not self._radio_setup.is_applied:
            self._defer_play_until_radio_ready = True
            self._pending_play_index = idx
            if not self._radio_apply_pending:
                self._request_radio_apply()
            return

        self._execute_play_with_data_mode(idx)

    def _execute_play_with_data_mode(self, idx: Optional[int]) -> None:
        # Nach Sprach-Mode (Ende letzter Sendung / Kontest-Hörpause): DATA-Mode
        # muss vor PTT fertig sein — run_engage_data läuft async.
        if self._radio_setup.is_applied and not self._radio_setup.in_data_mode:
            self._defer_play_until_engage_data = True
            self._pending_play_index_after_engage = idx
            self.lbl_status.setText(
                f"Schalte zurück auf {self._radio_setup.data_mode.value} …"
            )
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_engage_data",
                Qt.ConnectionType.QueuedConnection,
            )
            return
        self._mic_ptt_interrupted = False
        if self._controller.state != PlayerState.PAUSED_RX:
            self._apply_pending_seek_before_play(idx)
        self._controller.play(idx)

    def _on_contest_pre_roll_requested(self) -> None:
        """Kontest: nach Hörpause erst DATA-Mode, dann Vorlauf/PTT."""
        if self._radio_setup.is_applied and not self._radio_setup.in_data_mode:
            self._defer_contest_pre_roll_until_engage_data = True
            target = self._radio_setup.data_mode.value
            self.lbl_status.setText(f"Loop-Restart — schalte auf {target} …")
            QMetaObject.invokeMethod(
                self._setup_worker,
                "run_engage_data",
                Qt.ConnectionType.QueuedConnection,
            )
            return
        self._controller.begin_pre_roll_now()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self._sync_playlist_from_list()
        row = self.list_files.row(item)
        if row < 0:
            return
        self.list_files.setCurrentRow(row)

        if self._pc_list_click_stopped:
            self._pc_list_click_stopped = False
            self._start_pc_preview_for_row(row)
            return
        if self._is_pc_playing():
            self._start_pc_preview_for_row(row)
            return
        self._on_play()

    def _on_state_changed(self, state: PlayerState) -> None:
        if state != PlayerState.PLAYING:
            self._set_remaining_warn(False)
        self._handle_contest_state_transition(state)
        self._last_player_state = state
        self._update_transport_buttons()
        self._try_complete_radio_restore_after_close()

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
        - Weiter zum Vorlauf/PTT: ``PlayerController.contest_pre_roll_requested``
          — das Fenster schaltet zuerst DATA-Mode, dann ``begin_pre_roll_now``.
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

    def _update_transport_buttons(self) -> None:
        st = self._controller.state
        busy = self._controller.is_busy()
        pc_busy = self._is_pc_playing()
        pc_paused = self._is_pc_paused()
        pc_active = self._is_pc_active()
        has_selection = (
            self.list_files.currentRow() >= 0
            and self.list_files.currentRow() < len(self._playlist_names)
        )
        self.btn_play.setEnabled(
            multimedia_available()
            and bool(self._playlist_names)
            and st in (PlayerState.IDLE, PlayerState.PAUSED_RX)
            and not pc_busy
        )
        self.btn_play_pc.setEnabled(
            multimedia_available()
            and bool(self._playlist_names)
            and has_selection
            and not busy
        )
        self.btn_pause_pc.setEnabled(
            multimedia_available() and pc_active and self._pc_has_loaded_source()
        )
        self.btn_pause_pc.setText("Fortsetzen PC" if pc_paused else "Pause PC")
        self.btn_stop_pc.setEnabled(pc_active)
        self.btn_pause.setEnabled(
            st in (PlayerState.PLAYING, PlayerState.PAUSED_RX) and not pc_busy
        )
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
        self.btn_folder.setEnabled(not busy and not pc_busy)
        self.btn_refresh.setEnabled(not busy and not pc_busy)
        contest_on = self.check_contest.isChecked()
        self.radio_single.setEnabled(not busy and not contest_on and not pc_busy)
        self.radio_playlist.setEnabled(not busy and not contest_on and not pc_busy)
        self.check_contest.setEnabled(not busy and not pc_busy)
        self.spin_contest_listen.setEnabled(contest_on and not busy and not pc_busy)
        self.slider_volume.setEnabled(multimedia_available() and not pc_busy)
        self.combo_output.setEnabled(not pc_busy)
        self.combo_pc_output.setEnabled(not pc_busy)
        self.slider_pc_volume.setEnabled(not busy)
        self.check_tx_monitor_pc.setEnabled(multimedia_available())
        self.progress.setEnabled(
            multimedia_available()
            and self._duration_ms > 0
            and (not busy or pc_busy or self._pc_has_loaded_source())
        )

    def _on_position_changed(self, pos_ms: int, dur_ms: int) -> None:
        if self._use_pc_progress_for_slider():
            return
        self._update_progress_ui(pos_ms, dur_ms)

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
        if self._use_pc_progress_for_slider():
            self._seek_pc_player(pos_ms)
        if not self._controller.is_busy():
            self._controller.seek_position_ms(pos_ms)
        self.lbl_elapsed.setText(_format_ms(pos_ms))
        rem = max(0, self._duration_ms - pos_ms)
        self.lbl_remaining.setText(f"-{_format_ms(rem)}")
        self._update_remaining_warn(rem)

    def _apply_seek_from_slider(self) -> None:
        if self._duration_ms <= 0:
            return
        pos_ms = self._slider_position_ms()
        if self._use_pc_progress_for_slider():
            self._seek_pc_player(pos_ms)
        if not self._controller.is_busy():
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
        ap = self._settings.audio_player
        ap.folder_path = str(self._folder) if self._folder.is_dir() else ""
        pc_id = self.combo_pc_output.currentData()
        ap.pc_output_device_id = pc_id if isinstance(pc_id, str) else ""
        ap.pc_output_volume_percent = max(0, min(100, int(self.slider_pc_volume.value())))
        ap.tx_monitor_to_pc_enabled = bool(self.check_tx_monitor_pc.isChecked())
        self._save_geometry()

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

    def _on_pc_menus_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message and self._audio_radio_session is None:
            QMessageBox.warning(self, "Audio-Player", message)

    def _request_radio_apply(self) -> None:
        self._radio_apply_pending = True
        self.sync_data_mode_from_main()
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

    def _radio_transmit_activity_busy(self) -> bool:
        """CAT-Sendung / Replay läuft noch (PTT oder Wiedergabe-Pipeline)."""
        return self._controller.is_busy()

    def _request_radio_restore_on_close(self) -> None:
        """Nach Stopp: Funkgerät/Menüs zurück, wenn kein Audio-Fenster mehr offen."""
        if self._audio_radio_session is not None:
            self._audio_radio_session.request_restore_if_no_windows()
        elif self._radio_setup.is_applied:
            ok, msg = self._radio_setup.restore()
            if msg and not ok:
                QMessageBox.warning(self, "Audio-Player", msg)

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
        """Fenster zu: erst Sendung beenden, dann Mode/Menüs zurück."""
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)
        if self._radio_transmit_activity_busy():
            self._pending_radio_restore_on_close = True
            self.lbl_status.setText("Sendung wird beendet — Mode wird zurückgestellt …")
            self._controller.stop()
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
        self._controller.shutdown()
        self.close()

    def _on_radio_apply_finished(self, ok: bool, message: str) -> None:
        self._radio_apply_pending = False
        if message:
            self.lbl_status.setText(message)
        if not ok and message and self._audio_radio_session is None:
            QMessageBox.warning(self, "Audio-Player", message)
        if not ok:
            self._defer_play_until_radio_ready = False
            self._pending_play_index = None
            self._cancel_pending_cat_play_defer()
            return
        if self._defer_play_until_radio_ready:
            self._defer_play_until_radio_ready = False
            idx = self._pending_play_index
            self._pending_play_index = None
            self._execute_play_with_data_mode(idx)

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
        self._try_complete_radio_restore_after_close()

    def _on_radio_engage_data_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Player", message)
        if not ok:
            self._cancel_pending_cat_play_defer()
            return
        if self._defer_contest_pre_roll_until_engage_data:
            self._defer_contest_pre_roll_until_engage_data = False
            self._controller.begin_pre_roll_now()
            return
        if self._defer_play_until_engage_data:
            self._defer_play_until_engage_data = False
            idx = self._pending_play_index_after_engage
            self._pending_play_index_after_engage = None
            self._mic_ptt_interrupted = False
            if self._controller.state != PlayerState.PAUSED_RX:
                self._apply_pending_seek_before_play(idx)
            self._controller.play(idx)

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
            self._cancel_pending_cat_play_defer()
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
        self._cancel_pending_cat_play_defer()
        self._release_pc_source()
        if self._audio_radio_session is not None:
            self._audio_radio_session.on_window_hidden(self)
        if self._radio_transmit_activity_busy():
            self._pending_radio_restore_on_close = True
            self._force_close_after_radio_restore = True
            self._controller.stop()
            return
        self._finish_force_close()

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
        self._cancel_pending_cat_play_defer()
        self._release_pc_source()
        self._begin_radio_restore_on_close()
        self.hide()
        event.ignore()
        self.closed.emit()
