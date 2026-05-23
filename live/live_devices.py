"""PortAudio-/sounddevice-Geräteliste (getrennt von Qt Multimedia).

Es werden nur echte Aufnahmen bzw. Wiedergabe-Endpunkte aufgelistet – und
gleichnamige Einträge (derselbe Name unter MME/WASAPI/DirectSound/…) zu
je **einem** PortAudio‑Gerät zusammengeführt, wie im Qt‑Gerätedialog.
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


def _dedupe_device_rows(
    sd: object,
    *,
    want_input: bool,
) -> List[Tuple[int, str, str]]:
    """Pro logischem Gerät eine Zeile: (pa_index, anzeige_name, tooltip)."""
    rows: dict[str, tuple[int, int, str, str]] = {}
    # key_norm -> (rank, index, api_name, display_name)

    for i, d in enumerate(sd.query_devices()):  # type: ignore[union-attr]
        if want_input:
            if int(d["max_input_channels"]) <= 0:
                continue
        else:
            if int(d["max_output_channels"]) <= 0:
                continue

        display = str(d.get("name", f"Gerät {i}")).strip() or f"Gerät {i}"
        api = _hostapi_name_for_device(sd, d)
        rank = _hostapi_rank(api)
        gk = _norm_group_key(display)

        prev = rows.get(gk)
        if prev is None:
            rows[gk] = (rank, i, api, display)
            continue
        prev_rank, prev_i, prev_api, prev_disp = prev
        if rank < prev_rank or (rank == prev_rank and i < prev_i):
            rows[gk] = (rank, i, api, display)

    out_list: List[Tuple[int, str, str]] = []
    for _gk, (_r, idx, api, disp) in sorted(
        rows.items(),
        key=lambda kv: kv[1][3].lower(),
    ):
        tip = f"PortAudio #{idx}"
        if api:
            tip += f" — {api}"
        out_list.append((idx, disp, tip))
    return out_list


def list_input_devices() -> List[Tuple[str, str, str]]:
    """[(id, Kurzlabel, Tooltip), …]; erstes Tuple System-Standard.

    Kurzlabel ohne Host-API-Klammer — Doppelungen (MME/WASAPI/…) sind
    zusammengelegt; es bleibt der PortAudio-Index der bevorzugten API.
    """
    if not _HAVE_SD:
        return [("", "sounddevice nicht installiert", "")]
    assert _sd is not None
    head: List[Tuple[str, str, str]] = [("", "System-Standard", "PortAudio-Standardgerät")]
    for idx, label, tip in _dedupe_device_rows(_sd, want_input=True):
        head.append((str(idx), label, tip))
    return head


def list_output_devices() -> List[Tuple[str, str, str]]:
    """Wie :func:`list_input_devices`, nur Wiedergabe-Geräte."""
    if not _HAVE_SD:
        return [("", "sounddevice nicht installiert", "")]
    assert _sd is not None
    head: List[Tuple[str, str, str]] = [("", "System-Standard", "PortAudio-Standardgerät")]
    for idx, label, tip in _dedupe_device_rows(_sd, want_input=False):
        head.append((str(idx), label, tip))
    return head


def remap_live_device_id(saved_id: str, *, input_device: bool) -> str:
    """Mappt einen alten PA-Index ggf. auf den nach Deduplizierung gewählten Kanon."""
    sid = str(saved_id or "").strip()
    if not sid or not _HAVE_SD:
        return sid
    rows = list_input_devices() if input_device else list_output_devices()
    allowed = {r[0] for r in rows if r[0]}
    if sid in allowed:
        return sid
    assert _sd is not None
    try:
        old_i = int(sid)
    except ValueError:
        return ""
    all_d = _sd.query_devices()
    if old_i < 0 or old_i >= len(all_d):
        return ""
    key = _norm_group_key(str(all_d[old_i].get("name", "")))
    if not key:
        return ""
    for did, _lbl, _tip in rows:
        if not did:
            continue
        try:
            j = int(did)
        except ValueError:
            continue
        if 0 <= j < len(all_d):
            if _norm_group_key(str(all_d[j].get("name", ""))) == key:
                return did
    return ""


def resolve_duplex_device_indices(
    in_dev: Optional[int],
    out_dev: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """Wählt PA-Indizes für **ein** gemeinsames ``Stream`` mit gleicher Host-API.

    Nur für einen PortAudio‑Duplex‑Stream relevant. Fehlen gemeinsame APIs
    (z. B. zwei sehr unterschiedliche Karten), bleiben ``in_dev``/``out_dev``
    **unverändert** — die Engine kann dann auf **getrennte** Ein‑/Ausgabe‑Streams
    ausweichen.

    Bei der Gerätededuplizierung kann ein Slot sonst WASAPI haben, der andere
    WDM‑KS („Bad I/O device combination“ −9993). Dann Varianten desselben
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


__all__ = [
    "coerce_input_pa_index",
    "coerce_output_pa_index",
    "list_input_devices",
    "list_output_devices",
    "parse_device_id",
    "physical_same_input",
    "physical_same_output",
    "remap_live_device_id",
    "resolve_duplex_device_indices",
    "sounddevice_available",
]
