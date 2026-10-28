"""Multimodal visual embedding and vector search service using CLIP and ChromaDB."""

import logging
from pathlib import Path
from threading import RLock
from typing import Any
from PIL import Image

import chromadb
from chromadb.config import Settings

from media_core.config import CHROMA_PERSIST_DIR, KEYFRAMES_DIR
from media_core.formatting import format_seconds
from media_core.runtime import progress, stage
from media_core.storage import resolve_video_file

logger = logging.getLogger(__name__)

_VISUAL_MODEL_LOCK = RLock()
_VISUAL_MODEL_INSTANCE = None
_DEFAULT_VISUAL_MODEL = "clip-ViT-B-32"
VISUAL_COLLECTION_NAME = "media_visual_keyframes_v1"


def get_visual_embedding_model(model_name: str = _DEFAULT_VISUAL_MODEL):
    """Lazy singleton loader for CLIP visual embedding model."""
    global _VISUAL_MODEL_INSTANCE
    with _VISUAL_MODEL_LOCK:
        if _VISUAL_MODEL_INSTANCE is None:
            from sentence_transformers import SentenceTransformer
            _VISUAL_MODEL_INSTANCE = SentenceTransformer(model_name)
    return _VISUAL_MODEL_INSTANCE


def get_visual_collection(client=None):
    """Retrieve or create the ChromaDB collection for visual keyframes."""
    chroma_client = client or chromadb.PersistentClient(
        path=str(CHROMA_PERSIST_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    return chroma_client.get_or_create_collection(
        name=VISUAL_COLLECTION_NAME,
        metadata={"description": "MedIA visual keyframe embeddings", "space": "cosine"},
        configuration={"hnsw": {"space": "cosine"}},
    )


def index_keyframes_visual(
    video_id: str,
    visual_manifest: dict,
    keyframes_dir: Path,
    batch_size: int = 16,
    force: bool = False,
    model: Any = None,
    client: Any = None,
) -> dict:
    """Extract CLIP embeddings from representative keyframes and index in ChromaDB.

    Args:
        video_id: Clean video identifier (e.g. 'TV05')
        visual_manifest: Manifest dict containing scenes and frames
        keyframes_dir: Directory containing keyframe images
        batch_size: Embedding batch size
        force: If True, re-indexes existing keyframes
        model: Optional preloaded SentenceTransformer instance
        client: Optional ChromaDB client instance
    """
    clean_id = video_id.strip().upper()
    collection = get_visual_collection(client)
    embedder = model or get_visual_embedding_model()

    frames = visual_manifest.get("frames", [])
    # Filter to representative / non-duplicate frames
    target_frames = [f for f in frames if not f.get("is_duplicate")]
    if not target_frames:
        target_files = sorted(keyframes_dir.glob("frame_*.jpg"))
        target_frames = [
            {"filename": f.name, "timestamp": 0.0, "scene_id": 1}
            for f in target_files
        ]

    if not force:
        # Check existing count in collection for this video_id
        try:
            existing = collection.get(where={"video_id": clean_id})
            if existing and len(existing.get("ids", [])) >= len(target_frames) * 0.8:
                return {
                    "status": "cached",
                    "video_id": clean_id,
                    "indexed_keyframes_count": len(existing["ids"]),
                    "cache_hit": True,
                }
        except Exception:
            pass

    total_indexed = 0
    with stage("visual_indexing"):
        progress("indexing_keyframes", video_id=clean_id, total=len(target_frames))

        for i in range(0, len(target_frames), batch_size):
            chunk = target_frames[i : i + batch_size]
            batch_images = []
            valid_chunk_metas = []

            for meta in chunk:
                fn = meta["filename"]
                img_path = keyframes_dir / fn
                if not img_path.exists():
                    continue
                try:
                    img = Image.open(img_path)
                    img.load()
                    batch_images.append(img)
                    valid_chunk_metas.append(meta)
                except Exception:
                    continue

            if not batch_images:
                continue

            # Generate normalized visual embeddings
            vectors = embedder.encode(batch_images, normalize_embeddings=True)

            ids = [f"{clean_id}_{m['filename']}" for m in valid_chunk_metas]
            metadatas = [
                {
                    "video_id": clean_id,
                    "filename": m["filename"],
                    "timestamp": float(m.get("timestamp", 0.0)),
                    "scene_id": int(m.get("scene_id", 1)),
                    "keyframe_path": f"keyframes/{clean_id}/{m['filename']}",
                }
                for m in valid_chunk_metas
            ]
            documents = [
                f"Fotograma de {clean_id} en {format_seconds(m.get('timestamp', 0.0))}"
                for m in valid_chunk_metas
            ]

            collection.upsert(
                ids=ids,
                embeddings=[vec.tolist() if hasattr(vec, "tolist") else list(vec) for vec in vectors],
                metadatas=metadatas,
                documents=documents,
            )
            total_indexed += len(valid_chunk_metas)

    return {
        "status": "success",
        "video_id": clean_id,
        "indexed_keyframes_count": total_indexed,
        "cache_hit": False,
    }


def search_visual_index(
    query: str,
    top_k: int = 3,
    video_id: str = "",
    min_score: float = 0.20,
    model: Any = None,
    client: Any = None,
) -> list[dict]:
    """Search for keyframes matching a visual text concept using cosine similarity.

    Args:
        query: Natural language visual concept (e.g. 'gasolinera', 'coche de policía')
        top_k: Number of matches to return
        video_id: Optional video ID filter
        min_score: Minimum cosine similarity threshold (0.0 to 1.0)
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    collection = get_visual_collection(client)
    embedder = model or get_visual_embedding_model()

    # Encode textual query into multimodal vector space
    query_vec = embedder.encode(clean_query, normalize_embeddings=True)
    query_list = query_vec.tolist() if hasattr(query_vec, "tolist") else list(query_vec)

    where_filter = {"video_id": video_id.strip().upper()} if video_id.strip() else None

    try:
        results = collection.query(
            query_embeddings=[query_list],
            n_results=max(1, top_k * 2),
            where=where_filter,
            include=["metadatas", "distances", "documents"],
        )
    except Exception as exc:
        logger.warning(f"Visual vector query failed: {exc}")
        return []

    matched = []
    ids = (results.get("ids") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]

    seen_scenes = set()
    for doc_id, meta, dist in zip(ids, metas, distances):
        # In cosine space, similarity = 1.0 - distance
        similarity = round(max(0.0, min(1.0, 1.0 - float(dist))), 4)
        if similarity < min_score:
            continue

        vid = meta.get("video_id", "")
        sid = meta.get("scene_id", 0)
        scene_key = (vid, sid)
        if scene_key in seen_scenes:
            continue
        seen_scenes.add(scene_key)

        ts = float(meta.get("timestamp", 0.0))
        video_file = resolve_video_file(vid)

        matched.append({
            "video_id": vid,
            "video_title": f"Vídeo {vid}",
            "video_file_available": str(video_file) if video_file else None,
            "start_time": format_seconds(ts),
            "end_time": format_seconds(ts + 5.0),
            "duration": "00:00:05",
            "speakers": ["Concepto visual"],
            "matched_terms": [clean_query],
            "relevance_score": round(similarity * 100, 2),
            "semantic_score": similarity,
            "key_excerpt": f"[Concepto visual: {clean_query}] Similitud: {round(similarity * 100, 1)}%",
            "full_context": f"Fotograma en {format_seconds(ts)} con alta correspondencia visual para '{clean_query}'.",
            "search_engine": "multimodal_clip",
            "match_type": "visual_concept",
            "keyframe": meta.get("keyframe_path"),
            "suggested_clip_call": f"media_clips(video_id={vid!r}, start={ts}, duration=5.0)",
        })

    matched.sort(key=lambda item: item["relevance_score"], reverse=True)
    return matched[:top_k]
