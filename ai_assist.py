"""AI 보조 — 캡차·폼 분석 (OpenAI 호환 API, 선택 사항).

Cursor IDE 자체 API는 공개되어 있지 않습니다.
권장 워크플로:
  1. URL 검사 탭에서 「Cursor용 보고서 저장」
  2. 생성된 unsupported_urls_report.txt 를 Cursor 채팅에 붙여넣기
  3. 코드 수정 후 앱 업데이트

앱 내 자동화(OpenAI API 키 설정 시):
  - 이미지 캡차(reCAPTCHA 제외) Vision OCR
  - 댓글 폼 HTML 분석 → 셀렉터 제안
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

from app_paths import data_file

_DEFAULT_API_BASE = "https://api.openai.com/v1"


def _load_ai_config() -> dict[str, str]:
    path = data_file("app_state.json")
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            ai = raw.get("ai") or {}
            if isinstance(ai, dict):
                return {
                    "api_key": (ai.get("openai_api_key") or "").strip(),
                    "api_base": (ai.get("openai_api_base") or _DEFAULT_API_BASE).strip().rstrip("/"),
                    "model": (ai.get("model") or "gpt-4o-mini").strip(),
                }
        except Exception:
            pass
    return {
        "api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "api_base": os.environ.get("OPENAI_API_BASE", _DEFAULT_API_BASE).strip().rstrip("/"),
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip(),
    }


def is_configured() -> bool:
    return bool(_load_ai_config()["api_key"])


def capabilities_summary() -> str:
    if is_configured():
        return (
            "OpenAI API 키 설정됨 — 숫자/텍스트 이미지 캡차 Vision OCR, "
            "댓글 폼 셀렉터 제안 사용 가능 (reCAPTCHA/hCaptcha는 수동 필요)"
        )
    return (
        "API 키 미설정 — 미지원 URL 보고서를 Cursor 채팅에 붙여넣어 코드 확장 요청. "
        "또는 OpenAI API 키를 입력하면 앱 내 캡차·폼 분석 가능"
    )


def _chat_completion(
    messages: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    model: str | None = None,
    max_tokens: int = 500,
) -> tuple[str, str]:
    cfg = _load_ai_config()
    key = (api_key or cfg["api_key"]).strip()
    if not key:
        return "", "API 키가 없습니다 — URL 검사 탭에서 OpenAI API 키를 입력하거나 OPENAI_API_KEY 환경변수를 설정하세요."

    base = (api_base or cfg["api_base"]).rstrip("/")
    mdl = model or cfg["model"]
    payload = json.dumps({
        "model": mdl,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return (content or "").strip(), ""
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = str(exc)
        return "", f"API 오류 {exc.code}: {detail}"
    except Exception as exc:
        return "", str(exc)


def solve_image_captcha_with_vision(
    image_bytes: bytes,
    *,
    hint: str = "숫자만 있는 캡차 이미지입니다. 숫자만 출력하세요.",
) -> tuple[str, str]:
    """reCAPTCHA/hCaptcha가 아닌 단순 이미지 캡차용."""
    if not image_bytes:
        return "", "이미지 없음"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": hint},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }
    ]
    text, err = _chat_completion(messages, max_tokens=32)
    if err:
        return "", err
    digits = "".join(c for c in text if c.isdigit())
    if digits:
        return digits, f"Vision OCR: {digits}"
    cleaned = text.strip().split()[0] if text.strip() else ""
    return cleaned, f"Vision OCR: {cleaned or text[:40]}"


def suggest_comment_form_selectors(form_summary: str) -> tuple[str, str]:
    """페이지 스냅샷 텍스트를 넣으면 Playwright 셀렉터 제안."""
    if not form_summary.strip():
        return "", "폼 정보 없음"
    prompt = (
        "다음은 웹 페이지 댓글/게시 폼 구조입니다. "
        "Playwright Python 자동화용 CSS 셀렉터를 JSON으로 제안하세요. "
        '키: name, password, email, homepage, content, submit, captcha, captcha_image. '
        "값이 없으면 null. 설명 없이 JSON만.\n\n"
        f"{form_summary[:4000]}"
    )
    return _chat_completion([{"role": "user", "content": prompt}], max_tokens=400)


def login_spam_mitigation_tips(snapshot_text: str) -> tuple[str, str]:
    """로그인·스팸 상황별 대응 팁 (API 또는 오프라인)."""
    if not is_configured():
        tips = [
            "【Cursor 워크플로】 unsupported_urls_report.txt → Cursor 채팅 → writer 모듈 추가 요청",
            "【로그인】 회원 전용 게시판은 계정·쿠키 저장 기능이 필요 (현재 비회원 위주)",
            "【숫자 캡차】 ddddocr 로컬 OCR 이미 적용 (그누보드 KCaptcha)",
            "【이미지 캡차】 OpenAI API 키 설정 시 Vision OCR 시도 가능",
            "【reCAPTCHA/hCaptcha】 자동 우회 불가 — 2captcha 등 유료 서비스 또는 수동",
            "【스팸필터】 Akismet/Wordfence — 댓글에 URL·키워드 과다 반복 피하기, 자연스러운 문장",
            "【Akismet 승인대기】 moderation 감지됨 — 사이트 관리자 승인 전까지는 정상 동작",
        ]
        return "\n".join(f"• {t}" for t in tips), ""

    prompt = (
        "백링크 댓글 자동화 도구 사용자입니다. 아래 페이지 분석 결과를 보고 "
        "로그인·스팸필터·캡차 대응 방법을 한국어로 5줄 이내 bullet로 제안하세요.\n\n"
        f"{snapshot_text[:3500]}"
    )
    return _chat_completion([{"role": "user", "content": prompt}], max_tokens=350)
