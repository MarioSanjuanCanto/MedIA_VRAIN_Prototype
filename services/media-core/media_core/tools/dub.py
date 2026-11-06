"""Atomic MCP tool for temporally synchronized clip dubbing."""

from __future__ import annotations

from pathlib import Path

from media_core.formatting import format_seconds
from media_core.runtime import CancellationRequested, check_cancelled, measured, stage
from media_core.services.dubbing import render_dubbed_video, translate_segments, validate_timed_segments
from media_core.services.scene_detection import probe_video
from media_core.storage import load_test_vector, resolve_video_file
from media_core.tools.clips import media_clips


LANGUAGE_ALIASES = {
    "es": "es", "español": "es", "espanol": "es", "castellano": "es", "spanish": "es",
    "ca": "ca", "catalán": "ca", "catalan": "ca", "català": "ca", "valenciano": "ca",
    "en": "en", "inglés": "en", "ingles": "en", "english": "en",
    "fr": "fr", "francés": "fr", "frances": "fr", "french": "fr",
    "de": "de", "alemán": "de", "aleman": "de", "german": "de",
    "it": "it", "italiano": "it", "italian": "it",
    "pt": "pt", "portugués": "pt", "portugues": "pt", "portuguese": "pt",
}


def _relative_segments(video_id: str, start: float, end: float) -> list[dict]:
    vector = load_test_vector(video_id)
    source = vector.get("segments", []) if vector else []
    selected = []
    for item in source:
        try:
            item_start = float(item.get("start_seconds", item.get("start", 0)))
            item_end = float(item.get("end_seconds", item.get("end", 0)))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text", "")).strip()
        if not text or item_end <= start or item_start >= end:
            continue
        selected.append({
            "start_seconds": max(item_start, start) - start,
            "end_seconds": min(item_end, end) - start,
            "speaker": str(item.get("speaker", "")),
            "text": text,
        })
    return _coalesce(selected)


def _coalesce(segments: list[dict]) -> list[dict]:
    """Build non-overlapping speech turns suitable for one TTS call each."""
    result: list[dict] = []
    for raw in sorted(segments, key=lambda item: (item["start_seconds"], item["end_seconds"])):
        item = dict(raw)
        if result and item["start_seconds"] < result[-1]["end_seconds"]:
            item["start_seconds"] = result[-1]["end_seconds"]
        if item["end_seconds"] - item["start_seconds"] < 0.15:
            continue
        previous = result[-1] if result else None
        if (previous and item.get("speaker") == previous.get("speaker")
                and item["start_seconds"] - previous["end_seconds"] <= 0.35
                and item["end_seconds"] - previous["start_seconds"] <= 14):
            previous["end_seconds"] = item["end_seconds"]
            previous["text"] = f'{previous["text"]} {item["text"]}'.strip()
        else:
            result.append(item)
    return result


def _transcribe_clip(source: Path) -> list[dict]:
    from media_core.tools.transcribe import media_transcribe

    result = media_transcribe(
        source=str(source), language="auto", diarization=True,
        full_segments=True, index_transcript=False,
    )
    if result.get("status") != "success":
        raise ValueError(result.get("message") or "No se pudo transcribir el clip para doblarlo.")
    return _coalesce(result.get("segments") or [])


