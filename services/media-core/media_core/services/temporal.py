"""Resumable, source-bound temporal video analysis."""

import base64
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import RLock
import uuid

from media_core.config import CACHE_DIR, KEYFRAMES_DIR, TEMPORAL_CACHE_DIR, get_openrouter_key
from media_core.runtime import progress
from media_core.services.chroma import ChromaVectorStore
from media_core.services.openrouter import call_openrouter
from media_core.services.scene_detection import probe_video
from media_core.services.video_ocr import extract_ocr_from_frame
from media_core.services.vlm import DEFAULT_VISION_MODEL
from media_core.storage import file_fingerprint, load_test_vector, resolve_video_file

SCHEMA_VERSION = 1
CHUNK_SECONDS = 20.0
OVERLAP_SECONDS = 2.0
SAMPLE_FPS = 1.0
_LOCK = RLock()


class TemporalAnalysisFailure(RuntimeError):
    def __init__(self, message: str, cost_usd: float, unmetered_attempts: int = 0):
        super().__init__(message)
        self.cost_usd = cost_usd
        self.unmetered_attempts = unmetered_attempts


def _unmetered_attempts(chunk: dict) -> int:
    if "unmetered_attempts" in chunk:
        return max(0, int(chunk["unmetered_attempts"]))
    # Manifests written before this counter existed used null cost for one or
    # more requests whose charge could not be verified.
    return 1 if chunk.get("attempted") and chunk.get("cost_usd") is None else 0


def _video(video_id: str) -> tuple[Path, str]:
    path = resolve_video_file(video_id)
    if not path or not path.is_file():
        raise ValueError(f"No se encontró el vídeo '{video_id}'.")
    clean_id = re.sub(r"_demo$", "", path.stem, flags=re.I).upper()
    return path, clean_id


def _manifest_path(clean_id: str) -> Path:
    return TEMPORAL_CACHE_DIR / f"{clean_id}.json"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _source_stat(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _new_manifest(path: Path, clean_id: str, model: str) -> dict:
    duration = float(probe_video(path)["duration"])
    if duration <= 0:
        raise ValueError("No se pudo determinar la duración del vídeo.")
    chunks = []
    start = 0.0
    while start < duration:
        end = min(duration, start + CHUNK_SECONDS)
        chunks.append({"index": len(chunks), "start_seconds": round(start, 3),
                       "end_seconds": round(end, 3), "status": "pending"})
        if end >= duration:
            break
        start = end - OVERLAP_SECONDS
    return {"schema_version": SCHEMA_VERSION, "video_id": clean_id,
            "source": file_fingerprint(path), "source_stat": _source_stat(path),
            "model": model, "duration_seconds": duration, "sample_fps": SAMPLE_FPS,
            "chunk_seconds": CHUNK_SECONDS, "overlap_seconds": OVERLAP_SECONDS,
            "input_mode": "native", "chunks": chunks}


def _get_manifest(path: Path, clean_id: str, model: str, force: bool = False) -> dict:
    target = _manifest_path(clean_id)
    existing = None if force else _read_json(target)
    if (existing and existing.get("schema_version") == SCHEMA_VERSION
            and existing.get("source_stat") == _source_stat(path)
            and existing.get("source") == file_fingerprint(path)
            and existing.get("model") == model
            and existing.get("sample_fps") == SAMPLE_FPS
            and existing.get("chunk_seconds") == CHUNK_SECONDS):
        return existing
    result = _new_manifest(path, clean_id, model)
    _write_json(target, result)
    try:
        _store().collection.delete(where={"video_id": clean_id})
    except Exception:
        pass
    return result


def _summary(manifest: dict) -> dict:
    chunks = manifest["chunks"]
    done = sum(chunk.get("status") == "completed" for chunk in chunks)
    failed = sum(chunk.get("status") == "failed" for chunk in chunks)
    total = len(chunks)
    status = "completed" if done == total else "failed" if failed else "processing"
    cost = sum(float(chunk.get("cost_usd") or 0) for chunk in chunks)
    unknown = sum(_unmetered_attempts(chunk) for chunk in chunks)
    cost_type = ("unavailable" if unknown
                 else "estimated" if any(chunk.get("cost_type") == "estimated" for chunk in chunks)
                 else "actual")
    return {"status": status, "video_id": manifest["video_id"],
            "duration_seconds": manifest["duration_seconds"], "completed_chunks": done,
            "failed_chunks": failed, "total_chunks": total,
            "progress_percent": round(done / total * 100, 1) if total else 0,
            "cost_usd": round(cost, 6), "cost_type": cost_type,
            "unmetered_attempts": unknown,
            "model_used": manifest["model"],
            "sample_fps": manifest["sample_fps"], "input_mode": manifest["input_mode"],
            "coverage_complete": done == total}


def index_status(video_id: str) -> dict:
    path, clean_id = _video(video_id)
    manifest = _read_json(_manifest_path(clean_id))
    if not manifest:
        return {"status": "not_started", "video_id": clean_id, "coverage_complete": False,
                "provider_ready": bool(get_openrouter_key())}
    if (manifest.get("source_stat") != _source_stat(path)
            or manifest.get("source") != file_fingerprint(path)):
        return {"status": "stale", "video_id": clean_id, "coverage_complete": False,
                "provider_ready": bool(get_openrouter_key())}
    return {**_summary(manifest), "provider_ready": bool(get_openrouter_key())}


def _run_ffmpeg(command: list[str], timeout: float) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"FFmpeg no pudo preparar el tramo: {result.stderr[-500:]}")


