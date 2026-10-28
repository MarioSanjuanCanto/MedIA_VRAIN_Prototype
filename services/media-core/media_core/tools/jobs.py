"""Bounded single-process jobs: orchestration and progressive audio for Hermes/UI."""
import copy
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from media_core.runtime import CancellationRequested, _cancel_event, _reporter, check_cancelled, progress

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-job")
_lock = threading.RLock()
_jobs = {}
_futures = {}
_cancel_events = {}
_cancelled_owners = {}
_MAX_ACTIVE = 4
_TTL = 3600


def _prune():
    now = time.time()
    for owner_id, cancelled_at in list(_cancelled_owners.items()):
        if now - cancelled_at > _TTL:
            del _cancelled_owners[owner_id]
    for key, value in list(_jobs.items()):
        if value["status"] in {"completed", "failed", "cancelled"} and now - value["updated_at"] > _TTL:
            del _jobs[key]
            _futures.pop(key, None)
            _cancel_events.pop(key, None)
    finished = sorted(
        (v["updated_at"], k) for k, v in _jobs.items()
        if v["status"] in {"completed", "failed", "cancelled"}
    )
    for _, key in finished[:max(0, len(_jobs) - 50)]:
        del _jobs[key]
        _futures.pop(key, None)
        _cancel_events.pop(key, None)


def _run(job_id, operation, arguments):
    def report(stage, details):
        with _lock:
            job = _jobs[job_id]
            check_cancelled(_cancel_events.get(job_id))
            job["stage"] = stage
            job["updated_at"] = time.time()
            event = {"sequence": len(job["events"]) + 1, "stage": stage, **details}
            job["events"].append(event)
    with _lock:
        if _jobs[job_id].get("cancel_requested") or _jobs[job_id]["status"] == "cancelled":
            _jobs[job_id].update(status="cancelled", stage="cancelled", updated_at=time.time())
            _futures.pop(job_id, None)
            _cancel_events.pop(job_id, None)
            return
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    token = _reporter.set(report)
    cancel_token = _cancel_event.set(_cancel_events[job_id])
    try:
        result = operation(**arguments)
        failed = isinstance(result, dict) and result.get("status") == "error"
        if isinstance(result, list):
            failed = any(x.get("status") == "error" for x in result)
        with _lock:
            if _jobs[job_id].get("cancel_requested"):
                _jobs[job_id].update(status="cancelled", stage="cancelled", updated_at=time.time())
                _jobs[job_id].pop("result", None)
            else:
                _jobs[job_id].update(status="failed" if failed else "completed", result=result, updated_at=time.time())
    except CancellationRequested:
        with _lock:
            _jobs[job_id].update(status="cancelled", stage="cancelled", updated_at=time.time())
            _jobs[job_id].pop("result", None)
    except Exception as error:
        with _lock:
            if _jobs[job_id].get("cancel_requested"):
                _jobs[job_id].update(status="cancelled", stage="cancelled", updated_at=time.time())
                _jobs[job_id].pop("result", None)
            else:
                _jobs[job_id].update(status="failed", result={"status": "error", "message": str(error)}, updated_at=time.time())
    finally:
        _reporter.reset(token)
        _cancel_event.reset(cancel_token)
        with _lock:
            _futures.pop(job_id, None)
            _cancel_events.pop(job_id, None)


def _submit(operation, arguments, owner_id=""):
    with _lock:
        _prune()
        if owner_id and owner_id in _cancelled_owners:
            return {"status": "cancelled", "owner_id": owner_id}
        # Avoid duplicate expensive work when an agent repeats a request.
        for key, job in _jobs.items():
            if (job["status"] in {"queued", "running"}
                    and job["operation"] == operation.__name__
                    and job["arguments"] == arguments
                    and job.get("owner_id", "") == owner_id):
                return {"status": job["status"], "job_id": key, "deduplicated": True, "poll_after_seconds": 2}
        if sum(j["status"] in {"queued", "running"} for j in _jobs.values()) >= _MAX_ACTIVE:
            return {"status": "error", "message": "Cola llena. Espera a que termine un trabajo."}
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {"status": "queued", "operation": operation.__name__, "arguments": arguments,
            "owner_id": owner_id, "created_at": time.time(), "updated_at": time.time(),
            "events": [], "stage": "queued"}
        _cancel_events[job_id] = threading.Event()
        _futures[job_id] = _pool.submit(_run, job_id, operation, arguments)
        return {"status": "queued", "job_id": job_id, "poll_after_seconds": 2}


def media_job_status(job_id: str, after_sequence: int = 0) -> dict:
    """Consulta un trabajo. Los eventos audio_ready se pueden reproducir antes del final.

    Consulta cada 2 segundos mientras está activo; al terminar no sigas consultando.
    Los trabajos son locales a este proceso y caducan una hora después de terminar.
    """
    with _lock:
        _prune()
        if job_id not in _jobs:
            return {"status": "error", "message": "Trabajo desconocido, caducado o servidor reiniciado."}
        job = copy.deepcopy(_jobs[job_id])
    job.pop("arguments", None)
    job["events"] = [e for e in job["events"] if e["sequence"] > after_sequence]
    job["job_id"] = job_id
    job["elapsed_seconds"] = round((time.time() if job["status"] in {"queued", "running", "cancelling"} else job["updated_at"]) - job["created_at"], 3)
    return job


