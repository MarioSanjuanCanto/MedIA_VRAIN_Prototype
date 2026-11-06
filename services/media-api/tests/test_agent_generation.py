import asyncio
import json
import time
import pytest
from app import agent_generation as ag
from app import main as m


def page(text, offset=None, revision='stable'):
    return {'status': 'success', 'text': text, 'next_offset': offset, 'source_revision': revision,
            'total_chars': len(text), 'has_timestamps': True}


def test_summary_uses_all_pages_and_synthesizes():
    seen = []
    async def read(name, args):
        assert name == 'media_read_transcript'
        return page('primera fuente', 14) if args['offset'] == 0 else page('segunda fuente')
    async def generate(prompt):
        seen.append(prompt)
        return ['notas una', 'notas dos', 'resumen completo'][len(seen) - 1]
    result = asyncio.run(ag.summarize(read, generate, 'TV99', language='ca'))
    assert result['executive_summary'] == 'resumen completo'
    assert result['source_pages'] == 2
    assert 'notas una' in seen[2] and 'notas dos' in seen[2]


def test_changed_source_stops_before_generation():
    async def read(name, args):
        return page('first', 5) if not args['offset'] else page('second', revision='changed')
    async def generate(prompt):
        pytest.fail('No debe generar desde fuentes mezcladas')
    with pytest.raises(RuntimeError, match='cambió'):
        asyncio.run(ag.summarize(read, generate, 'TV99'))


def test_selection_keeps_model_proposal_for_core_validation():
    proposal = {'start_seconds': 0, 'end_seconds': 10, 'match_evidence': 'cita literal de la fuente'}
    async def read(name, args):
        return page('[0 -> 10] cita literal de la fuente')
    async def generate(prompt):
        return json.dumps({'clips': [proposal]})
    assert asyncio.run(ag.select_clips(read, generate, {'video_id': 'TV99'})) == [proposal]


def test_invalid_json_never_becomes_a_clip():
    async def read(name, args): return page('source')
    async def generate(prompt): return 'Un intervalo que no es JSON'
    with pytest.raises(RuntimeError, match='formato'):
        asyncio.run(ag.select_clips(read, generate, {'video_id': 'TV99'}))


def setup_job(monkeypatch, tmp_path):
    monkeypatch.delenv('MEDIA_GENERATION_BACKEND', raising=False)
    monkeypatch.setattr(m, 'STATE', tmp_path / 'state')
    return {'id': 'new', 'conversation_id': 'new', 'created_at': time.time(), 'events': [], 'artifacts': []}


def test_api_summary_calls_hermes_once_and_never_core_summary(monkeypatch, tmp_path):
    job = setup_job(monkeypatch, tmp_path)
    calls, prompts = [], []
    async def read(name, args):
        calls.append(name)
        return page('Contenido original de la fuente.')
    def generate(conversation, prompt, callback):
        prompts.append(prompt)
        return 'Resumen redactado por Hermes.'
    monkeypatch.setattr(m, 'mcp_call', read)
    monkeypatch.setattr(m.bridge, 'run_once', generate)
    result = asyncio.run(m.tool(job, 'media_summarize', {'video_id': 'TV99'}, 'Resumen'))
    assert result['executive_summary'] == job['summary'] == 'Resumen redactado por Hermes.'
    assert calls == ['media_read_transcript']
    assert len(prompts) == job['generation_metrics']['calls'] == 1


def test_api_selection_sends_candidates_to_core(monkeypatch, tmp_path):
    job = setup_job(monkeypatch, tmp_path)
    calls = []
    async def read(name, args):
        calls.append((name, args))
        if name == 'media_read_transcript': return page('[0 -> 10] Evidencia verificable del clip.')
        assert args['candidates'][0]['match_evidence'] == 'Evidencia verificable del clip.'
        return [{'status': 'success', 'selection_mode': 'agent'}]
    monkeypatch.setattr(m, 'mcp_call', read)
    monkeypatch.setattr(m.bridge, 'run_once', lambda *a: json.dumps({'clips': [
        {'start_seconds': 0, 'end_seconds': 10, 'match_evidence': 'Evidencia verificable del clip.'}]}))
    asyncio.run(m.tool(job, 'media_clips', {'video_id': 'TV99'}, 'Clip'))
    assert [name for name, _ in calls] == ['media_read_transcript', 'media_clips']


def test_explicit_turn_skips_generation(monkeypatch, tmp_path):
    job = setup_job(monkeypatch, tmp_path)
    async def call(name, args): return [{'status': 'success'}]
    monkeypatch.setattr(m, 'mcp_call', call)
    monkeypatch.setattr(m.bridge, 'run_once', lambda *a: pytest.fail('Turno conocido: no generar'))
    asyncio.run(m.tool(job, 'media_clips', {'video_id': 'TV99', 'person': 'Ana', 'turn_index': 2}, 'Clip'))
    assert 'generation_metrics' not in job


def test_visual_generation_rejects_answer_without_image_inspection(monkeypatch, tmp_path):
    job = setup_job(monkeypatch, tmp_path)
    monkeypatch.setattr(m.bridge, 'run_once', lambda *a: '{"description":"Inventada"}')
    with pytest.raises(RuntimeError, match='inspección'):
        asyncio.run(m.agent_text(job, 'Describe', allow_vision=True, minimum_vision_checks=1))


def test_visual_generation_accepts_confirmed_frames(monkeypatch, tmp_path):
    job = setup_job(monkeypatch, tmp_path)
    def generate(conversation, prompt, callback):
        callback('tool.start', {'name': 'vision_analyze'})
        callback('tool.complete', {'name': 'vision_analyze', 'result': {'_multimodal': True, 'meta': {'native_vision': True}}})
        return '{"description":"Plano observado"}'
    monkeypatch.setattr(m.bridge, 'run_once', generate)
    result = asyncio.run(m.agent_text(job, 'Describe', allow_vision=True, minimum_vision_checks=1))
    assert json.loads(result)['description'] == 'Plano observado'
