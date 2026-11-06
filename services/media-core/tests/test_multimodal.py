"""Contracts for progressive multimodal indexing and evidence-aware retrieval."""

from collections import OrderedDict
from types import SimpleNamespace

import pytest

from media_core.services import multimodal
from media_core.tools import multimodal as search_tool


def test_index_resumes_completed_items_and_retries_only_failures(tmp_path, monkeypatch):
    video = tmp_path / "TV88.mp4"
    video.write_bytes(b"source")
    cache = tmp_path / "manifests"
    cache.mkdir()
    identity = {"algorithm": "sha256", "sha256": "source-hash", "size": 6}
    monkeypatch.setattr(multimodal, "MULTIMODAL_CACHE_DIR", cache)
    monkeypatch.setattr(multimodal, "_source", lambda _: (video, "TV88", identity))
    monkeypatch.setattr(multimodal, "_model", lambda: object())
    monkeypatch.setattr(multimodal, "_items", lambda *_: [
        {"modality": "image", "start_seconds": 0.0, "end_seconds": 2.5, "filename": "frame.jpg"},
        {"modality": "audio", "start_seconds": 0.0, "end_seconds": 12.0},
    ])
    calls = []
    def index_one(_path, _video_id, _hash, item):
        calls.append(item["modality"])
        if item["modality"] == "audio" and calls.count("audio") == 1:
            raise RuntimeError("temporary decoder failure")
    monkeypatch.setattr(multimodal, "_index_one", index_one)
    class Collection:
        def get(self, **_): return {"ids": []}
        def delete(self, **_): raise AssertionError("No stale vectors")
    monkeypatch.setattr(multimodal, "_collection", lambda: Collection())

    first = multimodal.index_next("TV88", max_items=1)
    assert first["completed_items"] == 1 and first["coverage_complete"] is False
    second = multimodal.index_next("TV88", max_items=1)
    assert second["status"] == "partial" and second["failed_items"] == 1
    third = multimodal.index_next("TV88", max_items=1, retry_failed=True)
    assert third["status"] == "completed"
    assert calls == ["image", "audio", "audio"]


def test_search_fuses_nearby_candidates_without_claiming_verification(monkeypatch):
    monkeypatch.setattr(search_tool, "media_search", lambda *_, **__: [{
        "video_id": "TV05", "start_time": "00:00:10", "end_time": "00:00:18",
        "search_engine": "chromadb", "full_context": "Texto candidato"}])
    monkeypatch.setattr(search_tool, "media_search_visual", lambda *_, **__: [{
        "video_id": "TV05", "start_time": "00:00:12", "end_time": "00:00:17",
        "search_engine": "multimodal_clip", "keyframe": "keyframes/TV05/frame.jpg"}])
    monkeypatch.setattr(multimodal, "search_index", lambda *_, **__: [{
        "video_id": "TV05", "start_seconds": 11, "end_seconds": 16,
        "search_engine": "embeddinggemma_2", "modality": "video"}])
    monkeypatch.setattr(multimodal, "index_status", lambda _: {"status": "processing", "coverage_complete": False})

    result = search_tool.media_search_multimodal("persona con cartel", video_id="TV05")
    assert len(result["results"]) == 1
    candidate = result["results"][0]
    assert set(candidate["retrieval_engines"]) == {"chromadb", "multimodal_clip", "embeddinggemma_2"}
    assert set(candidate["modalities"]) == {"text", "image", "video"}
    assert candidate["evidence_status"] == "semantic_candidate"
    assert result["coverage_complete"] is False


def test_indexed_text_query_uses_gemma_before_text_or_clip(monkeypatch):
    calls = []
    monkeypatch.setattr(multimodal, "index_coverage", lambda _: {
        "completed_items": 28, "coverage_complete": True,
    })
    def gemma(*args):
        calls.append("gemma")
        return [{"video_id": "APUNT", "start_seconds": 40, "end_seconds": 48,
                 "modality": "video", "semantic_score": .61,
                 "search_engine": "embeddinggemma_2"}]
    monkeypatch.setattr(multimodal, "search_index", gemma)
    monkeypatch.setattr(search_tool, "media_search", lambda *_, **__: pytest.fail("Transcript searched first"))
    monkeypatch.setattr(search_tool, "media_search_visual", lambda *_, **__: pytest.fail("CLIP searched first"))

    result = search_tool.media_search_multimodal("teléfono móvil", "APUNT")

    assert calls == ["gemma"]
    assert result["retrieval_strategy"] == "multimodal_first"
    assert result["results"][0]["start_seconds"] == 40
    assert result["results"][0]["evidence_status"] == "semantic_candidate"


def test_indexed_text_query_falls_back_when_gemma_has_no_candidates(monkeypatch):
    calls = []
    monkeypatch.setattr(multimodal, "index_coverage", lambda _: {
        "completed_items": 3, "coverage_complete": False,
    })
    monkeypatch.setattr(multimodal, "search_index", lambda *_: calls.append("gemma") or [])
    monkeypatch.setattr(search_tool, "media_search", lambda *_, **__: calls.append("text") or [{
        "video_id": "APUNT", "start_seconds": 12, "end_seconds": 18,
        "search_engine": "chromadb"}])
    monkeypatch.setattr(search_tool, "media_search_visual", lambda *_, **__: calls.append("clip") or [])

    result = search_tool.media_search_multimodal("teléfono móvil", "APUNT")

    assert calls == ["gemma", "text", "clip"]
    assert result["retrieval_strategy"] == "fallback"
    assert result["results"][0]["start_seconds"] == 12


