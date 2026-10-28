"""MCP clip-selection and extraction tool."""

import json
import re
import sys
from pathlib import Path
import hashlib
import math
import subprocess
from media_core.runtime import measured, stage

from media_core.config import CACHE_DIR, DEFAULT_LLM_MODEL, OUTPUT_CLIPS_DIR, get_openrouter_key
from media_core.formatting import format_seconds
from media_core.services.ffmpeg import cut_clip, seconds
from media_core.services.clip_selection import compact_query, select_window, terms, normalize
from media_core.services.openrouter import call_openrouter
from media_core.services.scene_detection import probe_video
from media_core.services.speaker_identities import get_speaker_turns
from media_core.storage import load_test_vector, resolve_video_file, source_matches_file


def _parse_model_clips(raw_response: str, max_clips: int, search_topic: str) -> list[dict]:
    """Parse and annotate a JSON clip response without trusting its contents."""
    if not raw_response:
        return []
    clean_json = raw_response
    if "```json" in clean_json:
        clean_json = clean_json.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in clean_json:
        clean_json = clean_json.split("```", 1)[1].split("```", 1)[0]
    parsed = json.loads(clean_json.strip())
    clips_list = parsed.get("clips") if isinstance(parsed, dict) else parsed
    if not isinstance(clips_list, list):
        return []
    result = []
    for candidate in clips_list[:max_clips]:
        if not isinstance(candidate, dict):
            continue
        clip = dict(candidate)
        clip["model_generated_by"] = f"{DEFAULT_LLM_MODEL} (OpenRouter)"
        if search_topic:
            clip["topic_searched"] = search_topic
        result.append(clip)
    return result


def _validate_clip_candidates(
    candidates: list[dict],
    source_segments: list[dict],
    search_topic: str,
    context_coverage: str,
) -> list[dict]:
    """Accept only bounded intervals whose model evidence is literal and local."""
    validated = []
    for candidate in candidates:
        clip = dict(candidate)
        try:
            start = seconds(clip.get("start_seconds") if clip.get("start_seconds") is not None else clip["start_time"])
            end = seconds(clip.get("end_seconds") if clip.get("end_seconds") is not None else clip["end_time"])
            if end <= start or start < 0:
                continue
            if source_segments:
                last = max(seconds(segment.get("end_seconds", segment.get("end", 0))) for segment in source_segments)
                if end > last + 1:
                    continue
            if search_topic and "model_generated_by" in clip:
                evidence = str(clip.get("match_evidence", "")).strip()
                window_text = " ".join(
                    segment.get("text", "")
                    for segment in source_segments
                    if seconds(segment.get("start_seconds", segment.get("start", 0))) < end
                    and seconds(segment.get("end_seconds", segment.get("end", 0))) > start
                )
                if len(evidence) < 12 or normalize(evidence) not in normalize(window_text):
                    continue
            clip["start_seconds"], clip["end_seconds"] = start, end
            clip["duration"] = format_seconds(end - start)
            clip["context_coverage"] = context_coverage
            clip["cut_precision"] = "stream_copy_or_reencoded"
            clip["selection_mode"] = (
                clip.get("selection_method", "lexical")
                if "model_generated_by" not in clip
                else "editorial"
            )
            validated.append(clip)
        except (KeyError, TypeError, ValueError):
            continue
    return validated


def _cut_selected_clips(clips: list[dict], video_file: Path | None, clean_id: str,
                        cut_video_files: bool, *, precise: bool = False) -> list[dict]:
    """Materialize selected intervals, checking the duration of exact cuts."""
    if not cut_video_files:
        return clips
    if not video_file:
        for clip in clips:
            clip["cut_status"] = "video_no_disponible"
        return clips

    for clip in clips:
        if precise and clip.get("turn_index"):
            suffix = f"turn_{clip['turn_index']}"
        elif clip.get("topic_searched"):
            suffix = re.sub(r"[^a-zA-Z0-9]+", "_", clip["topic_searched"]).strip("_")[:20].lower()
        else:
            suffix = f"clip_{clip.get('clip_number', 1)}"
        signature = hashlib.sha256(json.dumps([
            "precise-v3" if precise else "copy-v2", str(video_file.resolve()),
            video_file.stat().st_size, video_file.stat().st_mtime_ns,
            clip["start_seconds"], clip["end_seconds"],
        ]).encode()).hexdigest()[:16]
        output_path = OUTPUT_CLIPS_DIR / f"{clean_id}_clip_{suffix}_{signature}.mp4"
        expected_duration = clip["end_seconds"] - clip["start_seconds"]

        def valid_output() -> bool:
            if not output_path.exists() or output_path.stat().st_size <= 1024:
                return False
            if not precise:
                return True
            try:
                return abs(float(probe_video(output_path)["duration"]) - expected_duration) <= 0.25
            except (OSError, subprocess.SubprocessError, KeyError, ValueError, IndexError):
                return False

        cached = valid_output()
        with stage("clip_cut"):
            cut_ok = cached or cut_clip(video_file, clip["start_seconds"],
                                        clip["end_seconds"], output_path, precise=precise)
        clip["cache_hit"] = cached
        if cut_ok and valid_output():
            clip["cut_video_file"] = str(output_path)
            clip["cut_video_size_mb"] = round(output_path.stat().st_size / (1024 * 1024), 2)
            clip["cut_status"] = "archivo_mp4_cortado_exitosamente"
        else:
            clip["cut_status"] = "error_al_cortar_o_verificar_clip"
    return clips


