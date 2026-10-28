"""MCP operational-information tool."""

import sys
import os
import shutil
from media_core.runtime import recent_timings

from media_core.config import (
    DEFAULT_LLM_MODEL,
    OUTPUT_CLIPS_DIR,
    SERVER_PORT,
    SERVER_TRANSPORT,
    VIDEOS_DIR,
    WHISPERX_MODEL,
    QWEN_TTS_MODEL,
    EMBEDDING_MODEL,
    get_hf_token,
    get_openrouter_key,
    get_replicate_token,
)
from media_core.indexing import get_vector_index_status
from media_core.storage import get_all_test_vectors


def get_server_info() -> dict:
    """Devuelve el estado operativo, modelos activos del consorcio y configuración de MedIA Core."""
    openrouter_status = (
        "configurada (acceso al proveedor no verificado por esta consulta)"
        if bool(get_openrouter_key())
        else "no detectada (resúmenes no disponibles)"
    )
    replicate_status = (
        "configurada (acceso al proveedor no verificado por esta consulta)"
        if bool(get_replicate_token())
        else "no detectada (usando transcripts de test vectors)"
    )
    hf_status = (
        "configurada (requiere acceso concedido a los modelos de diarización)"
        if bool(get_hf_token())
        else "no detectada (diarización limitada)"
    )

    all_vectors = get_all_test_vectors()
    available_vectors = sorted(
        {
            test_vector.get("id")
            for test_vector in all_vectors
            if test_vector.get("id")
        }
    )
    total_segments = sum(
        len(test_vector.get("segments", [])) for test_vector in all_vectors
    )
    local_videos = [
        file.name for file in VIDEOS_DIR.glob("*.*") if file.is_file()
    ]
    generated_clips = [file.name for file in OUTPUT_CLIPS_DIR.glob("*.mp4")]
    vector_index = get_vector_index_status()

    return {
        "service": "media-core-server",
        "version": "1.5.0",
        "status": "online",
        "port": SERVER_PORT,
        "transport": SERVER_TRANSPORT,
        "models_selected": {
            "microphone_transcription": f"Whisper {os.getenv('VOICE_WHISPER_MODEL', 'base')} (CPU local int8)",
            "asr_transcription": f"{WHISPERX_MODEL} (Replicate - 1º CSV)",
            "llm_reasoning": f"{DEFAULT_LLM_MODEL} (OpenRouter - 1º CSV)",
            "speaker_diarization": "pyannote.audio vía WhisperX con token HuggingFace",
            "semantic_search": f"ChromaDB + {EMBEDDING_MODEL}",
            "tts_synthesis": os.getenv("TTS_PROVIDER", "piper"),
        },
        "api_keys_status": {
            "replicate_whisperx": replicate_status,
            "openrouter_gemma": openrouter_status,
            "huggingface_diarization": hf_status,
        },
        "ffmpeg_installed": bool(shutil.which("ffmpeg")),
        "recent_timings": recent_timings(),
        "job_storage": "single_process_in_memory_ttl_1h",
        "available_tools": [
            "media_voice_status", "media_transcribe_microphone", "media_voice_reply",
            "media_process", "media_speak", "media_job_status", "media_job_cancel",
            "media_transcribe",
            "media_analyze_video",
            "media_ocr_video",
            "media_index_visual",
            "media_forget_video",
            "media_search_visual",
            "media_describe_scene",
            "media_crop_subject",
            "media_identify_person_web",
            "media_speakers",
            "media_find_all_visual",
            "media_summarize",
            "media_clips",
            "media_search",
            "media_tts",
            "get_server_info",
        ],
        "indexed_test_vectors": available_vectors,
        "total_indexed_segments": total_segments,
        "vector_index": vector_index,
        "local_video_files": local_videos,
        "output_clips_generated": generated_clips,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "server_pid": os.getpid(),
    }
