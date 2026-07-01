"""그누보드 숫자 캡차 로컬 OCR (전처리 + 다중 시도)."""

from __future__ import annotations

import re
from collections import Counter

import cv2
import numpy as np

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        import ddddocr

        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _decode_image(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("캡차 이미지를 읽을 수 없습니다.")
    return img


def _encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("캡차 이미지 인코딩 실패")
    return buf.tobytes()


def _preprocess_variants(image_bytes: bytes) -> list[bytes]:
    """KCaptcha 숫자 인식률 향상용 전처리 파이프라인."""
    img = _decode_image(image_bytes)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 배경 노이즈 제거
    gray = cv2.medianBlur(gray, 3)

    # 3배 확대 — 작은 숫자 캡차에 효과적
    big = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    variants: list[np.ndarray] = [big]

    # Otsu 이진화
    _, otsu = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(otsu)
    variants.append(cv2.bitwise_not(otsu))

    # 적응형 이진화
    adapt = cv2.adaptiveThreshold(
        big, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8
    )
    variants.append(adapt)
    variants.append(cv2.bitwise_not(adapt))

    # 대비 강화 (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(big)
    _, enhanced_bin = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(enhanced_bin)

    # 모폴로지로 끊긴 숫자 연결
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    morph = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel)
    variants.append(morph)

    # 중복 제거
    seen: set[bytes] = set()
    out: list[bytes] = []
    for v in variants:
        data = _encode_png(v)
        if data not in seen:
            seen.add(data)
            out.append(data)
    return out


def _ocr_digits(image_bytes: bytes) -> str:
    text = _get_ocr().classification(image_bytes)
    return re.sub(r"\D", "", text or "")


def is_valid_gnuboard_captcha(code: str) -> bool:
    """그누보드 KCaptcha는 보통 6자리 숫자."""
    return bool(re.fullmatch(r"\d{6}", code))


def solve_numeric_captcha(image_bytes: bytes) -> tuple[str, str]:
    """
    캡차 이미지 OCR.
    Returns: (인식된 숫자, 상세 로그)
    """
    candidates: list[str] = []
    logs: list[str] = []

    # 원본
    raw = _ocr_digits(image_bytes)
    if raw:
        candidates.append(raw)
        logs.append(f"원본={raw}")

    # 전처리 변형별 시도
    for i, variant in enumerate(_preprocess_variants(image_bytes), 1):
        try:
            digits = _ocr_digits(variant)
            if digits:
                candidates.append(digits)
                logs.append(f"전처리{i}={digits}")
        except Exception:
            continue

    if not candidates:
        return "", "인식 결과 없음"

    # 6자리 우선 — 다수결
    six = [c for c in candidates if len(c) == 6]
    if six:
        best, count = Counter(six).most_common(1)[0]
        detail = f"6자리 다수결 {best} ({count}/{len(six)}표) | " + ", ".join(logs)
        return best, detail

    # 5~7자리 허용 (OCR 오차)
    near = [c for c in candidates if 5 <= len(c) <= 7]
    if near:
        best, count = Counter(near).most_common(1)[0]
        # 6자리로 보정 시도
        if len(best) == 7:
            trimmed = best[:6]
            if six or any(len(c) == 6 for c in candidates):
                pass
            best = trimmed
        elif len(best) == 5:
            padded_candidates = [c for c in candidates if c.startswith(best) or best in c]
            if padded_candidates:
                best = max(padded_candidates, key=len)[:6]
        detail = f"근사값 {best} | " + ", ".join(logs)
        return best, detail

    # 최빈값
    best, count = Counter(candidates).most_common(1)[0]
    detail = f"최빈값 {best} | " + ", ".join(logs)
    return best, detail