def _sample_frames(path: Path, clean_id: str, chunk_index: int, start: float,
                   end: float, fps: float) -> list[tuple[float, Path]]:
    dest = KEYFRAMES_DIR / clean_id
    dest.mkdir(parents=True, exist_ok=True)
    prefix = f"temporal_{chunk_index:04d}_{int(fps)}fps"
    for old in dest.glob(f"{prefix}_*.jpg"):
        old.unlink(missing_ok=True)
    pattern = str(dest / f"{prefix}_%04d.jpg")
    _run_ffmpeg(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                 "-i", str(path), "-t", str(end - start), "-an", "-vf",
                 f"fps={fps},scale=-2:540", "-q:v", "4", pattern],
                timeout=max(90, (end - start) * 8))
    files = sorted(dest.glob(f"{prefix}_*.jpg"))
    if not files:
        raise RuntimeError("FFmpeg no extrajo ningún fotograma del tramo.")
    return [(round(min(end, start + i / fps), 3), image) for i, image in enumerate(files)]


def _speech(path: Path, clean_id: str, start: float, end: float) -> str:
    transcript = load_test_vector(clean_id) or {}
    if (transcript.get("request_metadata") or {}).get("source") != file_fingerprint(path):
        # A canonical test vector may have the same ID as a replacement upload.
        # Prefer the source-bound transcription cache in that case.
        transcript = _read_json(CACHE_DIR / f"{path.stem}_whisperx.json") or {}
        if (transcript.get("request_metadata") or {}).get("source") != file_fingerprint(path):
            return ""
    pieces = []
    for segment in transcript.get("segments", []):
        try:
            seg_start = float(segment.get("start_seconds", 0))
            seg_end = float(segment.get("end_seconds", seg_start))
        except (ValueError, TypeError):
            continue
        if seg_end >= start and seg_start <= end and segment.get("text"):
            pieces.append(f"+{max(0.0, seg_start - start):.1f}s {segment.get('speaker', 'Hablante')}: {segment['text']}")
    return "\n".join(pieces)[:2200]


def _ocr(frames: list[tuple[float, Path]]) -> list[dict]:
    result = []
    seen = set()
    for timestamp, image in frames:
        try:
            blocks = extract_ocr_from_frame(image)
        except Exception:
            return result
        for block in blocks:
            key = block["text"].casefold()
            if key not in seen:
                result.append({"timestamp": timestamp, "text": block["text"],
                               "region": block["region"], "frame": image.name})
                seen.add(key)
    return result


