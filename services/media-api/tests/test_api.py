import asyncio
import json
from pathlib import Path
import threading
import time
import pytest
from fastapi.testclient import TestClient
from app import main as m


@pytest.fixture
def env(tmp_path, monkeypatch):
    videos = tmp_path / 'data/test-vectors/videos'
    cache = tmp_path / 'data/cache/transcriptions'
    transcripts = tmp_path / 'shared/test-vectors/transcripts'
    visual_cache = tmp_path / 'data/cache/visual'
    ocr_cache = tmp_path / 'data/cache/ocr'
    keyframes = tmp_path / 'data/cache/keyframes'
    videos.mkdir(parents=True)
    cache.mkdir(parents=True)
    transcripts.mkdir(parents=True)
    visual_cache.mkdir(parents=True)
    ocr_cache.mkdir(parents=True)
    keyframes.mkdir(parents=True)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    monkeypatch.setattr(m, 'STATE', tmp_path / 'data/state')
    monkeypatch.setattr(m, 'TRANSCRIPTS', transcripts)
    monkeypatch.setattr(m, 'VIDEOS', videos)
    monkeypatch.setattr(m, 'CACHE', cache)
    monkeypatch.setattr(m, 'VISUAL_CACHE', visual_cache)
    monkeypatch.setattr(m, 'OCR_CACHE', ocr_cache)
    monkeypatch.setattr(m, 'KEYFRAMES', keyframes)
    monkeypatch.setattr(m, 'ROOTS', {'video': videos, 'audio': tmp_path / 'audio', 'clip': tmp_path / 'clips'})
    return videos, cache


def test_cache_requires_exact_source_identity(env):
    videos, cache = env
    p = videos / 'TV04.mp4'
    p.write_bytes(b'video')
    data = {'id': 'TV04', 'full_text': 'Synthetic test.', 'duration_seconds': 3}
    target = cache / 'TV04_whisperx.json'
    target.write_text(json.dumps(data))
    assert m.catalog()[0]['status'] == 'unverified'
    data['request_metadata'] = {'source': m.file_fingerprint(p)}
    target.write_text(json.dumps(data))
    assert m.catalog()[0]['status'] == 'ready'
    p.write_bytes(b'different video')
    assert m.catalog()[0]['status'] == 'unverified'


def test_cache_fingerprint_is_portable_between_paths(env, tmp_path):
    videos, cache = env
    original = tmp_path / 'windows' / 'TV04.mp4'
    current = videos / 'TV04.mp4'
    original.parent.mkdir()
    original.write_bytes(b'identical video')
    current.write_bytes(b'identical video')
    (cache / 'TV04_whisperx.json').write_text(json.dumps({
        'id': 'TV04',
        'full_text': 'Portable transcript.',
        'request_metadata': {'source': m.file_fingerprint(original)},
    }))

    assert m.catalog()[0]['status'] == 'ready'


def test_range_and_path_boundary(env):
    videos, _ = env
    (videos / 'test.mp4').write_bytes(b'0123456789')
    with TestClient(m.app) as client:
        res = client.get('/api/files/video/test.mp4', headers={'Range': 'bytes=2-5'})
        assert res.status_code == 206
        assert res.content == b'2345'
        assert res.headers['content-range'] == 'bytes 2-5/10'
        assert client.get('/api/files/audio/secret.env').status_code == 404
        assert client.post('/api/jobs', headers={'Origin': 'https://untrusted.example'}, json={}).status_code == 403
    for name in ('../secret.env', 'C:\\secret.wav', '..\\secret.wav'):
        with pytest.raises(m.HTTPException):
            m.safe_file('audio', name)


def test_restart_marks_active_jobs_failed(env):
    m.save({'id': 'old', 'status': 'running', 'created_at': 1, 'artifacts': []})
    with TestClient(m.app) as client:
        assert client.get('/api/jobs/old').json()['status'] == 'failed'


def test_health_reports_active_hermes_mode(env, monkeypatch):
    async def connected_mcp():
        return None

    monkeypatch.setattr(m, 'mcp_ping', connected_mcp)
    monkeypatch.setattr(m.bridge, 'status', lambda: {
        'configured_mode': 'auto',
        'active_mode': 'docker',
        'available': True,
        'running': True,
    })

    with TestClient(m.app) as client:
        health = client.get('/api/health').json()

    assert health == {
        'api': True,
        'media_core': True,
        'hermes_installed': True,
        'hermes_running': True,
        'hermes_configured_mode': 'auto',
        'hermes_mode': 'docker',
    }


def test_reasoning_endpoint_syncs_with_hermes_webui(env, monkeypatch):
    calls = []

    async def reasoning(effort=None):
        calls.append(effort)
        return {'reasoning_effort': effort or 'medium'}

    monkeypatch.setattr(m, 'webui_reasoning', reasoning)

    with TestClient(m.app) as client:
        assert client.get('/api/reasoning').json()['reasoning_effort'] == 'medium'
        changed = client.post('/api/reasoning', json={'effort': 'max'})
        invalid = client.post('/api/reasoning', json={'effort': 'ultra'})

    assert changed.status_code == 200
    assert changed.json()['reasoning_effort'] == 'max'
    assert invalid.status_code == 422
    assert calls == [None, 'max']


def test_visual_sweep_command_requires_video_and_query():
    assert m.parse_visual_sweep_command('/clips-todos TV05 personas mayores') == ('TV05', 'personas mayores')
    assert m.parse_visual_sweep_command('/clips-todos TV05') is None
    assert m.parse_visual_sweep_command('saca todos los clips de TV05') is None


