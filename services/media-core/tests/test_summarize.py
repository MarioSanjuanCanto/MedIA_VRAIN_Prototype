import asyncio
import json

import server
from media_core.services import openrouter
from media_core.tools import summarize


def test_media_summarize_uses_mocked_openrouter(monkeypatch):
    monkeypatch.setattr(summarize, "resolve_video_file", lambda video_id: None)
    monkeypatch.setattr(
        summarize,
        "load_test_vector",
        lambda video_id: {
            "id": video_id,
            "title": "Prueba",
            "segments": [{"text": "Contenido audiovisual"}],
        },
    )
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "token")
    monkeypatch.setattr(
        summarize,
        "call_openrouter",
        lambda *args, **kwargs: (
            '```json\n{"executive_summary":"Resumen","key_points":["Uno"],'
            '"main_topics":["Tema"]}\n```'
        ),
    )

    result = summarize.media_summarize(video_id="TV01")

    assert result["status"] == "success"
    assert result["executive_summary"] == "Resumen"
    assert result["key_points"] == ["Uno"]


def test_media_summarize_uses_query_as_summary_focus(monkeypatch):
    captured_messages = []

    monkeypatch.setattr(summarize, "resolve_video_file", lambda video_id: None)
    monkeypatch.setattr(
        summarize,
        "load_test_vector",
        lambda video_id: {
            "title": "Prueba",
            "segments": [{"text": "Contenido audiovisual"}],
        },
    )
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "token")

    def fake_openrouter(messages, **kwargs):
        captured_messages.extend(messages)
        return '{"executive_summary":"Resumen","key_points":[],"main_topics":[]}'

    monkeypatch.setattr(summarize, "call_openrouter", fake_openrouter)

    result = summarize.media_summarize(
        video_id="TV01",
        query="¿Qué inversión y presupuesto se mencionan?",
    )

    assert result["status"] == "success"
    assert "¿Qué inversión y presupuesto se mencionan?" in captured_messages[1]["content"]
    assert "prioriza" in captured_messages[1]["content"].lower()


def test_media_summarize_exposes_query_in_mcp_schema():
    tools = asyncio.run(server.mcp.list_tools())
    tool = next(item for item in tools if item.name == "media_summarize")

    assert tool.inputSchema["properties"]["query"]["default"] == ""


def test_media_summarize_falls_back_when_api_fails(monkeypatch):
    monkeypatch.setattr(summarize, "resolve_video_file", lambda video_id: None)
    monkeypatch.setattr(
        summarize,
        "load_test_vector",
        lambda video_id: {"title": "Prueba", "segments": [{"text": "Texto"}]},
    )
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "token")
    monkeypatch.setattr(summarize, "call_openrouter", lambda *args, **kwargs: None)

    result = summarize.media_summarize(video_id="TV01")

    assert result["status"] == "error"
    assert "ejemplo" in result["message"]


def test_media_summarize_reads_transcription_cache(tmp_path, monkeypatch):
    video = tmp_path / "TV02.mp4"
    video.touch()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "TV02_whisperx.json").write_text(
        json.dumps({"full_text": "Texto en caché"}), encoding="utf-8"
    )

    monkeypatch.setattr(summarize, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(summarize, "resolve_video_file", lambda video_id: video)
    monkeypatch.setattr(summarize, "load_test_vector", lambda video_id: None)
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "")

    result = summarize.media_summarize(video_id="TV02")

    assert result["status"] == "error"


def test_media_summarize_reports_missing_text(monkeypatch):
    monkeypatch.setattr(summarize, "resolve_video_file", lambda video_id: None)
    monkeypatch.setattr(summarize, "load_test_vector", lambda video_id: None)

    result = summarize.media_summarize(video_id="missing")

    assert result["status"] == "error"


def test_openrouter_returns_none_without_key(monkeypatch):
    monkeypatch.setattr(openrouter, "get_openrouter_key", lambda: "")

    assert openrouter.call_openrouter([]) is None


def test_openrouter_parses_successful_response(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "respuesta"}}]}
            ).encode()

    monkeypatch.setattr(openrouter, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(
        openrouter.urllib.request, "urlopen", lambda request, timeout: FakeResponse()
    )

    assert openrouter.call_openrouter([], json_mode=True) == "respuesta"


def test_summary_retries_invalid_json_once(monkeypatch):
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "key")
    calls = []
    def respond(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '{"executive_summary":"Texto "roto""}'
        return json.dumps({"executive_summary":"Texto correcto", "key_points":[], "main_topics":[]})
    monkeypatch.setattr(summarize, "call_openrouter", respond)
    result = summarize.media_summarize(transcript_text="Fuente original", language="ca")
    assert result["status"] == "success"
    assert result["format_retry"] is True
    assert len(calls) == 2
    assert "Fuente original" in calls[1][1]["content"]


def test_summary_invalid_schema_stops_after_two_attempts(monkeypatch):
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "key")
    calls = []
    def respond(*args, **kwargs):
        calls.append(1)
        return json.dumps({"executive_summary":"Texto", "key_points":"incorrecto", "main_topics":[]})
    monkeypatch.setattr(summarize, "call_openrouter", respond)
    result = summarize.media_summarize(transcript_text="Fuente")
    assert result["error_code"] == "invalid_summary_json"
    assert len(calls) == 2


def test_summary_valid_response_needs_one_call(monkeypatch):
    monkeypatch.setattr(summarize, "get_openrouter_key", lambda: "key")
    calls = []
    def respond(*args, **kwargs):
        calls.append(1)
        return json.dumps({"executive_summary":"Texto", "key_points":[], "main_topics":[]})
    monkeypatch.setattr(summarize, "call_openrouter", respond)
    result = summarize.media_summarize(transcript_text="Fuente")
    assert result["status"] == "success"
    assert result["format_retry"] is False
    assert len(calls) == 1