def _parse_response(raw: str, duration: float) -> dict:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(candidate)
    if not isinstance(value, dict) or not isinstance(value.get("summary"), str) or not value["summary"].strip():
        raise ValueError("El modelo no devolvió un resumen estructurado válido.")

    def strings(field: object, limit: int, width: int) -> list[str]:
        if isinstance(field, str):
            field = [field]
        if not isinstance(field, list):
            return []
        return [item.strip()[:width] for item in field if isinstance(item, str) and item.strip()][:limit]

    events = []
    raw_events = value.get("events")
    for item in raw_events if isinstance(raw_events, list) else []:
        if not isinstance(item, dict) or not str(item.get("action", "")).strip():
            continue
        try:
            start = max(0.0, min(duration, float(item.get("start_seconds", 0))))
            end = max(start, min(duration, float(item.get("end_seconds", start))))
        except (TypeError, ValueError):
            continue
        events.append({"start_seconds": round(start, 2), "end_seconds": round(end, 2),
                       "action": str(item["action"]).strip()[:350],
                       "subjects": strings(item.get("subjects"), 8, 120),
                       "objects": strings(item.get("objects"), 8, 120),
                       "visible_text": strings(item.get("visible_text"), 8, 200)})
    return {"summary": value["summary"].strip()[:1200], "events": events,
            "answer": str(value.get("answer", "")).strip()[:1200]}


def _cost(usage: dict, cached: bool) -> tuple[float | None, str]:
    if cached:
        return 0.0, "cached"
    try:
        if usage.get("cost") is not None:
            return float(usage["cost"]), "actual"
        if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
            input_rate = float(os.getenv("MEDIA_TEMPORAL_INPUT_USD_PER_M", "0.09"))
            output_rate = float(os.getenv("MEDIA_TEMPORAL_OUTPUT_USD_PER_M", "0.34"))
            return (float(usage["prompt_tokens"]) * input_rate +
                    float(usage["completion_tokens"]) * output_rate) / 1_000_000, "estimated"
    except (TypeError, ValueError):
        pass
    return None, "unavailable"


def _model_request(path: Path, clean_id: str, chunk_index: int, start: float, end: float,
                   frames: list[tuple[float, Path]], speech: str, ocr: list[dict],
                   model: str, mode: str, question: str = "") -> dict | None:
    duration = end - start
    task = ("Describe únicamente lo observable en el tramo. No inventes identidades ni causas. "
            "Usa tiempos relativos al inicio del tramo (0 a %.2f s). Devuelve JSON con "
            "summary (texto), events (lista de {start_seconds,end_seconds,action,subjects,objects,visible_text}) "
            "y answer (texto, si hay pregunta). Registra cambios y acciones concretas; si no hay, events puede ser []. "
            "Una persona solo tiene nombre si aparece en un rótulo o la transcripción lo identifica. "
            "Pregunta concreta: %s\nTranscripción con tiempos relativos: %s\nOCR orientativo: %s"
            % (duration, question or "ninguna", speech or "no disponible",
               "; ".join(f"+{x['timestamp'] - start:.1f}s {x['text']}" for x in ocr)[:1200] or "no disponible"))
    content = [{"type": "text", "text": task}]
    if mode == "native":
        with tempfile.TemporaryDirectory(dir=TEMPORAL_CACHE_DIR) as temporary:
            clip = Path(temporary) / "segment.mp4"
            try:
                _run_ffmpeg(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                             "-i", str(path), "-t", str(duration), "-an", "-vf", "fps=1,scale=-2:540",
                             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", str(clip)],
                            timeout=max(90, duration * 8))
                encoded = base64.b64encode(clip.read_bytes()).decode("ascii")
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                return {"_invalid": True, "cost_usd": 0.0, "cost_type": "local"}
        content.append({"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + encoded}})
    else:
        for timestamp, image in frames:
            content.append({"type": "text", "text": f"Fotograma en +{timestamp - start:.1f} s"})
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(image.read_bytes()).decode("ascii")}})
    response = call_openrouter(messages=[{"role": "user", "content": content}], model=model,
                               json_mode=True, temperature=0.1, max_tokens=1400,
                               timeout_seconds=150, return_metadata=True)
    if not isinstance(response, dict):
        return None
    cost, cost_type = ((0.0, "rejected") if response.get("request_rejected") else
                       _cost(response.get("usage") or {}, bool(response.get("cache_hit"))))
    if not response.get("content"):
        return {"_invalid": True, "cost_usd": cost, "cost_type": cost_type}
    try:
        parsed = _parse_response(response["content"], duration)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"_invalid": True, "cost_usd": cost, "cost_type": cost_type}
    return {**parsed, "input_mode": mode, "model_used": response.get("model") or model,
            "cost_usd": cost, "cost_type": cost_type}


