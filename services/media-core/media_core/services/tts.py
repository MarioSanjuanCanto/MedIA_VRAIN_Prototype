"""Qwen3-TTS speech synthesis service using Replicate with tuned language profiles."""

import os
import hashlib
import json
import threading
import uuid
import wave
from media_core.runtime import measured, stage

_TTS_LOCK = threading.RLock()
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from media_core.config import OUTPUT_AUDIO_DIR, QWEN_TTS_MODEL, get_replicate_token
from media_core.usage import record_usage
from media_core.formatting import format_seconds

LANGUAGE_MAPPING = {
    "auto": "auto",
    "es": "Spanish",
    "spanish": "Spanish",
    "español": "Spanish",
    "castellano": "Spanish",
    "es_es": "Spanish",
    "es-es": "Spanish",
    "ca-es": "Catalan",
    "ca_es": "Catalan",
    "en-us": "English",
    "en-gb": "English",
    "spain": "Spanish",
    "ca": "Catalan",
    "cat": "Catalan",
    "catalan": "Catalan",
    "català": "Catalan",
    "val": "Catalan",
    "valencian": "Catalan",
    "valencià": "Catalan",
    "en": "English",
    "english": "English",
    "ingles": "English",
    "inglés": "English",
    "fr": "French",
    "french": "French",
    "de": "German",
    "german": "German",
    "it": "Italian",
    "italian": "Italian",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "zh": "Chinese",
    "chinese": "Chinese",
    "ja": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "korean": "Korean",
    "ru": "Russian",
    "russian": "Russian",
}

AVAILABLE_SPEAKERS = [
    "Dylan",
    "Serena",
    "Aiden",
    "Eric",
    "Ono_anna",
    "Ryan",
    "Sohee",
    "Uncle_fu",
    "Vivian",
]

# Fixed, optimized profiles for each language ensuring clear, informative, and brisk broadcast delivery
LANGUAGE_PROFILES = {
    "Spanish": {
        "mode": "custom_voice",
        "replicate_lang": "Spanish",
        "default_speaker": "Aiden",
        "voice_description_male": (
            "A native male speaker of European Spanish from Spain, professional television news anchor, "
            "clear Castilian Spanish accent with natural distinction of s and z, sharp diction, "
            "speaking in an authoritative, clear, and informative tone."
        ),
        "voice_description_female": (
            "A native female speaker of European Spanish from Spain, professional television news journalist, "
            "clear Castilian Spanish accent with sharp diction, speaking in an articulate, clear and dynamic news tone."
        ),
        "style_instruction": (
            "Locución clara, informativa y profesional con dicción neutra y ritmo natural."
        ),
        "speed_factor": 1.0,
    },
    "English": {
        "mode": "custom_voice",
        "replicate_lang": "English",
        "default_speaker": "Aiden",
        "voice_description_male": "",
        "voice_description_female": "",
        "style_instruction": (
            "Speak in a clear, informative news broadcast style, with sharp articulation and a brisk, energetic, fast-paced delivery."
        ),
        "speed_factor": 1.0,
    },
    "Catalan": {
        "mode": "unsupported",
        "replicate_lang": None,
        "default_speaker": "Dylan",
        "voice_description_male": "",
        "voice_description_female": "",
        "style_instruction": (
            "Locución clara, informativa y profesional con dicción neutra y ritmo natural."
        ),
        "speed_factor": 1.0,
    },
    "auto": {
        "mode": "custom_voice",
        "replicate_lang": "auto",
        "default_speaker": "Dylan",
        "voice_description_male": "",
        "voice_description_female": "",
        "style_instruction": "Locución clara, informativa y profesional con dicción precisa y ritmo natural.",
        "speed_factor": 1.0,
    },
}


def normalize_language(language: str) -> str:
    """Normalize language code or name to internal language profile identifier."""
    clean = language.strip().lower()
    return LANGUAGE_MAPPING.get(clean, "unsupported")


