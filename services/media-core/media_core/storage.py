"""Filesystem access for videos, cached transcriptions, and test vectors."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from media_core.config import BASE_DIR, CACHE_DIR, TRANSCRIPTS_DIR, VIDEOS_DIR


@lru_cache(maxsize=128)
def _cached_sha256(path: str, size: int, modified_ns: int) -> str:
    """Hash a stable file; stat values make the in-process cache safe to reuse."""
    del size, modified_ns
    digest = hashlib.sha256()
    with Path(path).open("rb") as media:
        for chunk in iter(lambda: media.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path: Path) -> dict:
    """Return a path-independent identity for a media file."""
    resolved = path.resolve()
    before = resolved.stat()
    sha256 = _cached_sha256(str(resolved), before.st_size, before.st_mtime_ns)
    after = resolved.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"El archivo cambió mientras se calculaba su huella: {resolved}")
    return {"algorithm": "sha256", "sha256": sha256, "size": before.st_size}


def source_matches_file(source_identity, path: Path) -> bool:
    """Match portable hashes, retaining exact legacy-path compatibility and filename+size fallback."""
    if isinstance(source_identity, dict):
        if source_identity.get("algorithm") != "sha256":
            return False
        try:
            return source_identity == file_fingerprint(path)
        except OSError:
            return False
    if isinstance(source_identity, list) and len(source_identity) == 3:
        stat = path.stat()
        legacy = [str(path.resolve()), stat.st_size, stat.st_mtime_ns]
        if source_identity == legacy:
            return True
        try:
            orig_name = Path(source_identity[0]).name.casefold()
            if orig_name == path.name.casefold() and source_identity[1] == stat.st_size:
                return True
        except Exception:
            pass
        return False
    return False


def resolve_video_file(source: str) -> Path | None:
    """Resolve a direct media path or a video identifier stored in VIDEOS_DIR or legacy directories."""
    clean = source.strip()
    direct_path = Path(clean)
    if direct_path.exists() and direct_path.is_file():
        return direct_path

    legacy_videos_dir = BASE_DIR / "experiments" / "test_vectors" / "videos"
    search_dirs = [VIDEOS_DIR]
    if legacy_videos_dir.exists() and legacy_videos_dir.resolve() != VIDEOS_DIR.resolve():
        search_dirs.append(legacy_videos_dir)

    # A basename such as TV02.mp4 is relative to the media directory as well.
    if direct_path.name == clean:
        for vdir in search_dirs:
            candidate = vdir / clean
            if candidate.is_file():
                return candidate
    clean_id = clean.upper()
    for extension in [".mp4", ".mov", ".mkv", ".wav", ".mp3"]:
        for vdir in search_dirs:
            candidate = vdir / f"{clean_id}{extension}"
            if candidate.exists():
                return candidate
            candidate_demo = vdir / f"{clean_id}_demo{extension}"
            if candidate_demo.exists():
                return candidate_demo

    return None


def load_test_vector(video_id: str) -> dict | None:
    """Load a test vector from the base directory or transcription cache."""
    clean_id = video_id.strip().upper()
    legacy_transcripts_dir = BASE_DIR / "experiments" / "test_vectors"
    legacy_cache_dir = BASE_DIR / "cache" / "transcriptions"
    candidates = [
        TRANSCRIPTS_DIR / f"{clean_id}.json",
        CACHE_DIR / f"{clean_id}_whisperx.json",
        CACHE_DIR / f"{clean_id}.json",
        legacy_transcripts_dir / f"{clean_id}.json",
        legacy_cache_dir / f"{clean_id}_whisperx.json",
        legacy_cache_dir / f"{clean_id}.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                if data.get("id") and str(data["id"]).upper() != clean_id:
                    continue
                if not data.get("id"):
                    data["id"] = clean_id
                return data
            except Exception:
                continue
    return None


def get_all_test_vectors() -> list[dict]:
    """Return unique test vectors from the base directory and cache."""
    vectors_by_id: dict[str, dict] = {}

    if TRANSCRIPTS_DIR.exists():
        for file_path in sorted(TRANSCRIPTS_DIR.glob("*.json")):
            try:
                with file_path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                video_id = data.get("id") or file_path.stem.upper()
                data["id"] = video_id
                vectors_by_id[video_id] = data
            except Exception:
                continue

    if CACHE_DIR.exists():
        for file_path in sorted(CACHE_DIR.glob("*.json")):
            try:
                video_id = file_path.stem.replace("_whisperx", "").upper()
                if video_id not in vectors_by_id:
                    with file_path.open("r", encoding="utf-8") as file:
                        data = json.load(file)
                    data["id"] = video_id
                    vectors_by_id[video_id] = data
            except Exception:
                continue

    return list(vectors_by_id.values())
