"""MCP facade for progressive multimodal indexing and hybrid search."""

import re

from media_core.formatting import format_seconds
from media_core.runtime import measured
from media_core.services import multimodal
from media_core.tools.search import media_search
from media_core.tools.visual_search import media_search_visual


@measured
def media_index_multimodal(video_id: str, max_items: int = 4, retry_failed: bool = False) -> dict:
    """Indexa hasta max_items piezas con EmbeddingGemma 2; repite hasta completed.

    Pensada para un trabajador de baja prioridad. El manifiesto y los vectores
    persisten tras reinicios; la transcripción se puede consultar mientras tanto.
    """
    try:
        return multimodal.index_next(video_id, max_items, retry_failed)
    except Exception as error:
        return {"status": "error", "video_id": video_id, "message": str(error)[:500]}


@measured
def media_multimodal_index_status(video_id: str) -> dict:
    """Devuelve progreso y cobertura del índice EmbeddingGemma 2 de un vídeo."""
    return multimodal.index_status(video_id)


def _timed(result: dict) -> dict | None:
    try:
        start = result.get("start_seconds")
        end = result.get("end_seconds")
        if start is None:
            start = _seconds(result.get("start_time", ""))
        if end is None:
            end = _seconds(result.get("end_time", ""))
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not result.get("video_id") or end < start:
        return None
    return {**result, "start_seconds": start, "end_seconds": end,
            "start_time": format_seconds(start), "end_time": format_seconds(end),
            "evidence_status": result.get("evidence_status", "semantic_candidate"),
            "modality": result.get("modality") or (
                "image" if result.get("search_engine") in {"multimodal_clip", "visual_ocr"}
                else "text")}


def _seconds(value: str) -> float:
    parts = str(value).split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Tiempo no válido")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


@measured
def media_search_multimodal(query: str = "", video_id: str = "",
                            query_media: str = "", top_k: int = 5) -> dict:
    """Recupera candidatos por texto o archivo de referencia (imagen/audio/vídeo).

    Los resultados son candidatos, no confirmaciones. Para acciones y detalles
    temporales, Hermes debe verificar el tramo antes de responder como hecho.
    No afirmar ausencia si coverage_complete es False.
    """
    if not query.strip() and not query_media:
        return {"status": "invalid_query", "message": "Indica texto o un archivo de referencia."}
    limit = max(1, min(int(top_k), 20))
    scope = re.sub(r"_demo$", "", video_id.strip(), flags=re.I).upper()
    coverage = multimodal.index_coverage(scope)
    if query_media and scope:
        exact = multimodal.match_reference_frame(query_media, scope)
        if exact:
            point = _timed(exact)
            if point:
                return {
                    "status": "success", "query": query, "video_id": scope,
                    "coverage_complete": coverage.get("coverage_complete", False),
                    "multimodal_index": coverage,
                    "results": [{**point, "modalities": ["image"],
                                 "retrieval_engines": ["local_frame_match"]}],
                    "exact_visual_match": True, "warnings": [],
                    "note": "Coincidencia geométrica fuerte con un fotograma del vídeo.",
                }
    groups: list[list[dict]] = []
    warnings = []
    primary_multimodal = False
    gemma_attempted = False
    # A usable index is the first retrieval route for a scoped video. Text and
    # CLIP remain fallbacks when it has no candidates or cannot be queried.
    if query.strip() and not query_media and scope and coverage.get("completed_items", 0) > 0:
        gemma_attempted = True
        try:
            gemma = multimodal.search_index(query, "", scope, limit * 4)
            primary = [point for item in gemma if (point := _timed(item))]
            if primary:
                groups.append(primary)
                primary_multimodal = True
        except Exception as error:
            warnings.append(f"EmbeddingGemma 2 no disponible: {str(error)[:200]}")
    if not primary_multimodal:
        if query.strip() and not query_media:
            textual = media_search(query, top_k=min(20, limit * 3), video_id=scope)
            groups.append([point for item in textual if (point := _timed(item))])
            try:
                visual = media_search_visual(query, top_k=min(20, limit * 3), video_id=scope)
                groups.append([point for item in visual if (point := _timed(item))])
            except Exception as error:
                warnings.append(f"Índice CLIP no disponible: {str(error)[:200]}")
        if not gemma_attempted:
            try:
                gemma = multimodal.search_index(query, query_media, scope, limit * 4)
                groups.append([point for item in gemma if (point := _timed(item))])
            except Exception as error:
                warnings.append(f"EmbeddingGemma 2 no disponible: {str(error)[:200]}")
    ranked: list[dict] = []
    for group in groups:
        # With a reference file there is just one embedding engine. Preserve
        # its measured similarity instead of re-ranking it as if several
        # independent engines had voted for the same scene.
        ordered = (sorted(group, key=lambda item: item.get("semantic_score", 0), reverse=True)
                   if query_media or primary_multimodal else group)
        for rank, item in enumerate(ordered):
            match = next((candidate for candidate in ranked
                          if candidate["video_id"] == item["video_id"]
                          and abs(candidate["start_seconds"] - item["start_seconds"]) <= 3), None)
            if match is None:
                match = {**item, "modalities": [item["modality"]], "retrieval_engines": [item["search_engine"]],
                         "fusion_score": float(item.get("semantic_score", 0)) if query_media or primary_multimodal else 0.0}
                ranked.append(match)
            else:
                if item["modality"] not in match["modalities"]:
                    match["modalities"].append(item["modality"])
                if item["search_engine"] not in match["retrieval_engines"]:
                    match["retrieval_engines"].append(item["search_engine"])
                if not match.get("keyframe") and item.get("keyframe"):
                    match["keyframe"] = item["keyframe"]
                if not match.get("full_context") and item.get("full_context"):
                    match["full_context"] = item["full_context"]
                match["end_seconds"] = max(match["end_seconds"], item["end_seconds"])
                match["end_time"] = format_seconds(match["end_seconds"])
            if not query_media and not primary_multimodal:
                match["fusion_score"] += 1 / (60 + rank + 1)
    ranked.sort(key=lambda result: result["fusion_score"], reverse=True)
    return {"status": "success" if ranked else "no_results", "query": query,
            "video_id": scope, "coverage_complete": coverage.get("coverage_complete", False),
            "multimodal_index": coverage, "results": ranked[:limit], "warnings": warnings,
            "retrieval_strategy": "multimodal_first" if primary_multimodal else "fallback",
            "note": "La similitud localiza candidatos; verifica el fragmento antes de afirmar presencia, ausencia o tiempos exactos."}
