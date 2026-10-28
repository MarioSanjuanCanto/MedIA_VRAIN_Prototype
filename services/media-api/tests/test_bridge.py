import os
import queue
from pathlib import Path
import threading

import httpx
import pytest

from app.bridge import HermesBridge


def make_bridge(tmp_path, monkeypatch, mode='auto', key='secret'):
    monkeypatch.setenv('HERMES_MODE', mode)
    monkeypatch.setenv('HERMES_API_URL', 'http://127.0.0.1:8642')
    monkeypatch.setenv('HERMES_API_KEY', key)
    monkeypatch.setenv('HERMES_DOCKER_WORKDIR', '/workspace')
    return HermesBridge(tmp_path)


def test_auto_prefers_docker_when_gateway_is_available(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(bridge, '_docker_available', lambda: True)
    monkeypatch.setattr(bridge, '_run_docker', lambda *args: 'Docker')
    monkeypatch.setattr(bridge, '_run_local', lambda *args: pytest.fail('Local must not run'))

    assert bridge.run('conversation', 'synthetic', [], lambda *_: None) == 'Docker'
    assert bridge.active_mode == 'docker'


def test_auto_falls_back_to_local_when_gateway_is_unavailable(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(bridge, '_docker_available', lambda: False)
    monkeypatch.setattr(bridge, '_run_local', lambda *args: 'Local')
    monkeypatch.setattr(bridge, '_run_docker', lambda *args: pytest.fail('Docker must not run'))

    assert bridge.run('conversation', 'synthetic', [], lambda *_: None) == 'Local'
    assert bridge.active_mode == 'local'


def test_docker_requires_api_key(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker', key='')

    assert bridge.available() is False
    with pytest.raises(RuntimeError, match='HERMES_API_KEY'):
        bridge.run('conversation', 'synthetic', [], lambda *_: None)


def test_blank_local_paths_keep_platform_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_AGENT_DIR', '')
    monkeypatch.setenv('HERMES_PYTHON', '')
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')

    assert bridge.home != Path('.')
    expected = 'venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python'
    assert bridge.python == bridge.home / expected


def test_docker_rejects_invalid_api_key(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker', key='wrong')

    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'status': 'ok'})
        return httpx.Response(401, json={'error': {'message': 'Unauthorized'}})

    bridge._transport = httpx.MockTransport(handler)
    assert bridge.available() is False
    with pytest.raises(RuntimeError, match='clave'):
        bridge.run('conversation', 'synthetic', [], lambda *_: None)


def test_docker_sends_bearer_key_and_translates_paths(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker')
    video = tmp_path / 'data/test-vectors/videos/TV04.mp4'

    def handler(request):
        assert request.headers['Authorization'] == 'Bearer secret'
        body = __import__('json').loads(request.content)
        assert '/workspace/data/test-vectors/videos/TV04.mp4' in body['messages'][-1]['content']
        assert body['model_options'] == {'reasoning_effort': 'high'}
        return httpx.Response(200, json={
            'choices': [{'message': {'content': 'MEDIA:/workspace/data/outputs/audio/result.wav'}}]
        })

    bridge._transport = httpx.MockTransport(handler)
    result = bridge.run('conversation', f'Archivo: {video}', [], lambda *_: None, 'high')

    assert str(tmp_path / 'data/outputs/audio/result.wav') in result


def test_docker_streams_tool_activity(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker')
    events = []
    stream = '\n\n'.join([
        'event: hermes.tool.progress\ndata: {"tool":"skill_view","label":"media-video-archive","toolCallId":"call-1","status":"running"}',
        'event: hermes.tool.progress\ndata: {"tool":"skill_view","toolCallId":"call-1","status":"completed"}',
        'data: {"choices":[{"delta":{"content":"Hecho"},"finish_reason":null}]}',
        'data: [DONE]',
    ]) + '\n\n'

    def handler(request):
        body = __import__('json').loads(request.content)
        assert body['stream'] is True
        return httpx.Response(200, headers={
            'content-type': 'text/event-stream',
            'X-Hermes-Session-Id': 'stream-session',
        }, text=stream)

    bridge._transport = httpx.MockTransport(handler)
    result = bridge.run('conversation', 'synthetic', [], lambda event, payload: events.append((event, payload)))

    assert result == 'Hecho'
    assert bridge.sessions['conversation'] == 'stream-session'
    assert [(event, payload['name']) for event, payload in events[:-1]] == [
        ('tool.start', 'skill_view'), ('tool.complete', 'skill_view'),
    ]
    assert events[0][1]['label'] == 'media-video-archive'
    assert events[-1] == ('message.complete', {'text': 'Hecho'})


def test_docker_deletes_effective_hermes_session(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker')
    deleted = []

    def handler(request):
        if request.method == 'POST':
            return httpx.Response(
                200,
                headers={'X-Hermes-Session-Id': 'effective-session'},
                json={'choices': [{'message': {'content': 'Hecho'}}]},
            )
        deleted.append(request.url.path)
        return httpx.Response(200, json={'deleted': True})

    bridge._transport = httpx.MockTransport(handler)
    bridge.run('conversation', 'synthetic', [], lambda *_: None)
    result = bridge.delete_session('conversation')

    assert result['deleted'] is True
    assert deleted == ['/api/sessions/effective-session', '/api/sessions/conversation']
    assert 'conversation' not in bridge.sessions


def test_docker_delete_is_idempotent_when_session_is_missing(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker')
    bridge._transport = httpx.MockTransport(lambda request: httpx.Response(404))

    result = bridge.delete_session('missing')

    assert result == {'deleted': False, 'session_ids': [], 'mode': 'docker'}


def test_local_delete_closes_live_session_and_deletes_persisted_chat(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    bridge.sessions['conversation'] = 'live-session'
    bridge.stored_sessions['conversation'] = 'stored-session'
    bridge.session_reasoning['conversation'] = 'high'
    sent = []

    def send(method, params):
        sent.append((method, params))
        return method

    monkeypatch.setattr(bridge, '_send', send)
    bridge.frames = queue.Queue()
    bridge.frames.put({'id': 'session.close', 'result': {'closed': True}})
    bridge.frames.put({'id': 'session.delete', 'result': {'deleted': 'stored-session'}})

    result = bridge.delete_session('conversation')

    assert result == {
        'deleted': True, 'session_ids': ['stored-session'], 'mode': 'local',
    }
    assert sent == [
        ('session.close', {'session_id': 'live-session'}),
        ('session.delete', {'session_id': 'stored-session'}),
    ]
    assert 'conversation' not in bridge.sessions
    assert 'conversation' not in bridge.stored_sessions
    assert 'conversation' not in bridge.session_reasoning


def test_local_delete_treats_missing_persisted_session_as_idempotent(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    bridge.stored_sessions['conversation'] = 'stored-session'
    monkeypatch.setattr(bridge, '_send', lambda method, params: method)
    bridge.frames = queue.Queue()
    bridge.frames.put({
        'id': 'session.delete',
        'error': {'code': 4007, 'message': 'session not found'},
    })

    result = bridge.delete_session('conversation')

    assert result == {'deleted': False, 'session_ids': [], 'mode': 'local'}
    assert 'conversation' not in bridge.stored_sessions


def test_local_delete_continues_when_old_live_session_is_missing(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    bridge.orphan_sessions['conversation'] = {
        'session_id': 'live-from-terminated-gateway',
        'stored_session_id': 'stored-session',
        'mode': 'local',
    }
    sent = []

    def send(method, params):
        sent.append((method, params))
        return method

    monkeypatch.setattr(bridge, '_send', send)
    bridge.frames = queue.Queue()
    bridge.frames.put({
        'id': 'session.close',
        'error': {'code': 4007, 'message': 'session not found'},
    })
    bridge.frames.put({'id': 'session.delete', 'result': {'deleted': 'stored-session'}})

    result = bridge.delete_session('conversation', mode='local')

    assert result == {
        'deleted': True, 'session_ids': ['stored-session'], 'mode': 'local',
    }
    assert sent == [
        ('session.close', {'session_id': 'live-from-terminated-gateway'}),
        ('session.delete', {'session_id': 'stored-session'}),
    ]
    assert 'conversation' not in bridge.orphan_sessions


def test_local_delete_uses_persisted_id_after_media_api_restart(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    monkeypatch.setattr(bridge, '_send', lambda method, params: method)
    bridge.frames = queue.Queue()
    bridge.frames.put({'id': 'session.delete', 'result': {'deleted': 'stored-session'}})

    result = bridge.delete_session('conversation', 'stored-session')

    assert result == {
        'deleted': True, 'session_ids': ['stored-session'], 'mode': 'local',
    }


def test_local_hermes_event_contract(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    bridge.sessions['conversation'] = 's1'
    monkeypatch.setattr(bridge, '_send', lambda *args: 'request')
    bridge.frames = queue.Queue()
    bridge.frames.put({'method': 'event', 'params': {'type': 'tool.complete', 'session_id': 's1', 'payload': {'name': 'media_tts', 'result': {}}}})
    bridge.frames.put({'method': 'event', 'params': {'type': 'message.complete', 'session_id': 's1', 'payload': {'text': 'Done'}}})
    events = []

    result = bridge.run('conversation', 'synthetic', [], lambda event, payload: events.append(event))

    assert result == 'Done'
    assert events == ['tool.complete', 'message.complete']


def test_local_session_receives_and_updates_reasoning_effort(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    sent = []

    def send(method, params):
        sent.append((method, params))
        return method

    monkeypatch.setattr(bridge, '_send', send)
    bridge.frames = queue.Queue()
    bridge.frames.put({'id': 'session.create', 'result': {'session_id': 's1'}})
    bridge.frames.put({'method': 'event', 'params': {
        'type': 'message.complete', 'session_id': 's1', 'payload': {'text': 'Primera'},
    }})
    bridge.frames.put({'id': 'session.create', 'result': {'session_id': 's2'}})
    bridge.frames.put({'method': 'event', 'params': {
        'type': 'message.complete', 'session_id': 's2', 'payload': {'text': 'Segunda'},
    }})

    assert bridge.run('conversation', 'uno', [], lambda *_: None, 'low') == 'Primera'
    assert bridge.run('conversation', 'dos', [{'role': 'user', 'content': 'uno'}], lambda *_: None, 'high') == 'Segunda'

    creates = [params for method, params in sent if method == 'session.create']
    assert [params['reasoning_effort'] for params in creates] == ['low', 'high']
    assert creates[1]['messages'] == [{'role': 'user', 'content': 'uno'}]


def test_interrupt_stops_the_active_local_gateway(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')

    class Process:
        terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    process = Process()
    stopped = threading.Event()
    bridge.process = process
    bridge.sessions['conversation'] = 'session-one'
    bridge.active_runs['conversation'] = {'stop': stopped, 'mode': 'local', 'client': None}

    assert bridge.interrupt('conversation') is True
    assert stopped.is_set()
    assert process.terminated
    assert bridge.sessions == {}
    assert bridge.frames.get_nowait() == {'bridge_closed': True}
    assert bridge.interrupt('other-conversation') is False


def test_interrupt_does_not_kill_local_gateway_for_turn_waiting_on_lock(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')

    class Process:
        terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    process = Process()
    bridge.process = process
    waiting_stop = threading.Event()
    owner_stop = threading.Event()
    bridge.active_runs['waiting'] = {
        'stop': waiting_stop, 'mode': 'local', 'client': None, 'owns_local': False,
    }
    bridge.active_runs['owner'] = {
        'stop': owner_stop, 'mode': 'local', 'client': None, 'owns_local': True,
    }

    assert bridge.interrupt('waiting') is True
    assert waiting_stop.is_set()
    assert not process.terminated

    assert bridge.interrupt('owner') is True
    assert owner_stop.is_set()
    assert process.terminated


def test_delete_session_uses_recorded_mode_when_auto_availability_changes(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='auto')
    bridge.api_key = 'secret'
    bridge.session_modes['conversation'] = 'docker'
    deleted = []

    def handler(request):
        deleted.append(request.url.path)
        return httpx.Response(200, json={'deleted': True})

    bridge._transport = httpx.MockTransport(handler)
    monkeypatch.setattr(bridge, '_select_mode', lambda: (_ for _ in ()).throw(
        AssertionError('No debe volver a seleccionar backend')
    ))

    result = bridge.delete_session('conversation', 'stored-session')

    assert result['mode'] == 'docker'
    assert deleted == ['/api/sessions/conversation']


def test_docker_delete_uses_persisted_effective_session_after_restart(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='docker')
    deleted = []

    def handler(request):
        deleted.append(request.url.path)
        return httpx.Response(200, json={'deleted': True})

    bridge._transport = httpx.MockTransport(handler)
    result = bridge.delete_session(
        'conversation', mode='docker', effective_session_id='effective-session'
    )

    assert result['deleted'] is True
    assert deleted == ['/api/sessions/effective-session', '/api/sessions/conversation']


def test_local_session_created_during_interrupt_is_preserved_for_cleanup(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')
    monkeypatch.setattr(bridge, '_start', lambda: None)
    active = {
        'stop': threading.Event(), 'mode': 'local', 'client': None, 'owns_local': False,
    }
    monkeypatch.setattr(bridge, '_send', lambda method, params: method)

    def next_frame(deadline):
        active['stop'].set()
        return {
            'id': 'session.create',
            'result': {'session_id': 'live-new', 'stored_session_id': 'stored-new'},
        }

    monkeypatch.setattr(bridge, '_next', next_frame)

    with pytest.raises(RuntimeError, match='detenido'):
        bridge._run_local('conversation', 'hola', [], lambda *_: None, active)

    identity = bridge.session_identity('conversation')
    assert identity == {
        'session_id': 'live-new', 'stored_session_id': 'stored-new', 'mode': 'local',
    }


def test_interrupt_releases_a_local_turn_waiting_for_tool_output(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, mode='local')

    class Process:
        terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    bridge.process = Process()
    bridge.sessions['conversation'] = 's1'
    monkeypatch.setattr(bridge, '_start', lambda: None)
    monkeypatch.setattr(bridge, '_send', lambda *args: 'request')
    tool_started = threading.Event()
    errors = []

    def run():
        try:
            bridge.run('conversation', 'dame la imagen', [],
                       lambda event, payload: tool_started.set() if event == 'tool.start' else None)
        except RuntimeError as exc:
            errors.append(str(exc))

    turn = threading.Thread(target=run)
    turn.start()
    bridge.frames.put({'method': 'event', 'params': {
        'type': 'tool.start', 'session_id': 's1', 'payload': {'name': 'search_files'},
    }})
    assert tool_started.wait(timeout=2)
    assert bridge.interrupt('conversation') is True
    turn.join(timeout=2)

    assert not turn.is_alive()
    assert errors
    assert bridge.process.terminated
