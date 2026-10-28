"""MCP tools for temporal analysis and grounded event search."""

from media_core.runtime import measured
from media_core.services.temporal import describe_segment, index_next, index_status, search_events


@measured
def media_index_video(video_id: str, max_chunks: int = 1, force: bool = False,
                      retry_failed: bool = False, max_cost_usd: float = 3.0) -> dict:
    """Analiza los siguientes tramos pendientes del vídeo y conserva cada resultado.

    Una llamada procesa entre 1 y 10 tramos; repite hasta status=completed.
    La API web lo hace en segundo plano y reanuda tras reinicios. El límite de
    coste se aplica al vídeo completo, no a una llamada individual.
    """
    try:
        return index_next(video_id, max_chunks, force, retry_failed, max_cost_usd)
    except Exception as error:
        return {"status": "error", "message": str(error)[:500]}


@measured
def media_video_index_status(video_id: str) -> dict:
    """Devuelve cobertura, progreso, modelo y coste del índice temporal."""
    try:
        return index_status(video_id)
    except Exception as error:
        return {"status": "error", "message": str(error)[:500]}


@measured
def media_search_events(query: str, video_id: str, top_k: int = 5,
                        all_occurrences: bool = False) -> dict:
    """Busca acciones, objetos, personas descritas y rótulos por vídeo.

    Los resultados son candidatos con evidencia y tiempos; para recuentos o
    detalles fugaces verifica el tramo con media_describe_segment. Si el índice
    es parcial, coverage_complete=False impide afirmar ausencias.
    """
    try:
        return search_events(query, video_id, top_k, all_occurrences)
    except Exception as error:
        return {"status": "error", "message": str(error)[:500]}


@measured
def media_describe_segment(video_id: str, start_seconds: float, end_seconds: float,
                           prompt: str = "", fps: float = 3.0) -> dict:
    """Revisa 1-12 s con 1-5 fotogramas/s para confirmar una acción o detalle.

    Esta herramienta no reanaliza el vídeo completo. Devuelve los fotogramas
    examinados y tiempos absolutos para fundamentar la respuesta.
    """
    try:
        return describe_segment(video_id, start_seconds, end_seconds, prompt, fps)
    except Exception as error:
        return {"status": "error", "message": str(error)[:500]}
