"""Live-Meter (Version 0.4, erweitert in 0.6).

Zeigt:

* **S-Meter** + **DSP-Slider (NB / DNF / DNR / AGC)** in einer Zeile —
  bei NB/DNR ändert der Slider-Zug den Pegel und die LED toggelt on/off.
  Bei DNF nur on/off. AGC ist ein diskreter Slider mit den vier
  Stufen AUTO/FAST/MID/SLOW (kein OFF, weil das im Alltag nicht
  gebraucht wird).
* **AF/RF-Gain** als Mini-Balken.
* **TX-Bars** (ALC/COMP/PO/SWR) hochkant unten — bei RX gedimmt.

Polling läuft auf einem :class:`QThread`. Im RX-Modus wird zyklisch das
S-Meter und der TX-Status abgefragt; die DSP-Stati und Pegel werden auf
einem **Slow-Path** alle paar Sekunden gelesen (sie ändern sich selten).
Im TX-Modus werden nur die 4 TX-Meter gelesen.

Beim Trennen wird der Thread sauber gestoppt.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QMetaObject,
    QObject,
    Q_ARG,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from cat import SerialCAT
from cat.cat_errors import CatConnectionLostError, CatError
from cat.ft991_cat import FT991CAT
from mapping.meter_mapping import (
    METER_INFO,
    MeterInfo,
    MeterKind,
    SMETER_RAW_MAX,
    SMETER_TICKS,
    format_meter_value,
    meter_choices,
)
from mapping.rx_mapping import (
    AGC_LABELS,
    AGC_SLIDER_LABELS,
    AGC_SLIDER_MODES,
    AgcMode,
    RxMode,
    agc_mode_to_slider_pos,
    format_frequency_hz,
)


# ----------------------------------------------------------------------
# Datenmodelle
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class TxMeterSample:
    """Sample der vier TX-Meter und des TX-Status."""

    transmitting: bool
    values: Dict[MeterKind, int]


# Rückwärtskompatibler Alias — vor 0.6 hieß die Klasse so.
MeterSample = TxMeterSample


@dataclass(frozen=True)
class RxStatusSample:
    """Sample der RX-Statuswerte.

    Nur ``smeter`` wird in jedem Tick aktualisiert. Die langsameren Werte
    (DSP, Pegel, Mode, Frequenz) sind nur in jedem N-ten Tick gesetzt;
    sonst ``None`` (= UI lässt den alten Wert stehen).
    """

    smeter: int
    squelch: Optional[int] = None
    af_gain: Optional[int] = None
    rf_gain: Optional[int] = None
    agc: Optional[AgcMode] = None
    noise_blanker: Optional[Tuple[bool, int]] = None
    noise_reduction: Optional[Tuple[bool, int]] = None
    auto_notch: Optional[bool] = None
    mode: Optional[RxMode] = None
    frequency_hz: Optional[int] = None
    frequency_b_hz: Optional[int] = None


DEFAULT_INTERVAL_TX_MS = 250
DEFAULT_INTERVAL_RX_MS = 500
MIN_INTERVAL_MS = 100
MAX_INTERVAL_MS = 5000

#: Alle N RX-Ticks holen wir DSP-Stati / Pegel / Mode / Freq vom Radio.
#: Bei rx_interval = 500 ms entspricht N=6 -> alle 3 Sekunden.
SLOW_PATH_TICKS = 6


# ----------------------------------------------------------------------
# Poller (Worker)
# ----------------------------------------------------------------------


class MeterPoller(QObject):
    """Pollt zyklisch TX-Status + (bei TX) TX-Meter + (bei RX) S-Meter.

    **Adaptiv:** Im RX-Modus wird ``TX;`` + ``SM0;`` gesendet (2 Roundtrips)
    und das ``rx_interval_ms`` verwendet. Zusätzlich werden alle
    :data:`SLOW_PATH_TICKS` Ticks auch die DSP-Stati, Pegel und der Mode
    gelesen. Im TX-Modus werden die 4 TX-Meter gepollt und das
    schnellere ``tx_interval_ms`` verwendet.

    Verwendet einen ``singleShot``-basierten Re-Trigger, damit zwei Ticks
    niemals überlappen.
    """

    tx_sample = Signal(object)        # TxMeterSample
    rx_sample = Signal(object)        # RxStatusSample
    error_occurred = Signal(str)
    running_changed = Signal(bool)
    connection_lost = Signal()

    def __init__(
        self,
        serial_cat: SerialCAT,
        tx_interval_ms: int = DEFAULT_INTERVAL_TX_MS,
        rx_interval_ms: int = DEFAULT_INTERVAL_RX_MS,
    ) -> None:
        super().__init__()
        self._cat = serial_cat
        self._tx_interval_ms = self._clamp(tx_interval_ms)
        self._rx_interval_ms = max(self._tx_interval_ms, self._clamp(rx_interval_ms))
        self._active = False
        self._error_streak = 0
        self._last_tx: Optional[bool] = None
        self._rx_tick = 0           # Zähler für Slow-Path
        self._force_full_rx = True  # Beim Start einmal alle RX-Werte lesen

    @staticmethod
    def _clamp(ms: int) -> int:
        return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, int(ms)))

    # Konfiguration --------------------------------------------------------

    @Slot(int)
    def set_interval_ms(self, ms: int) -> None:
        self._tx_interval_ms = self._clamp(ms)
        if self._rx_interval_ms < self._tx_interval_ms:
            self._rx_interval_ms = self._tx_interval_ms

    @Slot(int)
    def set_rx_interval_ms(self, ms: int) -> None:
        self._rx_interval_ms = max(self._tx_interval_ms, self._clamp(ms))

    # Steuerung ------------------------------------------------------------

    @Slot()
    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._error_streak = 0
        self._last_tx = None
        self._rx_tick = 0
        self._force_full_rx = True
        self.running_changed.emit(True)
        QTimer.singleShot(0, self._tick)

    @Slot()
    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self.running_changed.emit(False)

    # Hauptpfad ------------------------------------------------------------

    @Slot()
    def _tick(self) -> None:
        if not self._active:
            return

        if not self._cat.is_connected():
            self._schedule_next(self._rx_interval_ms)
            return

        try:
            ft = FT991CAT(self._cat)
            tx = ft.get_tx_status()
            if tx:
                next_delay = self._poll_tx(ft)
            else:
                next_delay = self._poll_rx(ft)
            self._error_streak = 0
            self._last_tx = tx
        except CatConnectionLostError:
            self._active = False
            self.connection_lost.emit()
            self.running_changed.emit(False)
            return
        except CatError as exc:
            self._error_streak += 1
            if self._error_streak in (1, 5, 25):
                self.error_occurred.emit(str(exc))
            next_delay = self._rx_interval_ms
        except Exception as exc:  # noqa: BLE001
            log = self._cat.get_log()
            if log is not None:
                log.log_error(
                    "Unerwarteter Fehler im Meter-Poller:\n" + traceback.format_exc()
                )
            self.error_occurred.emit(repr(exc))
            next_delay = self._rx_interval_ms

        self._schedule_next(next_delay)

    def _poll_tx(self, ft: FT991CAT) -> int:
        """Liest alle 4 TX-Meter und emittiert TxMeterSample.

        Beim Übergang RX→TX setzen wir den Slow-Path-Zähler zurück, damit
        nach dem nächsten RX die DSP-Werte schnell wieder aktuell sind.
        """
        values: Dict[MeterKind, int] = {}
        for kind in (MeterKind.COMP, MeterKind.ALC, MeterKind.PO, MeterKind.SWR):
            values[kind] = ft.read_meter(kind)
        self.tx_sample.emit(TxMeterSample(transmitting=True, values=values))
        # Beim nächsten RX direkt einen vollen Slow-Path-Read machen.
        self._force_full_rx = True
        return self._tx_interval_ms

    def _poll_rx(self, ft: FT991CAT) -> int:
        """Liest S-Meter und (alle N Ticks) DSP/Pegel/Mode/Freq.

        TX-Bars werden mit einem Null-TxMeterSample zurückgesetzt — die
        ``set_value``-Cache-Logik verhindert unnötige Repaints.
        """
        # TX-Status-Update an die GUI (Bars auf 0, TX-LED aus).
        zero_values = {
            MeterKind.COMP: 0, MeterKind.ALC: 0,
            MeterKind.PO: 0, MeterKind.SWR: 0,
        }
        self.tx_sample.emit(TxMeterSample(transmitting=False, values=zero_values))

        smeter = ft.read_smeter()

        slow_path = self._force_full_rx or (self._rx_tick % SLOW_PATH_TICKS == 0)
        self._rx_tick += 1
        self._force_full_rx = False

        if not slow_path:
            self.rx_sample.emit(RxStatusSample(smeter=smeter))
            return self._rx_interval_ms

        # Slow-Path: alle paar Sekunden volle DSP-/Pegel-Werte holen.
        # Jeder Read kann CatProtocolError werfen — wir nehmen einzelne
        # Fehler in Kauf, damit ein einziger zickender Wert nicht den
        # ganzen Slow-Path lahmlegt. Ungelesene Werte bleiben einfach
        # None und die GUI behält den letzten Stand.
        def _safe(func):
            try:
                return func()
            except CatError as exc:
                log = self._cat.get_log()
                if log is not None:
                    log.log_warn(f"RX-Status übersprungen: {exc}")
                return None

        squelch = _safe(ft.read_squelch)
        af_gain = _safe(ft.read_af_gain)
        rf_gain = _safe(ft.read_rf_gain)
        agc = _safe(ft.read_agc)
        nb_on = _safe(ft.read_noise_blanker)
        nb_level = _safe(ft.read_noise_blanker_level)
        nr_on = _safe(ft.read_noise_reduction)
        nr_level = _safe(ft.read_noise_reduction_level)
        auto_notch = _safe(ft.read_auto_notch)
        mode = _safe(ft.read_rx_mode)
        freq_a = _safe(ft.read_frequency)
        freq_b = _safe(ft.read_frequency_b)

        sample = RxStatusSample(
            smeter=smeter,
            squelch=squelch,
            af_gain=af_gain,
            rf_gain=rf_gain,
            agc=agc,
            noise_blanker=(nb_on, nb_level) if (nb_on is not None and nb_level is not None) else None,
            noise_reduction=(nr_on, nr_level) if (nr_on is not None and nr_level is not None) else None,
            auto_notch=auto_notch,
            mode=mode,
            frequency_hz=freq_a,
            frequency_b_hz=freq_b,
        )
        self.rx_sample.emit(sample)
        return self._rx_interval_ms

    def _schedule_next(self, delay_ms: int) -> None:
        if self._active:
            QTimer.singleShot(delay_ms, self._tick)


# ----------------------------------------------------------------------
# TX-LED
# ----------------------------------------------------------------------


class TxIndicator(QFrame):
    """Kleiner Kreis mit drei Zuständen: ``off`` (grau, nicht verbunden /
    Polling gestoppt), ``rx`` (grün, Empfang) und ``tx`` (rot, sendet).
    """

    STATE_OFF = "off"
    STATE_RX = "rx"
    STATE_TX = "tx"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self._state = self.STATE_OFF

    def set_state(self, state: str) -> None:
        if state not in (self.STATE_OFF, self.STATE_RX, self.STATE_TX):
            return
        if state != self._state:
            self._state = state
            self.update()

    def set_active(self, active: bool) -> None:
        """Bequemer Setter: ``True`` -> TX, ``False`` -> RX. Für den
        ``off``-Zustand bitte direkt :meth:`set_state` benutzen."""
        self.set_state(self.STATE_TX if active else self.STATE_RX)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._state == self.STATE_TX:
            fill = QColor(220, 50, 50)
            border = QColor(120, 20, 20)
        elif self._state == self.STATE_RX:
            fill = QColor(60, 190, 80)
            border = QColor(28, 110, 40)
        else:  # STATE_OFF
            fill = QColor(70, 70, 70)
            border = QColor(40, 40, 40)
        painter.setPen(border)
        painter.setBrush(fill)
        painter.drawEllipse(2, 2, self.width() - 4, self.height() - 4)
        painter.end()


# ----------------------------------------------------------------------
# Status-LED für DSP-Schalter (NB / NR / Auto-Notch)
# ----------------------------------------------------------------------


class DspStatusLed(QFrame):
    """Mini-LED mit Label rechts: ``[●] NB``."""

    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on: Optional[bool] = None  # None = unbekannt
        self._suffix: str = ""

        self._dot = _LedDot()
        self._text = QLabel(label)
        f = self._text.font()
        f.setPointSizeF(f.pointSizeF() * 0.95)
        self._text.setFont(f)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._dot)
        layout.addWidget(self._text, stretch=1)

    def set_state(self, on: Optional[bool], suffix: str = "") -> None:
        if on == self._on and suffix == self._suffix:
            return
        self._on = on
        self._suffix = suffix
        self._dot.set_state(on)
        base = self._text.text().split(" ", 1)[0]
        self._text.setText(f"{base} {suffix}" if suffix else base)


class _LedDot(QWidget):
    """Reiner Mal-Widget für eine kleine LED."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._on: Optional[bool] = None

    def set_state(self, on: Optional[bool]) -> None:
        if on != self._on:
            self._on = on
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._on is True:
            fill = QColor(82, 196, 26)     # grün
            border = QColor(40, 110, 18)
        elif self._on is False:
            fill = QColor(70, 70, 70)
            border = QColor(40, 40, 40)
        else:
            fill = QColor(45, 45, 45)
            border = QColor(80, 80, 80)
        painter.setPen(border)
        painter.setBrush(fill)
        painter.drawEllipse(0, 0, self.width() - 1, self.height() - 1)
        painter.end()


