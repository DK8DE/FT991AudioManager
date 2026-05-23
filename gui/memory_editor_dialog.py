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


class MemoryEditorWindow(QMainWindow):
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
        self.setWindowTitle("FT-991/A Speicherkanal-Editor")
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

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Suche:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Name, Frequenz, Notiz, SQL/Power (Local) …"
        )
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit, 1)

        self.band_filter = QComboBox()
        self.band_filter.addItems(["Alle", "2m", "70cm", "HF", "leer", "belegt"])
        self.band_filter.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(QLabel("Band:"))
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

        self.status_label = QLabel("Bereit.")
        layout.addWidget(self.status_label)

        self.setCentralWidget(central)
        self._build_toolbar()
        self._build_menu()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Aktionen")
        self.addToolBar(tb)
        for text, slot in (
            ("Neu laden", self._start_read_from_radio),
            ("Speichern", self._save_to_radio),
            ("Exportieren", self._show_export_menu),
            ("Importieren", self._show_import_menu),
            ("Nach oben", lambda: self._move_row(-1)),
            ("Nach unten", lambda: self._move_row(1)),
            ("Einfügen", self._insert_row),
            ("Leeren", self._clear_row),
            ("Duplizieren", self._duplicate_row),
            ("Lücken schließen", self._close_gaps),
            ("Kanal → VFO", self._channel_to_vfo),
            ("VFO → Kanal", self._vfo_to_channel),
            ("Kanal setzen", self._set_channel_on_radio),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            tb.addWidget(btn)

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Datei")
        for label, slot in (
            ("&JSON exportieren …", self._export_json),
            ("&JSON importieren …", self._import_json),
            ("&CSV exportieren …", self._export_csv),
            ("&CSV importieren …", self._import_csv),
            ("&Sicherung erstellen …", self._manual_backup),
        ):
            act = QAction(label, self)
            act.triggered.connect(slot)
            menu.addAction(act)

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
        self.status_label.setText(
            "Liste aus lokalem Zwischenspeicher — „Neu laden“ liest vom Gerät."
        )
        return True

    def _start_initial_radio_read_without_dirty_prompt(self) -> None:
        """Erster Start ohne Disk-Cache: Lesen ohne „Änderungen verwerfen?“-Dialog."""
        self._commit_pending_editor()
        if not self._cat.is_connected():
            self.status_label.setText("Nicht verbunden — Speicher nicht gelesen.")
            return
        if self._host.is_busy:
            return
        if self._read_progress is not None:
            self._read_progress.close()
        self._read_progress = QProgressDialog(
            "Speicherkanäle lesen …", "Abbrechen", 0, 100, self
        )
        self._read_progress.setWindowTitle("Erstes Einlesen")
        self._read_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._read_progress.setMinimumDuration(0)
        self._read_progress.canceled.connect(self._host.stop)
        self._read_progress.show()
        self.status_label.setText("Lese Speicherkanäle vom Gerät …")
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
            QMessageBox.warning(self, "Nicht verbunden", "Keine CAT-Verbindung.")
            return
        if self._host.is_busy:
            QMessageBox.information(
                self,
                "Lädt …",
                "Ein Lese- oder Schreibvorgang läuft bereits.",
            )
            return
        if self._bank.changed_channels() or self._bank.any_layout_change():
            ans = QMessageBox.question(
                self,
                "Neu laden",
                "Ungespeicherte Änderungen verwerfen und alle Kanäle "
                "vom Funkgerät neu einlesen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        if self._read_progress is not None:
            self._read_progress.close()
        self._read_progress = QProgressDialog(
            "Speicherkanäle lesen …", "Abbrechen", 0, 100, self
        )
        self._read_progress.setWindowTitle("Neu laden")
        self._read_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._read_progress.setMinimumDuration(0)
        self._read_progress.canceled.connect(self._host.stop)
        self._read_progress.show()
        self.status_label.setText("Lese Speicherkanäle vom Gerät …")
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
                f"{sum(1 for c in self._bank.channels if c.enabled)} "
                f"belegte Kanäle geladen."
            )
            self._apply_filter()
            self._persist_editor_disk_cache_and_flag()
            self._notify_main_memory_dropdown()

    def _save_to_radio(self) -> None:
        self._commit_pending_editor()
        if not self._cat.is_connected():
            QMessageBox.warning(self, "Nicht verbunden", "Keine CAT-Verbindung.")
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
                "Validierung",
                "Bitte korrigieren:\n" + "\n".join(errors[:8]),
            )
            return

        full_write = self._bank.layout_changed or any(
            ch.moved for ch in self._bank.channels
        )
        channels = self._bank.channels_for_radio_write()
        if not channels:
            QMessageBox.information(self, "Speichern", "Keine Änderungen.")
            return
        if full_write:
            msg = (
                "Die Kanal-Reihenfolge wurde geändert.\n"
                "Alle 100 Speicherplätze werden neu ins Gerät geschrieben.\n\n"
                "Vorher wird eine Sicherung angelegt. Fortfahren?"
            )
        else:
            msg = (
                f"{len(channels)} geänderte Kanäle an das Gerät senden?\n"
                f"Vorher wird eine Sicherung angelegt."
            )

        if QMessageBox.question(self, "Speichern", msg) != QMessageBox.StandardButton.Yes:
            return

        backup_dir = app_data_dir() / "memory_backups"
        try:
            save_backup_json(self._bank, backup_path(backup_dir))
        except OSError as exc:
            QMessageBox.warning(self, "Sicherung", f"Sicherung fehlgeschlagen: {exc}")
            return

        self._write_progress = QProgressDialog(
            "Schreibe Speicherkanäle …", "Abbrechen", 0, len(channels), self
        )
        self._write_progress.setWindowTitle("Speichern")
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
        self.status_label.setText("Schreibe … (VFO-Modus wird gesetzt)")
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
        QMessageBox.information(self, "Speichern", "Schreiben abgeschlossen.")
        self.status_label.setText("Gespeichert.")
        self._persist_editor_disk_cache_and_flag()
        self._notify_main_memory_dropdown()

    def _on_op_failed(self, message: str) -> None:
        if self._read_progress:
            self._read_progress.close()
            self._read_progress = None
        if self._write_progress:
            self._write_progress.close()
            self._write_progress = None
        QMessageBox.critical(self, "Fehler", message)

    def _on_connection_lost(self) -> None:
        self._on_op_failed("CAT-Verbindung verloren.")

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
            f"Kanal in Zeile {new_row + 1} verschoben (Nr. neu vergeben)."
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
                f"Kanal in Zeile {dest_row + 1} verschoben (Nr. neu vergeben)."
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
                "Kanal → VFO",
                "Dieser Eintrag hat keine gültige Frequenz (> 0 MHz).",
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
                f"VFO: {format_frequency_hz(ch.rx_frequency_hz)} {ch.mode.value}"
            )
        except CatError as exc:
            QMessageBox.warning(self, "VFO", str(exc))

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
            QMessageBox.warning(self, "VFO", str(exc))

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
                f"Funkgerät auf Speicherkanal {ch.number:03d} geschaltet."
            )
            if self._apply_local_memory_overrides is not None:
                self._apply_local_memory_overrides(int(ch.number))
        except CatError as exc:
            QMessageBox.warning(self, "Speicherkanal", str(exc))

    def _context_menu(self, pos) -> None:  # noqa: ANN001
        menu = QMenu(self)
        for label, slot in (
            ("Duplizieren", self._duplicate_row),
            ("Leeren", self._clear_row),
            ("Kanal → VFO", self._channel_to_vfo),
            ("VFO → Kanal", self._vfo_to_channel),
            ("Kanal setzen", self._set_channel_on_radio),
        ):
            act = menu.addAction(label)
            act.triggered.connect(slot)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _apply_filter(self) -> None:
        text = self.search_edit.text().strip().lower()
        band = self.band_filter.currentText()
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
            if band == "leer" and not ch.is_empty:
                hide = True
            elif band == "belegt" and ch.is_empty:
                hide = True
            elif band not in ("Alle", "leer", "belegt"):
                if ch.detect_band_label() != band:
                    hide = True
            self.table.setRowHidden(row, hide)

    def _export_dir(self) -> str:
        folder = app_data_dir() / "memory_exports"
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def _show_export_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Als JSON (.json) …", self._export_json)
        menu.addAction("Als CSV (.csv, Semikolon für Excel) …", self._export_csv)
        btn = self.sender()
        if isinstance(btn, QWidget):
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec(QCursor.pos())

    def _show_import_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Aus JSON (.json) …", self._import_json)
        menu.addAction("Aus CSV (.csv, Semikolon) …", self._import_csv)
        btn = self.sender()
        if isinstance(btn, QWidget):
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            menu.exec(QCursor.pos())

    def _ask_import_mode(self) -> Optional[Literal["replace", "append"]]:
        box = QMessageBox(self)
        box.setWindowTitle("Importieren")
        box.setText("Wie soll die Datei eingespielt werden?")
        box.setInformativeText(
            "Alles ersetzen: die komplette Liste (100 Kanäle) wird durch "
            "den Inhalt der Datei ersetzt.\n\n"
            "Anhängen: nur belegte Kanäle aus der Datei werden in freie "
            "Slots der aktuellen Liste eingefügt."
        )
        replace_btn = box.addButton("Alles ersetzen", QMessageBox.ButtonRole.DestructiveRole)
        append_btn = box.addButton("Anhängen", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
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
        box.setWindowTitle("Importieren — nicht genug Platz")
        box.setText(
            f"In der Datei sind {import_count} belegte Kanäle, "
            f"frei sind nur {free_slots} Speicherplätze."
        )
        box.setInformativeText(
            "„Freie belegen“ fügt so viele Kanäle wie möglich in freie "
            "Slots ein; der Rest der Datei wird nicht übernommen."
        )
        fill_btn = box.addButton(
            "Freie belegen (bis voll)", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == fill_btn:
            return "fill"
        return "cancel"

    def _confirm_replace_if_dirty(self) -> bool:
        if not (self._bank.changed_channels() or self._bank.any_layout_change()):
            return True
        ans = QMessageBox.question(
            self,
            "Liste ersetzen",
            "Die aktuelle Liste hat ungespeicherte Änderungen.\n"
            "Trotzdem alles ersetzen?",
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
                self, "Importieren", f"Datei konnte nicht gelesen werden:\n{exc}"
            )
            return

        if mode == "replace":
            if not self._confirm_replace_if_dirty():
                return
            self._bank = imported
            self._bank.layout_changed = True
            detail = f"Liste ersetzt ({fmt_label})"
        else:
            import_count = MemoryChannelBank.count_nonempty_imported(
                imported.channels
            )
            free_slots = self._bank.empty_slot_count()
            if import_count == 0:
                QMessageBox.information(
                    self,
                    "Importieren",
                    "Die Datei enthält keine belegten Kanäle.",
                )
                return
            if import_count > free_slots:
                if free_slots == 0:
                    QMessageBox.warning(
                        self,
                        "Importieren",
                        f"Die Datei enthält {import_count} belegte Kanäle, "
                        "es ist aber kein freier Speicherplatz mehr vorhanden.",
                    )
                    return
                if self._ask_append_overflow(import_count, free_slots) != "fill":
                    return
            appended, skipped = self._bank.append_imported(imported.channels)
            if appended == 0:
                return
            detail = f"{appended} Kanal/Kanäle angehängt ({fmt_label})"
            if skipped:
                detail += f", {skipped} nicht übernommen (Speicher voll)"

        self._model.set_bank(self._bank)
        self._persist_sql_after_bank_structure_change()
        self._apply_filter()
        self.status_label.setText(f"Importiert: {path.name} — {detail}")

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Liste als JSON speichern",
            self._export_dir(),
            "JSON (*.json)",
        )
        if path:
            export_json(self._bank, Path(path))
            self.status_label.setText(f"Exportiert (JSON): {path}")

    def _import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Liste aus JSON laden",
            self._export_dir(),
            "JSON (*.json)",
        )
        if path:
            self._apply_import(Path(path), import_json, "JSON")

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Liste als CSV speichern",
            self._export_dir(),
            "CSV für Excel (*.csv)",
        )
        if path:
            export_csv(self._bank, Path(path))
            self.status_label.setText(f"Exportiert (CSV): {path}")

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Liste aus CSV laden",
            self._export_dir(),
            "CSV für Excel (*.csv)",
        )
        if path:
            self._apply_import(Path(path), import_csv, "CSV")

    def _manual_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Sicherung speichern", str(app_data_dir() / "memory_backups"), "JSON (*.json)"
        )
        if path:
            save_backup_json(self._bank, Path(path))
            QMessageBox.information(self, "Sicherung", f"Gespeichert:\n{path}")

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
                "Schließen",
                "Ungespeicherte Änderungen verwerfen?",
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
