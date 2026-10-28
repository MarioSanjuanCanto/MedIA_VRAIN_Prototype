"""Image cropping, VLM spatial grounding, and visual side-by-side composition service."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from media_core.services.openrouter import call_openrouter
from media_core.services.vlm import DEFAULT_VISION_MODEL, encode_image_base64

logger = logging.getLogger(__name__)


def detect_subject_bounding_box(
    image_path: Path,
    target_description: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Use multimodal VLM spatial grounding to locate a subject's bounding box.

    Returns:
        dict with found (bool), label (str), and box_2d ([ymin, xmin, ymax, xmax] in 0-1000 scale).
    """
    if not image_path.exists():
        return {
            "found": False,
            "label": target_description,
            "box_2d": None,
            "error": f"Image file not found: {image_path}",
        }

    target_model = model or DEFAULT_VISION_MODEL
    b64_data = encode_image_base64(image_path)

    system_prompt = (
        "You are an expert visual grounding and object localization assistant. "
        "Your task is to locate the target person, object, or visual region described by the user. "
        "Detect the bounding box tightly framing the subject (e.g. head and shoulders for a person if framed as such, "
        "or full body if prominent). "
        "Return exclusively a JSON object with this exact schema:\n"
        "{\n"
        '  "found": true,\n'
        '  "label": "brief description of the located subject",\n'
        '  "box_2d": [ymin, xmin, ymax, xmax]\n'
        "}\n"
        "Where ymin, xmin, ymax, xmax are normalized integer coordinates in the range 0 to 1000 "
        "(0 is top/left, 1000 is bottom/right). If the subject cannot be found, return {\"found\": false, \"box_2d\": null}."
    )

    user_content = [
        {"type": "text", "text": f"Locate the subject in this frame: '{target_description}'."},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
        },
    ]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        raw_response = call_openrouter(
            messages=messages,
            model=target_model,
            json_mode=True,
            temperature=0.1,
            max_tokens=250,
        )
        if not raw_response:
            return {"found": False, "label": target_description, "box_2d": None}

        parsed = json.loads(raw_response)
        box = parsed.get("box_2d")
        if box and isinstance(box, list) and len(box) == 4:
            return {
                "found": bool(parsed.get("found", True)),
                "label": parsed.get("label", target_description),
                "box_2d": [int(coord) for coord in box],
            }
        return {"found": False, "label": target_description, "box_2d": None}
    except Exception as exc:
        logger.warning("VLM bounding box detection failed: %s", exc)
        return {"found": False, "label": target_description, "box_2d": None, "error": str(exc)}


def crop_image_subject(
    image_path: Path,
    box_2d: list[int | float],
    output_path: Path,
    padding_pct: float = 0.10,
) -> dict[str, Any]:
    """Crop a bounding box region from an image using PIL with contextual padding.

    Coordinates in box_2d can be:
      - 0 to 1000 normalized ints [ymin, xmin, ymax, xmax]
      - 0.0 to 1.0 normalized floats [ymin, xmin, ymax, xmax]
      - or pixel coordinates [ymin, xmin, ymax, xmax]
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Source image does not exist: {image_path}")

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size

        ymin, xmin, ymax, xmax = box_2d

        # Normalize to pixels
        if max(ymin, xmin, ymax, xmax) <= 1.0:
            ymin_px = ymin * h
            xmin_px = xmin * w
            ymax_px = ymax * h
            xmax_px = xmax * w
        elif max(ymin, xmin, ymax, xmax) <= 1000.0:
            ymin_px = ymin * h / 1000.0
            xmin_px = xmin * w / 1000.0
            ymax_px = ymax * h / 1000.0
            xmax_px = xmax * w / 1000.0
        else:
            ymin_px = ymin
            xmin_px = xmin
            ymax_px = ymax
            xmax_px = xmax

        bw = xmax_px - xmin_px
        bh = ymax_px - ymin_px

        pad_x = bw * padding_pct
        pad_y = bh * padding_pct

        left = max(0, int(round(xmin_px - pad_x)))
        top = max(0, int(round(ymin_px - pad_y)))
        right = min(w, int(round(xmax_px + pad_x)))
        bottom = min(h, int(round(ymax_px + pad_y)))

        if right <= left or bottom <= top:
            # Fallback to whole image if invalid box
            left, top, right, bottom = 0, 0, w, h

        cropped = img.crop((left, top, right, bottom))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(output_path, "JPEG", quality=92)

        return {
            "crop_path": str(output_path),
            "width": cropped.width,
            "height": cropped.height,
            "box_pixels": [left, top, right, bottom],
            "original_size": [w, h],
        }


def create_side_by_side(
    reference_source: bytes | Path,
    crop_path: Path,
    output_path: Path,
    target_height: int = 400,
) -> Path:
    """Create a side-by-side comparison image: reference portrait on left, frame crop on right."""
    if isinstance(reference_source, (bytes, bytearray)):
        ref_img = Image.open(io.BytesIO(reference_source)).convert("RGB")
    else:
        ref_img = Image.open(reference_source).convert("RGB")

    with Image.open(crop_path) as crop_img:
        crop_img = crop_img.convert("RGB")

        # Resize both to same height
        crop_w = max(1, int(crop_img.width * (target_height / crop_img.height)))
        crop_resized = crop_img.resize((crop_w, target_height), Image.Resampling.LANCZOS)

        ref_w = max(1, int(ref_img.width * (target_height / ref_img.height)))
        ref_resized = ref_img.resize((ref_w, target_height), Image.Resampling.LANCZOS)

        separator_width = 4
        comp_width = ref_w + crop_w + separator_width
        composite = Image.new("RGB", (comp_width, target_height), (28, 28, 30))

        composite.paste(ref_resized, (0, 0))
        composite.paste(crop_resized, (ref_w + separator_width, 0))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        composite.save(output_path, "JPEG", quality=92)
        return output_path
