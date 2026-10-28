"""Tests for scene detection, keyframing, and perceptual deduplication."""

import json
from pathlib import Path
from PIL import Image, ImageDraw
import pytest

from media_core.services.scene_detection import compute_dhash, hamming_distance, analyze_video_frames
from media_core.tools.analyze_video import media_analyze_video


def _create_test_image(path: Path, color: tuple, shape: str = "rect") -> Path:
    img = Image.new("RGB", (100, 100), color=color)
    draw = ImageDraw.Draw(img)
    if shape == "circle":
        draw.ellipse([20, 20, 80, 80], fill=(255, 255, 255))
    elif shape == "line":
        draw.line([0, 0, 100, 100], fill=(0, 0, 0), width=5)
    img.save(path, "JPEG")
    return path


def test_dhash_and_hamming_distance(tmp_path):
    img1 = _create_test_image(tmp_path / "img1.jpg", (255, 0, 0), "rect")
    img2 = _create_test_image(tmp_path / "img2.jpg", (255, 0, 0), "rect")
    img3 = _create_test_image(tmp_path / "img3.jpg", (0, 255, 0), "circle")

    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)
    h3 = compute_dhash(img3)

    assert h1 == h2
    assert hamming_distance(h1, h2) == 0
    assert hamming_distance(h1, h3) > 5


def test_media_analyze_video_handles_missing_file():
    res = media_analyze_video("MISSING_VIDEO_123")
    assert res.get("status") == "error"
    assert "No se encontró el archivo de vídeo" in res.get("message", "")


def test_media_analyze_video_cache_hit(tmp_path, monkeypatch):
    import media_core.tools.analyze_video as av
    import media_core.config as cfg

    # Setup isolated directories
    visual_cache = tmp_path / "visual"
    keyframes = tmp_path / "keyframes"
    visual_cache.mkdir(parents=True)
    keyframes.mkdir(parents=True)
    monkeypatch.setattr(cfg, "VISUAL_CACHE_DIR", visual_cache)
    monkeypatch.setattr(cfg, "KEYFRAMES_DIR", keyframes)
    monkeypatch.setattr(av, "VISUAL_CACHE_DIR", visual_cache)
    monkeypatch.setattr(av, "KEYFRAMES_DIR", keyframes)

    # Fake video file
    fake_video = tmp_path / "TV01.mp4"
    fake_video.write_bytes(b"dummy video content for test")

    monkeypatch.setattr(av, "resolve_video_file", lambda _: fake_video)

    # Mock analyze_video_frames
    calls = []
    def mock_analyze(video_path, video_id, **kwargs):
        calls.append(video_id)
        return {
            "status": "success",
            "video_id": video_id,
            "duration_seconds": 10.0,
            "scenes_count": 2,
            "keyframes_count": 4,
            "kept_keyframes_count": 3,
            "deduplicated_count": 1,
            "scenes": [],
            "frames": [],
        }

    monkeypatch.setattr(av, "analyze_video_frames", mock_analyze)

    # First call: executes pipeline and writes cache
    res1 = media_analyze_video("TV01")
    assert res1["status"] == "success"
    assert res1["cache_hit"] is False
    assert len(calls) == 1

    # Second call: returns cached result instantly
    res2 = media_analyze_video("TV01")
    assert res2["status"] == "success"
    assert res2["cache_hit"] is True
    assert len(calls) == 1

    # Force call: bypasses cache
    res3 = media_analyze_video("TV01", force=True)
    assert res3["status"] == "success"
    assert res3["cache_hit"] is False
    assert len(calls) == 2
