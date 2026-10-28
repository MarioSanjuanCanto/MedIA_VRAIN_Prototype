import json
from unittest.mock import MagicMock
from PIL import Image

from media_core.services.vlm import encode_image_base64, describe_keyframe_vlm
from media_core.tools.vlm_tool import media_describe_scene


def test_encode_image_base64(tmp_path):
    img_path = tmp_path / "test.jpg"
    img = Image.new("RGB", (100, 100), color="red")
    img.save(img_path)

    b64 = encode_image_base64(img_path)
    assert isinstance(b64, str)
    assert len(b64) > 0


def test_describe_keyframe_vlm_with_mock(monkeypatch, tmp_path):
    img_path = tmp_path / "frame.jpg"
    img = Image.new("RGB", (100, 100), color="green")
    img.save(img_path)

    mock_llm_response = json.dumps({
        "description": "Una reportera sostiene un micrófono frente a una estación de servicio.",
        "setting": "Exterior en gasolinera",
        "subjects": ["Reportera", "Micrófono", "Vehículos"],
        "actions": ["Hablando a cámara"],
        "visible_elements": ["Logo Repsol", "Surtidores"],
        "visible_text": ["DIRECTO", "14:00"],
    })

    from media_core.services import vlm
    monkeypatch.setattr(vlm, "call_openrouter", lambda **kwargs: mock_llm_response)

    res = describe_keyframe_vlm(
        image_path=img_path,
        video_id="TV05",
        timestamp=45.0,
    )

    assert res["status"] == "success"
    assert "reportera" in res["description"]
    assert res["setting"] == "Exterior en gasolinera"
    assert "Reportera" in res["subjects"]
    assert "Logo Repsol" in res["visible_elements"]


def test_describe_keyframe_vlm_missing_file(tmp_path):
    res = describe_keyframe_vlm(
        image_path=tmp_path / "non_existent.jpg",
        video_id="TV01",
    )
    assert res["status"] == "error"


def test_media_describe_scene_missing_video():
    res = media_describe_scene(video_id="NON_EXISTENT_VIDEO_XYZ")
    assert res["status"] == "error"


def test_describe_keyframe_vlm_multiframe_and_person(monkeypatch, tmp_path):
    f1 = tmp_path / "f1.jpg"
    f2 = tmp_path / "f2.jpg"
    f3 = tmp_path / "f3.jpg"
    for p in (f1, f2, f3):
        Image.new("RGB", (50, 50), color="blue").save(p)

    captured_call = {}

    def mock_call_openrouter(**kwargs):
        captured_call.update(kwargs)
        return json.dumps({
            "description": "Salvador Illa gesticula en el atril y responde a las preguntas.",
            "person_detected": True,
            "person_role_identified": "Salvador Illa (Ministro)",
            "action_observed": "Habla en el atril y gesticula con la mano derecha",
            "setting": "Sala de prensa",
            "subjects": ["Salvador Illa"],
            "actions": ["Hablar", "Gesticular"],
            "visible_elements": ["Atril", "Micrófonos"],
            "visible_text": ["RUEDA DE PRENSA"],
        })

    from media_core.services import vlm
    monkeypatch.setattr(vlm, "call_openrouter", mock_call_openrouter)

    res = describe_keyframe_vlm(
        image_paths=[f1, f2, f3],
        video_id="TV01",
        timestamp=30.0,
        target_person="Salvador Illa",
        action_query="¿Qué está haciendo con las manos?",
        context_metadata={
            "ocr_texts": ["SALVADOR ILLA", "MINISTERIO"],
            "speaker": "Salvador Illa",
            "transcript_excerpt": "estamos trabajando intensamente",
        },
    )

    assert res["status"] == "success"
    assert res["person_detected"] is True
    assert "Salvador Illa" in res["person_role_identified"]
    assert "gesticula" in res["action_observed"]
    assert len(res["image_files"]) == 3

    # Verify OpenRouter messages contain all 3 frames and prompt context
    user_msgs = captured_call["messages"][1]["content"]
    image_items = [item for item in user_msgs if item.get("type") == "image_url"]
    assert len(image_items) == 3
    query_text = user_msgs[0]["text"]
    assert "Salvador Illa" in query_text
    assert "¿Qué está haciendo con las manos?" in query_text
    assert "SALVADOR ILLA" in query_text
    assert "estamos trabajando intensamente" in query_text


def test_media_describe_scene_with_multimodal_context(monkeypatch, tmp_path):
    # Setup mock video file and keyframes
    mock_video = tmp_path / "TV01.mp4"
    mock_video.touch()

    keyframes_dir = tmp_path / "keyframes" / "TV01"
    keyframes_dir.mkdir(parents=True)
    frame1 = keyframes_dir / "frame_0001.jpg"
    frame2 = keyframes_dir / "frame_0002.jpg"
    frame3 = keyframes_dir / "frame_0003.jpg"
    for f in (frame1, frame2, frame3):
        Image.new("RGB", (60, 60), color="yellow").save(f)

    # Patch paths
    import media_core.tools.vlm_tool as vlm_tool_mod
    monkeypatch.setattr(vlm_tool_mod, "resolve_video_file", lambda vid: mock_video)
    monkeypatch.setattr(vlm_tool_mod, "KEYFRAMES_DIR", tmp_path / "keyframes")

    # Patch visual manifest
    manifest = {
        "frames": [
            {"filename": "frame_0001.jpg", "timestamp": 8.0, "scene_id": 1},
            {"filename": "frame_0002.jpg", "timestamp": 10.0, "scene_id": 1},
            {"filename": "frame_0003.jpg", "timestamp": 12.0, "scene_id": 1},
        ],
        "scenes": [
            {"id": 1, "start_seconds": 8.0, "end_seconds": 12.0, "keyframe": str(frame2)}
        ]
    }
    monkeypatch.setattr(vlm_tool_mod, "media_analyze_video", lambda **kw: manifest)

    # Patch OCR cache
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    ocr_file = ocr_dir / "TV01_ocr.json"
    ocr_file.write_text(json.dumps({
        "extracted_texts": [
            {"timestamp": 9.5, "text": "SALVADOR ILLA | MINISTRO"}
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(vlm_tool_mod, "OCR_CACHE_DIR", ocr_dir)

    # Patch test vector
    monkeypatch.setattr(vlm_tool_mod, "load_test_vector", lambda vid: {
        "segments": [
            {"start_seconds": 7.0, "end_seconds": 13.0, "speaker": "Salvador Illa", "text": "buenos días a todos"}
        ]
    })

    # Mock VLM service
    monkeypatch.setattr(
        vlm_tool_mod,
        "describe_keyframe_vlm",
        lambda **kwargs: {
            "status": "success",
            "description": "Illa hablando.",
            "person_detected": True,
            "person_role_identified": kwargs.get("target_person"),
            "action_observed": "Hablando a los periodistas.",
            "target_paths_count": len(kwargs.get("image_paths", [])),
            "received_context": kwargs.get("context_metadata"),
        }
    )

    res = media_describe_scene(
        video_id="TV01",
        timestamp=10.0,
        person="Salvador Illa",
        action_query="¿qué está diciendo o haciendo?",
        multi_frame=True,
    )

    assert res["status"] == "success"
    assert res["target_paths_count"] == 3
    assert len(res["keyframe_paths"]) == 3
    assert "multimodal_context" in res
    assert "SALVADOR ILLA | MINISTRO" in res["multimodal_context"]["ocr_texts"]
    assert res["multimodal_context"]["speaker"] == "Salvador Illa"
