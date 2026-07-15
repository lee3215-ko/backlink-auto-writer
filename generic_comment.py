"""comment.php · 방명록 · 카페24 게시판 댓글."""

from __future__ import annotations

from typing import Optional

from app_logger import log
from article_builder import build_comment_content
from board_writer import BoardWriter, random_english_name
from link_utils import normalize_backlink_url, normalize_link_pairs
from page_guard import assert_native_comment_system, assert_page_accessible, verify_comment_backlink

COMMENT_SELECTORS = [
    'textarea[name="comment"]',
    'textarea[name="comments"]',
    'textarea[name="message"]',
    'textarea[name="content"]',
    'textarea[name="memo"]',
    'textarea[name="wr_content"]',
    "#comment",
    "#comments",
    'textarea[id*="comment"]',
    "form textarea",
]

NAME_SELECTORS = [
    'input[name="name"]',
    'input[name="author"]',
    'input[name="username"]',
    'input[name="nick"]',
    'input[name="writer"]',
    'input[placeholder*="Name" i]',
    'input[placeholder*="이름"]',
]

EMAIL_SELECTORS = [
    'input[name="email"]',
    'input[type="email"]',
    'input[name="mail"]',
]

URL_SELECTORS = [
    'input[name="url"]',
    'input[name="website"]',
    'input[name="homepage"]',
    'input[name="link"]',
    'input[placeholder*="URL" i]',
    'input[placeholder*="Website" i]',
]

SUBMIT_SELECTORS = [
    'input[type="submit"]',
    'button[type="submit"]',
    'button[name="submit"]',
    'input[name="submit"]',
    'button:has-text("Submit")',
    'button:has-text("Post")',
    'button:has-text("등록")',
    'button:has-text("댓글")',
    'input[value*="Submit"]',
    'input[value*="등록"]',
    "#btnSubmit",
    ".btn_submit",
]


class GenericCommentWriter(BoardWriter):
    """범용 PHP 방명록·comment.php·카페24 read 댓글."""

    def open_post(self, url: str) -> str:
        self.reset_cancel()
        self._launch_stealth_page(default_timeout=45000)
        self._source_url = url.strip()
        assert self.page is not None
        self.page.goto(self._source_url, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(2000)
        assert_page_accessible(self.page)
        assert_native_comment_system(self.page)
        self._dismiss_cookie_banners()
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        self.page.wait_for_timeout(800)
        self._expand_comment_form()

        if not self._has_comment_form():
            raise RuntimeError("댓글/방명록 폼을 찾을 수 없습니다.")
        return "댓글 페이지 열림"

    def _expand_comment_form(self) -> None:
        page = self.page
        assert page is not None
        if self._has_comment_form():
            return
        for sel in (
            'a:has-text("Leave a comment")',
            'a:has-text("Add comment")',
            'a:has-text("댓글")',
            'a:has-text("방명록")',
            'button:has-text("댓글")',
            ".comment_write",
            "#comment_write",
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    if self._has_comment_form():
                        return
            except Exception:
                continue

    def _has_comment_form(self) -> bool:
        return bool(self._find_first(COMMENT_SELECTORS))

    def fill_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
        html: bool = True,
    ) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")

        pairs = normalize_link_pairs(links)
        self.last_name = name or random_english_name()
        primary_url = normalize_backlink_url(pairs[0][0]) if pairs else ""
        style = "anchors" if html else "smart"
        content = build_comment_content(pairs, post_index=post_index, html=html, style=style)

        self._fill_first(NAME_SELECTORS, self.last_name)
        self._fill_first(EMAIL_SELECTORS, f"{self.last_name.lower().replace(' ', '')}@mail.com")
        if primary_url:
            self._fill_first(URL_SELECTORS, primary_url)
        self._fill_first(COMMENT_SELECTORS, content)
        return f"댓글 입력 ({self.last_name}, {len(content)}자)"

    def submit_comment(self) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")
        if not self._click_first(SUBMIT_SELECTORS):
            raise RuntimeError("댓글 등록 버튼을 찾을 수 없습니다.")
        self.page.wait_for_timeout(2500)
        return "댓글 등록 클릭"

    def fill_and_submit_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
        html: bool = True,
    ) -> str:
        pairs = normalize_link_pairs(links)
        primary_url = normalize_backlink_url(pairs[0][0]) if pairs else ""
        keyword = pairs[0][1] if pairs else ""

        detail = self.fill_comment(links, name=name, post_index=post_index, html=html)
        submit_msg = self.submit_comment()

        page = self.page
        assert page is not None
        ok, verify = verify_comment_backlink(
            page, target_url=primary_url, keyword=keyword, author=self.last_name,
        )
        if ok:
            return f"{detail} · {submit_msg} · 백링크 확인 ({verify})"
        return f"{detail} · {submit_msg} — 백링크 미확인"
