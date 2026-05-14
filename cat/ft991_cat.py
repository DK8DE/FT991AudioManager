"""FT-991/FT-991A-spezifische CAT-Kommando-Schicht.

Diese Klasse kapselt die Kommandos aus dem CAT-Manual. Implementiert sind:

* Identifikation (``ID;``)
* TX-Status (``TX;``) — für die TX-Sicherheit beim Schreiben
* Generisches Lesen/Schreiben von EX-Menüs
* Lesen/Schreiben eines kompletten Parametric-EQ-Sets
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from mapping.menu_mapping import format_ex_read, format_ex_write, parse_ex_response
from mapping.audio_mapping import (
    MIC_GAIN_MAX,
    MIC_GAIN_MIN,
    PROCESSOR_LEVEL_MAX,
    PROCESSOR_LEVEL_MIN,
    PR_FUNCTION_MIC_EQ,
    PR_FUNCTION_PROCESSOR,
    SSB_BPF_MENU,
    format_pr_query,
    format_pr_set,
    format_three_digit,
    parse_pr_response,
    parse_three_digit,
    ssb_bpf_decode_from_menu,
    ssb_bpf_encode_for_menu,
)
from mapping.meter_mapping import (
    MeterKind,
    format_rm_query,
    format_sm_query,
    parse_rm_response,
    parse_sm_response,
    parse_tx_response,
)
from mapping.rx_mapping import (
    AgcMode,
    RxMode,
    format_af_gain_query,
    format_agc_query,
    format_agc_set,
    format_auto_notch_query,
    format_auto_notch_set,
    format_frequency_b_query,
    format_frequency_query,
    format_mode_query,
    format_mode_set,
    format_nb_level_query,
    format_nb_level_set,
    format_nb_query,
    format_nb_set,
    format_nr_level_query,
    format_nr_level_set,
    format_nr_query,
    format_nr_set,
    format_rf_gain_query,
    format_squelch_query,
    parse_af_gain_response,
    parse_agc_response,
    parse_auto_notch_response,
    parse_frequency_b_response,
    parse_frequency_response,
    parse_mode_response,
    parse_nb_level_response,
    parse_nb_response,
    parse_nr_level_response,
    parse_nr_response,
    parse_rf_gain_response,
    parse_squelch_response,
)
from mapping.extended_mapping import (
    EXTENDED_DEFS,
    EXTENDED_DEFS_BY_KEY,
    ExtendedSettingDef,
    defs_for_mode,
)
from mapping.eq_mapping import (
    EqMenuSet,
    NORMAL_EQ_MENUS,
    PROCESSOR_EQ_MENUS,
    decode_bw,
    decode_freq,
    decode_level,
    encode_bw,
    encode_freq,
    encode_level,
)
from model.eq_band import EQBand, EQSettings

from .cat_errors import CatError, CatProtocolError, CatTimeoutError
from .cat_log import CatLog
from .serial_cat import SerialCAT


FT991A_RADIO_ID = "0570"
"""Die ``ID;``-Antwort des FT-991 und FT-991A ist ``ID0570;``."""

_ID_PATTERN = re.compile(r"^ID(\d{4});$")
_TX_PATTERN = re.compile(r"^TX(\d);$")


ProgressCallback = Callable[[int, int, str], None]
"""Signatur: ``(step, total, label)``. Wird vor jedem CAT-Schritt aufgerufen."""


@dataclass(frozen=True)
class RadioIdentity:
    raw: str
    radio_id: Optional[str]

    @property
    def is_ft991(self) -> bool:
        return self.radio_id == FT991A_RADIO_ID


class TxLockError(CatError):
    """Wird ausgelöst, wenn ein Schreibvorgang während TX verhindert wurde."""


class FT991CAT:
    """Hochsprachige CAT-API für den FT-991/FT-991A."""

    def __init__(self, serial_cat: SerialCAT) -> None:
        self._cat = serial_cat

    def get_log(self) -> Optional[CatLog]:
        """Bequemer Durchgriff aufs CAT-Log, falls gesetzt."""
        return self._cat.get_log()

    # ------------------------------------------------------------------
    # Identifikation
    # ------------------------------------------------------------------

    def get_radio_id(self) -> RadioIdentity:
        log = self.get_log()
        if log is not None:
            log.log_info("=== ID-Abfrage ===")
        response = self._cat.send_command("ID;")
        match = _ID_PATTERN.match(response)
        if not match:
            if log is not None:
                log.log_warn(
                    f"ID-Antwort nicht parsebar: {response!r} (erwartet ID####;)"
                )
            return RadioIdentity(raw=response, radio_id=None)
        return RadioIdentity(raw=response, radio_id=match.group(1))

    def test_connection(self) -> RadioIdentity:
        return self.get_radio_id()

    # ------------------------------------------------------------------
    # TX-Status
    # ------------------------------------------------------------------

    def is_transmitting(self) -> bool:
        """Liefert ``True``, wenn das Gerät gerade sendet (``TX;`` != 0).

        Falls die Antwort nicht parsebar ist, wird zur Sicherheit ``True``
        zurückgegeben, damit wir nicht versehentlich während TX schreiben.
        """
        try:
            response = self._cat.send_command("TX;")
        except CatTimeoutError:
            return True
        match = _TX_PATTERN.match(response)
        if not match:
            return True
        return match.group(1) != "0"

    def ensure_rx(self) -> None:
        """Wirft :class:`TxLockError`, wenn das Gerät gerade sendet."""
        if self.is_transmitting():
            raise TxLockError(
                "Das Funkgerät sendet gerade — Schreibvorgang wurde abgebrochen."
            )

    # ------------------------------------------------------------------
    # Generisches EX-Menü
    # ------------------------------------------------------------------

    def read_menu(self, menu_number: int) -> str:
        """Liest ein EX-Menü und liefert den Roh-Wert (ohne Präfix/``;``)."""
        cmd = format_ex_read(menu_number)
        response = self._cat.send_command(cmd)
        try:
            return parse_ex_response(response, menu_number)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def write_menu(self, menu_number: int, raw_value: str, *, tx_lock: bool = True) -> None:
        """Schreibt ein EX-Menü.

        ``raw_value`` ist die ASCII-Repräsentation des Werts (z. B. ``"03"``).
        Wenn ``tx_lock`` aktiv ist, wird vor dem Schreiben geprüft, ob das
        Gerät gerade sendet — falls ja, wird :class:`TxLockError` geworfen.
        """
        if tx_lock:
            self.ensure_rx()
        cmd = format_ex_write(menu_number, raw_value)
        # Schreiben liefert beim FT-991/A keine Antwort — wir lesen also nichts.
        self._cat.send_command(cmd, read_response=False)

    # ------------------------------------------------------------------
    # Komplettes EQ-Set
    # ------------------------------------------------------------------

    def read_eq(
        self,
        menus: EqMenuSet = NORMAL_EQ_MENUS,
        *,
        progress: Optional[ProgressCallback] = None,
        tolerate_bands: bool = False,
        skipped: Optional[list] = None,
    ) -> EQSettings:
        """Liest alle 9 Menüs eines EQ-Sets und liefert :class:`EQSettings`.

        Liest **erst** alle Rohwerte vom Gerät und versucht **danach** zu
        dekodieren. So landen bei einem Decode-Fehler trotzdem alle 9
        Rohwerte im Log und in der Fehlermeldung — wichtig fürs Diagnostizieren
        unbekannter Wert-Kodierungen.

        Wenn ``tolerate_bands=True``, wird ein nicht-dekodierbares Band auf
        Default zurückgesetzt, statt die gesamte Operation abzubrechen — die
        anderen beiden Bänder werden korrekt zurückgegeben. Wird ``skipped``
        übergeben, hängt die Methode dort kurze Labels der defekten Bänder
        an (analog zu ``read_extended_for_mode``).
        """
        log = self.get_log()
        if log is not None:
            menu_kind = "Normal-EQ" if menus is NORMAL_EQ_MENUS else "Processor-EQ"
            log.log_info(
                f"=== {menu_kind} lesen (EX{menus.band1_freq}..EX{menus.band3_bw}) ==="
            )

        # Slot-Reihenfolge laut Manual: Freq, Level, BW.
        band_layout = [
            (menus.band1_freq, menus.band1_level, menus.band1_bw),
            (menus.band2_freq, menus.band2_level, menus.band2_bw),
            (menus.band3_freq, menus.band3_level, menus.band3_bw),
        ]
        total = 9
        step = 0

        # Phase 1: Alle Rohwerte einsammeln
        raw_values: list[tuple[int, str]] = []  # (menu_number, raw_value)
        labels = ["Freq", "Level", "BW"]
        for band_index, band_menus in enumerate(band_layout):
            for slot_index, menu in enumerate(band_menus):
                step += 1
                self._notify(
                    progress,
                    step,
                    total,
                    f"EQ{band_index + 1} {labels[slot_index]} (EX{menu})",
                )
                raw = self.read_menu(menu)
                raw_values.append((menu, raw))

        # Phase 2: Dekodieren — band-für-band, damit ein defektes Band die
        # anderen zwei nicht reisst (wenn ``tolerate_bands`` aktiv ist).
        bands: list[EQBand] = []
        band_failures: list[tuple[int, ValueError]] = []
        fatal_exc: Optional[ValueError] = None
        for band_index in range(3):
            freq_raw = raw_values[band_index * 3][1]
            level_raw = raw_values[band_index * 3 + 1][1]
            bw_raw = raw_values[band_index * 3 + 2][1]
            try:
                freq = decode_freq(freq_raw, band_index)
                level = decode_level(level_raw)
                bw = decode_bw(bw_raw)
                bands.append(EQBand(freq=freq, level=level, bw=bw))
            except ValueError as exc:
                if not tolerate_bands:
                    fatal_exc = exc
                    break
                band_failures.append((band_index, exc))
                bands.append(EQBand())  # Default-Werte (Freq=OFF, Level=0, BW=5)

        summary = ", ".join(f"EX{m}={v!r}" for m, v in raw_values)

        if fatal_exc is not None:
            message = (
                f"Unerwartete Rohwerte beim EQ-Lesen: {fatal_exc}\n"
                f"Alle 9 Rohwerte: {summary}"
            )
            if log is not None:
                log.log_error(message)
            raise CatProtocolError(message) from fatal_exc

        if band_failures:
            if log is not None:
                log.log_warn(
                    f"EQ-Lesen: {len(band_failures)} Band(s) mit unerwarteten "
                    f"Rohwerten — Default-Werte verwendet. Alle 9 Rohwerte: {summary}"
                )
            for band_index, exc in band_failures:
                b_freq, b_level, b_bw = band_layout[band_index]
                band_label = (
                    f"EQ{band_index + 1} "
                    f"(EX{b_freq}/EX{b_level}/EX{b_bw}): "
                    f"Freq='{raw_values[band_index * 3][1]}', "
                    f"Level='{raw_values[band_index * 3 + 1][1]}', "
                    f"BW='{raw_values[band_index * 3 + 2][1]}'"
                )
                if log is not None:
                    log.log_warn(f"  • {band_label} — {exc}")
                if skipped is not None:
                    skipped.append(band_label)

        if log is not None and not band_failures:
            decoded_summary = ", ".join(
                f"EQ{i + 1}={b.freq}/{b.level:+d}dB/Q{b.bw}"
                for i, b in enumerate(bands)
            )
            log.log_info(f"EQ gelesen — {decoded_summary}")

        return EQSettings(eq1=bands[0], eq2=bands[1], eq3=bands[2])

    def write_eq(
        self,
        eq: EQSettings,
        menus: EqMenuSet = NORMAL_EQ_MENUS,
        *,
        progress: Optional[ProgressCallback] = None,
        tx_lock: bool = True,
        baseline: Optional[EQSettings] = None,
    ) -> int:
        """Schreibt ein EQ-Set ins Gerät.

        Wenn ``baseline`` gesetzt ist, werden nur die EQ-Slots geschrieben,
        deren codierter Rohwert sich gegenüber der Baseline verändert hat.
        Damit lässt sich beim Live-Sync der CAT-Traffic um Größenordnungen
        reduzieren.

        Returns:
            Anzahl der tatsächlich geschriebenen Slots (0..9).
        """
        log = self.get_log()
        if log is not None:
            menu_kind = "Normal-EQ" if menus is NORMAL_EQ_MENUS else "Processor-EQ"
            mode = "diff" if baseline is not None else "voll"
            log.log_info(
                f"=== {menu_kind} schreiben ({mode}, EX{menus.band1_freq}..EX{menus.band3_bw}) ==="
            )

        # Slot-Reihenfolge laut Manual: Freq, Level, BW.
        plan = [
            (menus.band1_freq, encode_freq(eq.eq1.freq, 0), "EQ1 Freq"),
            (menus.band1_level, encode_level(eq.eq1.level), "EQ1 Level"),
            (menus.band1_bw, encode_bw(eq.eq1.bw), "EQ1 BW"),
            (menus.band2_freq, encode_freq(eq.eq2.freq, 1), "EQ2 Freq"),
            (menus.band2_level, encode_level(eq.eq2.level), "EQ2 Level"),
            (menus.band2_bw, encode_bw(eq.eq2.bw), "EQ2 BW"),
            (menus.band3_freq, encode_freq(eq.eq3.freq, 2), "EQ3 Freq"),
            (menus.band3_level, encode_level(eq.eq3.level), "EQ3 Level"),
            (menus.band3_bw, encode_bw(eq.eq3.bw), "EQ3 BW"),
        ]

        if baseline is not None:
            baseline_plan = [
                (menus.band1_freq, encode_freq(baseline.eq1.freq, 0), "EQ1 Freq"),
                (menus.band1_level, encode_level(baseline.eq1.level), "EQ1 Level"),
                (menus.band1_bw, encode_bw(baseline.eq1.bw), "EQ1 BW"),
                (menus.band2_freq, encode_freq(baseline.eq2.freq, 1), "EQ2 Freq"),
                (menus.band2_level, encode_level(baseline.eq2.level), "EQ2 Level"),
                (menus.band2_bw, encode_bw(baseline.eq2.bw), "EQ2 BW"),
                (menus.band3_freq, encode_freq(baseline.eq3.freq, 2), "EQ3 Freq"),
                (menus.band3_level, encode_level(baseline.eq3.level), "EQ3 Level"),
                (menus.band3_bw, encode_bw(baseline.eq3.bw), "EQ3 BW"),
            ]
            # Nur tatsächlich geänderte Slots übernehmen.
            plan = [
                cur for cur, base in zip(plan, baseline_plan)
                if cur[1] != base[1]
            ]

        if not plan:
            self._notify(progress, 0, 0, "EQ: keine Änderungen")
            return 0

        if tx_lock:
            self.ensure_rx()

        total = len(plan)
        for step, (menu, raw, label) in enumerate(plan, start=1):
            self._notify(progress, step, total, f"{label} (EX{menu}) = {raw}")
            # tx_lock wurde oben einmal geprüft — keine zusätzliche Prüfung pro Menü,
            # sonst wären es 9 zusätzliche Roundtrips.
            self.write_menu(menu, raw, tx_lock=False)
        return total

    # ------------------------------------------------------------------
    # Convenience-Wrapper für Processor-EQ (Version 0.3 nutzt das gleiche)
    # ------------------------------------------------------------------

    def read_processor_eq(self, *, progress: Optional[ProgressCallback] = None) -> EQSettings:
        return self.read_eq(PROCESSOR_EQ_MENUS, progress=progress)

    def write_processor_eq(
        self,
        eq: EQSettings,
        *,
        progress: Optional[ProgressCallback] = None,
        tx_lock: bool = True,
        baseline: Optional[EQSettings] = None,
    ) -> int:
        return self.write_eq(
            eq,
            PROCESSOR_EQ_MENUS,
            progress=progress,
            tx_lock=tx_lock,
            baseline=baseline,
        )

    # ------------------------------------------------------------------
    # TX-Audio-Grundwerte (Version 0.3)
    # ------------------------------------------------------------------

    def get_mic_gain(self) -> int:
        """Liest den MIC-Gain (0..100)."""
        response = self._cat.send_command("MG;")
        try:
            return parse_three_digit("MG", response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def set_mic_gain(self, value: int) -> None:
        """Setzt den MIC-Gain (0..100). Geklemmt automatisch."""
        self.ensure_rx()
        if value < MIC_GAIN_MIN or value > MIC_GAIN_MAX:
            # Klemmt format_three_digit ohnehin — wir loggen aber einmal.
            log = self.get_log()
            if log is not None:
                log.log_warn(
                    f"MIC-Gain {value} ausserhalb {MIC_GAIN_MIN}..{MIC_GAIN_MAX}, wird geklemmt"
                )
        self._cat.send_command(format_three_digit("MG", value), read_response=False)

    def get_processor_enabled(self) -> bool:
        """Liest den Zustand des Speech Processors (``PR0;``)."""
        response = self._cat.send_command(format_pr_query(PR_FUNCTION_PROCESSOR))
        try:
            return parse_pr_response(response, PR_FUNCTION_PROCESSOR)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def set_processor_enabled(self, enabled: bool) -> None:
        """Schaltet den Speech Processor an/aus (``PR0n;``).

        Schreibt das Kommando und liest direkt danach den Zustand zurück.
        Stimmt das Echo nicht mit dem Wunsch überein, wird das im Log
        als Warning sichtbar — typisches Anzeichen, dass das Radio die
        verwendete P2-Codierung nicht versteht.
        """
        self.ensure_rx()
        self._cat.send_command(
            format_pr_set(PR_FUNCTION_PROCESSOR, enabled), read_response=False
        )
        self._verify_pr_echo("Speech Processor", PR_FUNCTION_PROCESSOR, enabled)

    def get_processor_level(self) -> int:
        """Liest den Speech-Processor-Level (``PL;``, 0..100)."""
        response = self._cat.send_command("PL;")
        try:
            # Manche Yaesu-Modelle geben PL0nnn; mit 4 Stellen aus. Wir nehmen
            # das, was nach dem Präfix kommt, und parsen tolerant.
            return parse_three_digit("PL", response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def set_processor_level(self, value: int) -> None:
        """Setzt den Speech-Processor-Level (0..100)."""
        self.ensure_rx()
        if value < PROCESSOR_LEVEL_MIN or value > PROCESSOR_LEVEL_MAX:
            log = self.get_log()
            if log is not None:
                log.log_warn(
                    f"Processor-Level {value} ausserhalb "
                    f"{PROCESSOR_LEVEL_MIN}..{PROCESSOR_LEVEL_MAX}, wird geklemmt"
                )
        self._cat.send_command(format_three_digit("PL", value), read_response=False)

    def get_mic_eq_enabled(self) -> bool:
        """Liest den Zustand des Parametric MIC EQ (``PR1;``)."""
        response = self._cat.send_command(format_pr_query(PR_FUNCTION_MIC_EQ))
        try:
            return parse_pr_response(response, PR_FUNCTION_MIC_EQ)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def set_mic_eq_enabled(self, enabled: bool) -> None:
        """Schaltet den Parametric MIC EQ an/aus (``PR1n;``).

        Mit Echo-Verify analog :meth:`set_processor_enabled`.
        """
        self.ensure_rx()
        self._cat.send_command(
            format_pr_set(PR_FUNCTION_MIC_EQ, enabled), read_response=False
        )
        self._verify_pr_echo("Parametric MIC EQ", PR_FUNCTION_MIC_EQ, enabled)

    def _verify_pr_echo(self, label: str, function: int, expected: bool) -> None:
        """Liest den PR-Status nach einem Set zurück und loggt Abweichungen.

        Wirft selbst keine Exception — die Anforderung an die GUI ist,
        weiterzulaufen und dem User über das Log eine Diagnose zu
        ermöglichen. Bei einem Timeout protokollieren wir das eine Mal
        und sind fertig (das Radio kann beim nächsten Read-Tick erneut
        gepollt werden).
        """
        log = self.get_log()
        try:
            response = self._cat.send_command(format_pr_query(function))
            actual = parse_pr_response(response, function)
        except (CatProtocolError, CatTimeoutError, ValueError) as exc:
            if log is not None:
                log.log_warn(f"{label}: Echo-Verify nicht möglich ({exc})")
            return
        if actual != expected:
            if log is not None:
                log.log_warn(
                    f"{label}: Schreib-Echo unerwartet — gewünscht "
                    f"{'ON' if expected else 'OFF'}, Radio meldet "
                    f"{'ON' if actual else 'OFF'} ({response.strip()}). "
                    "Das Radio versteht das Set-Kommando vermutlich nicht."
                )
        elif log is not None:
            log.log_info(
                f"{label}: Echo bestätigt {'ON' if actual else 'OFF'}"
            )

    def get_ssb_bpf(self) -> str:
        """Liest die SSB-TX-Bandbreite (EX112) als Profil-Key (z. B. ``100-2900``)."""
        raw = self.read_menu(SSB_BPF_MENU)
        try:
            return ssb_bpf_decode_from_menu(raw)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def set_ssb_bpf(self, key: str) -> None:
        """Setzt die SSB-TX-Bandbreite per Profil-Key (``100-2900`` etc.)."""
        try:
            raw = ssb_bpf_encode_for_menu(key)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        # write_menu prüft selbst tx_lock
        self.write_menu(SSB_BPF_MENU, raw)

    # ------------------------------------------------------------------
    # Mode-Kurzform (Bequemlichkeits-Wrapper, in 0.6 implementiert)
    # ------------------------------------------------------------------

    def get_mode(self) -> str:
        """Liest die aktuelle Betriebsart als String (z. B. ``"USB"``)."""
        return self.read_rx_mode().value

    # ------------------------------------------------------------------
    # Live-Meter (Version 0.4)
    # ------------------------------------------------------------------

    def get_tx_status(self) -> bool:
        """Liest den TX-Status. ``True`` wenn das Gerät sendet."""
        response = self._cat.send_command("TX;")
        try:
            return parse_tx_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_meter(self, kind: MeterKind) -> int:
        """Liest ein TX-Meter (``RMn;``) als Rohwert (typisch 0..255).

        ``kind`` ist eine :class:`MeterKind` (``MeterKind.ALC``, ``COMP``,
        ``PO``, ``SWR``). Strings werden ebenfalls akzeptiert.
        """
        if isinstance(kind, str):
            try:
                kind = MeterKind(kind.lower())
            except ValueError as exc:
                raise ValueError(f"Unbekannter Meter-Typ: {kind!r}") from exc
        response = self._cat.send_command(format_rm_query(kind))
        try:
            return parse_rm_response(response, kind)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_all_meters(self) -> Dict[MeterKind, int]:
        """Liest COMP / ALC / PO / SWR auf einen Schwung."""
        return {kind: self.read_meter(kind) for kind in (
            MeterKind.COMP, MeterKind.ALC, MeterKind.PO, MeterKind.SWR,
        )}

    # ------------------------------------------------------------------
    # RX-Anzeigen (Version 0.6): S-Meter + DSP-Status + Pegel + Mode + Freq
    # ------------------------------------------------------------------

    def read_smeter(self) -> int:
        """Liest den S-Meter-Rohwert (``SM0nnn;``, 0..255)."""
        response = self._cat.send_command(format_sm_query())
        try:
            return parse_sm_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_squelch(self) -> int:
        """Liest den Squelch-Pegel (``SQ0nnn;``, 0..100)."""
        response = self._cat.send_command(format_squelch_query())
        try:
            return parse_squelch_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_af_gain(self) -> int:
        """Liest den AF-Gain / Lautstärke (``AG0nnn;``, 0..255)."""
        response = self._cat.send_command(format_af_gain_query())
        try:
            return parse_af_gain_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_rf_gain(self) -> int:
        """Liest den RF-Gain (``RG0nnn;``, 0..255)."""
        response = self._cat.send_command(format_rf_gain_query())
        try:
            return parse_rf_gain_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_agc(self) -> AgcMode:
        """Liest den AGC-Modus (``GT0n;``)."""
        response = self._cat.send_command(format_agc_query())
        try:
            return parse_agc_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_noise_blanker(self) -> bool:
        """Liest den NB-Status (``NB0n;``)."""
        response = self._cat.send_command(format_nb_query())
        try:
            return parse_nb_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_noise_blanker_level(self) -> int:
        """Liest den NB-Level (``NL0nnn;``, 0..10)."""
        response = self._cat.send_command(format_nb_level_query())
        try:
            return parse_nb_level_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_noise_reduction(self) -> bool:
        """Liest den NR-Status (``NR0n;``)."""
        response = self._cat.send_command(format_nr_query())
        try:
            return parse_nr_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_noise_reduction_level(self) -> int:
        """Liest den NR-Level (``RL0nn;``, 1..15)."""
        response = self._cat.send_command(format_nr_level_query())
        try:
            return parse_nr_level_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_auto_notch(self) -> bool:
        """Liest den Auto-Notch-Status (``BC0n;``)."""
        response = self._cat.send_command(format_auto_notch_query())
        try:
            return parse_auto_notch_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Setter für DSP-Schalter & -Pegel
    #
    # Diese werden vom MeterWidget angesprochen, wenn der User an den
    # vertikalen Slidern neben dem S-Meter zieht. Kein TX-Lock — NB/DNR/
    # DNF sind RX-DSPs und dürfen auch während TX umgestellt werden
    # (wirken eben erst beim nächsten Empfangsdurchgang).
    # ------------------------------------------------------------------

    def write_noise_blanker(self, on: bool) -> None:
        """Schaltet den Noise Blanker (``NB0n;``)."""
        self._cat.send_command(format_nb_set(on), read_response=False)

    def write_noise_blanker_level(self, level: int) -> None:
        """Setzt den Noise-Blanker-Pegel (``NL0nnn;``, 0..10)."""
        self._cat.send_command(format_nb_level_set(level), read_response=False)

    def write_noise_reduction(self, on: bool) -> None:
        """Schaltet die Digital Noise Reduction (``NR0n;``)."""
        self._cat.send_command(format_nr_set(on), read_response=False)

    def write_noise_reduction_level(self, level: int) -> None:
        """Setzt den DNR-Pegel (``RL0nn;``, 1..15)."""
        self._cat.send_command(format_nr_level_set(level), read_response=False)

    def write_auto_notch(self, on: bool) -> None:
        """Schaltet den Digital Notch Filter (``BC0n;``)."""
        self._cat.send_command(format_auto_notch_set(on), read_response=False)

    def write_agc(self, mode: AgcMode) -> None:
        """Setzt den AGC-Modus (``GT0n;``).

        Wird vom AGC-Slider aufgerufen — auch hier kein TX-Lock, der
        AGC-Wechsel ist unkritisch für die Sendung.
        """
        self._cat.send_command(format_agc_set(mode), read_response=False)

    def read_rx_mode(self) -> RxMode:
        """Liest die aktuelle Betriebsart (``MD0n;``)."""
        response = self._cat.send_command(format_mode_query())
        try:
            return parse_mode_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def set_rx_mode(self, mode: RxMode, *, tx_lock: bool = True) -> None:
        """Setzt die Betriebsart des Radios (``MD0X;``).

        Bei aktivem TX wird ein :class:`TxLockError` ausgelöst — Mode-
        Wechsel während Sendebetrieb sind unsicher.
        """
        if tx_lock:
            self.ensure_rx()
        command = format_mode_set(mode)
        log = self.get_log()
        if log is not None:
            log.log_info(f"Set RX-Mode: {mode.value} → {command}")
        # MD-Schreibvorgang erwartet keine Antwort.
        self._cat.send_command(command, read_response=False)

    def read_frequency(self) -> int:
        """Liest die VFO-A-Frequenz in Hz (``FAnnnnnnnnn;``)."""
        response = self._cat.send_command(format_frequency_query())
        try:
            return parse_frequency_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    def read_frequency_b(self) -> int:
        """Liest die VFO-B-Frequenz in Hz (``FBnnnnnnnnn;``)."""
        response = self._cat.send_command(format_frequency_b_query())
        try:
            return parse_frequency_b_response(response)
        except ValueError as exc:
            raise CatProtocolError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Erweiterte Einstellungen (Version 0.5)
    # ------------------------------------------------------------------

    def read_extended(self, key_or_def) -> object:  # type: ignore[no-untyped-def]
        """Liest einen erweiterten Wert anhand ``key`` (str) oder
        :class:`ExtendedSettingDef`. Wirft :class:`CatProtocolError`,
        wenn der Wert nicht decodiert werden kann.
        """
        definition = self._resolve_ext(key_or_def)
        raw = self.read_menu(definition.menu)
        try:
            return definition.decoder(raw)
        except ValueError as exc:
            log = self.get_log()
            if log is not None:
                log.log_error(
                    f"Erweitert: EX{definition.menu:03d} ({definition.key}) "
                    f"liefert unerwarteten Rohwert {raw!r}: {exc}"
                )
            raise CatProtocolError(
                f"EX{definition.menu:03d} ({definition.key}): {exc}"
            ) from exc

    def write_extended(self, key_or_def, value, *, tx_lock: bool = True) -> None:  # type: ignore[no-untyped-def]
        """Schreibt einen erweiterten Wert."""
        definition = self._resolve_ext(key_or_def)
        try:
            raw = definition.encoder(value)
        except ValueError as exc:
            raise ValueError(
                f"Wert {value!r} für {definition.key} ungültig: {exc}"
            ) from exc
        self.write_menu(definition.menu, raw, tx_lock=tx_lock)

    def read_extended_for_mode(
        self,
        mode_group: str,
        *,
        progress: Optional[ProgressCallback] = None,
        tolerate_errors: bool = False,
        skipped: Optional[list] = None,
    ) -> Dict[str, object]:
        """Liest alle für ``mode_group`` relevanten Extended-Werte.

        Mit ``tolerate_errors=True`` werden einzelne Lesefehler protokolliert
        und das betroffene Feld wird übersprungen (kein Eintrag im Ergebnis),
        statt die gesamte Operation abzubrechen. Wenn ``skipped`` übergeben
        wird, hängt die Methode dort die Labels der übersprungenen Felder an.
        """
        relevant = defs_for_mode(mode_group)
        log = self.get_log()
        if log is not None:
            log.log_info(
                f"=== Erweitert lesen für {mode_group} "
                f"({len(relevant)} Werte) ==="
            )
        result: Dict[str, object] = {}
        for step, definition in enumerate(relevant, start=1):
            self._notify(progress, step, len(relevant), f"EX{definition.menu:03d} {definition.label}")
            try:
                result[definition.key] = self.read_extended(definition)
            except CatProtocolError as exc:
                if not tolerate_errors:
                    raise
                if log is not None:
                    log.log_warn(
                        f"Erweitert übersprungen: {definition.label} "
                        f"(EX{definition.menu:03d}) — {exc}"
                    )
                if skipped is not None:
                    skipped.append(
                        f"{definition.label} (EX{definition.menu:03d})"
                    )
        return result

    def write_extended_for_mode(
        self,
        mode_group: str,
        values: Dict[str, object],
        *,
        progress: Optional[ProgressCallback] = None,
        tx_lock: bool = True,
        baseline: Optional[Dict[str, object]] = None,
    ) -> int:
        """Schreibt alle für ``mode_group`` relevanten Extended-Werte aus ``values``.

        Wenn ``baseline`` gesetzt ist, werden nur Felder geschrieben, deren
        Wert sich gegenüber der Baseline geändert hat. Werte, die in
        ``values`` fehlen, werden grundsätzlich übersprungen.

        Returns:
            Anzahl der tatsächlich geschriebenen Felder.
        """
        relevant = defs_for_mode(mode_group)
        log = self.get_log()

        plan = []
        for d in relevant:
            if d.key not in values:
                continue
            new_value = values[d.key]
            if baseline is not None:
                base_value = baseline.get(d.key)
                if base_value == new_value:
                    continue
            plan.append((d, new_value))

        if log is not None:
            mode = "diff" if baseline is not None else "voll"
            log.log_info(
                f"=== Erweitert schreiben für {mode_group} "
                f"({mode}, {len(plan)} Werte) ==="
            )

        if not plan:
            self._notify(progress, 0, 0, "Erweitert: keine Änderungen")
            return 0

        if tx_lock:
            self.ensure_rx()

        total = len(plan)
        for step, (definition, value) in enumerate(plan, start=1):
            self._notify(
                progress, step, total,
                f"EX{definition.menu:03d} {definition.label} = {value!r}",
            )
            self.write_extended(definition, value, tx_lock=False)
        return total

    def _resolve_ext(self, key_or_def) -> ExtendedSettingDef:  # type: ignore[no-untyped-def]
        if isinstance(key_or_def, ExtendedSettingDef):
            return key_or_def
        try:
            return EXTENDED_DEFS_BY_KEY[str(key_or_def)]
        except KeyError as exc:
            raise ValueError(f"Unbekannter Extended-Key: {key_or_def!r}") from exc

    # ------------------------------------------------------------------
    # Helfer
    # ------------------------------------------------------------------

    @staticmethod
    def _notify(
        cb: Optional[ProgressCallback],
        step: int,
        total: int,
        label: str,
    ) -> None:
        if cb is not None:
            try:
                cb(step, total, label)
            except Exception:
                pass
