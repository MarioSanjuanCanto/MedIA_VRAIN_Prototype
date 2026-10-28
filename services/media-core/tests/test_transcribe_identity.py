import json
import threading
import time
from media_core import storage
from media_core.tools import transcribe, jobs


def test_missing_source_never_defaults_to_tv01(monkeypatch):
    monkeypatch.setattr(transcribe, 'resolve_video_file', lambda _: (_ for _ in ()).throw(AssertionError()))
    assert transcribe.media_transcribe()['error_code'] == 'source_required'


def test_tv02_filename_and_id_resolve_to_same_file(tmp_path, monkeypatch):
    source = tmp_path / 'TV02.mp4'
    source.touch()
    monkeypatch.setattr(storage, 'VIDEOS_DIR', tmp_path)
    assert storage.resolve_video_file('TV02') == source
    assert storage.resolve_video_file('TV02.mp4') == source


def test_archive_rejects_mismatched_id(tmp_path, monkeypatch):
    (tmp_path / 'TV02.json').write_text(json.dumps({'id': 'TV01', 'full_text': 'Wrong video'}))
    monkeypatch.setattr(storage, 'TRANSCRIPTS_DIR', tmp_path)
    monkeypatch.setattr(storage, 'CACHE_DIR', tmp_path / 'cache')
    assert storage.load_test_vector('TV02') is None


def test_content_fingerprint_is_portable_across_paths(tmp_path):
    mac_path = tmp_path / 'mac' / 'TV05.mp4'
    docker_path = tmp_path / 'workspace' / 'TV05.mp4'
    mac_path.parent.mkdir()
    docker_path.parent.mkdir()
    mac_path.write_bytes(b'same-video-content')
    docker_path.write_bytes(b'same-video-content')

    assert storage.file_fingerprint(mac_path) == storage.file_fingerprint(docker_path)


def test_content_fingerprint_changes_with_video_content(tmp_path):
    first = tmp_path / 'first.mp4'
    second = tmp_path / 'second.mp4'
    first.write_bytes(b'video-one')
    second.write_bytes(b'video-two')

    assert storage.file_fingerprint(first) != storage.file_fingerprint(second)


def test_new_video_never_reuses_legacy_or_other_video(tmp_path, monkeypatch):
    source = tmp_path / 'TV02.mp4'
    source.write_bytes(b'new video')
    monkeypatch.setattr(transcribe, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(transcribe, 'resolve_video_file', lambda _: source)
    monkeypatch.setattr(transcribe, 'get_replicate_token', lambda: '')
    for data in [{'id': 'TV01', 'language': 'es', 'segments': [{'text': 'Wrong'}]},
                 {'id': 'TV02', 'language': 'es', 'segments': [{'text': 'Unverified'}]}]:
        monkeypatch.setattr(transcribe, 'load_test_vector', lambda _: data)
        result = transcribe.media_transcribe('TV02', diarization=False)
        assert result['video_id'] == 'TV02'
        assert result['error_code'] == 'missing_replicate_key'


def test_verified_cache_is_indexed_when_requested(tmp_path, monkeypatch):
    source = tmp_path / 'TV02.mp4'
    source.touch()
    data = {'id': 'TV02', 'language': 'es', 'segments': [{'text': 'Correct'}],
            'indexed_in_search': False, 'request_metadata': {
                'source': [str(source.resolve()), 0, source.stat().st_mtime_ns], 'language': 'es', 'diarization': False}}
    monkeypatch.setattr(transcribe, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(transcribe, 'resolve_video_file', lambda _: source)
    monkeypatch.setattr(transcribe, 'load_test_vector', lambda _: data)
    calls = []
    monkeypatch.setattr(transcribe, 'index_test_vector', lambda d: calls.append(d['id']))
    result = transcribe.media_transcribe('TV02', diarization=False)
    assert result['indexed_in_search'] is True
    assert calls == ['TV02']


def test_transcribe_background_returns_id_before_asr_finishes(tmp_path, monkeypatch):
    source = tmp_path / 'TV02.mp4'
    source.touch()
    monkeypatch.setattr(transcribe, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(transcribe, 'TRANSCRIPTS_DIR', tmp_path / 'vectors')
    monkeypatch.setattr(transcribe, 'resolve_video_file', lambda _: source)
    monkeypatch.setattr(transcribe, 'load_test_vector', lambda _: None)
    monkeypatch.setattr(transcribe, 'get_replicate_token', lambda: 'test')
    monkeypatch.setattr(transcribe, 'extract_audio', lambda *args: True)
    gate = threading.Event()
    def asr(*args, **kwargs):
        assert gate.wait(5)
        return {'status': 'success', 'language': 'es', 'segments': [{'text': 'TV02'}]}
    monkeypatch.setattr(transcribe, 'transcribe_with_whisperx', asr)
    try:
        result = transcribe.media_transcribe('TV02', background=True, index_transcript=False)
        assert result['video_id'] == 'TV02'
        assert result['status'] == 'queued'
        assert jobs.media_job_status(result['job_id'])['status'] in {'queued', 'running'}
    finally:
        gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = jobs.media_job_status(result['job_id'])
        if state['status'] in {'completed', 'failed'}:
            break
        time.sleep(.01)
    assert state['status'] == 'completed'
    assert state['result']['video_id'] == 'TV02'
