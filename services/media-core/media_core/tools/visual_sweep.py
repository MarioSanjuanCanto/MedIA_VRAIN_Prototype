"""Background MCP entry point for exhaustive visual video search."""

from media_core.services.visual_sweep import run_visual_sweep
from media_core.tools.jobs import _submit


def media_find_all_visual(
    video_id: str,
    query: str,
    chunk_seconds: float = 20.0,
    sample_interval: float = 2.5,
    workers: int = 2,
    padding_seconds: float = 2.0,
    extract_clips: bool = True,
    owner_id: str = "",
) -> dict:
    """Busca exhaustivamente TODAS las apariciones visuales y extrae sus clips.

    Recorre el vídeo completo en bloques solapados, analiza muestras temporales con
    el VLM en paralelo, guarda una caché reanudable y devuelve job_id de inmediato.
    Consulta media_job_status para obtener percent/completed_chunks y el resultado.
    Para conteos de personas u objetos aclara que son apariciones visuales estimadas,
    no identidades únicas, salvo que exista seguimiento de identidad.
    """
    arguments = dict(video_id=video_id, query=query, chunk_seconds=chunk_seconds,
                     sample_interval=sample_interval, workers=workers,
                     padding_seconds=padding_seconds, extract_clips=extract_clips)
    return _submit(run_visual_sweep, arguments, owner_id=owner_id)
