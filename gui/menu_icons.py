"""Standard-Icons für Hauptmenü-Aktionen (Qt / Freedesktop mit Fallback)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QPushButton, QStyle

_CONTROL_BAR_ICON_PX = 18
_TRANSPORT_BTN_ICON_PX = 16


def menu_action_icon(
    standard: QStyle.StandardPixmap,
    *,
    theme_name: str = "",
) -> QIcon:
    """Freedesktop-Icon, falls vorhanden; sonst ``QStyle.standardIcon``."""
    if theme_name:
        themed = QIcon.fromTheme(theme_name)
        if not themed.isNull():
            return themed
    app = QApplication.instance()
    style = app.style() if isinstance(app, QApplication) else None
    if style is not None:
        return style.standardIcon(standard)
    return QIcon()


def control_bar_icon_size() -> QSize:
    """Empfohlene Icon-Größe für Buttons in der Radio-Control-Bar."""
    return QSize(_CONTROL_BAR_ICON_PX, _CONTROL_BAR_ICON_PX)


def _control_bar_icon(
    draw: Callable[[QPainter, int], None],
    *,
    logical_px: int = _CONTROL_BAR_ICON_PX,
) -> QIcon:
    dpr = 1.0
    app = QApplication.instance()
    if isinstance(app, QApplication):
        screen = app.primaryScreen()
        if screen is not None:
            dpr = screen.devicePixelRatio()
    side = max(12, int(logical_px * dpr))
    pm = QPixmap(side, side)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(painter, logical_px)
    painter.end()
    return QIcon(pm)


def _draw_play_green(p: QPainter, size: int) -> None:
    green = QColor(93, 220, 122)
    m = size * 0.22
    tri = QPolygonF(
        [
            QPointF(m, m),
            QPointF(m, size - m),
            QPointF(size - m * 0.85, size * 0.5),
        ]
    )
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(green)
    p.drawPolygon(tri)


def control_bar_play_green_icon() -> QIcon:
    """Grünes Play-Dreieck (Audio-Player-Button)."""
    return _control_bar_icon(_draw_play_green)


def _draw_record_red(p: QPainter, size: int) -> None:
    red = QColor(231, 76, 60)
    border = QColor(30, 30, 30)
    m = size * 0.24
    rect = QRectF(m, m, size - 2 * m, size - 2 * m)
    p.setPen(border)
    p.setBrush(red)
    p.drawEllipse(rect)


def control_bar_record_red_icon() -> QIcon:
    """Roter Aufnahme-Punkt (Audio-Recorder-Button)."""
    return _control_bar_icon(_draw_record_red)


def _draw_live_green_led(p: QPainter, size: int) -> None:
    # Gleicher Aufbau wie roter Rekorder‑Punkt, Farbe näher Yaesu-/Live‑Grün (#52c41a).
    green = QColor(82, 196, 26)
    border = QColor(30, 30, 30)
    m = size * 0.24
    rect = QRectF(m, m, size - 2 * m, size - 2 * m)
    p.setPen(border)
    p.setBrush(green)
    p.drawEllipse(rect)


def control_bar_live_green_led_icon() -> QIcon:
    """Grüne LED wie Rekorder‑Punkt — Live‑Monitoring-Schaltfläche."""
    return _control_bar_icon(_draw_live_green_led)


def volume_role_icon_size() -> QSize:
    """Icon-Größe vor Lautstärkeregler in Soundeinstellungen."""
    return QSize(_TRANSPORT_BTN_ICON_PX, _TRANSPORT_BTN_ICON_PX)


def volume_role_record_icon() -> QIcon:
    """Roter Punkt — Aufnahme-Lautstärke."""
    return _control_bar_icon(_draw_record_red, logical_px=_TRANSPORT_BTN_ICON_PX)


def volume_role_send_icon() -> QIcon:
    """Grünes Play — Sende-Lautstärke."""
    return _control_bar_icon(_draw_play_green, logical_px=_TRANSPORT_BTN_ICON_PX)


def volume_role_pc_icon() -> QIcon:
    """„PC“ für PC-Ausgabe-Lautstärke — ``WindowText`` der App-Palette (Hell/Dunkel)."""
    app = QApplication.instance()
    pc_color = (
        app.palette().color(QPalette.ColorRole.WindowText)
        if isinstance(app, QApplication)
        else QColor(33, 33, 33)
    )

    def _draw(p: QPainter, size: int) -> None:
        s = float(size)
        font = QFont()
        font.setPixelSize(max(8, int(s * 0.48)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(pc_color)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "PC")

    return _control_bar_icon(_draw, logical_px=_TRANSPORT_BTN_ICON_PX)


def _draw_speaker_white(p: QPainter, size: int) -> None:
    white = QColor(245, 245, 245)
    s = float(size)
    body = QPolygonF(
        [
            QPointF(s * 0.14, s * 0.34),
            QPointF(s * 0.38, s * 0.34),
            QPointF(s * 0.52, s * 0.22),
            QPointF(s * 0.52, s * 0.78),
            QPointF(s * 0.38, s * 0.66),
            QPointF(s * 0.14, s * 0.66),
        ]
    )
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(white)
    p.drawPolygon(body)
    for cx, cy, r, alpha in (
        (0.62, 0.50, 0.10, 255),
        (0.74, 0.50, 0.14, 200),
        (0.88, 0.50, 0.18, 140),
    ):
        color = white if alpha == 255 else QColor(245, 245, 245, alpha)
        p.setBrush(color)
        p.drawEllipse(QRectF((cx - r) * s, (cy - r) * s, 2 * r * s, 2 * r * s))


def menu_speaker_white_icon() -> QIcon:
    """Weißer Lautsprecher für Menüs (Dark Mode)."""
    return _control_bar_icon(_draw_speaker_white, logical_px=16)


def control_bar_speaker_white_icon() -> QIcon:
    """Weißer Lautsprecher für die Sound-Schaltfläche in der Control-Bar."""
    return _control_bar_icon(_draw_speaker_white)


def transport_button_icon_size() -> QSize:
    """Icon-Größe für Play/Pause/Stopp/Replay in Audio-Fenstern."""
    return QSize(_TRANSPORT_BTN_ICON_PX, _TRANSPORT_BTN_ICON_PX)


def set_transport_button_icon(button: QPushButton, icon: QIcon) -> None:
    button.setIcon(icon)
    button.setIconSize(transport_button_icon_size())


def transport_play_icon() -> QIcon:
    """Grünes Play-Symbol (Sendung / Play PC)."""
    return _control_bar_icon(_draw_play_green, logical_px=_TRANSPORT_BTN_ICON_PX)


def transport_pause_icon() -> QIcon:
    """Weiße Pause-Balken."""
    white = QColor(245, 245, 245)

    def _draw(p: QPainter, size: int) -> None:
        s = float(size)
        bar_w = s * 0.14
        gap = s * 0.12
        x0 = s * 0.28
        y0 = s * 0.22
        h = s * 0.56
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawRect(QRectF(x0, y0, bar_w, h))
        p.drawRect(QRectF(x0 + bar_w + gap, y0, bar_w, h))

    return _control_bar_icon(_draw, logical_px=_TRANSPORT_BTN_ICON_PX)


def transport_stop_icon() -> QIcon:
    """Weißes Stopp-Quadrat."""
    white = QColor(245, 245, 245)

    def _draw(p: QPainter, size: int) -> None:
        m = size * 0.26
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawRect(QRectF(m, m, size - 2 * m, size - 2 * m))

    return _control_bar_icon(_draw, logical_px=_TRANSPORT_BTN_ICON_PX)


def transport_replay_icon() -> QIcon:
    """Grünes Replay-Symbol (Pfeil im Kreis)."""
    green = QColor(93, 220, 122)

    def _draw(p: QPainter, size: int) -> None:
        s = float(size)
        cx, cy = s * 0.5, s * 0.5
        r = s * 0.32
        path = QPainterPath()
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        path.arcMoveTo(rect, 55)
        path.arcTo(rect, 55, 270)
        p.setPen(
            QPen(
                green,
                max(1.5, s * 0.1),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(green)
        head = QPolygonF(
            [
                QPointF(cx + r * 0.55, cy - r * 0.85),
                QPointF(cx + r * 1.05, cy - r * 0.35),
                QPointF(cx + r * 0.35, cy - r * 0.15),
            ]
        )
        p.drawPolygon(head)

    return _control_bar_icon(_draw, logical_px=_TRANSPORT_BTN_ICON_PX)


def transport_trash_icon() -> QIcon:
    """Weißes Mülleimer-Symbol (Datei löschen)."""
    white = QColor(245, 245, 245)
    dim = QColor(200, 200, 200)

    def _draw(p: QPainter, size: int) -> None:
        s = float(size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawRoundedRect(QRectF(s * 0.16, s * 0.20, s * 0.68, s * 0.12), 1, 1)
        p.drawRect(QRectF(s * 0.30, s * 0.14, s * 0.40, s * 0.08))
        body = QPolygonF(
            [
                QPointF(s * 0.24, s * 0.34),
                QPointF(s * 0.76, s * 0.34),
                QPointF(s * 0.70, s * 0.82),
                QPointF(s * 0.30, s * 0.82),
            ]
        )
        p.drawPolygon(body)
        p.setBrush(dim)
        stripe_w = s * 0.06
        for cx in (0.38, 0.50, 0.62):
            p.drawRect(QRectF(s * cx - stripe_w / 2, s * 0.40, stripe_w, s * 0.36))

    return _control_bar_icon(_draw, logical_px=_TRANSPORT_BTN_ICON_PX)
