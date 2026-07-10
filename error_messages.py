"""게시 실패·오류 메시지 한글화."""

from __future__ import annotations

import re

# (영문/기술 패턴, 한글 설명) — 순서 중요 (긴 패턴 우선)
_RULES: list[tuple[str, str]] = [
    (
        r"BrowserType\.launch: Executable doesn't exist",
        "내장 Chrome 없음 — exe 옆 ms-playwright\\chromium-*\\chrome.exe 확인",
    ),
    (
        r"Executable doesn't exist",
        "내장 Chrome 없음 — ms-playwright 폴더 누락·백신 차단. zip 전체 재설치 또는 최신 버전 업데이트",
    ),
    (
        r"It looks like you are using Playwright Sync API inside the asyncio loop",
        "브라우저 엔진 충돌 (수집·배포 동시 실행 또는 환경 오류) — 수집 중지 후 재시도",
    ),
    (
        r"'tuple' object has no attribute 'site_url'",
        "프로그램 내부 오류 (링크 데이터 형식) — 최신 버전으로 재시도",
    ),
    (r"Target page, context or browser has been closed", "브라우저가 닫혔습니다 (취소·오류·창 닫기)"),
    (r"Page\.goto: Timeout", "페이지 로딩 시간 초과 (사이트 느림·차단)"),
    (r"Timeout \d+ms exceeded", "작업 시간 초과 (로딩 지연·사이트 응답 없음)"),
    (r"net::ERR_", "네트워크 오류 (연결 실패·SSL·차단)"),
    (r"SSL:", "SSL 보안 연결 오류 (구형 사이트)"),
    (r"페이지 접근 실패 — 403", "사이트 방화벽(WAF) 차단 — Chrome UA 차단 시 Firefox UA로 자동 재시도"),
    (r"403 - Forbidden|403 Forbidden", "사이트 방화벽(WAF) 차단 (403)"),
    (r"Page not found|404", "페이지 없음 (404) — URL 삭제·변경됨"),
    (r"작업이 취소되었습니다", "작업 취소됨 (취소 버튼 또는 대기 중 중단)"),
    (r"Movable Type 댓글 폼을 찾을 수 없습니다", "Movable Type 댓글 폼 없음 (로딩·차단·회원 전용)"),
    (r"워드프레스 댓글 폼을 찾을 수 없습니다", "워드프레스 댓글 폼 없음 (로딩·차단·회원 전용)"),
    (r"댓글 폼을 찾을 수 없습니다", "댓글 폼 없음 (비활성·회원 전용·구조 다름)"),
    (r"댓글 입력란을 찾을 수 없습니다", "댓글 입력란 없음"),
    (r"댓글 등록 버튼", "댓글 등록 버튼 없음"),
    (r"댓글이 페이지에 표시되지 않습니다", "댓글 미표시 (스팸필터·승인대기·제출 실패)"),
    (r"댓글 제출이 거부", "댓글 제출 거부 (스팸필터·필수값)"),
    (r"글쓰기 폼을 찾을 수 없습니다", "글쓰기 폼 없음"),
    (r"캡차", "자동등록방지(캡차) 인식 실패"),
    (r"자동등록방지", "자동등록방지(캡차) 처리 실패"),
    (r"사이트 방화벽", "사이트 방화벽(WAF) 차단"),
    (r"페이지를 찾을 수 없습니다", "페이지 없음 (404)"),
    (r"페이지 접근이 차단", "페이지 접근 차단"),
    (r"브라우저가 열려 있지 않습니다", "브라우저 미실행"),
]


def is_form_miss_error(message: str) -> bool:
    """댓글/글쓰기 폼을 못 찾은 실패 — 스냅샷 업로드 우선 대상."""
    if not message:
        return False
    text = message.strip()
    keys = (
        "댓글 폼",
        "댓글 입력란",
        "댓글 등록 버튼",
        "글쓰기 폼",
        "comment form",
        "워드프레스 댓글 폼",
        "Movable Type 댓글 폼",
    )
    return any(k.lower() in text.lower() for k in keys)


def is_strengthenable_error(message: str) -> bool:
    """원격 업로드해 기능 보강할 가치가 있는 실패."""
    if not message:
        return False
    if is_form_miss_error(message):
        return True
    text = localize_error_message(message)
    skip = (
        "내장 Chrome 없음",
        "브라우저 엔진 충돌",
        "작업 취소",
        "브라우저가 닫혔",
        "브라우저 미실행",
        "네트워크 오류",
    )
    return not any(s in text for s in skip)


def localize_error_message(message: str) -> str:
    """이력·로그용 한글 사유 (기존 한글은 유지, 영문은 변환)."""
    if not message:
        return "알 수 없는 오류"
    text = message.strip()
    for pattern, korean in _RULES:
        if re.search(pattern, text, re.I | re.DOTALL):
            if len(text) > 200 and "Call log" in text:
                first = text.split("\n")[0]
                if re.search(pattern, first, re.I):
                    return korean
            return korean
    if re.search(r"[\uac00-\ud7a3]", text):
        return text if len(text) <= 200 else text[:197] + "..."
    first_line = text.split("\n")[0].strip()
    if len(first_line) > 120:
        first_line = first_line[:117] + "..."
    return f"오류: {first_line}"
