import pytest
from pathlib import Path
from unittest.mock import MagicMock

import server
from media_core.services import tts as tts_service
from media_core.tools import tts


def test_normalize_language():
    assert tts_service.normalize_language("es") == "Spanish"
    assert tts_service.normalize_language("spanish") == "Spanish"
    assert tts_service.normalize_language("español") == "Spanish"
    assert tts_service.normalize_language("ca") == "Catalan"
    assert tts_service.normalize_language("catalan") == "Catalan"
    assert tts_service.normalize_language("val") == "Catalan"
    assert tts_service.normalize_language("en") == "English"
    assert tts_service.normalize_language("unknown_lang") == "unsupported"


def test_media_tts_validates_empty_input():
    result = tts.media_tts(text="", video_id="")
    assert result["status"] == "error"
    assert "Debe especificar un texto" in result["message"]


def test_media_tts_synthesize_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_service, "get_replicate_token", lambda: "mock_token")
    monkeypatch.setattr(tts_service, "OUTPUT_AUDIO_DIR", tmp_path)

    # Mock Replicate Client
    mock_client = MagicMock()
    mock_client.run.return_value = "https://mock.replicate.delivery/audio.wav"
    monkeypatch.setattr("replicate.Client", lambda api_token: mock_client)

    # Mock urlretrieve to create a dummy audio file
    def mock_urlretrieve(url, filename):
        with open(filename, "wb") as f:
            f.write(b"RIFFdummywavdata")

    monkeypatch.setattr("urllib.request.urlretrieve", mock_urlretrieve)
    monkeypatch.setattr(tts_service, "convert_wav_to_mp3", lambda *args, **kwargs: False)
    monkeypatch.setattr(tts_service, "get_audio_duration", lambda path: 4.25)

    result = tts.media_tts(
        text="Buenos días, bienvenidos a MedIA.",
        language="es",
        speaker="Dylan", provider="qwen",
    )

    assert result["status"] == "success"
    assert "audio_file" in result
    assert result["duration_seconds"] == 4.25
    assert result["duration"] == "00:00:04"
    assert result["language_requested"] == "Spanish"
    assert result["speaker"] == "Dylan"
    assert mock_client.run.called


def test_media_tts_with_video_id(monkeypatch):
    monkeypatch.setattr(
        "media_core.tools.summarize.media_summarize",
        lambda video_id, style, language: {
            "status": "success",
            "executive_summary": "Resumen generado de prueba para locución.",
        },
    )

    captured = {}

    def mock_synthesize(text, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return {"status": "success", "text_synthesized": text}

    monkeypatch.setattr(tts, "synthesize_speech", mock_synthesize)

    result = tts.media_tts(video_id="TV05", summary_style="ejecutivo")
    assert result["status"] == "success"
    assert captured["text"] == "Resumen generado de prueba para locución."
    assert "output_filename" in captured


def test_media_tts_registered_in_server():
    import inspect
    from media_core.tools import PUBLIC_TOOLS

    assert tts.media_tts in PUBLIC_TOOLS
    tool_names = [t.__name__ for t in PUBLIC_TOOLS]
    assert "media_tts" in tool_names
