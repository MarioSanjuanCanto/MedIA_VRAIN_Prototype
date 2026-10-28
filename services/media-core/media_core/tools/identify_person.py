"""MCP tool for identifying unknown persons via web search, open-source context, and side-by-side comparison."""

from __future__ import annotations

import re
from pathlib import Path

from media_core.config import KEYFRAMES_DIR
from media_core.runtime import measured
from media_core.services.web_identification import identify_person_via_web
from media_core.tools.image_crop import media_crop_subject


@measured
def media_identify_person_web(
    video_id: str = "TV04",
    timestamp: float = 0.0,
    person_description: str = "",
    crop_path: str = "",
    context_hint: str = "",
    create_comparison: bool = True,
) -> dict:
    """Identifica a una persona desconocida en un fotograma buscando por internet y analizando el contexto político/parlamentario.

    Permite responder preguntas como:
    - '¿Quién es la mujer musulmana que hay detrás? ¿La puedes identificar buscando por internet?'
    - 'Identifícame el diputado que está a la derecha sin gafas en la bancada.'
    
    Flujo automático:
    1. Si no se aporta un recorte, segmenta y recorta automáticamente a la persona indicada en el fotograma.
    2. Cruza el orador del momento y su grupo parlamentario/bancada con los rasgos físicos.
    3. Realiza búsqueda web (DuckDuckGo, Wikipedia, fuentes parlamentarias) para hallar la persona.
    4. Descarga la fotografía oficial pública de referencia (Wikimedia/Wikipedia).
    5. Genera una imagen comparativa lado a lado (foto oficial a la izquierda, recorte a la derecha).

    Args:
        video_id: Identificador del vídeo ('TV04', etc.).
        timestamp: Segundo exacto del vídeo a analizar (ej: 687.0 para 11:27, 1234.0 para 20:34).
        person_description: Rasgos físicos o posición de la persona (ej: 'mujer musulmana con hiyab detrás del orador', 'diputado a la derecha sin gafas').
        crop_path: Ruta opcional a un recorte previo si ya se ejecutó media_crop_subject.
        context_hint: Pistas contextuales adicionales (ej: 'bancada de ERC', 'bancada de VOX').
        create_comparison: Si es True, genera una composición comparativa lado a lado con la foto oficial.
    """
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", video_id).upper()
    keyframes_dir = KEYFRAMES_DIR / clean_id

    resolved_crop_file: Path | None = None
    crop_info = None

    if crop_path:
        cand = Path(crop_path)
        if cand.is_file():
            resolved_crop_file = cand
        elif (KEYFRAMES_DIR / cand).is_file():
            resolved_crop_file = KEYFRAMES_DIR / cand
        elif (keyframes_dir / cand.name).is_file():
            resolved_crop_file = keyframes_dir / cand.name

    # If no crop path was passed, automatically crop using media_crop_subject
    if not resolved_crop_file:
        crop_res = media_crop_subject(
            video_id=clean_id,
            timestamp=timestamp,
            target_description=person_description,
        )
        if crop_res.get("status") == "success" and crop_res.get("absolute_crop_path"):
            resolved_crop_file = Path(crop_res["absolute_crop_path"])
            crop_info = crop_res
        else:
            return {
                "status": "error",
                "message": f"No se pudo recortar al sujeto para investigarlo: {crop_res.get('message')}",
            }

    # Perform web research and identity resolution
    result = identify_person_via_web(
        video_id=clean_id,
        timestamp=timestamp,
        person_description=person_description,
        crop_path=resolved_crop_file,
        context_hint=context_hint,
        create_comparison=create_comparison,
    )

    if crop_info:
        result["crop_details"] = crop_info

    return result
