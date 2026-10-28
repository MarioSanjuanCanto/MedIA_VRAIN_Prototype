"""Unit tests for media_crop_subject and media_identify_person_web tools."""

from pathlib import Path
from unittest.mock import patch
from PIL import Image

from media_core.services.image_processing import crop_image_subject, create_side_by_side
from media_core.tools.image_crop import media_crop_subject
from media_core.tools.identify_person import media_identify_person_web


def test_crop_image_subject_pil(tmp_path):
    # 1. Create a dummy test image (400x300)
    source_img = tmp_path / "test_frame.jpg"
    img = Image.new("RGB", (400, 300), color=(100, 150, 200))
    img.save(source_img)

    # 2. Crop using normalized 0-1000 coordinates
    out_crop = tmp_path / "crop.jpg"
    res = crop_image_subject(
        image_path=source_img,
        box_2d=[100, 200, 600, 700],  # ymin=100 (30px), xmin=200 (80px), ymax=600 (180px), xmax=700 (280px)
        output_path=out_crop,
        padding_pct=0.0,
    )

    assert out_crop.is_file()
    assert res["width"] == 200
    assert res["height"] == 150


def test_create_side_by_side(tmp_path):
    ref_path = tmp_path / "ref.jpg"
    crop_path = tmp_path / "crop.jpg"
    out_path = tmp_path / "side_by_side.jpg"

    Image.new("RGB", (200, 300), color=(255, 0, 0)).save(ref_path)
    Image.new("RGB", (100, 200), color=(0, 255, 0)).save(crop_path)

    create_side_by_side(
        reference_source=ref_path,
        crop_path=crop_path,
        output_path=out_path,
        target_height=400,
    )

    assert out_path.is_file()
    with Image.open(out_path) as comp:
        assert comp.height == 400
        assert comp.width > 300


def test_media_crop_subject_with_bbox(tmp_path, monkeypatch):
    source_img = tmp_path / "frame_0001.jpg"
    Image.new("RGB", (640, 480), color=(50, 50, 50)).save(source_img)

    # Call media_crop_subject directly passing keyframe_path and bbox
    res = media_crop_subject(
        video_id="TV04",
        timestamp=10.0,
        target_description="persona de prueba",
        bbox=[100, 100, 500, 500],
        keyframe_path=str(source_img),
    )

    assert res["status"] == "success"
    assert "crop_url" in res
    assert "media_line" in res
    assert "markdown_image" in res
    assert Path(res["absolute_crop_path"]).is_file()


def test_media_identify_person_web_mocked(tmp_path, monkeypatch):
    crop_img = tmp_path / "crop_test.jpg"
    Image.new("RGB", (200, 200), color=(80, 80, 80)).save(crop_img)

    mock_res = {
        "status": "success",
        "identified_name": "Najat Driouech Ben Moussa",
        "confidence": "alta probabilidad",
        "role_and_party": "Diputada de ERC por Barcelona",
        "context_evidence": ["Es la única diputada con hiyab en el Parlament."],
        "biography": "Nacida en 1981, filóloga y trabajadora social.",
        "crop_path": str(crop_img),
        "crop_url": "/api/keyframes/TV04/crop_test.jpg",
        "crop_markdown_image": "![Recorte](/api/keyframes/TV04/crop_test.jpg)",
        "reference_photo_url": "https://example.com/ref.jpg",
        "comparison_image_path": None,
        "comparison_image_url": None,
        "comparison_markdown_image": None,
        "formatted_report": "Identificada con alta probabilidad: Najat Driouech",
    }

    with patch("media_core.tools.identify_person.identify_person_via_web", return_value=mock_res):
        res = media_identify_person_web(
            video_id="TV04",
            timestamp=687.0,
            person_description="mujer con hiyab",
            crop_path=str(crop_img),
        )

    assert res["status"] == "success"
    assert res["identified_name"] == "Najat Driouech Ben Moussa"
    assert "Diputada de ERC" in res["role_and_party"]
    assert "crop_url" in res
