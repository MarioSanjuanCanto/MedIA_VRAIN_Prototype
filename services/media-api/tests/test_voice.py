import asyncio
import base64
import time
import pytest
from fastapi.testclient import TestClient
from app import main as m


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "voice_slots", asyncio.Semaphore(2))
    return TestClient(m.app)


def test_microphone_goes_only_to_local_mcp(client, monkeypatch):
    calls = []
    async def call(name, arguments):
        calls.append((name, arguments))
        return {"status": "success", "text": "Dame los vídeos."}
    monkeypatch.setattr(m, "mcp_call", call)
    response = client.post("/api/voice/transcribe?language=es", files={"file": ("voice.webm", b"voice", "audio/webm")})
    assert response.status_code == 200
    assert calls == [("media_transcribe_microphone", {"audio_base64": base64.b64encode(b"voice").decode(), "language": "es"})]
    assert client.post("/api/voice/transcribe", files={"file": ("empty", b"")}).status_code == 400
    assert client.post("/api/voice/transcribe", files={"file": ("big", b"x" * (8 * 1024 * 1024 + 1))}).status_code == 413


def test_reply_uses_saved_completed_answer_not_client_text(client, monkeypatch):
    calls = []
    monkeypatch.setattr(m, "get_job", lambda job: {"kind": "hermes_chat", "status": "completed", "answer": "Hay 3 vídeos."})
    monkeypatch.setattr(m, "safe_file", lambda kind, name: None)
    async def call(name, arguments):
        calls.append((name, arguments))
        return {"status": "success", "audio_file": "/data/audio/brief.wav", "spoken_text": "Hay 3 vídeos.", "duration_seconds": 2}
    monkeypatch.setattr(m, "mcp_call", call)
    result = client.post("/api/voice/reply", json={"job_id": "abc", "language": "ca", "answer": "Invented answer"})
    assert result.status_code == 200
    assert calls == [("media_voice_reply", {"answer": "Hay 3 vídeos.", "language": "ca"})]
    assert result.json()["url"] == "/api/files/audio/brief.wav"
    monkeypatch.setattr(m, "get_job", lambda job: {"kind": "hermes_chat", "status": "running"})
    assert client.post("/api/voice/reply", json={"job_id": "abc"}).status_code == 409


def test_voice_failures_and_origin_are_explicit(client, monkeypatch):
    async def fail(name, arguments):
        return {"status": "error", "message": "Falta Whisper local"}
    monkeypatch.setattr(m, "mcp_call", fail)
    assert client.get("/api/voice/status").status_code == 503
    response = client.post("/api/voice/transcribe", files={"file": ("voice", b"audio")})
    assert response.status_code == 422 and "Falta Whisper local" in response.json()["detail"]
    assert client.post("/api/voice/transcribe", headers={"Origin": "https://untrusted.example"}, files={"file": ("voice", b"audio")}).status_code == 403


def test_greeting_and_conversational_reply_use_piper(client, monkeypatch):
    calls = []
    monkeypatch.setattr(m, "safe_file", lambda kind, name: None)
    monkeypatch.setattr(m, "get_job", lambda job: {"kind": "hermes_chat", "status": "completed",
        "answer": "Respuesta extensa con tablas.", "spoken_answer": "He preparado cinco clips. ¿Quieres revisar el primero?"})
    async def call(name, arguments):
        calls.append((name, arguments))
        return {"status": "success", "audio_file": "/data/voice.wav", "spoken_text": arguments.get("spoken_text", ""), "duration_seconds": 3}
    monkeypatch.setattr(m, "mcp_call", call)
    greeting = client.post("/api/voice/greeting", json={"language": "ca"})
    assert greeting.status_code == 200
    assert "Hola, soc MedIA" in greeting.json()["spoken_text"]
    reply = client.post("/api/voice/reply", json={"job_id": "abc"})
    assert reply.status_code == 200
    assert calls[-1][1]["spoken_text"] == "He preparado cinco clips. ¿Quieres revisar el primero?"


def test_separates_spoken_turn_from_written_response():
    written, spoken = m.split_voice_answer("## Resultado\nListado completo.\n<media_voice>He encontrado tres vídeos. ¿Cuál quieres consultar?</media_voice>")
    assert written == "## Resultado\nListado completo."
    assert spoken == "He encontrado tres vídeos. ¿Cuál quieres consultar?"
    assert m.split_voice_answer("Respuesta sin metadatos.") == ("Respuesta sin metadatos.", "")


def test_waiting_phrases_use_local_voice_and_validate_selection(client, monkeypatch):
    calls = []
    monkeypatch.setattr(m, "safe_file", lambda kind, name: None)
    async def call(name, arguments):
        calls.append((name, arguments))
        return {"status": "success", "audio_file": "/data/wait.wav", "spoken_text": arguments["spoken_text"]}
    monkeypatch.setattr(m, "mcp_call", call)
    first = client.post("/api/voice/filler", json={"language": "es", "index": 0})
    second = client.post("/api/voice/filler", json={"language": "es", "index": 1})
    assert first.status_code == second.status_code == 200
    assert first.json()["spoken_text"] == "Estoy en ello."
    assert first.json()["spoken_text"] != second.json()["spoken_text"]
    assert all(name == "media_voice_reply" for name, _ in calls)
    assert client.post("/api/voice/filler", json={"index": 4}).status_code == 422
    assert client.post("/api/voice/filler", json={"language": "xx"}).status_code == 422


def test_same_hermes_turn_produces_written_and_conversational_answers(monkeypatch):
    prompts = []
    monkeypatch.setattr(m, "chat_jobs", lambda conversation: [])
    monkeypatch.setattr(m, "save", lambda job: None)
    monkeypatch.setattr(m.bridge, "stored_session_id", lambda conversation: None)
    monkeypatch.setattr(m.bridge, "session_identity", lambda conversation: {})
    monkeypatch.setattr(m.bridge, "session_mode", lambda conversation: "local")
    def run(conversation, prompt, history, update):
        prompts.append(prompt)
        return "Respuesta completa para leer.\n<media_voice>Aquí tienes lo que buscabas. ¿Seguimos con otro vídeo?</media_voice>"
    monkeypatch.setattr(m.bridge, "run", run)
    job = {"id": "voice-test", "status": "queued", "conversation_id": "voice-test", "created_at": time.time(), "events": [], "artifacts": []}
    asyncio.run(m.execute_chat(job, m.ChatRequest(message="Hola", voice_language="ca")))
    assert job["status"] == "completed"
    assert job["answer"] == "Respuesta completa para leer."
    assert job["spoken_answer"].startswith("Aquí tienes lo que buscabas.")
    assert len(prompts) == 1 and "catalán" in prompts[0] and "<media_voice>" in prompts[0]