def test_visual_sweep_chat_mirrors_remote_progress(monkeypatch):
    responses = iter([
        {'status': 'queued', 'job_id': 'remote-1'},
        {'status': 'running', 'events': [{'sequence': 1, 'stage': 'Analizando todo el vídeo', 'percent': 42}]},
        {'status': 'completed', 'events': [{'sequence': 2, 'stage': 'Análisis visual completado', 'percent': 100}],
         'result': {'status': 'success', 'occurrence_count': 0, 'occurrences': [], 'clips': [],
                    'coverage': {'sampled_frames': 120, 'sample_interval_seconds': 2.5}}},
    ])

    async def fake_mcp_call(name, arguments):
        return next(responses)

    async def no_wait(_):
        return None

    monkeypatch.setattr(m, 'mcp_call', fake_mcp_call)
    monkeypatch.setattr(m.asyncio, 'sleep', no_wait)
    monkeypatch.setattr(m, 'save', lambda job: None)
    hermes_prompts = []

    def fake_bridge(conversation_id, prompt, history, update):
        hermes_prompts.append(json.loads(prompt.split('\n\n', 1)[1]))
        return 'Hermes recibió el resultado final sin repetir herramientas.'

    monkeypatch.setattr(m.bridge, 'run', fake_bridge)
    job = {'id': 'local-1', 'conversation_id': 'conversation', 'message': '/clips-todos TV05 persona mayor',
           'media_core_owner_id': 'chat:local-1',
           'status': 'queued', 'stage': 'En cola', 'progress': 0, 'created_at': time.time(),
           'events': [], 'answer': '', 'artifacts': []}

    asyncio.run(m.execute_visual_sweep_chat(job, 'TV05', 'persona mayor'))

    assert job['status'] == 'completed'
    assert job['media_core_job_id'] == 'remote-1'
    assert job['progress'] == 100
    assert [event['progress'] for event in job['events']] == [42, 100]
    assert job['answer'] == 'Hermes recibió el resultado final sin repetir herramientas.'
    assert hermes_prompts == [{
        'video_id': 'TV05', 'query': 'persona mayor',
        'coverage': {'sampled_frames': 120, 'sample_interval_seconds': 2.5},
        'occurrence_count': 0, 'clips': [],
    }]


def test_remote_cancel_by_owner_closes_creation_race(monkeypatch):
    calls = []

    async def media_core(name, arguments):
        calls.append((name, arguments))
        return {'status': 'cancelled', 'owner_id': arguments['owner_id'], 'cancelled_job_ids': []}

    monkeypatch.setattr(m, 'mcp_call', media_core)
    job = {
        'id': 'local-race', 'conversation_id': 'conversation-race',
        'media_core_owner_id': 'chat:local-race', 'created_at': time.time(), 'events': [],
    }

    asyncio.run(m.cancel_media_core_job(job))

    assert calls == [('media_job_cancel', {'owner_id': 'chat:local-race'})]


def test_visual_sweep_owner_exists_before_background_create(env, monkeypatch):
    observed = []

    async def execute(job, video_id, query):
        observed.append((job['media_core_owner_id'], video_id, query))

    monkeypatch.setattr(m, 'execute_visual_sweep_chat', execute)

    with TestClient(m.app) as client:
        created = client.post('/api/chat/jobs', json={
            'message': '/clips-todos TV05 logo',
        }).json()
        for _ in range(50):
            if observed:
                break
            time.sleep(.01)

    assert created['media_core_owner_id'] == f"chat:{created['id']}"
    assert observed == [(created['media_core_owner_id'], 'TV05', 'logo')]


def test_visual_sweep_chat_exposes_provider_error(monkeypatch):
    responses = iter([
        {'status': 'queued', 'job_id': 'remote-error'},
        {'status': 'failed', 'events': [], 'result': {
            'status': 'error',
            'message': 'OpenRouter limitó temporalmente las peticiones (HTTP 429).',
        }},
    ])

    async def fake_mcp_call(name, arguments):
        return next(responses)

    async def no_wait(_):
        return None

    monkeypatch.setattr(m, 'mcp_call', fake_mcp_call)
    monkeypatch.setattr(m.asyncio, 'sleep', no_wait)
    monkeypatch.setattr(m, 'save', lambda job: None)
    job = {'id': 'local-error', 'conversation_id': 'conversation',
           'message': '/clips-todos TV05 persona mayor', 'status': 'queued',
           'stage': 'En cola', 'progress': 0, 'created_at': time.time(),
           'events': [], 'answer': '', 'artifacts': []}

    asyncio.run(m.execute_visual_sweep_chat(job, 'TV05', 'persona mayor'))

    assert job['status'] == 'failed'
    assert job['stage'] == 'Necesita atención'
    assert job['error'] == 'OpenRouter limitó temporalmente las peticiones (HTTP 429).'


def test_collect_initializes_missing_artifacts():
    job = {}
    m.collect({'status': 'success', 'clips': []}, job)
    assert job['artifacts'] == []


def test_costs_endpoint_never_exposes_provider_credentials(env, monkeypatch):
    async def costs(root, state):
        return {'currency': 'USD', 'period': '2026-09', 'actual_cost_usd': 1.25,
            'estimated_cost_usd': 0.1, 'total_cost_usd': 1.35, 'complete': True,
            'providers': [{'id': 'openrouter', 'name': 'OpenRouter', 'cost_usd': 1.25,
                'requests': None, 'cost_type': 'actual', 'configured': True, 'complete': True,
                'detail': 'Gasto mensual informado por OpenRouter.'}]}
    monkeypatch.setattr(m, 'provider_costs', costs)

    with TestClient(m.app) as client:
        result = client.get('/api/costs').json()

    assert result['total_cost_usd'] == 1.35
    assert result['providers'][0]['name'] == 'OpenRouter'
    assert 'key' not in json.dumps(result).lower()


def test_cost_report_downloads_pdf_without_credentials(env, monkeypatch):
    async def costs(root, state):
        return {
            'currency': 'USD', 'period': '2026-09', 'actual_cost_usd': 2.9,
            'estimated_cost_usd': 2.47, 'total_cost_usd': 5.37, 'complete': False,
            'refreshed_at': '2026-09-17T10:30:00+00:00',
            'providers': [
                {'id': 'openrouter', 'name': 'OpenRouter', 'cost_usd': 2.9,
                 'requests': None, 'cost_type': 'actual', 'configured': True,
                 'complete': True, 'detail': 'Gasto mensual informado por OpenRouter.'},
                {'id': 'replicate', 'name': 'Replicate', 'cost_usd': 2.47,
                 'requests': 465, 'cost_type': 'estimated', 'configured': True,
                 'complete': False, 'detail': 'Estimación parcial.'},
            ],
        }
    monkeypatch.setattr(m, 'provider_costs', costs)

    with TestClient(m.app) as client:
        response = client.get('/api/costs/report.pdf')

    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'attachment; filename="uso-apis-2026-09.pdf"'
    assert response.content.startswith(b'%PDF-1.4')
    assert b'Informe de uso de APIs' in response.content
    assert b'secret' not in response.content.lower()