# ----------------------------------------------------------------------
# DspSlider — vertikale Steuerung für NB / DNR (mit Level)
# bzw. DNF (nur On/Off).
#
# Aufbau (von oben nach unten):
#   * Klein-Label mit Bezeichnung (z.B. ``NB``)
#   * Klickbarer großer LED-Punkt zum Toggle-On/Off
#   * Vertikaler Slider (nur wenn ``supports_level`` True; sonst Spacer)
#   * Wert-Label unter dem Slider
#
# Signale (vom User ausgelöst, *nicht* beim programmatischen Set):
#   * ``toggled(bool)``       — LED wurde geklickt
#   * ``level_changed(int)``  — Slider wurde bewegt (kommt mit ~150 ms
#                               Debouncing, damit das CAT nicht überrannt
#                               wird)
# ----------------------------------------------------------------------


class DspSlider(QFrame):
    """Vertikaler DSP-Slider mit On/Off-LED und optionalem Level-Slider."""

    toggled = Signal(bool)
    level_changed = Signal(int)

    def __init__(
        self,
        label: str,
        *,
        supports_level: bool,
        level_min: int = 0,
        level_max: int = 10,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._supports_level = supports_level
        self._level_min = level_min
        self._level_max = level_max
        self._applying_remote = False     # blockt User-Signale beim Read-Update
        self._on: Optional[bool] = None

        # Debounce-Timer für Level-Writes (Slider zieht -> Schwall an Events).
        self._level_debounce = QTimer(self)
        self._level_debounce.setSingleShot(True)
        self._level_debounce.setInterval(150)
        self._level_debounce.timeout.connect(self._flush_level)

        self.setFrameShape(QFrame.NoFrame)

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(3)

        title = QLabel(label)
        title.setAlignment(Qt.AlignCenter)
        tf = title.font()
        tf.setBold(True)
        tf.setPointSizeF(tf.pointSizeF() * 0.85)
        title.setFont(tf)
        v.addWidget(title)

        # Klickbare LED (etwas größer als die Mini-LEDs, ~16px).
        self._led_btn = _LedButton()
        self._led_btn.clicked.connect(self._on_led_clicked)
        led_row = QHBoxLayout()
        led_row.setContentsMargins(0, 0, 0, 0)
        led_row.addStretch(1)
        led_row.addWidget(self._led_btn)
        led_row.addStretch(1)
        v.addLayout(led_row)

        if supports_level:
            self._slider = QSlider(Qt.Vertical)
            self._slider.setRange(level_min, level_max)
            self._slider.setValue(level_min)
            self._slider.setMinimumHeight(130)
            self._slider.setSingleStep(1)
            self._slider.setPageStep(1)
            self._slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self._slider.valueChanged.connect(self._on_slider_changed)
            slider_row = QHBoxLayout()
            slider_row.setContentsMargins(0, 0, 0, 0)
            slider_row.addStretch(1)
            slider_row.addWidget(self._slider)
            slider_row.addStretch(1)
            v.addLayout(slider_row, stretch=1)

            self._value_label = QLabel(str(level_min))
            self._value_label.setAlignment(Qt.AlignCenter)
            vf = self._value_label.font()
            vf.setPointSizeF(vf.pointSizeF() * 0.85)
            self._value_label.setFont(vf)
            v.addWidget(self._value_label)
        else:
            self._slider = None
            self._value_label = QLabel("OFF")
            self._value_label.setAlignment(Qt.AlignCenter)
            vf = self._value_label.font()
            vf.setPointSizeF(vf.pointSizeF() * 0.85)
            self._value_label.setFont(vf)
            v.addStretch(1)
            v.addWidget(self._value_label)

    # ------------------------------------------------------------------
    # User-Interaktion
    # ------------------------------------------------------------------

    def _on_led_clicked(self) -> None:
        if self._applying_remote:
            return
        new_state = not bool(self._on)
        self._on = new_state
        self._led_btn.set_state(new_state)
        if not self._supports_level:
            self._value_label.setText("ON" if new_state else "OFF")
        self.toggled.emit(new_state)

    def _on_slider_changed(self, value: int) -> None:
        if self._supports_level:
            self._value_label.setText(str(value))
        if self._applying_remote:
            return
        self._level_debounce.start()

    def _flush_level(self) -> None:
        if self._slider is None:
            return
        self.level_changed.emit(int(self._slider.value()))

    # ------------------------------------------------------------------
    # Programmatisches Setzen (vom Poller, ohne Signal-Pingpong)
    # ------------------------------------------------------------------

    def set_state(self, on: Optional[bool]) -> None:
        if on == self._on:
            return
        self._on = on
        self._led_btn.set_state(on)
        if not self._supports_level:
            if on is True:
                self._value_label.setText("ON")
            elif on is False:
                self._value_label.setText("OFF")
            else:
                self._value_label.setText("—")

    def set_level(self, level: int) -> None:
        if self._slider is None:
            return
        clamped = max(self._level_min, min(self._level_max, int(level)))
        if clamped == self._slider.value():
            return
        self._applying_remote = True
        try:
            self._slider.setValue(clamped)
        finally:
            self._applying_remote = False
        # Debounce-Trigger durch programmatisches Set zurücknehmen.
        self._level_debounce.stop()
        self._value_label.setText(str(clamped))


class _LedButton(QWidget):
    """Etwas größerer LED-Punkt als Klick-Toggle (~16px)."""

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self._on: Optional[bool] = None

    def set_state(self, on: Optional[bool]) -> None:
        if on != self._on:
            self._on = on
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._on is True:
            fill = QColor(82, 196, 26)
            border = QColor(40, 110, 18)
        elif self._on is False:
            fill = QColor(70, 70, 70)
            border = QColor(40, 40, 40)
        else:
            fill = QColor(45, 45, 45)
            border = QColor(80, 80, 80)
        pen = QPen(border)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)
        painter.end()


