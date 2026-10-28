"""MCP tool for targeted subject grounding and image cropping using PIL and multimodal VLM."""

from __future__ import annotations

import re
from pathlib import Path

from media_core.config import KEYFRAMES_DIR
from media_core.runtime import measured
from media_core.services.image_processing import crop_image_subject, detect_subject_bounding_box
from media_core.storage import resolve_video_file
from media_core.tools.analyze_video import media_analyze_video


@measured
def media_crop_subject(
    video_id: str = "TV04",
    timestamp: float = 0.0,
    target_description: str = "",
    bbox: list[float] | None = None,
    keyframe_path: str = "",
    padding_pct: float = 0.12,
    model: str | None = None,
) -> dict:
    """Localiza un sujeto, persona u objeto en un fotograma de vídeo y genera un recorte enfocado con PIL.

    Permite recortar según lo que pida el usuario (ej: 'la mujer musulmana con hiyab detrás del orador',
    'el diputado de la derecha sin gafas', 'el micrófono de mesa', o un bounding box [ymin, xmin, ymax, xmax]).

    Args:
        video_id: Identificador del vídeo ('TV04', 'TV01', etc.).
        timestamp: Segundo exacto del vídeo a analizar (ej. 687.0 para 11:27, 1234.0 para 20:34).
        target_description: Descripción en lenguaje natural del sujeto a recortar.
        bbox: Coordenadas opcionales [ymin, xmin, ymax, xmax] (en escala 0-1000 o 0.0-1.0). Si se omite, el VLM localiza al sujeto.
        keyframe_path: Ruta opcional al fotograma si ya se conoce.
        padding_pct: Margen contextual adicional (por defecto 0.12 = 12% a cada lado).
        model: Modelo de visión opcional (por defecto 'google/gemma-4-31b-it').
    """
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", video_id).upper()
    keyframes_dir = KEYFRAMES_DIR / clean_id

    # 1. Resolve source keyframe
    source_frame_path: Path | None = None
    if keyframe_path:
        candidate = Path(keyframe_path)
        if candidate.is_file():
            source_frame_path = candidate
        elif (KEYFRAMES_DIR / candidate).is_file():
            source_frame_path = KEYFRAMES_DIR / candidate
        elif (keyframes_dir / candidate.name).is_file():
            source_frame_path = keyframes_dir / candidate.name

    if not source_frame_path:
        # Locate closest keyframe to timestamp
        if not keyframes_dir.is_dir():
            media_analyze_video(video_id=video_id, force=False)

        manifest_path = KEYFRAMES_DIR.parent / "visual" / f"{clean_id}_visual.json"
        if manifest_path.is_file():
            import json
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                frames = manifest.get("frames", [])
                if frames:
                    closest = min(frames, key=lambda f: abs(f.get("timestamp", 0.0) - timestamp))
                    fn = closest.get("filename")
                    if fn and (keyframes_dir / fn).is_file():
                        source_frame_path = keyframes_dir / fn
            except Exception:
                pass

    if not source_frame_path and keyframes_dir.is_dir():
        all_frames = sorted(keyframes_dir.glob("frame_*.jpg"))
        if all_frames:
            source_frame_path = all_frames[0]

    if not source_frame_path or not source_frame_path.is_file():
        return {
            "status": "error",
            "message": f"No se encontró un fotograma válido para '{clean_id}' en el segundo {timestamp:.1f}.",
        }

    # 2. Detect bounding box if not explicitly provided
    box_to_use = bbox
    detected_label = target_description
    if not box_to_use:
        if not target_description:
            return {
                "status": "invalid_request",
                "message": "Debes proporcionar 'target_description' o 'bbox' para recortar.",
            }
        grounding = detect_subject_bounding_box(
            image_path=source_frame_path,
            target_description=target_description,
            model=model,
        )
        if not grounding.get("found") or not grounding.get("box_2d"):
            return {
                "status": "not_found",
                "message": f"No se pudo localizar '{target_description}' en el fotograma {source_frame_path.name}.",
                "source_frame": str(source_frame_path.resolve()),
            }
        box_to_use = grounding["box_2d"]
        detected_label = grounding.get("label", target_description)

    # 3. Perform crop with PIL
    safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", target_description or "crop").strip("_")[:24].lower()
    crop_filename = f"crop_{safe_slug}_{int(timestamp)}s_{source_frame_path.stem}.jpg"
    target_crop_path = keyframes_dir / crop_filename

    try:
        crop_info = crop_image_subject(
            image_path=source_frame_path,
            box_2d=box_to_use,
            output_path=target_crop_path,
            padding_pct=padding_pct,
        )
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Error al recortar la imagen con PIL: {exc}",
        }

    abs_path = str(target_crop_path.resolve())
    crop_url = f"/api/keyframes/{clean_id}/{crop_filename}"
    desc_label = target_description or "Recorte"

    return {
        "status": "success",
        "video_id": clean_id,
        "timestamp": timestamp,
        "source_frame": source_frame_path.name,
        "target_description": target_description,
        "detected_label": detected_label,
        "box_2d": box_to_use,
        "crop_dimensions": {"width": crop_info["width"], "height": crop_info["height"]},
        "crop_path": f"keyframes/{clean_id}/{crop_filename}",
        "absolute_crop_path": abs_path,
        "crop_url": crop_url,
        "media_line": f"MEDIA:{abs_path}",
        "markdown_image": f"![{desc_label}]({crop_url})",
    }
