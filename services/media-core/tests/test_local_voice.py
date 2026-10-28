import base64
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest
from media_core.services import local_voice as v


def test_rejects_invalid_audio_before_loading_model():
    with pytest.raises(ValueError, match="Audio no válido"):
        v.transcribe_microphone("not base64!")
    with pytest.raises(ValueError, match="vacía"):
        v.transcribe_microphone("")
    with pytest.raises(ValueError, match="8 MB"):
        v.transcribe_microphone("a" * (12 * 1024 * 1024))


def test_microphone_filters_silence_and_does_not_keep_recordings(tmp_path, monkeypatch):
    (tmp_path / "model.bin").touch()
    monkeypatch.setattr(v, "MODEL_DIR", tmp_path)
    decoded_paths = []

    def decode(args, **kwargs):
        assert "-protocol_whitelist" in args
        path = Path(args[-1]); decoded_paths.append(path)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 16000)
        return SimpleNamespace(returncode=0)

    class Model:
        def transcribe(self, path, **kwargs):
            assert kwargs["vad_filter"] and kwargs["language"] == "ca"
            return iter([SimpleNamespace(text="Mostra els vídeos.", no_speech_prob=0.1, avg_logprob=-0.1),
                         SimpleNamespace(text="Hallucinated silence", no_speech_prob=0.9, avg_logprob=-0.1)]), SimpleNamespace(language="ca")
    monkeypatch.setattr(v.subprocess, "run", decode)
    monkeypatch.setattr(v, "_model", Model())
    result = v.transcribe_microphone(base64.b64encode(b"recording").decode(), "ca")
    assert result["text"] == "Mostra els vídeos."
    assert result["provider"] == "whisper-local"
    assert all(not p.exists() for p in decoded_paths)


def test_rejects_recording_over_one_minute(tmp_path, monkeypatch):
    (tmp_path / "model.bin").touch()
    monkeypatch.setattr(v, "MODEL_DIR", tmp_path)
    def decode(args, **kwargs):
        with wave.open(args[-1], "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\0\0" * (16000 * 61))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(v.subprocess, "run", decode)
    with pytest.raises(ValueError, match="60 segundos"):
        v.transcribe_microphone(base64.b64encode(b"audio").decode())


def test_summary_is_short_removes_paths_and_preserves_numbers():
    assert v.speech_text("5 clips encontrados.") == "5 clips encontrados."
    result = v.bounded_summary("**He encontrado 5 clips.** Están ordenados. No leer esta tercera frase.")
    assert result == "He encontrado 5 clips. Están ordenados."
    long = v.bounded_summary(" ".join(["palabra"] * 300))
    assert len(long.split()) <= 45 and len(long) <= 320
    assert "http" not in v.speech_text("[Clip](https://host/video) https://host/otro")


def test_reply_uses_local_piper_and_bounds_output(monkeypatch):
    from media_core.services import tts
    calls = []
    def synthesize(text, **kwargs):
        calls.append((text, kwargs))
        return {"status": "success", "audio_file": "reply.wav"}
    monkeypatch.setattr(tts, "synthesize_speech", synthesize)
    reply = v.spoken_reply(" ".join(["resultat"] * 200), "ca")
    assert reply["summary_words"] <= 45
    assert calls[0][1] == {"language": "ca", "provider": "piper", "format_mp3": False}


def test_reply_is_extractive_and_never_calls_remote_model(monkeypatch):
    from media_core.services import openrouter, tts
    def fail(**kwargs): raise RuntimeError("offline")
    monkeypatch.setattr(openrouter, "call_openrouter", fail)
    monkeypatch.setattr(tts, "synthesize_speech", lambda text, **kwargs: {"status": "success"})
    reply = v.spoken_reply("No se pudieron generar los clips.", "es")
    assert reply["spoken_text"] == "No se pudieron generar los clips."
    assert reply["summary_provider"] == "local-extractive"
    assert "clips generados" not in reply["spoken_text"]


def test_conversational_voice_does_not_read_the_written_answer(monkeypatch):
    from media_core.services import tts
    captured = []
    monkeypatch.setattr(tts, "synthesize_speech", lambda text, **kwargs: captured.append(text) or {"status": "success"})
    result = v.spoken_reply("Una tabla de cien filas.", "es", "He encontrado cinco intervenciones. ¿Quieres escuchar la primera?")
    assert captured == ["He encontrado cinco intervenciones. ¿Quieres escuchar la primera?"]
    assert result["summary_provider"] == "conversation"