def _analyze(path: Path, clean_id: str, chunk: dict, model: str, mode: str,
             fps: float = SAMPLE_FPS, question: str = "") -> dict:
    start, end = chunk["start_seconds"], chunk["end_seconds"]
    frames = _sample_frames(path, clean_id, chunk["index"], start, end, fps)
    ocr = _ocr(frames)
    speech = _speech(path, clean_id, start, end)
    result = _model_request(path, clean_id, chunk["index"], start, end, frames,
                            speech, ocr, model, mode, question)
    first_result = result
    attempts = [result]
    if (result is None or result.get("_invalid")) and mode == "native":
        result = _model_request(path, clean_id, chunk["index"], start, end, frames,
                                speech, ocr, model, "frames", question)
        attempts.append(result)
    costs = [attempt.get("cost_usd") if attempt else None for attempt in attempts]
    total_cost = round(sum(cost for cost in costs if cost is not None), 8)
    unmetered = sum(cost is None for cost in costs)
    if result is None or result.get("_invalid"):
        raise TemporalAnalysisFailure("El VLM no devolvió un análisis válido del tramo.",
                                      total_cost, unmetered)
    result["cost_usd"] = total_cost
    result["unmetered_attempts"] = unmetered
    result["cost_type"] = ("unavailable" if unmetered else
                           "estimated" if any(attempt.get("cost_type") == "estimated" for attempt in attempts if attempt)
                           else "actual")
    result["preferred_input_mode"] = ("frames" if mode == "native" and
                                       first_result is not None and
                                       first_result.get("cost_type") in {"rejected", "local"} and
                                       result["input_mode"] == "frames" else mode)
    events = []
    for event in result["events"]:
        absolute_start = round(start + event["start_seconds"], 2)
        nearest = min(frames, key=lambda frame: abs(frame[0] - absolute_start))
        events.append({**event, "start_seconds": round(start + event["start_seconds"], 2),
                       "end_seconds": round(start + event["end_seconds"], 2),
                       "keyframe_path": f"keyframes/{clean_id}/{nearest[1].name}"})
    return {**result, "events": events, "ocr": ocr,
            "speech_excerpt": speech[:1200],
            "evidence_frames": [{"timestamp": ts, "keyframe_path": f"keyframes/{clean_id}/{image.name}"}
                                for ts, image in frames]}


@lru_cache(maxsize=1)
def _store() -> ChromaVectorStore:
    return ChromaVectorStore(collection_name="media_temporal_events_v1")


def _index_chunk(manifest: dict, chunk: dict) -> None:
    store = _store()
    document = " ".join([chunk.get("summary", ""),
                         *[event["action"] + " " + " ".join(event["subjects"] + event["objects"] + event["visible_text"])
                           for event in chunk.get("events", [])],
                         *[item["text"] for item in chunk.get("ocr", [])]])[:6000]
    store.collection.upsert(ids=[f"{manifest['video_id']}_{chunk['index']:05d}"],
                            documents=[document], embeddings=store.embedder.embed_documents([document]),
                            metadatas=[{"video_id": manifest["video_id"], "index": chunk["index"],
                                        "start_seconds": chunk["start_seconds"],
                                        "end_seconds": chunk["end_seconds"],
                                        "source_sha256": manifest["source"]["sha256"]}])


