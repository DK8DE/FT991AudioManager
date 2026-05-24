"""Ordnerwahl mit sichtbaren kompatiblen Dateien (zur Orientierung im Dialog)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

from i18n import tr

# Windows-Ordnerdialog zeigt keine Dateien — Qt-Dialog mit Filter nutzen.
_USE_QT_FOLDER_DIALOG = True


def _apply_file_dialog_labels(dlg: QFileDialog) -> None:
    """Qt-Dateidialog: UI-Texte (DontUseNativeDialog nutzt sonst Englisch)."""
    dlg.setLabelText(QFileDialog.DialogLabel.LookIn, tr("folder_dialog.look_in"))
    dlg.setLabelText(QFileDialog.DialogLabel.FileName, tr("folder_dialog.file_name"))
    dlg.setLabelText(QFileDialog.DialogLabel.FileType, tr("folder_dialog.file_type"))
    dlg.setLabelText(QFileDialog.DialogLabel.Accept, tr("folder_dialog.accept"))
    dlg.setLabelText(QFileDialog.DialogLabel.Reject, tr("folder_dialog.reject"))


def pick_folder_showing_files(
    parent: Optional[QWidget],
    title: str,
    start_dir: str,
    *,
    name_filter: str,
) -> Optional[str]:
    """Ordner wählen; im Dialog werden passende Dateien zur Kontrolle angezeigt."""
    start = start_dir if start_dir and Path(start_dir).is_dir() else str(Path.home())
    if _USE_QT_FOLDER_DIALOG:
        dlg = QFileDialog(parent, title, start)
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, False)
        dlg.setNameFilter(name_filter)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        _apply_file_dialog_labels(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        selected = dlg.selectedFiles()
        return selected[0] if selected else None
    path = QFileDialog.getExistingDirectory(parent, title, start)
    return path or None


def pick_audio_player_folder(
    parent: Optional[QWidget], start_dir: str
) -> Optional[str]:
    return pick_folder_showing_files(
        parent,
        tr("folder_dialog.player_title"),
        start_dir,
        name_filter=tr("folder_dialog.filter.player"),
    )


def pick_audio_recorder_folder(
    parent: Optional[QWidget], start_dir: str
) -> Optional[str]:
    return pick_folder_showing_files(
        parent,
        tr("folder_dialog.recorder_title"),
        start_dir,
        name_filter=tr("folder_dialog.filter.recorder"),
    )