def test_partial_summary_is_kept_when_tts_fails(env, monkeypatch):
    videos, _ = env
    (videos / 'TV04.mp4').write_bytes(b'video')
    monkeypatch.setattr(m, 'catalog', lambda: [{'filename': 'TV04.mp4', 'id': 'TV04', 'status': 'ready'}])
    calls = []
    async def call(name, args):
        calls.append((name, args))
        if name == 'media_summarize':
            return {'status': 'success', 'executive_summary': 'Synthetic summary.'}
        return {'status': 'error', 'message': 'Synthetic TTS failure'}
    monkeypatch.setattr(m, 'mcp_call', call)
    req = m.NewJob(video='TV04.mp4', action='audio', language='ca')
    job = {'id': 'test', 'created_at': 1, 'conversation_id': 'test', 'artifacts': [], 'events': []}
    asyncio.run(m.execute(job, req))
    assert job['status'] == 'failed'
    assert job['summary'] == 'Synthetic summary.'
    assert calls[1][1]['provider'] == 'piper'
    assert calls[1][1]['language'] == 'ca'
    assert calls[0][1]['video_id'] == 'TV04'


def test_artifacts_only_from_allowed_roots(env):
    outside = m.ROOT / 'secret.wav'
    outside.write_bytes(b'secret')
    assert m.artifact(str(outside)) is None
    audio = m.ROOTS['audio']
    audio.mkdir()
    (audio / 'sample.wav').write_bytes(b'audio')
    job = {'artifacts': []}
    m.collect({'result': {'audio_file': str(audio / 'sample.wav')}}, job)
    assert job['artifacts'][0]['url'] == '/api/files/audio/sample.wav'


def test_invalid_upload_rejected(env):
    with TestClient(m.app) as client:
        assert client.post('/api/videos', files={'file': ('bad.exe', b'data')}).status_code == 400
        assert client.post('/api/videos', files={'file': ('empty.mp4', b'')}).status_code == 400


def test_valid_upload_never_overwrites_source(env):
    import shutil
    import subprocess
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg is required for the real upload fixture')
    videos, _ = env
    source = m.ROOT / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=1:color=black', '-t', '1', '-c:v', 'libx264', str(source)], check=True, capture_output=True)
    original = videos / 'TV04.mp4'
    original.write_bytes(b'original')
    with TestClient(m.app) as client:
        response = client.post('/api/videos', files={'file': ('TV04.mp4', source.read_bytes(), 'video/mp4')}, headers={'Origin': 'http://127.0.0.1:3000'})
        assert response.status_code == 201
        assert response.headers['access-control-allow-origin'] == 'http://127.0.0.1:3000'
        name = response.json()['filename']
        assert name != original.name and name.startswith('TV04_')
        assert original.read_bytes() == b'original'
        uploaded = next(v for v in client.get('/api/videos').json() if v['filename'] == name)
        assert uploaded['status'] in {'new', 'indexing'}
        assert client.get(uploaded['url'], headers={'Range': 'bytes=0-15'}).status_code == 206


def test_delete_video_cleans_index_before_removing_file(env, monkeypatch):
    videos, _ = env
    source = videos / 'TV42.mp4'
    source.write_bytes(b'video')
    calls = []

    async def forget(name, arguments):
        calls.append((name, arguments, source.exists()))
        return {'status': 'success'}

    monkeypatch.setattr(m, 'mcp_call', forget)
    with TestClient(m.app) as client:
        response = client.delete('/api/videos/TV42.mp4')
        assert response.status_code == 200
        assert response.json()['deleted'] is True
        assert not source.exists()
        assert calls == [('media_forget_video', {'video_id': 'TV42', 'source_stem': 'TV42'}, True)]
        assert client.delete('/api/videos/TV42.mp4').status_code == 404


def test_delete_video_refuses_active_index_and_keeps_source_on_cleanup_failure(env, monkeypatch):
    videos, _ = env
    source = videos / 'TV43.mp4'
    source.write_bytes(b'video')
    m.indexing_jobs[source.name] = {'status': 'indexing'}
    try:
        with TestClient(m.app) as client:
            assert client.delete('/api/videos/TV43.mp4').status_code == 409
            assert source.exists()
    finally:
        m.indexing_jobs.pop(source.name, None)

    async def fail_forget(name, arguments):
        raise RuntimeError('media-core no responde')

    monkeypatch.setattr(m, 'mcp_call', fail_forget)
    with TestClient(m.app) as client:
        assert client.delete('/api/videos/TV43.mp4').status_code == 503
        assert source.exists()


def test_transcript_does_not_return_wrong_video_from_cache(env):
    videos, cache = env
    source = videos / 'TV04.mp4'
    source.write_bytes(b'video')
    st = source.stat()
    metadata = {'source': [str(source.resolve()), st.st_size, st.st_mtime_ns]}
    (cache / 'TV04_whisperx.json').write_text(json.dumps({'id': 'TV01', 'full_text': 'WRONG', 'request_metadata': metadata}))
    (m.TRANSCRIPTS / 'TV04.json').write_text(json.dumps({'id': 'TV04', 'full_text': 'CORRECT', 'request_metadata': metadata}))
    with TestClient(m.app) as client:
        assert client.get('/api/transcript/TV04.mp4').json()['text'] == 'CORRECT'

