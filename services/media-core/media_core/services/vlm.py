"""Targeted Vision-Language Model (VLM) service for scene and frame interpretation."""

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from media_core.runtime import stage
from media_core.services.openrouter import call_openrouter

logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = os.getenv("OPENROUTER_VISION_MODEL", "google/gemma-4-31b-it")


def encode_image_base64(image_path: Path) -> str:
    """Encode an image file to base64 ASCII string."""
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def describe_keyframe_vlm(
    image_path: Path | None = None,
    image_paths: list[Path] | None = None,
    video_id: str = "",
    timestamp: float = 0.0,
    user_prompt: str = "",
    model: str | None = None,
    context_metadata: dict | None = None,
    target_person: str = "",
    action_query: str = "",
) -> dict:
    """Analyze video keyframes (single or temporal multi-frame sequence) using a multimodal VLM via OpenRouter.

    Returns structured JSON with description, person identification, observed action, setting, subjects, and text.
    """
    paths: list[Path] = []
    if image_paths:
        paths = [p for p in image_paths if p and p.exists()]
    elif image_path and image_path.exists():
        paths = [image_path]

    if not paths:
        missing = str(image_path) if image_path else "fotogramas no especificados"
        return {
            "status": "error",
            "message": f"No se encontró el fotograma en '{missing}'.",
        }

    target_model = model or DEFAULT_VISION_MODEL

    system_instruction = (
        "Eres el analista de visión artificial y realizador audiovisual de la plataforma MedIA. "
        "Analiza el fotograma o secuencia temporal de fotogramas del vídeo con rigor periodístico y técnico. "
        "Si se aportan varios fotogramas, corresponden a una secuencia temporal consecutiva en torno al momento consultado: "
        "compara la progresión para deducir con exactitud el movimiento, lenguaje corporal, expresión y acción en curso. "
        "Devuelve exclusivamente un objeto JSON con las siguientes claves: "
        "- 'description': resumen narrativo conciso de lo que muestra la imagen o secuencia. "
        "- 'person_detected': booleano (true si la persona buscada o visible está en pantalla, false si no aparece). "
        "- 'person_role_identified': nombre, cargo o rol de la persona observada (deducido por rótulos en pantalla o contexto). "
        "- 'action_observed': descripción detallada de la acción o acontecimiento concreto en curso (gestos, movimientos, postura, interacción). "
        "- 'setting': tipo de plano y escenario (ej. 'Plano medio en plató', 'Exterior conexión directa en calle', 'Hemiciclo parlamentario'). "
        "- 'subjects': lista de personas, entidades u objetos prominentes. "
        "- 'actions': lista de acciones o acontecimientos observables. "
        "- 'visible_elements': lista de elementos visuales destacados (marcas, logotipos, vehículos, gráficos). "
        "- 'visible_text': lista de rótulos o textos legibles en pantalla."
    )

    prompt_parts = []
    if target_person:
        prompt_parts.append(f"Foco de atención: Observa e identifica específicamente a la persona o sujeto '{target_person}'.")
    if action_query:
        prompt_parts.append(f"Pregunta sobre la acción: {action_query}")
    if user_prompt:
        prompt_parts.append(user_prompt)
    if not prompt_parts:
        prompt_parts.append(f"Describe detalladamente qué ocurre en este momento de {video_id} a los {timestamp:.1f} segundos.")

    if context_metadata:
        context_lines = []
        if context_metadata.get("ocr_texts"):
            context_lines.append(f"Rótulos/textos detectados en pantalla en este intervalo: {', '.join(context_metadata['ocr_texts'])}")
        if context_metadata.get("speaker"):
            context_lines.append(f"Hablante/diarización activa en este intervalo: {context_metadata['speaker']}")
        if context_metadata.get("transcript_excerpt"):
            context_lines.append(f"Transcripción de audio en torno a este momento: \"{context_metadata['transcript_excerpt']}\"")
        if context_lines:
            prompt_parts.append("Contexto multimodal del momento:\n" + "\n".join(f"- {line}" for line in context_lines))

    query_text = "\n\n".join(prompt_parts)

    user_content: list[dict[str, Any]] = [{"type": "text", "text": query_text}]
    is_multi = len(paths) > 1
    for idx, path in enumerate(paths):
        b64_data = encode_image_base64(path)
        label = f"Fotograma {idx + 1} de {len(paths)}" if is_multi else "Fotograma"
        user_content.append({"type": "text", "text": f"[{label} - {path.name}]:"})
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
        })

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_content},
    ]

    with stage("vlm_scene_interpretation"):
        raw_response = call_openrouter(
            messages=messages,
            model=target_model,
            json_mode=True,
            temperature=0.2,
            max_tokens=900,
        )

    fallback_base = {
        "video_id": video_id,
        "timestamp": timestamp,
        "model_used": target_model,
        "image_file": paths[0].name,
        "image_files": [p.name for p in paths],
        "person_detected": bool(target_person),
        "person_role_identified": target_person,
        "action_observed": "",
        "setting": "",
        "subjects": [target_person] if target_person else [],
        "actions": [],
        "visible_elements": [],
        "visible_text": [],
    }

    if not raw_response:
        res = dict(fallback_base)
        res["status"] = "unavailable"
        res["description"] = "El servicio de VLM no está disponible (comprueba OPENROUTER_API_KEY)."
        return res

    try:
        parsed = json.loads(raw_response)
        parsed["status"] = "success"
        parsed["video_id"] = video_id
        parsed["timestamp"] = timestamp
        parsed["model_used"] = target_model
        parsed["image_file"] = paths[len(paths) // 2].name
        parsed["image_files"] = [p.name for p in paths]
        if "person_detected" not in parsed:
            parsed["person_detected"] = bool(target_person)
        if "action_observed" not in parsed:
            parsed["action_observed"] = ", ".join(parsed.get("actions", [])) or parsed.get("description", "")
        return parsed
    except json.JSONDecodeError:
        res = dict(fallback_base)
        res["status"] = "success"
        res["description"] = raw_response.strip()
        res["action_observed"] = raw_response.strip()
        return res

