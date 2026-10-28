"""Build and synchronize semantic-search chunks from transcription JSON."""

import argparse
import hashlib
import json
from dataclasses import dataclass

from media_core.services.chroma import get_chroma_store
from media_core.storage import get_all_test_vectors


@dataclass(frozen=True)
class TranscriptChunk:
    """A timestamped transcription block stored as one vector record."""

    chunk_id: str
    source_hash: str
    video_id: str
    video_title: str
    language: str
    document: str
    key_excerpt: str
    start_time: str
    end_time: str
    start_seconds: float
    end_seconds: float
    segment_start_index: int
    segment_end_index: int
    speakers: list[str]

    def to_metadata(self) -> dict:
        """Return scalar-only metadata accepted by ChromaDB."""
        return {
            "source_hash": self.source_hash,
            "video_id": self.video_id,
            "video_title": self.video_title,
            "language": self.language,
            "key_excerpt": self.key_excerpt,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "segment_start_index": self.segment_start_index,
            "segment_end_index": self.segment_end_index,
            "speakers": json.dumps(self.speakers, ensure_ascii=False),
        }


def _transcript_hash(test_vector: dict) -> str:
    relevant_data = {
        "id": test_vector.get("id"),
        "title": test_vector.get("title"),
        "language": test_vector.get("language"),
        "segments": test_vector.get("segments", []),
    }
    encoded = json.dumps(
        relevant_data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_transcript_chunks(
    test_vector: dict,
    max_words: int = 80,
    max_duration_seconds: float = 45.0,
    overlap_segments: int = 1,
) -> list[TranscriptChunk]:
    """Group contiguous segments into compact timestamped semantic chunks."""
    segments = test_vector.get("segments", [])
    if not segments:
        return []

    video_id = str(test_vector.get("id", "TV00")).upper()
    video_title = str(test_vector.get("title", f"Vídeo {video_id}"))
    language = str(test_vector.get("language", "unknown"))
    source_hash = _transcript_hash(test_vector)
    chunks = []
    start_index = 0

    while start_index < len(segments):
        end_index = start_index
        word_count = 0
        first_start = float(segments[start_index].get("start_seconds", 0.0))

        while end_index < len(segments):
            segment = segments[end_index]
            segment_words = len(str(segment.get("text", "")).split())
            candidate_end = float(segment.get("end_seconds", first_start))
            candidate_duration = max(0.0, candidate_end - first_start)
            exceeds_limit = end_index > start_index and (
                word_count + segment_words > max_words
                or candidate_duration > max_duration_seconds
            )
            if exceeds_limit:
                break
            word_count += segment_words
            end_index += 1

        if end_index == start_index:
            end_index += 1

        selected = segments[start_index:end_index]
        document = " ".join(
            str(segment.get("text", "")).strip() for segment in selected
        ).strip()
        if document:
            last_index = end_index - 1
            speakers = sorted(
                {
                    str(segment.get("speaker", "Hablante"))
                    for segment in selected
                }
            )
            chunk_id = (
                f"{video_id}:{source_hash[:16]}:{start_index}-{last_index}"
            )
            chunks.append(
                TranscriptChunk(
                    chunk_id=chunk_id,
                    source_hash=source_hash,
                    video_id=video_id,
                    video_title=video_title,
                    language=language,
                    document=document,
                    key_excerpt=str(selected[0].get("text", "")).strip(),
                    start_time=str(selected[0].get("start", "00:00:00")),
                    end_time=str(selected[-1].get("end", "00:00:00")),
                    start_seconds=float(selected[0].get("start_seconds", 0.0)),
                    end_seconds=float(selected[-1].get("end_seconds", 0.0)),
                    segment_start_index=start_index,
                    segment_end_index=last_index,
                    speakers=speakers,
                )
            )

        next_index = end_index - max(0, overlap_segments)
        start_index = max(start_index + 1, next_index)

    return chunks


def index_test_vector(test_vector: dict) -> dict:
    """Synchronize one transcription with the persistent Chroma collection."""
    chunks = build_transcript_chunks(test_vector)
    video_id = str(test_vector.get("id", "TV00")).upper()
    return get_chroma_store().sync_video(video_id, chunks)


def index_all_test_vectors(rebuild: bool = False) -> dict:
    """Synchronize every JSON test vector without aborting on one bad item."""
    store = get_chroma_store()
    if rebuild:
        store.reset()

    reports = []
    errors = []
    for test_vector in get_all_test_vectors():
        try:
            reports.append(index_test_vector(test_vector))
        except Exception as error:
            errors.append(
                {
                    "video_id": test_vector.get("id", "unknown"),
                    "error": str(error),
                }
            )

    return {
        "status": "success" if not errors else "partial",
        "videos_processed": len(reports),
        "indexed_chunks": store.count(),
        "reports": reports,
        "errors": errors,
    }


def search_vector_index(query: str, top_k: int, video_id: str = "") -> list[dict]:
    """Search indexed transcription chunks by semantic similarity."""
    return get_chroma_store().query(query, top_k, video_id=video_id) if video_id else get_chroma_store().query(query, top_k)


def get_vector_index_status() -> dict:
    """Return Chroma status without loading the embedding model."""
    try:
        store = get_chroma_store()
        return {
            "status": "online",
            "backend": "chromadb",
            "collection": store.collection_name,
            "indexed_chunks": store.count(),
            "embedding_model": store.embedder.model_name,
            "last_search_mode": getattr(store, "last_search_mode", "not_tested"),
            "last_search_error": getattr(store, "last_search_error", None),
        }
    except Exception as error:
        return {
            "status": "unavailable",
            "backend": "chromadb",
            "error": str(error),
        }


def main() -> None:
    """CLI entry point for initial indexing and explicit rebuilds."""
    parser = argparse.ArgumentParser(description="Index MedIA transcripts in ChromaDB")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete the collection before indexing all available transcripts.",
    )
    args = parser.parse_args()
    print(json.dumps(index_all_test_vectors(rebuild=args.rebuild), indent=2))


if __name__ == "__main__":
    main()