def test_clip_selection_evidence_is_kept_for_frontend(env, monkeypatch):
    videos, _ = env
    (videos / 'TV04.mp4').write_bytes(b'video')
    target = m.ROOTS['clip']
    target.mkdir()
    path = target / 'speaker.mp4'
    path.write_bytes(b'clip')
    monkeypatch.setattr(m, 'catalog', lambda: [{'filename': 'TV04.mp4', 'id': 'TV04', 'status': 'ready'}])
    async def call(name, args):
        assert name == 'media_clips'
        assert args['query'] == 'Donde habla Silvia Orriols'
        assert args['selection_mode'] == 'auto'
        return [{'title': args['query'], 'start_time': '00:33:59', 'end_time': '00:35:37',
                 'match_evidence': 'Tiene la palabra Silvia Oriol Serra.', 'selection_mode': 'speaker_turn',
                 'cut_video_file': str(path), 'cache_hit': False}]
    monkeypatch.setattr(m, 'mcp_call', call)
    job = {'id': 'clip-proof', 'created_at': 1, 'conversation_id': 'proof', 'artifacts': [], 'events': []}
    asyncio.run(m.execute(job, m.NewJob(video='TV04.mp4', action='clips', message='Donde habla Silvia Orriols')))
    assert job['status'] == 'completed'
    assert job['clips'][0]['start_time'] == '00:33:59'
    assert 'Silvia' in job['clips'][0]['match_evidence']
    assert job['artifacts'][0]['url'].endswith('speaker.mp4')


def test_global_chat_keeps_conversation_history(env, monkeypatch):
    calls = []

    def run(conversation, text, history, on_event):
        calls.append((conversation, text, history))
        on_event('message.complete', {'text': f'Respuesta a: {text}'})
        return f'Respuesta a: {text}'

    monkeypatch.setattr(m.bridge, 'run', run)

    first = {
        'id': 'chat-one', 'kind': 'hermes_chat', 'conversation_id': 'conversation-one',
        'message': 'Primera pregunta', 'status': 'queued', 'stage': 'En cola',
        'created_at': 1, 'events': [], 'answer': '',
    }
    asyncio.run(m.execute_chat(first, m.ChatRequest(
        message='Primera pregunta', conversation_id='conversation-one'
    )))
    second = {
        'id': 'chat-two', 'kind': 'hermes_chat', 'conversation_id': 'conversation-one',
        'message': 'Segunda pregunta', 'status': 'queued', 'stage': 'En cola',
        'created_at': 2, 'events': [], 'answer': '',
    }
    asyncio.run(m.execute_chat(second, m.ChatRequest(
        message='Segunda pregunta', conversation_id='conversation-one'
    )))

    assert second['status'] == 'completed'
    assert second['answer'] == 'Respuesta a: Segunda pregunta'
    assert calls[1][2] == [
        {'role': 'user', 'content': 'Primera pregunta'},
        {'role': 'assistant', 'content': 'Respuesta a: Primera pregunta'},
    ]


def test_global_chat_endpoints_are_separate_from_video_jobs(env, monkeypatch):
    monkeypatch.setattr(m.bridge, 'run', lambda conversation, text, history, on_event: 'Hecho')

    with TestClient(m.app) as client:
        response = client.post('/api/chat/jobs', json={'message': 'Revisa el proyecto'})
        assert response.status_code == 202
        created = response.json()
        assert created['kind'] == 'hermes_chat'
        assert created['conversation_id']

        for _ in range(50):
            current = client.get(f"/api/chat/jobs/{created['id']}").json()
            if current['status'] == 'completed':
                break
            time.sleep(0.01)

        assert current['answer'] == 'Hecho'
        history = client.get(f"/api/chat/{created['conversation_id']}").json()
        assert [item['id'] for item in history] == [created['id']]
        assert client.get('/api/jobs').json() == []


def test_delete_chat_removes_hermes_session_and_media_messages(env, monkeypatch):
    deleted_sessions = []
    session_deleted = threading.Event()
    monkeypatch.setattr(m.bridge, 'run', lambda conversation, text, history, on_event: 'Hecho')

    def delete_session(conversation, *args):
        deleted_sessions.append(conversation)
        session_deleted.set()
        return {'deleted': True, 'session_ids': [conversation], 'mode': 'docker'}

    monkeypatch.setattr(m.bridge, 'delete_session', delete_session)

    with TestClient(m.app) as client:
        created = client.post('/api/chat/jobs', json={'message': 'Revisa el proyecto'}).json()
        for _ in range(50):
            current = client.get(f"/api/chat/jobs/{created['id']}").json()
            if current['status'] == 'completed':
                break
            time.sleep(0.01)

        response = client.delete(f"/api/chat/{created['conversation_id']}")

        assert response.status_code == 200
        assert response.json()['deleted_jobs'] == 1
        assert response.json()['hermes']['queued'] is True
        assert session_deleted.wait(timeout=2)
        assert deleted_sessions == [created['conversation_id']]
        assert client.get(f"/api/chat/{created['conversation_id']}").json() == []
        assert client.get(f"/api/chat/jobs/{created['id']}").status_code == 404

def test_delete_chat_still_removes_media_messages_when_hermes_delete_fails(env, monkeypatch):
    attempted = threading.Event()
    m.save({
        'id': 'chat-failed-delete', 'kind': 'hermes_chat', 'conversation_id': 'conversation-one',
        'message': 'Hola', 'status': 'completed', 'stage': 'Completado',
        'created_at': 1, 'events': [], 'answer': 'Hola',
    })

    def fail_delete(*args):
        attempted.set()
        raise RuntimeError('Hermes no está disponible')

    monkeypatch.setattr(m.bridge, 'delete_session', fail_delete)
    with TestClient(m.app) as client:
        response = client.delete('/api/chat/conversation-one')

        assert response.status_code == 200
        assert response.json()['deleted_jobs'] == 1
        assert response.json()['hermes']['queued'] is True
        assert attempted.wait(timeout=2)
        assert client.get('/api/chat/conversation-one').json() == []


