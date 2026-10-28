import json

from media_core.services import temporal


def test_manifest_covers_entire_video_with_overlap(tmp_path, monkeypatch):
    source = tmp_path / "TV05.mp4"
    source.write_bytes(b"synthetic source")
    monkeypatch.setattr(temporal, "probe_video", lambda path: {"duration": 2715.0})
    monkeypatch.setattr(temporal, "file_fingerprint", lambda path: {"algorithm": "sha256", "sha256": "abc", "size": 16})

    manifest = temporal._new_manifest(source, "TV05", "test-model")
    chunks = manifest["chunks"]

    assert chunks[0]["start_seconds"] == 0
    assert chunks[-1]["end_seconds"] == 2715
    assert len(chunks) == 151
    assert all(left["end_seconds"] - right["start_seconds"] == 2
               for left, right in zip(chunks, chunks[1:]))


def test_model_response_uses_relative_timestamps_and_rejects_invalid_json():
    parsed = temporal._parse_response(json.dumps({
        "summary": "Una persona se levanta.",
        "events": [{"start_seconds": 4, "end_seconds": 6, "action": "Se levanta",
                    "subjects": ["persona de chaqueta azul"], "objects": [], "visible_text": []}],
    }), 20)
    assert parsed["events"][0]["start_seconds"] == 4
    assert parsed["events"][0]["end_seconds"] == 6
    assert temporal._parse_response(json.dumps({"summary": "Plano estable", "events": []}), 20)["events"] == []
    try:
        temporal._parse_response("respuesta libre", 20)
    except ValueError:
        pass
    else:
        raise AssertionError("La respuesta sin JSON debe rechazarse")


def test_model_response_accepts_null_or_scalar_optional_event_fields():
    parsed = temporal._parse_response(json.dumps({
        "summary": "Una persona interviene.",
        "events": [{"start_seconds": 2, "end_seconds": 4,
                    "action": "Habla ante el pleno", "subjects": None,
                    "objects": "micrófono", "visible_text": None}],
    }), 20)

    assert parsed["events"][0]["subjects"] == []
    assert parsed["events"][0]["objects"] == ["micrófono"]
    assert parsed["events"][0]["visible_text"] == []


def test_step_checkpoints_and_resumes_without_repeating_completed_chunks(tmp_path, monkeypatch):
    source = tmp_path / "TV05.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(temporal, "TEMPORAL_CACHE_DIR", tmp_path)
    monkeypatch.setattr(temporal, "_video", lambda video_id: (source, "TV05"))
    monkeypatch.setattr(temporal, "probe_video", lambda path: {"duration": 35.0})
    monkeypatch.setattr(temporal, "file_fingerprint", lambda path: {"algorithm": "sha256", "sha256": "abc", "size": 5})
    monkeypatch.setattr(temporal, "get_openrouter_key", lambda: "test")
    monkeypatch.setattr(temporal, "_store", lambda: type("Store", (), {"collection": type("Collection", (), {"delete": lambda *a, **k: None})()})())
    monkeypatch.setattr(temporal, "_index_chunk", lambda manifest, chunk: None)
    calls = []

    def analyze(path, video_id, chunk, model, mode):
        calls.append(chunk["index"])
        return {"summary": "Escena", "events": [], "ocr": [], "evidence_frames": [],
                "input_mode": "frames", "model_used": model, "cost_usd": 0.01}

    monkeypatch.setattr(temporal, "_analyze", analyze)
    first = temporal.index_next("TV05")
    second = temporal.index_next("TV05")

    assert first["completed_chunks"] == 1
    assert first["coverage_complete"] is False
    assert second["status"] == "completed"
    assert calls == [0, 1]
    assert temporal.index_status("TV05")["coverage_complete"] is True


