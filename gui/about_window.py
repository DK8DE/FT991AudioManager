"""Info-/About-Fenster für den FT-991/A Audiomanager."""

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

from i18n import tr, language_manager
from i18n.retranslatable import RetranslatableMixin
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


class AboutWindow(QDialog, RetranslatableMixin):
    """Info-/About-Fenster mit Logo, Metadaten und Lizenz."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setFixedSize(500, 360)

        self._meta_captions: dict[str, QLabel] = {}
        self._lbl_version_value: QLabel | None = None
        self._lbl_license_heading: QLabel | None = None
        self._lbl_copyright: QLabel | None = None
        self._lbl_apache_intro: QLabel | None = None
        self._lbl_disclaimer: QLabel | None = None
        self._lbl_links: QLabel | None = None
        self._btn_ok: QPushButton | None = None

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 12)

        root.addWidget(self._build_header())
        root.addWidget(self._build_license_header())
        root.addWidget(self._build_apache_box())
        root.addStretch(1)
        root.addLayout(self._build_button_row())

        self._register_retranslate()
        self.retranslate_ui()

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

        def _row(key: str, value: str) -> None:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel()
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
            self._meta_captions[key] = lbl
            if key == "version":
                self._lbl_version_value = val

        _row("author", APP_AUTHOR)
        _row("version", f"v{APP_VERSION}")
        _row("date", APP_DATE)

        h.addWidget(meta, 1, Qt.AlignmentFlag.AlignTop)
        return header

    def _build_license_header(self) -> QLabel:
        lbl = QLabel()
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        self._lbl_license_heading = lbl
        return lbl

    def _build_apache_box(self) -> QFrame:
        box = QFrame()
        box.setObjectName("licBox")
        box.setStyleSheet(_BOX_STYLE)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)

        copyright_lbl = QLabel()
        copyright_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        lay.addWidget(copyright_lbl)
        self._lbl_copyright = copyright_lbl

        intro_lbl = self._rich_label("")
        lay.addWidget(intro_lbl)
        self._lbl_apache_intro = intro_lbl

        disclaimer_lbl = self._rich_label("")
        lay.addWidget(disclaimer_lbl)
        self._lbl_disclaimer = disclaimer_lbl

        links_lbl = self._rich_label("")
        lay.addWidget(links_lbl)
        self._lbl_links = links_lbl
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
        btn_ok = QPushButton()
        btn_ok.setFixedWidth(90)
        btn_ok.clicked.connect(self.accept)
        self._btn_ok = btn_ok
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_ok)
        return row

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("about.title", app_name=APP_NAME))
        for key, lbl in self._meta_captions.items():
            lbl.setText(tr(f"about.{key}") + ":")
        if self._lbl_version_value is not None:
            self._lbl_version_value.setText(
                tr("about.version_value", version=APP_VERSION)
            )
        if self._lbl_license_heading is not None:
            self._lbl_license_heading.setText(tr("about.license_heading"))
        if self._lbl_copyright is not None:
            self._lbl_copyright.setText(
                tr("about.copyright", copyright=APP_COPYRIGHT)
            )
        if self._lbl_apache_intro is not None:
            self._lbl_apache_intro.setText(tr("about.license.apache_intro"))
        if self._lbl_disclaimer is not None:
            self._lbl_disclaimer.setText(tr("about.license.disclaimer"))
        if self._lbl_links is not None:
            self._lbl_links.setText(tr("about.license.links"))
        if self._btn_ok is not None:
            self._btn_ok.setText(tr("dialog.close"))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            language_manager().language_changed.disconnect(self._on_language_changed)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
