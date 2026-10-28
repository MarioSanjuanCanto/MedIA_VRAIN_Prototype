"""Service for extracting, resolving and propagating speaker identities across video transcripts."""

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from media_core.config import IDENTITIES_CACHE_DIR
from media_core.storage import load_test_vector

logger = logging.getLogger(__name__)

# Known institutional entities mapping
KNOWN_INSTITUTIONAL_ROLES = [
    (re.compile(r'\b(?:presidente|president)\s+de\s+la\s+generalitat\b', re.I), "Salvador Illa", "President de la Generalitat"),
    (re.compile(r'\b(?:consejera|consellera)\s+de\s+salud\b', re.I), "Olga Pané", "Consellera de Salut"),
    (re.compile(r'\b(?:presidenta|president)\s+del\s+parlament\b', re.I), "Josep Rull", "President del Parlament"),
]

# Parliamentary group mappings
PARLIAMENTARY_GROUPS = [
    (re.compile(r'\bvox\b', re.I), "VOX", "Diputado VOX"),
    (re.compile(r'\b(?:partido\s+popular|ppc|pp)\b', re.I), "PPC", "Diputado PPC"),
    (re.compile(r'\b(?:esquerra\s+republicana|erc)\b', re.I), "ERC", "Diputado ERC"),
    (re.compile(r'\bjunts\b', re.I), "Junts", "Diputada Junts"),
    (re.compile(r'\b(?:comuns|comun)\b', re.I), "Comuns", "Diputada Comuns"),
    (re.compile(r'\b(?:candidatura\s+de\s+unidad\s+popular|cup)\b', re.I), "CUP", "Diputada CUP"),
    (re.compile(r'\b(?:grup\s+parlamentari\s+mixt|grupo\s+parlamentario\s+mix|alian[çc]a)\b', re.I), "Aliança Catalana", "Diputada Aliança Catalana"),
    (re.compile(r'\b(?:socialistas\s+y\s+unidos|psc)\b', re.I), "PSC", "Diputado PSC"),
]

# Canonical name corrections for common ASR misspellings
CANONICAL_NAMES = {
    "ignacio garriga": "Ignacio Garriga",
    "ignacio garriga vaz de concicao": "Ignacio Garriga",
    "ignacio garriga, abad de concicau": "Ignacio Garriga",
    "alejandro fernandez": "Alejandro Fernández",
    "alejandro fernandez alvarez": "Alejandro Fernández",
    "josep maria jove": "Josep Maria Jové",
    "josep maria jove illador": "Josep Maria Jové",
    "josep maria jove llado": "Josep Maria Jové",
    "monica sales": "Mònica Sales",
    "monica sales de la cruz": "Mònica Sales",
    "monica salez": "Mònica Sales",
    "monica salez de la cruz": "Mònica Sales",
    "jessica albiach": "Jéssica Albiach",
    "jessica albiach-sa torres": "Jéssica Albiach",
    "maria pilar castillejo": "Pilar Castillejo",
    "maria pilar castillejo medina": "Pilar Castillejo",
    "pilar castillejo": "Pilar Castillejo",
    "silvia oriol": "Sílvia Orriols",
    "silvia oriol serra": "Sílvia Orriols",
    "silvia orriols": "Sílvia Orriols",
    "silvia orriols serra": "Sílvia Orriols",
    "salvador illa": "Salvador Illa",
    "josep rull": "Josep Rull",
}

TURN_CUE_RE = re.compile(
    r'\b(?:tiene la palabra|te la paraula|you have the floor|para responder,?\s+tiene la palabra|para repreguntar,?\s+tiene la palabra|para formularla,?\s+tiene la palabra)\b',
    re.I,
)


def normalize_ascii(text: str) -> str:
    """Normalize text removing accents for robust comparison."""
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


def clean_extracted_name(raw_name: str) -> str:
    """Clean name extracted from parliamentary introduction."""
    clean = re.sub(r'^(?:el|la)\s+diputad[ao]\s+', '', raw_name, flags=re.I)
    clean = re.sub(r'^(?:señor|señora|senyor|senyora)\s+', '', clean, flags=re.I)
    clean = re.sub(r'^(?:don|doña)\s+', '', clean, flags=re.I)
    clean = re.split(r'[,;\.]|\s+(?:por|para|de acuerdo|del grupo)\b', clean, flags=re.I)[0]
    clean = clean.strip()
    norm = normalize_ascii(clean)
    for key, canonical in CANONICAL_NAMES.items():
        if key in norm or norm in key:
            return canonical
    return clean.title() if clean else ""


