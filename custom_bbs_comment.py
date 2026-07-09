"""커스텀 BBS 댓글 — techbook.co.kr/view?seq= 등."""

from __future__ import annotations

from typing import Optional

from app_logger import log
from article_builder import build_comment_content
from board_writer import BoardWriter, random_english_name
from link_utils import normalize_backlink_url, normalize_link_pairs
from page_guard import assert_page_accessible, detect_comment_submit_message, page_contains_backlink, page_has_comment_by_author, verify_comment_backlink

COMMENT_SELECTORS = [
    "#comment",
    'textarea[name="comment"]',
    "form.comment_form textarea",
    "form#replyform textarea",
]

NAME_SELECTORS = [
    "#userNm",
    'input[name="userNm"]',
    'input[placeholder*="Name" i]',
]

EMAIL_SELECTORS = [
    "#email",
    'input[name="email"]',
    'input[type="email"]',
]

WEBSITE_SELECTORS = [
    "#website",
    'input[name="website"]',
    'input[placeholder*="Website" i]',
]

SUBMIT_SELECTORS = [
    "#btnReply",
    "button#btnReply",
    "form#replyform button.button-contactForm",
    "form.comment_form button[type='button']",
]


class CustomBbsCommentWriter(BoardWriter):
    """view?seq= + replyform/postreply 스타일 댓글."""

    def open_post(self, url: str) -> str:
        self.reset_cancel()
        self._launch_stealth_page(default_timeout=45000)
        self._source_url = url.strip()
        assert self.page is not None
        self.page.goto(self._source_url, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(2000)
        assert_page_accessible(self.page)
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        self.page.wait_for_timeout(800)

        if not self._has_comment_form():
            raise RuntimeError("댓글 폼을 찾을 수 없습니다. (replyform/comment textarea 없음)")
        return "커스텀 BBS 글 열림"

    def _has_comment_form(self) -> bool:
        return bool(self._find_first(COMMENT_SELECTORS) and self._find_first(NAME_SELECTORS))

    def fill_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
    ) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")

        pairs = normalize_link_pairs(links)
        self.last_name = name or random_english_name()
        primary_url = normalize_backlink_url(pairs[0][0]) if pairs else ""
        keyword = pairs[0][1] if pairs else ""
        self._last_keyword = keyword
        self._last_backlink_url = primary_url
        body = build_comment_content(pairs, post_index=post_index, style="anchors")

        self._fill_first(NAME_SELECTORS, self.last_name)
        self._fill_first(EMAIL_SELECTORS, f"{self.last_name.lower().replace(' ', '.')}@mail.com")
        if primary_url:
            self._fill_first(WEBSITE_SELECTORS, primary_url)
        if not self._fill_first(COMMENT_SELECTORS, body):
            raise RuntimeError("댓글 입력란을 찾을 수 없습니다.")

        log.info("커스텀 BBS 댓글 입력 (%s, %d자)", self.last_name, len(body))
        return f"댓글 입력 완료 ({self.last_name})"

    def fill_and_submit_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
    ) -> str:
        self.reset_cancel()
        fill_msg = self.fill_comment(links, name=name, post_index=post_index)
        page = self.page
        assert page is not None
        keyword = getattr(self, "_last_keyword", "")
        target_url = getattr(self, "_last_backlink_url", "")
        if not keyword and links:
            pairs = normalize_link_pairs(links)
            keyword = pairs[0][1] if pairs else ""
            target_url = normalize_backlink_url(pairs[0][0]) if pairs else ""

        clicked = False
        for sel in SUBMIT_SELECTORS:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            try:
                if not loc.first.is_visible():
                    continue
                try:
                    with page.expect_response(
                        lambda r: "postreply" in r.url.lower() or "reply" in r.url.lower(),
                        timeout=12000,
                    ):
                        loc.first.click(timeout=5000)
                except Exception:
                    loc.first.click(timeout=5000)
                clicked = True
                break
            except Exception as e:
                log.debug("제출 클릭 실패 %s: %s", sel, e)

        if not clicked:
            raise RuntimeError("댓글 등록 버튼(#btnReply)을 찾을 수 없습니다.")

        page.wait_for_timeout(2500)
        submit_state = detect_comment_submit_message(page)
        if submit_state == "error":
            raise RuntimeError("댓글 제출이 거부되었습니다. (스팸필터·필수값 누락)")

        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

        ok, detail = verify_comment_backlink(
            page,
            target_url=target_url,
            keyword=keyword,
            author=self.last_name,
        )
        if ok:
            return f"{fill_msg}\n댓글 등록 완료 — 백링크 확인 ({detail})"

        if submit_state == "moderation":
            return f"{fill_msg}\n댓글 제출됨 — 승인 대기 중일 수 있습니다."

        if page_has_comment_by_author(page, self.last_name, keyword=keyword):
            return f"{fill_msg}\n댓글 등록 완료 (본문 확인 · 링크 미확인)"

        raise RuntimeError(
            "댓글이 페이지에 표시되지 않습니다. (스팸필터·승인대기·제출 실패 — 성공으로 기록하지 마세요)"
        )
