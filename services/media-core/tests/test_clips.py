import json
from types import SimpleNamespace

from media_core.services import ffmpeg
from media_core.storage import file_fingerprint
from media_core.tools import clips


def test_media_clips_uses_mocked_openrouter(monkeypatch):
    monkeypatch.setattr(clips, "resolve_video_file", lambda video_id: None)
    monkeypatch.setattr(
        clips,
        "load_test_vector",
        lambda video_id: {
            "title": "Prueba",
            "segments": [
                {
                    "start": "00:00:00",
                    "end": "00:00:10",
                    "start_seconds": 0,
                    "end_seconds": 10,
                    "speaker": "SPEAKER_00",
                    "text": "Contenido destacado",
                }
            ],
        },
    )
    monkeypatch.setattr(clips, "get_openrouter_key", lambda: "token")
    monkeypatch.setattr(
        clips,
        "call_openrouter",
        lambda *args, **kwargs: (
            '{"clips":[{"clip_number":1,"title":"Clip",'
            '"start_time":"00:00:00","end_time":"00:00:10"}]}'
        ),
    )

    result = clips.media_clips(video_id="TV01", cut_video_files=False)

    assert len(result) == 1
    assert result[0]["title"] == "Clip"
    assert "OpenRouter" in result[0]["model_generated_by"]


def test_media_clips_reports_missing_segments(monkeypatch):
    monkeypatch.setattr(clips, "resolve_video_file", lambda video_id: None)
    monkeypatch.setattr(clips, "load_test_vector", lambda video_id: None)

    result = clips.media_clips(video_id="missing")

    assert result[0]["status"] == "error"


def test_media_clips_builds_focused_clip_and_cuts_file(tmp_path, monkeypatch):
    video = tmp_path / "TV03.mp4"
    video.write_bytes(b"video")
    output_dir = tmp_path / "clips"

    monkeypatch.setattr(clips, "OUTPUT_CLIPS_DIR", output_dir)
    monkeypatch.setattr(clips, "resolve_video_file", lambda video_id: video)
    monkeypatch.setattr(
        clips,
        "load_test_vector",
        lambda video_id: {
            "title": "Noticias",
            "id": "TV03",
            "request_metadata": {"source": [str(video.resolve()), video.stat().st_size, video.stat().st_mtime_ns]},
            "segments": [
                {
                    "start": "00:00:10",
                    "end": "00:00:20",
                    "start_seconds": 10,
                    "end_seconds": 20,
                    "speaker": "Reportera",
                    "text": "El precio del combustible vuelve a subir",
                }
            ],
        },
    )
    monkeypatch.setattr(clips, "get_openrouter_key", lambda: "")

    def fake_cut(source, start, end, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"x" * (2 * 1024 * 1024))
        return True

    monkeypatch.setattr(clips, "cut_clip", fake_cut)

    result = clips.media_clips(video_id="TV03", query="precio combustible")

    assert result[0]["topic_searched"] == "precio combustible"
    assert result[0]["cut_status"] == "archivo_mp4_cortado_exitosamente"
    assert result[0]["cut_video_size_mb"] > 0


def test_media_clips_accepts_fingerprint_created_at_another_path(tmp_path, monkeypatch):
    original = tmp_path / 'windows' / 'TV03.mp4'
    local = tmp_path / 'mac' / 'TV03.mp4'
    original.parent.mkdir()
    local.parent.mkdir()
    original.write_bytes(b'identical-video')
    local.write_bytes(b'identical-video')
    vector = {
        'id': 'TV03',
        'request_metadata': {'source': file_fingerprint(original)},
        'segments': [{
            'start': '00:00:00', 'end': '00:00:05',
            'start_seconds': 0, 'end_seconds': 5,
            'text': 'Rescate en Cabranes',
        }],
    }
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: local)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: vector)
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: '')

    result = clips.media_clips(
        video_id='TV03', query='rescate Cabranes', cut_video_files=False
    )

    assert result[0]['selection_mode'] == 'lexical'


def test_cut_clip_falls_back_to_reencoding(tmp_path, monkeypatch):
    output = tmp_path / "clip.mp4"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 2:
            from pathlib import Path
            Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    assert ffmpeg.cut_clip(tmp_path / "video.mp4", "00:00:00", "00:00:10", output)
    assert len(calls) == 2
    assert "libx264" in calls[1]

