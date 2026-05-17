"""Fenster für MP3-Audio-Recorder (Aufnahme + Replay über CAT-PTT)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QByteArray,
    QMetaObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
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
from audio.player_controller import (
    PlayerController,
    PlayerState,
    list_audio_output_devices,
    multimedia_available,
)
from audio.qt_multimedia_lazy import qt_multimedia_types, recorder_import_ok
from audio.radio_playback_setup import (
    RadioPlaybackSetup,
    RadioSetupWorker,
    data_mode_from_string,
)
from cat import SerialCAT
from mapping import TX_STATE_MIC_PTT, TX_STATE_RX
from model import AppSettings
from model.audio_recorder_settings import (
    ALLOWED_BITRATES_KBPS,
    DEFAULT_BITRATE_KBPS,
    default_recordings_folder,
    scan_recordings,
)

from .app_icon import app_icon


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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cat = serial_cat

        folder_str = settings.audio_recorder.folder_path
        if folder_str:
            self._folder = Path(folder_str)
        else:
            self._folder = default_recordings_folder()
            self._settings.audio_recorder.folder_path = str(self._folder)

        self.setWindowTitle("FT-991/A Audio-Recorder")
        self.setWindowIcon(app_icon())
        self.resize(560, 600)

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

        # CAT-Setup (DATA-Mode + EX072=USB), Mode wird mit dem Player geteilt.
        initial_data_mode = data_mode_from_string(settings.audio_player.data_mode)
        self._radio_setup = RadioPlaybackSetup(self._cat, initial_data_mode)
        self._setup_thread = QThread(self)
        self._setup_worker = RadioSetupWorker(self._radio_setup)
        self._setup_worker.moveToThread(self._setup_thread)
        self._setup_worker.apply_finished.connect(self._on_radio_apply_finished)
        self._setup_worker.restore_finished.connect(self._on_radio_restore_finished)
        self._setup_worker.engage_plain_finished.connect(
            self._on_radio_engage_plain_finished
        )
        self._setup_worker.engage_data_finished.connect(
            self._on_radio_engage_data_finished
        )
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
        self._last_player_state: Optional[PlayerState] = None

        self._build_ui()
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
        self.list_files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Doppelt so hoch wie die Qt-Default-QListWidget-Höhe (~200 px), damit
        # man mehr Aufnahmen ohne Scrollen sieht.
        self.list_files.setMinimumHeight(200)
        self.list_files.currentRowChanged.connect(self._on_list_row_changed)
        self.list_files.itemDoubleClicked.connect(self._on_item_double_clicked)
        list_l.addWidget(self.list_files)

        # Buttons direkt unter der Liste: Löschen + lokale PC-Vorhöre (kein TX).
        list_btn_row = QHBoxLayout()
        self.btn_delete = QPushButton("Datei löschen")
        self.btn_delete.setToolTip(
            "Markierte Datei nach Bestätigung dauerhaft löschen. "
            "Während Aufnahme oder laufender Wiedergabe gesperrt."
        )
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        list_btn_row.addWidget(self.btn_delete)

        self.btn_play_pc = QPushButton("Play PC")
        self.btn_play_pc.setToolTip(
            "Markierte Datei lokal über das PC-Ausgabegerät abspielen — "
            "kein CAT, keine PTT, keine Sendung."
        )
        self.btn_play_pc.clicked.connect(self._on_play_pc_clicked)
        list_btn_row.addWidget(self.btn_play_pc)

        self.btn_stop_pc = QPushButton("Stop")
        self.btn_stop_pc.setToolTip("Lokale PC-Wiedergabe stoppen.")
        self.btn_stop_pc.clicked.connect(self._on_stop_pc_clicked)
        list_btn_row.addWidget(self.btn_stop_pc)

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
        rec_row.addWidget(self.btn_record)

        self.btn_stop_rec = QPushButton("Stopp")
        self.btn_stop_rec.setMinimumWidth(96)
        self.btn_stop_rec.clicked.connect(self._on_stop_recording)
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
        rep_l = QHBoxLayout(rep_box)
        self.btn_replay = QPushButton("Replay")
        self.btn_replay.setToolTip(
            "Markierte Aufnahme einmal abspielen (Pre-Roll → CAT-TX → "
            "Datei → zurück auf Sprach-Mode)."
        )
        self.btn_replay.clicked.connect(self._on_replay_clicked)
        rep_l.addWidget(self.btn_replay)

        self.btn_stop_replay = QPushButton("Stopp Replay")
        self.btn_stop_replay.clicked.connect(self._on_stop_replay)
        rep_l.addWidget(self.btn_stop_replay)

        self.lbl_replay_position = QLabel("0:00 / 0:00")
        self.lbl_replay_position.setMinimumWidth(120)
        rep_l.addWidget(self.lbl_replay_position, stretch=1)

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
        self.slider_input_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_input_volume.setRange(0, 100)
        self.slider_input_volume.setValue(100)
        self.slider_input_volume.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_input_volume.setTickInterval(10)
        self.slider_input_volume.setPageStep(10)
        self.slider_input_volume.setToolTip(
            "Aufnahme-Lautstärke des gewählten Aufnahme-Geräts"
        )
        self.slider_input_volume.valueChanged.connect(self._on_input_volume_changed)
        in_vol_row.addWidget(self.slider_input_volume, 1)
        self.lbl_input_volume = QLabel("100 %")
        self.lbl_input_volume.setMinimumWidth(40)
        in_vol_row.addWidget(self.lbl_input_volume)
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
        self.slider_output_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_output_volume.setRange(0, 100)
        self.slider_output_volume.setValue(100)
        self.slider_output_volume.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_output_volume.setTickInterval(10)
        self.slider_output_volume.setPageStep(10)
        self.slider_output_volume.setToolTip(
            "Replay-Lautstärke des gewählten Wiedergabegeräts"
        )
        self.slider_output_volume.valueChanged.connect(self._on_output_volume_changed)
        out_vol_row.addWidget(self.slider_output_volume, 1)
        self.lbl_output_volume = QLabel("100 %")
        self.lbl_output_volume.setMinimumWidth(40)
        out_vol_row.addWidget(self.lbl_output_volume)
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
        self.slider_pc_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_pc_volume.setRange(0, 100)
        self.slider_pc_volume.setValue(100)
        self.slider_pc_volume.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_pc_volume.setTickInterval(10)
        self.slider_pc_volume.setPageStep(10)
        self.slider_pc_volume.setToolTip(
            "Lautstärke der lokalen PC-Vorhöre (Play PC)"
        )
        self.slider_pc_volume.valueChanged.connect(self._on_pc_volume_changed)
        pc_vol_row.addWidget(self.slider_pc_volume, 1)
        self.lbl_pc_volume = QLabel("100 %")
        self.lbl_pc_volume.setMinimumWidth(40)
        pc_vol_row.addWidget(self.lbl_pc_volume)
        dev_l.addLayout(pc_vol_row)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(_form_label("MP3-Bitrate:"))
        self.combo_bitrate = QComboBox()
        for kbps in ALLOWED_BITRATES_KBPS:
            self.combo_bitrate.addItem(f"{kbps} kbps", kbps)
        self.combo_bitrate.currentIndexChanged.connect(self._on_bitrate_changed)
        fmt_row.addWidget(self.combo_bitrate)
        fmt_row.addStretch(1)
        dev_l.addLayout(fmt_row)

        norm_row = QHBoxLayout()
        norm_row.addWidget(_form_label("Lautstärke:"))
        self.chk_normalize = QCheckBox("Soft-Kompressor")
        self.chk_normalize.setToolTip(
            "Hebt nach jeder Aufnahme den RMS-Pegel auf -14 dBFS "
            "(Streaming-Loudness-Norm) an und limitiert Spitzen sanft "
            "auf -1 dBFS. Macht leise FT-991-USB-CODEC-Aufnahmen so "
            "laut wie eine kommerzielle Musik-MP3, ohne harte "
            "Clipping-Verzerrungen.\n\n"
            "Ausgeschaltet = roher Pegel direkt vom USB-Codec."
        )
        self.chk_normalize.toggled.connect(self._on_normalize_toggled)
        norm_row.addWidget(self.chk_normalize, 1)
        dev_l.addLayout(norm_row)

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
            saved = self._settings.audio_recorder.input_device_id
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
            saved = self._settings.audio_recorder.output_device_id
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
            saved = self._settings.audio_recorder.pc_output_device_id
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
        # Pre-Roll für Replay wird mit dem Audio-Player geteilt
        # (settings.audio_player.pre_roll_ms — eigene UI gibt es bewusst nicht).
        self._player.set_timing(self._settings.audio_player.pre_roll_ms, 0)
        # Wiedergabe-Gerät am Controller anwenden (gegen "spielt auf Default-Karte"-Bug)
        saved_out = self.combo_output.currentData()
        if not isinstance(saved_out, str):
            saved_out = ""
        self._player.set_output_device_id(saved_out)
        # Lautstärken: erst UI synchron (ohne Signal-Echo), dann an Backend.
        self.slider_input_volume.blockSignals(True)
        try:
            self.slider_input_volume.setValue(ar.input_volume_percent)
            self.lbl_input_volume.setText(f"{ar.input_volume_percent} %")
        finally:
            self.slider_input_volume.blockSignals(False)
        self._recorder.set_input_volume_percent(ar.input_volume_percent)
        self.slider_output_volume.blockSignals(True)
        try:
            self.slider_output_volume.setValue(ar.output_volume_percent)
            self.lbl_output_volume.setText(f"{ar.output_volume_percent} %")
        finally:
            self.slider_output_volume.blockSignals(False)
        self._player.set_volume_percent(ar.output_volume_percent)
        # PC-Ausgabegerät vormerken — die Anwendung passiert beim
        # ersten Play-PC-Klick (lazy init des PC-Players).
        saved_pc = self.combo_pc_output.currentData()
        if not isinstance(saved_pc, str):
            saved_pc = ""
        self._pc_pending_device_id = saved_pc
        # PC-Volume vormerken (wird beim Lazy-Init des PC-Players angewendet).
        self.slider_pc_volume.blockSignals(True)
        try:
            self.slider_pc_volume.setValue(ar.pc_output_volume_percent)
            self.lbl_pc_volume.setText(f"{ar.pc_output_volume_percent} %")
        finally:
            self.slider_pc_volume.blockSignals(False)
        self._pc_pending_volume_percent = int(ar.pc_output_volume_percent)
        # Soft-Compressor/Normalize-Flag in UI + Recorder.
        self.chk_normalize.blockSignals(True)
        try:
            self.chk_normalize.setChecked(bool(ar.normalize_enabled))
        finally:
            self.chk_normalize.blockSignals(False)
        self._recorder.set_normalize_enabled(bool(ar.normalize_enabled))

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
        path = QFileDialog.getExistingDirectory(self, "Aufnahme-Ordner", start)
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
        paths = [self._folder / n for n in self._files]
        self._player.set_playlist(paths)

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
        # Vorlauf wird vom Audio-Player übernommen — falls dort live geändert,
        # ziehen wir den aktuellen Wert frisch nach.
        self._player.set_timing(self._settings.audio_player.pre_roll_ms, 0)
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

    def _on_player_position(self, pos_ms: int, dur_ms: int) -> None:
        self._duration_ms = max(0, dur_ms)
        self.lbl_replay_position.setText(
            f"{_format_ms(pos_ms)} / {_format_ms(dur_ms)}"
        )

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
        self._settings.audio_recorder.input_device_id = dev_id

    def _on_output_changed(self) -> None:
        dev_id = self.combo_output.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._settings.audio_recorder.output_device_id = dev_id
        self._player.set_output_device_id(dev_id)

    def _on_input_volume_changed(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.lbl_input_volume.setText(f"{value} %")
        self._settings.audio_recorder.input_volume_percent = value
        self._recorder.set_input_volume_percent(value)

    def _on_output_volume_changed(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.lbl_output_volume.setText(f"{value} %")
        self._settings.audio_recorder.output_volume_percent = value
        self._player.set_volume_percent(value)

    def _on_pc_output_changed(self) -> None:
        dev_id = self.combo_pc_output.currentData()
        if not isinstance(dev_id, str):
            dev_id = ""
        self._settings.audio_recorder.pc_output_device_id = dev_id
        self._pc_pending_device_id = dev_id
        if self._pc_player_ready:
            self._apply_pc_output_device(dev_id)

    def _on_pc_volume_changed(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.lbl_pc_volume.setText(f"{value} %")
        self._settings.audio_recorder.pc_output_volume_percent = value
        self._apply_pc_volume(value)

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

    def _on_normalize_toggled(self, checked: bool) -> None:
        # Wirkung ab der nächsten Aufnahme. Während einer laufenden
        # Aufnahme bleibt die zuvor gewählte Einstellung wirksam.
        self._settings.audio_recorder.normalize_enabled = bool(checked)
        self._recorder.set_normalize_enabled(bool(checked))

    # ------------------------------------------------------------------
    # CAT-Setup
    # ------------------------------------------------------------------

    def _apply_or_engage_data(self) -> None:
        """Setup anwenden oder vom Voice-Mode zurück in DATA-Mode wechseln."""
        target = data_mode_from_string(self._settings.audio_player.data_mode)
        if target != self._radio_setup.data_mode:
            self._radio_setup.set_data_mode(target)
        if not self._radio_setup.is_applied:
            self.lbl_status.setText(
                f"Funkgerät wird auf {target.value} / USB (072) geschaltet …"
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
        if not self._radio_setup.is_applied:
            return
        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_restore",
            Qt.ConnectionType.QueuedConnection,
        )

    def _on_radio_apply_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok:
            self._pending_after_apply = ""
            if message:
                QMessageBox.warning(self, "Audio-Recorder", message)
            return
        self._continue_after_data_mode_ready()

    def _on_radio_restore_finished(self, ok: bool, message: str) -> None:
        if message and not ok:
            QMessageBox.warning(self, "Audio-Recorder", message)

    def _on_radio_engage_plain_finished(self, ok: bool, message: str) -> None:
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Recorder", message)

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

        # Lokale PC-Vorhöre — exklusiv zu Aufnahme/Replay.
        self.btn_play_pc.setEnabled(
            multimedia_available()
            and has_selection
            and not any_busy
        )
        self.btn_stop_pc.setEnabled(pc_busy)
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
        if self._recorder.is_busy():
            self._recorder.shutdown()
        if self._player.is_busy():
            self._player.stop()
        self._release_pc_source()
        if self._radio_setup.is_applied:
            self._radio_setup.restore()
        self._setup_thread.quit()
        self._setup_thread.wait(2000)
        self._player.shutdown()
        self.close()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Beim Öffnen wird absichtlich KEIN DATA-Mode angefordert
        # (User-Wunsch — nichts umstellen, bevor er aktiv startet).

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.persist_settings()
        if getattr(self, "_force_close", False):
            super().closeEvent(event)
            self.closed.emit()
            return
        if self._recorder.is_busy():
            self._recorder.stop()
        if self._player.is_busy():
            self._player.stop()
        self._release_pc_source()
        if self._radio_setup.is_applied:
            ok, msg = self._radio_setup.restore()
            if msg and not ok:
                QMessageBox.warning(self, "Audio-Recorder", msg)
        self.hide()
        event.ignore()
        self.closed.emit()
