"""Exhaustive, resumable visual search across a complete video timeline."""

import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from media_core.config import DEFAULT_LLM_MODEL, OUTPUT_CLIPS_DIR, VISUAL_SWEEPS_DIR, get_openrouter_key
from media_core.runtime import CancellationRequested, check_cancelled, current_cancel_event, progress
from media_core.services.ffmpeg import cut_clip
from media_core.services.openrouter import call_openrouter
from media_core.services.scene_detection import probe_video
from media_core.storage import file_fingerprint, resolve_video_file

PIPELINE_VERSION = 1


def build_chunks(duration: float, chunk_seconds: float, overlap_seconds: float = 2.0) -> list[dict]:
    """Cover the full timeline with bounded, slightly overlapping chunks."""
    if duration <= 0 or chunk_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= chunk_seconds:
        raise ValueError("Duración o tamaño de bloque inválido.")
    step = chunk_seconds - overlap_seconds
    chunks = []
    start = 0.0
    while start < duration:
        end = min(duration, start + chunk_seconds)
        chunks.append({"index": len(chunks), "start_seconds": round(start, 3), "end_seconds": round(end, 3)})
        if end >= duration:
            break
        start += step
    return chunks


def merge_occurrences(items: list[dict], duration: float, gap_seconds: float = 2.0) -> list[dict]:
    """Validate model timestamps and merge duplicate/overlapping sightings."""
    clean = []
    for item in items:
        try:
            start = max(0.0, min(duration, float(item["start_seconds"])))
            end = max(start, min(duration, float(item["end_seconds"])))
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        clean.append({**item, "start_seconds": round(start, 3), "end_seconds": round(end, 3)})
    clean.sort(key=lambda item: (item["start_seconds"], item["end_seconds"]))
    merged = []
    for item in clean:
        if merged and item["start_seconds"] <= merged[-1]["end_seconds"] + gap_seconds:
            previous = merged[-1]
            previous["end_seconds"] = max(previous["end_seconds"], item["end_seconds"])
            descriptions = [value for value in (previous.get("description"), item.get("description")) if value]
            previous["description"] = " ".join(dict.fromkeys(descriptions))
            previous["evidence_frames"] = sorted(set(previous.get("evidence_frames", []) + item.get("evidence_frames", [])))
            previous["confidence"] = max(float(previous.get("confidence", 0)), float(item.get("confidence", 0)))
        else:
            merged.append(dict(item))
    return merged


def _json_object(raw: str | None) -> dict:
    if not raw:
        raise RuntimeError("El VLM no devolvió respuesta. Comprueba OPENROUTER_API_KEY y vuelve a intentarlo.")
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0]
    values = []
    try:
        values.append(json.loads(candidate))
    except ValueError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(candidate):
            if character not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
                values.append(value)
                break
            except ValueError:
                continue
    if not values:
        raise RuntimeError("El VLM devolvió un JSON inválido.")
    value = values[0]
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        if all(isinstance(item, int) for item in value):
            return {"selected_candidate_ids": value}
        if all(isinstance(item, dict) for item in value):
            key = "occurrences" if any("start_seconds" in item for item in value) else "frames"
            return {key: value, "frames" if key == "occurrences" else "occurrences": []}
    raise RuntimeError("El VLM devolvió una estructura JSON incompatible.")