# ----------------------------------------------------------------------
# AgcSlider — diskreter vertikaler Slider mit 4 Positionen
# (AUTO / FAST / MID / SLOW). Schreibt sofort, weil das Setzen des AGC-
# Modus auf dem Radio unkritisch ist. OFF wird in der GUI als "—" am
# Wert-Label angezeigt und der Slider auf eine neutrale Position
# (zwischen den Ticks) verschoben.
# ----------------------------------------------------------------------


class AgcSlider(QFrame):
    """Vertikaler Slider mit 4 diskreten AGC-Positionen."""

    mode_chosen = Signal(object)   # AgcMode

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._applying_remote = False

        self.setFrameShape(QFrame.NoFrame)

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(3)

        title = QLabel("AGC")
        title.setAlignment(Qt.AlignCenter)
        tf = title.font()
        tf.setBold(True)
        tf.setPointSizeF(tf.pointSizeF() * 0.85)
        title.setFont(tf)
        v.addWidget(title)

        # Platzhalter in derselben Höhe wie die LED-Reihe der DspSlider —
        # so liegen alle vier Spalten optisch auf einer Linie.
        spacer = QWidget()
        spacer.setFixedHeight(18)
        v.addWidget(spacer)

        self._slider = QSlider(Qt.Vertical)
        # Range 0..3 für AUTO/FAST/MID/SLOW. Wir nutzen invertedAppearance,
        # damit AUTO oben steht (= weichster Modus oben, härtester unten
        # ist die übliche Konvention bei Yaesu).
        self._slider.setRange(0, len(AGC_SLIDER_MODES) - 1)
        self._slider.setValue(0)
        self._slider.setMinimumHeight(130)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setTickPosition(QSlider.TicksRight)
        self._slider.setTickInterval(1)
        self._slider.setInvertedAppearance(False)
        self._slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._slider.valueChanged.connect(self._on_slider_changed)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.addStretch(1)
        slider_row.addWidget(self._slider)
        slider_row.addStretch(1)
        v.addLayout(slider_row, stretch=1)

        self._value_label = QLabel(AGC_SLIDER_LABELS[0])
        self._value_label.setAlignment(Qt.AlignCenter)
        vf = self._value_label.font()
        vf.setPointSizeF(vf.pointSizeF() * 0.85)
        self._value_label.setFont(vf)
        v.addWidget(self._value_label)

    # ------------------------------------------------------------------
    # User-Interaktion
    # ------------------------------------------------------------------

    def _on_slider_changed(self, value: int) -> None:
        idx = max(0, min(len(AGC_SLIDER_MODES) - 1, int(value)))
        self._value_label.setText(AGC_SLIDER_LABELS[idx])
        if self._applying_remote:
            return
        self.mode_chosen.emit(AGC_SLIDER_MODES[idx])

    # ------------------------------------------------------------------
    # Programmatisches Setzen (Poller-Update, kein Signal-Echo)
    # ------------------------------------------------------------------

    def set_mode(self, mode: Optional[AgcMode]) -> None:
        if mode is None:
            return
        pos = agc_mode_to_slider_pos(mode)
        self._applying_remote = True
        try:
            if pos < 0:
                # OFF oder unbekannt — Slider auf 0 lassen, Label „—".
                self._value_label.setText(AGC_LABELS.get(mode, "—"))
            else:
                if pos != self._slider.value():
                    self._slider.setValue(pos)
                self._value_label.setText(AGC_SLIDER_LABELS[pos])
        finally:
            self._applying_remote = False


