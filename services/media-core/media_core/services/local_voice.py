"""Local microphone transcription and short spoken replies. No remote ASR/TTS."""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import wave

from media_core.config import DATA_DIR

MAX_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 60
MODEL_NAME = os.getenv("VOICE_WHISPER_MODEL", "base")
MODEL_DIR = DATA_DIR / "models" / "voice-whisper" / MODEL_NAME
_lock = threading.Lock()
_model = None


def voice_status() -> dict:
    from media_core.services.piper_tts import model_path
    return {"status": "success", "asr": "faster-whisper", "model": MODEL_NAME,
            "local": True, "device": "cpu", "compute_type": "int8",
            "asr_ready": (MODEL_DIR / "model.bin").is_file(),
            "voices": {code: model_path(name).is_file() for code, name in
                       (("es", "Spanish"), ("ca", "Catalan"), ("en", "English"))},
            "max_seconds": MAX_SECONDS, "max_bytes": MAX_BYTES}


def transcribe_microphone(audio_base64: str, language: str = "es") -> dict:
    if language not in {"es", "ca", "en", "auto"}:
        raise ValueError("Idioma no válido.")
    if len(audio_base64) > ((MAX_BYTES + 2) // 3) * 4:
        raise ValueError("La grabación supera 8 MB.")
    try:
        raw = base64.b64decode(audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Audio no válido.") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("La grabación está vacía o supera 8 MB.")
    if not (MODEL_DIR / "model.bin").is_file():
        raise ValueError("Falta Whisper local. Ejecuta python -m media_core.services.local_voice --download.")
    started = time.monotonic()
    # Only one CPU decoder at once; reject a competing request instead of queuing.
    if not _lock.acquire(blocking=False):
        raise ValueError("Whisper está transcribiendo otra grabación. Inténtalo en unos segundos.")
    try:
        with tempfile.TemporaryDirectory(prefix="media-microphone-") as folder:
            source, decoded = Path(folder) / "recording", Path(folder) / "voice.wav"
            source.write_bytes(raw)
            result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                                     "-i", str(source), "-t", "61", "-vn", "-ac", "1", "-ar", "16000", str(decoded)],
                                    capture_output=True, timeout=25)
            if result.returncode or not decoded.is_file():
                raise ValueError("No se pudo leer la grabación de audio.")
            with wave.open(str(decoded)) as wav:
                duration = wav.getnframes() / wav.getframerate()
            if duration > MAX_SECONDS + 0.25:
                raise ValueError("Graba como máximo 60 segundos.")
            global _model
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(str(MODEL_DIR), device="cpu", compute_type="int8",
                                      cpu_threads=max(1, int(os.getenv("VOICE_WHISPER_THREADS", "2"))),
                                      num_workers=1, local_files_only=True)
            segments, info = _model.transcribe(str(decoded), language=None if language == "auto" else language,
                                              beam_size=1, vad_filter=True, condition_on_previous_text=False)
            texts = [s.text.strip() for s in segments
                     if getattr(s, "no_speech_prob", 0) < 0.6 and getattr(s, "avg_logprob", 0) > -1.2]
            return {"status": "success", "text": " ".join(texts).strip(), "language": info.language,
                    "duration_seconds": round(duration, 2), "elapsed_seconds": round(time.monotonic() - started, 2),
                    "provider": "whisper-local"}
    finally:
        _lock.release()


def speech_text(text: str) -> str:
    """Strip markup, links, tables, and filesystem paths before sending text to speech."""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|(?:[A-Za-z]:[\\/]|/api/|/app/|/home/)\S+", " ", text)
    lines = [re.sub(r"^\s*(?:#{1,6}\s+|>\s*|[-*]\s+|\d+[.)]\s+)", "", line)
             for line in text.splitlines() if "|" not in line]
    return re.sub(r"\s+", " ", re.sub(r"[*_`<>]", "", " ".join(lines))).strip()


def bounded_summary(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", speech_text(text))
    words = " ".join(sentences[:2]).split()[:45]
    result = " ".join(words)
    if len(result) > 320:
        result = result[:317].rsplit(" ", 1)[0]
    return result.rstrip(" ,;:") + ("." if result and result[-1] not in ".!?" else "")


def spoken_reply(answer: str, language: str = "es", spoken_text: str = "") -> dict:
    if language not in {"es", "ca", "en"}:
        raise ValueError("Selecciona castellano, catalán o inglés.")
    if not answer.strip() or len(answer) > 100000:
        raise ValueError("La respuesta está vacía o es demasiado larga.")
    from media_core.services.tts import synthesize_speech
    fallback = {"es": "Ya tienes la respuesta en el chat. Puedes consultar allí todos los detalles.",
                "ca": "Ja tens la resposta al xat. Hi pots consultar tots els detalls.",
                "en": "The answer is ready in the chat. You can find all the details there."}
    # Extractive summary: keep the answer's wording, never call another model or translate.
    clean_lines = [line for line in answer.splitlines()
                   if not re.match(r"^\s*#{1,6}\s", line)
                   and not re.fullmatch(r"\s*\*{0,2}(?:Resumen|Resultats?|Results?|Nota)\*{0,2}:?\s*", line, re.I)]
    summary = bounded_summary(spoken_text or "\n".join(clean_lines)) or fallback[language]
    result = synthesize_speech(summary, language=language, provider="piper", format_mp3=False)
    if result.get("status") != "success":
        return result
    return {**result, "spoken_text": summary, "summary_words": len(summary.split()),
            "summary_provider": "conversation" if spoken_text else "local-extractive"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Descarga explícita de Whisper para el micrófono local.")
    parser.add_argument("--download", action="store_true", required=True)
    parser.parse_args()
    if MODEL_NAME not in {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}:
        raise SystemExit("VOICE_WHISPER_MODEL no válido.")
    from huggingface_hub import snapshot_download
    snapshot_download(f"Systran/faster-whisper-{MODEL_NAME}", local_dir=str(MODEL_DIR),
                      allow_patterns=["*.json", "*.txt", "*.bin"])
    print("Whisper local preparado:", MODEL_DIR)
