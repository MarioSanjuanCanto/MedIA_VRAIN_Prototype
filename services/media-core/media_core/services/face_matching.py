"""Compare detected faces with attributed reference photographs using OpenCV SFace."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

import cv2
import httpx
import numpy as np

from media_core.config import FACE_MODELS_DIR
from media_core.services.face_detector import ensure_yunet_model

logger = logging.getLogger(__name__)
SFACE_MODEL_PATH = FACE_MODELS_DIR / "face_recognition_sface_2021dec.onnx"
SFACE_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
_model_lock = Lock()


def ensure_sface_model() -> Path | None:
    """Download the Apache-2.0 OpenCV Zoo weights once, when recognition is requested."""
    if SFACE_MODEL_PATH.exists() and SFACE_MODEL_PATH.stat().st_size > 30_000_000:
        return SFACE_MODEL_PATH
    with _model_lock:
        if SFACE_MODEL_PATH.exists() and SFACE_MODEL_PATH.stat().st_size > 30_000_000:
            return SFACE_MODEL_PATH
        try:
            with httpx.stream("GET", SFACE_URL, follow_redirects=True, timeout=90.0) as response:
                response.raise_for_status()
                temporary = SFACE_MODEL_PATH.with_suffix(".download")
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
                if temporary.stat().st_size <= 30_000_000:
                    temporary.unlink(missing_ok=True)
                    return None
                temporary.replace(SFACE_MODEL_PATH)
            return SFACE_MODEL_PATH
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("SFace unavailable: %s", exc)
            return None


def face_feature(image_path: Path) -> np.ndarray | None:
    """Align the largest detected face and return a normalized SFace vector."""
    detector_path = ensure_yunet_model()
    recognizer_path = ensure_sface_model()
    if not detector_path or not recognizer_path:
        return None
    image = cv2.imread(str(image_path))
    if image is None:
        return None
    faces = None
    for scale in (1, 2, 3):
        candidate = image if scale == 1 else cv2.resize(
            image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
        )
        height, width = candidate.shape[:2]
        # YuNet's ONNX graph in older OpenCV builds fails on some odd dimensions.
        padded_width = (width + 31) // 32 * 32
        padded_height = (height + 31) // 32 * 32
        candidate = cv2.copyMakeBorder(
            candidate, 0, padded_height - height, 0, padded_width - width,
            cv2.BORDER_CONSTANT, value=(0, 0, 0),
        )
        detector = cv2.FaceDetectorYN.create(
            str(detector_path), "", (padded_width, padded_height), score_threshold=0.55
        )
        try:
            _, faces = detector.detect(candidate)
        except cv2.error as exc:
            logger.debug("Face detector could not process %s at %sx: %s", image_path, scale, exc)
            continue
        if faces is not None and len(faces):
            image = candidate
            break
    if faces is None or len(faces) == 0:
        return None
    face = max(faces, key=lambda row: row[2] * row[3])
    if min(face[2], face[3]) < 30:
        return None
    recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
    try:
        aligned = recognizer.alignCrop(image, face)
        vector = recognizer.feature(aligned).reshape(-1).astype(np.float32)
    except cv2.error as exc:
        logger.debug("SFace could not process %s: %s", image_path, exc)
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else None


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))
