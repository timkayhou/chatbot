"""Offline image inspection for outbound attachments."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


class MediaUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaAnalysis:
    extracted_text: tuple[str, ...]
    qr_payloads: tuple[str, ...]
    width: int
    height: int


def _modules():
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise MediaUnavailable("media extras are not installed") from error
    return cv2, np


@lru_cache(maxsize=1)
def _ocr_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as error:
        raise MediaUnavailable("OCR extras are not installed") from error
    return RapidOCR()


def analyze_image(content: bytes) -> MediaAnalysis:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("image must be between 1 byte and 10 MB")
    cv2, np = _modules()
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("unsupported or invalid image")
    height, width = image.shape[:2]
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("image dimensions are too large")

    detector = cv2.QRCodeDetector()
    qr_values: list[str] = []
    try:
        found, decoded, _, _ = detector.detectAndDecodeMulti(image)
        if found:
            qr_values.extend(value.strip() for value in decoded if value.strip())
    except cv2.error:
        pass
    if not qr_values:
        value, _, _ = detector.detectAndDecode(image)
        if value.strip():
            qr_values.append(value.strip())

    result = _ocr_engine()(image)
    text_values = tuple(value.strip() for value in (result.txts or ()) if value.strip())
    return MediaAnalysis(text_values, tuple(dict.fromkeys(qr_values)), width, height)