def test_delete_chat_passes_persisted_local_session_after_restart(env, monkeypatch):
    m.save({
        'id': 'chat-local', 'kind': 'hermes_chat', 'conversation_id': 'conversation-local',
        'hermes_session_id': 'stored-session', 'message': 'Hola', 'status': 'completed',
        'stage': 'Completado', 'created_at': 1, 'events': [], 'answer': 'Hola',
    })
    deleted = []
    session_deleted = threading.Event()

    def delete_session(conversation, persisted_session_id, mode=None, effective_session_id=None):
        deleted.append((conversation, persisted_session_id))
        session_deleted.set()
        return {'deleted': True, 'session_ids': [persisted_session_id], 'mode': 'local'}

    monkeypatch.setattr(m.bridge, 'delete_session', delete_session)
    with TestClient(m.app) as client:
        response = client.delete('/api/chat/conversation-local')
        assert session_deleted.wait(timeout=2)

    assert response.status_code == 200
    assert deleted == [('conversation-local', 'stored-session')]


def test_delete_chat_force_cancels_active_turn_and_prevents_late_resurrection(env, monkeypatch):
    started = threading.Event()
    released = threading.Event()
    cleaned = threading.Event()
    calls = []

    def run(conversation, text, history, on_event):
        started.set()
        released.wait(timeout=5)
        return 'Respuesta que llegó después del borrado'

    def interrupt(conversation):
        calls.append(('interrupt', conversation))
        released.set()
        return True

    def delete_session(conversation, *args):
        interrupt(conversation)
        calls.append(('delete', conversation))
        cleaned.set()
        return {'deleted': True, 'session_ids': [conversation], 'mode': 'docker'}

    monkeypatch.setattr(m.bridge, 'run', run)
    monkeypatch.setattr(m.bridge, 'interrupt', interrupt)
    monkeypatch.setattr(m.bridge, 'delete_session', delete_session)

    with TestClient(m.app) as client:
        created = client.post('/api/chat/jobs', json={'message': 'Trabajo largo'}).json()
        assert started.wait(timeout=2)

        deleted = client.delete(f"/api/chat/{created['conversation_id']}")

        assert deleted.status_code == 200
        assert deleted.json()['deleted_jobs'] == 1
        assert cleaned.wait(timeout=2)
        assert calls == [
            ('interrupt', created['conversation_id']),
            ('delete', created['conversation_id']),
        ]
        assert client.get(f"/api/chat/{created['conversation_id']}").json() == []
        assert client.get(f"/api/chat/jobs/{created['id']}").status_code == 404

        # The cancelled coroutine and its worker thread may finish after DELETE.
        # Neither completion nor a finally/save block may recreate the row.
        released.set()
        time.sleep(0.05)
        assert client.get(f"/api/chat/jobs/{created['id']}").status_code == 404


def test_delete_chat_cancels_only_its_remote_media_core_job(env, monkeypatch):
    cancelled = []

    async def media_core(name, arguments):
        assert name == 'media_job_cancel'
        cancelled.append(arguments['job_id'])
        return {'status': 'cancelling', 'job_id': arguments['job_id']}

    class ActiveTask:
        def __init__(self):
            self.cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    target = {
        'id': 'chat-target', 'kind': 'hermes_chat', 'conversation_id': 'conversation-target',
        'message': '/clips-todos TV05 logo', 'status': 'running', 'stage': 'Analizando',
        'created_at': time.time(), 'events': [], 'answer': '', 'artifacts': [],
        'media_core_job_id': 'remote-target',
    }
    unrelated = {
        **target, 'id': 'chat-other', 'conversation_id': 'conversation-other',
        'media_core_job_id': 'remote-other',
    }
    target_task = ActiveTask()
    other_task = ActiveTask()
    monkeypatch.setattr(m, 'chat_live_jobs', {target['id']: target, unrelated['id']: unrelated})
    monkeypatch.setattr(m, 'chat_tasks', {target['id']: target_task, unrelated['id']: other_task})
    monkeypatch.setattr(m, 'mcp_call', media_core)
    monkeypatch.setattr(m.bridge, 'interrupt', lambda conversation: True)
    monkeypatch.setattr(m.bridge, 'delete_session', lambda *args: {
        'deleted': True, 'session_ids': [args[0]], 'mode': 'docker',
    })
    m.save(target)
    m.save(unrelated)

    with TestClient(m.app) as client:
        response = client.delete('/api/chat/conversation-target')

    assert response.status_code == 200
    assert cancelled == ['remote-target']
    assert target_task.cancelled is True
    assert other_task.cancelled is False
    assert unrelated['status'] == 'running'


def test_cancel_chat_job_propagates_remote_media_core_cancel(env, monkeypatch):
    calls = []

    async def media_core(name, arguments):
        calls.append((name, arguments))
        return {'status': 'cancelling', 'job_id': arguments['job_id']}

    class ActiveTask:
        def done(self):
            return False

        def cancel(self):
            calls.append(('local_task_cancel', {}))

    job = {
        'id': 'chat-cancel', 'kind': 'hermes_chat', 'conversation_id': 'conversation-cancel',
        'message': '/clips-todos TV05 logo', 'status': 'running', 'stage': 'Analizando',
        'created_at': time.time(), 'events': [], 'answer': '', 'artifacts': [],
        'media_core_job_id': 'remote-cancel',
    }
    monkeypatch.setattr(m, 'chat_live_jobs', {job['id']: job})
    monkeypatch.setattr(m, 'chat_tasks', {job['id']: ActiveTask()})
    monkeypatch.setattr(m, 'mcp_call', media_core)
    monkeypatch.setattr(m.bridge, 'interrupt', lambda conversation: True)

    response = asyncio.run(m.cancel_chat_job(job['id']))

    assert response['status'] == 'cancelled'
    assert calls == [
        ('media_job_cancel', {'job_id': 'remote-cancel'}),
        ('local_task_cancel', {}),
    ]