def resolve_speaker_identities(segments: list[dict], video_id: str = "") -> dict[str, dict]:
    """Parse transcript segments and resolve real identities for generic SPEAKER_XX labels."""
    identities: dict[str, dict] = {}
    moderator_counts: dict[str, int] = {}

    for i, seg in enumerate(segments):
        text = seg.get("text", "")
        speaker = seg.get("speaker", "")
        if not speaker or speaker.lower() in {"hablante", "unknown", "speaker"}:
            continue

        turn_match = TURN_CUE_RE.search(text)
        if not turn_match:
            continue

        # The speaker granting the floor is acting as moderator
        moderator_counts[speaker] = moderator_counts.get(speaker, 0) + 1

        # Look at who was called
        phrase_after = text[turn_match.end():].strip()
        phrase_before = text[:turn_match.start()].strip()
        full_intro = text

        detected_name = ""
        detected_role = ""
        detected_party = ""

        # 1. Check institutional role first (e.g. "presidente de la Generalitat")
        for pattern, inst_name, inst_role in KNOWN_INSTITUTIONAL_ROLES:
            if pattern.search(full_intro):
                detected_name = inst_name
                detected_role = inst_role
                break

        # 2. Check parliamentary group
        for pattern, party_name, default_role in PARLIAMENTARY_GROUPS:
            if pattern.search(full_intro):
                detected_party = party_name
                if not detected_role:
                    detected_role = default_role
                break

        # 3. Extract named speaker if not institutional
        if not detected_name and phrase_after:
            name_match = re.search(
                r'(?:(?:el|la)\s+diputad[ao]|(?:el|la)\s+senyora?|(?:el|la)\s+ministr[ao]|(?:el|la)\s+consejer[ao]|(?:el|la)\s+conseller[ao]|señor|señora|senyor|senyora)\s+(?:señor|señora|senyor|senyora)?\s*([A-ZÁÉÍÓÚÀÈÒÑa-záéíóúàèòñ\s\-\'\.]+)',
                phrase_after,
                re.I,
            )
            if not name_match:
                name_match = re.search(
                    r'(?:(?:el|la)\s+diputad[ao]|(?:el|la)\s+senyora?|(?:el|la)\s+ministr[ao]|(?:el|la)\s+consejer[ao]|(?:el|la)\s+conseller[ao]|señor|señora|senyor|senyora)\s+(?:señor|señora|senyor|senyora)?\s*([A-ZÁÉÍÓÚÀÈÒÑa-záéíóúàèòñ\s\-\'\.]+)',
                    full_intro,
                    re.I,
                )
            if name_match:
                extracted = clean_extracted_name(name_match.group(1))
                if extracted and len(extracted) > 3 and normalize_ascii(extracted) not in {"en nombre", "del grupo"}:
                    detected_name = extracted

        # Identify following speaker who actually takes the floor
        if detected_name or detected_role:
            next_idx = i + 1
            while next_idx < len(segments):
                next_speaker = segments[next_idx].get("speaker")
                if next_speaker and next_speaker != speaker and next_speaker.lower() not in {"hablante", "unknown", "speaker"}:
                    # Found the target speaker
                    if next_speaker not in identities or not identities[next_speaker].get("name"):
                        identities[next_speaker] = {
                            "speaker_id": next_speaker,
                            "name": detected_name or detected_role,
                            "role": detected_role or (f"Diputado {detected_party}" if detected_party else "Portavoz"),
                            "party": detected_party,
                            "evidence": text[:180],
                            "first_turn_seconds": segments[next_idx].get("start_seconds", 0.0),
                        }
                    elif detected_name and identities[next_speaker].get("name") != detected_name:
                        # If existing had only role, upgrade to exact name
                        identities[next_speaker]["name"] = detected_name
                        if detected_party:
                            identities[next_speaker]["party"] = detected_party
                    break
                next_idx += 1

    # Designate the principal moderator (e.g. Josep Rull / President del Parlament)
    if moderator_counts:
        main_moderator = max(moderator_counts.items(), key=lambda item: item[1])[0]
        if main_moderator not in identities:
            identities[main_moderator] = {
                "speaker_id": main_moderator,
                "name": "Josep Rull",
                "role": "President del Parlament (Moderador)",
                "party": "Junts",
                "evidence": "Fórmula reglamentaria de moderación de los turnos en el pleno",
                "first_turn_seconds": 0.0,
            }

    return identities


