"""Remove one video's derived data from Media Core."""

import re
import shutil

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

from media_core.config import (
    BASE_DIR, CACHE_DIR, CHROMA_COLLECTION, CHROMA_PERSIST_DIR, CROPS_CACHE_DIR,
    FACES_CACHE_DIR, IDENTITIES_CACHE_DIR, KEYFRAMES_DIR, OCR_CACHE_DIR,
    TEMPORAL_CACHE_DIR, TRANSCRIPTS_DIR, VISUAL_CACHE_DIR, VISUAL_SWEEPS_DIR,
)
from media_core.services.visual_embeddings import VISUAL_COLLECTION_NAME


def media_forget_video(video_id: str, source_stem: str = '') -> dict:
    """Remove the transcript, visual and temporal indexes and caches for a video.

    The original video is deleted separately by Media API after this succeeds.
    """
    clean_id = video_id.strip().upper()
    if not clean_id or clean_id in {'.', '..'} or re.search(r'[/\\:\x00]', clean_id):
        raise ValueError('Identificador de vídeo no válido.')
    if source_stem and (source_stem in {'.', '..'} or re.search(r'[/\\:\x00]', source_stem)):
        raise ValueError('Nombre de archivo no válido.')

    client = chromadb.PersistentClient(
        path=str(CHROMA_PERSIST_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    removed_vectors = 0
    for collection_name in (CHROMA_COLLECTION, VISUAL_COLLECTION_NAME, 'media_temporal_events_v1'):
        try:
            collection = client.get_collection(collection_name)
        except NotFoundError:
            continue
        ids = collection.get(where={'video_id': clean_id})['ids']
        if ids:
            collection.delete(ids=ids)
            removed_vectors += len(ids)

    files = {
        CACHE_DIR / f'{clean_id}_whisperx.json',
        CACHE_DIR / f'{clean_id}.json',
        TRANSCRIPTS_DIR / f'{clean_id}.json',
        VISUAL_CACHE_DIR / f'{clean_id}_visual.json',
        OCR_CACHE_DIR / f'{clean_id}_ocr.json',
        IDENTITIES_CACHE_DIR / f'{clean_id}_identities.json',
        TEMPORAL_CACHE_DIR / f'{clean_id}.json',
        BASE_DIR / 'experiments' / 'test_vectors' / f'{clean_id}.json',
        BASE_DIR / 'cache' / 'transcriptions' / f'{clean_id}_whisperx.json',
        BASE_DIR / 'cache' / 'transcriptions' / f'{clean_id}.json',
    }
    if source_stem:
        files.add(CACHE_DIR / f'{source_stem}_whisperx.json')
    removed_files = 0
    for path in files:
        if path.is_file():
            path.unlink()
            removed_files += 1

    removed_directories = 0
    for root in (KEYFRAMES_DIR, VISUAL_SWEEPS_DIR, FACES_CACHE_DIR, CROPS_CACHE_DIR):
        path = root / clean_id
        if path.is_dir() and path.resolve().is_relative_to(root.resolve()):
            shutil.rmtree(path)
            removed_directories += 1

    return {
        'status': 'success',
        'video_id': clean_id,
        'removed_vectors': removed_vectors,
        'removed_cache_files': removed_files,
        'removed_cache_directories': removed_directories,
    }