def test_named_speaker_not_found_never_falls_back_to_llm(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [
        {'start_seconds': 0, 'end_seconds': 10, 'text': 'El momento donde habla de economía.', 'speaker': 'A'}]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    monkeypatch.setattr(clips, 'call_openrouter', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Must not generate an unrelated clip')))
    result = clips.media_clips(query='El momento donde habla Maria Perez', cut_video_files=False)
    assert result[0]['status'] == 'error'


def test_fast_query_without_text_match_does_not_call_openrouter(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [
        {'start_seconds': 0, 'end_seconds': 10, 'text': 'Una intervención sobre servicios públicos.', 'speaker': 'A'}]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    monkeypatch.setattr(clips, 'call_openrouter', lambda *a, **k: (_ for _ in ()).throw(AssertionError('fast must not call the LLM')))

    result = clips.media_clips(query='electricidad', selection_mode='fast', cut_video_files=False)

    assert result[0]['status'] == 'error'
    assert 'editorial' in result[0]['message']


def test_auto_query_uses_lexical_match_without_openrouter(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [{
        'start_seconds': 0,
        'end_seconds': 10,
        'text': 'Accidente de un motorista en Cabranes.',
        'speaker': 'A',
    }]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    monkeypatch.setattr(clips, 'call_openrouter', lambda *a, **k: (_ for _ in ()).throw(AssertionError('auto must prefer lexical evidence')))

    result = clips.media_clips(
        query='accidente motorista Cabranes',
        selection_mode='auto',
        cut_video_files=False,
    )

    assert result[0]['selection_mode'] == 'lexical'


def test_auto_query_falls_back_to_editorial(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [{
        'start_seconds': 0,
        'end_seconds': 10,
        'text': 'La subida del precio de la luz afecta a las familias.',
        'speaker': 'A',
    }]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    monkeypatch.setattr(
        clips,
        'call_openrouter',
        lambda *a, **k: ('{"clips":[{"start_time":"00:00:00","end_time":"00:00:10",'
                              '"match_evidence":"La subida del precio de la luz afecta a las familias."}]}'),
    )

    result = clips.media_clips(
        query='electricidad', selection_mode='auto', cut_video_files=False
    )

    assert result[0]['selection_mode'] == 'editorial'


def test_editorial_query_retries_once_with_explicit_evidence_contract(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [
        {'start_seconds': 0, 'end_seconds': 10,
         'text': 'La subida del precio de la luz afecta a las familias.', 'speaker': 'A'}]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    responses = iter([
        '{"clips":[{"start_time":"00:00:00","end_time":"00:00:10"}]}',
        '{"clips":[{"start_time":"00:00:00","end_time":"00:00:10",'
        '"match_evidence":"La subida del precio de la luz afecta a las familias."}]}',
    ])
    calls = []

    def call_openrouter(messages, **kwargs):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(clips, 'call_openrouter', call_openrouter)

    result = clips.media_clips(query='electricidad', selection_mode='editorial', cut_video_files=False)

    assert result[0]['selection_mode'] == 'editorial'
    assert result[0]['match_evidence'].startswith('La subida')
    assert len(calls) == 2
    assert '- match_evidence: str' in calls[0][0]['content']
    assert 'respuesta anterior' in calls[1][-1]['content']


def test_editorial_prompt_uses_compact_topic_on_first_attempt(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [{
        'start_seconds': 0,
        'end_seconds': 10,
        'text': 'Accidente de un motorista en Cabranes.',
        'speaker': 'A',
    }]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    calls = []

    def call_openrouter(messages, **kwargs):
        calls.append(messages)
        return ('{"clips":[{"start_time":"00:00:00","end_time":"00:00:10",'
                '"match_evidence":"Accidente de un motorista en Cabranes."}]}')

    monkeypatch.setattr(clips, 'call_openrouter', call_openrouter)

    result = clips.media_clips(
        query='Hazme por favor un clip de TV05 sobre el accidente de un motorista en Cabranes',
        selection_mode='editorial',
        cut_video_files=False,
    )

    prompt = ' '.join(message['content'] for message in calls[0])
    assert result[0]['selection_mode'] == 'editorial'
    assert 'accidente motorista cabranes' in prompt.lower()
    assert 'hazme por favor' not in prompt.lower()


def test_editorial_query_needs_evidence_inside_selected_interval(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'segments': [
        {'start_seconds': 0, 'end_seconds': 10, 'text': 'Una intervención sobre servicios públicos.', 'speaker': 'A'}]})
    monkeypatch.setattr(clips, 'get_openrouter_key', lambda: 'key')
    calls = []
    def invalid_response(*args, **kwargs):
        calls.append(1)
        return '{"clips":[{"start_time":"00:00:00","end_time":"00:00:10","match_evidence":"Una frase inventada sobre astronomía"}]}'
    monkeypatch.setattr(clips, 'call_openrouter', invalid_response)
    assert clips.media_clips(query='astronomía', selection_mode='editorial', cut_video_files=False)[0]['status'] == 'error'
    assert len(calls) == 2


def test_precise_cut_uses_encoder_instead_of_keyframe_copy(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        from pathlib import Path
        calls.append(command)
        Path(command[-1]).write_bytes(b'x' * 2048)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ffmpeg.subprocess, 'run', run)
    assert ffmpeg.cut_clip(tmp_path/'source.mp4', 2.35, 5.85, tmp_path/'result.mp4', precise=True)
    assert 'copy' not in calls[0] and 'libx264' in calls[0]
    assert calls[0][calls[0].index('-ss')+1] == '2.35'


def test_tv04_sixth_and_first_five_illa_turns_are_selected_by_index(monkeypatch):
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(clips, 'call_openrouter', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('A diarized turn must not be selected by an LLM')))

    sixth = clips.media_clips(video_id='TV04', person='Salvador Illa', turn_index=6,
                              cut_video_files=False)
    assert len(sixth) == 1
    assert sixth[0]['turn_index'] == 6
    assert (sixth[0]['start_seconds'], sixth[0]['end_seconds']) == (1092.48, 1126.68)
    assert sixth[0]['selection_mode'] == 'speaker_turn_exact'

    first_five = clips.media_clips(video_id='TV04', person='Salvador Illa',
                                  turn_index=1, max_clips=5, cut_video_files=False)
    assert len(first_five) == 5
    assert [item['turn_index'] for item in first_five] == [1, 2, 3, 4, 5]
    assert [item['start_seconds'] for item in first_five] == sorted(
        item['start_seconds'] for item in first_five)
    assert all(item['turns_found'] == 13 for item in first_five)

    nonconsecutive = clips.media_clips(video_id='TV04', person='Salvador Illa',
                                       turn_indices=[2, 6], cut_video_files=False)
    assert [item['turn_index'] for item in nonconsecutive] == [2, 6]
    assert clips.media_clips(video_id='TV04', person='Salvador Illa',
                             turn_index=14, cut_video_files=False)[0]['status'] == 'error'


def test_turn_clips_work_for_another_video_and_cut_each_exact_interval(tmp_path, monkeypatch):
    from media_core.services import speaker_identities
    monkeypatch.setattr(speaker_identities, 'IDENTITIES_CACHE_DIR', tmp_path)
    video = tmp_path / 'TV99.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: video)
    monkeypatch.setattr(clips, 'OUTPUT_CLIPS_DIR', tmp_path / 'clips')
    monkeypatch.setattr(clips, 'CACHE_DIR', tmp_path)
    segments = [
        {'start': '00:00:00', 'end': '00:00:02', 'start_seconds': 0, 'end_seconds': 2,
         'speaker': 'MOD', 'text': 'Tiene la palabra la señora María Pérez.'},
        {'start': '00:00:02', 'end': '00:00:05', 'start_seconds': 2.25, 'end_seconds': 5.4,
         'speaker': 'GUEST', 'text': 'Primera intervención.'},
        {'start': '00:00:05', 'end': '00:00:06', 'start_seconds': 5.4, 'end_seconds': 6,
         'speaker': 'MOD', 'text': 'Gracias.'},
        {'start': '00:00:06', 'end': '00:00:10', 'start_seconds': 6.25, 'end_seconds': 10.7,
         'speaker': 'GUEST', 'text': 'Segunda intervención.'},
    ]
    source_identity = [str(video.resolve()), video.stat().st_size, video.stat().st_mtime_ns]
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {
        'id': 'TV99', 'request_metadata': {'source': source_identity}, 'segments': segments,
    })
    # An older cache with another turn order must not change indices shown by media_speakers.
    (tmp_path / 'TV99_whisperx.json').write_text(json.dumps({
        'id': 'TV99', 'request_metadata': {'source': source_identity},
        'segments': [{'speaker': 'OTHER', 'start_seconds': 1, 'end_seconds': 20, 'text': 'Old cache'}],
    }), encoding='utf-8')
    calls = []
    durations = {}

    def fake_cut(source, start, end, output, **kwargs):
        calls.append((start, end, kwargs['precise']))
        durations[output] = end - start
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'x' * 2048)
        return True

    monkeypatch.setattr(clips, 'cut_clip', fake_cut)
    monkeypatch.setattr(clips, 'probe_video', lambda path: {'duration': durations[path]})
    result = clips.media_clips(video_id='TV99', person='María Pérez',
                               turn_index=1, max_clips=2)
    assert len(result) == 2
    assert [(item['start_seconds'], item['end_seconds']) for item in result] == [
        (2.25, 5.4), (6.25, 10.7)]
    assert calls == [(2.25, 5.4, True), (6.25, 10.7, True)]
    assert all(item['cut_status'] == 'archivo_mp4_cortado_exitosamente' for item in result)
    assert len({item['cut_video_file'] for item in result}) == 2


def test_exact_interval_rejects_invalid_range_and_uses_reencoding(tmp_path, monkeypatch):
    video = tmp_path / 'TV03.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: video)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: None)
    monkeypatch.setattr(clips, 'OUTPUT_CLIPS_DIR', tmp_path / 'clips')
    calls = []

    def fake_cut(source, start, end, output, **kwargs):
        calls.append((start, end, kwargs['precise']))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'x' * 2048)
        return True

    monkeypatch.setattr(clips, 'cut_clip', fake_cut)
    monkeypatch.setattr(clips, 'probe_video', lambda path: {'duration': 100.0 if path == video else 4.4})
    assert clips.media_clips('TV03', start_seconds=98, end_seconds=102)[0]['status'] == 'error'
    result = clips.media_clips('TV03', start_seconds=10.1, end_seconds=14.5)
    assert result[0]['cut_status'] == 'archivo_mp4_cortado_exitosamente'
    assert calls == [(10.1, 14.5, True)]
