import json

from media_core import storage
from media_core.tools import search


def test_media_search_ranks_matching_segments(monkeypatch):
    monkeypatch.setattr(
        search,
        "search_vector_index",
        lambda query, top_k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        search,
        "get_all_test_vectors",
        lambda: [
            {
                "id": "TV01",
                "title": "Noticias",
                "segments": [
                    {
                        "start": "00:00:00",
                        "end": "00:00:10",
                        "start_seconds": 0,
                        "end_seconds": 10,
                        "speaker": "Presentador",
                        "text": "Sube el precio del combustible",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(search, "resolve_video_file", lambda video_id: None)

    result = search.media_search("precio combustible")

    assert result[0]["video_id"] == "TV01"
    assert result[0]["matched_terms"] == ["combustible", "precio"]
    assert result[0]["relevance_score"] > 0
    assert result[0]["search_engine"] == "lexical_fallback"


def test_media_search_returns_chroma_results(monkeypatch):
    monkeypatch.setattr(
        search,
        "search_vector_index",
        lambda query, top_k: [
            {
                "id": "TV01:abc:0-1",
                "document": "La partida económica crece este año.",
                "distance": 0.12,
                "metadata": {
                    "video_id": "TV01",
                    "video_title": "Presupuestos",
                    "start_time": "00:02:00",
                    "end_time": "00:02:20",
                    "start_seconds": 120.0,
                    "end_seconds": 140.0,
                    "speakers": '["Consejera"]',
                    "key_excerpt": "La partida económica crece.",
                },
            }
        ],
    )
    monkeypatch.setattr(search, "resolve_video_file", lambda video_id: None)

    result = search.media_search("¿Qué financiación se menciona?")

    assert result[0]["video_id"] == "TV01"
    assert result[0]["search_engine"] == "chromadb"
    assert result[0]["semantic_score"] == 0.88
    assert result[0]["speakers"] == ["Consejera"]


def test_media_search_rejects_empty_query():
    result = search.media_search("a de y")

    assert result[0]["status"] == "invalid_query"


def test_media_search_reports_no_results(monkeypatch):
    monkeypatch.setattr(search, "search_vector_index", lambda query, top_k: [])
    monkeypatch.setattr(search, "get_all_test_vectors", lambda: [])

    result = search.media_search("tema inexistente")

    assert result[0]["status"] == "no_results"


def test_storage_resolves_direct_path_and_video_id(tmp_path, monkeypatch):
    direct = tmp_path / "direct.mp4"
    direct.touch()
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "TV02_demo.mov").touch()
    monkeypatch.setattr(storage, "VIDEOS_DIR", videos)

    assert storage.resolve_video_file(str(direct)) == direct
    assert storage.resolve_video_file("TV02") == videos / "TV02_demo.mov"
    assert storage.resolve_video_file("missing") is None


def test_storage_merges_vectors_with_base_precedence(tmp_path, monkeypatch):
    vectors = tmp_path / "vectors"
    cache = tmp_path / "cache"
    vectors.mkdir()
    cache.mkdir()
    (vectors / "TV01.json").write_text(
        json.dumps({"id": "TV01", "title": "Base"}), encoding="utf-8"
    )
    (cache / "TV01_whisperx.json").write_text(
        json.dumps({"title": "Cache"}), encoding="utf-8"
    )
    (cache / "TV02_whisperx.json").write_text(
        json.dumps({"title": "Only cache"}), encoding="utf-8"
    )
    monkeypatch.setattr(storage, "TRANSCRIPTS_DIR", vectors)
    monkeypatch.setattr(storage, "CACHE_DIR", cache)

    loaded = storage.load_test_vector("TV02")
    all_vectors = {item["id"]: item for item in storage.get_all_test_vectors()}

    assert loaded["id"] == "TV02"
    assert all_vectors["TV01"]["title"] == "Base"
    assert all_vectors["TV02"]["title"] == "Only cache"