def get_audio_duration(audio_file: Path) -> float:
    """Extract audio duration in seconds using ffprobe, fallback to 0.0."""
    try:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_file),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def convert_wav_to_mp3(
    wav_path: Path, mp3_path: Path, speed_factor: float = 1.0
) -> bool:
    """Convert WAV audio to standard MP3 format using ffmpeg with optional pitch-preserving speedup."""
    try:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
        ]
        if speed_factor and abs(speed_factor - 1.0) > 0.01:
            command.extend(["-filter:a", f"atempo={speed_factor:.2f}"])
        command.extend([
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(mp3_path),
        ])
        result = subprocess.run(command, capture_output=True, timeout=30)
        return result.returncode == 0 and mp3_path.exists()
    except Exception:
        return False


def _synthesize_qwen(
    text: str,
    language: str = "es",
    speaker: str = "",
    style_instruction: str = "",
    voice_description: str = "",
    reference_audio: str = "",
    speed_factor: float | None = None,
    output_filename: str = "",
    format_mp3: bool = True,
) -> dict:
    """Synthesize text into speech using Qwen3-TTS on Replicate with tuned language profiles.

    Args:
        text: Plain text to synthesize.
        language: Language identifier ('es' for European Spanish, 'ca'/'val', 'en', 'auto', etc.).
        speaker: Voice name ('Dylan', 'Serena', 'Aiden', 'Eric', etc.). If empty, chosen from language profile.
        style_instruction: Optional delivery style override.
        voice_description: Optional voice description for 'voice_design' mode.
        reference_audio: Optional audio URL for 'voice_clone' mode.
        speed_factor: Optional tempo multiplier (e.g. 1.15 for brisk news pace). Defaults to profile.
        output_filename: Optional custom filename for the generated audio.
        format_mp3: If True and ffmpeg is available, converts WAV to MP3.

    Returns:
        dict with status, audio_file path, duration, size_mb, language, and speaker.
    """
    clean_text = text.strip()
    if not clean_text:
        return {
            "status": "error",
            "message": "El texto para síntesis de voz no puede estar vacío.",
        }

    if normalize_language(language) in {"Catalan", "unsupported"}:
        return {"status": "error", "message": "Qwen3-TTS no admite este idioma. Usa Piper para catalán."}

    token = get_replicate_token()
    if not token:
        return {
            "status": "error",
            "message": "REPLICATE_API_KEY no configurada. Configure el token en el archivo .env.",
        }

    import replicate

    client = replicate.Client(api_token=token)

    # Determine language profile
    target_lang = normalize_language(language)
    profile = LANGUAGE_PROFILES.get(target_lang, LANGUAGE_PROFILES["auto"])

    replicate_lang = profile["replicate_lang"]
    final_speed = speed_factor if speed_factor is not None else profile.get("speed_factor", 1.0)

    # Selected speaker and gender detection
    selected_speaker = speaker if speaker in AVAILABLE_SPEAKERS else profile["default_speaker"]
    is_female = selected_speaker in {"Serena", "Vivian", "Sohee", "Ono_anna"}

    # Build input payload
    if reference_audio:
        mode = "voice_clone"
        replicate_input: dict = {
            "text": clean_text,
            "mode": mode,
            "language": replicate_lang,
            "reference_audio": reference_audio,
        }
    elif voice_description:
        mode = "voice_design"
        replicate_input = {
            "text": clean_text,
            "mode": mode,
            "language": replicate_lang,
            "voice_description": voice_description,
        }
    elif speaker and speaker in AVAILABLE_SPEAKERS:
        mode = "custom_voice"
        replicate_input = {
            "text": clean_text,
            "mode": mode,
            "language": replicate_lang,
            "speaker": selected_speaker,
        }
    elif profile.get("mode") == "voice_design":
        mode = "voice_design"
        desc = profile["voice_description_female"] if is_female else profile["voice_description_male"]
        replicate_input = {
            "text": clean_text,
            "mode": mode,
            "language": replicate_lang,
            "voice_description": desc,
        }
    else:
        mode = "custom_voice"
        replicate_input = {
            "text": clean_text,
            "mode": mode,
            "language": replicate_lang,
            "speaker": selected_speaker,
        }

    # Style instruction priority: user override > profile default
    final_style = style_instruction.strip() if style_instruction else profile.get("style_instruction", "")
    if final_style:
        replicate_input["style_instruction"] = final_style

    try:
        output = client.run(QWEN_TTS_MODEL, input=replicate_input)
        try:
            qwen_rate = float(os.getenv("REPLICATE_QWEN_TTS_USD_PER_1000_CHARS", "0.02"))
        except ValueError:
            qwen_rate = 0.02
        record_usage("replicate", QWEN_TTS_MODEL, len(clean_text) / 1000 * qwen_rate,
            operation="text_to_speech", input_characters=len(clean_text), cost_type="estimated")
    except Exception as error:
        return {
            "status": "error",
            "message": f"Error al invocar Qwen3-TTS en Replicate: {error}",
        }

    audio_url = str(output)
    if not audio_url.startswith("http"):
        return {
            "status": "error",
            "message": f"Respuesta inesperada de Replicate: {output}",
        }

    # Output file paths
    OUTPUT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    if output_filename:
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", output_filename.strip())
        base_name = safe_name.rsplit(".", 1)[0]
    else:
        text_slug = re.sub(r"[^a-zA-Z0-9]+", "_", clean_text[:25]).strip("_").lower()
        base_name = f"tts_{target_lang.lower()}_{selected_speaker.lower()}_{text_slug or 'audio'}"

    wav_path = OUTPUT_AUDIO_DIR / f"{base_name}.wav"
    mp3_path = OUTPUT_AUDIO_DIR / f"{base_name}.mp3"

    try:
        urllib.request.urlretrieve(audio_url, str(wav_path))
    except Exception as error:
        return {
            "status": "error",
            "message": f"Error al descargar el audio generado desde Replicate: {error}",
            "audio_url": audio_url,
        }

    final_path = wav_path
    if format_mp3 and convert_wav_to_mp3(wav_path, mp3_path, speed_factor=final_speed):
        final_path = mp3_path
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

    duration_seconds = get_audio_duration(final_path)
    size_mb = round(final_path.stat().st_size / (1024 * 1024), 2)

    return {
        "status": "success",
        "audio_file": str(final_path),
        "duration_seconds": round(duration_seconds, 2),
        "duration": format_seconds(duration_seconds),
        "size_mb": size_mb,
        "language_requested": target_lang,
        "speaker": selected_speaker,
        "mode": mode,
        "speed_factor": final_speed,
        "model_used": QWEN_TTS_MODEL,
        "text_synthesized": clean_text[:120] + ("..." if len(clean_text) > 120 else ""),
    }


