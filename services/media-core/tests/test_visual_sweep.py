import json
import threading
import time

import pytest

from media_core.runtime import _reporter
from media_core.services import visual_sweep
from media_core.tools import jobs


def test_build_chunks_covers_complete_timeline_with_overlap():
    assert visual_sweep.build_chunks(125, 60, 2) == [
        {"index": 0, "start_seconds": 0.0, "end_seconds": 60.0},
        {"index": 1, "start_seconds": 58.0, "end_seconds": 118.0},
        {"index": 2, "start_seconds": 116.0, "end_seconds": 125},
    ]


def test_merge_occurrences_rejects_invalid_times_and_deduplicates_overlap():
    result = visual_sweep.merge_occurrences([
        {"start_seconds": 10, "end_seconds": 14, "description": "Persona", "evidence_frames": [10], "confidence": .7},
        {"start_seconds": 13, "end_seconds": 18, "description": "Persona mayor", "evidence_frames": [15], "confidence": .9},
        {"start_seconds": 80, "end_seconds": 70},
        {"start_seconds": "bad", "end_seconds": 20},
    ], 100)
    assert len(result) == 1
    assert result[0]["start_seconds"] == 10
    assert result[0]["end_seconds"] == 18
    assert result[0]["evidence_frames"] == [10, 15]
    assert result[0]["confidence"] == .9


def test_json_object_recovers_fenced_text_and_top_level_lists():
    assert visual_sweep._json_object('Respuesta:\n```json\n{"frames": [], "occurrences": []}\n```')["frames"] == []
    assert visual_sweep._json_object('[{"sample_index": 0, "description": "coche"}]') == {
        "frames": [{"sample_index": 0, "description": "coche"}], "occurrences": [],
    }
    assert visual_sweep._json_object('[0, 2]') == {"selected_candidate_ids": [0, 2]}


def test_empty_vlm_response_is_not_sent_to_json_repair(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    calls = []
    monkeypatch.setattr(
        visual_sweep, "_extract_frames",
        lambda *args: [{"path": frame, "timestamp": 0.0}],
    )
    monkeypatch.setattr(
        visual_sweep, "call_openrouter",
        lambda *args, **kwargs: calls.append(kwargs) or None,
    )

    with pytest.raises(RuntimeError, match="respuesta vacía"):
        visual_sweep._analyze_chunk(
            tmp_path / "TV05.mp4", "TV05", "persona",
            {"index": 0, "start_seconds": 0.0, "end_seconds": 20.0},
            2.5, tmp_path / "chunk",
        )

    assert len(calls) == 1
    assert calls[0]["timeout_seconds"] == 90
    assert calls[0]["retry_delays"] == (2, 5, 10)
    assert calls[0]["raise_errors"] is True


def test_completed_chunk_is_reused_without_extracting_frames(monkeypatch, tmp_path):
    directory = tmp_path / "chunk"
    directory.mkdir()
    cached = {"status": "success", "sampled_frames": 8, "occurrences": []}
    (directory / "result.json").write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(
        visual_sweep, "_extract_frames",
        lambda *args: (_ for _ in ()).throw(AssertionError("frames were regenerated")),
    )

    result = visual_sweep._analyze_chunk(
        tmp_path / "TV05.mp4", "TV05", "persona",
        {"index": 0, "start_seconds": 0.0, "end_seconds": 20.0},
        2.5, directory,
    )

    assert result["cache_hit"] is True
    assert result["sampled_frames"] == 8


def test_visual_sweep_rejects_more_than_two_workers():
    result = visual_sweep.run_visual_sweep("TV05", "persona", workers=3)
    assert result["status"] == "error"
    assert "workers 1..2" in result["message"]


def test_visual_sweep_reports_progress_and_writes_manifest(monkeypatch, tmp_path):
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"video")
    cache = tmp_path / "sweeps"
    output = tmp_path / "clips"
    events = []
    monkeypatch.setattr(visual_sweep, "VISUAL_SWEEPS_DIR", cache)
    monkeypatch.setattr(visual_sweep, "OUTPUT_CLIPS_DIR", output)
    monkeypatch.setattr(visual_sweep, "get_openrouter_key", lambda: "test")
    monkeypatch.setattr(visual_sweep, "resolve_video_file", lambda _: video)
    monkeypatch.setattr(visual_sweep, "probe_video", lambda _: {"duration": 70})
    monkeypatch.setattr(visual_sweep, "file_fingerprint", lambda _: {"algorithm": "sha256", "sha256": "x", "size": 5})
    monkeypatch.setattr(visual_sweep, "_analyze_chunk", lambda *args: {
        "status": "success", "sampled_frames": 12,
        "occurrences": [{"start_seconds": args[3]["start_seconds"] + 2, "end_seconds": args[3]["start_seconds"] + 4,
                         "description": "coincidencia", "evidence_frames": [args[3]["start_seconds"] + 2], "confidence": .8}],
    })
    monkeypatch.setattr(visual_sweep, "_reduce_occurrences", lambda query, items, duration: items)

    def fake_cut(source, start, end, target, precise=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"clip")
        return True

    monkeypatch.setattr(visual_sweep, "cut_clip", fake_cut)
    token = _reporter.set(lambda stage, details: events.append({"stage": stage, **details}))
    try:
        result = visual_sweep.run_visual_sweep("TV05", "persona mayor", chunk_seconds=60, workers=2)
    finally:
        _reporter.reset(token)

    assert result["status"] == "success"
    assert result["coverage"]["chunk_count"] == 2
    assert result["coverage"]["sampled_frames"] == 24
    assert result["occurrence_count"] == 2
    assert all(item["clip_file"].endswith(".mp4") for item in result["clips"])
    assert events[-1]["percent"] == 100
    assert [event["percent"] for event in events] == sorted(event["percent"] for event in events)
    manifest = next((cache / "TV05").glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["status"] == "success"


def test_visual_sweep_cancel_does_not_wait_for_inflight_provider_call(monkeypatch, tmp_path):
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"video")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_futures", {})
    monkeypatch.setattr(jobs, "_cancelled_owners", {}, raising=False)
    monkeypatch.setattr(visual_sweep, "VISUAL_SWEEPS_DIR", tmp_path / "sweeps")
    monkeypatch.setattr(visual_sweep, "get_openrouter_key", lambda: "test")
    monkeypatch.setattr(visual_sweep, "resolve_video_file", lambda _: video)
    monkeypatch.setattr(visual_sweep, "probe_video", lambda _: {"duration": 70})
    monkeypatch.setattr(visual_sweep, "file_fingerprint", lambda _: {"sha256": "x"})

    def blocked_provider(*args, **kwargs):
        started.set()
        release.wait(5)
        finished.set()
        return {"status": "success", "sampled_frames": 1, "occurrences": []}

    monkeypatch.setattr(visual_sweep, "_analyze_chunk", blocked_provider)
    submitted = jobs._submit(visual_sweep.run_visual_sweep, {
        "video_id": "TV05", "query": "logo", "chunk_seconds": 60,
        "sample_interval": 2.5, "workers": 1, "extract_clips": False,
    })
    assert started.wait(2)

    before = time.monotonic()
    jobs.media_job_cancel(submitted["job_id"])
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        state = jobs.media_job_status(submitted["job_id"])
        if state["status"] == "cancelled":
            break
        time.sleep(.01)

    assert state["status"] == "cancelled"
    assert time.monotonic() - before < 1
    release.set()
    assert finished.wait(2)
