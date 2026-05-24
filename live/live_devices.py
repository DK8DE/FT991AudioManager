"""PortAudio-/sounddevice-Geräteliste (getrennt von Qt Multimedia).

Anzeigenamen wie im **Audio-Player** / **Recorder** (Qt ``QMediaDevices`` =
Windows-Geräteliste). Intern bleibt die Auswahl der **PortAudio-Index** für
``sounddevice``.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

try:
    import sounddevice as _sd

    _HAVE_SD = True
except ImportError:
    _sd = None  # type: ignore[assignment]
    _HAVE_SD = False

# Mindest-Token-Übereinstimmung PA ↔ Qt (0…1), sonst kein Mapping
_QT_PA_MATCH_MIN_SCORE = 0.45


# Niedriger = bevorzugt (typisch Windows; unbekannte APIs = 9)
def _hostapi_rank(api_name: str) -> int:
    a = api_name.lower().replace(" ", "")
    if "wasapi" in a:
        return 0
    if "wdm-ks" in a or "wdmks" in a:
        return 1
    if "directsound" in a:
        return 2
    if "mme" in a:
        return 3
    return 9


def sounddevice_available() -> bool:
    return bool(_HAVE_SD)


def _hostapi_name_by_idx(sd: object, hostapi_idx: int) -> str:
    """Name der Host-API aus deren Index (nicht Geräte-Index)."""
    if hostapi_idx < 0:
        return ""
    try:
        ha = sd.query_hostapis(hostapi_idx)  # type: ignore[union-attr]
        if isinstance(ha, dict):
            return str(ha.get("name", "")).strip()
    except Exception:
        pass
    try:
        apis = sd.query_hostapis()  # type: ignore[union-attr]
        if isinstance(apis, list) and 0 <= hostapi_idx < len(apis):
            ent = apis[hostapi_idx]
            if isinstance(ent, dict):
                return str(ent.get("name", "")).strip()
    except Exception:
        pass
    return ""


def _hostapi_name_for_device(sd: object, device_dict: dict) -> str:
    """Name der Host-API (z. B. WASAPI, MME)."""
    try:
        hidx = int(device_dict.get("hostapi", -1))
        if hidx < 0:
            return ""
        ha = None
        try:
            ha = sd.query_hostapis(hidx)  # type: ignore[union-attr]
        except TypeError:
            ha = None
        if isinstance(ha, dict):
            return str(ha.get("name", "")).strip()
        apis = sd.query_hostapis()  # type: ignore[union-attr]
        if isinstance(apis, list) and 0 <= hidx < len(apis):
            ent = apis[hidx]
            if isinstance(ent, dict):
                return str(ent.get("name", "")).strip()
    except Exception:
        pass
    return ""


def _norm_group_key(base_name: str) -> str:
    """Einheitlicher Gruppierungsschlüssel für dasselbe logische Gerät.

    Unter Windows haben identische Endpunkte oft leicht andere Zeichenketten
    zwischen Host-APIs **und** Duplikate wie ``(2- USB…)`` vs. ``(USB…)`` sowie
    abgeschnittene Namen („… Audio CODE“ ohne ``)``).
    """
    s = str(base_name or "").strip().lower()
    s = " ".join(s.split())
    # Karten-Präfix in Klammern: "(2- Foo)" → "(foo)"
    s = re.sub(r"\(\s*\d+\s*-\s*", "(", s)
    # Ungeschlossene Klammer am Ende (abgeschnittener Gerätename)
    if s.count("(") > s.count(")"):
        idx = s.rfind("(")
        before = s[:idx].strip()
        inner = s[idx + 1 :].strip()
        inner = re.sub(r"^\d+\s*-\s*", "", inner)
        # Häufig: "usb audio code" ohne abschließendes "c" / ")
        inner = re.sub(r"audio\s+code$", "audio codec", inner)
        s = f"{before} ({inner})" if before else f"({inner})"
    s = " ".join(s.split())
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    # "… audio code)" oder "… audio code" → codec (nicht „codec“ zerstören)
    s = re.sub(r"audio\s+code(?=[\s\)]|$)(?!c)", "audio codec", s)
    return " ".join(s.split())


def _norm_match_key(base_name: str) -> str:
    """Weicher Match-Schlüssel PA ↔ Qt (Windows-Anzeigename)."""
    s = _norm_group_key(base_name)
    s = re.sub(r"\(r\)", "", s)
    s = s.replace("®", "").replace("™", "")
    s = re.sub(r"\s*-\s*", " ", s)
    s = re.sub(r"[^\w\s()]", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


def _token_set(key: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _norm_match_key(key)))


def _match_score(a: str, b: str) -> float:
    ta = _token_set(a)
    tb = _token_set(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / float(max(len(ta), len(tb)))


def _qt_multimedia_device_labels(*, want_input: bool) -> List[str]:
    """Geordnete Windows-/Qt-Anzeigenamen."""
    try:
        from audio.qt_multimedia_lazy import qt_multimedia_types
    except ImportError:
        return []

    mm = qt_multimedia_types()
    if mm is None:
        return []
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
    if want_input:
        devices = QMediaDevices.audioInputs()
    else:
        devices = QMediaDevices.audioOutputs()
    out: List[str] = []
    for dev in devices:
        desc = str(dev.description()).strip()
        if desc:
            out.append(desc)
    return out


def _pa_row_tip(sd: object, idx: int, api: str) -> str:
    tip = f"PortAudio #{idx}"
    if api:
        tip += f" — {api}"
    return tip


def _best_pa_row_for_qt_name(
    sd: object,
    qt_desc: str,
    *,
    want_input: bool,
    min_score: float = _QT_PA_MATCH_MIN_SCORE,
) -> Optional[Tuple[int, str, str]]:
    """Beste PortAudio-Zeile (index, pa_name, tooltip) für einen Qt-Anzeigenamen."""
    qkey = _norm_match_key(qt_desc)
    if not qkey:
        return None

    best: Optional[Tuple[int, int, float, str, str]] = None
    # (hostapi_rank, index, match_score, pa_name, api)

    for i, d in enumerate(sd.query_devices()):  # type: ignore[union-attr]
        if want_input:
            if int(d.get("max_input_channels") or 0) <= 0:
                continue
        else:
            if int(d.get("max_output_channels") or 0) <= 0:
                continue

        pa_name = str(d.get("name", f"Gerät {i}")).strip() or f"Gerät {i}"
        score = _match_score(qkey, pa_name)
        if score < min_score:
            continue

        api = _hostapi_name_for_device(sd, d)
        rank = _hostapi_rank(api)
        cand = (rank, i, score, pa_name, api)
        if best is None:
            best = cand
            continue
        if cand[0] < best[0] or (cand[0] == best[0] and cand[2] > best[2]) or (
            cand[0] == best[0] and cand[2] == best[2] and cand[1] < best[1]
        ):
            best = cand

    if best is None:
        return None
    _rank, idx, match_score, pa_name, api = best
    tip = _pa_row_tip(sd, idx, api)
    if match_score < _QT_PA_MATCH_MIN_SCORE:
        tip = (
            f"{tip}\nHinweis: Gerätezuordnung unsicher "
            f"(Score {match_score:.2f}) — ggf. „Geräte neu laden“."
        )
    return idx, pa_name, tip


def _raw_pa_device_rows(
    sd: object,
    *,
    want_input: bool,
) -> List[Tuple[int, str, str]]:
    """Alle PortAudio-Geräte ohne Zusammenlegung."""
    out: List[Tuple[int, str, str]] = []
    for i, d in enumerate(sd.query_devices()):  # type: ignore[union-attr]
        if want_input:
            if int(d.get("max_input_channels") or 0) <= 0:
                continue
        else:
            if int(d.get("max_output_channels") or 0) <= 0:
                continue
        display = str(d.get("name", f"Gerät {i}")).strip() or f"Gerät {i}"
        api = _hostapi_name_for_device(sd, d)
        out.append((i, display, _pa_row_tip(sd, i, api)))
    out.sort(key=lambda row: row[1].lower())
    return out


def _device_rows(
    sd: object,
    *,
    want_input: bool,
) -> List[Tuple[int, str, str]]:
    """Geräteliste: Qt-Anzeigenamen mit PortAudio-Index, sonst rohe PA-Liste."""
    qt_labels = _qt_multimedia_device_labels(want_input=want_input)
    if qt_labels:
        out: List[Tuple[int, str, str]] = []
        for qdesc in qt_labels:
            row = _best_pa_row_for_qt_name(sd, qdesc, want_input=want_input)
            if row is None:
                row = _best_pa_row_for_qt_name(
                    sd,
                    qdesc,
                    want_input=want_input,
                    min_score=0.30,
                )
            if row is None:
                continue
            idx, pa_label, tip = row
            tip_full = tip
            if pa_label.lower() != qdesc.lower():
                tip_full = f"{tip}\nPortAudio: {pa_label}"
            out.append((idx, qdesc, tip_full))
        if out:
            return out
    return _raw_pa_device_rows(sd, want_input=want_input)


def list_input_devices() -> List[Tuple[str, str, str]]:
    """[(id, Kurzlabel, Tooltip), …]; erstes Tuple System-Standard."""
    if not _HAVE_SD:
        return [("", "sounddevice nicht installiert", "")]
    assert _sd is not None
    head: List[Tuple[str, str, str]] = [("", "System-Standard", "PortAudio-Standardgerät")]
    for idx, label, tip in _device_rows(_sd, want_input=True):
        head.append((str(idx), label, tip))
    return head


def list_output_devices() -> List[Tuple[str, str, str]]:
    """Wie :func:`list_input_devices`, nur Wiedergabe-Geräte."""
    if not _HAVE_SD:
        return [("", "sounddevice nicht installiert", "")]
    assert _sd is not None
    head: List[Tuple[str, str, str]] = [("", "System-Standard", "PortAudio-Standardgerät")]
    for idx, label, tip in _device_rows(_sd, want_input=False):
        head.append((str(idx), label, tip))
    return head


def remap_live_device_id(
    saved_id: str,
    saved_label: str = "",
    *,
    input_device: bool,
) -> tuple[str, str]:
    """Mappt einen alten PA-Index auf einen aktuellen Listeneintrag.

    Liefert ``(id, anzeigename)``. Ungültige IDs werden geleert (``""``).
    """
    sid = str(saved_id or "").strip()
    slabel = str(saved_label or "").strip()
    if not sid and not slabel:
        return "", ""

    rows = list_input_devices() if input_device else list_output_devices()
    allowed = {r[0] for r in rows if r[0]}
    id_to_label = {r[0]: r[1] for r in rows if r[0]}

    if sid in allowed:
        return sid, id_to_label.get(sid, slabel)

    needles: list[str] = []
    if slabel:
        needles.append(_norm_match_key(slabel))
    if sid and _HAVE_SD:
        try:
            old_i = int(sid)
            all_d = _sd.query_devices()  # type: ignore[union-attr]
            if 0 <= old_i < len(all_d):
                old_name = str(all_d[old_i].get("name", ""))
                key = _norm_match_key(old_name)
                if key and key not in needles:
                    needles.append(key)
        except ValueError:
            pass

    best_id = ""
    best_lbl = ""
    best_score = 0.0
    for needle in needles:
        if not needle:
            continue
        for did, lbl, _tip in rows:
            if not did:
                continue
            score = _match_score(needle, lbl)
            if score >= _QT_PA_MATCH_MIN_SCORE and score > best_score:
                best_score = score
                best_id = did
                best_lbl = lbl
        if best_id:
            break
        if _HAVE_SD:
            assert _sd is not None
            for did, lbl, _tip in rows:
                if not did:
                    continue
                try:
                    j = int(did)
                except ValueError:
                    continue
                all_d = _sd.query_devices()
                if 0 <= j < len(all_d):
                    pa_name = str(all_d[j].get("name", ""))
                    score = _match_score(needle, pa_name)
                    if score >= _QT_PA_MATCH_MIN_SCORE and score > best_score:
                        best_score = score
                        best_id = did
                        best_lbl = lbl

    if best_id:
        return best_id, best_lbl

    return "", slabel


def remap_live_settings_devices(live: object) -> bool:
    """Remappt alle Live-PortAudio-Rollen; ``True`` wenn sich IDs geändert haben."""
    specs = (
        ("input_device_id", "input_device_label", True),
        ("output_device_id", "output_device_label", False),
        ("funk_output_device_id", "funk_output_device_label", False),
        ("funk_listen_input_device_id", "funk_listen_input_device_label", True),
    )
    changed = False
    for id_field, label_field, input_device in specs:
        old_id = str(getattr(live, id_field, "") or "")
        old_lbl = str(getattr(live, label_field, "") or "")
        new_id, new_lbl = remap_live_device_id(
            old_id, old_lbl, input_device=input_device
        )
        rows = list_input_devices() if input_device else list_output_devices()
        allowed = {r[0] for r in rows if r[0]}
        if old_id and old_id not in allowed and not new_id:
            new_id = ""
        if new_id != old_id:
            setattr(live, id_field, new_id)
            changed = True
        if new_lbl and new_lbl != old_lbl:
            setattr(live, label_field, new_lbl)
            changed = True
        elif new_id and not new_lbl:
            lbl = device_label_for_id(new_id, input_device=input_device)
            if lbl:
                setattr(live, label_field, lbl)
                if lbl != old_lbl:
                    changed = True
    clamp = getattr(live, "clamp_recursive", None)
    if callable(clamp):
        clamp()
    return changed


def resolve_duplex_device_indices(
    in_dev: Optional[int],
    out_dev: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """Wählt PA-Indizes für **ein** gemeinsames ``Stream`` mit gleicher Host-API.

    Nur für einen PortAudio‑Duplex‑Stream relevant. Fehlen gemeinsame APIs
    (z. B. zwei sehr unterschiedliche Karten), bleiben ``in_dev``/``out_dev``
    **unverändert** — die Engine kann dann auf **getrennte** Ein‑/Ausgabe‑Streams
    ausweichen.

    Unter Windows kann ein Slot sonst WASAPI haben, der andere WDM‑KS
    („Bad I/O device combination“ −9993). Dann Varianten desselben
    *Gerätenamens* so wählen, dass beide dieselbe Host‑API haben.
    """
    if not _HAVE_SD or _sd is None:
        return in_dev, out_dev
    if in_dev is None or out_dev is None:
        return in_dev, out_dev

    try:
        all_d = _sd.query_devices()
        di = _sd.query_devices(device=in_dev, kind="input")
        do = _sd.query_devices(device=out_dev, kind="output")
    except Exception:
        return in_dev, out_dev

    hi = int(di.get("hostapi", -1))
    ho = int(do.get("hostapi", -1))
    if hi >= 0 and ho >= 0 and hi == ho:
        return in_dev, out_dev

    key_in = _norm_group_key(str(di.get("name", "")))
    key_out = _norm_group_key(str(do.get("name", "")))

    def candidates(key: str, want_input: bool) -> List[Tuple[int, int]]:
        xs: List[Tuple[int, int]] = []
        for i, d in enumerate(all_d):
            try:
                if want_input:
                    if int(d.get("max_input_channels") or 0) <= 0:
                        continue
                else:
                    if int(d.get("max_output_channels") or 0) <= 0:
                        continue
                if _norm_group_key(str(d.get("name", ""))) != key:
                    continue
                h = int(d.get("hostapi", -1))
                if h >= 0:
                    xs.append((i, h))
            except Exception:
                continue
        return xs

    cin = candidates(key_in, True)
    cout = candidates(key_out, False)
    in_apis = {h for _, h in cin}
    out_apis = {h for _, h in cout}
    common = in_apis & out_apis
    if not common:
        return in_dev, out_dev

    ha_list = sorted(
        common,
        key=lambda h_api: (_hostapi_rank(_hostapi_name_by_idx(_sd, h_api)), h_api),
    )

    for cand_h in ha_list:
        best_in = None
        best_rank = None
        for i, h in cin:
            if h != cand_h:
                continue
            r = (
                _hostapi_rank(_hostapi_name_for_device(_sd, all_d[i])),
                i,
            )
            if best_rank is None or r < best_rank:
                best_rank = r
                best_in = i
        best_out = None
        best_rank_o = None
        for i, h in cout:
            if h != cand_h:
                continue
            r = (
                _hostapi_rank(_hostapi_name_for_device(_sd, all_d[i])),
                i,
            )
            if best_rank_o is None or r < best_rank_o:
                best_rank_o = r
                best_out = i
        if best_in is not None and best_out is not None:
            return int(best_in), int(best_out)

    return in_dev, out_dev


def parse_device_id(raw: str | None, allowed: Sequence[str] | None = None) -> int | None:
    """„“ oder None → None (PortAudio-DEFAULT).
    Erlaubt optional Schnellprüfung gegen eine erlaubte ID-Liste.
    """
    if raw is None or str(raw).strip() == "":
        return None
    sid = str(raw).strip()
    if allowed is not None and sid not in allowed:
        return None
    try:
        return int(sid)
    except ValueError:
        return None


def coerce_output_pa_index(pa_index_optional: Optional[int]) -> Optional[int]:
    """Löst Ausgangs-Index ``None`` (PortAudio-Standard) zum aktuellen Default-Gerät auf.

    Zwei Wahlen („System-Standard“ vs. konkrete Karte desselben Endpunkts) sollen
    nicht zwei parallele ``OutputStreams`` öffnen und den Monitor doppelt befeuern.
    """
    if not _HAVE_SD or _sd is None:
        return pa_index_optional
    if pa_index_optional is not None:
        try:
            return int(pa_index_optional)
        except (TypeError, ValueError):
            return None
    try:
        pair = getattr(_sd.default, "device", None)
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            oidx = pair[1]
            if isinstance(oidx, int) and oidx >= 0:
                return int(oidx)
        if isinstance(pair, dict):
            oidx_raw = pair.get("output") or pair.get("1")
            if isinstance(oidx_raw, int) and oidx_raw >= 0:
                return int(oidx_raw)
    except Exception:
        pass
    return None


def physical_same_output(pa_a: Optional[int], pa_b: Optional[int]) -> bool:
    """Zwei Ausgangs-Wahlen (:func:`parse_device_id`‑Index inkl. Default) dasselbe Gerät?"""
    ia = coerce_output_pa_index(pa_a)
    ib = coerce_output_pa_index(pa_b)
    if ia is not None and ib is not None:
        if ia == ib:
            return True
        if not _HAVE_SD or _sd is None:
            return False
        try:
            da = _sd.query_devices(ia, "output")  # type: ignore[arg-type]
            db = _sd.query_devices(ib, "output")  # type: ignore[arg-type]
            ga = _norm_group_key(str(da.get("name", "")))
            gb = _norm_group_key(str(db.get("name", "")))
            return bool(ga) and ga == gb
        except Exception:
            return False
    # Beide ohne auflösbaren Index — nur dann als „gleich“ (kein Vergleich sonst möglich)
    return ia is None and ib is None


def coerce_input_pa_index(pa_index_optional: Optional[int]) -> Optional[int]:
    """Löst Eingangs-Index ``None`` (PortAudio-Standard) zum aktuellen Default‑Aufnahmegerät auf."""
    if not _HAVE_SD or _sd is None:
        return pa_index_optional
    if pa_index_optional is not None:
        try:
            return int(pa_index_optional)
        except (TypeError, ValueError):
            return None
    try:
        pair = getattr(_sd.default, "device", None)
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            iidx = pair[0]
            if isinstance(iidx, int) and iidx >= 0:
                return int(iidx)
        if isinstance(pair, dict):
            i_raw = pair.get("input") or pair.get("0")
            if isinstance(i_raw, int) and i_raw >= 0:
                return int(i_raw)
    except Exception:
        pass
    return None


def physical_same_input(pa_a: Optional[int], pa_b: Optional[int]) -> bool:
    """Zwei Eingangs-Wahlen dasselbe logische Aufnahmegerät?"""
    ia = coerce_input_pa_index(pa_a)
    ib = coerce_input_pa_index(pa_b)
    if ia is not None and ib is not None:
        if ia == ib:
            return True
        if not _HAVE_SD or _sd is None:
            return False
        try:
            da = _sd.query_devices(ia, "input")  # type: ignore[arg-type]
            db = _sd.query_devices(ib, "input")  # type: ignore[arg-type]
            ga = _norm_group_key(str(da.get("name", "")))
            gb = _norm_group_key(str(db.get("name", "")))
            return bool(ga) and ga == gb
        except Exception:
            return False
    return ia is None and ib is None


def device_label_for_id(device_id: str, *, input_device: bool) -> str:
    """Anzeigename aus der Live-Geräteliste für einen PortAudio-Index."""
    sid = str(device_id or "").strip()
    if not sid:
        return ""
    rows = list_input_devices() if input_device else list_output_devices()
    for did, lbl, _tip in rows:
        if did == sid:
            return lbl
    return ""


def windows_samplerate_hints_for_live(live: object) -> List[float]:
    """Windows-WASAPI-Sampleraten der gewählten Live-Geräte (Reihenfolge = Priorität)."""
    import sys

    if sys.platform != "win32":
        return []
    try:
        from audio.windows_endpoint_volume import (
            windows_mix_samplerates_for_labels,
        )
    except ImportError:
        return []

    label_specs: list[tuple[str, bool]] = []
    specs = (
        (str(getattr(live, "input_device_id", "") or ""), True),
        (str(getattr(live, "output_device_id", "") or ""), False),
        (str(getattr(live, "funk_output_device_id", "") or ""), False),
        (str(getattr(live, "funk_listen_input_device_id", "") or ""), True),
    )
    for dev_id, capture in specs:
        lbl = device_label_for_id(dev_id, input_device=capture)
        if lbl:
            label_specs.append((lbl, capture))
    if not label_specs:
        return []
    return [float(sr) for sr in windows_mix_samplerates_for_labels(label_specs)]


__all__ = [
    "coerce_input_pa_index",
    "coerce_output_pa_index",
    "device_label_for_id",
    "list_input_devices",
    "list_output_devices",
    "parse_device_id",
    "physical_same_input",
    "physical_same_output",
    "remap_live_device_id",
    "remap_live_settings_devices",
    "resolve_duplex_device_indices",
    "sounddevice_available",
    "windows_samplerate_hints_for_live",
]
