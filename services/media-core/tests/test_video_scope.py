from types import SimpleNamespace
from media_core.tools import search, summarize
from media_core.services.chroma import ChromaVectorStore


def test_chroma_filters_before_ranking():
    captured = {}
    def query(**kwargs):
        captured.update(kwargs)
        return {}
    store = object.__new__(ChromaVectorStore)
    store.collection = SimpleNamespace(count=lambda: 100, query=query)
    store.embedder = SimpleNamespace(embed_query=lambda text: [0.1])
    assert store.query('health', 3, video_id='TV04') == []
    assert captured['where'] == {'video_id': 'TV04'}
    assert captured['n_results'] == 3


def test_search_never_leaks_other_videos(monkeypatch):
    def query(text, top_k, video_id):
        assert video_id == 'TV04'
        return [{'metadata': {'video_id': name}, 'document': 'health', 'distance': .1}
                for name in ['TV01', 'TV04']]
    monkeypatch.setattr(search, 'search_vector_index', query)
    result = search.media_search('health', video_id='tv04')
    assert [x['video_id'] for x in result] == ['TV04']


def test_empty_video_filter_does_not_fallback_to_other_video(monkeypatch):
    monkeypatch.setattr(search, 'search_vector_index', lambda *args, **kwargs: [])
    monkeypatch.setattr(search, 'get_all_test_vectors', lambda: [
        {'id': 'TV01', 'segments': [{'text': 'health'}]}])
    assert search.media_search('health', video_id='TV04')[0]['status'] == 'no_results'


def test_brief_summary_uses_full_transcript_and_plain_english(monkeypatch):
    monkeypatch.setattr(summarize, 'get_openrouter_key', lambda: 'test')
    def generate(messages, **kwargs):
        assert kwargs['json_mode'] is False
        assert 'English' in messages[0]['content']
        assert '60 palabras' in messages[0]['content']
        assert 'opening middle ending' in messages[1]['content']
        return 'The video covers a parliamentary debate.'
    monkeypatch.setattr(summarize, 'call_openrouter', generate)
    result = summarize.media_summarize(transcript_text='opening middle ending', language='en', style='breve')
    assert result['status'] == 'success'
    assert result['response_format'] == 'text'
    assert result['key_points'] == []


def test_bad_json_retries_as_plain_text(monkeypatch):
    monkeypatch.setattr(summarize, 'get_openrouter_key', lambda: 'test')
    calls = []
    def generate(messages, **kwargs):
        calls.append(kwargs['json_mode'])
        return '{"broken' if len(calls) == 1 else 'A discussion of public services.'
    monkeypatch.setattr(summarize, 'call_openrouter', generate)
    result = summarize.media_summarize(transcript_text='Source material', language='en')
    assert calls == [True, False]
    assert result['status'] == 'success' and result['format_retry']
    assert result['response_format'] == 'text'