@measured
def synthesize_speech(
    text: str, language: str = "es", speaker: str = "",
    style_instruction: str = "", voice_description: str = "",
    reference_audio: str = "", speed_factor: float | None = None,
    output_filename: str = "", format_mp3: bool = False, provider: str = "auto",
) -> dict:
    """Route explicit language to a configured backend; local CPU Piper is the default."""
    clean = text.strip()
    target = normalize_language(language)
    if not clean or len(clean) > 20000:
        return {"status": "error", "message": "El texto debe tener entre 1 y 20000 caracteres."}
    selected = os.getenv("TTS_PROVIDER", "piper") if provider == "auto" else provider
    if selected == "qwen":
        with stage("tts_remote"):
            return _synthesize_qwen(text, language, speaker, style_instruction, voice_description,
                                    reference_audio, speed_factor, output_filename, format_mp3)
    if target not in {"Spanish", "Catalan", "English"}:
        return {"status": "error", "message": "Indica language='es', 'ca' o 'en'. No se adivina el idioma ni se traduce el texto."}
    if selected not in {"piper", "http"}:
        return {"status": "error", "message": "Proveedor desconocido: usa piper, http o qwen."}
    speed = 1.0 if speed_factor is None else speed_factor
    if not 0.5 <= speed <= 2.0:
        return {"status": "error", "message": "speed_factor debe estar entre 0.5 y 2.0"}
    if selected == "piper" and any((style_instruction, voice_description, reference_audio)):
        return {"status": "error", "message": "Piper usa voces fijas; no admite clonación ni instrucciones de estilo. Usa provider='qwen' para inglés/español."}
    try:
        from media_core.services import piper_tts
        model = piper_tts.model_path(target) if selected == "piper" else None
        model_signature = [str(model), model.stat().st_mtime_ns] if model and model.exists() else str(model)
        endpoint = os.getenv("TTS_HTTP_URL", "")
        if selected == "http" and not endpoint:
            raise ValueError("Configura TTS_HTTP_URL (contrato POST JSON -> audio WAV).")
        key = hashlib.sha256(json.dumps([clean, target, speaker, speed, selected, model_signature,
            endpoint, os.getenv("TTS_HTTP_MODEL", ""), reference_audio, voice_description, style_instruction,
            format_mp3], ensure_ascii=False).encode()).hexdigest()[:24]
        OUTPUT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_AUDIO_DIR / ("tts_" + key + (".mp3" if format_mp3 else ".wav"))
        # Serialize generations on CPU, deduplicate identical simultaneous requests.
        with _TTS_LOCK:
            cached = path.exists() and path.stat().st_size > 44
            if not cached:
                temporary = OUTPUT_AUDIO_DIR / (uuid.uuid4().hex + ".wav")
                try:
                    if selected == "piper":
                        piper_tts.synthesize(clean, target, speaker, speed, temporary)
                    else:
                        import httpx
                        with stage("tts_remote"):
                            response = httpx.post(endpoint, json={"text": clean, "language": language,
                                "voice": speaker, "speed": speed, "reference_audio": reference_audio,
                                "style_instruction": style_instruction, "voice_description": voice_description},
                                timeout=httpx.Timeout(120, connect=5))
                            response.raise_for_status()
                            temporary.write_bytes(response.content)
                    with wave.open(str(temporary)) as audio:
                        if audio.getnframes() == 0:
                            raise ValueError("El proveedor devolvió audio vacío")
                    if format_mp3:
                        if not convert_wav_to_mp3(temporary, path, speed_factor=1.0):
                            path.unlink(missing_ok=True)
                            raise ValueError("No se pudo convertir a MP3")
                    else:
                        temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            if output_filename:
                label = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(output_filename).stem)[:60] or "audio"
                named = OUTPUT_AUDIO_DIR / (label + "_" + key + path.suffix)
                if not named.exists():
                    shutil.copyfile(path, named)
                path = named
        with stage("audio_metadata"):
            if path.suffix == ".wav":
                with wave.open(str(path)) as audio:
                    duration = audio.getnframes() / audio.getframerate()
                    sample_rate = audio.getframerate()
            else:
                duration, sample_rate = get_audio_duration(path), None
        return {"status": "success", "audio_file": str(path), "duration_seconds": round(duration, 3),
            "duration": format_seconds(duration), "sample_rate": sample_rate, "format": path.suffix[1:],
            "size_mb": round(path.stat().st_size / 1048576, 3), "language_requested": target,
            "speaker": speaker or "0", "provider": selected, "model_used": str(model) if model else os.getenv("TTS_HTTP_MODEL", "configured-http"),
            "cache_hit": cached, "speed_factor": speed, "mode": "fixed_voice" if selected == "piper" else "remote",
            "text_synthesized": clean[:120]}
    except Exception as error:
        return {"status": "error", "message": str(error), "provider": selected}
