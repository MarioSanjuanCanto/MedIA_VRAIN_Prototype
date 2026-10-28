"""WhisperX transcription through Replicate."""

import sys
from pathlib import Path

from media_core.config import WHISPERX_MODEL, get_hf_token, get_replicate_token
from media_core.formatting import format_seconds
from media_core.usage import record_usage


def transcribe_with_whisperx(
    audio_file: Path, language: str = "es", diarization: bool = True
) -> dict | None:
    """Invoke the configured WhisperX model through Replicate."""
    token = get_replicate_token()
    if not token:
        print(
            "[WhisperX Error] No se encontró REPLICATE_API_KEY en .env",
            file=sys.stderr,
        )
        return None

    try:
        import httpx
        import replicate

        print(
            f"[WhisperX] Enviando '{audio_file.name}' a Replicate ({WHISPERX_MODEL})..."
        )
        client = replicate.Client(api_token=token)
        if hasattr(client, "_client") and hasattr(client._client, "timeout"):
            client._client.timeout = httpx.Timeout(300.0, connect=300.0)

        hf_token = get_hf_token()
        with audio_file.open("rb") as file:
            input_params = {
                "audio_file": file,
                "diarization": diarization,
                "align_output": True,
            }
            if diarization:
                if hf_token:
                    input_params["huggingface_access_token"] = hf_token
                else:
                    print(
                        "[WhisperX Warning] Diarización solicitada pero no se encontró HUGGINGFACE_ACCESS_TOKEN. Se desactiva diarización para evitar fallo.",
                        file=sys.stderr,
                    )
                    input_params["diarization"] = False
            if language and language.lower() not in ("auto", "none"):
                input_params["language"] = language.lower()

            model_to_call = WHISPERX_MODEL
            if ":" not in model_to_call:
                model_to_call = (
                    f"{model_to_call}:"
                    "655845d6190ef70573c669245f245892cd039df4b880a1e3a65852c09252f5cc"
                )
            output = client.run(model_to_call, input=input_params)
            record_usage("replicate", WHISPERX_MODEL, None, operation="transcription")

        if not output or not isinstance(output, dict):
            return None

        formatted_segments = []
        full_text_parts = []
        max_end = 0.0
        for segment in output.get("segments", []):
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
            max_end = max(max_end, end)
            text = segment.get("text", "").strip()
            if text:
                full_text_parts.append(text)
            formatted_segments.append(
                {
                    "start": format_seconds(start),
                    "end": format_seconds(end),
                    "start_seconds": round(start, 2),
                    "end_seconds": round(end, 2),
                    "speaker": segment.get("speaker") or "Hablante",
                    "text": text,
                }
            )

        speakers = sorted({segment["speaker"] for segment in formatted_segments})
        return {
            "status": "success",
            "diarization": input_params["diarization"],
            "model_used": f"{WHISPERX_MODEL} (Replicate)",
            "language": output.get("detected_language", language),
            "duration": format_seconds(max_end),
            "duration_seconds": round(max_end, 2),
            "speakers": speakers,
            "total_segments": len(formatted_segments),
            "full_text": " ".join(full_text_parts),
            "segments": formatted_segments,
        }
    except Exception as error:
        print(f"[WhisperX Exception] {error}", file=sys.stderr)
        if 'GatedRepoError' in str(error) or ('403' in str(error) and 'pyannote' in str(error)):
            return {"status": "error", "error_code": "diarization_access_denied",
                "message": "Hugging Face deniega el acceso al modelo de diarización (403). La cuenta del token debe tener acceso a pyannote/speaker-diarization-community-1. Puedes transcribir sin separar hablantes."}
        return None
