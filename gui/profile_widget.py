"""Profilverwaltung — Version 0.3.

Verwaltet ein vollständiges Audioprofil:

- Grundwerte (MIC Gain, MIC EQ on/off, Speech Processor + Level, SSB-BPF)
- Parametric MIC EQ (Normal-EQ, EX121–EX129)
- Processor-EQ (EX130–EX138)

CAT-Lesen/Schreiben läuft auf einem Worker-Thread, damit die GUI nicht
einfriert. Vor jedem Schreibvorgang wird der TX-Status geprüft.
"""

from __future__ import annotations

import time
import traceback
from copy import deepcopy
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cat import CatConnectionLostError, CatError, FT991CAT, SerialCAT, TxLockError
from cat.cat_errors import CatProtocolError, CatTimeoutError
from mapping.audio_mapping import (
    MIC_GAIN_DEFAULT,
    PROCESSOR_LEVEL_DEFAULT,
    SSB_BPF_DEFAULT_KEY,
)
from mapping.eq_mapping import NORMAL_EQ_MENUS, PROCESSOR_EQ_MENUS
from mapping.extended_mapping import defs_for_mode
from mapping.rx_mapping import DEFAULT_MODE_FOR_GROUP, RxMode, mode_group_for
from model import (
    AudioProfile,
    EQSettings,
    ExtendedSettings,
    PresetStore,
    VALID_MODE_GROUPS,
)

from .audio_basics_widget import AudioBasicsValues, AudioBasicsWidget
from .eq_editor_widget import EQEditorWidget
from .extended_widget import ExtendedSettingsWidget


# ---------------------------------------------------------------------
# Worker für vollständige Profil-IO
# ---------------------------------------------------------------------


# Anzahl Schritte beim Lesen eines kompletten Profils (ohne Extended):
# 1 × MIC Gain + 1 × Processor on/off + 1 × Processor Level
# + 1 × MIC EQ on/off + 1 × SSB BPF (EX112)
# + 9 × Normal-EQ + 9 × Processor-EQ = 23
_BASE_STEPS = 23


def _total_steps_for(mode_group: str) -> int:
    return _BASE_STEPS + len(defs_for_mode(mode_group))


