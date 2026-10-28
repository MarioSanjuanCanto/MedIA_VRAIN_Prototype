import numpy as np
from unittest.mock import MagicMock
from PIL import Image

from media_core.services.visual_embeddings import (
    index_keyframes_visual,
    search_visual_index,
)
from media_core.tools.visual_search import media_index_visual, media_search_visual


def test_index_and_search_visual_with_mock(tmp_path):
    kf_dir = tmp_path / "keyframes" / "TV88"
    kf_dir.mkdir(parents=True)
    img_path = kf_dir / "frame_0001.jpg"
    img = Image.new("RGB", (960, 540), color="blue")
    img.save(img_path)

    manifest = {
        "frames": [
            {
                "filename": "frame_0001.jpg",
                "timestamp": 15.5,
                "scene_id": 2,
                "is_duplicate": False,
            }
        ]
    }

    mock_collection = MagicMock()
    mock_collection.get.return_value = {"ids": []}
    mock_collection.query.return_value = {
        "ids": [["TV88_frame_0001.jpg"]],
        "metadatas": [[{
            "video_id": "TV88",
            "filename": "frame_0001.jpg",
            "timestamp": 15.5,
            "scene_id": 2,
            "keyframe_path": "keyframes/TV88/frame_0001.jpg",
        }]],
        "distances": [[0.15]],
    }

    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection

    mock_model = MagicMock()
    # Mock embedding return: vector of 512 dimensions
    mock_model.encode.return_value = np.zeros((1, 512), dtype=np.float32)

    # 1. Test indexing
    res = index_keyframes_visual(
        video_id="TV88",
        visual_manifest=manifest,
        keyframes_dir=kf_dir,
        model=mock_model,
        client=mock_client,
    )
    assert res["status"] == "success"
    assert res["indexed_keyframes_count"] == 1
    assert mock_collection.upsert.called

    # 2. Test searching
    mock_model.encode.return_value = np.zeros(512, dtype=np.float32)
    search_res = search_visual_index(
        query="coche azul",
        top_k=3,
        video_id="TV88",
        model=mock_model,
        client=mock_client,
    )
    assert len(search_res) == 1
    assert search_res[0]["video_id"] == "TV88"
    assert search_res[0]["match_type"] == "visual_concept"
    assert search_res[0]["keyframe"] == "keyframes/TV88/frame_0001.jpg"
    assert search_res[0]["relevance_score"] > 80.0


def test_media_index_visual_missing_video():
    res = media_index_visual(video_id="NON_EXISTENT_999")
    assert res["status"] == "error"


def test_media_search_visual_empty_query():
    res = media_search_visual(query="   ")
    assert res[0]["status"] == "invalid_query"
