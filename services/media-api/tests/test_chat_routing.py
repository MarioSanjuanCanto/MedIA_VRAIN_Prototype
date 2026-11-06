import asyncio
import time
import pytest
from app import main as m
from app.turn_clips import parse_turn_clip_request


CLIP_PROMPT = ('Extrae un clip con Media Core. Para tiempos conocidos usa media_clips con ese intervalo; '
               'para turnos de una persona consulta media_speakers y conserva sus índices. '
               'Petición del usuario: TV05 Mieres')


def job(tmp_path, monkeypatch, message):
    monkeypatch.setattr(m, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(m, 'catalog', lambda: [{'id': 'TV05', 'url': '/api/files/video/TV05.mp4'}])
    return {'id': 'routing', 'kind': 'hermes_chat', 'conversation_id': 'routing',
            'message': message, 'status': 'queued', 'created_at': time.time(),
            'stage': 'En cola', 'events': [], 'artifacts': []}


def test_legacy_instructions_do_not_become_a_speaker_name():
    assert parse_turn_clip_request(CLIP_PROMPT, []) is None
    assert parse_turn_clip_request('Corta el clip número 6 de TV05', [
        {'role': 'user', 'content': CLIP_PROMPT},
    ]) is None


def test_legacy_client_is_accepted_without_routing_on_its_instructions():
    request = m.ChatRequest(message=CLIP_PROMPT)
    assert request.message == '/clip TV05 Mieres'
    assert request.command_prompt == CLIP_PROMPT


def test_summary_never_prefetches_multimodal_index(tmp_path, monkeypatch):
    request = m.ChatRequest(message='/resumir TV05', command_prompt='Resume el vídeo. Petición del usuario: TV05')
    state = job(tmp_path, monkeypatch, request.message)
    async def no_media_calls(*args): pytest.fail('Un resumen no debe buscar en índices multimodales')
    monkeypatch.setattr(m, 'mcp_call', no_media_calls)
    monkeypatch.setattr(m.bridge, 'run', lambda *a, **kw: 'Resumen correcto')
    asyncio.run(m.execute_chat(state, request))
    assert state['status'] == 'completed'
    assert state['answer'] == 'Resumen correcto'


@pytest.mark.parametrize('index_state', [
    {'status': 'not_started'}, {'status': 'stale', 'completed_items': 9},
    {'status': 'processing', 'completed_items': 0}, {'status': 'completed', 'completed_items': 0},
    {'status': 'processing', 'completed_items': 3}, {'status': 'partial', 'completed_items': 3},
])
def test_unindexed_video_skips_multimodal_search(tmp_path, monkeypatch, index_state):
    state = job(tmp_path, monkeypatch, '/clip TV05 Mieres')
    calls, prompts = [], []
    async def call(name, args):
        calls.append(name)
        assert name == 'media_multimodal_index_status'
        return index_state
    monkeypatch.setattr(m, 'mcp_call', call)
    monkeypatch.setattr(m.bridge, 'run', lambda conversation, prompt, *a, **kw: prompts.append(prompt) or 'Clip encontrado')
    asyncio.run(m.execute_chat(state, m.ChatRequest(
        message=state['message'], command_prompt=CLIP_PROMPT)))
    assert state['status'] == 'completed'
    assert calls == ['media_multimodal_index_status']
    assert 'media_search en la transcripción' in prompts[0]
    assert not any('Buscando en el índice multimodal' in e.get('stage', '') for e in state['events'])


def test_indexed_video_can_still_use_multimodal_search(tmp_path, monkeypatch):
    state = job(tmp_path, monkeypatch, 'Busca Mieres en TV05')
    calls = []
    async def call(name, args):
        calls.append(name)
        if name == 'media_multimodal_index_status': return {'status': 'completed', 'completed_items': 2}
        return {'results': [], 'coverage_complete': False}
    monkeypatch.setattr(m, 'mcp_call', call)
    result = asyncio.run(m.chat_candidates(state, 'TV05', 'Mieres'))
    assert result['index_available'] is True
    assert calls == ['media_multimodal_index_status', 'media_search_multimodal']


def test_named_video_supported_by_exhaustive_command():
    assert m.parse_visual_sweep_command('/clips-todos APUNT cartel') == ('APUNT', 'cartel')


def test_agent_policy_requires_clip_then_dubbed_video():
    assert 'media_dub_clip' in m.AGENT_MEDIA_POLICY
    assert 'source=cut_video_file' in m.AGENT_MEDIA_POLICY
    assert 'no el clip intermedio ni un audio suelto' in m.AGENT_MEDIA_POLICY


def test_agent_policy_defaults_subtitles_to_original_language():
    assert 'media_subtitle_video' in m.AGENT_MEDIA_POLICY
    assert 'usa original' in m.AGENT_MEDIA_POLICY
    assert 'subtitled_video_file' in m.AGENT_MEDIA_POLICY


@pytest.mark.parametrize('block_status', [True, False])
def test_slow_multimodal_service_does_not_block_the_chat(tmp_path, monkeypatch, block_status):
    state = job(tmp_path, monkeypatch, 'Busca Mieres en TV05')
    original_wait = asyncio.wait_for
    async def short_deadline(awaitable, timeout):
        return await original_wait(awaitable, min(timeout, .01))
    async def call(name, args):
        if name == 'media_multimodal_index_status' and not block_status:
            return {'status': 'completed', 'completed_items': 2}
        await asyncio.Event().wait()
    monkeypatch.setattr(m.asyncio, 'wait_for', short_deadline)
    monkeypatch.setattr(m, 'mcp_call', call)
    result = asyncio.run(m.chat_candidates(state, 'TV05', 'Mieres'))
    assert result['retrieval_strategy'] == 'unavailable'
    assert result['index_available'] is False