def test_optional_visual_index_failure_keeps_transcript_results(monkeypatch):
    monkeypatch.setattr(search_tool, "media_search", lambda *_, **__: [{
        "video_id": "TV05", "start_time": "00:00:10", "end_time": "00:00:18",
        "search_engine": "chromadb", "full_context": "Texto candidato"}])
    def unavailable(*_, **__):
        raise RuntimeError("CLIP no cargó")
    monkeypatch.setattr(search_tool, "media_search_visual", unavailable)
    monkeypatch.setattr(multimodal, "search_index", lambda *_, **__: [])
    monkeypatch.setattr(multimodal, "index_coverage", lambda _: {"coverage_complete": False})

    result = search_tool.media_search_multimodal("texto", video_id="TV05")
    assert result["status"] == "success"
    assert result["results"][0]["full_context"] == "Texto candidato"
    assert result["warnings"] == ["Índice CLIP no disponible: CLIP no cargó"]


def test_reference_file_uses_only_combined_multimodal_embedding(monkeypatch):
    def unexpected(*_, **__):
        raise AssertionError("No se debe confundir texto recuperado con coincidencia del archivo")
    monkeypatch.setattr(search_tool, "media_search", unexpected)
    monkeypatch.setattr(search_tool, "media_search_visual", unexpected)
    monkeypatch.setattr(multimodal, "match_reference_frame", lambda *args: None)
    monkeypatch.setattr(multimodal, "search_index", lambda *_, **__: [{
        "video_id": "TV05", "start_seconds": 12, "end_seconds": 18,
        "modality": "image", "semantic_score": 0.56, "search_engine": "embeddinggemma_2"}, {
        "video_id": "TV05", "start_seconds": 32, "end_seconds": 38,
        "modality": "image", "semantic_score": 0.82, "search_engine": "embeddinggemma_2"}])
    monkeypatch.setattr(multimodal, "index_coverage", lambda _: {"coverage_complete": False})

    result = search_tool.media_search_multimodal("pancarta", "TV05", "reference.png")
    assert result["results"][0]["retrieval_engines"] == ["embeddinggemma_2"]
    assert result["results"][0]["start_seconds"] == 32
    assert result["results"][0]["semantic_score"] == 0.82


def test_repeated_reference_reuses_its_embedding(tmp_path, monkeypatch):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"same-image")
    second.write_bytes(b"same-image")
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(multimodal, "MULTIMODAL_QUERY_DIR", tmp_path)
    monkeypatch.setattr(multimodal, "_QUERY_VECTORS", OrderedDict())
    monkeypatch.setattr(multimodal, "resolve_video_file", lambda _: video)
    monkeypatch.setattr(multimodal, "file_fingerprint", lambda _: {"sha256": "source-hash"})
    encoded = []
    monkeypatch.setattr(multimodal, "_encode", lambda payload, prompt: encoded.append(payload) or [0.1, 0.2])

    class Collection:
        def count(self): return 1
        def query(self, **_):
            return {"metadatas": [[{"video_id": "TV05", "source_sha256": "source-hash",
                                    "start_seconds": 12, "end_seconds": 14,
                                    "modality": "image", "keyframe": ""}]],
                    "distances": [[0.2]]}

    monkeypatch.setattr(multimodal, "_collection", lambda: Collection())
    assert multimodal.search_index("", "first.png", "TV05")
    assert multimodal.search_index("", "second.png", "TV05")
    assert len(encoded) == 1


def test_short_video_frame_match_skips_semantic_model(tmp_path, monkeypatch):
    monkeypatch.setattr(multimodal, "match_reference_frame", lambda *_: {
        "video_id": "TV05", "start_seconds": 26.0, "end_seconds": 28.0,
        "modality": "image", "search_engine": "local_frame_match",
        "evidence_status": "verified_visual_match", "visual_inliers": 180,
    })
    monkeypatch.setattr(multimodal, "search_index", lambda *_, **__: pytest.fail("No debe cargar Gemma"))
    monkeypatch.setattr(multimodal, "index_coverage", lambda _: {"coverage_complete": True})

    result = search_tool.media_search_multimodal("", "TV05", "reference.png")

    assert result["exact_visual_match"] is True
    assert result["results"][0]["start_seconds"] == 26.0
    assert result["results"][0]["visual_inliers"] == 180


def test_geometric_frame_match_confirms_identical_frame(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 256, (270, 480), dtype=np.uint8)
    query = tmp_path / "reference.png"
    cv2.imwrite(str(query), frame)
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(multimodal, "MULTIMODAL_QUERY_DIR", tmp_path)
    monkeypatch.setattr(multimodal, "resolve_video_file", lambda _: video)
    monkeypatch.setattr(multimodal, "probe_video", lambda _: {"duration": 1.0})
    monkeypatch.setattr(multimodal.subprocess, "run", lambda *_, **__: SimpleNamespace(
        returncode=0, stdout=frame.tobytes(),
    ))

    match = multimodal.match_reference_frame(query.name, "TV05")

    assert match is not None
    assert match["evidence_status"] == "verified_visual_match"
    assert match["start_seconds"] == 0
    assert match["visual_inliers"] >= 50

    unrelated = rng.integers(0, 256, (270, 480), dtype=np.uint8)
    monkeypatch.setattr(multimodal.subprocess, "run", lambda *_, **__: SimpleNamespace(
        returncode=0, stdout=unrelated.tobytes(),
    ))
    assert multimodal.match_reference_frame(query.name, "TV05") is None
