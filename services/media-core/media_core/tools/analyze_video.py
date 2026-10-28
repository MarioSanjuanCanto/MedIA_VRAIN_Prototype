"""MCP tool for visual scene analysis, keyframe extraction, and visual manifest generation."""

import json
import re
from pathlib import Path

from media_core.config import VISUAL_CACHE_DIR, KEYFRAMES_DIR
from media_core.runtime import measured, progress, stage
from media_core.services.scene_detection import analyze_video_frames
from media_core.storage import file_fingerprint, load_test_vector, resolve_video_file, source_matches_file


@measured
def media_analyze_video(
    video_id: str = "TV01",
    force: bool = False,
    extract_keyframes: bool = True,
    threshold: float = 0.35,
    max_interval: float = 2.5,
) -> dict:
    """Detecta cambios de plano, extrae fotogramas clave y genera el manifiesto visual con caché reanudable.

    Args:
        video_id: Identificador del vídeo ('TV01', 'TV04', etc.) o ruta directa al archivo.
        force: Si es True, reanaliza el vídeo incluso si existe caché previa válida.
        extract_keyframes: Si es True, guarda los fotogramas clave como imágenes JPEG en data/cache/keyframes/.
        threshold: Umbral de detección de corte de escena (0.0 a 1.0, por defecto 0.35).
        max_interval: Intervalo máximo en segundos entre fotogramas en planos largos (por defecto 2.5s).
    """
    video_file = resolve_video_file(video_id)
    if not video_file or not video_file.is_file():
        return {
            "status": "error",
            "message": f"No se encontró el archivo de vídeo para '{video_id}'. Coloca el archivo en data/test-vectors/videos/.",
        }

    stem = video_file.stem
    clean_id = re.sub(r"_demo$", "", stem, flags=re.I).upper()
    cache_path = VISUAL_CACHE_DIR / f"{clean_id}_visual.json"

    # Reusable cache verification by SHA-256 fingerprint
    if cache_path.exists() and not force:
        try:
            cached_manifest = json.loads(cache_path.read_text(encoding="utf-8"))
            source_identity = cached_manifest.get("request_metadata", {}).get("source")
            if source_matches_file(source_identity, video_file):
                cached_manifest["cache_hit"] = True
                return cached_manifest
        except (OSError, ValueError):
            pass

    # Source fingerprint calculation
    with stage("visual_fingerprinting"):
        fingerprint = file_fingerprint(video_file)

    # Optional speech segments alignment
    tv = load_test_vector(clean_id)
    whisper_segments = tv.get("segments") if tv else None

    # Extraction and deduplication pipeline
    dest_dir = KEYFRAMES_DIR / clean_id
    manifest = analyze_video_frames(
        video_path=video_file,
        video_id=clean_id,
        output_dir=dest_dir,
        threshold=threshold,
        max_interval=max_interval,
        whisper_segments=whisper_segments,
    )

    manifest["request_metadata"] = {
        "source": fingerprint,
        "video_file": str(video_file.resolve()),
        "threshold": threshold,
        "max_interval": max_interval,
    }
    manifest["cache_hit"] = False

    # Persist visual manifest
    VISUAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return manifest
