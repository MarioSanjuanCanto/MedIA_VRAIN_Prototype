"""MCP tool for targeted scene understanding and visual interpretation with VLM."""

import json
import re
from pathlib import Path
from typing import Any

from media_core.config import KEYFRAMES_DIR, OCR_CACHE_DIR, VISUAL_CACHE_DIR
from media_core.runtime import measured, progress
from media_core.services.vlm import describe_keyframe_vlm
from media_core.storage import load_test_vector, resolve_video_file
from media_core.tools.analyze_video import media_analyze_video


@measured
def media_describe_scene(
    video_id: str = "TV01",
    timestamp: float = 0.0,
    scene_id: int | None = None,
    prompt: str = "",
    person: str = "",
    action_query: str = "",
    multi_frame: bool = True,
    model: str | None = None,
) -> dict:
    """Interpreta visualmente una escena o fotograma de vídeo utilizando un Vision-Language Model (VLM).

    Permite a Hermes inspeccionar qué ocurre exactamente en un momento del vídeo: entorno,
    sujetos, acciones en curso, qué hace una persona específica, marcas y rótulos visibles.

    Args:
        video_id: Identificador del vídeo ('TV01', 'TV05', etc.) o ruta al archivo.
        timestamp: Segundo exacto del vídeo a describir (por defecto 0.0).
        scene_id: ID opcional de la escena a describir (prioritario sobre timestamp si se especifica).
        prompt: Pregunta u observación específica para guiar la interpretación visual.
        person: Nombre o rol de la persona sobre la que se quiere poner el foco de atención (ej. 'María Jesús González', 'Salvador Illa', 'reportera').
        action_query: Pregunta concreta sobre la acción o suceso (ej. '¿qué está haciendo?', '¿cómo reacciona?').
        multi_frame: Si es True, analiza una ráfaga temporal de fotogramas contiguos para comprender movimiento y acción continua.
        model: Modelo de visión opcional (por defecto 'google/gemma-4-31b-it').

    Entrega de fotograma:
        El resultado ya incluye 'markdown_image' (ej: '![Fotograma](/api/keyframes/...)'), 'keyframe_url',
        'media_line' (ej: 'MEDIA:<ruta_absoluta>') y 'absolute_keyframe_path'. Puedes usarlos directamente en tu
        respuesta sin buscar archivos locales ni usar search_files.
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

    # Ensure visual manifest and keyframes exist
    if not visual_cache_path.exists() or not keyframes_dir.exists():
        progress("generating_visual_prerequisites", video_id=clean_id)
        visual_manifest = media_analyze_video(video_id=video_id, force=False)
    else:
        try:
            visual_manifest = json.loads(visual_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            visual_manifest = media_analyze_video(video_id=video_id, force=False)

    scenes = visual_manifest.get("scenes", [])
    frames = visual_manifest.get("frames", [])

    target_keyframe_filename = None
    target_timestamp = timestamp
    matched_scene_id = scene_id
    target_paths: list[Path] = []

    # 1. If scene_id was given, find representative keyframe
    if scene_id is not None and scenes:
        scene = next((s for s in scenes if s.get("id") == scene_id), None)
        if scene and scene.get("keyframe"):
            target_keyframe_filename = Path(scene["keyframe"]).name
            target_timestamp = scene.get("start_seconds", 0.0)

    # 2. Otherwise locate closest frame to timestamp
    if not target_keyframe_filename and frames:
        closest_idx = min(range(len(frames)), key=lambda i: abs(frames[i].get("timestamp", 0.0) - timestamp))
        closest = frames[closest_idx]
        target_keyframe_filename = closest.get("filename")
        target_timestamp = closest.get("timestamp", timestamp)
        matched_scene_id = closest.get("scene_id")

        if multi_frame and len(frames) > 1:
            indices = [closest_idx]
            if closest_idx > 0 and abs(frames[closest_idx - 1].get("timestamp", 0.0) - target_timestamp) <= 5.0:
                indices.insert(0, closest_idx - 1)
            if closest_idx + 1 < len(frames) and abs(frames[closest_idx + 1].get("timestamp", 0.0) - target_timestamp) <= 5.0:
                indices.append(closest_idx + 1)
            for idx in indices:
                fname = frames[idx].get("filename")
                if fname:
                    p = keyframes_dir / fname
                    if p.exists():
                        target_paths.append(p)

    # 3. Fallback to first available file
    if not target_keyframe_filename:
        files = sorted(keyframes_dir.glob("frame_*.jpg"))
        if not files:
            return {
                "status": "error",
                "message": f"No se encontraron fotogramas clave extraídos para '{clean_id}'.",
            }
        target_keyframe_filename = files[0].name

    if not target_paths:
        target_paths = [keyframes_dir / target_keyframe_filename]

    # Multimodal context fusion (OCR + Transcript around timestamp)
    context_metadata: dict[str, Any] = {}
    ocr_cache_file = OCR_CACHE_DIR / f"{clean_id}_ocr.json"
    if ocr_cache_file.exists():
        try:
            ocr_data = json.loads(ocr_cache_file.read_text(encoding="utf-8"))
            nearby_texts = [
                t["text"] for t in ocr_data.get("extracted_texts", [])
                if abs(t.get("timestamp", 0.0) - target_timestamp) <= 12.0
            ]
            if nearby_texts:
                context_metadata["ocr_texts"] = list(dict.fromkeys(nearby_texts))[:6]
        except Exception:
            pass

    test_vector = load_test_vector(clean_id)
    active_speaker_info = None
    if test_vector and test_vector.get("segments"):
        segs = test_vector.get("segments", [])
        from media_core.services.speaker_identities import get_speaker_at_timestamp
        active_speaker_info = get_speaker_at_timestamp(clean_id, target_timestamp, segments=segs)
        if active_speaker_info and active_speaker_info.get("status") == "success":
            context_metadata["speaker"] = active_speaker_info.get("display_name")
            context_metadata["speaker_id"] = active_speaker_info.get("speaker_id")
            context_metadata["speaker_name"] = active_speaker_info.get("name")
            context_metadata["speaker_role"] = active_speaker_info.get("role")
        matching_segs = [
            s for s in segs
            if s.get("start_seconds", 0.0) - 3.0 <= target_timestamp <= s.get("end_seconds", 0.0) + 3.0
        ]
        if matching_segs:
            context_metadata["transcript_excerpt"] = " ".join(s.get("text", "") for s in matching_segs)[:220]

    # If person wasn't explicitly given but speaker was identified, focus on them
    query_person = person
    if not query_person and active_speaker_info and active_speaker_info.get("name"):
        if active_speaker_info["name"] not in {"Desconocido", "Hablante", "SPEAKER"}:
            query_person = active_speaker_info["name"]

    res = describe_keyframe_vlm(
        image_paths=target_paths,
        video_id=clean_id,
        timestamp=target_timestamp,
        user_prompt=prompt,
        target_person=query_person,
        action_query=action_query,
        context_metadata=context_metadata if context_metadata else None,
        model=model,
    )
    target_abs = str((keyframes_dir / target_keyframe_filename).resolve())
    target_url = f"/api/keyframes/{clean_id}/{target_keyframe_filename}"
    res["scene_id"] = matched_scene_id
    res["keyframe_path"] = f"keyframes/{clean_id}/{target_keyframe_filename}"
    res["keyframe_paths"] = [f"keyframes/{clean_id}/{p.name}" for p in target_paths]
    res["absolute_keyframe_path"] = target_abs
    res["absolute_keyframe_paths"] = [str(p.resolve()) for p in target_paths]
    res["keyframe_url"] = target_url
    res["media_line"] = f"MEDIA:{target_abs}"
    res["markdown_image"] = f"![Fotograma {clean_id} {target_timestamp:.1f}s]({target_url})"
    if active_speaker_info and active_speaker_info.get("status") == "success":
        res["active_speaker"] = active_speaker_info
        if not res.get("person_role_identified"):
            res["person_role_identified"] = active_speaker_info.get("display_name")
    if context_metadata:
        res["multimodal_context"] = context_metadata
    return res
