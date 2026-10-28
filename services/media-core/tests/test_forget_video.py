from pathlib import Path

import pytest

from media_core.tools import forget_video


class FakeCollection:
    def __init__(self):
        self.ids = ['TV42_1', 'TV99_1']

    def get(self, where):
        return {'ids': [item for item in self.ids if item.startswith(where['video_id'] + '_')]}

    def delete(self, ids):
        self.ids = [item for item in self.ids if item not in ids]


class FakeClient:
    def __init__(self):
        self.collections = {
            name: FakeCollection()
            for name in (
                forget_video.CHROMA_COLLECTION,
                forget_video.VISUAL_COLLECTION_NAME,
                'media_temporal_events_v1',
            )
        }

    def get_collection(self, name):
        return self.collections[name]


def test_forget_video_removes_only_selected_video_data(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(forget_video.chromadb, 'PersistentClient', lambda **kwargs: client)
    monkeypatch.setattr(forget_video, 'BASE_DIR', tmp_path)
    for name in (
        'CACHE_DIR', 'TRANSCRIPTS_DIR', 'VISUAL_CACHE_DIR', 'OCR_CACHE_DIR',
        'IDENTITIES_CACHE_DIR', 'TEMPORAL_CACHE_DIR', 'KEYFRAMES_DIR',
        'VISUAL_SWEEPS_DIR', 'FACES_CACHE_DIR', 'CROPS_CACHE_DIR',
    ):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(forget_video, name, directory)

    (forget_video.CACHE_DIR / 'TV42_whisperx.json').write_text('transcript')
    (forget_video.CACHE_DIR / 'TV99_whisperx.json').write_text('keep')
    keyframes = forget_video.KEYFRAMES_DIR / 'TV42'
    keyframes.mkdir()
    (keyframes / 'frame.jpg').write_bytes(b'frame')

    result = forget_video.media_forget_video('TV42', 'TV42')

    assert result['removed_vectors'] == 3
    assert not (forget_video.CACHE_DIR / 'TV42_whisperx.json').exists()
    assert not keyframes.exists()
    assert (forget_video.CACHE_DIR / 'TV99_whisperx.json').exists()
    assert all(collection.ids == ['TV99_1'] for collection in client.collections.values())


def test_forget_video_rejects_path_like_ids():
    with pytest.raises(ValueError):
        forget_video.media_forget_video('../TV42')
    with pytest.raises(ValueError):
        forget_video.media_forget_video('TV42', '../other')
