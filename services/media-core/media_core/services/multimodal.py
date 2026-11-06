"""Resumable, CPU-bounded EmbeddingGemma 2 indexing and retrieval."""

import json
import hashlib
import math
import os
import re
import subprocess
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

import chromadb
from chromadb.config import Settings
from PIL import Image

from media_core.config import (
    CHROMA_PERSIST_DIR, EMBEDDING_CACHE_DIR, KEYFRAMES_DIR, VIDEOS_DIR,
    MULTIMODAL_CACHE_DIR, MULTIMODAL_CPU_THREADS, MULTIMODAL_MODEL,
    MULTIMODAL_QUERY_DIR,
)
from media_core.services.scene_detection import probe_video
from media_core.storage import file_fingerprint, resolve_video_file

COLLECTION = "media_embeddinggemma2_v1"
SCHEMA_VERSION = 1
WINDOW_SECONDS = 12.0
_LOCK = threading.RLock()
_MODEL = None
_CLIENT = None
_QUERY_VECTORS: OrderedDict[tuple[str, str, str], list[float]] = OrderedDict()
_QUERY_CACHE_SIZE = 32


def _model():
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(MULTIMODAL_CPU_THREADS)
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            _MODEL = SentenceTransformer(
                MULTIMODAL_MODEL,
                cache_folder=str(EMBEDDING_CACHE_DIR),
                model_kwargs={"dtype": torch.bfloat16, "low_cpu_mem_usage": True},
            )
        return _MODEL


