"""Video OCR service using RapidOCR and spatial chyron classification."""

import logging
from pathlib import Path
from typing import Any
from PIL import Image

from media_core.runtime import progress, stage

logger = logging.getLogger(__name__)

_RAPID_OCR_INSTANCE = None


def get_ocr_engine():
    """Lazy singleton instantiation of RapidOCR engine."""
    global _RAPID_OCR_INSTANCE
    if _RAPID_OCR_INSTANCE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _RAPID_OCR_INSTANCE = RapidOCR()
        except ImportError as exc:
            raise RuntimeError(
                "rapidocr-onnxruntime no está instalado. Ejecuta 'uv add rapidocr-onnxruntime'."
            ) from exc
    return _RAPID_OCR_INSTANCE


def classify_spatial_region(box: list[list[float]], width: int, height: int) -> str:
    """Classify the on-screen location of a detected text polygon into broadcast regions:

    - 'lower_third': y_top >= 0.58 (names, job titles, lower third banners, tickers, subtitles)
    - 'upper_banner': y_bottom <= 0.22 (live bugs, top headlines, timestamps, channel logos)
    - 'center_screen': 0.22 < y < 0.58 (presentation slides, signs, documents, credits)
    """
    if not box or height <= 0:
        return "center_screen"

    y_coords = [pt[1] for pt in box]
    min_y = min(y_coords)
    max_y = max(y_coords)

    rel_y_top = min_y / height
    rel_y_bottom = max_y / height

    if rel_y_top >= 0.58:
        return "lower_third"
    if rel_y_bottom <= 0.22:
        return "upper_banner"
    return "center_screen"


def clean_text_candidate(text: str) -> str:
    """Normalize and filter raw OCR text candidates."""
    cleaned = " ".join(text.strip().split())
    # Ignore 1-2 character isolated noise unless it's numeric/time (e.g. '14:00' or '85')
    if len(cleaned) <= 2 and not cleaned.isdigit():
        return ""
    # Filter common standalone Asian glyph artifacts from low contrast broadcast noise
    if len(cleaned) == 1:
        return ""
    return cleaned


def extract_ocr_from_frame(
    frame_path: Path,
    min_confidence: float = 0.60,
    engine: Any = None,
) -> list[dict]:
    """Run OCR on a single keyframe JPEG and return classified text blocks."""
    if not frame_path.exists():
        return []

    ocr = engine or get_ocr_engine()

    try:
        with Image.open(frame_path) as img:
            width, height = img.size
    except (OSError, ValueError):
        return []

    results, _ = ocr(str(frame_path))
    if not results:
        return []

    extracted = []
    for line in results:
        box, text, score = line[0], line[1], float(line[2])
        if score < min_confidence:
            continue

        clean_text = clean_text_candidate(text)
        if not clean_text:
            continue

        region = classify_spatial_region(box, width, height)

        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        bbox = [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]

        extracted.append({
            "text": clean_text,
            "region": region,
            "confidence": round(score, 3),
            "bbox": bbox,
        })

    # Sort text blocks top-to-bottom, left-to-right
    extracted.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return extracted