def _ensure_index(manifest: dict) -> None:
    store = _store()
    current = store.collection.get(where={"video_id": manifest["video_id"]}, include=["metadatas"])
    existing = {point_id: metadata for point_id, metadata in zip(current["ids"], current["metadatas"])}
    for chunk in manifest["chunks"]:
        point_id = f"{manifest['video_id']}_{chunk['index']:05d}"
        if (chunk.get("status") == "completed" and
                existing.get(point_id, {}).get("source_sha256") != manifest["source"]["sha256"]):
            _index_chunk(manifest, chunk)


def index_next(video_id: str, max_chunks: int = 1, force: bool = False,
               retry_failed: bool = False, max_cost_usd: float = 3.0) -> dict:
    if not 1 <= max_chunks <= 10:
        return {"status": "error", "message": "max_chunks debe estar entre 1 y 10."}
    if not get_openrouter_key():
        return {"status": "error", "message": "Falta OPENROUTER_API_KEY para analizar el vídeo."}
    path, clean_id = _video(video_id)
    model = os.getenv("OPENROUTER_VISION_MODEL", DEFAULT_VISION_MODEL)
    with _LOCK:
        manifest = _get_manifest(path, clean_id, model, force)
        if retry_failed:
            for chunk in manifest["chunks"]:
                if chunk["status"] == "failed":
                    chunk["status"] = "pending"
        processed = 0
        for chunk in manifest["chunks"]:
            if chunk["status"] != "pending" or processed >= max_chunks:
                continue
            spent = _summary(manifest)["cost_usd"]
            if spent >= max_cost_usd:
                _write_json(_manifest_path(clean_id), manifest)
                return {**_summary(manifest), "status": "budget_exhausted",
                        "message": f"El análisis alcanzó el límite de {max_cost_usd:.2f} US$."}
            unknown = sum(_unmetered_attempts(item) for item in manifest["chunks"])
            unknown_limit = max(0, int(os.getenv("MEDIA_TEMPORAL_MAX_UNMETERED_REQUESTS", "3")))
            if (unknown >= unknown_limit and
                    os.getenv("MEDIA_TEMPORAL_ALLOW_UNMETERED", "false").lower() != "true"):
                return {**_summary(manifest), "status": "cost_unavailable",
                        "message": f"Hay {unknown} peticiones sin cargo verificable; se detuvo el análisis antes del siguiente tramo."}
            progress("temporal_chunk", video_id=clean_id, chunk_index=chunk["index"],
                     total_chunks=len(manifest["chunks"]))
            try:
                result = _analyze(path, clean_id, chunk, model, manifest["input_mode"])
                preferred_mode = result.pop("preferred_input_mode", result["input_mode"])
                previous_cost = chunk.get("cost_usd") or 0.0
                result["cost_usd"] = round(previous_cost + result["cost_usd"], 8)
                result["unmetered_attempts"] = (_unmetered_attempts(chunk) +
                                                 result.get("unmetered_attempts", 0))
                chunk.update(result, status="completed")
                chunk["attempted"] = True
                manifest["input_mode"] = preferred_mode
                chunk.pop("error", None)
                _write_json(_manifest_path(clean_id), manifest)
                try:
                    _index_chunk(manifest, chunk)
                except Exception as index_error:
                    chunk["index_warning"] = str(index_error)[:300]
                    _write_json(_manifest_path(clean_id), manifest)
            except Exception as error:
                # Local FFmpeg/OCR/index preparation failures did not send a
                # billable model request. A TemporalAnalysisFailure carries
                # None when a sent request's charge is genuinely unknown.
                incurred = getattr(error, "cost_usd", 0.0)
                previous_cost = chunk.get("cost_usd") or 0.0
                chunk["cost_usd"] = round(previous_cost + incurred, 8)
                chunk["unmetered_attempts"] = (_unmetered_attempts(chunk) +
                                                 getattr(error, "unmetered_attempts", 0))
                chunk.update(status="failed", attempted=True, error=str(error)[:500])
                _write_json(_manifest_path(clean_id), manifest)
                return {**_summary(manifest), "status": "failed", "message": str(error)[:500],
                        "failed_chunk_index": chunk["index"]}
            processed += 1
        return _summary(manifest)


