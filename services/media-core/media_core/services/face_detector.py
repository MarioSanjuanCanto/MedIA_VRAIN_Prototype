"""Face detection helpers using OpenCV YuNet."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

import httpx

from media_core.config import FACE_MODELS_DIR

logger = logging.getLogger(__name__)

YUNET_MODEL_PATH = FACE_MODELS_DIR / "face_detection_yunet_2023mar.onnx"
YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_model_lock = Lock()


def ensure_yunet_model() -> Path | None:
    """Download the OpenCV Zoo YuNet face detector model weights if needed."""
    if YUNET_MODEL_PATH.exists() and YUNET_MODEL_PATH.stat().st_size > 200_000:
        return YUNET_MODEL_PATH
    with _model_lock:
        if YUNET_MODEL_PATH.exists() and YUNET_MODEL_PATH.stat().st_size > 200_000:
            return YUNET_MODEL_PATH
        try:
            with httpx.stream("GET", YUNET_URL, follow_redirects=True, timeout=60.0) as response:
                response.raise_for_status()
                temporary = YUNET_MODEL_PATH.with_suffix(".download")
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
                if temporary.stat().st_size <= 200_000:
                    temporary.unlink(missing_ok=True)
                    return None
                temporary.replace(YUNET_MODEL_PATH)
            return YUNET_MODEL_PATH
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("YuNet unavailable: %s", exc)
            return None
