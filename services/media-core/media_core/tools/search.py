"""MCP semantic-search tool with ChromaDB and lexical fallback."""

import json
import re
import sys
from pathlib import Path
from media_core.services.clip_selection import terms, matched_terms, select_window
from media_core.runtime import measured

from media_core.formatting import format_seconds
from media_core.indexing import search_vector_index
from media_core.storage import get_all_test_vectors, resolve_video_file


def _query_terms(query: str) -> list[str]:
    return terms(query)


def _matched_terms(query_terms: list[str], text: str) -> list[str]:
    return sorted(matched_terms(query_terms, text))


def _vector_results(query: str, query_terms: list[str], top_k: int, video_filter: str = "") -> list[dict]:
    records = search_vector_index(query, top_k, video_id=video_filter) if video_filter else search_vector_index(query, top_k)
    results = []
    for record in records:
        metadata = record.get("metadata", {})
        document = record.get("document", "")
        video_id = str(metadata.get("video_id", "TV00"))
        if video_filter and video_id != video_filter:
            continue
        video_file = resolve_video_file(video_id)
        try:
            speakers = json.loads(str(metadata.get("speakers", "[]")))
        except json.JSONDecodeError:
            speakers = []
        start_seconds = float(metadata.get("start_seconds", 0.0))
        end_seconds = float(metadata.get("end_seconds", start_seconds))
        semantic_score = round(
            max(0.0, min(1.0, 1.0 - float(record.get("distance", 1.0)))),
            4,
        )
        # Similarity is a ranking signal, never proof that a requested fact exists.
        if semantic_score < 0.35:
            continue
        results.append(
            {
                "video_id": video_id,
                "video_title": metadata.get("video_title", f"Vídeo {video_id}"),
                "video_file_available": str(video_file) if video_file else None,
                "start_time": metadata.get("start_time", "00:00:00"),
                "end_time": metadata.get("end_time", "00:00:00"),
                "duration": format_seconds(max(0.0, end_seconds - start_seconds)),
                "speakers": speakers,
                "matched_terms": _matched_terms(query_terms, document),
                "relevance_score": round(semantic_score * 100, 2),
                "semantic_score": semantic_score,
                "key_excerpt": metadata.get("key_excerpt", document[:240]),
                "full_context": document,
                "search_engine": "chromadb",
                "retrieval_mode": record.get("retrieval_mode", "hnsw"),
                "match_type": "semantic_candidate",
                "suggested_clip_call": (
                    f"media_clips(video_id='{video_id}', query='{query}')"
                ),
            }
        )
    return results


def _lexical_search(query: str, query_terms: list[str], top_k: int, video_filter: str = "") -> list[dict]:
    results = []
    for vector in get_all_test_vectors():
        video_id = vector.get("id", "")
        if video_filter and video_id != video_filter:
            continue
        segments = vector.get("segments", [])
        selection, _ = select_window(query, segments)
        if not selection:
            continue
        block = segments[selection['start']:selection['end']+1]
        first, last = block[0], block[-1]
        video_file = resolve_video_file(video_id)
        results.append({
            "video_id": video_id, "video_title": vector.get("title", video_id),
            "video_file_available": str(video_file) if video_file else None,
            "start_time": first.get("start"), "end_time": last.get("end"),
            "duration": format_seconds(last.get("end_seconds", 0) - first.get("start_seconds", 0)),
            "speakers": sorted({s.get("speaker", "Hablante") for s in block}),
            "matched_terms": sorted(selection['matched_terms']),
            "relevance_score": len(selection['matched_terms']), "semantic_score": None,
            "key_excerpt": selection['evidence'],
            "full_context": " ".join(s.get("text", "") for s in block),
            "search_engine": "transcript_speaker_turn" if selection['method'] == 'speaker_turn' else "lexical_fallback",
            "match_warning": selection['match_warning'],
            "suggested_clip_call": f"media_clips(video_id={video_id!r}, query={query!r})",
        })
    results.sort(key=lambda r: r['relevance_score'], reverse=True)
    return results[:top_k]