@measured
def media_clips(
    video_id: str = "TV01",
    query: str = "",
    topic: str = "",
    transcript_text: str = "",
    max_clips: int = 1,
    cut_video_files: bool = True,
    selection_mode: str = "auto",
    person: str = "",
    turn_index: int | None = None,
    turn_indices: list[int] | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[dict]:
    """Recorta turnos completos de una persona por índice o selecciona clips temáticos.

    Para todas las intervenciones de alguien, llama primero a media_speakers(person=...)
    y luego a media_clips(person=..., turn_index=1, max_clips=10), continuando en
    lotes. Cada turno produce su propio MP4; no uses query con el nombre ni
    media_search para enumerar turnos.

    Si se especifica 'query' o 'topic' (ej: 'motorista accidentado en Cabranes', 'precio del combustible', 'la gaita'):
    localiza evidencia textual del tema; si se pide una persona, verifica su presentación y recorta su turno de hablante.
    Si no se indica 'query', selecciona los momentos más destacados y virales del vídeo completo.

    auto intenta primero evidencia textual y usa el selector editorial solo si no la encuentra.
    fast nunca usa OpenRouter; editorial consulta el LLM salvo para turnos de persona ya verificados.
    Envía los segmentos con timestamps a Gemma 4 en OpenRouter para calcular los puntos de corte, titulares,
    ganchos y justificación. Si el vídeo existe en disco, recorta físicamente los clips a MP4 en data/outputs/clips/.

    Args:
        video_id: Identificador del vídeo ('TV01', 'TV05') o ruta al archivo de vídeo.
        query: Tema, suceso o consulta específica para buscar clips de un tema concreto (ej: 'motorista accidentado en Cabranes').
        topic: Alias de query.
        transcript_text: Transcripción opcional. Si está vacía, se toma la del vídeo o test vector.
        max_clips: Número máximo de clips a sugerir (por defecto 1). Aumentar solo si el usuario pide varias piezas.
        selection_mode: auto para fast con fallback editorial, fast solo textual o editorial con LLM. El corte se recodifica con tiempos precisos.
        cut_video_files: Si es True y el vídeo existe en disco, recorta físicamente los clips a MP4 independientes.
        person: Nombre o identificador del hablante para cortar sus turnos diarizados, sin búsqueda textual.
        turn_index: Número de intervención (desde 1). Con max_clips > 1, corta esa y las siguientes.
            Si se indica solo person, comienza en la primera intervención.
        turn_indices: Números concretos de intervenciones; permite, por ejemplo, [2, 4, 7].
        start_seconds: Inicio explícito de un corte, en segundos del vídeo.
        end_seconds: Fin explícito del corte. Debe acompañar a start_seconds.
    """
    if not 1 <= max_clips <= 10 or selection_mode not in {"auto", "fast", "editorial"}:
        return [{"status": "error", "message": "max_clips debe ser 1..10; selection_mode auto, fast o editorial."}]
    exact_time_requested = start_seconds is not None or end_seconds is not None
    turns_requested = bool(person.strip()) or turn_index is not None or turn_indices is not None
    if (exact_time_requested and turns_requested) or ((exact_time_requested or turns_requested) and (query or topic).strip()):
        return [{"status": "error", "message": "Elige una sola forma de selección: tiempos, persona/turnos o consulta textual."}]
    if exact_time_requested and (start_seconds is None or end_seconds is None):
        return [{"status": "error", "message": "Indica start_seconds y end_seconds juntos."}]
    if turns_requested and not person.strip():
        return [{"status": "error", "message": "Indica person para seleccionar intervenciones."}]
    if turn_index is not None and turn_indices is not None:
        return [{"status": "error", "message": "Usa turn_index o turn_indices, no ambos."}]
    search_topic = (query or topic).strip()
    if search_topic and not terms(search_topic):
        return [{"status": "error", "message": "Indica un tema o el nombre de una persona para localizar el clip."}]
    editorial_topic = compact_query(search_topic) if search_topic else ""
    video_file = resolve_video_file(video_id)
    clean_id = (
        re.sub(r"_demo$", "", video_file.stem, flags=re.I).upper()
        if video_file
        else video_id.strip().upper()
    )
    test_vector = load_test_vector(clean_id)
    title = (
        test_vector.get("title", f"Vídeo {clean_id}")
        if test_vector
        else f"Vídeo {video_id}"
    )

    source_segments = []
    if video_file:
        cache_file = CACHE_DIR / f"{video_file.stem}_whisperx.json"
        if cache_file.exists():
            try:
                with cache_file.open("r", encoding="utf-8") as file:
                    cached_data = json.load(file)
                source_identity = cached_data.get("request_metadata", {}).get("source")
                if str(cached_data.get("id", "")).upper() == clean_id and source_matches_file(source_identity, video_file):
                    source_segments = cached_data.get("segments", [])
            except Exception:
                pass

    if not source_segments and test_vector:
        source_identity = (test_vector.get('request_metadata') or {}).get('source')
        if not video_file or (str(test_vector.get('id', '')).upper() == clean_id and source_matches_file(source_identity, video_file)):
            source_segments = test_vector.get("segments", [])

    # Numbered turns must use the same transcript/order exposed by media_speakers.
    if turns_requested and test_vector and test_vector.get("segments"):
        source_identity = (test_vector.get("request_metadata") or {}).get("source")
        if not video_file or (str(test_vector.get("id", "")).upper() == clean_id
                              and source_matches_file(source_identity, video_file)):
            source_segments = test_vector["segments"]

    if exact_time_requested:
        try:
            start, end = float(start_seconds), float(end_seconds)
        except (TypeError, ValueError):
            return [{"status": "error", "message": "Los tiempos deben ser números en segundos."}]
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            return [{"status": "error", "message": "El intervalo debe tener inicio >= 0 y fin mayor que inicio."}]
        try:
            duration = float(probe_video(video_file)["duration"]) if video_file else max(
                float(s["end_seconds"]) for s in source_segments
            )
        except (OSError, subprocess.SubprocessError, KeyError, ValueError, StopIteration, IndexError) as exc:
            return [{"status": "error", "message": f"No se pudo verificar la duración del vídeo: {exc}"}]
        if end > duration + 0.01:
            return [{"status": "error", "message": f"El intervalo termina después del vídeo ({duration:.2f} s)."}]
        clip = {
            "status": "success", "clip_number": 1, "video_id": clean_id,
            "title": f"{clean_id} · {format_seconds(start)}–{format_seconds(end)}",
            "start_time": format_seconds(start), "end_time": format_seconds(end),
            "start_seconds": start, "end_seconds": end,
            "duration": format_seconds(end - start), "duration_seconds": round(end - start, 3),
            "selection_mode": "explicit_interval", "context_coverage": "exact_interval",
            "cut_precision": "reencoded",
        }
        return _cut_selected_clips([clip], video_file, clean_id, cut_video_files, precise=True)

    if turns_requested:
        if not source_segments:
            return [{"status": "error", "message": f"No hay transcripción diarizada válida para '{clean_id}'."}]
        turns = get_speaker_turns(clean_id, person.strip(), segments=source_segments)
        if not turns:
            return [{"status": "error", "message": f"No se encontraron intervenciones de '{person}' en '{clean_id}'."}]
        if turn_indices is not None:
            indexes = turn_indices
            if (not indexes or len(indexes) > 10
                    or any(type(index) is not int for index in indexes)
                    or len(set(indexes)) != len(indexes)):
                return [{"status": "error", "message": "turn_indices debe contener entre 1 y 10 números distintos."}]
        else:
            first = 1 if turn_index is None else turn_index
            indexes = list(range(first, min(first + max_clips, len(turns) + 1))) if type(first) is int else []
        if not indexes or any(type(index) is not int or index < 1 or index > len(turns) for index in indexes):
            return [{"status": "error", "message": f"Índice de intervención inválido; '{person}' tiene {len(turns)} intervenciones."}]
        selected = []
        for number, index in enumerate(indexes, 1):
            turn = turns[index - 1]
            start, end = float(turn["start_seconds"]), float(turn["end_seconds"])
            selected.append({
                "status": "success", "clip_number": number, "video_id": clean_id,
                "title": f"{turn['name']}: intervención {index}",
                "person": turn["name"], "speaker_id": turn["speaker_id"],
                "turn_index": index, "turns_found": len(turns),
                "start_time": turn["start"], "end_time": turn["end"],
                "start_seconds": start, "end_seconds": end,
                "duration": format_seconds(end - start), "duration_seconds": round(end - start, 3),
                "selection_mode": "speaker_turn_exact", "context_coverage": "diarized_turn",
                "cut_precision": "reencoded", "match_evidence": turn["full_text"][:240],
            })
        return _cut_selected_clips(selected, video_file, clean_id, cut_video_files, precise=True)

    if not source_segments and not transcript_text:
        return [
            {
                "status": "error",
                "message": f"No se encontraron segmentos para '{video_id}'.",
            }
        ]

    focused_candidate = None
    focused_segments = []
    selection = None
    if search_topic and source_segments:
        selection, person_intent = select_window(search_topic, source_segments)
        if person_intent and not selection:
            return [{"status": "error", "message": "No se pudo verificar el turno de esa persona en la transcripción. No se ha generado un clip. Comprueba cómo aparece su nombre o indica un intervalo."}]
        if selection:
            focused_segments = source_segments[selection['start']:selection['end'] + 1]
            first, last = focused_segments[0], focused_segments[-1]
            focused_candidate = {
                "clip_number": 1, "title": search_topic,
                "start_time": first.get("start", format_seconds(first.get("start_seconds", 0))),
                "end_time": last.get("end", format_seconds(last.get("end_seconds", 0))),
                "start_seconds": first.get("start_seconds"), "end_seconds": last.get("end_seconds"),
                "speaker": first.get("speaker", "Hablante"),
                "hook": first.get("text", ""),
                "justification": "Turno identificado mediante su presentación y el cambio de hablante." if selection['method'] == 'speaker_turn' else "Coincidencia de los términos de búsqueda en la transcripción.",
                "topic_searched": search_topic,
                "selection_method": selection['method'],
                "match_evidence": selection['evidence'],
                "matched_terms": selection['matched_terms'],
                "match_warning": selection['match_warning'],
            }

        # Fast mode is deliberately deterministic: a semantic guess belongs to
        # editorial mode and must never happen silently through an LLM fallback.
        if selection_mode == "fast" and not focused_candidate:
            return [{
                "status": "error",
                "message": (
                    "El modo fast no encontró una coincidencia textual verificable. "
                    "Prueba selection_mode='editorial' para una búsqueda semántica."
                ),
            }]

    if focused_candidate and focused_segments:
        context_segments = focused_segments
    else:
        context_segments = (
            source_segments
        )

    segments_context = ""
    if context_segments:
        for segment in context_segments:
            segments_context += (
                f"[{segment.get('start', '')} -> {segment.get('end', '')}] "
                f"({segment.get('speaker', '')}): {segment.get('text', '')}\n"
            )
    elif transcript_text:
        segments_context = transcript_text

    # Bound editorial prompt size while covering the complete timeline.
    context_coverage = "full" if context_segments == source_segments else "focused"
    if len(segments_context) > 36000 and context_segments:
        block_size = max(1, math.ceil(len(context_segments) / 150))
        lines = []
        for offset in range(0, len(context_segments), block_size):
            block = context_segments[offset:offset + block_size]
            excerpt = " ".join(s.get("text", "") for s in block)
            if len(excerpt) > 160:
                excerpt = excerpt[:80] + " […] " + excerpt[-80:]
            lines.append(f"[{block[0].get('start')} -> {block[-1].get('end')}] {excerpt}")
        segments_context = "Vista abreviada de todos los bloques temporales; cortes aproximados.\n" + "\n".join(lines)
        context_coverage = "abbreviated_timeline"

    clips_result = []
    use_editorial_selector = (
        selection_mode == "editorial"
        or (selection_mode == "auto" and not focused_candidate)
        or not search_topic
    )
    if get_openrouter_key() and segments_context and use_editorial_selector and not (
        focused_candidate and selection and selection["method"] == "speaker_turn"
    ):
        if search_topic:
            system_prompt = (
                "Eres un realizador y editor audiovisual para telediarios y redes sociales. "
                f"El usuario busca específicamente clips sobre: '{editorial_topic}'. "
                "Localiza en la transcripción con marcas de tiempo el momento exacto que aborda este tema, "
                "ajustando el inicio y el final a los segmentos relevantes. El clip debe ser conciso y centrado en la noticia o declaración concreta (duración típica entre 30 y 90 segundos, máximo 2 minutos; evita agrupar noticias contiguas diferentes). Si el tema o la persona no aparecen, devuelve clips vacíos; nunca un destacado genérico. Incluye match_evidence con una cita literal del fragmento que demuestra la relación con la consulta. "
                "Debes responder ÚNICAMENTE con un JSON con la clave 'clips': lista de objetos con:\n"
                "- clip_number: int\n- title: str\n- start_time: str (00:MM:SS)\n- end_time: str (00:MM:SS)\n"
                "- duration: str\n- speaker: str\n- hook: str\n- justification: str\n"
                "- match_evidence: str (cita literal exacta copiada de los segmentos del intervalo)\n"
                "Ejemplo mínimo: {\"clips\":[{\"start_time\":\"00:00:10\",\"end_time\":\"00:00:20\",\"match_evidence\":\"cita literal exacta\"}]}\n"
            )
            user_prompt = (
                f"Vídeo: {title}\n"
                f"Tema buscado: {editorial_topic}\n\n"
                f"Segmentos con tiempos:\n{segments_context}\n\n"
                "Genera el mejor clip para este tema en formato JSON con la clave 'clips'."
            )
        else:
            system_prompt = (
                "Eres un realizador y editor de vídeo profesional para televisión y redes sociales (YouTube Shorts, TikTok, LinkedIn). "
                "A partir de los segmentos con marcas de tiempo (inicio y fin) facilitados, identifica los mejores clips destacados. "
                "Debes responder ÚNICAMENTE con un objeto JSON que contenga la clave 'clips', que es una lista de objetos con:\n"
                "- clip_number: int (1, 2, ...)\n- title: str\n- start_time: str (Formato 00:MM:SS exacto)\n"
                "- end_time: str (Formato 00:MM:SS exacto)\n- duration: str\n- speaker: str\n- hook: str\n- justification: str\n"
            )
            user_prompt = (
                f"Vídeo: {title}\n"
                f"Selecciona como máximo {max_clips} clips.\n\n"
                f"Segmentos con tiempos:\n{segments_context}\n\n"
                "Devuelve los clips en formato JSON con la clave 'clips'."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        attempts = 2 if search_topic and selection_mode in {"auto", "editorial"} else 1
        for attempt in range(attempts):
            raw_response = call_openrouter(
                messages, model=DEFAULT_LLM_MODEL, json_mode=True, max_tokens=min(3500, 400 * max_clips + 300)
            )
            try:
                candidates = _parse_model_clips(raw_response, max_clips, search_topic)
            except Exception as error:
                print(f"[Clips Parse Error] {error}", file=sys.stderr)
                candidates = []
            if _validate_clip_candidates(candidates, source_segments, search_topic, context_coverage):
                clips_result = candidates
                break
            if attempt + 1 < attempts:
                # One bounded repair attempt fixes schema omissions/paraphrases
                # without weakening the evidence gate or creating an agent loop.
                messages.extend([
                    {"role": "assistant", "content": raw_response or "{}"},
                    {"role": "user", "content": (
                        "La respuesta anterior no es válida: falta una match_evidence literal o no pertenece al intervalo. "
                        "Devuelve de nuevo el JSON completo y copia match_evidence palabra por palabra de los segmentos comprendidos entre start_time y end_time."
                    )},
                ])

    if not clips_result and focused_candidate:
        clips_result = [focused_candidate]

    if not clips_result:
        return [{"status": "error", "message": "No se encontraron clips verificables; prueba otra consulta o configura OpenRouter."}]
    clips_result = _validate_clip_candidates(clips_result, source_segments, search_topic, context_coverage)
    if not clips_result:
        return [{"status": "error", "message": "El selector no devolvió un intervalo con evidencia verificable. No se ha generado un clip."}]

    return _cut_selected_clips(clips_result, video_file, clean_id, cut_video_files)