def test_cost_guard_stops_before_next_chunk(tmp_path, monkeypatch):
    source = tmp_path / "TV05.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(temporal, "TEMPORAL_CACHE_DIR", tmp_path)
    monkeypatch.setattr(temporal, "_video", lambda video_id: (source, "TV05"))
    monkeypatch.setattr(temporal, "probe_video", lambda path: {"duration": 35.0})
    monkeypatch.setattr(temporal, "file_fingerprint", lambda path: {"algorithm": "sha256", "sha256": "abc", "size": 5})
    monkeypatch.setattr(temporal, "get_openrouter_key", lambda: "test")
    monkeypatch.setattr(temporal, "_store", lambda: type("Store", (), {"collection": type("Collection", (), {"delete": lambda *a, **k: None})()})())
    monkeypatch.setattr(temporal, "_index_chunk", lambda manifest, chunk: None)
    monkeypatch.setattr(temporal, "_analyze", lambda *args: {
        "summary": "Escena", "events": [], "ocr": [], "evidence_frames": [],
        "input_mode": "frames", "model_used": "test-model", "cost_usd": 0.02})

    result = temporal.index_next("TV05", max_chunks=2, max_cost_usd=0.01)

    assert result["status"] == "budget_exhausted"
    assert result["completed_chunks"] == 1


