import chromadb

from media_core import indexing
from media_core.services.chroma import ChromaVectorStore


class FakeEmbedder:
    """Small deterministic embedder for tests; no model download required."""

    model_name = "fake-multilingual"

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.lower()
        if any(term in normalized for term in ("presupuesto", "financia")):
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _test_vector() -> dict:
    return {
        "id": "TV01",
        "title": "Noticias",
        "language": "es",
        "segments": [
            {
                "start": "00:00:00",
                "end": "00:00:10",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "speaker": "Presentador",
                "text": "El presupuesto anual aumenta.",
            },
            {
                "start": "00:00:10",
                "end": "00:00:20",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "speaker": "Invitada",
                "text": "La inversión se destina a tecnología.",
            },
            {
                "start": "00:00:20",
                "end": "00:00:30",
                "start_seconds": 20.0,
                "end_seconds": 30.0,
                "speaker": "Presentador",
                "text": "El equipo ganó el partido.",
            },
        ],
    }


def test_build_chunks_preserves_context_and_is_deterministic():
    vector = _test_vector()

    chunks = indexing.build_transcript_chunks(
        vector,
        max_words=9,
        max_duration_seconds=20,
        overlap_segments=1,
    )
    repeated = indexing.build_transcript_chunks(
        vector,
        max_words=9,
        max_duration_seconds=20,
        overlap_segments=1,
    )

    assert chunks[0].video_id == "TV01"
    assert chunks[0].start_seconds == 0.0
    assert chunks[0].end_seconds == 10.0
    assert chunks[1].start_seconds == 10.0
    assert chunks[0].chunk_id == repeated[0].chunk_id
    assert chunks[0].speakers == ["Presentador"]


def test_chroma_store_upserts_and_semantically_queries(tmp_path):
    client = chromadb.EphemeralClient()
    store = ChromaVectorStore(
        client=client,
        embedder=FakeEmbedder(),
        collection_name=f"semantic-{tmp_path.name}",
    )
    chunks = indexing.build_transcript_chunks(
        _test_vector(),
        max_words=9,
        max_duration_seconds=20,
        overlap_segments=0,
    )

    first_sync = store.sync_video("TV01", chunks)
    second_sync = store.sync_video("TV01", chunks)
    results = store.query("¿Cómo se financia el proyecto?", top_k=1)

    assert first_sync["indexed_chunks"] == len(chunks)
    assert second_sync["unchanged"] is True
    assert store.count() == len(chunks)
    assert results[0]["metadata"]["video_id"] == "TV01"
    assert "presupuesto" in results[0]["document"].lower()


def test_chroma_store_removes_stale_chunks(tmp_path):
    client = chromadb.EphemeralClient()
    store = ChromaVectorStore(
        client=client,
        embedder=FakeEmbedder(),
        collection_name=f"stale-{tmp_path.name}",
    )
    original = indexing.build_transcript_chunks(
        _test_vector(), max_words=9, max_duration_seconds=20, overlap_segments=0
    )
    store.sync_video("TV01", original)

    updated_vector = _test_vector()
    updated_vector["segments"] = updated_vector["segments"][:1]
    updated = indexing.build_transcript_chunks(updated_vector)
    result = store.sync_video("TV01", updated)

    assert result["removed_chunks"] == len(original)
    assert store.count() == len(updated)
