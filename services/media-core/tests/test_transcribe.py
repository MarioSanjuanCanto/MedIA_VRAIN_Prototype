import json
from types import SimpleNamespace

import replicate

from media_core import formatting
from media_core.services import ffmpeg, whisperx
from media_core.tools import transcribe


def test_media_transcribe_returns_cached_transcription(tmp_path, monkeypatch):
    video = tmp_path / "TV99.mp4"
    video.touch()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "TV99_whisperx.json").write_text(
        json.dumps(
            {
                "id": "TV99",
                "indexed_in_search": True,
                "request_metadata": {"source": [str(video.resolve()), video.stat().st_size, video.stat().st_mtime_ns], "language": "es", "diarization": True},
                "title": "Cached video",
                "language": "es",
                "duration": "00:00:02",
                "duration_seconds": 2,
                "speakers": ["SPEAKER_00"],
                "full_text": "Texto guardado",
                "segments": [
                    {
                        "start": "00:00:00",
                        "end": "00:00:02",
                        "speaker": "SPEAKER_00",
                        "text": "Texto guardado",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(transcribe, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda source: video)
    monkeypatch.setattr(
        transcribe,
        "transcribe_with_whisperx",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("API called")),
    )
    indexed = []
    monkeypatch.setattr(
        transcribe, "index_test_vector", lambda data: indexed.append(data["id"])
    )

    result = transcribe.media_transcribe(source="TV99")

    assert result["status"] == "success"
    assert result["video_id"] == "TV99"
    assert result["source"] == str(video.resolve())
    assert result["transcription_preview"] == "Texto guardado"
    assert indexed == []
    assert result["cache_hit"] is True


def test_media_transcribe_reports_missing_source(monkeypatch):
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda source: None)
    monkeypatch.setattr(transcribe, "get_replicate_token", lambda: "")
    monkeypatch.setattr(transcribe, "load_test_vector", lambda video_id: None)

    result = transcribe.media_transcribe(source="missing")

    assert result["status"] == "error"
    assert "missing" in result["message"]


def test_media_transcribe_handles_ffmpeg_failure(tmp_path, monkeypatch):
    video = tmp_path / "TV98.mp4"
    video.touch()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    monkeypatch.setattr(transcribe, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda source: video)
    monkeypatch.setattr(transcribe, "get_replicate_token", lambda: "token")
    monkeypatch.setattr(transcribe, "extract_audio", lambda source, target: False)
    monkeypatch.setattr(transcribe, "load_test_vector", lambda video_id: None)

    result = transcribe.media_transcribe(source="TV98")

    assert result["status"] == "error"


def test_media_transcribe_persists_successful_result(tmp_path, monkeypatch):
    video = tmp_path / "TV97.mp4"
    video.write_bytes(b"video")
    cache_dir = tmp_path / "cache"
    vectors_dir = tmp_path / "vectors"
    cache_dir.mkdir()
    vectors_dir.mkdir()

    def fake_extract(source, target):
        target.write_bytes(b"audio")
        return True

    monkeypatch.setattr(transcribe, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", vectors_dir)
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda source: video)
    monkeypatch.setattr(transcribe, "get_replicate_token", lambda: "token")
    monkeypatch.setattr(transcribe, "extract_audio", fake_extract)
    monkeypatch.setattr(
        transcribe,
        "transcribe_with_whisperx",
        lambda *args, **kwargs: {
            "status": "success",
            "language": "es",
            "speakers": ["SPEAKER_00"],
            "full_text": "Resultado",
            "segments": [{"text": "Resultado"}],
        },
    )
    indexed = []
    monkeypatch.setattr(
        transcribe, "index_test_vector", lambda data: indexed.append(data["id"])
    )

    result = transcribe.media_transcribe(source="TV97", full_segments=True)

    assert result["id"] == "TV97"
    assert (cache_dir / "TV97_whisperx.json").exists()
    assert (vectors_dir / "TV97.json").exists()
    assert not (cache_dir / "TV97_temp.mp3").exists()
    assert indexed == ["TV97"]
    stored = json.loads((cache_dir / "TV97_whisperx.json").read_text())
    assert stored["request_metadata"]["source"]["algorithm"] == "sha256"
    assert stored["request_metadata"]["source"]["sha256"]
    assert str(video.resolve()) not in json.dumps(stored["request_metadata"]["source"])


def test_media_transcribe_survives_vector_index_failure(tmp_path, monkeypatch):
    video = tmp_path / "TV96.mp4"
    video.touch()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "TV96_whisperx.json").write_text(
        json.dumps({"id": "TV96", "status": "success", "segments": [], "full_text": "Texto",
            "request_metadata": {"source": [str(video.resolve()), video.stat().st_size, video.stat().st_mtime_ns], "language": "es", "diarization": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(transcribe, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(transcribe, "resolve_video_file", lambda source: video)
    monkeypatch.setattr(
        transcribe,
        "index_test_vector",
        lambda data: (_ for _ in ()).throw(RuntimeError("Chroma unavailable")),
    )

    result = transcribe.media_transcribe(source="TV96")

    assert result["status"] == "success"


def test_build_transcribe_response_can_return_full_payload():
    payload = {"status": "success", "segments": []}

    assert formatting.build_transcribe_agent_response(payload, True) is payload


def test_extract_audio_uses_ffmpeg(tmp_path, monkeypatch):
    output = tmp_path / "audio.mp3"

    def fake_run(command, **kwargs):
        output.write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    assert ffmpeg.extract_audio(tmp_path / "video.mp4", output) is True


def test_extract_audio_handles_process_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ffmpeg.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert ffmpeg.extract_audio(tmp_path / "video.mp4", tmp_path / "audio.mp3") is False


def test_whisperx_normalizes_segments_without_diarization(tmp_path, monkeypatch):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    captured = {}

    class FakeClient:
        def __init__(self, api_token):
            captured["token"] = api_token
            self._client = SimpleNamespace(timeout=None)

        def run(self, model, input):
            captured["model"] = model
            captured["input"] = input.copy()
            return {
                "detected_language": "es",
                "segments": [
                    {"start": 0.2, "end": 2.6, "text": " Hola ", "speaker": None}
                ],
            }

    monkeypatch.setattr(whisperx, "get_replicate_token", lambda: "token")
    monkeypatch.setattr(whisperx, "get_hf_token", lambda: "")
    monkeypatch.setattr(replicate, "Client", FakeClient)

    result = whisperx.transcribe_with_whisperx(audio, diarization=False)

    assert result["status"] == "success"
    assert result["duration"] == "00:00:03"
    assert result["full_text"] == "Hola"
    assert result["segments"][0]["speaker"] == "Hablante"
    assert captured["input"]["diarization"] is False


def test_whisperx_requires_replicate_token(tmp_path, monkeypatch):
    monkeypatch.setattr(whisperx, "get_replicate_token", lambda: "")

    assert whisperx.transcribe_with_whisperx(tmp_path / "audio.mp3") is None