class _ProfileIoWorker(QObject):
    """Liest oder schreibt ein komplettes Audioprofil."""

    progressed = Signal(int, int, str)
    # Liefert (AudioProfile, list-of-skipped-field-labels)
    read_done = Signal(object, list)
    write_done = Signal()
    failed = Signal(str, str)
    #: Verbindung ist während des Roundtrips weggebrochen (Kabel/Strom).
    #: Wird *statt* ``failed`` emittiert; kein Dialog, nur stille Anzeige.
    connection_lost = Signal()

    def __init__(
        self,
        ft: FT991CAT,
        *,
        write: bool,
        profile: Optional[AudioProfile] = None,
        live_name: str = "Live-Werte",
        live_mode_group: str = "SSB",
        baseline: Optional[AudioProfile] = None,
        target_mode: Optional[object] = None,
    ) -> None:
        super().__init__()
        self._ft = ft
        self._write = write
        self._profile = profile
        self._live_name = live_name
        self._live_mode_group = live_mode_group
        #: Wenn gesetzt und Modus identisch zum aktuellen Profil, schreibt
        #: der Worker im Diff-Modus (nur geänderte EX-Werte landen am Bus).
        self._baseline = baseline
        #: Wenn gesetzt (nur bei Read-Worker erlaubt), wird das Radio vor
        #: dem Lesen auf diesen Mode umgeschaltet. So folgt das Gerät dem
        #: Mode-Wechsel in der GUI.
        self._target_mode = target_mode
        self._step = 0
        self._total = _total_steps_for(
            profile.mode_group if profile is not None else live_mode_group
        )
        # Felder, die beim Lesen aufgrund unerwarteter Rohwerte
        # auf Default fielen (zur Anzeige in der GUI).
        self._skipped: list[str] = []

    # ---- Hauptpfad ---------------------------------------------------

    def run(self) -> None:
        try:
            if self._write:
                assert self._profile is not None
                self._do_write(self._profile)
                self.write_done.emit()
            else:
                profile = self._do_read()
                self.read_done.emit(profile, list(self._skipped))
        except TxLockError as exc:
            self.failed.emit("TX aktiv", str(exc))
        except CatConnectionLostError:
            # USB/Strom weg während der Operation — stillschweigend
            # abbrechen. MainWindow erfährt es über andere Pfade
            # (MeterPoller emittiert ebenfalls).
            self.connection_lost.emit()
        except CatTimeoutError as exc:
            self.failed.emit("Timeout", str(exc))
        except CatError as exc:
            self.failed.emit("CAT-Fehler", str(exc))
        except Exception as exc:  # noqa: BLE001
            log = self._ft.get_log()
            if log is not None:
                log.log_error(
                    "Unerwarteter Fehler im Profil-Worker:\n" + traceback.format_exc()
                )
            self.failed.emit("Unerwarteter Fehler", repr(exc))

    # ---- Fault-Tolerance --------------------------------------------

    def _safe_read(self, label: str, fn, default):
        """Liest ein Einzelfeld; bei CAT-Protokoll-Fehler Default + Log + skip.

        Andere Fehler (Timeout, TxLock, IO-Fehler) werden NICHT abgefangen
        und führen weiterhin zum Abbruch des gesamten Vorgangs.
        """
        self._tick(label)
        try:
            return fn()
        except CatProtocolError as exc:
            log = self._ft.get_log()
            if log is not None:
                log.log_warn(
                    f"{label}: unerwarteter Rohwert ({exc}) — Default '{default}' "
                    "verwendet"
                )
            self._skipped.append(label)
            return default

    # ---- Lesen -------------------------------------------------------

    def _do_read(self) -> AudioProfile:
        log = self._ft.get_log()
        if log is not None:
            log.log_info("=== Komplettes Profil lesen ===")

        # Wenn die GUI den Mode-Wechsel angefordert hat, schalten wir das
        # Radio zuerst um. Eine kurze Pause gibt dem Radio Zeit, die mode-
        # spezifischen EX-Register zu aktualisieren.
        if self._target_mode is not None:
            try:
                self._ft.set_rx_mode(self._target_mode)
                # Die EX-Register sind bei Yaesu nicht streng mode-gefiltert,
                # aber ein paar Geräte brauchen einen Wimpernschlag, bis sie
                # die neue Operating-Lane melden. 80 ms ist ein konservativer
                # Default, der in der Praxis keine wahrnehmbare Latenz ergibt.
                time.sleep(0.08)
            except (CatProtocolError, CatTimeoutError) as exc:
                # Mode-Set fehlgeschlagen — wir lesen dennoch weiter, der
                # Anwender sieht dann eben den alten Mode-Stand.
                if log is not None:
                    log.log_warn(f"Mode-Set fehlgeschlagen: {exc}")

        mic_gain = self._safe_read(
            "MIC Gain (MG)", self._ft.get_mic_gain, MIC_GAIN_DEFAULT
        )
        processor_enabled = self._safe_read(
            "Speech Processor (PR0)", self._ft.get_processor_enabled, False
        )
        processor_level = self._safe_read(
            "Processor Level (PL)", self._ft.get_processor_level, PROCESSOR_LEVEL_DEFAULT
        )
        mic_eq_enabled = self._safe_read(
            "Parametric MIC EQ (PR1)", self._ft.get_mic_eq_enabled, False
        )
        ssb_bpf = self._safe_read(
            "SSB TX-Bandbreite (EX112)", self._ft.get_ssb_bpf, SSB_BPF_DEFAULT_KEY
        )

        # EQ-Sets: bei Fehler Default-EQ verwenden (statt Komplettabbruch)
        normal_eq = self._safe_eq_read("Normal-EQ", NORMAL_EQ_MENUS)
        processor_eq = self._safe_eq_read("Processor-EQ", PROCESSOR_EQ_MENUS)

        # Extended-Settings tolerant lesen — einzelne Felder können fehlen
        ext_values = self._ft.read_extended_for_mode(
            self._live_mode_group,
            progress=self._eq_progress("Erweitert"),
            tolerate_errors=True,
            skipped=self._skipped,
        )
        extended = ExtendedSettings()
        extended.apply_keyed_dict(ext_values)

        return AudioProfile(
            name=self._live_name,
            mode_group=self._live_mode_group,
            mic_gain=mic_gain,
            mic_eq_enabled=mic_eq_enabled,
            speech_processor_enabled=processor_enabled,
            speech_processor_level=processor_level,
            ssb_tx_bpf=ssb_bpf,
            normal_eq=normal_eq,
            processor_eq=processor_eq,
            extended=extended,
        )

    def _safe_eq_read(self, label: str, menus) -> EQSettings:
        """Liest ein EQ-Set; defekte Bänder fallen auf Default, der Rest bleibt.

        ``read_eq`` wird mit ``tolerate_bands=True`` aufgerufen — Decode-Fehler
        einzelner Bänder bringen nicht das gesamte Set zu Fall. Nur wenn die
        Phase-1-Roh-Lesung selbst scheitert (Timeout, garbled Response),
        landet der gesamte EQ auf Default.
        """
        band_skipped: list[str] = []
        try:
            eq = self._ft.read_eq(
                menus,
                progress=self._eq_progress(label),
                tolerate_bands=True,
                skipped=band_skipped,
            )
            for entry in band_skipped:
                self._skipped.append(f"{label} — {entry}")
            return eq
        except CatProtocolError as exc:
            log = self._ft.get_log()
            if log is not None:
                log.log_warn(
                    f"{label}: konnte nicht gelesen werden ({exc}) — "
                    "Default-EQ verwendet"
                )
            self._skipped.append(label)
            # Progress-Counter aufholen, damit der Dialog nicht hängenbleibt.
            # ``read_eq`` emittiert 9 Ticks; im Fehlerfall ist unbekannt, wie
            # weit es kam. Wir überspringen sicherheitshalber den Rest.
            for _ in range(9):
                self._step += 1
            self.progressed.emit(self._step, self._total, f"{label} übersprungen")
            return EQSettings.default()

    # ---- Schreiben ---------------------------------------------------

    def _do_write(self, profile: AudioProfile) -> None:
        log = self._ft.get_log()
        # Baseline nur als Diff-Quelle gelten lassen, wenn die Mode-Gruppe
        # zum Profil passt — sonst sind die Extended-Werte nicht
        # vergleichbar und wir schreiben zur Sicherheit alles.
        baseline = self._baseline
        if baseline is not None and baseline.mode_group != profile.mode_group:
            baseline = None

        if log is not None:
            mode = "Diff" if baseline is not None else "voll"
            log.log_info(
                f"=== Profil ‘{profile.name}’ schreiben ({mode}) ==="
            )

        # TX-Lock einmal — die einzelnen Helfer schreiben dann mit tx_lock=False.
        self._ft.ensure_rx()

        written = 0

        if baseline is None or baseline.mic_gain != profile.mic_gain:
            self._tick(f"MIC Gain -> {profile.mic_gain}")
            self._ft.set_mic_gain(profile.mic_gain)
            written += 1

        if (baseline is None or
                baseline.speech_processor_enabled != profile.speech_processor_enabled):
            self._tick(
                f"Speech Processor -> "
                f"{'an' if profile.speech_processor_enabled else 'aus'}"
            )
            self._ft.set_processor_enabled(profile.speech_processor_enabled)
            written += 1

        if (baseline is None or
                baseline.speech_processor_level != profile.speech_processor_level):
            self._tick(f"Processor Level -> {profile.speech_processor_level}")
            self._ft.set_processor_level(profile.speech_processor_level)
            written += 1

        if (baseline is None or
                baseline.mic_eq_enabled != profile.mic_eq_enabled):
            self._tick(
                f"Parametric MIC EQ -> {'an' if profile.mic_eq_enabled else 'aus'}"
            )
            self._ft.set_mic_eq_enabled(profile.mic_eq_enabled)
            written += 1

        if baseline is None or baseline.ssb_tx_bpf != profile.ssb_tx_bpf:
            self._tick(f"SSB TX-Bandbreite -> {profile.ssb_tx_bpf}")
            self._ft.set_ssb_bpf(profile.ssb_tx_bpf)
            written += 1

        written += self._ft.write_eq(
            profile.normal_eq,
            NORMAL_EQ_MENUS,
            progress=self._eq_progress("Normal-EQ schreiben"),
            tx_lock=False,
            baseline=baseline.normal_eq if baseline is not None else None,
        )
        written += self._ft.write_eq(
            profile.processor_eq,
            PROCESSOR_EQ_MENUS,
            progress=self._eq_progress("Processor-EQ schreiben"),
            tx_lock=False,
            baseline=baseline.processor_eq if baseline is not None else None,
        )

        # Erweiterte Einstellungen schreiben (mode-spezifisch).
        written += self._ft.write_extended_for_mode(
            profile.mode_group,
            profile.extended.as_keyed_dict(),
            progress=self._eq_progress("Erweitert schreiben"),
            tx_lock=False,
            baseline=(baseline.extended.as_keyed_dict()
                      if baseline is not None else None),
        )

        if log is not None:
            log.log_info(f"=== Profil-Write fertig: {written} Felder geschrieben ===")

    # ---- Progress-Helfer --------------------------------------------

    def _tick(self, label: str) -> None:
        self._step += 1
        self.progressed.emit(self._step, self._total, label)

    def _eq_progress(self, prefix: str):
        """Liefert ein Progress-Callback für ``read_eq``/``write_eq``,
        das die Gesamtschritte des Profils mitführt."""
        def _cb(step: int, total: int, label: str) -> None:
            self._step += 1
            self.progressed.emit(self._step, self._total, f"{prefix}: {label}")
        return _cb


