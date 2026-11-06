import json
import pytest
from PIL import Image
from media_core.tools import context, clips, vlm_tool


@pytest.fixture
def transcript(monkeypatch):
    source = {'id': 'TV99', 'title': 'Fuente', 'segments': [
        {'start_seconds': 0, 'end_seconds': 10, 'text': 'La inversión será de cinco millones de euros.'},
        {'start_seconds': 10, 'end_seconds': 20, 'text': 'El segundo tema es el transporte público.'},
    ]}
    for module in (context, clips):
        monkeypatch.setattr(module, 'resolve_video_file', lambda _: None)
        monkeypatch.setattr(module, 'load_test_vector', lambda _: source)
    monkeypatch.setattr(clips, 'call_openrouter', lambda *a, **k: pytest.fail('No debe llamar a otro modelo'))
    return source


def test_read_transcript_paginates_without_losing_text(transcript):
    transcript['segments'] *= 30
    complete = context.media_read_transcript('TV99', max_chars=48000)
    chunks, offset = [], 0
    while True:
        page = context.media_read_transcript('TV99', offset=offset, max_chars=1000)
        assert page['source_revision'] == complete['source_revision']
        assert page['llm_calls'] == 0
        chunks.append(page['text'])
        offset = page['next_offset']
        if offset is None:
            break
    assert ''.join(chunks) == complete['text']


def test_read_interval_excludes_unrelated_topic(transcript):
    result = context.media_read_transcript('TV99', start_seconds=0, end_seconds=10)
    assert 'cinco millones' in result['text']
    assert 'transporte' not in result['text']


def test_reader_rejects_stale_source(monkeypatch, tmp_path):
    video = tmp_path / 'TV99.mp4'
    video.write_bytes(b'new video')
    monkeypatch.setattr(context, 'resolve_video_file', lambda _: video)
    monkeypatch.setattr(context, 'CACHE_DIR', tmp_path)
    (tmp_path / 'TV99_whisperx.json').write_text(json.dumps({'id': 'TV99', 'full_text': 'old text'}))
    monkeypatch.setattr(context, 'load_test_vector', lambda _: {'id': 'TV99', 'full_text': 'wrong source'})
    result = context.media_read_transcript('TV99')
    assert result['status'] == 'error'


def test_agent_candidates_require_local_literal_evidence(transcript):
    candidate = {'start_seconds': 0, 'end_seconds': 10, 'title': 'Inversión',
                 'match_evidence': 'La inversión será de cinco millones'}
    result = clips.media_clips('TV99', candidates=[candidate], cut_video_files=False)
    assert result[0]['selection_mode'] == 'agent'
    assert result[0]['start_seconds'] == 0
    wrong = {**candidate, 'start_seconds': 10, 'end_seconds': 20}
    assert clips.media_clips('TV99', candidates=[wrong], cut_video_files=False)[0]['status'] == 'error'


def test_invalid_candidate_prevents_all_cuts(transcript, monkeypatch):
    monkeypatch.setattr(clips, '_cut_selected_clips', lambda *a, **k: pytest.fail('No debe cortar'))
    valid = {'start_seconds': 0, 'end_seconds': 10, 'match_evidence': 'La inversión será de cinco millones'}
    invalid = {**valid, 'end_seconds': float('nan')}
    result = clips.media_clips('TV99', max_clips=2, candidates=[valid, invalid])
    assert result[0]['status'] == 'error'


def test_fast_without_query_never_calls_model(transcript, monkeypatch):
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'configured')
    result = clips.media_clips('TV99', selection_mode='fast', cut_video_files=False)
    assert result[0]['status'] == 'error'


def test_scene_context_preserves_frames_and_ocr_without_vlm(tmp_path, monkeypatch):
    video = tmp_path / 'TV99.mp4'
    video.touch()
    frames = tmp_path / 'keyframes' / 'TV99'
    frames.mkdir(parents=True)
    for n in (8, 10, 12):
        Image.new('RGB', (30, 30), 'green').save(frames / f'frame_{n}.jpg')
    manifest = {'frames': [{'filename': f'frame_{n}.jpg', 'timestamp': n} for n in (8, 10, 12)]}
    monkeypatch.setattr(vlm_tool, 'KEYFRAMES_DIR', tmp_path / 'keyframes')
    monkeypatch.setattr(vlm_tool, 'VISUAL_CACHE_DIR', tmp_path)
    monkeypatch.setattr(vlm_tool, 'OCR_CACHE_DIR', tmp_path)
    monkeypatch.setattr(vlm_tool, 'resolve_video_file', lambda _: video)
    monkeypatch.setattr(vlm_tool, 'load_test_vector', lambda _: None)
    monkeypatch.setattr(vlm_tool, 'media_analyze_video', lambda **k: manifest)
    monkeypatch.setattr(vlm_tool, 'describe_keyframe_vlm', lambda **k: pytest.fail('No debe consultar VLM'))
    (tmp_path / 'TV99_ocr.json').write_text(json.dumps({'extracted_texts': [{'timestamp': 10, 'text': 'DIRECTO'}]}))
    result = vlm_tool.media_scene_context('TV99', timestamp=10)
    assert result['status'] == 'success'
    assert result['llm_calls'] == 0
    assert [f['timestamp'] for f in result['evidence_frames']] == [8, 10, 12]
    assert result['multimodal_context']['ocr_texts'] == ['DIRECTO']
    assert 'description' not in result
    with Image.open(result['vision_image_path']) as sheet:
        assert sheet.height == 3 * (30 + 24)
        assert sheet.width == 30
    assert vlm_tool.media_scene_context('TV99', timestamp=10)['vision_image_path'] == result['vision_image_path']


def test_untimed_transcript_can_be_summarized_but_not_used_for_cuts(transcript):
    transcript['segments'] = [{'text': 'Texto completo sin tiempos.'}]
    result = context.media_read_transcript('TV99')
    assert result['status'] == 'success'
    assert result['text'] == 'Texto completo sin tiempos.'
    assert result['has_timestamps'] is False
