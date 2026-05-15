"""OCR 서비스 — RapidOCR 기반 텍스트 검출 및 추출."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            _ocr_engine = RapidOCR()
            logger.info("RapidOCR engine initialized")
        except ImportError:
            logger.warning("rapidocr_onnxruntime not installed. Run: pip install rapidocr-onnxruntime")
    return _ocr_engine


def _bytes_to_array(image_bytes: bytes):
    import cv2
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _fuzzy_score(candidate: str, target: str) -> float:
    """대소문자 무시 부분 일치 점수 (0.0~1.0)."""
    try:
        from rapidfuzz.fuzz import partial_ratio  # type: ignore
        return partial_ratio(target.lower(), candidate.lower()) / 100.0
    except ImportError:
        return 1.0 if target.lower() in candidate.lower() else 0.0


class OcrItem:
    def __init__(self, text: str, box: list, score: float):
        self.text = text
        self.box = box  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        self.score = score

    @property
    def center(self) -> Tuple[int, int]:
        xs = [p[0] for p in self.box]
        ys = [p[1] for p in self.box]
        return (int(sum(xs) / 4), int(sum(ys) / 4))


def run_ocr(image_bytes: bytes) -> List[OcrItem]:
    """이미지에서 OCR 실행."""
    engine = _get_engine()
    if engine is None:
        return []
    img = _bytes_to_array(image_bytes)
    if img is None:
        logger.warning("OCR: 이미지 디코딩 실패")
        return []
    try:
        result, _ = engine(img)
    except Exception as e:
        logger.error("OCR 엔진 오류: %s", e)
        return []
    if not result:
        return []
    return [OcrItem(text=item[1][0], box=item[0], score=item[1][1]) for item in result]


def has_text(
    image_bytes: bytes, target: str, threshold: float = 0.8
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    """이미지에서 target 텍스트 존재 여부 판단. (found, center_xy)"""
    items = run_ocr(image_bytes)
    best_score = 0.0
    best_center = None
    for item in items:
        score = _fuzzy_score(item.text, target)
        if score > best_score:
            best_score = score
            best_center = item.center
    if best_score >= threshold:
        return True, best_center
    return False, None


def find_text_center(
    image_bytes: bytes, target: str, threshold: float = 0.8
) -> Optional[Tuple[int, int]]:
    """텍스트 중심 좌표 반환. 없으면 None."""
    found, center = has_text(image_bytes, target, threshold)
    return center if found else None


def extract_region_text(
    image_bytes: bytes, x: int, y: int, width: int, height: int
) -> str:
    """지정 영역 크롭 후 OCR로 텍스트 추출."""
    import cv2
    img = _bytes_to_array(image_bytes)
    if img is None:
        return ""
    h, w = img.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(w, x + width), min(h, y + height)
    if x2 <= x1 or y2 <= y1:
        return ""
    crop = img[y1:y2, x1:x2]
    _, buf = cv2.imencode(".png", crop)
    if buf is None:
        return ""
    items = run_ocr(buf.tobytes())
    return " ".join(item.text for item in items).strip()
