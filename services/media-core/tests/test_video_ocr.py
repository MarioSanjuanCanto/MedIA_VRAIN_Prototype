import json
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from media_core.services.video_ocr import (
    classify_spatial_region,
    clean_text_candidate,
    extract_ocr_from_frame,
    analyze_video_ocr,
)
from media_core.tools.ocr import media_ocr_video
from media_core.tools import ocr as ocr_tool


def test_classify_spatial_region():
    # Lower third (y_top >= 0.58)
    lower_box = [[100, 400], [400, 400], [400, 480], [100, 480]]
    assert classify_spatial_region(lower_box, 960, 540) == "lower_third"

    # Upper banner (y_bottom <= 0.22)
    upper_box = [[50, 20], [200, 20], [200, 80], [50, 80]]
    assert classify_spatial_region(upper_box, 960, 540) == "upper_banner"

    # Center screen
    center_box = [[200, 200], [600, 200], [600, 300], [200, 300]]
    assert classify_spatial_region(center_box, 960, 540) == "center_screen"


def test_clean_text_candidate():
    assert clean_text_candidate("  Laura   Barrero  ") == "Laura Barrero"
    assert clean_text_candidate("14:00") == "14:00"
    assert clean_text_candidate("日") == ""
    assert clean_text_candidate("a") == ""
    assert clean_text_candidate("85") == "85"


def test_analyze_video_ocr_with_mock_engine(tmp_path):
    # Create fake keyframes
    kf_dir = tmp_path / "keyframes" / "TV99"
    kf_dir.mkdir(parents=True)
    f1 = kf_dir / "frame_0001.jpg"

    # Create a small valid image
    from PIL import Image
    img = Image.new("RGB", (960, 540), color="white")
    img.save(f1)

    mock_engine = MagicMock()
    # Mock return: [ [box, text, score] ], elapse
    mock_engine.return_value = (
        [
            [[[100, 420], [400, 420], [400, 470], [100, 470]], "Laura Barrero", 0.95],
            [[[50, 30], [180, 30], [180, 60], [50, 60]], "DIRECTO", 0.88],
        ],
        0.05,
    )

    manifest = {
        "scenes": [
            {
                "id": 1,
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "keyframe": "keyframes/TV99/frame_0001.jpg",
            }
        ],
        "frames": [
            {
                "filename": "frame_0001.jpg",
                "timestamp": 0.0,
                "scene_id": 1,
                "is_scene_boundary": True,
                "is_duplicate": False,
            }
        ],
    }

    res = analyze_video_ocr(
        video_id="TV99",
        keyframes_dir=kf_dir,
        visual_manifest=manifest,
        min_confidence=0.6,
        engine=mock_engine,
    )

    assert res["status"] == "success"
    assert res["total_texts_detected"] == 2
    assert "Laura Barrero" in res["all_text"]
    assert "DIRECTO" in res["all_text"]
    assert res["scenes"][0]["lower_thirds"] == ["Laura Barrero"]
    assert res["scenes"][0]["upper_banners"] == ["DIRECTO"]


def test_media_ocr_video_missing_file():
    res = media_ocr_video(video_id="NON_EXISTENT_VIDEO_99")
    assert res["status"] == "error"


def test_media_ocr_video_requires_explicit_video_id():
    assert inspect.signature(media_ocr_video).parameters["video_id"].default is inspect.Parameter.empty
    result = media_ocr_video(video_id="")
    assert result["error_code"] == "video_id_required"


def test_media_ocr_video_at_timestamp_uses_only_requested_frame(tmp_path, monkeypatch):
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"source")
    calls = []

    def fake_ffmpeg(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"frame" * 500)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(ocr_tool, "resolve_video_file", lambda video_id: video)
    monkeypatch.setattr(ocr_tool, "KEYFRAMES_DIR", tmp_path / "keyframes")
    monkeypatch.setattr(ocr_tool, "probe_video", lambda path: {"duration": 1000.0})
    monkeypatch.setattr(ocr_tool.subprocess, "run", fake_ffmpeg)
    monkeypatch.setattr(ocr_tool, "extract_ocr_from_frame", lambda path, min_confidence: [
        {"text": "TEXTO EN PANTALLA", "region": "lower_third", "confidence": 0.93, "bbox": [1, 2, 3, 4]}
    ])
    monkeypatch.setattr(ocr_tool, "analyze_video_ocr", lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("A time-specific request must not scan the whole video")))

    result = media_ocr_video(video_id="TV05", timestamp=270.0)
    assert result["status"] == "success"
    assert result["video_id"] == "TV05"
    assert result["timestamp"] == 270.0
    assert result["all_text"] == "TEXTO EN PANTALLA"
    assert result["total_texts_detected"] == 1
    assert "270.000" in calls[0]
    assert result["extracted_texts"][0]["keyframe"].startswith("keyframes/TV05/ocr_at_")
    assert media_ocr_video(video_id="TV05", timestamp=270.0)["all_text"] == "TEXTO EN PANTALLA"
    assert len(calls) == 1


def test_media_ocr_video_rejects_time_outside_video(tmp_path, monkeypatch):
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(ocr_tool, "resolve_video_file", lambda video_id: video)
    monkeypatch.setattr(ocr_tool, "probe_video", lambda path: {"duration": 100.0})
    result = media_ocr_video(video_id="TV05", timestamp=270.0)
    assert result["status"] == "error"
    assert "timestamp" in result["message"]