# ---------------------------------------------------------------------
# Profil-Widget
# ---------------------------------------------------------------------


class ProfileWidget(QWidget):
    """Vereint Profilauswahl, Grundwerte, Normal-EQ und Processor-EQ."""

    #: Wird emittiert, wenn ein laufender Read/Write feststellt, dass die
    #: CAT-Verbindung weggebrochen ist (USB/Strom). Das Hauptfenster fängt
    #: das ab und reagiert (LED, Reconnect-Watcher) — ohne Fehler-Dialog.
    connection_lost = Signal()

    def __init__(
        self,
        serial_cat: SerialCAT,
        preset_store: PresetStore,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._cat = serial_cat
        self._store = preset_store
        self._suppress_dirty = False
        self._dirty = False
        self._current_profile_name: Optional[str] = None
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_ProfileIoWorker] = None
        self._progress_dialog: Optional[QProgressDialog] = None
        #: Wenn ``True``, wird die "Erweiterte Einstellungen"-Sektion bei
        #: SSB-Modus ausgeblendet. Wird vom Hauptfenster über
        #: :meth:`set_hide_extended_in_ssb` gesetzt.
        self._hide_extended_in_ssb = False

        # --- Auto-Sync-Mechanik --------------------------------------------
        # Wenn die GUI mit dem Gerät verbunden ist, schreibt jede Editor-
        # Änderung nach kurzer Debounce-Pause automatisch das komplette
        # Profil ins Radio. Mode- und Profil-Wechsel triggern direkte Read-
        # bzw. Write-Aktionen. Ein QTimer fasst Tastatur-/Maus-Ereignisse
        # zu einem einzigen Schreibvorgang zusammen.
        self._auto_write_timer = QTimer(self)
        self._auto_write_timer.setSingleShot(True)
        self._auto_write_timer.setInterval(350)
        self._auto_write_timer.timeout.connect(self._flush_auto_write)
        #: Wird gesetzt, wenn während eines laufenden Workers eine neue
        #: Aktion angefragt wird. Tuple: (kind, optional profile snapshot).
        self._pending_action: Optional[tuple[str, Optional[AudioProfile]]] = None
        #: Ob der aktuell laufende Worker stille Auto-Aktion ist (keine
        #: Dialoge, kein Progress-Fenster).
        self._worker_silent: bool = False
        #: Das zuletzt verifiziert ans Gerät übertragene Profil. Dient als
        #: Baseline für Diff-Writes — nur Felder, die sich gegenüber dieser
        #: Snapshot verändert haben, werden tatsächlich übertragen.
        self._last_synced_profile: Optional[AudioProfile] = None
        #: Profil, das aktuell vom Worker geschrieben wird (für späteres
        #: Update von ``_last_synced_profile`` nach erfolgreichem Write).
        self._writing_profile: Optional[AudioProfile] = None
        #: Aktueller TX-Zustand des Radios. Wird vom Hauptfenster über
        #: :meth:`notify_tx_state` gemeldet. Bei TX→RX-Übergang versuchen
        #: wir ausstehende Schreibvorgänge automatisch erneut.
        self._tx_active: bool = False
        #: ``True``, wenn ein Schreibvorgang zuletzt am TX-Lock gescheitert
        #: ist und auf den TX→RX-Übergang wartet.
        self._tx_block_pending: bool = False
        #: Zuletzt vom Radio gemeldete Mode-Gruppe. Verhindert, dass jedes
        #: Polling-Sample ein Read auslöst — wir reagieren nur auf echte
        #: Wechsel.
        self._last_radio_mode_group: Optional[str] = None

        self._build_ui()
        self._reload_profile_list(select_first=True)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # === Kopf: Profil-Auswahl + Aktionen + Mode + Live-Sync (alles in
        # einer Zeile, damit der vertikale Platz für den Editor maximal ist).
        # Hinweis: „Aus Gerät lesen" und „In Gerät schreiben" gibt es nicht
        # mehr — Mode-Wechsel löst automatisch ein Read aus, jede Änderung
        # sowie ein Profil-Wechsel ein Write.
        header = QFrame()
        header.setFrameShape(QFrame.StyledPanel)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.setSpacing(6)
        # ``SetMinimumSize`` zwingt den umgebenden Container (und damit das
        # Hauptfenster), nie schmaler zu werden als die Summe der
        # Mindestbreiten der Children. Ohne diesen Constraint dürfen die
        # Buttons rechts vom Profil-Dropdown auf 0 schrumpfen und das Combo
        # überlappen — genau das wollen wir hier verhindern.
        header_layout.setSizeConstraint(QLayout.SetMinimumSize)
        outer.addWidget(header)

        header_layout.addWidget(QLabel("<b>Profil:</b>"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(300)
        self.profile_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        header_layout.addWidget(self.profile_combo, stretch=1)

        # Die Buttons bekommen ``Minimum`` als horizontale SizePolicy —
        # damit liefert Qt die ``sizeHint``-Breite als hartes Minimum
        # und der Knopf wird auch bei knappem Platz nicht weiter
        # zusammengedrückt, sondern das Fenster wird breiter gehalten.
        button_policy = QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)

        self.save_button = QPushButton("Profil speichern")
        self.save_button.setSizePolicy(button_policy)
        self.save_button.clicked.connect(self._on_save_clicked)
        header_layout.addWidget(self.save_button)

        self.save_as_button = QPushButton("Speichern unter…")
        self.save_as_button.setSizePolicy(button_policy)
        self.save_as_button.clicked.connect(self._on_save_as_clicked)
        header_layout.addWidget(self.save_as_button)

        self.delete_button = QPushButton("Profil löschen")
        self.delete_button.setSizePolicy(button_policy)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        header_layout.addWidget(self.delete_button)

        header_layout.addSpacing(18)

        header_layout.addWidget(QLabel("Mode-Gruppe:"))
        self.mode_combo = QComboBox()
        self.mode_combo.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        for mg in VALID_MODE_GROUPS:
            self.mode_combo.addItem(mg)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header_layout.addWidget(self.mode_combo)

        header_layout.addSpacing(18)

        self._sync_label = QLabel("Live-Sync: aus")
        self._sync_label.setStyleSheet("color: gray;")
        # Feste Breite — verhindert, dass sich das Layout (Buttons, Combos)
        # bei wechselndem Status-Text seitwärts verschiebt. 145px reichen
        # für die längsten Meldungen wie "Sync: ⏸ wartet auf RX".
        self._sync_label.setFixedWidth(145)
        self._sync_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        header_layout.addWidget(self._sync_label)

        # === Scrollbarer Body mit Grundwerten und beiden EQ-Sets ===
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, stretch=1)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        scroll.setWidget(body)

        # Grundwerte
        self.basics = AudioBasicsWidget()
        # Wir wollen sowohl Dirty-Status setzen als auch den aktiven EQ-Pfad
        # neu berechnen, sobald der Speech Processor an/aus geschaltet wird.
        self.basics.changed.connect(self._mark_dirty)
        self.basics.changed.connect(self._update_eq_active_path)
        body_layout.addWidget(self.basics)

        # === Normal-EQ ===
        # Titel + Untertitel machen sofort klar, wann dieser EQ greift.
        self.normal_eq_box = QGroupBox(
            "Parametric MIC EQ — Normal  (aktiv wenn Speech Processor aus)"
        )
        normal_layout = QVBoxLayout(self.normal_eq_box)
        normal_layout.setContentsMargins(8, 12, 8, 8)
        normal_layout.setSpacing(4)

        normal_hint = QLabel(
            "Greift in den Audio-Pfad, solange der Speech Processor "
            "ausgeschaltet ist. Menüs EX119–EX127."
        )
        normal_hint.setWordWrap(True)
        normal_hint.setStyleSheet("color: gray;")
        normal_layout.addWidget(normal_hint)

        self.normal_eq_editor = EQEditorWidget()
        self.normal_eq_editor.changed.connect(self._mark_dirty)
        normal_layout.addWidget(self.normal_eq_editor)
        body_layout.addWidget(self.normal_eq_box)

        # === Processor-EQ ===  (nur in SSB sichtbar)
        self.processor_eq_box = QGroupBox(
            "Processor EQ  (aktiv wenn Speech Processor an)"
        )
        processor_layout = QVBoxLayout(self.processor_eq_box)
        processor_layout.setContentsMargins(8, 12, 8, 8)
        processor_layout.setSpacing(4)

        processor_hint = QLabel(
            "Greift erst bei eingeschaltetem Speech Processor und ersetzt "
            "dann den Normal-EQ. Menüs EX128–EX136."
        )
        processor_hint.setWordWrap(True)
        processor_hint.setStyleSheet("color: gray;")
        processor_layout.addWidget(processor_hint)

        self.processor_eq_editor = EQEditorWidget()
        self.processor_eq_editor.changed.connect(self._mark_dirty)
        processor_layout.addWidget(self.processor_eq_editor)
        body_layout.addWidget(self.processor_eq_box)

        # Erweiterte Einstellungen (Version 0.5)
        self.extended_editor = ExtendedSettingsWidget()
        self.extended_editor.changed.connect(self._mark_dirty)
        body_layout.addWidget(self.extended_editor)

        body_layout.addStretch(1)

        # Status-Zeile
        self.status_label = QLabel("Bereit.")
        self.status_label.setStyleSheet("color: gray;")
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # CAT-Verfügbarkeit
    # ------------------------------------------------------------------

    def set_cat_available(self, available: bool) -> None:
        if available:
            self._sync_label.setText("Live-Sync: aktiv")
            self._sync_label.setStyleSheet("color: #2ea043;")
            # Beim Connect direkt ein Read anstoßen — die GUI soll mit den
            # aktuellen Geräte-Werten starten. Der erste Mode-Wechsel hat
            # ggf. schon einen Read angefordert; doppelte Reads sind durch
            # die Pending-Queue serialisiert.
            self._schedule_action("read")
        else:
            self._sync_label.setText("Live-Sync: aus")
            self._sync_label.setStyleSheet("color: gray;")
            # Bei Verlust der Verbindung Auto-Write-Pending verwerfen, damit
            # nach Reconnect nicht stale Werte überschrieben werden.
            self._auto_write_timer.stop()
            self._pending_action = None
            # Baseline ungültig machen — nach Reconnect fängt der erste
            # Auto-Read wieder eine frische Baseline.
            self._last_synced_profile = None
            self._last_radio_mode_group = None
            self._tx_block_pending = False
            self.profile_combo.setEnabled(True)

    # ------------------------------------------------------------------
    # Profilauswahl
    # ------------------------------------------------------------------

    def _reload_profile_list(self, *, select_first: bool = False) -> None:
        names = self._store.names()
        previously = self._current_profile_name
        self._suppress_dirty = True
        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            for name in names:
                self.profile_combo.addItem(name)
            if names:
                target = previously if (previously in names and not select_first) else names[0]
                idx = self.profile_combo.findText(target)
                if idx >= 0:
                    self.profile_combo.setCurrentIndex(idx)
        finally:
            self.profile_combo.blockSignals(False)
            self._suppress_dirty = False

        if names:
            self._apply_profile_to_editors(self.profile_combo.currentText())
        else:
            self._current_profile_name = None
            self._set_editors_enabled(False)

        self._dirty = False
        self._refresh_status()

    def _apply_profile_to_editors(self, name: str) -> None:
        profile = self._store.find(name)
        if profile is None:
            return
        self._current_profile_name = profile.name
        self._suppress_dirty = True
        try:
            idx = self.mode_combo.findText(profile.mode_group)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
            self.basics.set_values(
                AudioBasicsValues(
                    mic_gain=profile.mic_gain,
                    mic_eq_enabled=profile.mic_eq_enabled,
                    speech_processor_enabled=profile.speech_processor_enabled,
                    speech_processor_level=profile.speech_processor_level,
                    ssb_tx_bpf=profile.ssb_tx_bpf,
                )
            )
            self.normal_eq_editor.set_settings(profile.normal_eq)
            self.processor_eq_editor.set_settings(profile.processor_eq)
            self.extended_editor.set_values(profile.extended)
            self._apply_mode_relevance(profile.mode_group)
        finally:
            self._suppress_dirty = False
        self._dirty = False
        self._refresh_status()

    def _apply_mode_relevance(self, mode_group: str) -> None:
        """Versteckt alle UI-Sektionen, die in ``mode_group`` keinen Effekt haben."""
        is_ssb = mode_group.upper() == "SSB"
        self.basics.apply_mode_relevance(mode_group)
        self.processor_eq_box.setVisible(is_ssb)
        self.extended_editor.apply_mode_relevance(mode_group)
        # User-Override: Erweiterte Einstellungen bei SSB ggf. komplett
        # ausblenden. In allen anderen Modi (und ohne Override) bleibt der
        # Container sichtbar — die mode-spezifische Sub-Logik im
        # ExtendedSettingsWidget regelt, was darin angezeigt wird.
        show_extended = not (is_ssb and self._hide_extended_in_ssb)
        self.extended_editor.setVisible(show_extended)
        self._update_eq_active_path()

    def set_hide_extended_in_ssb(self, hide: bool) -> None:
        """Wird vom Hauptfenster aufgerufen, wenn die User-Einstellung
        wechselt. Wendet die neue Sichtbarkeit sofort an, ohne dass das
        Profil neu geladen werden muss.
        """
        hide = bool(hide)
        if hide == self._hide_extended_in_ssb:
            return
        self._hide_extended_in_ssb = hide
        # Sichtbarkeit für den aktuellen Modus neu auswerten.
        profile = (
            self._store.find(self._current_profile_name)
            if self._current_profile_name
            else None
        )
        mode_group = profile.mode_group if profile else self.mode_combo.currentText()
        self._apply_mode_relevance(mode_group)

    def _update_eq_active_path(self) -> None:
        """Markiert je nach Speech-Processor-Zustand, welcher EQ gerade greift.

        Wird sowohl bei Profil-Wechsel, Mode-Wechsel als auch bei jedem
        ``basics.changed`` aufgerufen.
        """
        # Außerhalb von SSB gibt es keinen Speech Processor — dort greift
        # konzeptionell immer der Normal-EQ.
        mode_group = self.mode_combo.currentText().upper()
        is_ssb = mode_group == "SSB"
        processor_on = (
            is_ssb and bool(self.basics.get_values().speech_processor_enabled)
        )

        if not is_ssb:
            self.normal_eq_editor.set_path_status(
                active=True,
                hint_text="● aktiv — in dieser Betriebsart gibt es keinen Speech Processor",
            )
            return

        if processor_on:
            self.normal_eq_editor.set_path_status(
                active=False,
                hint_text="○ inaktiv — Speech Processor ist an, der Processor-EQ greift",
            )
            self.processor_eq_editor.set_path_status(
                active=True,
                hint_text="● aktiv — wird gerade verwendet",
            )
        else:
            self.normal_eq_editor.set_path_status(
                active=True,
                hint_text="● aktiv — wird gerade verwendet",
            )
            self.processor_eq_editor.set_path_status(
                active=False,
                hint_text="○ inaktiv — Speech Processor ist aus",
            )

    def _on_mode_changed(self, _idx: int) -> None:
        # Initialer Aufruf vor dem Build kann passieren (Combo wird vor allen
        # anderen Widgets erstellt). Erst nach dem Build alles aktualisieren.
        if hasattr(self, "extended_editor"):
            self._apply_mode_relevance(self.mode_combo.currentText())
        if self._suppress_dirty:
            return
        if self._cat.is_connected():
            new_group = self.mode_combo.currentText()
            # Wenn das Radio bereits in der gewünschten Gruppe ist (typisch
            # bei Combo-Wechsel via ``notify_radio_mode``), reicht ein
            # einfaches Read — wir wollen ja nicht in Echo-Loops geraten.
            if new_group == self._last_radio_mode_group:
                self._schedule_action("read")
            else:
                target_mode = DEFAULT_MODE_FOR_GROUP.get(new_group)
                if target_mode is not None:
                    # Wir nehmen vorweg an, dass das Radio gleich in der
                    # neuen Gruppe sein wird. Damit ignoriert
                    # ``notify_radio_mode`` Polling-Samples, die noch den
                    # alten Mode zeigen, und löst keinen Pong-Effekt aus.
                    self._last_radio_mode_group = new_group
                    self._schedule_action("set_mode_and_read", target_mode)
                else:
                    self._schedule_action("read")
        self._mark_dirty()

    def _set_editors_enabled(self, enabled: bool) -> None:
        for w in (
            self.basics,
            self.normal_eq_editor,
            self.processor_eq_editor,
            self.extended_editor,
        ):
            w.setEnabled(enabled)

    def _on_profile_selected(self) -> None:
        if self._suppress_dirty:
            return
        if self._dirty and self._current_profile_name:
            answer = QMessageBox.question(
                self,
                "Ungespeicherte Änderungen",
                (
                    f"Profil ‘{self._current_profile_name}’ hat ungespeicherte "
                    "Änderungen. Vorher speichern?"
                ),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Cancel:
                idx = self.profile_combo.findText(self._current_profile_name)
                if idx >= 0:
                    self._suppress_dirty = True
                    self.profile_combo.setCurrentIndex(idx)
                    self._suppress_dirty = False
                return
            if answer == QMessageBox.Yes:
                self._save_current_profile_inplace()
        new_name = self.profile_combo.currentText()
        self._apply_profile_to_editors(new_name)
        # Frisch geladenes Profil direkt ins Gerät übertragen, damit die
        # Realität am Radio dem entspricht, was die UI anzeigt.
        if self._cat.is_connected():
            self._schedule_action("write_full")

    def _mark_dirty(self) -> None:
        if self._suppress_dirty:
            return
        self._dirty = True
        self._refresh_status()
        # Debounce: nach kurzer Ruhephase schreiben wir das komplette
        # Profil in einem Rutsch ins Gerät. Mehrere kurz aufeinander­
        # folgende Änderungen (z. B. Drag eines EQ-Punkts) werden so zu
        # genau einem Schreibvorgang zusammengeführt.
        if self._cat.is_connected():
            self._auto_write_timer.start()

    def _refresh_status(self) -> None:
        name = self._current_profile_name or "—"
        flag = " • ungespeichert" if self._dirty else ""
        self.status_label.setText(f"Aktives Profil: {name}{flag}")

    # ------------------------------------------------------------------
    # Profil speichern / löschen
    # ------------------------------------------------------------------

    def _build_profile_from_editors(self, name: str) -> AudioProfile:
        existing = self._store.find(name)
        if existing is not None:
            new_profile = deepcopy(existing)
            new_profile.name = name
        else:
            new_profile = AudioProfile(name=name)
        new_profile.mode_group = self.mode_combo.currentText()
        basics = self.basics.get_values()
        new_profile.mic_gain = basics.mic_gain
        new_profile.mic_eq_enabled = basics.mic_eq_enabled
        new_profile.speech_processor_enabled = basics.speech_processor_enabled
        new_profile.speech_processor_level = basics.speech_processor_level
        new_profile.ssb_tx_bpf = basics.ssb_tx_bpf
        new_profile.normal_eq = self.normal_eq_editor.get_settings()
        new_profile.processor_eq = self.processor_eq_editor.get_settings()
        new_profile.extended = self.extended_editor.get_values()
        return new_profile

    def _save_current_profile_inplace(self) -> None:
        if not self._current_profile_name:
            return
        profile = self._build_profile_from_editors(self._current_profile_name)
        try:
            self._store.upsert(profile)
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return
        self._dirty = False
        self._refresh_status()

    def _on_save_clicked(self) -> None:
        if not self._current_profile_name:
            self._on_save_as_clicked()
            return
        self._save_current_profile_inplace()

    def _on_save_as_clicked(self) -> None:
        default_name = self._current_profile_name or "Neues Profil"
        new_name, ok = QInputDialog.getText(
            self,
            "Profil speichern unter…",
            "Name des neuen Profils:",
            text=default_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if self._store.find(new_name) is not None:
            answer = QMessageBox.question(
                self,
                "Überschreiben?",
                f"Ein Profil ‘{new_name}’ existiert bereits. Überschreiben?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        profile = self._build_profile_from_editors(new_name)
        try:
            self._store.upsert(profile)
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return

        self._current_profile_name = new_name
        self._reload_profile_list()

    def _on_delete_clicked(self) -> None:
        if not self._current_profile_name:
            return
        name = self._current_profile_name
        answer = QMessageBox.question(
            self,
            "Profil löschen?",
            f"Profil ‘{name}’ wirklich löschen?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._store.remove(name)
        self._current_profile_name = None
        self._reload_profile_list(select_first=True)

    # ------------------------------------------------------------------
    # CAT-Operationen (Worker-Thread)
    # ------------------------------------------------------------------

    def request_auto_read(self) -> bool:
        """Stößt einen Read-Vorgang an, sofern die Voraussetzungen stimmen.

        Wird vom Hauptfenster direkt nach einem erfolgreichen Connect (inkl.
        Auto-Reconnect) gerufen, damit die GUI immer mit dem aktuellen
        Geräte-Zustand startet.

        Gibt ``True`` zurück, wenn der Read sofort gestartet oder als
        Pending eingereiht wurde; ``False``, wenn keine Verbindung besteht.
        Es werden **keine Dialoge** angezeigt — Auto-Aktionen sollen still
        bleiben.
        """
        if not self._cat.is_connected():
            return False
        self._schedule_action("read")
        return True

    # ------------------------------------------------------------------
    # Aktions-Planer (Auto-Read / Auto-Write)
    # ------------------------------------------------------------------

    def _schedule_action(
        self,
        kind: str,
        payload: Optional[object] = None,
    ) -> None:
        """Plant eine CAT-Aktion ein.

        Aktionstypen + Payload:

        * ``"read"``                — ``payload`` ignoriert
        * ``"write_full"``          — ``payload`` ist ``AudioProfile`` (optional)
        * ``"set_mode_and_read"``   — ``payload`` ist :class:`RxMode`

        Wenn aktuell schon ein Worker läuft, wird die Aktion als **pending**
        gespeichert (letzte gewinnt). Sonst wird sie sofort gestartet.
        """
        if kind not in ("read", "write_full", "set_mode_and_read"):
            raise ValueError(f"unbekannte Aktion: {kind}")
        if self._worker_thread is not None:
            # Spätere Aktionen überschreiben pendings, wir wollen immer den
            # neuesten Stand abbilden.
            self._pending_action = (kind, payload)
            return
        # Bei einem direkten Read den Debounce-Timer stoppen — der Read
        # überschreibt die UI sowieso.
        if kind in ("read", "set_mode_and_read"):
            self._auto_write_timer.stop()
        self._dispatch_action(kind, payload)

    def _dispatch_action(
        self,
        kind: str,
        payload: Optional[object],
    ) -> None:
        if not self._cat.is_connected():
            return
        if kind == "read":
            self._start_worker(write=False, silent=True)
            return
        if kind == "set_mode_and_read":
            target = payload if isinstance(payload, RxMode) else None
            # Mode-Set ist effektiv ein Schreibvorgang am Radio. Während TX
            # parken wir die Aktion und holen sie beim TX→RX-Edge nach.
            if self._tx_active:
                self._pending_action = (kind, payload)
                self._tx_block_pending = True
                self._sync_label.setText("Sync: ⏸ TX aktiv")
                self._sync_label.setStyleSheet("color: #ffae42;")
                return
            self._start_worker(
                write=False, silent=True, target_mode=target
            )
            return
        if kind == "write_full":
            profile = payload if isinstance(payload, AudioProfile) else None
            name = (profile.name if profile is not None
                    else self._current_profile_name or "Live")
            prof = profile or self._build_profile_from_editors(name)
            # Wenn das Radio sendet, Schreibvorgang nicht starten — er würde
            # mit ``TxLockError`` fehlschlagen. Wir merken uns die Absicht
            # und re-triggern beim TX→RX-Übergang.
            if self._tx_active:
                self._pending_action = (kind, prof)
                self._tx_block_pending = True
                self._sync_label.setText("Sync: ⏸ TX aktiv")
                self._sync_label.setStyleSheet("color: #ffae42;")
                return
            self._start_worker(write=True, profile=prof, silent=True)

    def _flush_auto_write(self) -> None:
        """Wird vom Debounce-Timer aufgerufen, wenn der Anwender seine
        Änderungen wahrscheinlich abgeschlossen hat."""
        if not self._cat.is_connected():
            return
        name = self._current_profile_name or "Live"
        profile = self._build_profile_from_editors(name)
        self._schedule_action("write_full", profile)

    # ------------------------------------------------------------------
    # Externe Notifications (Meter-Poller / MainWindow)
    # ------------------------------------------------------------------

    def notify_tx_state(self, active: bool) -> None:
        """Wird vom Hauptfenster bei jeder TX/RX-Statusänderung gerufen.

        Bei TX→RX-Übergang versuchen wir einen wegen ``TxLockError`` oder
        wegen TX-Block ausgesetzten Schreib-/Mode-Vorgang automatisch
        erneut.
        """
        active = bool(active)
        was_tx = self._tx_active
        self._tx_active = active
        if not (was_tx and not active):
            return
        self._tx_block_pending = False
        if self._worker_thread is not None:
            # Worker noch beschäftigt — nach Abschluss wird die Pending-
            # Queue ohnehin abgearbeitet.
            return
        # Bei TX→RX zuerst eine eventuell geparkte Pending-Aktion
        # ausführen (z. B. Mode-Wechsel oder Profilwechsel-Write).
        if self._pending_action is not None and self._cat.is_connected():
            kind, payload = self._pending_action
            self._pending_action = None
            self._dispatch_action(kind, payload)
            return
        # Sonst: noch unsynchronisierte Editor-Änderungen wegschreiben.
        if self._dirty and self._cat.is_connected():
            self._flush_auto_write()

    def notify_radio_mode(self, mode: object) -> None:
        """Wird vom Hauptfenster bei jedem RX-Sample mit der aktuellen
        Radio-Mode (``RxMode``) gerufen.

        Bei einem Wechsel der Mode-Gruppe (SSB/AM/FM/DATA/C4FM) wird die
        Profil-Mode-Combo nachgezogen — was wiederum einen Auto-Read der
        zugehörigen Werte triggert. Ignoriert Modi außerhalb der unter­
        stützten Gruppen (CW, RTTY, …).

        Während ein Worker läuft (z. B. weil die GUI gerade selber einen
        Mode-Set initiiert hat), werden alle Samples ignoriert — sonst
        gerieten wir in Pong-Effekte mit veralteten Polling-Werten.
        """
        if not isinstance(mode, RxMode):
            return
        if self._worker_thread is not None:
            return
        group = mode_group_for(mode)
        if group not in VALID_MODE_GROUPS:
            # CW/RTTY/OTHER → wir lassen die Combo stehen, machen nichts.
            return
        if group == self._last_radio_mode_group:
            return
        # Wir setzen den Cache **vor** dem Combo-Wechsel: damit erkennt
        # ``_on_mode_changed`` die neue Gruppe als „radio-getrieben" und
        # macht nur Read (kein erneutes Mode-Set Richtung Radio).
        self._last_radio_mode_group = group
        idx = self.mode_combo.findText(group)
        if idx < 0:
            return
        if idx == self.mode_combo.currentIndex():
            return
        # currentIndexChanged löst _on_mode_changed aus, das (wenn
        # ``_suppress_dirty`` False ist) einen Auto-Read planen.
        self.mode_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # Worker-Lebenszyklus
    # ------------------------------------------------------------------

    def _start_worker(
        self,
        *,
        write: bool,
        profile: Optional[AudioProfile] = None,
        silent: bool = False,
        target_mode: Optional[RxMode] = None,
    ) -> None:
        if self._worker_thread is not None:
            return  # bereits beschäftigt

        ft = FT991CAT(self._cat)
        thread = QThread(self)
        # Beim Schreiben: Baseline (zuletzt synchronisiertes Profil)
        # mitgeben — der Worker schreibt dann nur die Diffs. Wenn noch
        # nie etwas synchronisiert wurde, bleibt baseline None → voller
        # Schreibvorgang.
        worker = _ProfileIoWorker(
            ft,
            write=write,
            profile=profile,
            live_name="Live-Werte aus Gerät",
            live_mode_group=self.mode_combo.currentText(),
            baseline=self._last_synced_profile if write else None,
            target_mode=target_mode if not write else None,
        )
        worker.moveToThread(thread)

        # Während der Worker läuft, merken wir uns das geschriebene Profil
        # für späteres Baseline-Update.
        self._writing_profile = deepcopy(profile) if (write and profile is not None) else None

        # Silent-Auto-Aktionen ohne modalen Fortschrittsdialog. Statt
        # dessen läuft eine kurze Info im Sync-Label rechts neben den
        # Buttons.
        dialog: Optional[QProgressDialog] = None
        title = "Profil ins Gerät schreiben…" if write else "Profil aus Gerät lesen…"
        if not silent:
            total_steps = _total_steps_for(
                profile.mode_group if profile is not None else self.mode_combo.currentText()
            )
            dialog = QProgressDialog(title, "Abbrechen", 0, total_steps, self)
            dialog.setWindowTitle(title)
            dialog.setWindowModality(Qt.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.setValue(0)
            dialog.setCancelButton(None)

        if silent:
            self._sync_label.setText(
                "Sync: schreibe…" if write else "Sync: lese…"
            )
            self._sync_label.setStyleSheet("color: #2ea043;")

        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_progressed)
        worker.read_done.connect(self._on_read_done)
        worker.write_done.connect(self._on_write_done)
        worker.failed.connect(self._on_worker_failed)
        worker.connection_lost.connect(self.connection_lost)

        for sig in (worker.read_done, worker.write_done, worker.failed,
                    worker.connection_lost):
            sig.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._worker_thread = thread
        self._worker = worker
        self._progress_dialog = dialog
        self._worker_silent = silent
        # Profil-Combo IMMER während eines Workers deaktivieren — auch im
        # Silent-Modus, damit der Anwender keinen Profil-Wechsel ins Leere
        # klickt, der dann erst nach Sekunden ausgeführt wird.
        self.profile_combo.setEnabled(False)
        if not silent:
            self._set_buttons_busy(True)
        thread.start()

    def _on_progressed(self, step: int, total: int, label: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setMaximum(total)
            self._progress_dialog.setValue(step)
            self._progress_dialog.setLabelText(label)

    def _on_read_done(self, profile: object, skipped: object) -> None:
        if not isinstance(profile, AudioProfile):
            return
        skipped_list = list(skipped) if isinstance(skipped, list) else []
        self._suppress_dirty = True
        try:
            self.basics.set_values(
                AudioBasicsValues(
                    mic_gain=profile.mic_gain,
                    mic_eq_enabled=profile.mic_eq_enabled,
                    speech_processor_enabled=profile.speech_processor_enabled,
                    speech_processor_level=profile.speech_processor_level,
                    ssb_tx_bpf=profile.ssb_tx_bpf,
                )
            )
            self.normal_eq_editor.set_settings(profile.normal_eq)
            self.processor_eq_editor.set_settings(profile.processor_eq)
            self.extended_editor.set_values(profile.extended)
            self._apply_mode_relevance(self.mode_combo.currentText())
        finally:
            self._suppress_dirty = False
        # Nach einem Read sind UI-Werte == Geräte-Werte → nichts mehr zu
        # schreiben. Außerdem stoppen wir den Debounce-Timer, falls noch
        # Reste laufen.
        self._dirty = False
        self._auto_write_timer.stop()
        # Baseline für künftige Diff-Writes setzen — der Read war gerade
        # die Ground Truth. ``profile.mode_group`` ist im Worker bereits auf
        # den aktuellen UI-Mode gesetzt worden.
        self._last_synced_profile = deepcopy(profile)
        # Mode-Gruppe vom Profil reflektiert das, was wir gerade gelesen
        # haben — egal, ob das vom MD0-Poll oder vom Auto-Read kam.
        self._last_radio_mode_group = profile.mode_group

        if skipped_list and not self._worker_silent:
            bullets = "\n".join(f"• {item}" for item in skipped_list)
            QMessageBox.information(
                self,
                "Teilweise gelesen",
                (
                    "Die folgenden Werte konnten nicht aus dem Gerät gelesen werden "
                    "und stehen jetzt auf Default. Sie werden nicht geschrieben, "
                    "solange du sie nicht änderst und das Profil speicherst:\n\n"
                    f"{bullets}"
                ),
            )
        if skipped_list:
            self.status_label.setText(
                f"Aktives Profil: {self._current_profile_name or '—'} "
                f"• Live-Werte (mit {len(skipped_list)} übersprungenen Feldern)"
            )
        else:
            self.status_label.setText(
                f"Aktives Profil: {self._current_profile_name or '—'} "
                "• Synchron mit Gerät"
            )

    def _on_write_done(self) -> None:
        # Baseline aktualisieren: das gerade geschriebene Profil ist nun
        # auch im Gerät. Damit wird der nächste Diff-Write minimal.
        if self._writing_profile is not None:
            self._last_synced_profile = self._writing_profile
            self._writing_profile = None
        if self._worker_silent:
            self._sync_label.setText("Sync: ✓")
            self._sync_label.setStyleSheet("color: #2ea043;")
        else:
            QMessageBox.information(
                self,
                "Geschrieben",
                "Das Profil wurde erfolgreich ins Gerät übertragen.",
            )

    def _on_worker_failed(self, title: str, message: str) -> None:
        if self._worker_silent:
            # Stille Auto-Aktion: kein Pop-up, nur ein Hinweis im Sync-Label.
            short = message.splitlines()[0] if message else title
            self._sync_label.setText(f"Sync: ⚠ {short}")
            self._sync_label.setStyleSheet("color: #ffae42;")
            # Bei TX-Lock markieren wir „warte auf RX" — die Edge-Erkennung
            # in notify_tx_state() triggert dann automatisch den nächsten
            # Versuch, sobald TX endet.
            if "TX" in title or "TX" in message:
                self._tx_block_pending = True
                self._sync_label.setText("Sync: ⏸ wartet auf RX")
        else:
            QMessageBox.warning(self, title, message)

    def _on_thread_finished(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._worker_thread is not None:
            self._worker_thread.deleteLater()
            self._worker_thread = None
        was_silent = self._worker_silent
        self._worker_silent = False
        self._writing_profile = None
        # Profil-Combo wieder freigeben (nur wenn weiter verbunden).
        self.profile_combo.setEnabled(self._cat.is_connected())
        if not was_silent:
            self._set_buttons_busy(False)
        else:
            # Sync-Label nach kurzem Erfolgsblitz auf "aktiv" zurücksetzen.
            QTimer.singleShot(800, self._reset_sync_label_if_idle)

        # Pending-Action ausführen, falls in der Zwischenzeit etwas neues
        # angefragt wurde. Mit kleinem Delay, damit Qt aufräumen kann.
        if self._pending_action is not None:
            kind, profile = self._pending_action
            self._pending_action = None
            QTimer.singleShot(
                30,
                lambda k=kind, p=profile: self._dispatch_action(k, p),
            )

    def _reset_sync_label_if_idle(self) -> None:
        if self._worker_thread is not None:
            return
        if self._cat.is_connected():
            self._sync_label.setText("Live-Sync: aktiv")
            self._sync_label.setStyleSheet("color: #2ea043;")
        else:
            self._sync_label.setText("Live-Sync: aus")
            self._sync_label.setStyleSheet("color: gray;")

    def _set_buttons_busy(self, busy: bool) -> None:
        for btn in (
            self.save_button,
            self.save_as_button,
            self.delete_button,
            self.profile_combo,
        ):
            btn.setEnabled(not busy)