def _extract_frames(video: Path, chunk: dict, interval: float, directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("frame_*.jpg"):
        old.unlink(missing_ok=True)
    length = chunk["end_seconds"] - chunk["start_seconds"]
    pattern = directory / "frame_%04d.jpg"
    command = [
        "ffmpeg", "-y", "-ss", str(chunk["start_seconds"]), "-i", str(video), "-t", str(length),
        "-vf", f"fps=1/{interval},scale=-2:480", "-q:v", "3", str(pattern),
    ]
    result = subprocess.run(command, capture_output=True, timeout=max(120, length * 3))
    if result.returncode != 0:
        raise RuntimeError("FFmpeg no pudo extraer los fotogramas del bloque.")
    return [
        {"path": path, "timestamp": round(min(chunk["end_seconds"], chunk["start_seconds"] + index * interval), 3)}
        for index, path in enumerate(sorted(directory.glob("frame_*.jpg")))
    ]


def _analyze_chunk(video: Path, video_id: str, query: str, chunk: dict, interval: float,
                   directory: Path, cancel_event=None) -> dict:
    check_cancelled(cancel_event)
    cache = directory / "result.json"
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("status") == "success":
                cached["cache_hit"] = True
                check_cancelled(cancel_event)
                return cached
        except (OSError, ValueError):
            pass
    frames = _extract_frames(video, chunk, interval, directory / "frames")
    check_cancelled(cancel_event)
    if not frames:
        raise RuntimeError("El bloque no produjo fotogramas.")
    content = [{"type": "text", "text": (
        f"Vídeo {video_id}. Analiza únicamente el intervalo {chunk['start_seconds']:.1f}-"
        f"{chunk['end_seconds']:.1f} s. Consulta: {query}. Cada imagen va precedida por su tiempo absoluto. "
        "Describe cada muestra y localiza TODAS las apariciones relacionadas con la consulta. "
        "No inventes personas, edades ni tiempos. Una edad visual es solo una estimación, indícalo en la descripción. "
        "Devuelve una entrada por imagen y no omitas muestras. JSON: {\"frames\":[{\"sample_index\":integer,"
        "\"timestamp\":number,\"description\":string,\"matches_query\":boolean,\"estimated_count\":integer}],"
        "\"occurrences\":[{\"start_seconds\":number,\"end_seconds\":number,\"description\":string,"
        "\"evidence_frames\":[number],\"confidence\":number}]}."
    )}]
    for sample_index, frame in enumerate(frames):
        content.append({"type": "text", "text": f"Muestra {sample_index}, fotograma en {frame['timestamp']:.1f} s"})
        encoded = base64.b64encode(frame["path"].read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    check_cancelled(cancel_event)
    raw = call_openrouter(
        messages=[
            {"role": "system", "content": "Eres un analista audiovisual exhaustivo. Responde solo JSON y conserva los tiempos proporcionados."},
            {"role": "user", "content": content},
        ],
        model=DEFAULT_LLM_MODEL,
        json_mode=True,
        temperature=0.1,
        max_tokens=3500,
        timeout_seconds=90,
        retry_delays=(2, 5, 10),
        raise_errors=True,
    )
    check_cancelled(cancel_event)
    if not raw:
        raise RuntimeError("OpenRouter devolvió una respuesta vacía o truncada para este bloque.")
    try:
        parsed = _json_object(raw)
    except RuntimeError:
        # Repair only the textual response: much cheaper than resending all images.
        check_cancelled(cancel_event)
        repaired = call_openrouter(
            messages=[
                {"role": "system", "content": (
                    "Convierte la respuesta en un único objeto JSON válido con las claves frames y occurrences. "
                    "No añadas información ni cambies números. Responde exclusivamente con JSON."
                )},
                {"role": "user", "content": raw or ""},
            ],
            model=DEFAULT_LLM_MODEL, json_mode=True, temperature=0.0, max_tokens=2500,
            timeout_seconds=90, retry_delays=(2, 5, 10), raise_errors=True,
        )
        check_cancelled(cancel_event)
        parsed = _json_object(repaired)
    available_times = [frame["timestamp"] for frame in frames]
    descriptions = {
        item.get("sample_index"): item for item in parsed.get("frames", [])
        if isinstance(item, dict) and isinstance(item.get("sample_index"), int)
    }
    frame_rows = []
    for sample_index, frame in enumerate(frames):
        description = descriptions.get(sample_index, {})
        frame_rows.append({
            "sample_index": sample_index,
            "timestamp": frame["timestamp"],
            "interval_start_seconds": frame["timestamp"],
            "interval_end_seconds": round(min(chunk["end_seconds"], frame["timestamp"] + interval), 3),
            "image_file": str(frame["path"]),
            "description": str(description.get("description", "")),
            "matches_query": bool(description.get("matches_query", False)),
            "estimated_count": max(0, int(description.get("estimated_count", 0)))
                if isinstance(description.get("estimated_count", 0), (int, float)) else 0,
        })
    occurrences = []
    for item in parsed.get("occurrences", []):
        if not isinstance(item, dict):
            continue
        evidence = [float(value) for value in item.get("evidence_frames", []) if isinstance(value, (int, float))]
        # Reject hallucinated evidence outside this chunk or far from a sampled frame.
        if not evidence or any(min(abs(value - known) for known in available_times) > interval / 2 + 0.25 for value in evidence):
            continue
        occurrences.append(item)
    result = {
        "status": "success", "chunk": chunk, "frames": frame_rows,
        "occurrences": occurrences, "sampled_frames": len(frames), "cache_hit": False,
    }
    temporary = cache.with_suffix(".tmp")
    check_cancelled(cancel_event)
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cache)
    return result


def _reduce_occurrences(query: str, occurrences: list[dict], duration: float) -> list[dict]:
    if not occurrences:
        return []
    candidates = [{**item, "candidate_id": index} for index, item in enumerate(occurrences)]
    raw = call_openrouter(
        messages=[
            {"role": "system", "content": (
                "Revisa detecciones visuales de bloques solapados. Responde solo JSON con selected_candidate_ids, "
                "una lista de enteros. Conserva todas las detecciones compatibles con la consulta y descarta solo "
                "falsos positivos claros. No generes ni modifiques tiempos."
            )},
            {"role": "user", "content": json.dumps({"query": query, "duration": duration, "candidates": candidates}, ensure_ascii=False)},
        ],
        model=DEFAULT_LLM_MODEL, json_mode=True, temperature=0.0, max_tokens=2500,
    )
    try:
        reviewed = _json_object(raw)
        if not isinstance(reviewed.get("selected_candidate_ids"), list):
            raise RuntimeError("El revisor no devolvió la selección esperada.")
        selected_ids = reviewed["selected_candidate_ids"]
    except RuntimeError:
        return merge_occurrences(occurrences, duration)
    selected = [occurrences[index] for index in selected_ids if isinstance(index, int) and 0 <= index < len(occurrences)]
    return merge_occurrences(selected, duration)


def run_visual_sweep(
    video_id: str,
    query: str,
    chunk_seconds: float = 20.0,
    sample_interval: float = 2.5,
    workers: int = 2,
    padding_seconds: float = 2.0,
    extract_clips: bool = True,
) -> dict:
    """Analyze the complete video in resumable parallel chunks and optionally cut every match."""
    cancel_event = current_cancel_event()
    check_cancelled(cancel_event)
    if not query.strip():
        return {"status": "error", "message": "Indica qué quieres localizar en todo el vídeo."}
    if not 15 <= chunk_seconds <= 180 or not 1 <= sample_interval <= 15 or not 1 <= workers <= 2:
        return {"status": "error", "message": "chunk_seconds 15..180, sample_interval 1..15 y workers 1..2."}
    if not get_openrouter_key():
        return {"status": "error", "message": "Falta OPENROUTER_API_KEY para analizar visualmente el vídeo."}
    video = resolve_video_file(video_id)
    if not video:
        return {"status": "error", "message": f"No se encontró el vídeo '{video_id}'."}
    clean_id = re.sub(r"_demo$", "", video.stem, flags=re.I).upper()
    info = probe_video(video)
    duration = info["duration"]
    chunks = build_chunks(duration, chunk_seconds)
    identity = file_fingerprint(video)
    key = hashlib.sha256(json.dumps({
        "version": PIPELINE_VERSION, "source": identity, "query": query.strip().casefold(),
        "chunk_seconds": chunk_seconds, "sample_interval": sample_interval,
    }, sort_keys=True).encode()).hexdigest()[:16]
    root = VISUAL_SWEEPS_DIR / clean_id / key
    root.mkdir(parents=True, exist_ok=True)
    progress("Preparando análisis visual completo", percent=2, completed_chunks=0, total_chunks=len(chunks))
    check_cancelled(cancel_event)
    results = []
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="visual-sweep")
    futures = {}
    try:
        futures = {
            pool.submit(
                _analyze_chunk, video, clean_id, query.strip(), chunk, sample_interval,
                root / f"chunk_{chunk['index']:04d}", cancel_event,
            ): chunk
            for chunk in chunks
        }
        pending = set(futures)
        completed = 0
        while pending:
            check_cancelled(cancel_event)
            ready, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in ready:
                results.append(future.result())
                completed += 1
                percent = 5 + round(75 * completed / len(chunks))
                progress("Analizando todo el vídeo", percent=percent, completed_chunks=completed, total_chunks=len(chunks))
    except CancellationRequested:
        for future in futures:
            future.cancel()
        # Provider/ffmpeg calls already in flight cannot be force-killed safely.
        # Do not wait for them: they check the event before publishing cache data.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    except Exception:
        for future in futures:
            future.cancel()
        # Let already-running calls finish and persist their atomic chunk cache.
        # A subsequent attempt can then resume without repeating successful work.
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    candidates = [item for result in results for item in result.get("occurrences", [])]
    check_cancelled(cancel_event)
    candidates = merge_occurrences(candidates, duration)
    progress("Consolidando coincidencias", percent=84, completed_chunks=len(chunks), total_chunks=len(chunks))
    occurrences = _reduce_occurrences(query.strip(), candidates, duration)
    check_cancelled(cancel_event)
    clips = []
    if extract_clips and occurrences:
        slug = re.sub(r"[^a-z0-9]+", "-", query.casefold()).strip("-")[:36] or "resultado"
        for index, occurrence in enumerate(occurrences, 1):
            check_cancelled(cancel_event)
            start = max(0.0, occurrence["start_seconds"] - padding_seconds)
            end = min(duration, occurrence["end_seconds"] + padding_seconds)
            output = OUTPUT_CLIPS_DIR / f"{clean_id}_visual_{slug}_{index:03d}_{key}.mp4"
            if not output.exists() and not cut_clip(video, str(start), str(end), output, precise=True):
                raise RuntimeError(f"No se pudo extraer el clip visual {index}.")
            clips.append({**occurrence, "start_seconds": round(start, 3), "end_seconds": round(end, 3), "clip_file": str(output)})
            progress("Extrayendo clips", percent=85 + round(14 * index / len(occurrences)), completed_clips=index, total_clips=len(occurrences))
            check_cancelled(cancel_event)
    manifest = {
        "status": "success", "video_id": clean_id, "query": query.strip(), "duration_seconds": duration,
        "coverage": {"start_seconds": 0, "end_seconds": duration, "chunk_count": len(chunks),
                     "sample_interval_seconds": sample_interval, "sampled_frames": sum(item.get("sampled_frames", 0) for item in results)},
        "occurrence_count": len(occurrences), "occurrences": occurrences, "clips": clips,
        "manifest_file": str(root / "manifest.json"),
    }
    check_cancelled(cancel_event)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    progress("Análisis visual completado", percent=100, completed_chunks=len(chunks), total_chunks=len(chunks))
    return manifest