def test_delete_active_local_chat_captures_stored_session_before_interrupt(env, monkeypatch):
    started = threading.Event()
    released = threading.Event()
    deleted = []
    session_deleted = threading.Event()

    def run(conversation, text, history, on_event):
        started.set()
        released.wait(timeout=5)
        return 'Respuesta tardía'

    def interrupt(conversation):
        # Local interruption terminates the gateway and clears its in-memory
        # session maps, exactly as HermesBridge._terminate_local does.
        m.bridge.stored_sessions.pop(conversation, None)
        released.set()
        return True

    def delete_session(conversation, persisted_session_id, mode=None, effective_session_id=None):
        deleted.append((conversation, persisted_session_id))
        session_deleted.set()
        return {'deleted': True, 'session_ids': [persisted_session_id], 'mode': 'local'}

    monkeypatch.setattr(m.bridge, 'run', run)
    monkeypatch.setattr(m.bridge, 'interrupt', interrupt)
    monkeypatch.setattr(m.bridge, 'delete_session', delete_session)

    with TestClient(m.app) as client:
        created = client.post('/api/chat/jobs', json={'message': 'Trabajo local largo'}).json()
        assert started.wait(timeout=2)
        monkeypatch.setitem(m.bridge.stored_sessions, created['conversation_id'], 'stored-active')

        response = client.delete(f"/api/chat/{created['conversation_id']}")
        assert session_deleted.wait(timeout=2)

    assert response.status_code == 200
    assert deleted == [(created['conversation_id'], 'stored-active')]


def test_delete_chat_is_idempotent_after_jobs_and_session_are_gone(env, monkeypatch):
    calls = []
    attempted = threading.Event()

    def delete_session(conversation, *args):
        calls.append(conversation)
        attempted.set()
        return {'deleted': False, 'session_ids': [], 'mode': 'docker'}

    monkeypatch.setattr(m.bridge, 'delete_session', delete_session)
    m.save({
        'id': 'chat-idempotent', 'kind': 'hermes_chat', 'conversation_id': 'conversation-idempotent',
        'message': 'Hola', 'status': 'completed', 'stage': 'Listo', 'created_at': 1,
        'events': [], 'answer': 'Hola',
    })

    with TestClient(m.app) as client:
        first = client.delete('/api/chat/conversation-idempotent')
        second = client.delete('/api/chat/conversation-idempotent')
        assert attempted.wait(timeout=2)

    assert first.status_code == 200
    assert first.json()['deleted_jobs'] == 1
    assert second.status_code == 200
    assert second.json()['deleted_jobs'] == 0
    assert set(calls) == {'conversation-idempotent'}


def test_delete_chat_does_not_wait_for_slow_hermes_cleanup(env, monkeypatch):
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    m.save({
        'id': 'chat-slow-cleanup', 'kind': 'hermes_chat', 'conversation_id': 'conversation-slow',
        'hermes_session_id': 'stored-slow', 'message': 'Hola', 'status': 'completed',
        'stage': 'Listo', 'created_at': 1, 'events': [], 'answer': 'Hola',
    })

    def slow_delete(*args):
        cleanup_started.set()
        release_cleanup.wait(timeout=5)
        return {'deleted': True, 'session_ids': ['stored-slow'], 'mode': 'local'}

    monkeypatch.setattr(m.bridge, 'delete_session', slow_delete)
    try:
        with TestClient(m.app) as client:
            started_at = time.monotonic()
            response = client.delete('/api/chat/conversation-slow')
            elapsed = time.monotonic() - started_at

            assert response.status_code == 200
            assert elapsed < 0.5
            assert response.json()['hermes']['queued'] is True
            assert cleanup_started.wait(timeout=2)
            assert client.get('/api/chat/conversation-slow').json() == []
            release_cleanup.set()
    finally:
        release_cleanup.set()


def test_hermes_cleanup_retries_durably_with_original_session_id(env, monkeypatch):
    first_attempt = threading.Event()
    cleaned = threading.Event()
    calls = []
    m.save({
        'id': 'chat-retry-cleanup', 'kind': 'hermes_chat', 'conversation_id': 'conversation-retry',
        'hermes_session_id': 'stored-retry', 'message': 'Hola', 'status': 'completed',
        'stage': 'Listo', 'created_at': 1, 'events': [], 'answer': 'Hola',
    })

    def flaky_delete(conversation, persisted_session_id, mode=None, effective_session_id=None):
        calls.append((conversation, persisted_session_id, mode))
        if len(calls) == 1:
            first_attempt.set()
            raise OSError('fallo transitorio')
        cleaned.set()
        return {'deleted': True, 'session_ids': [persisted_session_id], 'mode': mode or 'local'}

    monkeypatch.setattr(m.bridge, 'delete_session', flaky_delete)
    with TestClient(m.app) as client:
        response = client.delete('/api/chat/conversation-retry')
        assert response.status_code == 200
        assert first_attempt.wait(timeout=2)

    # The failed target survives API shutdown in SQLite, including the durable
    # local session id needed after the bridge has lost its in-memory maps.
    with m.db() as con:
        pending = con.execute(
            'SELECT session_id, attempts FROM hermes_cleanup WHERE conversation_id=?',
            ('conversation-retry',),
        ).fetchone()
    assert pending == ('stored-retry', 1)

    # A fresh lifespan resumes the pending cleanup automatically.
    with TestClient(m.app):
        assert cleaned.wait(timeout=3)

    assert len(calls) >= 2
    assert {call[1] for call in calls} == {'stored-retry'}


