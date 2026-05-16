"""Zentrales Theme-Modul (Dark/Light).

Alle Farben sind hier als Konstanten gebündelt — kein verstreutes Hex-Geraffel
mehr in Widget-Dateien. Über :func:`apply_theme` lässt sich das Theme jederzeit
zur Laufzeit umschalten.

Verwendung::

    from gui.theme import apply_theme, is_dark_mode
    apply_theme(app, dark=True)   # Dark erzwingen
    apply_theme(app, dark=False)  # System-Theme nutzen
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


# =====================================================================
# Globale Palette (Qt Color Roles)  — aus der Vorgabe
# =====================================================================

DARK_COLORS: Dict[str, str] = {
    "Window":           "#1E1E1E",
    "WindowText":       "#FFFFFF",
    "Base":             "#2D2D2D",
    "AlternateBase":    "#252525",
    "ToolTipBase":      "#1E1E1E",
    "ToolTipText":      "#FFFFFF",
    "Text":             "#FFFFFF",
    "Button":           "#2D2D2D",
    "ButtonText":       "#FFFFFF",
    "BrightText":       "#FF5050",
    "Link":             "#2196F3",
    "Highlight":        "#2196F3",
    "HighlightedText":  "#FFFFFF",
}

#: Panels, Combos, Tabellen — etwas heller als Fensterhintergrund
PANEL_BG = "#2D2D2D"
PANEL_BORDER = "#3A3A3A"
#: S-Meter / TX-Meter-Balken (dunkler als Panel)
METER_BAR_BG = "#1B1B1B"
METER_TRACK_BG = METER_BAR_BG
#: Speicherkanal-Editor-Tabelle
TABLE_HEADER_BG = "#383838"
TABLE_GRID_COLOR = "#1A1A1A"
ACCENT_BLUE = DARK_COLORS["Highlight"]
ACCENT_GREEN = "#5DDC7A"
#: Inaktiver Slider-Track und Skalenstriche (SQL, NB, …)
SLIDER_INACTIVE = "#9F9F9F"

#: Zusatzfarben für Disabled-Zustand (sanftere Abblendung)
DARK_DISABLED = {
    "WindowText":  "#7A7A7A",
    "Text":        "#7A7A7A",
    "ButtonText":  "#7A7A7A",
    "Highlight":   "#3B3B3B",
}


# =====================================================================
# Sidebar / Navigations-Liste
# =====================================================================

SIDEBAR_BG          = DARK_COLORS["Window"]
SIDEBAR_ITEM_BG     = DARK_COLORS["Base"]
SIDEBAR_TEXT        = DARK_COLORS["WindowText"]
SIDEBAR_SELECTED_BG = DARK_COLORS["Highlight"]
SIDEBAR_SELECTED_FG = DARK_COLORS["HighlightedText"]
SIDEBAR_HOVER_BG    = "#2A2A2A"
SIDEBAR_HOVER_FG    = "#FFFFFF"
SIDEBAR_SEPARATOR   = "#3A3A3A"
SIDEBAR_ITEM_RADIUS = 3  # px


# =====================================================================
# Log-Level-Farben — bewusst auf beiden Backgrounds gut lesbar
# =====================================================================

LOG_COLORS_DARK = {
    "TX":    "#4FA8FF",
    "RX":    "#5DDC7A",
    "INFO":  "#9E9E9E",
    "WARN":  "#FFB050",
    "ERROR": "#FF6E6E",
    "DEBUG": "#7F7F7F",
}

LOG_COLORS_LIGHT = {
    "TX":    "#1565C0",
    "RX":    "#2E7D32",
    "INFO":  "#616161",
    "WARN":  "#EF6C00",
    "ERROR": "#C62828",
    "DEBUG": "#9E9E9E",
}


# =====================================================================
# Status-/LED-Farben (für spätere Versionen 0.4 — TX/PO/SWR-Meter)
# =====================================================================

LED_OK    = "#5DDC7A"   # grün
LED_WARN  = "#FFB050"   # gelb-orange
LED_ALARM = "#FF5050"   # rot
LED_IDLE  = "#5A5A5A"   # grau


# =====================================================================
# Paletten- und Stylesheet-Generierung
# =====================================================================


def make_dark_palette() -> QPalette:
    """Baut die :class:`QPalette` für das Dark-Theme."""
    p = QPalette()
    # Aktive/Inactive Standardfarben
    p.setColor(QPalette.Window,          QColor(DARK_COLORS["Window"]))
    p.setColor(QPalette.WindowText,      QColor(DARK_COLORS["WindowText"]))
    p.setColor(QPalette.Base,            QColor(DARK_COLORS["Base"]))
    p.setColor(QPalette.AlternateBase,   QColor(DARK_COLORS["AlternateBase"]))
    p.setColor(QPalette.ToolTipBase,     QColor(DARK_COLORS["ToolTipBase"]))
    p.setColor(QPalette.ToolTipText,     QColor(DARK_COLORS["ToolTipText"]))
    p.setColor(QPalette.Text,            QColor(DARK_COLORS["Text"]))
    p.setColor(QPalette.Button,          QColor(DARK_COLORS["Button"]))
    p.setColor(QPalette.ButtonText,      QColor(DARK_COLORS["ButtonText"]))
    p.setColor(QPalette.BrightText,      QColor(DARK_COLORS["BrightText"]))
    p.setColor(QPalette.Link,            QColor(DARK_COLORS["Link"]))
    p.setColor(QPalette.Highlight,       QColor(DARK_COLORS["Highlight"]))
    p.setColor(QPalette.HighlightedText, QColor(DARK_COLORS["HighlightedText"]))

    # Disabled-Varianten — etwas zurückhaltender
    p.setColor(QPalette.Disabled, QPalette.WindowText,
               QColor(DARK_DISABLED["WindowText"]))
    p.setColor(QPalette.Disabled, QPalette.Text,
               QColor(DARK_DISABLED["Text"]))
    p.setColor(QPalette.Disabled, QPalette.ButtonText,
               QColor(DARK_DISABLED["ButtonText"]))
    p.setColor(QPalette.Disabled, QPalette.Highlight,
               QColor(DARK_DISABLED["Highlight"]))
    return p


def build_dark_stylesheet() -> str:
    """Stylesheet-Ergänzungen für Details, die die Palette allein nicht trifft.

    Insbesondere: Sidebar-/Listen-Optik mit Hover-Zustand sowie kompaktere
    Tabs, die zur Sidebar-Anmutung passen.
    """
    return f"""
    /* --- Sidebar-/Navigations-Listen --- */
    QListView, QListWidget {{
        background: {SIDEBAR_BG};
        color:      {SIDEBAR_TEXT};
        border:     none;
        border-right: 1px solid {SIDEBAR_SEPARATOR};
        outline: 0;
    }}
    QListView::item, QListWidget::item {{
        background:    {SIDEBAR_ITEM_BG};
        color:         {SIDEBAR_TEXT};
        padding:       6px 10px;
        margin:        2px 4px;
        border-radius: {SIDEBAR_ITEM_RADIUS}px;
    }}
    QListView::item:hover, QListWidget::item:hover {{
        background: {SIDEBAR_HOVER_BG};
        color:      {SIDEBAR_HOVER_FG};
    }}
    QListView::item:selected, QListWidget::item:selected {{
        background: {SIDEBAR_SELECTED_BG};
        color:      {SIDEBAR_SELECTED_FG};
    }}

    /* --- Tabs (wir haben aktuell statt Sidebar Tabs) --- */
    QTabBar::tab {{
        background: {DARK_COLORS["Base"]};
        color:      {DARK_COLORS["WindowText"]};
        padding:    6px 12px;
        margin:     2px 2px 0 2px;
        border:     1px solid {DARK_COLORS["AlternateBase"]};
        border-top-left-radius: {SIDEBAR_ITEM_RADIUS}px;
        border-top-right-radius: {SIDEBAR_ITEM_RADIUS}px;
    }}
    QTabBar::tab:hover {{
        background: {SIDEBAR_HOVER_BG};
        color:      {SIDEBAR_HOVER_FG};
    }}
    QTabBar::tab:selected {{
        background: {SIDEBAR_SELECTED_BG};
        color:      {SIDEBAR_SELECTED_FG};
    }}
    QTabWidget::pane {{
        border: 1px solid {DARK_COLORS["AlternateBase"]};
        background: {DARK_COLORS["Window"]};
    }}

    /* --- Tooltips --- */
    QToolTip {{
        background-color: {DARK_COLORS["ToolTipBase"]};
        color:            {DARK_COLORS["ToolTipText"]};
        border: 1px solid {SIDEBAR_SEPARATOR};
        padding: 4px;
    }}

    /* --- Eingabefelder und Combos --- */
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {DARK_COLORS["Base"]};
        color:      {DARK_COLORS["Text"]};
        border:     1px solid {SIDEBAR_SEPARATOR};
        border-radius: {SIDEBAR_ITEM_RADIUS}px;
        padding:    2px 4px;
        selection-background-color: {DARK_COLORS["Highlight"]};
        selection-color: {DARK_COLORS["HighlightedText"]};
    }}
    QComboBox QAbstractItemView {{
        background: {DARK_COLORS["Base"]};
        color:      {DARK_COLORS["Text"]};
        selection-background-color: {DARK_COLORS["Highlight"]};
        selection-color: {DARK_COLORS["HighlightedText"]};
    }}

    /* --- Buttons --- */
    QPushButton {{
        background: {DARK_COLORS["Button"]};
        color:      {DARK_COLORS["ButtonText"]};
        border:     1px solid {SIDEBAR_SEPARATOR};
        border-radius: {SIDEBAR_ITEM_RADIUS}px;
        padding:    4px 10px;
    }}
    QPushButton:hover  {{ background: {SIDEBAR_HOVER_BG}; color: {SIDEBAR_HOVER_FG}; }}
    QPushButton:pressed{{ background: {DARK_COLORS["Highlight"]}; color: {DARK_COLORS["HighlightedText"]}; }}
    QPushButton:disabled{{ color: {DARK_DISABLED["ButtonText"]}; border-color: #3A3A3A; background: #1F1F1F; }}

    /* --- GroupBox-Titel passend zur Palette --- */
    QGroupBox {{
        border: 1px solid {SIDEBAR_SEPARATOR};
        border-radius: {SIDEBAR_ITEM_RADIUS}px;
        margin-top: 10px;
        padding-top: 6px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 6px;
        color: {DARK_COLORS["WindowText"]};
    }}

    /* --- StatusBar / DockWidget-Titel --- */
    QStatusBar {{ background: {DARK_COLORS["Window"]}; color: {DARK_COLORS["WindowText"]}; }}
    QDockWidget {{ color: {DARK_COLORS["WindowText"]}; }}
    QDockWidget::title {{
        background: {DARK_COLORS["Base"]};
        padding: 4px;
        border-bottom: 1px solid {SIDEBAR_SEPARATOR};
    }}

    /* --- Menü --- */
    QMenuBar, QMenu {{
        background: {DARK_COLORS["Window"]};
        color:      {DARK_COLORS["WindowText"]};
    }}
    QMenuBar::item:selected, QMenu::item:selected {{
        background: {DARK_COLORS["Highlight"]};
        color:      {DARK_COLORS["HighlightedText"]};
    }}

    /* --- Panel-Rahmen (VFO-Zeile, S-Meter, Combos) — nicht für LEDs --- */
    QFrame#panelFrame {{
        background-color: {PANEL_BG};
        border: 1px solid {PANEL_BORDER};
        border-radius: {SIDEBAR_ITEM_RADIUS}px;
    }}

    /* --- Slider (SQL, AGC, MIC, DSP) — hellblauer Track + Griff --- */
    QSlider::groove:vertical {{
        background: {SLIDER_INACTIVE};
        width: 5px;
        border-radius: 2px;
    }}
    QSlider::handle:vertical {{
        background: {ACCENT_BLUE};
        height: 14px;
        width: 14px;
        margin: 0 -5px;
        border-radius: 7px;
        border: none;
    }}
    /* Fusion: sub-page = oberhalb, add-page = unterhalb des Griffs — Blau von unten */
    QSlider::add-page:vertical {{
        background: {ACCENT_BLUE};
        border-radius: 2px;
    }}
    QSlider::sub-page:vertical {{
        background: {SLIDER_INACTIVE};
        border-radius: 2px;
    }}
    QSlider::groove:horizontal {{
        background: #2A2A2A;
        height: 5px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {ACCENT_BLUE};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
        border: none;
    }}
    QSlider::sub-page:horizontal {{
        background: {ACCENT_BLUE};
        border-radius: 2px;
    }}
    QSlider::add-page:horizontal {{
        background: #2A2A2A;
        border-radius: 2px;
    }}

    /* --- AF/RF-Balken --- */
    QProgressBar {{
        border: 1px solid #3A3A3A;
        border-radius: 2px;
        background: {METER_TRACK_BG};
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {ACCENT_BLUE};
        border-radius: 1px;
    }}

    /* --- Tabellen (Speicherkanal-Editor) --- */
    QTableView {{
        background-color: {DARK_COLORS["Base"]};
        alternate-background-color: {DARK_COLORS["AlternateBase"]};
        color: {DARK_COLORS["Text"]};
        gridline-color: {TABLE_GRID_COLOR};
        border: 1px solid {PANEL_BORDER};
        selection-background-color: {DARK_COLORS["Highlight"]};
        selection-color: {DARK_COLORS["HighlightedText"]};
    }}
    QTableView::item {{
        padding: 2px 4px;
    }}
    QHeaderView::section {{
        background-color: {TABLE_HEADER_BG};
        color: {DARK_COLORS["WindowText"]};
        padding: 4px 6px;
        border: none;
        border-right: 1px solid {TABLE_GRID_COLOR};
        border-bottom: 1px solid {TABLE_GRID_COLOR};
    }}
    QTableCornerButton::section {{
        background-color: {TABLE_HEADER_BG};
        border: none;
    }}
    """


# =====================================================================
# Anwenden / Zustand abfragen
# =====================================================================


# Wir merken uns die ursprüngliche Light-Palette und den ursprünglichen Style,
# damit beim Zurückschalten auf System-Theme nichts hängen bleibt.
_default_palette: Optional[QPalette] = None
_default_style: Optional[str] = None
_current_dark: bool = False


def apply_theme(app: QApplication, *, dark: bool) -> None:
    """Wendet das gewünschte Theme auf die Anwendung an.

    - ``dark=True``  → Fusion-Style + Dark-Palette + Dark-Stylesheet
    - ``dark=False`` → ursprüngliche System-Palette + leeres Stylesheet
    """
    global _default_palette, _default_style, _current_dark

    if _default_palette is None:
        _default_palette = QPalette(app.palette())
        _default_style = app.style().objectName() if app.style() else None

    if dark:
        # Fusion ist auf allen Plattformen konsistent dunkel darstellbar.
        if "Fusion" in QStyleFactory.keys():
            app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(make_dark_palette())
        app.setStyleSheet(build_dark_stylesheet())
    else:
        if _default_style and _default_style in QStyleFactory.keys():
            app.setStyle(QStyleFactory.create(_default_style))
        app.setPalette(_default_palette)
        app.setStyleSheet("")

    _current_dark = bool(dark)


def is_dark_mode() -> bool:
    """``True``, wenn zuletzt Dark-Mode angewendet wurde."""
    return _current_dark


def current_log_colors() -> Dict[str, str]:
    """Liefert das passende Log-Farbschema für den aktuellen Modus."""
    return LOG_COLORS_DARK if _current_dark else LOG_COLORS_LIGHT
