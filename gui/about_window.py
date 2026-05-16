"""Info-/About-Fenster für den FT-991A Audio-Profilmanager."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from model._app_paths import installed_icon_path, resource_dir
from version import APP_AUTHOR, APP_COPYRIGHT, APP_DATE, APP_NAME, APP_VERSION

_BOX_STYLE = (
    "QFrame#licBox {"
    " background-color: palette(alternate-base);"
    " border: 1px solid palette(mid);"
    " border-radius: 4px;"
    "}"
    " QFrame#licBox QLabel { background: transparent; border: none; }"
)


def _logo_pixmap(target_dip: int = 88) -> QPixmap:
    candidates: list[Path] = []
    ico = installed_icon_path()
    if ico is not None:
        candidates.append(ico)
    root = resource_dir()
    candidates.extend([root / "logo.ico", root / "logo.svg"])
    for path in candidates:
        if path.is_file():
            pm = QPixmap(str(path))
            if not pm.isNull():
                return pm.scaledToWidth(
                    target_dip,
                    Qt.TransformationMode.SmoothTransformation,
                )
    return QPixmap()


class AboutWindow(QDialog):
    """Info-/About-Fenster mit Logo, Metadaten und Lizenz."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Über {APP_NAME}")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setFixedSize(500, 360)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 12)

        root.addWidget(self._build_header())
        root.addWidget(self._build_license_header())
        root.addWidget(self._build_apache_box())
        root.addStretch(1)
        root.addLayout(self._build_button_row())

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)

        logo_lbl = QLabel()
        pm = _logo_pixmap()
        if not pm.isNull():
            logo_lbl.setPixmap(pm)
            logo_lbl.setFixedSize(pm.size())
        else:
            logo_lbl.setFixedSize(88, 88)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        h.addWidget(logo_lbl, 0, Qt.AlignmentFlag.AlignTop)

        meta = QWidget()
        v = QVBoxLayout(meta)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        lbl_app = QLabel(APP_NAME)
        lbl_app.setStyleSheet("font-size: 18px; font-weight: bold;")
        v.addWidget(lbl_app)
        v.addSpacing(2)

        def _row(label: str, value: str) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label + ":")
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setFixedWidth(70)
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            v.addLayout(row)

        _row("Autor", APP_AUTHOR)
        _row("Version", f"v{APP_VERSION}")
        _row("Datum", APP_DATE)

        h.addWidget(meta, 1, Qt.AlignmentFlag.AlignTop)
        return header

    def _build_license_header(self) -> QLabel:
        lbl = QLabel("Lizenz")
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        return lbl

    def _build_apache_box(self) -> QFrame:
        box = QFrame()
        box.setObjectName("licBox")
        box.setStyleSheet(_BOX_STYLE)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)

        copyright_lbl = QLabel(f"Copyright {APP_COPYRIGHT}")
        copyright_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        lay.addWidget(copyright_lbl)

        lay.addWidget(
            self._rich_label(
                "Lizenziert unter der <b>Apache License, Version 2.0</b>. "
                "Die Nutzung dieser Anwendung ist nur in Übereinstimmung mit dieser "
                "Lizenz gestattet. Eine Kopie der Lizenz liegt der Installation bei "
                "und ist auch online verfügbar."
            )
        )
        lay.addWidget(
            self._rich_label(
                "Die Software wird <i>\"wie besehen\"</i> bereitgestellt, ohne "
                "ausdrückliche oder stillschweigende Gewährleistung jeglicher Art. "
                "Einzelheiten regelt der Lizenztext."
            )
        )
        lay.addWidget(
            self._rich_label(
                "Lizenztext: "
                '<a href="https://www.apache.org/licenses/LICENSE-2.0">'
                "apache.org/licenses/LICENSE-2.0</a><br>"
                "Projekt: "
                '<a href="https://github.com/DK8DE/FT991AudioManager">'
                "github.com/DK8DE/FT991AudioManager</a>"
            )
        )
        return box

    @staticmethod
    def _rich_label(html: str) -> QLabel:
        w = QLabel(html)
        w.setWordWrap(True)
        w.setTextFormat(Qt.TextFormat.RichText)
        w.setOpenExternalLinks(True)
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        w.setStyleSheet("font-size: 11px;")
        return w

    def _build_button_row(self) -> QHBoxLayout:
        btn_ok = QPushButton("Schließen")
        btn_ok.setFixedWidth(90)
        btn_ok.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_ok)
        return row