def _collection():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = chromadb.PersistentClient(
            path=str(CHROMA_PERSIST_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _CLIENT.get_or_create_collection(
        name=COLLECTION,
        metadata={"description": "EmbeddingGemma 2 timed media", "schema_version": SCHEMA_VERSION},
        configuration={"hnsw": {"space": "cosine"}},
    )


def _manifest_path(video_id: str) -> Path:
    return MULTIMODAL_CACHE_DIR / f"{video_id}.json"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _source(video_id: str) -> tuple[Path, str, dict]:
    path = resolve_video_file(video_id)
    if not path or not path.is_file():
        raise ValueError(f"No se encontró el vídeo '{video_id}'.")
    clean_id = re.sub(r"_demo$", "", path.stem, flags=re.I).upper()
    return path, clean_id, file_fingerprint(path)


def _items(path: Path, video_id: str) -> list[dict]:
    from media_core.tools.analyze_video import media_analyze_video
    visual = media_analyze_video(video_id=str(path), force=False)
    if visual.get("status") == "error":
        raise RuntimeError(visual.get("message", "No se pudieron extraer fotogramas."))
    duration = float(visual.get("duration_seconds") or probe_video(path)["duration"])
    if duration <= 0:
        raise ValueError("No se pudo determinar la duración del vídeo.")
    frames = [f for f in visual.get("frames", []) if not f.get("is_duplicate")]
    chosen = []
    last_time = -math.inf
    last_scene = None
    for frame in frames:
        timestamp = float(frame.get("timestamp", 0))
        scene = frame.get("scene_id")
        if scene != last_scene or timestamp - last_time >= 8:
            chosen.append({"modality": "image", "start_seconds": timestamp,
                           "end_seconds": min(duration, timestamp + 2.5),
                           "filename": frame["filename"]})
            last_time, last_scene = timestamp, scene
    items = chosen
    has_audio = bool(_probe_audio(path))
    for start in range(0, math.ceil(duration), int(WINDOW_SECONDS)):
        end = min(duration, start + WINDOW_SECONDS)
        if has_audio:
            items.append({"modality": "audio", "start_seconds": float(start),
                          "end_seconds": end})
        items.append({"modality": "video", "start_seconds": float(start),
                      "end_seconds": end})
    return items


def _probe_audio(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.returncode == 0 and bool(json.loads(result.stdout).get("streams"))


def _prepare(path: Path, modality: str, start: float, end: float, dest: Path) -> None:
    if modality != "audio":
        raise ValueError("_prepare solo prepara audio; el vídeo se entrega como fotogramas.")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
               "-i", str(path), "-t", str(end - start), "-vn", "-ac", "1", "-ar", "16000", str(dest)]
    result = subprocess.run(
        command, capture_output=True, text=True,
        timeout=max(90, (end - start) * 10),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode or not dest.is_file():
        raise RuntimeError(f"FFmpeg no preparó {modality}: {result.stderr[-300:]}")


def _video_frames(path: Path, start: float, end: float) -> list[Image.Image]:
    """Decode at most 12 evenly spaced frames without TorchCodec's Windows DLLs."""
    duration = end - start
    if duration <= 0:
        raise ValueError("El fragmento de vídeo debe tener duración positiva.")
    rate = min(1.0, 12.0 / duration)
    with tempfile.TemporaryDirectory(prefix="media-gemma-frames-") as temp:
        pattern = str(Path(temp) / "frame-%03d.jpg")
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                   "-i", str(path), "-t", str(duration), "-an", "-vf",
                   f"fps={rate:.6f},scale=-2:360", "-frames:v", "12", "-q:v", "4", pattern]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=max(90, duration * 10),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise RuntimeError(f"FFmpeg no extrajo los fotogramas: {result.stderr[-300:]}")
        frames = []
        for frame in sorted(Path(temp).glob("frame-*.jpg")):
            with Image.open(frame) as image:
                frames.append(image.convert("RGB"))
        if not frames:
            raise RuntimeError("El fragmento de vídeo no contiene fotogramas legibles.")
        return frames


def _encode(payload: dict, prompt_name: str | None = None) -> list[float]:
    if isinstance(payload.get("image"), str):
        with Image.open(payload["image"]) as image:
            payload = {**payload, "image": image.convert("RGB")}
    if isinstance(payload.get("video"), str):
        path = Path(payload["video"])
        payload = {**payload, "video": _video_frames(path, 0, float(probe_video(path)["duration"]))}
    vector = _model().encode(
        payload, prompt_name=prompt_name, normalize_embeddings=True,
        show_progress_bar=False,
        processing_kwargs={"image": {"max_soft_tokens": 140},
                           "video": {"fps": 1, "max_frames": 12}},
    )
    return vector.tolist() if hasattr(vector, "tolist") else list(vector)


def _index_one(path: Path, video_id: str, source_hash: str, item: dict) -> None:
    modality = item["modality"]
    start, end = item["start_seconds"], item["end_seconds"]
    if modality == "image":
        image = KEYFRAMES_DIR / video_id / item["filename"]
        if not image.is_file():
            raise FileNotFoundError(image)
        vector = _encode({"image": str(image)})
        keyframe = f"keyframes/{video_id}/{item['filename']}"
    elif modality == "video":
        vector = _encode({"video": _video_frames(path, start, end)})
        keyframe = ""
    else:
        with tempfile.TemporaryDirectory(prefix="media-gemma-") as temp:
            clip = Path(temp) / "clip.wav"
            _prepare(path, modality, start, end, clip)
            vector = _encode({modality: str(clip)})
        keyframe = ""
    point_id = f"{video_id}:{source_hash[:16]}:{modality}:{start:.3f}"
    _collection().upsert(
        ids=[point_id], embeddings=[vector],
        documents=[f"{video_id} {modality} {start:.2f}-{end:.2f}s"],
        metadatas=[{"video_id": video_id, "source_sha256": source_hash,
                    "model": MULTIMODAL_MODEL, "schema_version": SCHEMA_VERSION,
                    "modality": modality, "start_seconds": start,
                    "end_seconds": end, "keyframe": keyframe}],
    )


def _summary(manifest: dict) -> dict:
    items = manifest["items"]
    done = sum(item.get("status") == "completed" for item in items)
    failed = sum(item.get("status") == "failed" for item in items)
    pending = len(items) - done - failed
    status = "completed" if not pending and not failed else "partial" if not pending else "processing"
    counts = {kind: sum(item.get("status") == "completed" and item["modality"] == kind
                        for item in items) for kind in ("image", "audio", "video")}
    return {"status": status, "video_id": manifest["video_id"],
            "completed_items": done, "total_items": len(items), "failed_items": failed,
            "coverage_complete": status == "completed", "indexed_by_modality": counts,
            "progress_percent": round(100 * done / len(items), 1) if items else 100.0}


def index_status(video_id: str) -> dict:
    try:
        path, clean_id, identity = _source(video_id)
    except ValueError as error:
        return {"status": "error", "message": str(error)}
    manifest = _read(_manifest_path(clean_id))
    if not manifest:
        return {"status": "not_started", "video_id": clean_id, "coverage_complete": False}
    if manifest.get("source") != identity or manifest.get("model") != MULTIMODAL_MODEL:
        return {"status": "stale", "video_id": clean_id, "coverage_complete": False}
    return _summary(manifest)


def index_coverage(video_id: str = "") -> dict:
    if video_id:
        return index_status(video_id)
    videos = [path for path in VIDEOS_DIR.iterdir()
              if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}]
    states = [index_status(path.stem) for path in videos]
    complete = sum(state.get("coverage_complete", False) for state in states)
    return {"status": "completed" if videos and complete == len(videos) else "mixed",
            "coverage_complete": bool(videos) and complete == len(videos),
            "completed_videos": complete, "total_videos": len(videos)}


def index_next(video_id: str, max_items: int = 4, retry_failed: bool = False) -> dict:
    if not 1 <= max_items <= 16:
        return {"status": "error", "message": "max_items debe estar entre 1 y 16."}
    with _LOCK:
        path, clean_id, identity = _source(video_id)
        # A missing/incompatible model is transient; retain every item as pending.
        _model()
        target = _manifest_path(clean_id)
        manifest = _read(target)
        if (not manifest or manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("model") != MULTIMODAL_MODEL
                or manifest.get("source") != identity):
            # Retire records for an earlier upload with the same video ID.
            collection = _collection()
            old = collection.get(where={"video_id": clean_id})
            if old.get("ids"):
                collection.delete(ids=old["ids"])
            manifest = {"schema_version": SCHEMA_VERSION, "model": MULTIMODAL_MODEL,
                        "video_id": clean_id, "source": identity,
                        "items": _items(path, clean_id)}
            _write(target, manifest)
        elif retry_failed:
            for item in manifest["items"]:
                if item.get("status") == "failed":
                    item.pop("error", None)
                    item.pop("status", None)
            _write(target, manifest)
        processed = 0
        for item in manifest["items"]:
            if item.get("status") in {"completed", "failed"}:
                continue
            try:
                _index_one(path, clean_id, identity["sha256"], item)
                item["status"] = "completed"
            except Exception as error:
                item.update(status="failed", error=str(error)[:300])
            _write(target, manifest)
            processed += 1
            if processed >= max_items:
                break
        return _summary(manifest)


def search_index(query: str, query_media: str = "", video_id: str = "", top_k: int = 10) -> list[dict]:
    collection = _collection()
    if collection.count() == 0:
        return []
    payload = {}
    cache_key = None
    if query.strip():
        payload["text"] = query.strip()
    if query_media:
        if Path(query_media).name != query_media:
            raise ValueError("Nombre de archivo de consulta no válido.")
        target = (MULTIMODAL_QUERY_DIR / query_media).resolve()
        if not target.is_file() or not target.is_relative_to(MULTIMODAL_QUERY_DIR.resolve()):
            raise ValueError("El archivo de consulta no está disponible en el espacio temporal autorizado.")
        suffix = target.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            modality = "image"
        elif suffix in {".wav", ".mp3", ".ogg", ".m4a"}:
            modality = "audio"
        elif suffix in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
            modality = "video"
        else:
            raise ValueError("Tipo de archivo de consulta no admitido.")
        payload[modality] = str(target)
        with target.open("rb") as source:
            cache_key = (MULTIMODAL_MODEL, query.strip(), hashlib.file_digest(source, "sha256").hexdigest())
    if not payload:
        return []
    with _LOCK:
        vector = _QUERY_VECTORS.get(cache_key) if cache_key else None
        if vector is not None:
            _QUERY_VECTORS.move_to_end(cache_key)
    if vector is None:
        vector = _encode(payload, "SearchQuery" if query.strip() else None)
        if cache_key:
            with _LOCK:
                _QUERY_VECTORS[cache_key] = vector
                _QUERY_VECTORS.move_to_end(cache_key)
                while len(_QUERY_VECTORS) > _QUERY_CACHE_SIZE:
                    _QUERY_VECTORS.popitem(last=False)
    where = {"where": {"video_id": video_id.strip().upper()}} if video_id.strip() else {}
    raw = collection.query(**where, query_embeddings=[vector],
                           n_results=min(collection.count(), max(1, top_k)),
                           include=["metadatas", "distances"])
    results = []
    for metadata, distance in zip((raw.get("metadatas") or [[]])[0],
                                  (raw.get("distances") or [[]])[0]):
        video = resolve_video_file(metadata.get("video_id", ""))
        if not video or file_fingerprint(video)["sha256"] != metadata.get("source_sha256"):
            continue
        results.append({"video_id": metadata["video_id"],
                        "start_seconds": float(metadata["start_seconds"]),
                        "end_seconds": float(metadata["end_seconds"]),
                        "modality": metadata["modality"],
                        "keyframe": metadata.get("keyframe") or None,
                        "semantic_score": round(1 - float(distance), 4),
                        "search_engine": "embeddinggemma_2",
                        "evidence_status": "semantic_candidate"})
    return results


def match_reference_frame(query_media: str, video_id: str) -> dict | None:
    """Find a strong geometric match for a screenshot in a short, scoped video.

    This is a fast path for frames copied from the source video. Weak or
    ambiguous matches fall back to the semantic embedding search.
    """
    if Path(query_media).name != query_media or Path(query_media).suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".webp",
    }:
        return None
    query_path = (MULTIMODAL_QUERY_DIR / query_media).resolve()
    if not query_path.is_file() or not query_path.is_relative_to(MULTIMODAL_QUERY_DIR.resolve()):
        return None
    video = resolve_video_file(video_id)
    if not video or not video.is_file():
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        duration = float(probe_video(video)["duration"])
        if duration <= 0 or duration > 180:
            return None

        reference = cv2.imread(str(query_path), cv2.IMREAD_GRAYSCALE)
        if reference is None:
            return None
        scale = min(480 / reference.shape[1], 480 / reference.shape[0])
        reference = cv2.resize(reference, (max(1, round(reference.shape[1] * scale)),
                                           max(1, round(reference.shape[0] * scale))))
        detector = cv2.ORB_create(1000)
        reference_points, reference_features = detector.detectAndCompute(reference, None)
        if reference_features is None or len(reference_points) < 100:
            return None
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video), "-vf",
             "fps=2,scale=480:270,format=gray", "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        frame_size = 480 * 270
        if decoded.returncode or not decoded.stdout or len(decoded.stdout) % frame_size:
            return None
        frames = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(-1, 270, 480)
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        best = (0, 0, None, None)
        for index, frame in enumerate(frames):
            frame_points, frame_features = detector.detectAndCompute(frame, None)
            if frame_features is None:
                continue
            pairs = matcher.knnMatch(reference_features, frame_features, k=2)
            good = [first for pair in pairs if len(pair) == 2
                    for first, second in [pair] if first.distance < .75 * second.distance]
            if len(good) > best[0]:
                best = (len(good), index, frame_points, good)
        count, index, frame_points, good = best
        if count < max(80, round(len(reference_points) * .15)):
            return None
        source_points = np.float32([reference_points[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
        target_points = np.float32([frame_points[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
        _, inlier_mask = cv2.findHomography(source_points, target_points, cv2.RANSAC, 5)
        inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
        if inliers < max(50, round(count * .5)):
            return None
        second = index / 2
        return {
            "video_id": video_id, "start_seconds": second,
            "end_seconds": min(duration, second + 2), "modality": "image",
            "search_engine": "local_frame_match", "evidence_status": "verified_visual_match",
            "visual_inliers": inliers,
            "key_excerpt": "La imagen coincide geométricamente con un fotograma del vídeo.",
        }
    except (KeyError, OSError, ValueError, subprocess.TimeoutExpired, cv2.error):
        return None
