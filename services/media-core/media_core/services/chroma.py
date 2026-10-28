"""Persistent ChromaDB adapter for transcription chunks."""

from functools import lru_cache, wraps
from threading import RLock
import math
import sys

_STORE_LOCK = RLock()

def serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _STORE_LOCK:
            return fn(*args, **kwargs)
    return call
from typing import Protocol

import chromadb
from chromadb.config import Settings

from media_core.config import CHROMA_COLLECTION, CHROMA_PERSIST_DIR
from media_core.services.embeddings import MultilingualEmbedder


class Embedder(Protocol):
    """Embedding operations required by the vector store."""

    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class ChromaVectorStore:
    """Store, synchronize, and query versioned transcription chunks."""

    def __init__(
        self,
        client=None,
        embedder: Embedder | None = None,
        collection_name: str = CHROMA_COLLECTION,
    ) -> None:
        self.client = client or chromadb.PersistentClient(
            path=str(CHROMA_PERSIST_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        self.embedder = embedder or MultilingualEmbedder()
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={
                "description": "MedIA transcription chunks",
                "embedding_model": self.embedder.model_name,
                "schema_version": 1,
            },
            configuration={"hnsw": {"space": "cosine"}},
        )

    def count(self) -> int:
        """Return the number of indexed chunks."""
        return self.collection.count()

    @serialized
    def sync_video(self, video_id: str, chunks: list) -> dict:
        """Idempotently replace one video's stale chunks after a successful upsert."""
        existing = self.collection.get(
            where={"video_id": video_id},
            include=["metadatas"],
        )
        existing_ids = set(existing.get("ids") or [])
        new_ids = {chunk.chunk_id for chunk in chunks}

        if existing_ids == new_ids and new_ids:
            return {
                "video_id": video_id,
                "indexed_chunks": len(new_ids),
                "removed_chunks": 0,
                "unchanged": True,
            }

        if chunks:
            documents = [chunk.document for chunk in chunks]
            embeddings = self.embedder.embed_documents(documents)
            metadatas = [chunk.to_metadata() for chunk in chunks]
            for start in range(0, len(chunks), 64):
                stop = start + 64
                self.collection.upsert(
                    ids=[chunk.chunk_id for chunk in chunks[start:stop]],
                    documents=documents[start:stop],
                    embeddings=embeddings[start:stop],
                    metadatas=metadatas[start:stop],
                )

        stale_ids = sorted(existing_ids - new_ids)
        if stale_ids:
            self.collection.delete(ids=stale_ids)

        return {
            "video_id": video_id,
            "indexed_chunks": len(new_ids),
            "removed_chunks": len(stale_ids),
            "unchanged": False,
        }

    @serialized
    def query(self, text: str, top_k: int, video_id: str = "") -> list[dict]:
        """Return nearest transcription chunks and their cosine distances."""
        count = self.count()
        if count == 0:
            return []

        embedding = self.embedder.embed_query(text)
        where = {"where": {"video_id": video_id}} if video_id else {}
        try:
            result = self.collection.query(
                **where, query_embeddings=[embedding],
                n_results=min(max(1, top_k), count),
                include=["documents", "metadatas", "distances"],
            )
            self.last_search_mode = "hnsw"
        except Exception as error:
            # Preserve the collection if its approximate index is damaged. The
            # stored embeddings still support exact cosine search, with scope.
            print(f"[Chroma] Approximate index unavailable; using exact cosine: {error}", file=sys.stderr)
            self.last_search_mode = "exact_cosine_fallback"
            self.last_search_error = str(error)
            records = []
            query_norm = math.sqrt(sum(float(x) ** 2 for x in embedding))
            for offset in range(0, count, 256):
                batch = self.collection.get(**where, limit=256, offset=offset,
                    include=["documents", "metadatas", "embeddings"])
                for i, point_id in enumerate(batch['ids']):
                    vector = batch['embeddings'][i]
                    norm = math.sqrt(sum(float(x) ** 2 for x in vector))
                    similarity = sum(float(a) * float(b) for a, b in zip(embedding, vector)) / (query_norm * norm) if query_norm and norm else 0
                    records.append({"id": point_id, "document": batch['documents'][i] or "",
                        "metadata": batch['metadatas'][i] or {}, "distance": 1 - similarity,
                        "retrieval_mode": self.last_search_mode})
                if len(batch['ids']) < 256:
                    break
            return sorted(records, key=lambda r: r['distance'])[:max(1, top_k)]
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            {
                "id": point_id,
                "document": document or "",
                "metadata": metadata or {},
                "distance": float(distance),
            }
            for point_id, document, metadata, distance in zip(
                ids, documents, metadatas, distances
            )
        ]

    def reset(self) -> None:
        """Delete and recreate the configured collection."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={
                "description": "MedIA transcription chunks",
                "embedding_model": self.embedder.model_name,
                "schema_version": 1,
            },
            configuration={"hnsw": {"space": "cosine"}},
        )


@serialized
@lru_cache(maxsize=1)
def get_chroma_store() -> ChromaVectorStore:
    """Return the process-wide persistent Chroma store."""
    return ChromaVectorStore()
