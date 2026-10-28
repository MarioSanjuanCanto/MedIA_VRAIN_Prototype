"""Formatting helpers for MCP responses."""

from media_core.config import WHISPERX_MODEL


def format_seconds(seconds: float) -> str:
    """Convert seconds to HH:MM:SS."""
    secs = int(round(seconds))
    hrs = secs // 3600
    mins = (secs % 3600) // 60
    remaining_seconds = secs % 60
    return f"{hrs:02d}:{mins:02d}:{remaining_seconds:02d}"


def build_transcribe_agent_response(
    full_data: dict, full_segments: bool = False
) -> dict:
    """Build the compact transcription response returned to the agent."""
    if full_segments:
        return full_data

    segments = full_data.get("segments", [])
    speakers = full_data.get("speakers", [])
    full_text = full_data.get("full_text", "")
    preview = full_text[:350] + ("..." if len(full_text) > 350 else "")

    return {
        "status": "success",
        "video_id": full_data.get("id"),
        "source": full_data.get("source"),
        "index_error": full_data.get("index_error"),
        "title": full_data.get("title"),
        "model_used": full_data.get(
            "model_used", f"{WHISPERX_MODEL} (Replicate)"
        ),
        "language": full_data.get("language", "es"),
        "duration": full_data.get("duration", "00:00"),
        "duration_seconds": full_data.get("duration_seconds", 0),
        "total_segments": len(segments),
        "speakers_detected": speakers[:12],
        "total_speakers_count": len(speakers),
        "transcription_preview": preview,
        "sample_first_segments": segments[:2],
        "sample_last_segments": segments[-2:] if len(segments) >= 2 else [],
        "indexed_in_search": full_data.get("indexed_in_search"),
        "cache_hit": full_data.get("cache_hit", False),
        "cache_validation": full_data.get("cache_validation"),
        "message": (
            f"Transcripción completada con éxito ({len(segments)} segmentos) disponibles en MedIA Core. "
            "La transcripción completa está guardada en el servidor. NO necesitas ejecutar código Python propio: "
            "utiliza 'media_search(query=...)' para encontrar momentos específicos, 'media_summarize(video_id=...)' "
            "para el resumen ejecutivo con Gemma 4, o 'media_clips(video_id=..., query=...)' para cortar clips en MP4."
        ),
    }
