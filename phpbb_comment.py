"""phpBB / SMF 포럼 게스트 답글."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from app_logger import log
from article_builder import build_comment_content
from board_writer import BoardWriter, random_english_name
from forum_url import is_smf_thread_url, phpbb_reply_url, smf_reply_url
from link_utils import normalize_backlink_url, normalize_link_pairs
from page_guard import assert_page_accessible, page_contains_backlink, verify_comment_backlink

MESSAGE_SELECTORS = [
    'textarea[name="message"]',
    "#message",
    'textarea#message',
    'textarea[name="quickreply"]',
    'textarea[name="post_text"]',
    "#quickreply textarea",
    'div[contenteditable="true"]',
]

NAME_SELECTORS = [
    'input[name="username"]',
    "#username",
    'input[name="guestname"]',
    'input[name="name"]',
]

EMAIL_SELECTORS = [
    'input[name="email"]',
    'input[name="guestemail"]',
    'input[type="email"]',
]

SUBJECT_SELECTORS = [
    'input[name="subject"]',
    "#subject",
]

SUBMIT_SELECTORS = [
    'input[name="post"]',
    'input[name="submit"]',
    'input[type="submit"][name="post"]',
    'button[name="post"]',
    'button[type="submit"]',
    'input[value*="Submit"]',
    'input[value*="Post"]',
    'input[value*="등록"]',
]

REPLY_LINK_SELECTORS = [
    'a[href*="mode=reply"]',
    'a[href*="action=post"]',
    'a:has-text("Post Reply")',
    'a:has-text("Reply")',
    'a:has-text("답변")',
    'a:has-text("댓글")',
]


class PhpbbCommentWriter(BoardWriter):
    """phpBB showthread/viewtopic · SMF index.php?topic= 게스트 답글."""

    def open_post(self, url: str) -> str:
        self.reset_cancel()
        self._launch_stealth_page(default_timeout=45000)
        self._source_url = url.strip()
        assert self.page is not None
        self._is_smf = is_smf_thread_url(url)

        for candidate in (self._reply_page_url(url), url):
            try:
                self.page.goto(candidate, wait_until="domcontentloaded", timeout=45000)
                self.page.wait_for_timeout(1500)
                break
            except Exception as exc:
                log.info("포럼 로드 실패 %s: %s", candidate[:60], exc)

        assert_page_accessible(self.page)
        self._dismiss_cookie_banners()
        self._open_reply_form()

        if not self._has_reply_form():
            raise RuntimeError(
                "포럼 답글 폼을 찾을 수 없습니다. (로그인 필요·답글 차단·목록 페이지일 수 있음)"
            )
        return "포럼 글 열림"

    def _reply_page_url(self, url: str) -> str:
        if is_smf_thread_url(url):
            return smf_reply_url(url)
        return phpbb_reply_url(url)

    def _open_reply_form(self) -> None:
        page = self.page
        assert page is not None
        if self._has_reply_form():
            return
        for sel in REPLY_LINK_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=4000)
                    page.wait_for_timeout(1500)
                    if self._has_reply_form():
                        return
            except Exception:
                continue
        try:
            reply_u = self._reply_page_url(self._source_url)
            if reply_u != page.url:
                page.goto(reply_u, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
        except Exception:
            pass
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

    def _has_reply_form(self) -> bool:
        return bool(self._find_first(MESSAGE_SELECTORS))

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
        content = build_comment_content(pairs, post_index=post_index, html=False, style="bbcode")

        self._fill_first(NAME_SELECTORS, self.last_name)
        self._fill_first(EMAIL_SELECTORS, f"{self.last_name.lower().replace(' ', '')}@mail.com")
        if self._find_first(SUBJECT_SELECTORS):
            kw = pairs[0][1] if pairs else "Re"
            self._fill_first(SUBJECT_SELECTORS, f"Re: {kw[:40]}")
        self._fill_first(MESSAGE_SELECTORS, content)
        return f"포럼 답글 입력 ({self.last_name}, {len(content)}자)"

    def submit_comment(self) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")
        if not self._click_first(SUBMIT_SELECTORS):
            page = self.page
            assert page is not None
            for label in ("Post", "Submit", "Send", "등록", "답변"):
                try:
                    page.get_by_role("button", name=label).first.click(timeout=3000)
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError("답글 등록 버튼을 찾을 수 없습니다.")
        self.page.wait_for_timeout(2500)
        return "답글 등록 클릭"

    def fill_and_submit_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
    ) -> str:
        pairs = normalize_link_pairs(links)
        primary_url = normalize_backlink_url(pairs[0][0]) if pairs else ""
        keyword = pairs[0][1] if pairs else ""

        detail = self.fill_comment(links, name=name, post_index=post_index)
        submit_msg = self.submit_comment()

        page = self.page
        assert page is not None
        ok, verify = verify_comment_backlink(
            page, target_url=primary_url, keyword=keyword, author=self.last_name,
        )
        if ok:
            return f"{detail} · {submit_msg} · 백링크 확인 ({verify})"
        found, html_detail = page_contains_backlink(page, primary_url, keyword=keyword)
        if found:
            return f"{detail} · {submit_msg} · 백링크 확인 ({html_detail})"
        return f"{detail} · {submit_msg} — 백링크 미확인 (로그인·승인대기 가능)"