def test_native_fallback_counts_both_model_requests(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    monkeypatch.setattr(temporal, "_sample_frames", lambda *args: [(2.0, frame)])
    monkeypatch.setattr(temporal, "_ocr", lambda frames: [])
    monkeypatch.setattr(temporal, "_speech", lambda *args: "")
    modes = []

    def fake_request(*args):
        mode = args[9]
        modes.append(mode)
        if mode == "native":
            return {"_invalid": True, "cost_usd": 0.003, "cost_type": "actual"}
        return {"summary": "Se mueve una persona", "events": [], "answer": "",
                "input_mode": "frames", "model_used": "test", "cost_usd": 0.004,
                "cost_type": "actual"}

    monkeypatch.setattr(temporal, "_model_request", fake_request)
    result = temporal._analyze(tmp_path / "TV05.mp4", "TV05",
                               {"index": 0, "start_seconds": 0.0, "end_seconds": 20.0},
                               "test", "native")
    assert modes == ["native", "frames"]
    assert result["cost_usd"] == 0.007
    assert result["preferred_input_mode"] == "native"


def test_timeout_fallback_keeps_native_for_next_chunk(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    monkeypatch.setattr(temporal, "_sample_frames", lambda *args: [(2.0, frame)])
    monkeypatch.setattr(temporal, "_ocr", lambda frames: [])
    monkeypatch.setattr(temporal, "_speech", lambda *args: "")

    def fake_request(*args):
        if args[9] == "native":
            return None  # The provider timed out without a usage report.
        return {"summary": "Intervención", "events": [], "answer": "",
                "input_mode": "frames", "model_used": "test", "cost_usd": 0.004,
                "cost_type": "actual"}

    monkeypatch.setattr(temporal, "_model_request", fake_request)
    result = temporal._analyze(tmp_path / "TV04.mp4", "TV04",
                               {"index": 25, "start_seconds": 450.0, "end_seconds": 470.0},
                               "test", "native")
    assert result["cost_usd"] == 0.004
    assert result["unmetered_attempts"] == 1
    assert result["preferred_input_mode"] == "native"


def test_one_legacy_unmetered_chunk_does_not_stop_resume(tmp_path, monkeypatch):
    source = tmp_path / "TV04.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(temporal, "TEMPORAL_CACHE_DIR", tmp_path)
    monkeypatch.setattr(temporal, "_video", lambda video_id: (source, "TV04"))
    monkeypatch.setattr(temporal, "get_openrouter_key", lambda: "test")
    monkeypatch.setattr(temporal, "_index_chunk", lambda *args: None)
    monkeypatch.setenv("OPENROUTER_VISION_MODEL", temporal.DEFAULT_VISION_MODEL)
    manifest = {
        "schema_version": temporal.SCHEMA_VERSION, "video_id": "TV04",
        "source": temporal.file_fingerprint(source), "source_stat": temporal._source_stat(source),
        "model": temporal.DEFAULT_VISION_MODEL, "duration_seconds": 35.0,
        "sample_fps": temporal.SAMPLE_FPS, "chunk_seconds": temporal.CHUNK_SECONDS,
        "input_mode": "native", "chunks": [
            {"index": 0, "start_seconds": 0.0, "end_seconds": 20.0,
             "status": "completed", "attempted": True, "cost_usd": None},
            {"index": 1, "start_seconds": 18.0, "end_seconds": 35.0,
             "status": "pending"}]}
    temporal._write_json(temporal._manifest_path("TV04"), manifest)
    monkeypatch.setattr(temporal, "_analyze", lambda *args: {
        "summary": "Escena", "events": [], "ocr": [], "evidence_frames": [],
        "input_mode": "native", "model_used": "test", "cost_usd": 0.01,
        "unmetered_attempts": 0})

    result = temporal.index_next("TV04")

    assert result["status"] == "completed"
    assert result["unmetered_attempts"] == 1
    assert result["cost_usd"] == 0.01


def test_transcript_is_bound_to_exact_video_source(tmp_path, monkeypatch):
    source = tmp_path / "TV05.mp4"
    source.write_bytes(b"new video")
    stale = {"segments": [{"start_seconds": 0, "end_seconds": 3,
                             "text": "Afirmación de otro vídeo"}],
             "request_metadata": {"source": {"algorithm": "sha256", "sha256": "old", "size": 9}}}
    monkeypatch.setattr(temporal, "load_test_vector", lambda video_id: stale)
    monkeypatch.setattr(temporal, "CACHE_DIR", tmp_path)
    assert temporal._speech(source, "TV05", 0, 20) == ""


def test_event_search_returns_source_bound_timed_candidates(tmp_path, monkeypatch):
    source = tmp_path / "TV05.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(temporal, "TEMPORAL_CACHE_DIR", tmp_path)
    monkeypatch.setattr(temporal, "_video", lambda video_id: (source, "TV05"))
    manifest = {"video_id": "TV05", "source_stat": temporal._source_stat(source),
                "source": temporal.file_fingerprint(source), "sample_fps": 1,
                "duration_seconds": 20, "model": "test", "input_mode": "frames",
                "chunks": [{"index": 0, "start_seconds": 0, "end_seconds": 20,
                            "status": "completed", "summary": "Una persona sale",
                            "events": [{"start_seconds": 5, "end_seconds": 7,
                                        "action": "Sale por la puerta"}],
                            "ocr": [], "evidence_frames": []}]}
    temporal._write_json(temporal._manifest_path("TV05"), manifest)
    monkeypatch.setattr(temporal, "_ensure_index", lambda manifest: None)
    store = type("Store", (), {"query": lambda self, *args, **kwargs: [{
        "metadata": {"index": 0, "source_sha256": manifest["source"]["sha256"]},
        "distance": 0.1}]})()
    monkeypatch.setattr(temporal, "_store", lambda: store)

    result = temporal.search_events("persona sale", "TV05")

    assert result["coverage_complete"] is True
    assert result["results"][0]["events"][0]["start_seconds"] == 5
    assert result["results"][0]["semantic_score"] == 0.9


def test_local_failure_can_be_retried_without_unmetered_block(tmp_path, monkeypatch):
    source = tmp_path / "TV05.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(temporal, "TEMPORAL_CACHE_DIR", tmp_path)
    monkeypatch.setattr(temporal, "_video", lambda video_id: (source, "TV05"))
    monkeypatch.setattr(temporal, "probe_video", lambda path: {"duration": 3.0})
    monkeypatch.setattr(temporal, "get_openrouter_key", lambda: "test")
    monkeypatch.setattr(temporal, "_store", lambda: type("Store", (), {"collection": type("Collection", (), {"delete": lambda *a, **k: None})()})())
    monkeypatch.setattr(temporal, "_index_chunk", lambda *args: None)
    calls = 0

    def analyze(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("FFmpeg no pudo preparar el tramo")
        return {"summary": "Escena", "events": [], "ocr": [], "evidence_frames": [],
                "input_mode": "frames", "model_used": "test", "cost_usd": 0.01}

    monkeypatch.setattr(temporal, "_analyze", analyze)
    assert temporal.index_next("TV05")["status"] == "failed"
    result = temporal.index_next("TV05", retry_failed=True)
    assert result["status"] == "completed"
    assert result["cost_usd"] == 0.01
