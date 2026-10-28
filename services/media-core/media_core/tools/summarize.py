"""MCP summarization tool."""

import json
import sys
import re
from pathlib import Path
from media_core.runtime import measured, stage

from media_core.config import CACHE_DIR, DEFAULT_LLM_MODEL, get_openrouter_key
from media_core.services.openrouter import call_openrouter
from media_core.storage import load_test_vector, resolve_video_file, source_matches_file


@measured
def media_summarize(
    video_id: str = "TV01",
    transcript_text: str = "",
    style: str = "ejecutivo",
    query: str = "",
    language: str = "es",
) -> dict:
    """Genera un resumen estructurado y puntos clave a partir de un vídeo o transcripción.

    Utiliza el modelo Gemma 4 31B (1º de la lista de selección de MedIA) a través de OpenRouter.

    Args:
        video_id: Identificador del vídeo ('TV01', 'TV05') o ruta al archivo de vídeo.
        transcript_text: Texto opcional a resumir. Si está vacío, se toma la transcripción de video_id.
        style: Estilo del resumen ('breve': 2 frases, 'ejecutivo', 'detallado', 'redes'). Por defecto 'ejecutivo'.
        query: Consulta opcional que indica el tema o pregunta que debe priorizar el resumen.
    """
    from media_core.services.tts import normalize_language
    output_language = normalize_language(language)
    if output_language not in {"Spanish", "Catalan", "English"}:
        return {"status": "error", "message": "Indica el idioma del resumen: es, ca o en."}
    video_file = resolve_video_file(video_id)
    stem = video_file.stem if video_file else Path(video_id.strip()).stem
    clean_id = re.sub(r"_demo$", "", stem, flags=re.I).upper()
    test_vector = load_test_vector(clean_id) if not transcript_text.strip() else None

    text_to_summarize = transcript_text.strip()
    title = (
        test_vector.get("title", f"Vídeo {clean_id}")
        if test_vector
        else f"Vídeo {video_id}"
    )

    if not text_to_summarize and video_file:
        cache_file = CACHE_DIR / f"{video_file.stem}_whisperx.json"
        if cache_file.exists():
            try:
                with cache_file.open("r", encoding="utf-8") as file:
                    cached_data = json.load(file)
                source_identity = (cached_data.get('request_metadata') or {}).get('source')
                if str(cached_data.get('id', '')).upper() == clean_id and source_matches_file(source_identity, video_file):
                    text_to_summarize = cached_data.get("full_text", "")
                    title = cached_data.get('title', title)
            except Exception:
                pass

    if not text_to_summarize and test_vector:
        source_identity = (test_vector.get("request_metadata") or {}).get("source")
        if not video_file or (str(test_vector.get("id", "")).upper() == clean_id and source_matches_file(source_identity, video_file)):
            text_to_summarize = test_vector.get("full_text", "") or " ".join(
                segment["text"] for segment in test_vector.get("segments", [])
            )
            title = test_vector.get("title", title)

    if not text_to_summarize:
        return {
            "status": "error",
            "message": "No hay texto disponible para resumir. Indique un video_id válido (TV01, TV05) o pase transcript_text.",
        }

    failure_code = "missing_api_key"
    if get_openrouter_key():
        clean_query = query.strip()
        focus_instruction = (
            f"Consulta o foco solicitado: {clean_query}\n"
            "Prioriza en el resumen la información que responda a esta consulta, "
            "sin inventar datos que no aparezcan en la transcripción.\n\n"
            if clean_query
            else ""
        )
        system_prompt = (
            "Eres un periodista y redactor audiovisual experto en el proyecto MedIA. "
            "Tu tarea es analizar la transcripción suministrada y generar un resumen de alta calidad. "
            f"Escribe todos los valores de texto en {output_language}. "
            "Debes responder en formato JSON estricto con las claves: "
            "'executive_summary' (texto fluido), 'key_points' (lista de 3 a 5 puntos esenciales), "
            "y 'main_topics' (lista de temas clave). "
            "Limita el resumen a 350 palabras, cada punto a 40 palabras y los temas a 8. "
            "Escapa las comillas dentro de los textos y cierra todas las listas y el objeto JSON."
        )
        user_prompt = (
            f"Título del vídeo: {title}\n"
            f"Estilo requerido: {style}\n"
            f"{focus_instruction}"
            f"Transcripción:\n{text_to_summarize}\n\n"
            "Devuelve tu análisis en formato JSON."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        brief = style.strip().lower() in {"breve", "short", "very short"}
        text_prompt = (
            f"Resume únicamente la transcripción suministrada en {output_language}. "
            "Devuelve solo un párrafo de texto natural, sin JSON ni Markdown. "
            + ("Máximo dos frases y 60 palabras. " if brief else "Máximo 200 palabras. ")
            + "Describe de qué trata en general, sin inventar hechos ni convertir opiniones de los hablantes en hechos."
        )
        for attempt in range(2):
            plain_mode = brief or attempt > 0
            request_messages = ([{"role": "system", "content": text_prompt},
                {"role": "user", "content": f"Título: {title}\n{focus_instruction}Transcripción:\n{text_to_summarize}"}]
                if plain_mode else messages)
            raw_response = call_openrouter(
                request_messages, model=DEFAULT_LLM_MODEL, json_mode=not plain_mode,
                temperature=0.1, max_tokens=(500 if brief else 1200) if plain_mode else 2400,
            )
            if not raw_response:
                failure_code = "upstream_unavailable"
                break
            try:
                clean_json = raw_response.strip()
                if clean_json.startswith("```"):
                    clean_json = clean_json.split("\n", 1)[-1].rsplit("```", 1)[0]
                response_format = "json"
                if plain_mode and clean_json and not any(marker in clean_json for marker in ('{', '}', '[', ']', '```')):
                    parsed = {"executive_summary": clean_json, "key_points": [], "main_topics": []}
                    response_format = "text"
                else:
                    parsed = json.loads(clean_json.strip())
                if not isinstance(parsed, dict) or not isinstance(parsed.get("executive_summary"), str) or not parsed["executive_summary"].strip():
                    raise ValueError("Resumen vacío o respuesta inválida")
                for field in ("key_points", "main_topics"):
                    if not isinstance(parsed.get(field), list) or not all(isinstance(item, str) for item in parsed[field]):
                        raise ValueError(f"{field} debe ser una lista de textos")
                return {
                    "status": "success",
                    "model_used": f"{DEFAULT_LLM_MODEL} (OpenRouter)",
                    "video_id": video_id,
                    "title": title,
                    "style": style,
                    "format_retry": attempt > 0,
                    "response_format": response_format,
                    "executive_summary": parsed.get("executive_summary", ""),
                    "language": language,
                    "key_points": parsed.get("key_points", []),
                    "main_topics": parsed.get("main_topics", []),
                }
            except (ValueError, TypeError) as error:
                failure_code = "invalid_summary_json"
                print(f"[Summary JSON Warning] attempt={attempt + 1} model={DEFAULT_LLM_MODEL}: {error}", file=sys.stderr)
                # Retry as plain prose from the original transcript; the server builds the response JSON.
                messages = [*messages, {"role": "user", "content":
                    "La respuesta anterior no cumplió el formato JSON requerido. Genera de nuevo "
                    "el resumen desde la transcripción original: un objeto JSON completo, sin Markdown, "
                    "con executive_summary como texto y key_points y main_topics como listas de textos. "
                    "Usa frases breves y evita comillas dentro de los valores."}]

    reasons = {
        "missing_api_key": "Falta configurar OPENROUTER_API_KEY.",
        "upstream_unavailable": "OpenRouter no devolvió una respuesta utilizable; consulta el error del servicio en el log.",
        "invalid_summary_json": "El modelo devolvió una respuesta inutilizable incluso después del reintento en texto.",
    }
    return {"status": "error", "error_code": failure_code,
            "message": reasons[failure_code] + " No se devuelve texto de ejemplo."}