def _ocr_search(query: str, query_terms: list[str], top_k: int, video_filter: str = "") -> list[dict]:
    """Search on-screen text and chyrons extracted from video keyframes."""
    from media_core.config import OCR_CACHE_DIR
    if not OCR_CACHE_DIR.exists():
        return []

    results = []
    norm_query = query.strip().casefold()
    clean_terms = [t.casefold() for t in query_terms if len(t) >= 3]

    cache_files = (
        [OCR_CACHE_DIR / f"{video_filter}_ocr.json"]
        if video_filter
        else list(OCR_CACHE_DIR.glob("*_ocr.json"))
    )

    for cpath in cache_files:
        if not cpath.is_file():
            continue
        try:
            data = json.loads(cpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        vid = data.get("video_id", cpath.stem.replace("_ocr", ""))
        extracted = data.get("extracted_texts", [])
        scenes_map = {s.get("scene_id"): s for s in data.get("scenes", [])}
        video_file = resolve_video_file(vid)

        seen_scenes = set()
        for item in extracted:
            text = item.get("text", "")
            text_norm = text.casefold()
            sid = item.get("scene_id", 0)

            direct_match = norm_query in text_norm
            matched = [t for t in clean_terms if t in text_norm]

            if direct_match or matched:
                if sid in seen_scenes:
                    continue
                seen_scenes.add(sid)

                ts = float(item.get("timestamp", 0.0))
                score = 100.0 if direct_match else min(95.0, len(matched) * 30.0 + (item.get("confidence", 0.8) * 10.0))
                reg = item.get("region", "lower_third")

                scene_info = scenes_map.get(sid, {})
                scene_all_texts = scene_info.get("on_screen_text") or [text]
                scene_lower_thirds = scene_info.get("lower_thirds") or []

                speaker_labels = [f"Rótulo: {lt}" for lt in scene_lower_thirds] if scene_lower_thirds else [f"Rótulo: {text}"]
                excerpt_str = " | ".join(scene_all_texts) if scene_all_texts else text

                results.append({
                    "video_id": vid,
                    "video_title": f"Vídeo {vid}",
                    "video_file_available": str(video_file) if video_file else None,
                    "start_time": format_seconds(ts),
                    "end_time": format_seconds(ts + 5.0),
                    "duration": "00:00:05",
                    "speakers": speaker_labels,
                    "matched_terms": [norm_query] if direct_match else matched,
                    "relevance_score": round(score, 2),
                    "semantic_score": round(score / 100.0, 4),
                    "key_excerpt": f"[Faldón/Rótulo] {excerpt_str}",
                    "full_context": f"Rótulos visibles en pantalla a los {format_seconds(ts)} (Escena {sid}): " + ", ".join(scene_all_texts) + (". Faldones detectados: " + ", ".join(scene_lower_thirds) if scene_lower_thirds else "") + ".",
                    "search_engine": "visual_ocr",
                    "match_type": "visual_on_screen_text",
                    "keyframe": item.get("keyframe"),
                    "suggested_clip_call": f"media_clips(video_id={vid!r}, start={ts}, duration=5.0)",
                })

    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results[:top_k]


@measured
def media_search(query: str, top_k: int = 3, video_id: str = "") -> list[dict]:
    """Busca por significado en las transcripciones indexadas en ChromaDB y texto en pantalla (OCR).

    Permite encontrar en qué vídeo, minuto y segundo se trató un tema específico.
    Devuelve fragmentos con timestamps, oradores y contexto. Si el índice vectorial no
    está disponible, conserva el buscador léxico anterior como respaldo.

    Args:
        query: Consulta o pregunta en lenguaje natural (ej: 'motorista accidentado en Cabranes', 'presupuesto plataforma MedIA', 'precio del combustible').
        video_id: ID del vídeo (TV04) para limitar la búsqueda; vacío busca en todo el archivo.
        top_k: Número máximo de resultados a devolver (por defecto 3).
    """
    video_filter = Path(video_id.strip()).stem.upper()
    if video_filter.endswith("_DEMO"):
        video_filter = video_filter[:-5]
    query_terms = _query_terms(query)

    if not query_terms:
        return [
            {
                "status": "invalid_query",
                "message": "La consulta de búsqueda debe contener al menos una palabra clave relevante.",
            }
        ]

    safe_top_k = max(1, min(int(top_k), 20))
    grounded = _lexical_search(query, query_terms, safe_top_k, video_filter)
    _, person_intent = select_window(query, [])
    if person_intent or any(r['search_engine'] == 'transcript_speaker_turn' for r in grounded):
        return grounded or [{"status": "no_results", "query": query,
            "message": "No se pudo verificar el turno de esa persona en la transcripción."}]
    try:
        results = _vector_results(query, query_terms, safe_top_k, video_filter)
    except Exception as error:
        print(f"[Chroma Search Warning] {error}", file=sys.stderr)
        if not grounded:
            return [{"status": "search_unavailable", "query": query,
                "message": "La búsqueda semántica no está disponible y no hay coincidencias textuales verificadas. No se puede concluir que el tema no exista."}]
        results = []

    if not results:
        results = grounded

    ocr_results = _ocr_search(query, query_terms, safe_top_k, video_filter)
    if ocr_results:
        if not results:
            results = ocr_results
        else:
            results = (results + ocr_results)[:safe_top_k]

    try:
        from media_core.services.visual_embeddings import search_visual_index
        visual_results = search_visual_index(query, top_k=safe_top_k, video_id=video_filter, min_score=0.23)
        if visual_results:
            if not results:
                results = visual_results
            else:
                results = (results + visual_results)[:safe_top_k]
    except Exception:
        pass

    if not results:
        return [
            {
                "status": "no_results",
                "query": query,
                "search_engine": "chromadb_with_lexical_fallback",
                "message": f"No se encontraron fragmentos que coincidan con '{query}'. Pruebe con términos como: motorista, Cabranes, combustible, gaita, presupuesto, llengües cooficials.",
            }
        ]

    return results
