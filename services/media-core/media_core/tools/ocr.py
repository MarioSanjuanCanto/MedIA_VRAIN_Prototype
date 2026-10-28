"""MCP tool for video OCR, lower-thirds extraction, and on-screen graphics indexing."""

import json
import hashlib
import math
import os
import re
import subprocess
import uuid
from pathlib import Path

from media_core.config import KEYFRAMES_DIR, OCR_CACHE_DIR, VISUAL_CACHE_DIR
from media_core.runtime import measured, progress, stage
from media_core.services.scene_detection import probe_video
from media_core.services.video_ocr import analyze_video_ocr, extract_ocr_from_frame
from media_core.storage import file_fingerprint, resolve_video_file, source_matches_file
from media_core.tools.analyze_video import media_analyze_video


@measured
def media_ocr_video(
    video_id: str,
    force: bool = False,
    min_confidence: float = 0.60,
    sample_representatives_only: bool = True,
    timestamp: float | None = None,
) -> dict:
    """Lee el texto visible de un vídeo; para un instante concreto indica video_id y timestamp.

    Con timestamp extrae el fotograma exacto y aplica OCR solo a ese momento.
    Sin timestamp analiza o reutiliza el OCR del vídeo completo.

    Args:
        video_id: Obligatorio. Identificador del vídeo ('TV05', etc.) o ruta al archivo.
        force: Si es True, reejecuta el OCR ignorando la caché previa.
        min_confidence: Umbral de confianza mínimo de detección de texto (0.0 a 1.0, por defecto 0.60).
        sample_representatives_only: Si es True, procesa los fotogramas clave representativos de cada escena (rápido).
        timestamp: Segundo del vídeo que se quiere leer (4:30 = 270.0). Omítelo solo para analizar todo el vídeo.
    """
    if not isinstance(video_id, str) or not video_id.strip():
        return {
            "status": "error", "error_code": "video_id_required",
            "message": "Indica video_id, por ejemplo 'TV05'. No se selecciona un vídeo por defecto.",
        }
    video_file = resolve_video_file(video_id)
    if not video_file or not video_file.is_file():
        return {
            "status": "error",
            "message": f"No se encontró el archivo de vídeo para '{video_id}'. Coloca el archivo en data/test-vectors/videos/.",
        }

    stem = video_file.stem
    clean_id = re.sub(r"_demo$", "", stem, flags=re.I).upper()
    if timestamp is not None:
        try:
            requested = float(timestamp)
            duration = float(probe_video(video_file)["duration"])
        except (TypeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            return {"status": "error", "video_id": clean_id,
                    "message": f"No se pudo validar el instante del vídeo: {exc}"}
        if not math.isfinite(requested) or requested < 0 or requested >= duration:
            return {"status": "error", "video_id": clean_id,
                    "message": f"timestamp debe estar entre 0 y {duration:.2f} segundos."}
        stamp = video_file.stat()
        signature = hashlib.sha256(
            f"{video_file.resolve()}:{stamp.st_size}:{stamp.st_mtime_ns}:{requested:.3f}".encode()
        ).hexdigest()[:12]
        frame_dir = KEYFRAMES_DIR / clean_id
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / f"ocr_at_{round(requested * 1000):09d}_{signature}.jpg"
        if not frame_path.is_file():
            temporary = frame_dir / f"ocr_tmp_{uuid.uuid4().hex}.jpg"
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                       "-ss", f"{requested:.3f}", "-i", str(video_file),
                       "-frames:v", "1", "-q:v", "2", str(temporary)]
            try:
                with stage("ocr_frame_extraction"):
                    result = subprocess.run(command, capture_output=True, text=True, timeout=45,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if result.returncode or not temporary.is_file() or temporary.stat().st_size <= 1024:
                    return {"status": "error", "video_id": clean_id,
                            "message": f"No se pudo extraer el fotograma en {requested:.3f} s: {result.stderr[-300:]}"}
                temporary.replace(frame_path)
            except (OSError, subprocess.SubprocessError) as exc:
                return {"status": "error", "video_id": clean_id,
                        "message": f"No se pudo extraer el fotograma en {requested:.3f} s: {exc}"}
            finally:
                temporary.unlink(missing_ok=True)
        with stage("ocr_single_frame"):
            blocks = extract_ocr_from_frame(frame_path, min_confidence=min_confidence)
        keyframe = f"keyframes/{clean_id}/{frame_path.name}"
        return {
            "status": "success", "video_id": clean_id, "timestamp": requested,
            "mode": "single_frame", "frame_path": str(frame_path),
            "keyframe_path": keyframe,
            "keyframe_url": f"/api/keyframes/{clean_id}/{frame_path.name}",
            "media_line": f"MEDIA:{frame_path.resolve()}",
            "markdown_image": f"![Fotograma {clean_id} {requested:.1f}s](/api/keyframes/{clean_id}/{frame_path.name})",
            "total_texts_detected": len(blocks),
            "extracted_texts": [{**block, "timestamp": requested, "keyframe": keyframe} for block in blocks],
            "all_text": " | ".join(block["text"] for block in blocks),
        }
    cache_path = OCR_CACHE_DIR / f"{clean_id}_ocr.json"

    # Reusable cache verification by SHA-256 fingerprint
    if cache_path.exists() and not force:
        try:
            cached_ocr = json.loads(cache_path.read_text(encoding="utf-8"))
            source_identity = cached_ocr.get("request_metadata", {}).get("source")
            if source_matches_file(source_identity, video_file):
                cached_ocr["cache_hit"] = True
                return cached_ocr
        except (OSError, ValueError):
            pass

    # Source fingerprint calculation
    with stage("ocr_fingerprinting"):
        fingerprint = file_fingerprint(video_file)

    # Ensure keyframes and visual manifest exist
    keyframes_dir = KEYFRAMES_DIR / clean_id
    visual_cache_path = VISUAL_CACHE_DIR / f"{clean_id}_visual.json"
    visual_manifest = None

    if visual_cache_path.exists():
        try:
            visual_manifest = json.loads(visual_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    if not keyframes_dir.exists() or not any(keyframes_dir.glob("frame_*.jpg")) or visual_manifest is None:
        progress("extracting_visual_prerequisites", video_id=clean_id)
        visual_manifest = media_analyze_video(video_id=video_id, force=False)

    # Run OCR analysis
    ocr_result = analyze_video_ocr(
        video_id=clean_id,
        keyframes_dir=keyframes_dir,
        visual_manifest=visual_manifest,
        min_confidence=min_confidence,
        sample_representatives_only=sample_representatives_only,
    )

    ocr_result["request_metadata"] = {
        "source": fingerprint,
        "video_file": str(video_file.resolve()),
        "min_confidence": min_confidence,
        "sample_representatives_only": sample_representatives_only,
    }
    ocr_result["cache_hit"] = False

    # Persist OCR cache
    OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(ocr_result, ensure_ascii=False, indent=2), encoding="utf-8")

    return ocr_result
