"""Speicherkanal-Editor — Hauptfenster."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal, Optional

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from cat import CatError, FT991CAT, SerialCAT
from gui.app_icon import app_icon
from i18n import tr
from i18n.retranslatable import RetranslatableMixin
from gui.memory_editor_io import (
    backup_path,
    export_csv,
    export_json,
    import_csv,
    import_json,
    load_backup_json,
    save_backup_json,
)
from gui.memory_editor_table import (
    COL_LOCAL_PC_POWER,
    COL_LOCAL_SQL,
    MemoryEditorTableModel,
    MemoryEditorTableView,
    attach_delegates,
    memory_pc_power_value_to_combo,
    memory_sql_value_to_combo,
)
from gui.memory_editor_workers import MemoryEditorWorkerHost
from gui.profile_widget import ProfileWidget
from mapping.rx_mapping import format_frequency_hz
from model import AppSettings
from model._app_paths import app_data_dir
from model.memory_combo_cache import memory_editor_bank_cache_path
from model.memory_editor_channel import (
    MemoryChannelBank,
    MemoryEditorChannel,
    normalize_memory_local_pc_power_value,
    normalize_memory_local_sql_value,
)


class MemoryEditorWindow(QMainWindow, RetranslatableMixin):
    """Editor für Speicherplätze 001..100."""

    def __init__(
        self,
        serial_cat: SerialCAT,
        *,
        profile_widget: Optional[ProfileWidget] = None,
        app_settings: Optional[AppSettings] = None,
        persist_settings: Optional[Callable[[], None]] = None,
        apply_local_memory_overrides: Optional[Callable[[int], None]] = None,
        sync_main_memory_dropdown: Optional[
            Callable[[MemoryChannelBank], None]
        ] = None,
        parent: Optional[QWidget] = None,
        on_closed: Optional[Callable[[], None]] = None,
    ) -> None:
        del parent
        super().__init__(None)
        self.setWindowTitle(tr("memory.window.title"))
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # Kein Parent — frei beweglich, MainWindow kann darüber liegen (s. LogWindow).
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(1280, 720)

        self._cat = serial_cat
        self._profile_widget = profile_widget
        if profile_widget is not None:
            profile_widget.set_cat_blocked(True)
        self._bank = MemoryChannelBank()
        self._host = MemoryEditorWorkerHost(serial_cat, self)
        self._read_progress: Optional[QProgressDialog] = None
        self._write_progress: Optional[QProgressDialog] = None
        self._on_closed = on_closed
        self._closed_notified = False
        self._app_settings = app_settings
        self._persist_settings = persist_settings
        self._apply_local_memory_overrides = apply_local_memory_overrides
        self._sync_main_memory_dropdown = sync_main_memory_dropdown

        self._build_ui()
        self._wire_signals()
        self._bootstrap_initial_data()
        self._register_retranslate()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("memory.window.title"))
        self._lbl_search.setText(tr("memory.label.search"))
        self.search_edit.setPlaceholderText(tr("memory.search.placeholder"))
        self._lbl_band.setText(tr("memory.label.band"))
        cur_band = self.band_filter.currentData()
        self.band_filter.blockSignals(True)
        self.band_filter.clear()
        for key, label_key in self._band_filter_items:
            self.band_filter.addItem(tr(label_key), key)
        idx = self.band_filter.findData(cur_band)
        if idx >= 0:
            self.band_filter.setCurrentIndex(idx)
        self.band_filter.blockSignals(False)
        self._toolbar.setWindowTitle(tr("memory.toolbar.actions"))
        for label_key, btn in self._toolbar_buttons:
            btn.setText(tr(label_key))
        for act, label_key in self._file_menu_actions:
            act.setText(tr(label_key))
        self._file_menu.setTitle(tr("memory.menu.file"))
        self.table.retranslate_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        filter_row = QHBoxLayout()
        self._lbl_search = QLabel(tr("memory.label.search"))
        filter_row.addWidget(self._lbl_search)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("memory.search.placeholder"))
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)

        self.band_filter = QComboBox()
        self._band_filter_items = (
            ("all", "memory.filter.all"),
            ("2m", "memory.filter.2m"),
            ("70cm", "memory.filter.70cm"),
            ("HF", "memory.filter.hf"),
            ("empty", "memory.filter.empty"),
            ("used", "memory.filter.used"),
        )
        for key, label_key in self._band_filter_items:
            self.band_filter.addItem(tr(label_key), key)
        self.band_filter.currentIndexChanged.connect(self._apply_filter)
        self._lbl_band = QLabel(tr("memory.label.band"))
        filter_row.addWidget(self._lbl_band)
        filter_row.addWidget(self.band_filter)

        layout.addLayout(filter_row)

        self.table = MemoryEditorTableView()
        self._model = MemoryEditorTableModel(self._bank, self.table)
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._model.rowsMoved.connect(self._on_rows_moved)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.delete_rows_requested.connect(self._clear_row)
        attach_delegates(self.table)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel(tr("common.ready_with_dot"))
        layout.addWidget(self.status_label)

        self.setCentralWidget(central)
        self._build_toolbar()
        self._build_menu()

    def _build_toolbar(self) -> None:
        self._toolbar = QToolBar(tr("memory.toolbar.actions"))
        self.addToolBar(self._toolbar)
        self._toolbar_buttons: list[tuple[str, QPushButton]] = []
        for label_key, slot in (
            ("memory.btn.reload", self._start_read_from_radio),
            ("memory.btn.save", self._save_to_radio),
            ("memory.btn.export", self._show_export_menu),
            ("memory.btn.import", self._show_import_menu),
            ("memory.btn.move_up", lambda: self._move_row(-1)),
            ("memory.btn.move_down", lambda: self._move_row(1)),
            ("memory.btn.insert", self._insert_row),
            ("memory.btn.clear", self._clear_row),
            ("memory.btn.duplicate", self._duplicate_row),
            ("memory.btn.close_gaps", self._close_gaps),
            ("memory.btn.channel_to_vfo", self._channel_to_vfo),
            ("memory.btn.vfo_to_channel", self._vfo_to_channel),
            ("memory.btn.set_channel", self._set_channel_on_radio),
        ):
            btn = QPushButton(tr(label_key))
            btn.clicked.connect(slot)
            self._toolbar.addWidget(btn)
            self._toolbar_buttons.append((label_key, btn))

    def _build_menu(self) -> None:
        self._file_menu = self.menuBar().addMenu(tr("memory.menu.file"))
        self._file_menu_actions: list[tuple[QAction, str]] = []
        for label_key, slot in (
            ("memory.menu.export_json", self._export_json),
            ("memory.menu.import_json", self._import_json),
            ("memory.menu.export_csv", self._export_csv),
            ("memory.menu.import_csv", self._import_csv),
            ("memory.menu.backup", self._manual_backup),
        ):
            act = QAction(tr(label_key), self)
            act.triggered.connect(slot)
            self._file_menu.addAction(act)
            self._file_menu_actions.append((act, label_key))

    def _wire_signals(self) -> None:
        self._host.read_progress.connect(
            self._on_read_progress, Qt.ConnectionType.QueuedConnection
        )
        self._host.read_finished.connect(
            self._on_read_finished, Qt.ConnectionType.QueuedConnection
        )
        self._host.write_progress.connect(
            self._on_write_progress, Qt.ConnectionType.QueuedConnection
        )
        self._host.write_finished.connect(
            self._on_write_finished, Qt.ConnectionType.QueuedConnection
        )
        self._host.operation_failed.connect(
            self._on_op_failed, Qt.ConnectionType.QueuedConnection
        )
        self._host.connection_lost.connect(
            self._on_connection_lost, Qt.ConnectionType.QueuedConnection
        )
        self._model.memory_local_prefs_changed.connect(
            self._persist_local_memory_mappings
        )

    def _hydrate_memory_local_prefs_from_app_settings(self) -> None:
        """Lokale SQL-/Power-Overrides nach Einlesen vom Funkgerät aus settings.json."""
        if self._app_settings is None:
            return
        smap = self._app_settings.ui.memory_channel_local_sql
        pmap = self._app_settings.ui.memory_channel_local_pc_power
        for ch in self._bank.channels:
            ks = str(ch.number)
            ch.local_sql = normalize_memory_local_sql_value(smap.get(ks))
            raw_pw = pmap.get(ks)
            ch.local_pc_power_watts = normalize_memory_local_pc_power_value(
                raw_pw,
                ch.rx_frequency_hz,
            )

    def _persist_local_memory_mappings(self) -> None:
        """Speichert Kanal-Nr → lokale SQL/Power in AppSettings/settings.json."""
        if self._app_settings is None:
            return
        sm: dict[str, int] = {}
        pm: dict[str, int] = {}
        for ch in self._bank.channels:
            ks = str(ch.number)
            if ch.local_sql is not None:
                v = normalize_memory_local_sql_value(ch.local_sql)
                if v is not None:
                    sm[ks] = v
            if ch.local_pc_power_watts is not None:
                pw = normalize_memory_local_pc_power_value(
                    ch.local_pc_power_watts,
                    ch.rx_frequency_hz,
                )
                if pw is not None:
                    pm[ks] = pw
        self._app_settings.ui.memory_channel_local_sql = sm
        self._app_settings.ui.memory_channel_local_pc_power = pm
        if self._persist_settings is not None:
            self._persist_settings()
        else:
            try:
                self._app_settings.save()
            except OSError:
                pass

    def _refresh_memory_local_column_cells(self) -> None:
        if self._model.rowCount() <= 0:
            return
        tl_sql = self._model.index(0, COL_LOCAL_SQL)
        br_sql = self._model.index(self._model.rowCount() - 1, COL_LOCAL_SQL)
        self._model.dataChanged.emit(tl_sql, br_sql)
        tl_pw = self._model.index(0, COL_LOCAL_PC_POWER)
        br_pw = self._model.index(self._model.rowCount() - 1, COL_LOCAL_PC_POWER)
        self._model.dataChanged.emit(tl_pw, br_pw)

    def _persist_sql_after_bank_structure_change(self) -> None:
        """Nach verschieben/import — Mapping an neue Kanal-Nr. anbinden."""
        self._persist_local_memory_mappings()
        self._refresh_memory_local_column_cells()

    def _selected_rows(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows if rows else [self.table.currentIndex().row()]

    def _hydrate_sql_from_app_settings(self) -> None:
        """Rückwärtskompatibler Aufrufer — nutzt gemeinsame Hydration."""
        self._hydrate_memory_local_prefs_from_app_settings()

    def _persist_local_sql_mapping(self) -> None:
        """Rückwärtskompatibel: schreibt SQL+Power."""
        self._persist_local_memory_mappings()

    def _refresh_sql_local_column_cells(self) -> None:
        self._refresh_memory_local_column_cells()

    def _commit_pending_editor(self) -> None:
        """Offenen Inline-Editor zwingend ins Model schreiben.

        Hintergrund: ``QTableView`` committet die laufende Bearbeitung erst
        beim ``Enter``/``Tab`` oder Fokuswechsel. Klickt der Anwender direkt
        aus der Zelle auf "Speichern" oder "Neu laden", gehen seine
        Änderungen sonst verloren — die Bank speichert dann den alten
        Funkgerät-Inhalt zurueck (Symptom: Slot wird mit den vorher
        gelesenen Daten ueberschrieben).

        Strategie: aktuellen Editor (sofern vorhanden) ueber ``commitData``
        ins Model schreiben und mittels ``closeEditor`` mit
        ``SubmitModelCache`` final schliessen — danach den Fokus zurueck
        zur Tabelle setzen.
        """
        from PySide6.QtWidgets import QAbstractItemDelegate, QApplication

        delegate = self.table.itemDelegate()
        focus = QApplication.focusWidget()
        candidates: list = []
        if focus is not None and focus is not self.table:
            parent = focus.parent()
            if parent is self.table or parent is self.table.viewport():
                candidates.append(focus)
        try:
            persistent = self.table.indexWidget(self.table.currentIndex())
        except Exception:  # noqa: BLE001
            persistent = None
        if persistent is not None and persistent not in candidates:
            candidates.append(persistent)

        for editor in candidates:
            try:
                self.table.commitData(editor)
            except Exception:  # noqa: BLE001
                pass
            try:
                if delegate is not None:
                    delegate.closeEditor.emit(
                        editor, QAbstractItemDelegate.EndEditHint.SubmitModelCache
                    )
            except Exception:  # noqa: BLE001
                pass

        self.table.setFocus()

    def _bootstrap_initial_data(self) -> None:
        """Beim ersten Mal: vom Gerät lesen. Später: lokale Bank, „Neu laden“ = MT."""
        if self._try_load_editor_from_disk_cache():
            return
        QTimer.singleShot(0, self._start_initial_radio_read_without_dirty_prompt)

    def _try_load_editor_from_disk_cache(self) -> bool:
        if self._app_settings is None:
            return False
        if not self._app_settings.ui.memory_editor_disk_cache_ready:
            return False
        path = memory_editor_bank_cache_path()
        if not path.is_file():
            return False
        try:
            bank = load_backup_json(path)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self._bank = bank
        self._bank.layout_changed = False
        self._model.set_bank(self._bank)
        self._hydrate_memory_local_prefs_from_app_settings()
        self._persist_local_memory_mappings()
        self._refresh_memory_local_column_cells()
        self._apply_filter()
        self.status_label.setText(tr("memory.status.cache_loaded"))
        return True

    def _start_initial_radio_read_without_dirty_prompt(self) -> None:
        """Erster Start ohne Disk-Cache: Lesen ohne „Änderungen verwerfen?“-Dialog."""
        self._commit_pending_editor()
        if not self._cat.is_connected():
            self.status_label.setText(tr("memory.status.not_connected"))
            return
        if self._host.is_busy:
            return
        if self._read_progress is not None:
            self._read_progress.close()
        self._read_progress = QProgressDialog(
            tr("memory.progress.read_label"),
            tr("memory.progress.read_cancel"),
            0,
            100,
            self,
        )
        self._read_progress.setWindowTitle(tr("memory.progress.read_first_title"))
        self._read_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._read_progress.setMinimumDuration(0)
        self._read_progress.canceled.connect(self._host.stop)
        self._read_progress.show()
        self.status_label.setText(tr("memory.status.reading"))
        self._host.start_read()

    def _persist_editor_disk_cache_and_flag(self) -> None:
        if self._app_settings is None:
            return
        try:
            save_backup_json(self._bank, memory_editor_bank_cache_path())
            self._app_settings.ui.memory_editor_disk_cache_ready = True
            if self._persist_settings is not None:
                self._persist_settings()
            else:
                self._app_settings.save()
        except OSError:
            pass

    def _notify_main_memory_dropdown(self) -> None:
        if self._sync_main_memory_dropdown is None:
            return
        try:
            self._sync_main_memory_dropdown(self._bank)
        except Exception:  # noqa: BLE001
            pass

    def _start_read_from_radio(self) -> None:
        self._commit_pending_editor()
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                tr("memory.msg.not_connected.title"),
                tr("memory.msg.not_connected"),
            )
            return
        if self._host.is_busy:
            QMessageBox.information(
                self,
                tr("memory.msg.busy.title"),
                tr("memory.msg.busy"),
            )
            return
        if self._bank.changed_channels() or self._bank.any_layout_change():
            ans = QMessageBox.question(
                self,
                tr("memory.msg.reload.title"),
                tr("memory.msg.reload.question"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        if self._read_progress is not None:
            self._read_progress.close()
        self._read_progress = QProgressDialog(
            tr("memory.progress.read_label"),
            tr("memory.progress.read_cancel"),
            0,
            100,
            self,
        )
        self._read_progress.setWindowTitle(tr("memory.progress.read_reload_title"))
        self._read_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._read_progress.setMinimumDuration(0)
        self._read_progress.canceled.connect(self._host.stop)
        self._read_progress.show()
        self.status_label.setText(tr("memory.status.reading"))
        self._host.start_read()

    def _on_read_progress(self, current: int, total: int) -> None:
        if self._read_progress:
            self._read_progress.setMaximum(total)
            self._read_progress.setValue(current)

    def _on_read_finished(self, bank: object) -> None:
        if self._read_progress:
            self._read_progress.close()
            self._read_progress = None
        if isinstance(bank, MemoryChannelBank):
            self._bank = bank
            self._bank.layout_changed = False
            self._model.set_bank(self._bank)
            self._hydrate_sql_from_app_settings()
            self._persist_local_sql_mapping()
            self._refresh_sql_local_column_cells()
            self.status_label.setText(
                tr("memory.status.channels_loaded").format(
                    count=sum(1 for c in self._bank.channels if c.enabled)
                )
            )
            self._apply_filter()
            self._persist_editor_disk_cache_and_flag()
            self._notify_main_memory_dropdown()

    def _save_to_radio(self) -> None:
        self._commit_pending_editor()
        if not self._cat.is_connected():
            QMessageBox.warning(
                self,
                tr("memory.msg.not_connected.title"),
                tr("memory.msg.not_connected"),
            )
            return
        errors = []
        for ch in self._bank.channels:
            if ch.enabled:
                err = ch.validate_name() or ch.validate_frequency()
                if err:
                    errors.append(f"#{ch.number:03d}: {err}")
        if errors:
            QMessageBox.warning(
                self,
                tr("memory.msg.validation.title"),
                tr("memory.msg.validation.prefix").format(
                    errors="\n".join(errors[:8])
                ),
            )
            return

        full_write = self._bank.layout_changed or any(
            ch.moved for ch in self._bank.channels
        )
        channels = self._bank.channels_for_radio_write()
        if not channels:
            QMessageBox.information(
                self, tr("memory.msg.no_changes.title"), tr("memory.msg.no_changes")
            )
            return
        if full_write:
            msg = tr("memory.msg.save.full_write")
        else:
            msg = tr("memory.msg.save.partial").format(count=len(channels))

        if (
            QMessageBox.question(self, tr("memory.progress.write_title"), msg)
            != QMessageBox.StandardButton.Yes
        ):
            return

        backup_dir = app_data_dir() / "memory_backups"
        try:
            save_backup_json(self._bank, backup_path(backup_dir))
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("memory.msg.backup_failed.title"),
                tr("memory.msg.backup_failed").format(error=exc),
            )
            return

        self._write_progress = QProgressDialog(
            tr("memory.progress.write_label"),
            tr("memory.progress.read_cancel"),
            0,
            len(channels),
            self,
        )
        self._write_progress.setWindowTitle(tr("memory.progress.write_title"))
        self._write_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._write_progress.setMinimumDuration(0)
        self._write_progress.setValue(0)
        self._write_progress.canceled.connect(self._host.stop)
        self._write_progress.show()
        from mapping.memory_editor_codec import normalize_channel_for_write

        for ch in channels:
            normalize_channel_for_write(ch)
        # Immer Kanal 001..100 in Reihenfolge ans Gerät
        channels = sorted(channels, key=lambda c: c.number)
        self.status_label.setText(tr("memory.status.writing"))
        self._host.start_write(channels)

    def _on_write_progress(self, current: int, total: int, detail: str) -> None:
        if self._write_progress:
            self._write_progress.setMaximum(total)
            self._write_progress.setValue(current)
            self._write_progress.setLabelText(detail)

    def _on_write_finished(self) -> None:
        if self._write_progress:
            self._write_progress.close()
            self._write_progress = None
        for ch in self._bank.channels:
            ch.changed = False
            ch.moved = False
        self._bank.layout_changed = False
        self._model.set_bank(self._bank)
        QMessageBox.information(
            self,
            tr("memory.msg.write_done.title"),
            tr("memory.msg.write_done"),
        )
        self.status_label.setText(tr("memory.status.saved"))
        self._persist_editor_disk_cache_and_flag()
        self._notify_main_memory_dropdown()

    def _on_op_failed(self, message: str) -> None:
        if self._read_progress:
            self._read_progress.close()
            self._read_progress = None
        if self._write_progress:
            self._write_progress.close()
            self._write_progress = None
        QMessageBox.critical(self, tr("memory.msg.error.title"), message)

    def _on_connection_lost(self) -> None:
        self._on_op_failed(tr("memory.msg.connection_lost"))

    def _on_rows_moved(
        self,
        parent: QModelIndex,
        start: int,
        end: int,
        destination: QModelIndex,
        row: int,
    ) -> None:
        del parent, destination
        if start != end:
            return
        new_row = row if start > row else row - 1
        self._select_row(new_row)
        self.status_label.setText(
            tr("memory.status.row_moved").format(row=new_row + 1)
        )
        self._persist_sql_after_bank_structure_change()

    def _select_row(self, row: int) -> None:
        """Zeile markieren und sichtbar scrollen (für Mehrfach-Verschieben)."""
        if row < 0 or row >= self._model.rowCount():
            return
        self.table.clearSelection()
        idx = self._model.index(row, 0)
        self.table.selectRow(row)
        self.table.setCurrentIndex(idx)
        self.table.scrollTo(idx)

    def _move_row(self, delta: int) -> None:
        row = self._selected_rows()[0]
        if row < 0:
            return
        dest_row = row + delta
        if dest_row < 0 or dest_row >= len(self._bank.channels):
            return
        if self._model.reorder_row(row, dest_row):
            self._select_row(dest_row)
            self.status_label.setText(
                tr("memory.status.row_moved").format(row=dest_row + 1)
            )

    def _insert_row(self) -> None:
        row = self._selected_rows()[0]
        self._bank.insert_at(row)
        self._model.set_bank(self._bank)
        self._persist_sql_after_bank_structure_change()

    def _clear_row(self) -> None:
        for row in reversed(self._selected_rows()):
            self._bank.clear_at(row)
        self._model.set_bank(self._bank)
        self._persist_sql_after_bank_structure_change()

    def _duplicate_row(self) -> None:
        row = self._selected_rows()[0]
        self._bank.duplicate_at(row)
        self._model.set_bank(self._bank)
        self._persist_sql_after_bank_structure_change()

    def _close_gaps(self) -> None:
        self._bank.close_gaps()
        self._model.set_bank(self._bank)
        self._persist_sql_after_bank_structure_change()

    def _channel_to_vfo(self) -> None:
        row = self._selected_rows()[0]
        ch = self._bank.channels[row]
        if ch.rx_frequency_hz <= 0:
            QMessageBox.information(
                self,
                tr("memory.msg.no_frequency.title"),
                tr("memory.msg.no_frequency"),
            )
            return
        ft = FT991CAT(self._cat)
        try:
            ft._cat.send_command(  # noqa: SLF001
                f"FA{ch.rx_frequency_hz:09d};",
                read_response=False,
            )
            from mapping.rx_mapping import format_mode_set
            ft._cat.send_command(format_mode_set(ch.mode), read_response=False)  # noqa: SLF001
            self.status_label.setText(
                tr("memory.status.vfo_set").format(
                    freq=format_frequency_hz(ch.rx_frequency_hz),
                    mode=ch.mode.value,
                )
            )
        except CatError as exc:
            QMessageBox.warning(self, tr("memory.msg.vfo.title"), str(exc))

    def _vfo_to_channel(self) -> None:
        row = self._selected_rows()[0]
        ch = self._bank.channels[row]
        ft = FT991CAT(self._cat)
        try:
            ch.rx_frequency_hz = ft.read_frequency()
            ch.mode = ft.read_rx_mode()
            ch.enabled = True
            ch.shift_offset_hz = ch.suggest_shift_offset_hz()
            if ch.local_pc_power_watts is not None:
                ch.local_pc_power_watts = normalize_memory_local_pc_power_value(
                    ch.local_pc_power_watts,
                    ch.rx_frequency_hz,
                )
            ch.mark_changed()
            self._model.set_bank(self._bank)
        except CatError as exc:
            QMessageBox.warning(self, tr("memory.msg.vfo.title"), str(exc))

    def _set_channel_on_radio(self) -> None:
        """Aktiven Speicherkanal am Funkgerät wählen (Memory-Modus, ``MC``)."""
        row = self._selected_rows()[0]
        if row < 0:
            return
        ch = self._bank.channels[row]
        ft = FT991CAT(self._cat)
        try:
            ft.select_memory_channel(ch.number)
            self.status_label.setText(
                tr("memory.status.radio_channel").format(number=f"{ch.number:03d}")
            )
            if self._apply_local_memory_overrides is not None:
                self._apply_local_memory_overrides(int(ch.number))
        except CatError as exc:
            QMessageBox.warning(self, tr("memory.msg.channel.title"), str(exc))

    def _context_menu(self, pos) -> None:  # noqa: ANN001
        menu = QMenu(self)
        for label_key, slot in (
            ("memory.context.duplicate", self._duplicate_row),
            ("memory.context.clear", self._clear_row),
            ("memory.context.channel_to_vfo", self._channel_to_vfo),
            ("memory.context.vfo_to_channel", self._vfo_to_channel),
            ("memory.context.set_channel", self._set_channel_on_radio),
        ):
            act = menu.addAction(tr(label_key))
            act.triggered.connect(slot)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _apply_filter(self) -> None:
        text = self.search_edit.text().strip().lower()
        band = self.band_filter.currentData()
        for row, ch in enumerate(self._bank.channels):
            hide = False
            if text:
                blob = (
                    f"{ch.name} {ch.rx_frequency_mhz} {ch.local_note}"
                    f" {memory_sql_value_to_combo(ch.local_sql)}"
                    f" {memory_pc_power_value_to_combo(ch.local_pc_power_watts, ch.rx_frequency_hz)}"
                ).lower()
                if text not in blob:
                    hide = True
            if band == "empty" and not ch.is_empty:
                hide = True
            elif band == "used" and ch.is_empty:
                hide = True
            elif band not in ("all", "empty", "used"):
                if ch.detect_band_label() != band:
                    hide = True
            self.table.setRowHidden(row, hide)

    def _export_dir(self) -> str:
        folder = app_data_dir() / "memory_exports"
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def _show_export_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(tr("memory.menu.export_json_action"), self._export_json)
        menu.addAction(tr("memory.menu.export_csv_action"), self._export_csv)
        btn = self.sender()
        if isinstance(btn, QWidget):
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec(QCursor.pos())

    def _show_import_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(tr("memory.menu.import_json_action"), self._import_json)
        menu.addAction(tr("memory.menu.import_csv_action"), self._import_csv)
        btn = self.sender()
        if isinstance(btn, QWidget):
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec(QCursor.pos())

    def _ask_import_mode(self) -> Optional[Literal["replace", "append"]]:
        box = QMessageBox(self)
        box.setWindowTitle(tr("memory.dialog.import.title"))
        box.setText(tr("memory.dialog.import.question"))
        box.setInformativeText(tr("memory.dialog.import.info"))
        replace_btn = box.addButton(
            tr("memory.dialog.import.replace"), QMessageBox.ButtonRole.DestructiveRole
        )
        append_btn = box.addButton(
            tr("memory.dialog.import.append"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(tr("common.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == replace_btn:
            return "replace"
        if clicked == append_btn:
            return "append"
        return None

    def _ask_append_overflow(
        self, import_count: int, free_slots: int
    ) -> Optional[Literal["fill", "cancel"]]:
        box = QMessageBox(self)
        box.setWindowTitle(tr("memory.dialog.import.overflow.title"))
        box.setText(
            tr("memory.dialog.import.overflow.text").format(
                import_count=import_count,
                free_slots=free_slots,
            )
        )
        box.setInformativeText(tr("memory.dialog.import.overflow.info"))
        fill_btn = box.addButton(
            tr("memory.dialog.import.fill_free"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(tr("common.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == fill_btn:
            return "fill"
        return "cancel"

    def _confirm_replace_if_dirty(self) -> bool:
        if not (self._bank.changed_channels() or self._bank.any_layout_change()):
            return True
        ans = QMessageBox.question(
            self,
            tr("memory.dialog.replace_dirty.title"),
            tr("memory.dialog.replace_dirty.question"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return ans == QMessageBox.StandardButton.Yes

    def _apply_import(
        self,
        path: Path,
        loader: Callable[[Path], MemoryChannelBank],
        fmt_label: str,
    ) -> None:
        mode = self._ask_import_mode()
        if mode is None:
            return
        try:
            imported = loader(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self,
                tr("memory.msg.import_read_failed.title"),
                tr("memory.msg.import_read_failed").format(error=exc),
            )
            return

        if mode == "replace":
            if not self._confirm_replace_if_dirty():
                return
            self._bank = imported
            self._bank.layout_changed = True
            detail = tr("memory.import.detail.replaced").format(format=fmt_label)
        else:
            import_count = MemoryChannelBank.count_nonempty_imported(
                imported.channels
            )
            free_slots = self._bank.empty_slot_count()
            if import_count == 0:
                QMessageBox.information(
                    self,
                    tr("memory.msg.import_empty.title"),
                    tr("memory.msg.import_empty"),
                )
                return
            if import_count > free_slots:
                if free_slots == 0:
                    QMessageBox.warning(
                        self,
                        tr("memory.msg.import_no_space.title"),
                        tr("memory.msg.import_no_space").format(count=import_count),
                    )
                    return
                if self._ask_append_overflow(import_count, free_slots) != "fill":
                    return
            appended, skipped = self._bank.append_imported(imported.channels)
            if appended == 0:
                return
            detail = tr("memory.import.detail.appended").format(
                count=appended, format=fmt_label
            )
            if skipped:
                detail += tr("memory.import.detail.skipped").format(skipped=skipped)

        self._model.set_bank(self._bank)
        self._persist_sql_after_bank_structure_change()
        self._apply_filter()
        self.status_label.setText(
            tr("memory.status.imported").format(filename=path.name, detail=detail)
        )

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("memory.file.export_json.title"),
            self._export_dir(),
            tr("memory.file.export_json.filter"),
        )
        if path:
            export_json(self._bank, Path(path))
            self.status_label.setText(
                tr("memory.status.export_json").format(path=path)
            )

    def _import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("memory.file.import_json.title"),
            self._export_dir(),
            tr("memory.file.import_json.filter"),
        )
        if path:
            self._apply_import(Path(path), import_json, "JSON")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("memory.file.export_csv.title"),
            self._export_dir(),
            tr("memory.file.export_csv.filter"),
        )
        if path:
            export_csv(self._bank, Path(path))
            self.status_label.setText(
                tr("memory.status.export_csv").format(path=path)
            )

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("memory.file.import_csv.title"),
            self._export_dir(),
            tr("memory.file.import_csv.filter"),
        )
        if path:
            self._apply_import(Path(path), import_csv, "CSV")

    def _manual_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("memory.file.backup.title"),
            str(app_data_dir() / "memory_backups"),
            tr("memory.file.backup.filter"),
        )
        if path:
            save_backup_json(self._bank, Path(path))
            QMessageBox.information(
                self,
                tr("memory.msg.backup_saved.title"),
                tr("memory.msg.backup_saved").format(path=path),
            )

    def _notify_closed(self) -> None:
        if self._closed_notified:
            return
        self._closed_notified = True
        self._commit_pending_editor()
        self._persist_local_sql_mapping()
        self._host.stop()
        if self._profile_widget is not None:
            self._profile_widget.set_cat_blocked(False)
        if self._on_closed is not None:
            self._on_closed()

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._bank.changed_channels() or self._bank.any_layout_change():
            ans = QMessageBox.question(
                self,
                tr("memory.dialog.close.title"),
                tr("memory.dialog.close.question"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._notify_closed()
        super().closeEvent(event)


def open_memory_editor(
    serial_cat: SerialCAT,
    *,
    profile_widget: Optional[ProfileWidget] = None,
    app_settings: Optional[AppSettings] = None,
    persist_settings: Optional[Callable[[], None]] = None,
    apply_local_memory_overrides: Optional[Callable[[int], None]] = None,
    sync_main_memory_dropdown: Optional[
        Callable[[MemoryChannelBank], None]
    ] = None,
    parent: Optional[QWidget] = None,
    on_closed: Optional[Callable[[], None]] = None,
) -> MemoryEditorWindow:
    """Öffnet den Editor (nicht-modal)."""
    win = MemoryEditorWindow(
        serial_cat,
        profile_widget=profile_widget,
        app_settings=app_settings,
        persist_settings=persist_settings,
        apply_local_memory_overrides=apply_local_memory_overrides,
        sync_main_memory_dropdown=sync_main_memory_dropdown,
        parent=parent,
        on_closed=on_closed,
    )
    win.destroyed.connect(win._notify_closed)
    win.show()
    return win
