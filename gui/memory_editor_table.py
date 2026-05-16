"""Tabellenmodell und Delegates fuer den Speicherkanal-Editor."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
)
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QStyledItemDelegate, QTableView

from mapping.memory_tones import (
    CTCSS_TONES_HZ,
    DCS_CODES,
    ToneMode,
    ctcss_labels,
    dcs_labels,
)
from model.memory_editor_channel import (
    EDITOR_MODES,
    MemoryChannelBank,
    MemoryEditorChannel,
    SHIFT_OFFSET_PRESETS_MHZ,
    ShiftDirection,
    editor_mode_label,
    editor_mode_from_label,
)


COLUMN_HEADERS = [
    "Nr",
    "Name",
    "RX MHz",
    "Mode",
    "Ablage",
    "Offset MHz",
    "Ton-Modus",
    "CTCSS Hz",
    "DCS",
    "Notiz",
    "Status",
]

# RX-MHz-Zelle bei doppelter Frequenz
_DUP_FREQ_BG = QColor(255, 210, 210)
_DUP_FREQ_FG = QColor(0, 0, 0)


class MemoryEditorTableModel(QAbstractTableModel):
    def __init__(self, bank: MemoryChannelBank, parent=None) -> None:
        super().__init__(parent)
        self._bank = bank

    @property
    def bank(self) -> MemoryChannelBank:
        return self._bank

    def set_bank(self, bank: MemoryChannelBank) -> None:
        self.beginResetModel()
        self._bank = bank
        self.endResetModel()
        self.refresh_duplicate_highlight()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._bank.channels)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(COLUMN_HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMN_HEADERS[section]
        return str(section + 1)

    def _channel(self, row: int) -> MemoryEditorChannel:
        return self._bank.channels[row]

    def _is_duplicate_freq_cell(self, ch: MemoryEditorChannel) -> bool:
        if ch.rx_frequency_hz <= 0 or ch.is_empty:
            return False
        return ch.rx_frequency_hz in self._bank.duplicate_frequency_hz()

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # noqa: N802
        if not index.isValid():
            return Qt.NoItemFlags
        col = index.column()
        base = (
            Qt.ItemIsSelectable
            | Qt.ItemIsEnabled
            | Qt.ItemIsDragEnabled
            | Qt.ItemIsDropEnabled
        )
        if col in (0, 10):
            return base
        return base | Qt.ItemIsEditable

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        if not index.isValid():
            return None
        ch = self._channel(index.row())
        col = index.column()
        if col == 2:
            if role == Qt.ItemDataRole.BackgroundRole:
                if self._is_duplicate_freq_cell(ch):
                    return QBrush(_DUP_FREQ_BG)
                return QBrush(Qt.BrushStyle.NoBrush)
            if role == Qt.ItemDataRole.ForegroundRole:
                if self._is_duplicate_freq_cell(ch):
                    return _DUP_FREQ_FG
                return None
        if role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        if col == 0:
            return f"{ch.number:03d}"
        if col == 1:
            return ch.name
        if col == 2:
            return f"{ch.rx_frequency_mhz:.6f}"
        if col == 3:
            return editor_mode_label(ch.mode)
        if col == 4:
            return ch.shift_direction.value
        if col == 5:
            if ch.shift_direction == ShiftDirection.SIMPLEX and ch.shift_offset_hz == 0:
                return "0"
            return f"{ch.shift_offset_mhz:.3f}"
        if col == 6:
            return ch.tone_mode.value
        if col == 7:
            return f"{ch.ctcss_tone_hz:.1f}"
        if col == 8:
            return str(ch.dcs_code)
        if col == 9:
            return ch.local_note
        if col == 10:
            return ch.change_status.value
        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:  # noqa: N802
        if not index.isValid():
            return False
        ch = self._channel(index.row())
        col = index.column()
        if role != Qt.EditRole:
            return False
        try:
            if col == 1:
                ch.name = str(value)
                ch.sanitize_name()
            elif col == 2:
                ch.rx_frequency_mhz = float(str(value).replace(",", "."))
            elif col == 3:
                ch.mode = editor_mode_from_label(str(value))
            elif col == 4:
                ch.shift_direction = ShiftDirection(str(value))
            elif col == 5:
                ch.shift_offset_mhz = float(str(value).replace(",", "."))
            elif col == 6:
                ch.tone_mode = ToneMode(str(value))
            elif col == 7:
                ch.ctcss_tone_hz = float(str(value).replace(",", "."))
            elif col == 8:
                ch.dcs_code = int(value)
            elif col == 9:
                ch.local_note = str(value)
            else:
                return False
            # Ohne "Aktiv"-Spalte gelten bearbeitete Einträge als aktiv.
            ch.enabled = True
            ch.mark_changed()
            status_idx = self.index(index.row(), 10)
            self.dataChanged.emit(index, index)
            self.dataChanged.emit(status_idx, status_idx)
            self.refresh_duplicate_highlight()
            return True
        except (ValueError, KeyError):
            return False

    def refresh_duplicate_highlight(self) -> None:
        """RX-MHz-Spalte live neu einfärben (rot nur bei aktivem Duplikat)."""
        if self.rowCount() <= 0:
            return
        top = self.index(0, 2)
        bottom = self.index(self.rowCount() - 1, 2)
        # Alle Rollen — damit Hintergrund/Schrift nach Konfliktende wieder normal werden.
        self.dataChanged.emit(top, bottom)
        view = self.parent()
        if isinstance(view, QTableView):
            view.viewport().update()

    def channel_at_row(self, row: int) -> MemoryEditorChannel:
        return self._channel(row)

    def supportedDragActions(self) -> Qt.DropAction:  # noqa: N802
        return Qt.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:  # noqa: N802
        return Qt.MoveAction

    def moveRows(  # noqa: N802
        self,
        parent: QModelIndex,
        start: int,
        count: int,
        destination: QModelIndex,
        row: int,
    ) -> bool:
        if parent.isValid() or count != 1:
            return False
        dest = row if row < start else row - 1
        if dest < 0 or dest >= len(self._bank.channels) or dest == start:
            return False
        self.beginMoveRows(parent, start, start, parent, row)
        ch = self._bank.channels.pop(start)
        ch.moved = True
        self._bank.channels.insert(dest, ch)
        self._bank.layout_changed = True
        self._bank.renumber()
        self.endMoveRows()
        if self.rowCount() > 0:
            nr_top = self.index(0, 0)
            nr_bot = self.index(self.rowCount() - 1, 0)
            self.dataChanged.emit(nr_top, nr_bot, [Qt.ItemDataRole.DisplayRole])
        self.refresh_duplicate_highlight()
        return True

    def reorder_row(self, source_row: int, dest_row: int) -> bool:
        """Verschiebt eine Zeile auf ``dest_row`` (Zielindex in der Liste)."""
        if source_row == dest_row:
            return False
        dest_child = dest_row + 1 if source_row < dest_row else dest_row
        return self.moveRows(QModelIndex(), source_row, 1, QModelIndex(), dest_child)


class _EditableComboDelegate(QStyledItemDelegate):
    """Dropdown mit Freitext — z. B. Ablage-Offset 0 / 0,600 / 7,600 MHz."""

    def __init__(self, items: list[str], parent=None) -> None:
        super().__init__(parent)
        self._items = items

    def createEditor(self, parent, option, index):  # noqa: ANN001
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self._items)
        le = combo.lineEdit()
        if le is not None:
            le.setPlaceholderText("z. B. 1.250")
        return combo

    def setEditorData(self, editor, index) -> None:  # noqa: ANN001
        text = index.model().data(index, Qt.EditRole)
        editor.setCurrentText(str(text))

    def setModelData(self, editor, model, index) -> None:  # noqa: ANN001
        model.setData(index, editor.currentText().strip(), Qt.EditRole)


class _ComboDelegate(QStyledItemDelegate):
    def __init__(self, items: list[str], parent=None) -> None:
        super().__init__(parent)
        self._items = items

    def createEditor(self, parent, option, index):  # noqa: ANN001
        combo = QComboBox(parent)
        combo.addItems(self._items)
        return combo

    def setEditorData(self, editor, index) -> None:  # noqa: ANN001
        text = index.model().data(index, Qt.EditRole)
        i = editor.findText(str(text))
        editor.setCurrentIndex(i if i >= 0 else 0)

    def setModelData(self, editor, model, index) -> None:  # noqa: ANN001
        model.setData(index, editor.currentText(), Qt.EditRole)


class MemoryEditorTableView(QTableView):
    """Tabelle mit Zeilen-Drag&Drop (eigenes dropEvent — Qt-Standard
    unterstützt QAbstractTableModel nicht zuverlässig)."""

    delete_rows_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setToolTip(
            "Zeile auswählen, gedrückt halten und ziehen, um den Kanal "
            "in der Liste zu verschieben (Nr. wird neu vergeben).\n"
            "Entf: markierte Zeilen leeren."
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if (
            event.key() == Qt.Key.Key_Delete
            and self.state() != QAbstractItemView.State.EditingState
            and self.selectionModel() is not None
            and self.selectionModel().hasSelection()
        ):
            self.delete_rows_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _editor_model(self) -> Optional[MemoryEditorTableModel]:
        model = self.model()
        if isinstance(model, MemoryEditorTableModel):
            return model
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        model = self._editor_model()
        if model is None or event.source() is not self:
            event.ignore()
            return
        selected = self.selectionModel().selectedRows()
        if len(selected) != 1:
            event.ignore()
            return
        source_row = selected[0].row()
        pos = (
            event.position().toPoint()
            if hasattr(event, "position")
            else event.pos()
        )
        drop_index = self.indexAt(pos)
        if drop_index.isValid():
            dest_row = drop_index.row()
        else:
            dest_row = max(0, model.rowCount() - 1)
        if model.reorder_row(source_row, dest_row):
            event.acceptProposedAction()
            self.selectRow(dest_row)
            self.scrollTo(model.index(dest_row, 0))
        else:
            event.ignore()


def attach_delegates(table) -> None:  # noqa: ANN001
    table.setItemDelegateForColumn(
        3,
        _ComboDelegate([editor_mode_label(m) for m in EDITOR_MODES], table),
    )
    table.setItemDelegateForColumn(
        4,
        _ComboDelegate([s.value for s in ShiftDirection], table),
    )
    table.setItemDelegateForColumn(
        5,
        _EditableComboDelegate(list(SHIFT_OFFSET_PRESETS_MHZ), table),
    )
    table.setItemDelegateForColumn(
        6,
        _ComboDelegate([t.value for t in ToneMode], table),
    )
    table.setItemDelegateForColumn(
        7,
        _ComboDelegate(ctcss_labels(), table),
    )
    table.setItemDelegateForColumn(
        8,
        _ComboDelegate(dcs_labels(), table),
    )