# ----------------------------------------------------------------------
# ScaledMeterBar — gemeinsame Implementierung für S-Meter und TX-Meter
# ----------------------------------------------------------------------


def _bar_gradient_for(warn: float, danger: float, *, enabled: bool) -> QLinearGradient:
    """Erstellt einen vertikalen Farbverlauf für die Bar.

    Der Verlauf nutzt ``warn`` und ``danger`` (jeweils 0..1, Anteil der
    Bar-Höhe) als Übergänge zwischen den Zonen:

    * 0 .. warn   = grün (alles in Ordnung)
    * warn .. danger = orange (achtsam)
    * danger .. 1 = rot (Übersteuerung / kritisch)

    Bei ``enabled=False`` (z. B. TX-Bars im RX-Modus, S-Meter im TX-Modus)
    bekommen wir einen sehr dezenten Verlauf in Grautönen.
    """
    grad = QLinearGradient(0, 1, 0, 0)
    if enabled:
        green = QColor("#1bb340")
        orange = QColor("#ed8a19")
        red = QColor("#c62828")
        # Die Stops liegen so, dass der Übergang nicht direkt am Schwellwert
        # ist sondern ein kleiner Verlaufsbereich entsteht. Das wirkt
        # weicher und macht die Schwelle visuell trotzdem klar.
        warn_lo = max(0.0, warn - 0.04)
        danger_lo = max(warn + 0.01, danger - 0.04)
        grad.setColorAt(0.0, green)
        grad.setColorAt(warn_lo, green)
        grad.setColorAt(warn, orange)
        grad.setColorAt(danger_lo, orange)
        grad.setColorAt(danger, red)
        grad.setColorAt(1.0, red)
    else:
        grad.setColorAt(0.0, QColor("#3c5040"))
        grad.setColorAt(1.0, QColor("#604040"))
    return grad