def test_docker_effective_session_is_persisted_in_job_and_cleanup_outbox(env, monkeypatch):
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    job = {
        'id': 'docker-effective', 'kind': 'hermes_chat', 'conversation_id': 'docker-conversation',
        'message': 'Hola', 'reasoning_effort': None, 'status': 'queued', 'stage': 'En cola',
        'created_at': time.time(), 'events': [], 'answer': '', 'artifacts': [],
    }

    def run(conversation, text, history, on_event):
        m.bridge.sessions[conversation] = 'docker-effective-session'
        m.bridge.session_modes[conversation] = 'docker'
        return 'Hecho'

    monkeypatch.setattr(m.bridge, 'run', run)
    asyncio.run(m.execute_chat(job, m.ChatRequest(
        message='Hola', conversation_id='docker-conversation',
    )))
    assert job['hermes_effective_session_id'] == 'docker-effective-session'

    # Simulate an API restart: only the persisted job can seed the outbox.
    m.bridge.sessions.clear()
    m.bridge.session_modes.clear()

    def slow_delete(*args):
        cleanup_started.set()
        release_cleanup.wait(timeout=5)
        return {'deleted': True, 'session_ids': ['docker-effective-session'], 'mode': 'docker'}

    monkeypatch.setattr(m.bridge, 'delete_session', slow_delete)
    try:
        with TestClient(m.app) as client:
            response = client.delete('/api/chat/docker-conversation')
            assert response.json()['hermes']['session_ids'] == ['docker-effective-session']
            assert cleanup_started.wait(timeout=2)
            with m.db() as con:
                row = con.execute('''
                    SELECT effective_session_id, mode FROM hermes_cleanup
                    WHERE conversation_id=?
                ''', ('docker-conversation',)).fetchone()
            assert row == ('docker-effective-session', 'docker')
            release_cleanup.set()
    finally:
        release_cleanup.set()


def test_deleted_conversation_is_tombstoned_before_a_late_post(env, monkeypatch):
    monkeypatch.setattr(m.bridge, 'delete_session', lambda *args: {
        'deleted': False, 'session_ids': [], 'mode': 'docker',
    })

    with TestClient(m.app) as client:
        deleted = client.delete('/api/chat/preassigned-conversation')
        late_post = client.post('/api/chat/jobs', json={
            'message': 'Este POST llegó tarde',
            'conversation_id': 'preassigned-conversation',
        })

        assert deleted.status_code == 200
        assert late_post.status_code == 410
        assert client.get('/api/chat/preassigned-conversation').json() == []


def test_delete_conversation_state_is_atomic_on_outbox_failure(env):
    m.save({
        'id': 'chat-atomic', 'kind': 'hermes_chat', 'conversation_id': 'conversation-atomic',
        'message': 'Hola', 'status': 'completed', 'stage': 'Listo',
        'created_at': 1, 'events': [], 'answer': 'Hola',
    })
    with m.db() as con:
        con.execute('''CREATE TRIGGER fail_cleanup_insert
            BEFORE INSERT ON hermes_cleanup
            BEGIN SELECT RAISE(ABORT, 'synthetic outbox failure'); END''')

    with pytest.raises(m.sqlite3.IntegrityError, match='synthetic outbox failure'):
        m.delete_conversation_state('conversation-atomic', None, None, 'local')

    with m.db() as con:
        assert con.execute(
            'SELECT 1 FROM deleted_conversations WHERE conversation_id=?',
            ('conversation-atomic',),
        ).fetchone() is None
    assert m.get_job('chat-atomic')['conversation_id'] == 'conversation-atomic'


def test_shutdown_waits_for_cleanup_thread_before_closing_bridge(env, monkeypatch):
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    cleanup_finished = threading.Event()
    closed = threading.Event()

    def slow_delete(*args):
        cleanup_started.set()
        release_cleanup.wait(timeout=5)
        cleanup_finished.set()
        return {'deleted': True, 'session_ids': [], 'mode': 'docker'}

    def close_bridge():
        assert cleanup_finished.is_set()
        closed.set()

    monkeypatch.setattr(m.bridge, 'delete_session', slow_delete)
    monkeypatch.setattr(m.bridge, 'close', close_bridge)
    client = TestClient(m.app)
    client.__enter__()
    client.delete('/api/chat/conversation-shutdown')
    assert cleanup_started.wait(timeout=2)

    shutdown = threading.Thread(target=lambda: client.__exit__(None, None, None))
    shutdown.start()
    time.sleep(0.05)
    assert not closed.is_set()
    release_cleanup.set()
    shutdown.join(timeout=2)

    assert not shutdown.is_alive()
    assert closed.is_set()


def test_global_chat_rejects_empty_message(env):
    with TestClient(m.app) as client:
        response = client.post('/api/chat/jobs', json={'message': '   '})

    assert response.status_code == 400
    assert response.json()['detail'] == 'Escribe un mensaje para Hermes.'