def _request_cancel(job_id, job):
    if job["status"] in {"completed", "failed", "cancelled"}:
        return False
    job["cancel_requested"] = True
    job["updated_at"] = time.time()
    _cancel_events.setdefault(job_id, threading.Event()).set()
    future = _futures.get(job_id)
    if job["status"] == "queued" and future is not None and future.cancel():
        job.update(status="cancelled", stage="cancelled")
        _futures.pop(job_id, None)
        _cancel_events.pop(job_id, None)
    else:
        job.update(status="cancelling", stage="cancelling")
    return True


def media_job_cancel(job_id: str = "", owner_id: str = "") -> dict:
    """Cancel one background job without affecting the server or other jobs.

    Queued work is removed from the executor immediately. Running work changes to
    ``cancelling`` and stops at its next progress checkpoint; a late result is
    always discarded, so cancellation cannot resurrect a completed response.
    """
    with _lock:
        _prune()
        if not job_id and not owner_id:
            return {"status": "error", "message": "Indica job_id u owner_id."}
        if owner_id:
            _cancelled_owners[owner_id] = time.time()
        matching = [
            key for key, candidate in _jobs.items()
            if (job_id and key == job_id) or (owner_id and candidate.get("owner_id") == owner_id)
        ]
        cancelled_job_ids = [key for key in matching if _request_cancel(key, _jobs[key])]
        if owner_id and (not job_id or job_id not in _jobs):
            return {
                "status": "cancelled", "owner_id": owner_id,
                "cancelled_job_ids": cancelled_job_ids,
            }
        job = _jobs.get(job_id)
        if job is None:
            return {"status": "error", "message": "Trabajo desconocido, caducado o servidor reiniciado."}
    state = media_job_status(job_id)
    state["cancel_requested"] = state.get("cancel_requested", False)
    return state


def _process(source, query, language, diarization, max_clips, selection_mode):
    from media_core.tools.transcribe import media_transcribe
    from media_core.tools.clips import media_clips
    progress("transcription")
    transcript = media_transcribe(source=source, language=language, diarization=diarization, index_transcript=False)
    if transcript.get("status") == "error":
        return transcript
    progress("selecting_clips")
    clips = media_clips(video_id=source, query=query, max_clips=max_clips, selection_mode=selection_mode)
    if any(c.get("status") == "error" for c in clips):
        return {"status": "error", "clips": clips, "transcription": transcript}
    return {"status": "success", "transcription": transcript, "clips": clips}


def media_process(source: str, query: str = "", language: str = "es", diarization: bool = False,
                  max_clips: int = 1, selection_mode: str = "auto") -> dict:
    """Empieza vídeo→transcripción reutilizable→clips en UNA llamada; devuelve job_id enseguida.

    Preferir esta herramienta para 'coge este vídeo y saca clips'. No llamar primero a
    transcribe/search/clips. Usa media_job_status para progreso y resultado. Sin query
    usa selección editorial; con query, auto prueba primero coincidencias textuales
    y recurre a editorial solo cuando hacen falta.
    La diarización es opcional y más lenta. No genera embeddings para sacar clips.
    """
    if not 1 <= max_clips <= 10 or selection_mode not in {"auto", "fast", "editorial"}:
        return {"status": "error", "message": "max_clips 1..10; selection_mode auto/fast/editorial"}
    return _submit(_process, dict(source=source, query=query, language=language, diarization=diarization,
                                 max_clips=max_clips, selection_mode=selection_mode))


def split_speech(text, limit=240):
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    chunks = []
    for sentence in sentences:
        # Split long sentences on whitespace; preserve all text, including long tokens.
        while len(sentence) > limit:
            split = sentence.rfind(" ", 0, limit + 1)
            if split < 1:
                split = limit
            chunks.append(sentence[:split].strip())
            sentence = sentence[split:].strip()
        if sentence:
            chunks.append(sentence)
    return chunks


def _speak(text, video_id, language, speaker, provider, speed_factor):
    from media_core.tools.summarize import media_summarize
    from media_core.tools.tts import media_tts
    if not text.strip():
        progress("summary")
        summary = media_summarize(video_id=video_id, language=language)
        if summary.get("status") == "error":
            return summary
        text = summary["executive_summary"]
    chunks = split_speech(text)
    if len(chunks) > 256:
        return {"status": "error", "message": "Demasiadas frases; limita la solicitud a 256 fragmentos."}
    results = []
    offset = 0.0
    for index, chunk in enumerate(chunks):
        progress("synthesizing", chunk_index=index, total_chunks=len(chunks))
        audio = media_tts(text=chunk, language=language, speaker=speaker, provider=provider,
                          speed_factor=speed_factor, format_mp3=False)
        if audio.get("status") == "error":
            return {**audio, "completed_chunks": results}
        item = {"chunk_index": index, "text": chunk, "start_seconds": round(offset, 3), **audio}
        results.append(item)
        progress("audio_ready", **item)
        offset += audio["duration_seconds"]
    return {"status": "success", "chunks": results, "duration_seconds": round(offset, 3),
            "streaming_mode": "sentence_files", "visemes_available": False}


def media_speak(text: str = "", video_id: str = "", language: str = "es", speaker: str = "",
                provider: str = "auto", speed_factor: float = 1.0) -> dict:
    """Voz progresiva para futuro frontend/avatar. Devuelve job_id; consulta audio_ready.

    Genera WAV por frases ordenadas, reproducibles mientras se generan las siguientes.
    Incluye tiempos de audio; no ofrece sincronización fonética ni visemas todavía.
    """
    if (not text.strip() and not video_id) or len(text) > 20000:
        return {"status": "error", "message": "Indica text (máximo 20000 caracteres) o video_id."}
    return _submit(_speak, dict(text=text, video_id=video_id, language=language, speaker=speaker,
                               provider=provider, speed_factor=speed_factor))