def search_events(query: str, video_id: str, top_k: int = 5,
                  all_occurrences: bool = False) -> dict:
    if not query.strip():
        return {"status": "error", "message": "Indica qué acción, persona u objeto quieres buscar."}
    path, clean_id = _video(video_id)
    manifest = _read_json(_manifest_path(clean_id))
    if (not manifest or manifest.get("source_stat") != _source_stat(path)
            or manifest.get("source") != file_fingerprint(path)):
        return {"status": "not_indexed", "video_id": clean_id, "results": [],
                "message": "Este vídeo aún no tiene un índice temporal vigente."}
    _ensure_index(manifest)
    store = _store()
    completed = {chunk["index"]: chunk for chunk in manifest["chunks"] if chunk["status"] == "completed"}
    if not completed:
        return {"status": "processing", "video_id": clean_id, "results": [],
                "coverage_complete": False}
    count = len(completed) if all_occurrences else min(max(1, top_k * 3), len(completed))
    matches = store.query(query.strip(), count, video_id=clean_id)
    results = []
    for match in matches:
        index = int(match["metadata"].get("index", -1))
        chunk = completed.get(index)
        if not chunk or match["metadata"].get("source_sha256") != manifest["source"]["sha256"]:
            continue
        score = round(1 - float(match["distance"]), 4)
        if all_occurrences and score < 0.20:
            continue
        results.append({"video_id": clean_id, "chunk_index": index,
                        "start_seconds": chunk["start_seconds"], "end_seconds": chunk["end_seconds"],
                        "summary": chunk.get("summary", ""), "events": chunk.get("events", []),
                        "ocr": chunk.get("ocr", []), "evidence_frames": chunk.get("evidence_frames", [])[:3],
                        "semantic_score": score, "match_type": "temporal_candidate"})
    if all_occurrences:
        results.sort(key=lambda item: item["start_seconds"])
    else:
        results = results[:max(1, min(top_k, 50))]
    return {"status": "success", "video_id": clean_id, "query": query,
            "coverage_complete": _summary(manifest)["coverage_complete"],
            "sample_fps": SAMPLE_FPS, "results": results,
            "note": "Son candidatos del índice; verifica el fragmento antes de afirmar presencia, ausencia o recuentos exactos. Los tramos solapados pueden duplicar una aparición."}


def describe_segment(video_id: str, start_seconds: float, end_seconds: float,
                     prompt: str = "", fps: float = 3.0) -> dict:
    path, clean_id = _video(video_id)
    if start_seconds < 0 or end_seconds <= start_seconds or end_seconds - start_seconds > 12:
        return {"status": "error", "message": "Indica un intervalo válido de hasta 12 segundos."}
    if not 1 <= fps <= 5:
        return {"status": "error", "message": "fps debe estar entre 1 y 5."}
    duration = float(probe_video(path)["duration"])
    if end_seconds > duration:
        return {"status": "error", "message": "El intervalo supera la duración del vídeo."}
    model = os.getenv("OPENROUTER_VISION_MODEL", DEFAULT_VISION_MODEL)
    interval_key = int(hashlib.sha256(f"{start_seconds:.3f}:{end_seconds:.3f}".encode()).hexdigest()[:10], 16)
    chunk = {"index": interval_key + 1_000_000, "start_seconds": start_seconds,
             "end_seconds": end_seconds}
    try:
        result = _analyze(path, clean_id, chunk, model, "frames", fps, prompt)
    except Exception as error:
        return {"status": "error", "message": str(error)[:500]}
    return {"status": "success", "video_id": clean_id, "start_seconds": start_seconds,
            "end_seconds": end_seconds, "sample_fps": fps, **result}
