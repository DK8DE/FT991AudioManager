"""Fenster für CAT-Audio-Player (MP3/WAV + PTT)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QGroupBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
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
from audio.radio_playback_setup import RadioPlaybackSetup, RadioSetupWorker
from cat import SerialCAT
from model import AppSettings
from model.audio_player_settings import merge_playlist_order, scan_audio_files

from .app_icon import app_icon


def _format_ms(ms: int) -> str:
    ms = max(0, int(ms))
    s = ms // 1000
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


class AudioPlayerWindow(QMainWindow):
    """Audio-Player mit Sendeliste und CAT-PTT."""

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
        self._folder = Path(settings.audio_player.folder_path or "")
        self._playlist_names: list[str] = list(settings.audio_player.playlist_order)

        self.setWindowTitle("FT-991A Audio-Player")
        self.setWindowIcon(app_icon())
        self.resize(520, 560)

        self._controller = PlayerController(self._cat, self)
        self._radio_setup = RadioPlaybackSetup(self._cat)
        self._setup_thread = QThread(self)
        self._setup_worker = RadioSetupWorker(self._radio_setup)
        self._setup_worker.moveToThread(self._setup_thread)
        self._setup_worker.apply_finished.connect(self._on_radio_apply_finished)
        self._setup_worker.restore_finished.connect(self._on_radio_restore_finished)
        self._setup_thread.start()
        self._radio_apply_pending = False

        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.position_changed.connect(self._on_position_changed)
        self._controller.current_file_changed.connect(self._on_current_file)
        self._controller.error.connect(self._on_error)
        self._controller.status_message.connect(self._on_status)

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
        self.list_files.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_files.model().layoutChanged.connect(self._on_list_reordered)
        list_l.addWidget(self.list_files)
        root.addWidget(list_box, stretch=1)

        mode_box = QGroupBox("Wiedergabe")
        mode_l = QVBoxLayout(mode_box)
        self.radio_single = QRadioButton("Nach jeder Datei stoppen (RX)")
        self.radio_playlist = QRadioButton("Alle nacheinander")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.radio_single)
        self._mode_group.addButton(self.radio_playlist)
        self.radio_single.toggled.connect(self._sync_mode_to_controller)
        mode_l.addWidget(self.radio_single)
        mode_l.addWidget(self.radio_playlist)

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

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        time_row = QHBoxLayout()
        self.lbl_elapsed = QLabel("0:00")
        self.lbl_remaining = QLabel("-0:00")
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
        self._sync_timing()
        self._sync_mode_to_controller()
        self.slider_volume.blockSignals(True)
        try:
            self.slider_volume.setValue(ap.volume_percent)
            self.lbl_volume.setText(f"{ap.volume_percent} %")
        finally:
            self.slider_volume.blockSignals(False)
        self._controller.set_volume_percent(ap.volume_percent)

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

    def _on_list_reordered(self, *args) -> None:
        self._playlist_names = [
            self.list_files.item(i).text()
            for i in range(self.list_files.count())
            if self.list_files.item(i) is not None
        ]
        self._settings.audio_player.playlist_order = list(self._playlist_names)
        self._push_playlist_to_controller()

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
        row = self.list_files.currentRow()
        if row < 0 and self.list_files.count() > 0:
            row = 0
            self.list_files.setCurrentRow(0)
        self._controller.play(row if row >= 0 else None)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.list_files.row(item)
        self._controller.play(row)

    def _on_state_changed(self, state: PlayerState) -> None:
        self._update_transport_buttons()

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
        self.radio_single.setEnabled(not busy)
        self.radio_playlist.setEnabled(not busy)
        self.slider_volume.setEnabled(multimedia_available())

    def _on_position_changed(self, pos_ms: int, dur_ms: int) -> None:
        if dur_ms > 0:
            self.progress.setValue(int(1000 * pos_ms / dur_ms))
        else:
            self.progress.setValue(0)
        self.lbl_elapsed.setText(_format_ms(pos_ms))
        rem = max(0, dur_ms - pos_ms)
        self.lbl_remaining.setText(f"-{_format_ms(rem)}")

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
        self._playlist_names = [
            self.list_files.item(i).text()
            for i in range(self.list_files.count())
            if self.list_files.item(i) is not None
        ]
        self._settings.audio_player.playlist_order = list(self._playlist_names)
        self._settings.audio_player.folder_path = (
            str(self._folder) if self._folder.is_dir() else ""
        )
        self._save_geometry()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._radio_setup.is_applied and not self._radio_apply_pending:
            self._request_radio_apply()

    def _request_radio_apply(self) -> None:
        self._radio_apply_pending = True
        self.lbl_status.setText("Funkgerät wird auf DATA-FM / USB (072) geschaltet …")
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_apply",
            Qt.ConnectionType.QueuedConnection,
        )

    def _request_radio_restore(self) -> None:
        if not self._radio_setup.is_applied:
            return
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(
            self._setup_worker,
            "run_restore",
            Qt.ConnectionType.QueuedConnection,
        )

    def _on_radio_apply_finished(self, ok: bool, message: str) -> None:
        self._radio_apply_pending = False
        if message:
            self.lbl_status.setText(message)
        if not ok and message:
            QMessageBox.warning(self, "Audio-Player", message)

    def _on_radio_restore_finished(self, ok: bool, message: str) -> None:
        if message and not ok:
            QMessageBox.warning(self, "Audio-Player", message)

    def force_close(self) -> None:
        self._force_close = True
        if self._controller.is_busy():
            self._controller.stop()
        if self._radio_setup.is_applied:
            self._radio_setup.restore()
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
        if self._radio_setup.is_applied:
            ok, msg = self._radio_setup.restore()
            if msg and not ok:
                QMessageBox.warning(self, "Audio-Player", msg)
        self.hide()
        event.ignore()
        self.closed.emit()
