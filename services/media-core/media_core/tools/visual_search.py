"""MCP tools for visual concept search and visual vector indexation."""

import json
import re
from pathlib import Path

from media_core.config import KEYFRAMES_DIR, VISUAL_CACHE_DIR
from media_core.runtime import measured, progress
from media_core.services.visual_embeddings import index_keyframes_visual, search_visual_index
from media_core.storage import resolve_video_file
from media_core.tools.analyze_video import media_analyze_video


@measured
def media_index_visual(
    video_id: str = "TV01",
    force: bool = False,
) -> dict:
    """Genera embeddings visuales (CLIP) para los fotogramas clave del vídeo y los indexa en ChromaDB.

    Args:
        video_id: Identificador del vídeo ('TV01', 'TV05', etc.) o ruta al archivo.
        force: Si es True, fuerza la regeneración del índice ignorando la caché.
    """
    video_file = resolve_video_file(video_id)
    if not video_file or not video_file.is_file():
        return {
            "status": "error",
            "message": f"No se encontró el archivo de vídeo para '{video_id}'.",
        }

    stem = video_file.stem
    clean_id = re.sub(r"_demo$", "", stem, flags=re.I).upper()
    keyframes_dir = KEYFRAMES_DIR / clean_id
    visual_cache_path = VISUAL_CACHE_DIR / f"{clean_id}_visual.json"

    # Ensure prerequisite keyframes exist
    if not visual_cache_path.exists() or not keyframes_dir.exists():
        progress("generating_visual_prerequisites", video_id=clean_id)
        visual_manifest = media_analyze_video(video_id=video_id, force=False)
    else:
        try:
            visual_manifest = json.loads(visual_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            visual_manifest = media_analyze_video(video_id=video_id, force=False)

    return index_keyframes_visual(
        video_id=clean_id,
        visual_manifest=visual_manifest,
        keyframes_dir=keyframes_dir,
        force=force,
    )


@measured
def media_search_visual(
    query: str,
    top_k: int = 3,
    video_id: str = "",
) -> list[dict]:
    """Busca escenas de vídeo basándose en conceptos visuales (objetos, entornos, acciones o escenarios).

    Utiliza embeddings multimodales (CLIP) para comparar semánticamente la descripción textual
    con el contenido visual de los fotogramas clave.

    Args:
        query: Concepto visual en lenguaje natural (ej: 'gasolinera', 'coche de policía', 'presentador en plató').
        top_k: Número máximo de resultados a devolver (por defecto 3).
        video_id: ID opcional de vídeo para acotar la búsqueda.
    """
    clean_query = query.strip()
    if not clean_query:
        return [
            {
                "status": "invalid_query",
                "message": "Introduce un concepto visual para buscar (ej: 'gasolinera', 'coche de policía').",
            }
        ]

    results = search_visual_index(query=clean_query, top_k=top_k, video_id=video_id)
    if not results:
        return [
            {
                "status": "no_results",
                "query": clean_query,
                "search_engine": "multimodal_clip",
                "message": f"No se encontraron escenas que coincidan visualmente con '{clean_query}'. Asegúrate de haber indexado los vídeos con media_index_visual.",
            }
        ]
    return results
