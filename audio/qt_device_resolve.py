"""Qt-Multimedia-Geräte: stabile Zuordnung trotz geänderter Windows-Namen.

Windows vergibt bei anderem USB-Port oft neue MMDevice-GUIDs und Anzeigenamen
(``(2- USB Audio CODEC)``). Gespeichert werden GUID **und** Anzeigename;
beim Laden wird per normalisiertem Gerätenamen auf die aktuelle ID gemappt
(analog zu :func:`live.live_devices.remap_live_device_id`).
"""

from __future__ import annotations

from live.live_devices import _QT_PA_MATCH_MIN_SCORE, _match_score, _norm_group_key

# Gleicher Schwellwert wie Live PA ↔ Qt
_QT_MATCH_MIN_SCORE = _QT_PA_MATCH_MIN_SCORE


def list_qt_audio_devices(*, input_device: bool) -> list[tuple[str, str]]:
    """[(id, Anzeigename), …] — leere id = System-Standard."""
    if input_device:
        from audio.audio_recorder import list_audio_input_devices

        return list_audio_input_devices()
    from audio.player_controller import list_audio_output_devices

    return list_audio_output_devices()


def qt_device_label_for_id(device_id: str, *, input_device: bool) -> str:
    sid = str(device_id or "").strip()
    if not sid:
        return ""
    for did, lbl in list_qt_audio_devices(input_device=input_device):
        if did == sid:
            return lbl
    return ""


def remap_qt_device_id(
    saved_id: str,
    saved_label: str,
    *,
    input_device: bool,
) -> tuple[str, str]:
    """Liefert ``(aktuelle_id, aktueller_anzeigename)``.

    Ist die gespeicherte GUID noch gültig, bleibt sie erhalten (Label wird
    aktualisiert). Sonst Fuzzy-Match über den gespeicherten Anzeigenamen.
    """
    sid = str(saved_id or "").strip()
    slabel = str(saved_label or "").strip()
    rows = list_qt_audio_devices(input_device=input_device)

    if not sid and not slabel:
        return "", ""

    for did, lbl in rows:
        if sid and did == sid:
            return did, lbl

    needle = _norm_group_key(slabel) if slabel else ""
    if not needle and sid:
        # Legacy: gespeicherte ID war früher manchmal der Anzeigename
        needle = _norm_group_key(sid)

    if needle:
        best_id = ""
        best_lbl = ""
        best_score = 0.0
        for did, lbl in rows:
            if not did:
                continue
            score = _match_score(needle, lbl)
            if score >= _QT_MATCH_MIN_SCORE and score > best_score:
                best_score = score
                best_id = did
                best_lbl = lbl
        if best_id:
            return best_id, best_lbl

    return sid, slabel


def resolve_qt_device_id(
    saved_id: str,
    saved_label: str,
    *,
    input_device: bool,
) -> str:
    """Nur die aufgelöste Geräte-ID (für Player/Recorder zur Laufzeit)."""
    resolved_id, _label = remap_qt_device_id(
        saved_id, saved_label, input_device=input_device
    )
    return resolved_id


def remap_global_audio_devices(global_audio: object) -> bool:
    """Mappt alle Qt-Rollen in ``GlobalAudioSettings``; ``True`` wenn geändert."""
    from model.global_audio_settings import ROLE_INPUT, ROLE_PC, ROLE_SEND

    changed = False
    for role, input_device in (
        (ROLE_INPUT, True),
        (ROLE_SEND, False),
        (ROLE_PC, False),
    ):
        old_id = str(global_audio.device_id_for(role) or "")
        old_lbl = str(global_audio.device_label_for(role) or "")
        new_id, new_lbl = remap_qt_device_id(
            old_id, old_lbl, input_device=input_device
        )
        if new_id != old_id:
            global_audio.set_device_id_for(role, new_id)
            changed = True
        if new_lbl and new_lbl != old_lbl:
            global_audio.set_device_label_for(role, new_lbl)
            changed = True
    return changed