class ScaledMeterBar(QWidget):
    """Vertikaler Balken mit Skala links, Farbverlauf-Fill und optionalem
    Header-Label sowie Wertanzeige unten.

    Diese Klasse rendert sowohl den großen S-Meter als auch die kompakten
    TX-Meter (ALC/COMP/PO/SWR). Konfiguration:

    * ``label_text``       — Überschrift über der Bar (leer = keine Überschrift)
    * ``unit_text``        — wird hinter dem Wert angezeigt (leer = weglassen)
    * ``raw_max``          — Skala 0..raw_max
    * ``ticks``            — Liste ``[(raw, label), …]`` für die Beschriftung
    * ``warn`` / ``danger``— Schwellwerte für den Farbverlauf
    * ``value_formatter``  — wandelt den Rohwert in einen Anzeigetext um
    * ``show_squelch``     — wenn ``True``, kann eine Squelch-Linie gezeichnet werden
    * ``bar_width`` / ``scale_width`` / ``bar_min_height`` — Geometrie
    * ``header_font_scale``/``value_font_scale``/``tick_font_scale`` — Schriftgrößen
    """

    def __init__(
        self,
        *,
        label_text: str = "",
        unit_text: str = "",
        raw_max: int = 255,
        ticks: Optional[List[tuple]] = None,
        warn: float = 0.7,
        danger: float = 0.9,
        value_formatter: Optional[callable] = None,  # type: ignore[valid-type]
        show_squelch: bool = False,
        bar_width: int = 22,
        scale_width: int = 38,
        bar_min_height: int = 180,
        header_font_scale: float = 1.0,
        value_font_scale: float = 0.9,
        tick_font_scale: float = 0.78,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._raw_max = raw_max
        self._ticks: List[tuple] = list(ticks or [])
        self._warn = max(0.0, min(1.0, warn))
        self._danger = max(self._warn, min(1.0, danger))
        self._value_formatter = value_formatter or (lambda v: f"{v}")
        self._show_squelch = show_squelch
        self._bar_width = bar_width
        self._scale_width = scale_width
        self._tick_font_scale = tick_font_scale

        self._value: Optional[int] = None
        self._squelch_raw: Optional[int] = None
        self._enabled = True
        #: Wenn gesetzt, wird der Balken neutral dargestellt (kein Roh-Wert,
        #: kein Tooltip mit Skala) und das Wert-Label zeigt "—". Wir
        #: nutzen das z. B. für SWR auf VHF/UHF, wo die ``RM6;``-Skala
        #: vom FT-991A nicht zuverlässig kalibriert ist.
        self._unavailable_reason: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        if label_text:
            self._label = QLabel(label_text)
            self._label.setAlignment(Qt.AlignCenter)
            lf = self._label.font()
            lf.setBold(True)
            lf.setPointSizeF(lf.pointSizeF() * header_font_scale)
            self._label.setFont(lf)
            outer.addWidget(self._label)
        else:
            self._label = None

        # Eigener Mal-Canvas für Skala + Bar + Squelch-Linie.
        self._canvas = _ScaledBarCanvas(self)
        outer.addWidget(self._canvas, stretch=1)
        self._canvas.setMinimumHeight(bar_min_height)
        self._canvas.setMinimumWidth(scale_width + bar_width + 6)
        self._canvas.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self._value_label = QLabel("—")
        self._value_label.setAlignment(Qt.AlignCenter)
        vf = self._value_label.font()
        vf.setPointSizeF(vf.pointSizeF() * value_font_scale)
        self._value_label.setFont(vf)
        self._unit_text = unit_text
        outer.addWidget(self._value_label)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def set_value(self, raw: int) -> None:
        # Im „nicht verfügbar"-Modus ignorieren wir Roh-Werte komplett —
        # der Aufrufer muss erst :meth:`clear_unavailable` rufen, bevor
        # wieder echte Werte dargestellt werden.
        if self._unavailable_reason is not None:
            return
        clamped = max(0, min(self._raw_max, int(raw)))
        if clamped == self._value:
            return
        self._value = clamped
        text = self._value_formatter(clamped)
        if self._unit_text and not text.endswith(self._unit_text):
            self._value_label.setText(text)
        else:
            self._value_label.setText(text)
        # Diagnose-Tooltip: Roh-Wert anzeigen. Hilft besonders bei SWR auf
        # VHF/UHF, wenn die KW-Skala nicht passt — der Anwender kann so
        # ablesen, welcher Roh-Wert vom Radio tatsächlich kommt.
        self.setToolTip(f"Roh: {clamped} / {self._raw_max}\nSkala: {text}")
        self._canvas.update()

    def set_unavailable(self, reason: str) -> None:
        """Markiert die Bar als „nicht verfügbar".

        Verwendet z. B. für SWR auf VHF/UHF, wo das FT-991A über CAT
        keine zuverlässig kalibrierten Werte liefert (Roh-Wert geht
        sofort auf 255, obwohl das Front-Panel 1:1.2 zeigt). Wir
        machen das in der GUI sichtbar, statt eine erfundene Zahl
        anzuzeigen. Der Wert wird auf 0 zurückgesetzt, der Balken
        bleibt leer und das Wert-Label zeigt ``"—"``.
        """
        if self._unavailable_reason == reason:
            return
        self._unavailable_reason = reason
        self._value = 0
        self._value_label.setText("—")
        self.setToolTip(reason)
        self._canvas.update()

    def clear_unavailable(self) -> None:
        """Hebt :meth:`set_unavailable` wieder auf."""
        if self._unavailable_reason is None:
            return
        self._unavailable_reason = None
        # Letzten echten Wert vergessen, damit der nächste set_value()
        # garantiert greift (sonst würde der Cache-Check ``clamped ==
        # self._value`` ihn überspringen).
        self._value = None
        self._value_label.setText("—")
        self.setToolTip("")
        self._canvas.update()

    def set_squelch(self, raw: Optional[int]) -> None:
        if not self._show_squelch or raw == self._squelch_raw:
            return
        self._squelch_raw = raw
        self._canvas.update()

    def set_enabled_visual(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._canvas.update()
        if self._label is not None:
            self._label.setStyleSheet("" if enabled else "color: #777;")
        self._value_label.setStyleSheet("" if enabled else "color: #777;")

    def reset(self) -> None:
        self._value = None
        self._squelch_raw = None
        self._value_label.setText("—")
        self._canvas.update()


class _ScaledBarCanvas(QWidget):
    """Reines Mal-Widget für :class:`ScaledMeterBar`. Greift via Eltern-
    Referenz auf den Zustand zu — so können wir den Bar-Bereich frei
    layouten, ohne zusätzliche Sub-Widgets zu instanziieren.
    """

    def __init__(self, parent: "ScaledMeterBar") -> None:
        super().__init__(parent)
        self._parent_ref = parent

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = self._parent_ref
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            self._paint(painter, p)
        finally:
            painter.end()

    def _paint(self, painter: QPainter, p: "ScaledMeterBar") -> None:
        height = self.height()
        bar_x = p._scale_width
        bar_top = 6
        bar_bottom = height - 6
        bar_height = bar_bottom - bar_top
        bar_w = p._bar_width

        # Hintergrund-Bar
        border_color = QColor("#555") if p._enabled else QColor("#3a3a3a")
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(QColor("#1b1b1b"))
        painter.drawRoundedRect(bar_x, bar_top, bar_w, bar_height, 3, 3)

        # Gefüllter Bereich (Farbverlauf gemäss warn/danger)
        if p._value is not None and p._value > 0:
            frac = p._value / p._raw_max if p._raw_max > 0 else 0.0
            filled_top = int(bar_bottom - frac * bar_height)
            grad = _bar_gradient_for(p._warn, p._danger, enabled=p._enabled)
            # Gradient-Koordinaten auf Pixel ummappen, damit die Stops mit
            # der Bar-Höhe übereinstimmen.
            grad.setStart(0, bar_bottom)
            grad.setFinalStop(0, bar_top)
            painter.setBrush(grad)
            painter.setPen(Qt.NoPen)
            inner_x = bar_x + 1
            inner_w = bar_w - 2
            inner_h = bar_bottom - filled_top - 1
            if inner_h > 0:
                painter.drawRoundedRect(inner_x, filled_top, inner_w, inner_h, 2, 2)

        # Squelch-Linie (nur S-Meter, gelb)
        if p._show_squelch and p._squelch_raw is not None and p._enabled:
            sq_frac = max(0.0, min(1.0, p._squelch_raw / 100.0))
            sq_y = int(bar_bottom - sq_frac * bar_height)
            painter.setPen(QPen(QColor("#f0c419"), 2))
            painter.drawLine(bar_x - 4, sq_y, bar_x + bar_w + 4, sq_y)

        # Skala links
        tick_color = QColor("#cccccc") if p._enabled else QColor("#666666")
        painter.setPen(QPen(tick_color, 1))
        scale_font = QFont(self.font())
        scale_font.setPointSizeF(max(6.5, scale_font.pointSizeF() * p._tick_font_scale))
        painter.setFont(scale_font)
        fm = QFontMetrics(scale_font)
        for raw_tick, tick_label in p._ticks:
            if p._raw_max <= 0:
                continue
            tick_frac = max(0.0, min(1.0, raw_tick / p._raw_max))
            ty = int(bar_bottom - tick_frac * bar_height)
            painter.drawLine(bar_x - 3, ty, bar_x - 1, ty)
            text_rect_y = ty - fm.ascent() // 2 - 1
            painter.drawText(
                2, text_rect_y, p._scale_width - 6, fm.height(),
                Qt.AlignRight | Qt.AlignVCenter, tick_label,
            )


def make_smeter_bar() -> ScaledMeterBar:
    return ScaledMeterBar(
        label_text="",
        raw_max=SMETER_RAW_MAX,
        ticks=SMETER_TICKS,
        warn=0.50,             # S9 (raw 84) -> orange-Übergang
        danger=0.55,           # S9+ -> rot startet
        value_formatter=lambda raw: _format_smeter_text(raw),
        show_squelch=True,
        bar_width=22,
        scale_width=38,
        bar_min_height=170,
        value_font_scale=0.9,
        tick_font_scale=0.78,
    )


def make_tx_meter_bar(kind: MeterKind) -> ScaledMeterBar:
    info: MeterInfo = METER_INFO[kind]
    return ScaledMeterBar(
        label_text=info.label,
        unit_text=info.unit,
        raw_max=info.raw_max,
        ticks=info.ticks,
        warn=info.warn,
        danger=info.danger,
        value_formatter=info.value_formatter,
        show_squelch=False,
        bar_width=14,
        scale_width=26,
        bar_min_height=90,
        header_font_scale=0.88,
        value_font_scale=0.82,
        tick_font_scale=0.72,
    )


def _format_smeter_text(raw: int) -> str:
    """Kombiniert S-Punkt und Rohwert: ``"S7 • 56"``."""
    label = "S0"
    for tick_raw, tick_label in SMETER_TICKS:
        if raw >= tick_raw:
            label = tick_label
        else:
            break
    return f"{label} • {raw}"


# ----------------------------------------------------------------------
# Mini-Bar für AF / RF-Gain (kompakt, ohne Farbe)
# ----------------------------------------------------------------------


class MiniLevelBar(QWidget):
    """Kleine horizontale Balkenanzeige mit Label links und Wert rechts."""

    def __init__(self, label: str, raw_max: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._raw_max = raw_max
        self._value: Optional[int] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._label = QLabel(label)
        font = self._label.font()
        font.setPointSizeF(font.pointSizeF() * 0.9)
        self._label.setFont(font)
        self._label.setMinimumWidth(22)
        layout.addWidget(self._label)

        self.bar = QProgressBar()
        self.bar.setOrientation(Qt.Horizontal)
        self.bar.setRange(0, raw_max)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(
            "QProgressBar { border: 1px solid #555; border-radius: 2px; background: #1a1a1a; }"
            "QProgressBar::chunk { background-color: #4a7fbf; }"
        )
        layout.addWidget(self.bar, stretch=1)

        self._value_label = QLabel("—")
        self._value_label.setFont(font)
        self._value_label.setMinimumWidth(36)
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._value_label)

    def set_value(self, raw: Optional[int]) -> None:
        if raw == self._value:
            return
        self._value = raw
        if raw is None:
            self.bar.setValue(0)
            self._value_label.setText("—")
        else:
            self.bar.setValue(max(0, min(self._raw_max, int(raw))))
            self._value_label.setText(f"{raw}")


# ----------------------------------------------------------------------
# Meter-Widget (Tab-Inhalt)
# ----------------------------------------------------------------------


class MeterWidget(QWidget):
    """Kompakter Meter-Bereich: S-Meter oben, DSP-Status, AF/RF, TX-Bars unten."""

    tx_status_changed = Signal(bool)
    connection_lost = Signal()
    #: ``(mode, frequency_a_hz, frequency_b_hz)`` — wird vom Header beobachtet.
    #: Frequenzen können ``0`` sein, wenn der Wert beim Slow-Path-Read nicht
    #: gelesen werden konnte (Sample lässt das Feld dann auf ``None``).
    rx_info_changed = Signal(object, int, int)

    SIDEBAR_MIN_WIDTH = 290
    SIDEBAR_MAX_WIDTH = 380

    def __init__(
        self,
        serial_cat: SerialCAT,
        tx_interval_ms: int = DEFAULT_INTERVAL_TX_MS,
        rx_interval_ms: int = DEFAULT_INTERVAL_RX_MS,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._cat = serial_cat
        # Hochlevel-Wrapper für DSP-Writes (NB / DNR / DNF). Wird beim
        # Schreiben benutzt; ``SerialCAT`` ist über RLock threadsafe,
        # daher kollidiert das nicht mit dem parallel laufenden Poller.
        self._ft = FT991CAT(serial_cat)

        self._thread: Optional[QThread] = None
        self._poller: Optional[MeterPoller] = None
        self._tx_interval_ms = max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, int(tx_interval_ms)))
        self._rx_interval_ms = max(self._tx_interval_ms,
                                   min(MAX_INTERVAL_MS, int(rx_interval_ms)))
        self._last_tx: Optional[bool] = None
        #: Letzte vom Radio gemeldete VFO-A-Frequenz (Hz). Wird aus dem
        #: RX-Status-Sample übernommen und beim TX-Sample geprüft, um
        #: die SWR-Anzeige auf VHF/UHF zu unterdrücken (siehe
        #: :meth:`_apply_swr_value`).
        self._last_vfo_a_hz: Optional[int] = None

        self.setMinimumWidth(self.SIDEBAR_MIN_WIDTH)
        self.setMaximumWidth(self.SIDEBAR_MAX_WIDTH)
        self._build_ui()
        self._connect_dsp_signals()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # --- Kopf: TX-LED + Klartext ------------------------------------
        head_frame = QFrame()
        head_frame.setFrameShape(QFrame.StyledPanel)
        head_layout = QHBoxLayout(head_frame)
        head_layout.setContentsMargins(6, 4, 6, 4)
        head_layout.setSpacing(6)
        outer.addWidget(head_frame)

        head_layout.addStretch(1)
        self.tx_led = TxIndicator()
        head_layout.addWidget(self.tx_led)
        self.tx_label = QLabel("RX")
        font = self.tx_label.font()
        font.setBold(True)
        self.tx_label.setFont(font)
        self.tx_label.setMinimumWidth(34)
        head_layout.addWidget(self.tx_label)
        head_layout.addStretch(1)

        # --- S-Meter (links) + DSP-Slider (rechts daneben) --------------
        # Yaesu-Terminologie: Digital Noise Reduction (NR-Kommando) und
        # Digital Notch Filter (BC-Kommando = Auto-Notch). Die drei
        # Slider sind interaktiv — Klick auf die LED schaltet on/off,
        # Slider-Zug ändert den Pegel.
        smeter_frame = QFrame()
        smeter_frame.setFrameShape(QFrame.StyledPanel)
        smeter_v = QVBoxLayout(smeter_frame)
        smeter_v.setContentsMargins(6, 6, 6, 6)
        smeter_v.setSpacing(3)

        smeter_row = QHBoxLayout()
        smeter_row.setContentsMargins(0, 0, 0, 0)
        smeter_row.setSpacing(6)
        smeter_row.addStretch(1)

        # Eigene Spalte für das S-Meter, damit der "S-Meter"-Header genau
        # über dem Balken sitzt (und nicht — wie bei einem gemeinsamen
        # zentrierten Label oberhalb der Reihe — irgendwo zwischen
        # Balken und Slidern landet). Die vier DSP-Spalten bringen ihren
        # Header selber mit, alles steht so auf einer Linie.
        smeter_column = QVBoxLayout()
        smeter_column.setContentsMargins(0, 0, 0, 0)
        smeter_column.setSpacing(3)
        smeter_title = QLabel("S-Meter")
        smeter_title.setAlignment(Qt.AlignCenter)
        sf = smeter_title.font()
        sf.setBold(True)
        smeter_title.setFont(sf)
        smeter_column.addWidget(smeter_title)
        self.smeter_bar = make_smeter_bar()
        smeter_column.addWidget(self.smeter_bar, stretch=1, alignment=Qt.AlignHCenter)
        smeter_row.addLayout(smeter_column, stretch=0)

        # DSP-Slider rechts neben dem S-Meter — NB (0..10), DNF (on/off),
        # DNR (1..15), AGC (4 diskrete Positionen). DNF hat keinen Pegel
        # im FT-991A, deshalb ohne Slider, aber gleicher Spaltenaufbau.
        # AGC ist ein diskreter Modus-Schalter (AUTO/FAST/MID/SLOW).
        self.nb_slider = DspSlider("NB", supports_level=True,
                                   level_min=0, level_max=10)
        self.an_slider = DspSlider("DNF", supports_level=False)
        self.nr_slider = DspSlider("DNR", supports_level=True,
                                   level_min=1, level_max=15)
        self.agc_slider = AgcSlider()
        for s in (self.nb_slider, self.an_slider, self.nr_slider, self.agc_slider):
            smeter_row.addWidget(s)
        smeter_row.addStretch(1)
        smeter_v.addLayout(smeter_row, stretch=1)

        outer.addWidget(smeter_frame, stretch=0)

        # --- AF / RF-Gain (Mini-Bars) -----------------------------------
        gain_frame = QFrame()
        gain_frame.setFrameShape(QFrame.StyledPanel)
        gain_layout = QVBoxLayout(gain_frame)
        gain_layout.setContentsMargins(6, 4, 6, 4)
        gain_layout.setSpacing(2)

        self.af_gain_bar = MiniLevelBar("AF", 255)
        self.rf_gain_bar = MiniLevelBar("RF", 255)
        self.sq_gain_bar = MiniLevelBar("SQL", 100)
        gain_layout.addWidget(self.af_gain_bar)
        gain_layout.addWidget(self.rf_gain_bar)
        gain_layout.addWidget(self.sq_gain_bar)

        outer.addWidget(gain_frame)

        # --- TX-Bars (kleiner, hochkant, unten) -------------------------
        bars_frame = QFrame()
        bars_frame.setFrameShape(QFrame.StyledPanel)
        bars_outer = QVBoxLayout(bars_frame)
        bars_outer.setContentsMargins(6, 4, 6, 4)
        bars_outer.setSpacing(3)

        tx_label = QLabel("TX-Meter")
        tx_label.setAlignment(Qt.AlignCenter)
        tlf = tx_label.font()
        tlf.setBold(True)
        tlf.setPointSizeF(tlf.pointSizeF() * 0.85)
        tx_label.setFont(tlf)
        bars_outer.addWidget(tx_label)

        bars_layout = QHBoxLayout()
        bars_layout.setContentsMargins(0, 0, 0, 0)
        bars_layout.setSpacing(2)

        self._bars: Dict[MeterKind, ScaledMeterBar] = {}
        for kind, _info in meter_choices():
            bar = make_tx_meter_bar(kind)
            self._bars[kind] = bar
            bars_layout.addWidget(bar, stretch=1)
        bars_outer.addLayout(bars_layout, stretch=1)

        outer.addWidget(bars_frame, stretch=1)

        # --- Statuszeile ------------------------------------------------
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: gray; font-size: 10px;")
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # Lebenszyklus des Pollers
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._thread is not None and self._poller is not None

    def set_intervals(self, tx_interval_ms: int, rx_interval_ms: int) -> None:
        self._tx_interval_ms = max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, int(tx_interval_ms)))
        self._rx_interval_ms = max(self._tx_interval_ms,
                                   min(MAX_INTERVAL_MS, int(rx_interval_ms)))
        if self._poller is not None:
            QMetaObject.invokeMethod(
                self._poller,
                "set_interval_ms",
                Qt.QueuedConnection,
                Q_ARG(int, self._tx_interval_ms),
            )
            QMetaObject.invokeMethod(
                self._poller,
                "set_rx_interval_ms",
                Qt.QueuedConnection,
                Q_ARG(int, self._rx_interval_ms),
            )

    def start_polling(self) -> None:
        if self.is_running():
            return

        thread = QThread(self)
        poller = MeterPoller(
            self._cat,
            tx_interval_ms=self._tx_interval_ms,
            rx_interval_ms=self._rx_interval_ms,
        )
        poller.moveToThread(thread)

        poller.tx_sample.connect(self._on_tx_sample)
        poller.rx_sample.connect(self._on_rx_sample)
        poller.error_occurred.connect(self._on_poller_error)
        poller.connection_lost.connect(self.connection_lost)
        thread.started.connect(poller.start)
        thread.finished.connect(poller.deleteLater)

        self._thread = thread
        self._poller = poller
        thread.start()

    def stop_polling(self) -> None:
        if self._poller is not None:
            QMetaObject.invokeMethod(self._poller, "stop", Qt.QueuedConnection)
        if self._thread is not None:
            thread = self._thread
            thread.quit()
            if not thread.wait(1500):
                thread.terminate()
                thread.wait(500)
            self._thread = None
            self._poller = None

        self._reset_all()
        self.tx_led.set_state(TxIndicator.STATE_OFF)
        self.tx_label.setText("—")
        if self._last_tx:
            self.tx_status_changed.emit(False)
        self._last_tx = None

    def on_connection_changed(self, connected: bool) -> None:
        if connected:
            self.start_polling()
        else:
            self.stop_polling()

    # ------------------------------------------------------------------
    # Signal-Handler vom Poller
    # ------------------------------------------------------------------

    def _on_tx_sample(self, sample: object) -> None:
        if not isinstance(sample, TxMeterSample):
            return
        transmitting = sample.transmitting
        self.tx_led.set_active(transmitting)
        self.tx_label.setText("TX" if transmitting else "RX")

        # TX-Bars im RX-Modus gedimmt darstellen, in TX volle Farbe
        for kind, value in sample.values.items():
            bar = self._bars.get(kind)
            if bar is None:
                continue
            if kind == MeterKind.SWR:
                self._apply_swr_value(bar, value)
            else:
                bar.set_value(value)
            bar.set_enabled_visual(transmitting)

        # S-Meter im TX gedimmt darstellen
        self.smeter_bar.set_enabled_visual(not transmitting)

        self.status_label.setStyleSheet("color: gray;")
        if self._last_tx is None or self._last_tx != transmitting:
            self._last_tx = transmitting
            self.tx_status_changed.emit(transmitting)

    def _on_rx_sample(self, sample: object) -> None:
        if not isinstance(sample, RxStatusSample):
            return
        # VFO-A merken — wir brauchen die Frequenz im TX-Pfad, um auf
        # VHF/UHF die SWR-Anzeige zu unterdrücken.
        if sample.frequency_hz is not None:
            self._last_vfo_a_hz = sample.frequency_hz
        # S-Meter immer aktualisieren — ScaledMeterBar formatiert intern.
        self.smeter_bar.set_value(sample.smeter)

        # Slow-Path-Felder nur anwenden, wenn vom Poller mitgeschickt
        if sample.squelch is not None:
            self.smeter_bar.set_squelch(sample.squelch)
            self.sq_gain_bar.set_value(sample.squelch)
        if sample.af_gain is not None:
            self.af_gain_bar.set_value(sample.af_gain)
        if sample.rf_gain is not None:
            self.rf_gain_bar.set_value(sample.rf_gain)
        if sample.agc is not None:
            self.agc_slider.set_mode(sample.agc)
        if sample.noise_blanker is not None:
            on, level = sample.noise_blanker
            self.nb_slider.set_state(on)
            self.nb_slider.set_level(level)
        if sample.noise_reduction is not None:
            on, level = sample.noise_reduction
            self.nr_slider.set_state(on)
            self.nr_slider.set_level(level)
        if sample.auto_notch is not None:
            self.an_slider.set_state(sample.auto_notch)

        # Mode + Frequenzen an's Header weiterreichen
        if (
            sample.mode is not None
            or sample.frequency_hz is not None
            or sample.frequency_b_hz is not None
        ):
            self.rx_info_changed.emit(
                sample.mode,
                sample.frequency_hz or 0,
                sample.frequency_b_hz or 0,
            )

    def _on_poller_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #c62828;")
        self.status_label.setText(f"Meter-Fehler: {message}")

    # ------------------------------------------------------------------
    # SWR-Sonderbehandlung VHF/UHF
    # ------------------------------------------------------------------

    #: Frequenzgrenze, oberhalb der das ``RM6;``-Roh des FT-991A
    #: empirisch unbrauchbar wird (Roh-Wert geht sofort auf 255, obwohl
    #: das Front-Panel z. B. 1:1.2 anzeigt). Auf 6 m (50 MHz) verhält
    #: sich das Radio nach den uns vorliegenden Logs noch wie KW, daher
    #: wählen wir die Grenze bei 50 MHz und nicht schon bei 30 MHz.
    VHF_UHF_SWR_THRESHOLD_HZ = 50_000_000

    def _apply_swr_value(self, bar: "ScaledMeterBar", raw: int) -> None:
        """Setzt den SWR-Wert auf der Bar — auf VHF/UHF mit Hinweis statt Zahl."""
        if (
            self._last_vfo_a_hz is not None
            and self._last_vfo_a_hz >= self.VHF_UHF_SWR_THRESHOLD_HZ
        ):
            bar.set_unavailable(
                "SWR via CAT auf VHF/UHF nicht zuverlässig.\n"
                "Bitte das SWR am Front-Panel des Radios ablesen.\n"
                f"(Roh-Wert vom Radio: {max(0, int(raw))} / 255)"
            )
            return
        bar.clear_unavailable()
        bar.set_value(raw)

    # ------------------------------------------------------------------
    # DSP-Slider (NB / DNF / DNR) — User-Write-Pfad
    # ------------------------------------------------------------------

    def _connect_dsp_signals(self) -> None:
        self.nb_slider.toggled.connect(self._on_nb_toggled)
        self.nb_slider.level_changed.connect(self._on_nb_level_changed)
        self.an_slider.toggled.connect(self._on_dnf_toggled)
        self.nr_slider.toggled.connect(self._on_dnr_toggled)
        self.nr_slider.level_changed.connect(self._on_dnr_level_changed)
        self.agc_slider.mode_chosen.connect(self._on_agc_chosen)

    def _safe_write(self, writer, *, where: str) -> None:
        """Führt einen DSP-Write aus und fängt Fehler still in der Statuszeile."""
        if not self._cat.is_connected():
            return
        try:
            writer()
        except CatConnectionLostError:
            self.connection_lost.emit()
        except (CatError, OSError) as exc:
            self.status_label.setStyleSheet("color: #c62828;")
            self.status_label.setText(f"{where}: {exc}")
        except Exception:  # noqa: BLE001
            self.status_label.setStyleSheet("color: #c62828;")
            self.status_label.setText(f"{where}: unerwarteter Fehler")
            traceback.print_exc()

    def _on_nb_toggled(self, on: bool) -> None:
        self._safe_write(lambda: self._ft.write_noise_blanker(on),
                         where="NB schreiben")

    def _on_nb_level_changed(self, level: int) -> None:
        self._safe_write(lambda: self._ft.write_noise_blanker_level(level),
                         where="NB-Level schreiben")

    def _on_dnr_toggled(self, on: bool) -> None:
        self._safe_write(lambda: self._ft.write_noise_reduction(on),
                         where="DNR schreiben")

    def _on_dnr_level_changed(self, level: int) -> None:
        self._safe_write(lambda: self._ft.write_noise_reduction_level(level),
                         where="DNR-Level schreiben")

    def _on_dnf_toggled(self, on: bool) -> None:
        self._safe_write(lambda: self._ft.write_auto_notch(on),
                         where="DNF schreiben")

    def _on_agc_chosen(self, mode: object) -> None:
        if not isinstance(mode, AgcMode):
            return
        self._safe_write(lambda: self._ft.write_agc(mode),
                         where="AGC schreiben")

    # ------------------------------------------------------------------
    # Helfer
    # ------------------------------------------------------------------

    def _reset_all(self) -> None:
        for bar in self._bars.values():
            bar.reset()
            bar.set_enabled_visual(True)
        self.smeter_bar.reset()
        self.af_gain_bar.set_value(None)
        self.rf_gain_bar.set_value(None)
        self.sq_gain_bar.set_value(None)
        self.agc_slider.set_mode(None)
        self.nb_slider.set_state(None)
        self.nr_slider.set_state(None)
        self.an_slider.set_state(None)