def test_global_chat_can_cancel_a_running_turn(env, monkeypatch):
    started = threading.Event()
    stopped = threading.Event()

    def run(conversation, text, history, on_event):
        started.set()
        stopped.wait(timeout=5)
        return 'Respuesta tardía'

    monkeypatch.setattr(m.bridge, 'run', run)
    monkeypatch.setattr(m.bridge, 'interrupt', lambda conversation: stopped.set())

    with TestClient(m.app) as client:
        created = client.post('/api/chat/jobs', json={'message': 'Busca una imagen'}).json()
        assert started.wait(timeout=2)
        cancelled = client.post(f"/api/chat/jobs/{created['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()['status'] == 'cancelled'
        assert stopped.is_set()
        assert client.get(f"/api/chat/jobs/{created['id']}").json()['status'] == 'cancelled'
        retry = client.post('/api/chat/jobs', json={
            'message': 'Otra pregunta', 'conversation_id': created['conversation_id'],
        })
        assert retry.status_code == 202


def test_global_chat_publishes_keyframe_from_visual_tool(env, monkeypatch):
    keyframes = m.KEYFRAMES / 'TV04'
    keyframes.mkdir()
    (keyframes / 'frame_0001.jpg').write_bytes(b'frame')

    def run(conversation, text, history, on_event):
        on_event('tool.complete', {'name': 'media_describe_scene', 'result': {
            'status': 'success', 'keyframe_path': 'keyframes/TV04/frame_0001.jpg',
        }})
        return 'Este fotograma muestra el hemiciclo.'

    monkeypatch.setattr(m.bridge, 'run', run)
    job = {
        'id': 'chat-image', 'kind': 'hermes_chat', 'conversation_id': 'conversation-image',
        'message': 'Describe TV04', 'status': 'queued', 'stage': 'En cola',
        'created_at': time.time(), 'events': [], 'answer': '',
    }
    asyncio.run(m.execute_chat(job, m.ChatRequest(
        message='Describe TV04', conversation_id='conversation-image',
    )))

    assert job['status'] == 'completed'
    assert job['artifacts'] == [{
        'kind': 'image', 'name': 'frame_0001.jpg',
        'url': '/api/keyframes/TV04/frame_0001.jpg',
    }]


def test_global_chat_publishes_sanitized_live_activity(env, monkeypatch):
    def run(conversation, text, history, on_event):
        on_event('tool.start', {
            'name': 'skill_view', 'label': 'media-video-archive',
            'arguments': {'name': 'media-video-archive', 'secret': 'hidden'},
        })
        on_event('tool.complete', {'name': 'skill_view', 'result': {}})
        return 'Hecho.'

    monkeypatch.setattr(m.bridge, 'run', run)
    job = {
        'id': 'chat-activity', 'kind': 'hermes_chat', 'conversation_id': 'conversation-activity',
        'message': 'Resume el vídeo', 'status': 'queued', 'stage': 'En cola',
        'created_at': time.time(), 'events': [], 'answer': '',
    }
    asyncio.run(m.execute_chat(job, m.ChatRequest(
        message='Resume el vídeo', conversation_id='conversation-activity',
    )))

    assert job['events'][0]['kind'] == 'skill'
    assert job['events'][0]['label'] == 'media-video-archive'
    assert job['events'][0]['status'] == 'completed'
    assert 'secret' not in json.dumps(job['events'])


def test_image_follow_up_directs_hermes_to_video_tool(env, monkeypatch):
    previous = {
        'id': 'previous-image', 'kind': 'hermes_chat', 'conversation_id': 'conversation-image',
        'message': 'Describe la imagen del vídeo TV04', 'status': 'completed', 'stage': 'Listo',
        'created_at': 1, 'events': [], 'answer': 'Se ve el hemiciclo.',
    }
    m.save(previous)
    sent = []
    monkeypatch.setattr(m.bridge, 'run', lambda conversation, text, history, on_event: sent.append(text) or 'Aquí está.')
    job = {
        'id': 'follow-up-image', 'kind': 'hermes_chat', 'conversation_id': 'conversation-image',
        'message': 'dame la imagen', 'status': 'queued', 'stage': 'En cola',
        'created_at': 2, 'events': [], 'answer': '',
    }
    asyncio.run(m.execute_chat(job, m.ChatRequest(
        message='dame la imagen', conversation_id='conversation-image',
    )))

    assert job['status'] == 'completed'
    assert 'TV04' in sent[0]
    assert 'media_describe_scene' in sent[0]
    assert 'no uses search_files' in sent[0].lower()


def test_visual_manifest_and_keyframes_endpoints(env, tmp_path):
    visual_cache = tmp_path / 'data/cache/visual'
    keyframes = tmp_path / 'data/cache/keyframes'
    (visual_cache / 'TV04_visual.json').write_text(json.dumps({
        'video_id': 'TV04',
        'scenes_count': 1,
        'keyframes_count': 1,
    }))
    tv04_dir = keyframes / 'TV04'
    tv04_dir.mkdir(parents=True)
    (tv04_dir / 'frame_0001.jpg').write_bytes(b'dummy jpeg data')

    with TestClient(m.app) as client:
        res = client.get('/api/visual/TV04')
        assert res.status_code == 200
        assert res.json()['video_id'] == 'TV04'

        missing = client.get('/api/visual/MISSING')
        assert missing.status_code == 404

        frame_res = client.get('/api/keyframes/TV04/frame_0001.jpg')
        assert frame_res.status_code == 200
        assert frame_res.content == b'dummy jpeg data'
        assert frame_res.headers['content-type'] == 'image/jpeg'

        bad_frame = client.get('/api/keyframes/TV04/frame_9999.jpg')
        assert bad_frame.status_code == 404


def test_ocr_manifest_endpoint(env, tmp_path):
    ocr_cache = tmp_path / 'data/cache/ocr'
    (ocr_cache / 'TV05_ocr.json').write_text(json.dumps({
        'video_id': 'TV05',
        'unique_texts_count': 5,
        'extracted_texts': [{'text': 'Laura Barrero', 'region': 'lower_third'}],
    }))

    with TestClient(m.app) as client:
        res = client.get('/api/ocr/TV05')
        assert res.status_code == 200
        assert res.json()['video_id'] == 'TV05'
        assert res.json()['unique_texts_count'] == 5

        missing = client.get('/api/ocr/MISSING')
        assert missing.status_code == 404


def test_visual_search_and_describe_endpoints(env, monkeypatch):
    async def mock_mcp(name, arguments):
        if name == 'media_search_visual':
            return [{'video_id': 'TV05', 'matched_terms': [arguments['query']]}]
        if name == 'media_describe_scene':
            return {'status': 'success', 'description': 'Plano medio de presentadora'}
        return {}

    monkeypatch.setattr(m, 'mcp_call', mock_mcp)

    with TestClient(m.app) as client:
        res = client.get('/api/visual/search?q=gasolinera')
        assert res.status_code == 200
        assert res.json()[0]['video_id'] == 'TV05'

        res_desc = client.post('/api/visual/describe', json={'video_id': 'TV05', 'timestamp': 10.0})
        assert res_desc.status_code == 200
        assert res_desc.json()['description'] == 'Plano medio de presentadora'


def test_video_index_and_status_endpoints(env, monkeypatch):
    videos, _ = env
    p = videos / 'TV04.mp4'
    p.write_bytes(b'video content')

    calls = []
    async def mock_mcp(name, arguments):
        calls.append((name, arguments))
        return {'status': 'success'}

    monkeypatch.setattr(m, 'mcp_call', mock_mcp)

    with TestClient(m.app) as client:
        # Check initial status
        st = client.get('/api/videos/TV04.mp4/status')
        assert st.status_code == 200
        assert 'status' in st.json()

        # Trigger indexing
        res = client.post('/api/videos/TV04.mp4/index')
        assert res.status_code == 202
        assert res.json()['status'] == 'indexing'