@measured
def media_dub_clip(
    source: str = "",
    target_language: str = "en",
    video_id: str = "",
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    voice_provider: str = "piper",
    lip_sync: bool = False,
    background: bool = True,
    wait_seconds: float = 25,
) -> dict:
    """Traduce y dobla un vídeo o clip conservando exactamente sus tiempos.

    Para doblar un clip recién creado, pasa su `cut_video_file` en source y los
    `video_id`, `start_seconds` y `end_seconds` que devolvió media_clips. Así se
    reutiliza la transcripción original sin volver a transcribir ni pagar ASR.

    Espera en el servidor hasta 25 segundos y devuelve el resultado si acaba.
    Si sigue activo devuelve job_id: consulta media_job_status con wait_seconds=25
    hasta completed/failed/cancelled; el MP4 final está en result. No uses terminal ni sleep.
    No repitas el doblaje mientras ese trabajo esté activo. background=False
    espera el resultado completo y se reserva para llamadas internas.

    El audio traducido se ajusta frase a frase a los intervalos originales. La
    mezcla conserva música, ambiente y efectos y atenúa el audio original durante
    cada intervención. No modifica los fotogramas ni realiza sincronización labial.

    Args:
        source: Ruta del MP4 completo o del clip devuelto por media_clips.
        target_language: Idioma de destino: es, ca, en, fr, de, it o pt.
        video_id: ID del vídeo original; necesario para reutilizar sus timestamps.
        start_seconds: Inicio absoluto del clip en el vídeo original.
        end_seconds: Final absoluto del clip en el vídeo original.
        voice_provider: piper para es/ca/en local; qwen para idiomas compatibles remotos.
        lip_sync: Debe ser False; esta herramienta sincroniza audio, no modifica labios.
        background: True para evitar timeouts del cliente MCP durante la generación.
        wait_seconds: Espera inicial de 0 a 25 segundos; devuelve antes si el trabajo termina.
    """
    if lip_sync:
        return {
            "status": "error",
            "message": "El lip-sync visual no está disponible. Usa lip_sync=false para doblaje con sincronización temporal exacta.",
        }
    requested_language = target_language.strip().lower()
    language = LANGUAGE_ALIASES.get(requested_language)
    if language is None:
        return {"status": "error", "message": "target_language debe ser es, ca, en, fr, de, it o pt."}
    if voice_provider == "piper" and language not in {"es", "ca", "en"}:
        return {"status": "error", "message": "Piper solo admite es, ca y en; usa voice_provider='qwen'."}
    if (start_seconds is None) != (end_seconds is None):
        return {"status": "error", "message": "Indica start_seconds y end_seconds juntos."}
    if not 0 <= wait_seconds <= 25:
        return {'status': 'error', 'message': 'wait_seconds debe estar entre 0 y 25.'}

    if background:
        from media_core.tools.jobs import _submit, wait_for_media_result
        return wait_for_media_result(_submit(media_dub_clip, dict(source=source, target_language=language,
            video_id=video_id, start_seconds=start_seconds, end_seconds=end_seconds,
            voice_provider=voice_provider, lip_sync=False, background=False)), wait_seconds)
    check_cancelled()

    clip = resolve_video_file(source) if source.strip() else None
    clip_result = None
    if clip is None and video_id.strip() and start_seconds is not None:
        with stage("clip_cut"):
            clips = media_clips(
                video_id=video_id, start_seconds=start_seconds, end_seconds=end_seconds,
                max_clips=1, cut_video_files=True,
            )
        if not clips or clips[0].get("status") != "success" or not clips[0].get("cut_video_file"):
            return {"status": "error", "message": (clips[0].get("message") if clips else "No se pudo crear el clip de origen.")}
        clip_result = clips[0]
        clip = Path(clip_result["cut_video_file"])
    if clip is None:
        return {"status": "error", "message": "Indica source, o video_id junto con start_seconds y end_seconds."}

    try:
        duration = float(probe_video(clip)["duration"])
    except Exception as exc:
        return {"status": "error", "message": f"No se pudo verificar el clip de origen: {exc}"}
    if duration <= 0:
        return {"status": "error", "message": "El clip de origen tiene duración inválida."}

    try:
        if video_id.strip() and start_seconds is not None:
            absolute_start, absolute_end = float(start_seconds), float(end_seconds)
            if absolute_start < 0 or absolute_end <= absolute_start:
                raise ValueError("El intervalo original no es válido.")
            segments = _relative_segments(video_id.strip().upper(), absolute_start, absolute_end)
        else:
            with stage("transcription"):
                segments = _transcribe_clip(clip)
        segments = validate_timed_segments(segments, duration)
        with stage("translation"):
            translated = translate_segments(segments, language)
        check_cancelled()
        with stage("dub_render"):
            rendered = render_dubbed_video(
                clip, translated, language, voice_provider=voice_provider,
                output_name=f"{video_id or clip.stem}_clip",
            )
    except CancellationRequested:
        raise
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    return {
        "status": "success",
        "video_id": video_id.strip().upper() or clip.stem,
        "source_clip_name": clip.name,
        "dubbed_video_file": rendered["video_file"],
        "target_language": language,
        "duration_seconds": round(duration, 3),
        "duration": format_seconds(duration),
        "segments_dubbed": len(translated),
        "synchronization": "segment_timestamps_duration_locked",
        "audio_strategy": "sidechain_duck_original_plus_translated_voice",
        "lip_sync": False,
        "voice_provider": voice_provider,
        "cache_hit": rendered["cache_hit"],
        "audio_verification": rendered.get('audio_verification'),
        "clip_created_in_this_call": clip_result is not None,
    }
