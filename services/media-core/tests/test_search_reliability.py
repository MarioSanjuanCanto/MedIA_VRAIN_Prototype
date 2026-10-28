from media_core.tools import search, summarize
from media_core.services.chroma import ChromaVectorStore


def test_generic_words_and_prefixes_are_not_search_evidence(monkeypatch):
    monkeypatch.setattr(search, 'search_vector_index', lambda *a, **k: [])
    monkeypatch.setattr(search, 'get_all_test_vectors', lambda: [{'id': 'TV04', 'segments': [
        {'text': 'Habla de cuántos actos contra la integridad física.', 'start_seconds': 0, 'end_seconds': 10}]}])
    assert search.media_search('ornitorrinco cuántico intergaláctico', video_id='TV04')[0]['status'] == 'no_results'
    assert search.media_search('Donde habla Maria Perez', video_id='TV04')[0]['status'] == 'no_results'


def test_named_search_returns_verified_turn_despite_spelling(monkeypatch):
    monkeypatch.setattr(search, 'search_vector_index', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Do not guess names semantically')))
    monkeypatch.setattr(search, 'resolve_video_file', lambda _: None)
    monkeypatch.setattr(search, 'get_all_test_vectors', lambda: [{'id': 'TV04', 'segments': [
        {'text': 'Tiene la palabra Silvia Oriol Serra.', 'speaker': 'CHAIR'},
        {'text': 'Esta es mi intervención.', 'speaker': 'SPEAKER_08', 'start': '00:01:00', 'end': '00:01:10', 'start_seconds': 60, 'end_seconds': 70}]}])
    result = search.media_search('Donde habla Silvia Orriols', video_id='TV04.mp4')[0]
    assert result['start_time'] == '00:01:00'
    assert result['search_engine'] == 'transcript_speaker_turn'
    assert result['match_warning']


def test_exact_search_recovers_broken_index_with_scope():
    class Embedder:
        def embed_query(self, _): return [1, 0]
    class Collection:
        def count(self): return 3
        def query(self, **kw): raise RuntimeError('Error finding id')
        def get(self, **kw):
            assert kw['where'] == {'video_id': 'TV04'}
            return {'ids': ['wrong', 'right'], 'documents': ['Other', 'Relevant'],
                    'metadatas': [{'video_id': 'TV04'}] * 2, 'embeddings': [[0, 1], [1, 0]]}
    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.collection = Collection()
    store.embedder = Embedder()
    result = store.query('question', 1, 'TV04')
    assert result[0]['id'] == 'right'
    assert result[0]['distance'] == 0
    assert result[0]['retrieval_mode'] == 'exact_cosine_fallback'


def test_summary_rejects_stale_physical_source(tmp_path, monkeypatch):
    import json
    video = tmp_path / 'QA_control.mp4'
    video.write_bytes(b'new media')
    (tmp_path / 'QA_control_whisperx.json').write_text(json.dumps({'id': 'QA_CONTROL', 'full_text': 'Wrong old text'}))
    monkeypatch.setattr(summarize, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(summarize, 'resolve_video_file', lambda _: video)
    monkeypatch.setattr(summarize, 'load_test_vector', lambda _: {'segments': [{'text': 'Wrong archive'}]})
    result = summarize.media_summarize(video_id=str(video))
    assert result['status'] == 'error'


def test_search_failure_is_not_claimed_as_no_results(monkeypatch):
    monkeypatch.setattr(search, 'get_all_test_vectors', lambda: [])
    monkeypatch.setattr(search, 'search_vector_index', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('broken')))
    assert search.media_search('presupuesto')[0]['status'] == 'search_unavailable'


def test_clip_rejects_unverified_archive_for_physical_video(tmp_path, monkeypatch):
    from media_core.tools import clips
    video = tmp_path / 'TV04.mp4'
    video.write_bytes(b'new source')
    monkeypatch.setattr(clips, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(clips, 'resolve_video_file', lambda _: video)
    monkeypatch.setattr(clips, 'load_test_vector', lambda _: {'id': 'TV04', 'segments': [{'text': 'Old budget'}]})
    assert clips.media_clips('TV04', query='budget')[0]['status'] == 'error'


def test_diarization_access_error_is_actionable(tmp_path, monkeypatch):
    import replicate
    from media_core.services import whisperx
    class Client:
        def __init__(self, **kw): pass
        def run(self, *a, **kw): raise RuntimeError('GatedRepoError: 403 pyannote')
    monkeypatch.setattr(replicate, 'Client', Client)
    monkeypatch.setattr(whisperx, 'get_replicate_token', lambda: 'test')
    monkeypatch.setattr(whisperx, 'get_hf_token', lambda: 'test')
    audio = tmp_path / 'audio.mp3'
    audio.write_bytes(b'audio')
    result = whisperx.transcribe_with_whisperx(audio)
    assert result['error_code'] == 'diarization_access_denied'
    assert 'speaker-diarization-community-1' in result['message']
