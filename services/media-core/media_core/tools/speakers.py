"""MCP tool for speaker identity resolution, turn querying, and timeline discovery."""

from typing import Any

from media_core.runtime import measured
from media_core.services.speaker_identities import (
    get_or_resolve_speaker_identities,
    get_speaker_at_timestamp,
    get_speaker_turns,
)
from media_core.storage import load_test_vector, resolve_video_file


@measured
def media_speakers(
    video_id: str = "TV04",
    timestamp: float | None = None,
    person: str = "",
) -> dict[str, Any]:
    """Enumera todas las intervenciones ordenadas de una persona en un vídeo.

    Usa `person` para obtener los turnos numerados y sus tiempos; después
    `media_clips(person=..., turn_index=...)` corta cada uno. `media_search`
    encuentra menciones textuales y no sirve para contar intervenciones.

    Permite a Hermes saber exactamente qué persona o cargo está hablando en cualquier momento
    del vídeo (ej. minuto 20:30 -> Ignacio Garriga, VOX) y consultar la lista completa de
    turnos de palabra de un político o interviniente (ej. Salvador Illa o 'president').

    Args:
        video_id: Identificador del vídeo ('TV04', etc.) o ruta al archivo.
        timestamp: Segundo exacto opcional a consultar (ej: 1230.0 para min 20:30).
        person: Nombre o rol opcional (ej: 'Salvador Illa', 'president', 'Ignacio Garriga') para listar todos sus turnos.
    """
    video_file = resolve_video_file(video_id)
    clean_id = video_file.stem.replace("_demo", "").upper() if video_file else video_id.strip().upper()

    vector = load_test_vector(clean_id)
    if not vector or not vector.get("segments"):
        return {
            "status": "error",
            "message": f"No se encontró transcripción con diarización para el vídeo '{clean_id}'.",
        }

    segments = vector.get("segments", [])

    # Case 1: Query who is speaking at a specific timestamp
    if timestamp is not None:
        speaker_at_t = get_speaker_at_timestamp(clean_id, timestamp, segments=segments)
        return speaker_at_t

    # Case 2: Query all turns for a specific person or role
    if person:
        turns = get_speaker_turns(clean_id, person, segments=segments)
        return {
            "status": "success",
            "video_id": clean_id,
            "query_person": person,
            "turns_count": len(turns),
            "turns": turns,
        }

    # Case 3: Return the directory of all identified speakers in the video
    identities = get_or_resolve_speaker_identities(clean_id, segments=segments)
    return {
        "status": "success",
        "video_id": clean_id,
        "speakers_count": len(identities),
        "speakers": identities,
    }
