"""Voice tools for the small open source chat avatar."""
from media_core.services.local_voice import spoken_reply, transcribe_microphone, voice_status


def media_voice_status() -> dict:
    """Comprueba Whisper y Piper locales sin cargar modelos ni descargar nada."""
    return voice_status()


def media_transcribe_microphone(audio_base64: str, language: str = "es") -> dict:
    """Transcribe una grabación de micrófono con Whisper CPU local; máximo 8 MB y 60 segundos."""
    try:
        return transcribe_microphone(audio_base64, language)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def media_voice_reply(answer: str, language: str = "es", spoken_text: str = "") -> dict:
    """Resume una respuesta en máximo dos frases y 45 palabras y la locuta con Piper local.

    El resumen es extractivo local, sin llamadas a un LLM adicional. La síntesis permanece en CPU local.
    No sustituye media_tts para locuciones completas pedidas explícitamente.
    """
    try:
        return spoken_reply(answer, language, spoken_text)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