def get_or_resolve_speaker_identities(video_id: str, segments: list[dict] | None = None) -> dict[str, dict]:
    """Get cached speaker identities for video or resolve and cache them."""
    clean_id = video_id.strip().upper()
    cache_file = IDENTITIES_CACHE_DIR / f"{clean_id}_identities.json"

    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    segs = segments
    if not segs:
        vector = load_test_vector(clean_id)
        if vector:
            segs = vector.get("segments", [])

    if not segs:
        return {}

    identities = resolve_speaker_identities(segs, video_id=clean_id)
    if identities:
        try:
            cache_file.write_text(json.dumps(identities, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not write identities cache for {clean_id}: {exc}")

    return identities


def get_speaker_at_timestamp(
    video_id: str,
    timestamp: float,
    segments: list[dict] | None = None,
) -> dict[str, Any]:
    """Find active speaker at a given timestamp in seconds and return full identity and turn."""
    clean_id = video_id.strip().upper()
    segs = segments
    if not segs:
        vector = load_test_vector(clean_id)
        if vector:
            segs = vector.get("segments", [])

    if not segs:
        return {"status": "error", "message": f"No se encontró transcripción para '{clean_id}'"}

    identities = get_or_resolve_speaker_identities(clean_id, segs)

    # Find matching segment
    match_seg = None
    for seg in segs:
        start = seg.get("start_seconds", 0.0)
        end = seg.get("end_seconds", 0.0)
        if start - 1.0 <= timestamp <= end + 1.0:
            match_seg = seg
            break

    if not match_seg:
        # Closest segment
        match_seg = min(segs, key=lambda s: abs(s.get("start_seconds", 0.0) - timestamp))

    speaker_id = match_seg.get("speaker", "Desconocido")
    identity = identities.get(speaker_id, {})

    name = identity.get("name") or speaker_id
    role = identity.get("role", "")
    party = identity.get("party", "")

    return {
        "status": "success",
        "video_id": clean_id,
        "timestamp": timestamp,
        "speaker_id": speaker_id,
        "name": name,
        "role": role,
        "party": party,
        "display_name": f"{name} ({role})" if role and role != name else name,
        "segment_text": match_seg.get("text", ""),
        "segment_start": match_seg.get("start", ""),
        "segment_end": match_seg.get("end", ""),
        "evidence": identity.get("evidence", ""),
    }


def get_speaker_turns(
    video_id: str,
    person_or_query: str,
    segments: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Get all continuous speech turns for a specific person or role."""
    clean_id = video_id.strip().upper()
    segs = segments
    if not segs:
        vector = load_test_vector(clean_id)
        if vector:
            segs = vector.get("segments", [])

    if not segs:
        return []

    identities = get_or_resolve_speaker_identities(clean_id, segs)
    query_norm = normalize_ascii(person_or_query)

    # Find matching speaker_id
    target_speaker_id = None
    target_identity = None

    for spk_id, id_data in identities.items():
        name_norm = normalize_ascii(id_data.get("name", ""))
        role_norm = normalize_ascii(id_data.get("role", ""))
        party_norm = normalize_ascii(id_data.get("party", ""))

        if (
            query_norm in name_norm
            or name_norm in query_norm
            or any(w in name_norm for w in query_norm.split() if len(w) > 3)
            or (query_norm in role_norm)
            or ("president" in query_norm and "president" in role_norm)
            or (party_norm and query_norm in party_norm)
        ):
            target_speaker_id = spk_id
            target_identity = id_data
            break

    if not target_speaker_id:
        return []

    # Group contiguous segments of target_speaker_id into distinct turns
    turns = []
    current_turn: dict[str, Any] | None = None

    for seg in segs:
        if seg.get("speaker") == target_speaker_id:
            if current_turn is None:
                current_turn = {
                    "turn_index": len(turns) + 1,
                    "speaker_id": target_speaker_id,
                    "name": target_identity.get("name", target_speaker_id),
                    "role": target_identity.get("role", ""),
                    "party": target_identity.get("party", ""),
                    "start": seg.get("start", ""),
                    "end": seg.get("end", ""),
                    "start_seconds": seg.get("start_seconds", 0.0),
                    "end_seconds": seg.get("end_seconds", 0.0),
                    "text_snippets": [seg.get("text", "")],
                }
            else:
                current_turn["end"] = seg.get("end", "")
                current_turn["end_seconds"] = seg.get("end_seconds", 0.0)
                current_turn["text_snippets"].append(seg.get("text", ""))
        else:
            if current_turn is not None:
                current_turn["duration_seconds"] = round(
                    current_turn["end_seconds"] - current_turn["start_seconds"], 2
                )
                current_turn["full_text"] = " ".join(current_turn["text_snippets"])
                del current_turn["text_snippets"]
                turns.append(current_turn)
                current_turn = None

    if current_turn is not None:
        current_turn["duration_seconds"] = round(
            current_turn["end_seconds"] - current_turn["start_seconds"], 2
        )
        current_turn["full_text"] = " ".join(current_turn["text_snippets"])
        del current_turn["text_snippets"]
        turns.append(current_turn)

    return turns
