"""Atomic MCP tool for original-language or translated video subtitles."""

from __future__ import annotations

from pathlib import Path

from media_core.formatting import format_seconds
from media_core.runtime import CancellationRequested, check_cancelled, measured, stage
from media_core.services.dubbing import translate_segments, validate_timed_segments
from media_core.services.scene_detection import probe_video
from media_core.services.subtitles import render_subtitled_video
from media_core.storage import resolve_video_file
from media_core.tools.clips import media_clips
from media_core.tools.dub import LANGUAGE_ALIASES, _relative_segments, _transcribe_clip


ORIGINAL_LANGUAGE_ALIASES = {"", "auto", "original", "idioma original", "source", "source language"}


@measured
def media_subtitle_video(
    source: str = "",
    target_language: str = "original",
    video_id: str = "",
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    background: bool = True,
    wait_seconds: float = 25,
) -> dict:
    """Genera un MP4 con subtítulos visibles en el idioma solicitado.

    Si `target_language` se omite o vale `original`, conserva el texto original y
    no llama a ningún traductor. Si se pide otro idioma, traduce cada segmento y
    mantiene sus timestamps. Para subtitular un clip recién creado, pasa su
    `cut_video_file` junto con el `video_id`, `start_seconds` y `end_seconds`
    devueltos por media_clips, evitando una segunda transcripción.

    Espera en el servidor hasta 25 segundos y devuelve el resultado si acaba.
    Si sigue activo devuelve job_id; llama directamente media_job_status con
    wait_seconds=25 hasta completed/failed/cancelled. No uses terminal ni sleep.
    Si source es el MP4 doblado, conserva ese audio al añadir los subtítulos.

    Args:
        source: Ruta del vídeo completo o del clip devuelto por media_clips.
        target_language: original por defecto; también es, ca, en, fr, de, it o pt y sus nombres.
        video_id: ID original para reutilizar la transcripción existente.
        start_seconds: Inicio absoluto del clip en el vídeo original.
        end_seconds: Final absoluto del clip en el vídeo original.
        background: True evita bloquear al cliente MCP durante traducción/render.
        wait_seconds: Espera inicial de 0 a 25 segundos; termina en cuanto acaba el trabajo.
    """
    requested_language = target_language.strip().lower()
    original_requested = requested_language in ORIGINAL_LANGUAGE_ALIASES
    language = "original" if original_requested else LANGUAGE_ALIASES.get(requested_language)
    if language is None:
        return {"status": "error", "message": "target_language debe ser original, es, ca, en, fr, de, it o pt."}
    if (start_seconds is None) != (end_seconds is None):
        return {"status": "error", "message": "Indica start_seconds y end_seconds juntos."}
    if not 0 <= wait_seconds <= 25:
        return {'status': 'error', 'message': 'wait_seconds debe estar entre 0 y 25.'}

    if background:
        from media_core.tools.jobs import _submit, wait_for_media_result
        return wait_for_media_result(_submit(media_subtitle_video, dict(source=source, target_language=language,
            video_id=video_id, start_seconds=start_seconds, end_seconds=end_seconds, background=False)), wait_seconds)
    check_cancelled()

    requested_source = source.strip() or video_id.strip()
    video = resolve_video_file(requested_source) if requested_source else None
    clip_result = None
    original = resolve_video_file(video_id) if video_id.strip() else None
    is_original = not source.strip() or not video_id.strip() or (video is not None and original is not None and video.resolve() == original.resolve())
    if video is not None and start_seconds is not None and is_original:
        # A full source plus an interval still needs a physical clip whose subtitle
        # timestamps begin at zero.
        with stage("clip_cut"):
            clips = media_clips(
                video_id=video_id or source, start_seconds=start_seconds, end_seconds=end_seconds,
                max_clips=1, cut_video_files=True,
            )
        if not clips or clips[0].get("status") != "success" or not clips[0].get("cut_video_file"):
            return {"status": "error", "message": clips[0].get("message") if clips else "No se pudo crear el clip."}
        clip_result = clips[0]
        video = Path(clip_result["cut_video_file"])
    if video is None:
        return {"status": "error", "message": "Indica source o un video_id disponible."}

    try:
        duration = float(probe_video(video)["duration"])
        if video_id.strip() and start_seconds is not None:
            absolute_start, absolute_end = float(start_seconds), float(end_seconds)
            segments = _relative_segments(video_id.strip().upper(), absolute_start, absolute_end)
        elif video_id.strip() and source.strip():
            # A supplied clip without original bounds cannot safely reuse absolute
            # timestamps; transcribe that clip instead.
            with stage("transcription"):
                segments = _transcribe_clip(video)
        elif video_id.strip():
            segments = _relative_segments(video_id.strip().upper(), 0.0, duration)
        else:
            with stage("transcription"):
                segments = _transcribe_clip(video)
        segments = validate_timed_segments(segments, duration)
        check_cancelled()
        if original_requested:
            captions = [{**item, "subtitle_text": item["text"]} for item in segments]
        else:
            with stage("translation"):
                captions = [
                    {**item, "subtitle_text": item["translated_text"]}
                    for item in translate_segments(segments, language)
                ]
        with stage("subtitle_render"):
            check_cancelled()
            rendered = render_subtitled_video(
                video, captions, language,
                output_name=f"{video_id or video.stem}_clip",
            )
    except CancellationRequested:
        raise
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    return {
        "status": "success",
        "video_id": video_id.strip().upper() or video.stem,
        "source_video_name": video.name,
        "subtitled_video_file": rendered["video_file"],
        "subtitle_file_name": Path(rendered["subtitle_file"]).name,
        "subtitle_language": language,
        "translated": not original_requested,
        "segments_subtitled": len(captions),
        "duration_seconds": round(duration, 3),
        "duration": format_seconds(duration),
        "synchronization": "original_segment_timestamps",
        "cache_hit": rendered["cache_hit"],
        "clip_created_in_this_call": clip_result is not None,
    }
