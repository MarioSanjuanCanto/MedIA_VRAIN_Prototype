"""Runtime configuration and environment access for Media Core."""

import os
from pathlib import Path
import shutil

from dotenv import load_dotenv

SERVER_NAME = "media-core-server"
SERVER_TRANSPORT = "streamable-http"

CURRENT_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = CURRENT_DIR.parent.parent

load_dotenv(CURRENT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")


def ensure_ffmpeg_on_path() -> Path | None:
    """Expose an installed FFmpeg toolchain to non-login service processes.

    GUI launchers and background agents on macOS commonly omit Homebrew from
    PATH. ``ffmpeg-python`` is not a replacement for the ffmpeg/ffprobe
    executables, so resolve their shared directory once at Core startup.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return Path(shutil.which("ffmpeg")).resolve().parent
    configured = os.getenv("MEDIA_FFMPEG_DIR", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend((Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("C:/ffmpeg/bin")))
    for directory in candidates:
        ffmpeg = directory / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffprobe = directory / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if not (ffmpeg.is_file() and ffprobe.is_file()):
            continue
        current = os.environ.get("PATH", "")
        entries = current.split(os.pathsep) if current else []
        if str(directory) not in entries:
            os.environ["PATH"] = str(directory) + (os.pathsep + current if current else "")
        return directory.resolve()
    return None


FFMPEG_DIR = ensure_ffmpeg_on_path()

SERVER_HOST = os.getenv("MEDIA_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("MEDIA_PORT", "8001"))


def _directory(name: str, default: Path) -> Path:
    configured = os.getenv(name, "").strip()
    path = Path(configured).expanduser() if configured else default
    return (BASE_DIR / path).resolve() if not path.is_absolute() else path.resolve()


SHARED_DIR = _directory("MEDIA_SHARED_DIR", BASE_DIR / "shared")
DATA_DIR = _directory("MEDIA_DATA_DIR", BASE_DIR / "data")
TRANSCRIPTS_DIR = SHARED_DIR / "test-vectors" / "transcripts"
VIDEOS_DIR = DATA_DIR / "test-vectors" / "videos"
CACHE_DIR = DATA_DIR / "cache" / "transcriptions"
OUTPUT_CLIPS_DIR = DATA_DIR / "outputs" / "clips"
OUTPUT_AUDIO_DIR = DATA_DIR / "outputs" / "audio"
VECTOR_STORE_DIR = DATA_DIR / "indexes"
TTS_MODELS_DIR = DATA_DIR / "models" / "tts"
CHROMA_PERSIST_DIR = _directory(
    "CHROMA_PERSIST_DIR", VECTOR_STORE_DIR / "chroma"
)
EMBEDDING_CACHE_DIR = DATA_DIR / "models" / "embeddings"

KEYFRAMES_DIR = DATA_DIR / "cache" / "keyframes"
VISUAL_CACHE_DIR = DATA_DIR / "cache" / "visual"
VISUAL_SWEEPS_DIR = DATA_DIR / "cache" / "visual-sweeps"
MULTIMODAL_CACHE_DIR = DATA_DIR / "cache" / "multimodal"
MULTIMODAL_QUERY_DIR = DATA_DIR / "state" / "search-uploads"
TEMPORAL_CACHE_DIR = DATA_DIR / "cache" / "temporal"
OCR_CACHE_DIR = DATA_DIR / "cache" / "ocr"
IDENTITIES_CACHE_DIR = DATA_DIR / "cache" / "identities"
CROPS_CACHE_DIR = DATA_DIR / "cache" / "crops"
FACE_MODELS_DIR = DATA_DIR / "models" / "faces"
FACES_CACHE_DIR = DATA_DIR / "cache" / "faces"

TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
KEYFRAMES_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_SWEEPS_DIR.mkdir(parents=True, exist_ok=True)
MULTIMODAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
MULTIMODAL_QUERY_DIR.mkdir(parents=True, exist_ok=True)
TEMPORAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
IDENTITIES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CROPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
FACE_MODELS_DIR.mkdir(parents=True, exist_ok=True)
FACES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LLM_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it")
WHISPERX_MODEL = os.getenv(
    "REPLICATE_MODEL",
    "victor-upmeet/whisperx:655845d6190ef70573c669245f245892cd039df4b880a1e3a65852c09252f5cc",
)
QWEN_TTS_MODEL = os.getenv(
    "QWEN_TTS_MODEL",
    "qwen/qwen3-tts:d490a561cf1171a8dc3d96d1e57efffea7dd34607148bb641f3d9de4e38c472e",
)
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "media_transcripts_v1")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
CHROMA_AUTO_INDEX = os.getenv("CHROMA_AUTO_INDEX", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "google/embeddinggemma-2")
MULTIMODAL_CPU_THREADS = max(1, int(os.getenv("MULTIMODAL_CPU_THREADS", "2")))


def get_openrouter_key() -> str:
    """Return the configured OpenRouter API key."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        load_dotenv(CURRENT_DIR / ".env", override=True)
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
    return key


def get_replicate_token() -> str:
    """Return the Replicate token and normalize both supported variable names."""
    token = (
        os.getenv("REPLICATE_API_KEY", "").strip()
        or os.getenv("REPLICATE_API_TOKEN", "").strip()
    )
    if not token:
        load_dotenv(CURRENT_DIR / ".env", override=True)
        token = (
            os.getenv("REPLICATE_API_KEY", "").strip()
            or os.getenv("REPLICATE_API_TOKEN", "").strip()
        )
    if token:
        os.environ["REPLICATE_API_TOKEN"] = token
        os.environ["REPLICATE_API_KEY"] = token
    return token


def get_hf_token() -> str:
    """Return the Hugging Face token used for WhisperX diarization."""
    token = (
        os.getenv("HUGGINGFACE_ACCESS_TOKEN", "").strip()
        or os.getenv("HF_TOKEN", "").strip()
        or os.getenv("HUGGING_FACE_HUB_TOKEN", "").strip()
    )
    if not token:
        load_dotenv(CURRENT_DIR / ".env", override=True)
        token = (
            os.getenv("HUGGINGFACE_ACCESS_TOKEN", "").strip()
            or os.getenv("HF_TOKEN", "").strip()
            or os.getenv("HUGGING_FACE_HUB_TOKEN", "").strip()
        )
    return token
