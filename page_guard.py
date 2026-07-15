"""페이지 접근·댓글 제출 결과 검증."""

from __future__ import annotations

from playwright.sync_api import Page

from link_utils import backlink_host_keys, normalize_backlink_url, page_html_has_backlink

BLOCK_TITLE_KW = ("403", "404", "forbidden", "not found", "error")
BLOCK_BODY_KW = (
    "page not found",
    "404",
    "403 forbidden",
    "ninjafirewall",
    "access denied",
    "차단",
    "접근이 거부",
    "찾을 수 없",
)


def assert_page_accessible(page: Page) -> None:
    """404·WAF 등으로 글/댓글 페이지가 열리지 않으면 예외."""
    title = (page.title() or "").lower()
    if any(k in title for k in BLOCK_TITLE_KW):
        raise RuntimeError(f"페이지 접근 실패 — {page.title()}")

    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body = ""

    if any(k in body for k in BLOCK_BODY_KW):
        if "page not found" in body or "찾을 수 없" in body:
            raise RuntimeError("페이지를 찾을 수 없습니다 (404) — URL이 삭제되었거나 잘못되었습니다.")
        if "ninjafirewall" in body or "403" in body:
            raise RuntimeError("사이트 방화벽(WAF)에 차단되었습니다 — 수동 접속·IP 변경 후 재시도하세요.")
        raise RuntimeError("페이지 접근이 차단되었습니다.")


def assert_native_comment_system(page: Page) -> None:
    """Disqus 등 외부 댓글 시스템이면 자동화 불가 예외."""
    try:
        html = (page.content() or "").lower()
    except Exception:
        html = ""
    markers = (
        "disqus.com/embed.js",
        "disqus_thread",
        "disqus.com/count.js",
        "data-disqus",
    )
    if any(m in html for m in markers):
        raise RuntimeError(
            "Disqus 외부 댓글 시스템입니다 — 자체 댓글 폼이 없어 자동 등록할 수 없습니다."
        )
    if "comments.facebook.com" in html or "fb-comments" in html:
        raise RuntimeError(
            "Facebook 댓글 플러그인입니다 — 자체 댓글 폼이 없어 자동 등록할 수 없습니다."
        )


def page_has_comment_by_author(page: Page, author: str, *, keyword: str = "") -> bool:
    """댓글 목록 영역에서 작성자·키워드 확인."""
    if not author:
        return False
    for sel in (".comments-body", ".comment-body", ".comment-content", "#comments", ".comments-list", "ol.commentlist"):
        try:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            for i in range(min(loc.count(), 12)):
                block = loc.nth(i).inner_text(timeout=2000)
                if author in block and (not keyword or keyword in block):
                    return True
        except Exception:
            continue
    html = page.content()
    if author in html and (not keyword or keyword in html):
        # 댓글 블록 근처에 작성자명이 있는지
        idx = html.find(author)
        if idx >= 0:
            window = html[max(0, idx - 400) : idx + 800].lower()
            if any(x in window for x in ("comments-body", "comment-author", "comment-meta", "comments-post")):
                return True
    return False


def detect_comment_submit_message(page: Page) -> str:
    """제출 직후 댓글 영역·상단 알림만 검사 (글 본문 'spam' 단어 오탐 방지)."""
    snippets: list[str] = []
    for sel in (
        ".comment-error",
        "#comment-error",
        ".comments-error",
        "#respond",
        ".comment-respond",
        "#comments",
        ".comments",
        "form[name='comments_form']",
        ".notice",
        ".error",
    ):
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                snippets.append(loc.first.inner_text(timeout=1500).lower())
        except Exception:
            pass
    try:
        snippets.append(page.locator("body").inner_text(timeout=3000)[:2500].lower())
    except Exception:
        pass

    text = "\n".join(snippets)
    moderation_phrases = (
        "awaiting moderation",
        "held for moderation",
        "moderation queue",
        "승인 대기",
        "검토 중",
        "보호 중",
    )
    error_phrases = (
        "your comment could not",
        "comment could not be posted",
        "error posting your comment",
        "could not post your comment",
        "댓글을 등록할 수 없",
        "댓글 등록에 실패",
        "marked as spam",
        "detected as spam",
        "flagged as spam",
        "스팸으로 분류",
        "스팸으로 처리",
        "invalid comment",
        "please try again",
    )
    for p in moderation_phrases:
        if p in text:
            return "moderation"
    for p in error_phrases:
        if p in text:
            return "error"
    return ""


def page_contains_backlink(page: Page, target_url: str, *, keyword: str = "") -> tuple[bool, str]:
    """제출 후 페이지에 백링크(앵커 또는 URL)가 있는지 확인."""
    if not target_url:
        return False, "대상 URL 없음"
    try:
        html = page.content()
    except Exception:
        return False, "페이지 읽기 실패"
    return page_html_has_backlink(html, normalize_backlink_url(target_url), keyword=keyword)


def verify_comment_backlink(
    page: Page,
    *,
    target_url: str,
    keyword: str = "",
    author: str = "",
) -> tuple[bool, str]:
    """
    댓글 등록 후 백링크 검증 — href 우선, 없으면 작성자·키워드 확인.
    """
    found, detail = page_contains_backlink(page, target_url, keyword=keyword)
    if found:
        return True, detail
    if author and page_has_comment_by_author(page, author, keyword=keyword):
        html_low = page.content().lower()
        for host in backlink_host_keys(target_url):
            if host in html_low:
                return True, f"작성자 댓글 + 호스트({host})"
        return False, "댓글은 있으나 백링크 href 미확인"
    return False, detail
