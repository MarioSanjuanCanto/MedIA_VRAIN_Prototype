import asyncio
import io
import json
import threading
import time
import urllib.error
import wave
from pathlib import Path

from media_core.tools import clips, jobs, transcribe, tts
from media_core.services import openrouter, tts as synthesis


def vector(text="El precio del combustible sube"):
    return {"id": "TV01", "language": "es", "segments": [{"text": text,
        "start": "00:00:01", "end": "00:00:10", "start_seconds": 1, "end_seconds": 10}]}


def test_fast_clips_skip_llm(monkeypatch):
    monkeypatch.setattr(clips, "resolve_video_file", lambda _: None)
    monkeypatch.setattr(clips, "load_test_vector", lambda _: vector())
    monkeypatch.setattr(clips, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(clips, "call_openrouter", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    result = clips.media_clips(query="precio combustible", cut_video_files=False)
    assert result[0]["selection_mode"] == "lexical"
    assert result[0]["start_time"] == "00:00:01"


def test_no_invented_clips_when_provider_fails(monkeypatch):
    monkeypatch.setattr(clips, "resolve_video_file", lambda _: None)
    monkeypatch.setattr(clips, "load_test_vector", lambda _: vector())
    monkeypatch.setattr(clips, "get_openrouter_key", lambda: "")
    assert clips.media_clips()[0]["status"] == "error"


def test_invalid_editorial_timestamps_are_not_cut(monkeypatch):
    monkeypatch.setattr(clips, "resolve_video_file", lambda _: None)
    monkeypatch.setattr(clips, "load_test_vector", lambda _: vector())
    monkeypatch.setattr(clips, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(clips, "call_openrouter", lambda *a, **k: '{"clips":[{"start_time":"00:01:00","end_time":"00:00:10"}]}')
    assert clips.media_clips()[0]["status"] == "error"


def test_summary_language_and_real_key(monkeypatch):
    from media_core.tools import summarize
    captured = {}
    def summarize_fake(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "executive_summary": "Bon dia."}
    monkeypatch.setattr(summarize, "media_summarize", summarize_fake)
    monkeypatch.setattr(tts, "synthesize_speech", lambda **kwargs: kwargs)
    result = tts.media_tts(video_id="TV01", language="ca")
    assert captured["language"] == "ca"
    assert result["text"] == "Bon dia."


def test_qwen_refuses_catalan_before_network(monkeypatch):
    monkeypatch.setattr(synthesis, "get_replicate_token", lambda: (_ for _ in ()).throw(AssertionError()))
    result = synthesis.synthesize_speech("Bon dia", language="ca", provider="qwen")
    assert result["status"] == "error"
    assert "Qwen" in result["message"]


def test_piper_reuses_identical_audio_and_invalidates_speed(tmp_path, monkeypatch):
    from media_core.services import piper_tts
    monkeypatch.setattr(synthesis, "OUTPUT_AUDIO_DIR", tmp_path)
    monkeypatch.setattr(piper_tts, "MODEL_DIR", tmp_path)
    calls = []
    def generate(text, language, speaker, speed, output):
        calls.append(speed)
        with wave.open(str(output), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 1600)
    monkeypatch.setattr(piper_tts, "synthesize", generate)
    first = synthesis.synthesize_speech("Hola", provider="piper")
    second = synthesis.synthesize_speech("Hola", provider="piper")
    third = synthesis.synthesize_speech("Hola", provider="piper", speed_factor=1.1)
    assert first["status"] == "success"
    assert not first["cache_hit"] and second["cache_hit"]
    assert len(calls) == 2 and third["audio_file"] != first["audio_file"]
    assert first["format"] == "wav"


def test_piper_rejects_unsupported_features():
    assert synthesis.synthesize_speech("Hola", provider="piper", reference_audio="voice.wav")["status"] == "error"
    assert synthesis.synthesize_speech("Hola", language="nonsense", provider="piper")["status"] == "error"


def test_llm_cache_invalidates_on_prompt(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"message":{"content":"answer"}}]}'
    monkeypatch.setattr(openrouter, "get_openrouter_key", lambda: "key")
    def request(*args, **kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", request)
    assert openrouter.call_openrouter([{"content": "one"}]) == "answer"
    assert openrouter.call_openrouter([{"content": "one"}]) == "answer"
    openrouter.call_openrouter([{"content": "two"}])
    assert len(calls) == 2


def test_openrouter_retries_429_with_configured_backoff(monkeypatch):
    attempts = []
    waits = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"message":{"content":"answer"}}]}'

    def request(*args, **kwargs):
        attempts.append(kwargs.get("timeout"))
        if len(attempts) < 3:
            raise urllib.error.HTTPError(
                "https://openrouter.ai", 429, "limited", {},
                io.BytesIO(b'{"error":{"message":"rate limited"}}'),
            )
        return Response()

    monkeypatch.setattr(openrouter, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", request)
    monkeypatch.setattr(openrouter.time, "sleep", waits.append)

    result = openrouter.call_openrouter(
        [{"content": "retry-429"}], timeout_seconds=90,
        retry_delays=(2, 5, 10), raise_errors=True,
    )

    assert result == "answer"
    assert attempts == [90, 90, 90]
    assert waits == [2, 5]


def test_openrouter_exposes_non_retryable_auth_error(monkeypatch):
    def request(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://openrouter.ai", 401, "unauthorized", {},
            io.BytesIO(b'{"error":{"message":"invalid key"}}'),
        )

    monkeypatch.setattr(openrouter, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", request)
    try:
        openrouter.call_openrouter(
            [{"content": "auth-error"}], retry_delays=(2, 5, 10), raise_errors=True,
        )
    except openrouter.OpenRouterError as error:
        assert error.status_code == 401
        assert "API key" in str(error)
    else:
        raise AssertionError("OpenRouterError was not raised")


def test_indexed_transcript_avoids_remote_asr(tmp_path, monkeypatch):
    source = tmp_path / "TV01.mp4"
    source.touch()
    monkeypatch.setattr(transcribe, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda _: source)
    data = vector()
    data["request_metadata"] = {"source": [str(source.resolve()), source.stat().st_size, source.stat().st_mtime_ns], "language": "es", "diarization": True}
    data["indexed_in_search"] = True
    monkeypatch.setattr(transcribe, "load_test_vector", lambda _: data)
    monkeypatch.setattr(transcribe, "extract_audio", lambda *a: (_ for _ in ()).throw(AssertionError("ASR called")))
    assert transcribe.media_transcribe(source="TV01")["cache_hit"] is True


def test_changed_media_invalidates_transcript(tmp_path, monkeypatch):
    source = tmp_path / "TV01.mp4"
    source.write_bytes(b"changed")
    data = vector()
    data["request_metadata"] = {"source": ["old", 1, 2], "language": "es", "diarization": True}
    (tmp_path / "TV01_whisperx.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(transcribe, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda _: source)
    monkeypatch.setattr(transcribe, "load_test_vector", lambda _: data)
    monkeypatch.setattr(transcribe, "get_replicate_token", lambda: "")
    assert transcribe.media_transcribe(source="TV01")["status"] == "error"


def test_jobs_return_before_completion_and_deduplicate():
    gate = threading.Event()
    def work(value):
        assert gate.wait(5)
        return {"status": "success", "value": value}
    first = jobs._submit(work, {"value": 1})
    try:
        assert jobs._submit(work, {"value": 1})["job_id"] == first["job_id"]
        assert jobs.media_job_status(first["job_id"])["status"] in {"queued", "running"}
    finally:
        gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = jobs.media_job_status(first["job_id"])
        if state["status"] == "completed": break
        time.sleep(.01)
    assert state["result"]["value"] == 1


def test_running_job_cancel_is_cooperative_and_isolated(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_futures", {}, raising=False)
    started = {"cancelled": threading.Event(), "other": threading.Event()}
    release = {"cancelled": threading.Event(), "other": threading.Event()}

    def work(name):
        started[name].set()
        assert release[name].wait(5)
        jobs.progress("checkpoint", name=name)
        return {"status": "success", "name": name}

    cancelled = jobs._submit(work, {"name": "cancelled"})
    other = jobs._submit(work, {"name": "other"})
    assert started["cancelled"].wait(2)
    assert started["other"].wait(2)

    response = jobs.media_job_cancel(cancelled["job_id"])

    assert response["status"] == "cancelling"
    assert jobs.media_job_status(other["job_id"])["status"] == "running"
    release["cancelled"].set()
    release["other"].set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        cancelled_state = jobs.media_job_status(cancelled["job_id"])
        other_state = jobs.media_job_status(other["job_id"])
        if cancelled_state["status"] == "cancelled" and other_state["status"] == "completed":
            break
        time.sleep(.01)

    assert cancelled_state["status"] == "cancelled"
    assert "result" not in cancelled_state
    assert other_state["result"]["name"] == "other"


def test_queued_job_cancel_is_terminal_and_idempotent(monkeypatch):
    class QueuedFuture:
        def __init__(self):
            self.cancel_calls = 0

        def cancel(self):
            self.cancel_calls += 1
            return True

    future = QueuedFuture()
    now = time.time()
    monkeypatch.setattr(jobs, "_jobs", {
        "queued": {
            "status": "queued", "operation": "work", "arguments": {},
            "created_at": now, "updated_at": now, "events": [], "stage": "queued",
        },
    })
    monkeypatch.setattr(jobs, "_futures", {"queued": future}, raising=False)

    first = jobs.media_job_cancel("queued")
    second = jobs.media_job_cancel("queued")

    assert first["status"] == "cancelled"
    assert second["status"] == "cancelled"
    assert future.cancel_calls == 1


def test_owner_cancel_tombstone_prevents_late_job_creation(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_futures", {})
    monkeypatch.setattr(jobs, "_cancelled_owners", {}, raising=False)
    called = threading.Event()

    cancelled = jobs.media_job_cancel(owner_id="chat:deleted")
    submitted = jobs._submit(lambda: called.set(), {}, owner_id="chat:deleted")

    assert cancelled == {
        "status": "cancelled", "owner_id": "chat:deleted",
        "cancelled_job_ids": [],
    }
    assert submitted == {"status": "cancelled", "owner_id": "chat:deleted"}
    assert not called.is_set()


def test_deduplication_never_shares_jobs_between_owners(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_futures", {})
    monkeypatch.setattr(jobs, "_cancel_events", {}, raising=False)
    monkeypatch.setattr(jobs, "_cancelled_owners", {}, raising=False)
    release = threading.Event()

    def work(value):
        release.wait(5)
        return {"status": "success", "value": value}

    first = jobs._submit(work, {"value": 1}, owner_id="chat:first")
    second = jobs._submit(work, {"value": 1}, owner_id="chat:second")
    try:
        assert first["job_id"] != second["job_id"]
        jobs.media_job_cancel(owner_id="chat:first")
        assert jobs.media_job_status(second["job_id"])["status"] in {"queued", "running"}
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        first_state = jobs.media_job_status(first["job_id"])
        second_state = jobs.media_job_status(second["job_id"])
        if first_state["status"] == "cancelled" and second_state["status"] == "completed":
            break
        time.sleep(.01)
    assert first_state["status"] == "cancelled"
    assert second_state["status"] == "completed"


def test_cancelling_jobs_do_not_consume_queue_capacity(monkeypatch):
    class PendingFuture:
        def cancel(self):
            return True

    class Pool:
        def submit(self, *args):
            return PendingFuture()

    now = time.time()
    monkeypatch.setattr(jobs, "_jobs", {
        str(index): {
            "status": "cancelling", "operation": "old", "arguments": {},
            "created_at": now, "updated_at": now, "events": [], "stage": "cancelling",
        }
        for index in range(jobs._MAX_ACTIVE)
    })
    monkeypatch.setattr(jobs, "_futures", {})
    monkeypatch.setattr(jobs, "_pool", Pool())

    submitted = jobs._submit(lambda: {"status": "success"}, {})

    assert submitted["status"] == "queued"


def test_speech_chunks_preserve_text_and_publish_progress(monkeypatch):
    from media_core.runtime import _reporter
    monkeypatch.setattr(tts, "media_tts", lambda **kw: {"status": "success", "duration_seconds": 2, "audio_file": "chunk.wav"})
    events = []
    token = _reporter.set(lambda stage, details: events.append((stage, details)))
    try:
        result = jobs._speak("Hola. Bon dia.", "", "ca", "", "piper", 1.0)
    finally:
        _reporter.reset(token)
    assert result["duration_seconds"] == 4
    assert len([x for x in events if x[0] == "audio_ready"]) == 2
    assert result["chunks"][1]["start_seconds"] == 2
    text = "paraula " * 100
    assert " ".join(jobs.split_speech(text)).split() == text.split()


def test_mcp_sync_work_does_not_block_event_loop():
    from media_core.tools import register_tools
    import media_core.tools as public
    gate = threading.Event()
    started = threading.Event()
    def slow() -> dict:
        started.set()
        gate.wait(2)
        return {"status": "success"}
    class FakeMCP:
        def tool(self):
            return lambda fn: setattr(self, "fn", fn)
    old = public.PUBLIC_TOOLS
    public.PUBLIC_TOOLS = (slow,)
    mcp = FakeMCP()
    try: register_tools(mcp)
    finally: public.PUBLIC_TOOLS = old
    async def check():
        task = asyncio.create_task(mcp.fn())
        try:
            for _ in range(100):
                if started.is_set(): break
                await asyncio.sleep(.005)
            assert started.is_set() and not task.done()
        finally:
            gate.set()
        await task
    asyncio.run(check())