def analyze_video_ocr(
    video_id: str,
    keyframes_dir: Path,
    visual_manifest: dict | None = None,
    min_confidence: float = 0.60,
    sample_representatives_only: bool = True,
    engine: Any = None,
) -> dict:
    """Extract on-screen text, chyrons, and graphics from video keyframes.

    Args:
        video_id: Video identifier (e.g. 'TV05')
        keyframes_dir: Directory containing extracted keyframe JPEGs
        visual_manifest: Manifest from Fase 0 containing scenes and frames
        min_confidence: Minimum OCR confidence threshold (0.0 to 1.0)
        sample_representatives_only: If True, scans representative scene keyframes for high speed
        engine: Optional OCR engine instance (for testing or custom models)
    """
    clean_id = video_id.strip().upper()
    ocr = engine or get_ocr_engine()

    scenes = (visual_manifest or {}).get("scenes", [])
    frames = (visual_manifest or {}).get("frames", [])

    # If no manifest was provided, scan existing files on disk
    if not frames:
        frame_files = sorted(keyframes_dir.glob("frame_*.jpg"))
        frames_to_scan = [
            {"filename": f.name, "timestamp": 0.0, "scene_id": 1, "is_scene_boundary": True}
            for f in frame_files
        ]
    elif sample_representatives_only and scenes:
        # Prioritize 1-2 keyframes per scene: scene boundary + middle frame if scene is long
        target_filenames = set()
        for s in scenes:
            kf = Path(s.get("keyframe", "")).name
            if kf:
                target_filenames.add(kf)
        # Also include any boundary frames marked in frames
        for f in frames:
            if f.get("is_scene_boundary") and not f.get("is_duplicate"):
                target_filenames.add(f["filename"])
        frames_to_scan = [f for f in frames if f["filename"] in target_filenames]
    else:
        # Only non-duplicate frames
        frames_to_scan = [f for f in frames if not f.get("is_duplicate")]

    total_to_scan = len(frames_to_scan)
    all_extracted_items = []
    scene_texts_map: dict[int, dict[str, list[str]]] = {}
    unique_text_set = set()

    with stage("video_ocr_extraction"):
        progress("ocr_scanning", video_id=clean_id, total_frames=total_to_scan)

        for i, meta in enumerate(frames_to_scan):
            filename = meta["filename"]
            frame_path = keyframes_dir / filename
            timestamp = meta.get("timestamp", 0.0)
            scene_id = meta.get("scene_id", 1)

            frame_ocr = extract_ocr_from_frame(frame_path, min_confidence=min_confidence, engine=ocr)

            if scene_id not in scene_texts_map:
                scene_texts_map[scene_id] = {
                    "all": [],
                    "lower_third": [],
                    "upper_banner": [],
                    "center_screen": [],
                }

            for item in frame_ocr:
                txt = item["text"]
                reg = item["region"]

                # Deduplicate within the same scene
                if txt not in scene_texts_map[scene_id]["all"]:
                    scene_texts_map[scene_id]["all"].append(txt)
                    scene_texts_map[scene_id][reg].append(txt)
                    unique_text_set.add(txt)

                    all_extracted_items.append({
                        "text": txt,
                        "region": reg,
                        "confidence": item["confidence"],
                        "timestamp": timestamp,
                        "scene_id": scene_id,
                        "keyframe": f"keyframes/{clean_id}/{filename}",
                        "bbox": item["bbox"],
                    })

    # Build enriched scenes with OCR metadata
    scenes_summary = []
    if scenes:
        for s in scenes:
            sid = s.get("id", 1)
            ocr_info = scene_texts_map.get(sid, {"all": [], "lower_third": [], "upper_banner": [], "center_screen": []})
            scenes_summary.append({
                "scene_id": sid,
                "start_seconds": s.get("start_seconds", 0.0),
                "end_seconds": s.get("end_seconds", 0.0),
                "keyframe": s.get("keyframe", ""),
                "on_screen_text": ocr_info["all"],
                "lower_thirds": ocr_info["lower_third"],
                "upper_banners": ocr_info["upper_banner"],
                "center_screen": ocr_info["center_screen"],
            })
    else:
        for sid, ocr_info in sorted(scene_texts_map.items()):
            scenes_summary.append({
                "scene_id": sid,
                "on_screen_text": ocr_info["all"],
                "lower_thirds": ocr_info["lower_third"],
                "upper_banners": ocr_info["upper_banner"],
                "center_screen": ocr_info["center_screen"],
            })

    scenes_with_text = sum(1 for s in scenes_summary if s["on_screen_text"])

    return {
        "status": "success",
        "video_id": clean_id,
        "total_frames_scanned": total_to_scan,
        "total_texts_detected": len(all_extracted_items),
        "unique_texts_count": len(unique_text_set),
        "scenes_with_text_count": scenes_with_text,
        "extracted_texts": all_extracted_items,
        "scenes": scenes_summary,
        "all_text": " | ".join(sorted(unique_text_set)),
    }
