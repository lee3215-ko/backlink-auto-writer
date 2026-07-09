"""Movable Type 비회원 댓글 폼 (일본·구형 블로그)."""

from __future__ import annotations

from article_builder import build_comment_content
from board_writer import BoardWriter, random_english_name
from link_utils import normalize_backlink_url, normalize_link_pairs
from page_guard import assert_page_accessible, detect_comment_submit_message, page_has_comment_by_author, verify_comment_backlink

MT_AUTHOR_SELECTORS = [
    "#comment-author",
    'input[name="author"]',
    'form[action*="mt-comments"] input[name="author"]',
]

MT_EMAIL_SELECTORS = [
    "#comment-email",
    'input[name="email"]',
    'form[action*="mt-comments"] input[name="email"]',
]

MT_URL_SELECTORS = [
    "#comment-url",
    'input[name="url"]',
    'form[action*="mt-comments"] input[name="url"]',
]

MT_TEXT_SELECTORS = [
    "#comment-text",
    'textarea[name="text"]',
    'textarea[name="comment"]',
    'textarea[name="body"]',
    'form[action*="mt-comments"] textarea[name="text"]',
    'form[action*="mt-comment"] textarea',
]

MT_SUBMIT_SELECTORS = [
    "#comment-post",
    'form[name="comments_form"] input[name="post"]',
    'input[name="post"][value*="Post"]',
    'input[name="post"]',
    'input#comment-post',
    'form[action*="mt-comments"] input[name="post"]',
    'input[type="submit"][value="投稿"]',
    'input[type="submit"][value*="投稿"]',
]

MT_AUTHOR_ROLE_NAMES = ("名前", "名前 :", "Name")
MT_EMAIL_ROLE_NAMES = ("メールアドレス", "メールアドレス :", "Email")
MT_URL_ROLE_NAMES = ("URL", "URL :", "Website")
MT_TEXT_ROLE_NAMES = ("コメント", "Comment")


class MovableTypeCommentWriter(BoardWriter):
    """Movable Type 글 페이지 댓글 (author/email/url/text)."""

    def open_post(self, url: str) -> str:
        self.reset_cancel()
        self._stealth_profile_index = 0
        self._launch_stealth_page(default_timeout=45000)
        self._source_url = url.strip()
        assert self.page is not None
        try:
            self.page.goto(self._source_url, wait_until="load", timeout=45000)
        except Exception:
            self.page.goto(self._source_url, wait_until="domcontentloaded", timeout=45000)
        if self._is_waf_blocked_page():
            self._relaunch_alternate_stealth_profile(default_timeout=45000)
            try:
                self.page.goto(self._source_url, wait_until="load", timeout=45000)
            except Exception:
                self.page.goto(self._source_url, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(2000)
        assert_page_accessible(self.page)
        self._dismiss_cookie_banners()
        self._expand_mt_comment_form()
        self._wait_for_comment_form(max_sec=30)

        if not self._has_mt_comment_form():
            raise RuntimeError(
                "Movable Type 댓글 폼을 찾을 수 없습니다. (로딩 지연·댓글 차단·회원 전용)"
            )
        return "Movable Type 글 열림"

    def _wait_for_comment_form(self, max_sec: int = 30) -> None:
        page = self.page
        assert page is not None
        for step in range(max_sec):
            if self._cancelled:
                raise RuntimeError("작업이 취소되었습니다.")
            if self._has_mt_comment_form():
                self._scroll_to_comment_area()
                return
            page.evaluate(
                """(step) => {
                    const h = document.body.scrollHeight;
                    window.scrollTo(0, h * Math.min(1, (step + 1) / 6));
                }""",
                step,
            )
            if step % 3 == 2:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        self._scroll_to_comment_area()

    def _scroll_to_comment_area(self) -> None:
        page = self.page
        assert page is not None
        for sel in ('form[action*="mt-comments"]', "#comment-form", ".comments-open", "#comments"):
            loc = page.locator(sel)
            if loc.count() > 0:
                try:
                    loc.first.scroll_into_view_if_needed(timeout=4000)
                    page.wait_for_timeout(400)
                    return
                except Exception:
                    pass
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

    def _expand_mt_comment_form(self) -> None:
        """댓글 작성 링크·접힌 폼 펼치기."""
        if self._has_mt_comment_form():
            return
        page = self.page
        assert page is not None
        for sel in (
            'a[href*="mt-comments"]',
            'a:has-text("コメントを書く")',
            'a:has-text("コメント")',
            'a:has-text("Leave a comment")',
            "#comments-open-content a",
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(1200)
                    if self._has_mt_comment_form():
                        return
            except Exception:
                pass

    def _has_mt_comment_form(self) -> bool:
        if self._find_first(MT_TEXT_SELECTORS):
            return True
        page = self.page
        assert page is not None
        if page.locator('form[name="comments_form"]').count() > 0:
            if page.locator("#text, textarea[name='text']").count() > 0:
                return True
        if page.locator('form[action*="meep.cgi"]').count() > 0:
            return True
        if page.locator('form[action*="mt-comments"]').count() > 0:
            return True
        if page.locator('form[action*="mt-comment"]').count() > 0:
            return True
        if page.locator("#comment-form, .comments-open-form").count() > 0:
            return True
        for name in MT_TEXT_ROLE_NAMES:
            try:
                if page.get_by_role("textbox", name=name).count() > 0:
                    return True
            except Exception:
                pass
        return False

    def _fill_mt_field(self, selectors: list[str], value: str, *, role_names: tuple[str, ...] = ()) -> bool:
        if selectors and self._fill_first(selectors, value):
            return True
        page = self.page
        assert page is not None
        for name in role_names:
            try:
                loc = page.get_by_role("textbox", name=name)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(value)
                    return True
            except Exception:
                pass
        return False

    def fill_comment(self, links: list, *, post_index: int = 0) -> str:
        page = self.page
        assert page is not None
        pairs = normalize_link_pairs(links)
        self.last_name = random_english_name()
        email = f"{self.last_name.lower().replace(' ', '.')}@mail.com"
        site_url = normalize_backlink_url(pairs[0][0]) if pairs else "https://example.com"
        self._last_backlink_url = site_url
        self._last_keyword = pairs[0][1] if pairs else ""
        body = build_comment_content(pairs, post_index=post_index, style="anchors")

        self._fill_mt_field(MT_AUTHOR_SELECTORS, self.last_name, role_names=MT_AUTHOR_ROLE_NAMES)
        self._fill_mt_field(MT_EMAIL_SELECTORS, email, role_names=MT_EMAIL_ROLE_NAMES)
        self._fill_mt_field(MT_URL_SELECTORS, site_url, role_names=MT_URL_ROLE_NAMES)
        self._prepare_meep_form()
        if not self._fill_mt_field(MT_TEXT_SELECTORS, body, role_names=MT_TEXT_ROLE_NAMES):
            raise RuntimeError("댓글 본문 입력란을 찾을 수 없습니다.")
        return f"MT 댓글 입력 완료 — {self.last_name}"

    def _prepare_meep_form(self) -> None:
        """mu.nu meep.cgi — 쿠키 저장 라디오·hidden 필드."""
        page = self.page
        assert page is not None
        if page.locator('form[name="comments_form"]').count() == 0:
            return
        try:
            forget = page.locator('input#forget[name="bakecookie"]')
            if forget.count() > 0:
                forget.first.check(timeout=2000)
        except Exception:
            pass

    def _click_mt_submit(self) -> None:
        page = self.page
        assert page is not None
        if page.locator('form[name="comments_form"]').count() > 0:
            loc = page.locator('form[name="comments_form"] input[name="post"]')
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(no_wait_after=True)
                return
        if self._click_first(MT_SUBMIT_SELECTORS):
            return
        try:
            page.get_by_role("button", name="投稿").click(timeout=5000)
        except Exception as e:
            raise RuntimeError(f"댓글 등록 버튼을 찾을 수 없습니다: {e}") from e

    def fill_and_submit_comment(self, links: list, *, post_index: int = 0) -> str:
        pairs = normalize_link_pairs(links)
        msg = self.fill_comment(pairs, post_index=post_index)
        page = self.page
        assert page is not None
        try:
            with page.expect_navigation(timeout=25000, wait_until="domcontentloaded"):
                self._click_mt_submit()
        except Exception:
            self._click_mt_submit()
            page.wait_for_timeout(3000)

        keyword = pairs[0][1] if pairs else ""
        target_url = getattr(self, "_last_backlink_url", normalize_backlink_url(pairs[0][0]) if pairs else "")
        submit_state = detect_comment_submit_message(page)
        if submit_state == "error":
            raise RuntimeError("댓글 제출이 거부되었습니다. (스팸·필수값 오류)")

        ok, detail = verify_comment_backlink(
            page, target_url=target_url, keyword=keyword, author=self.last_name,
        )
        if ok:
            return msg + f" · 댓글 등록 완료 — 백링크 확인 ({detail})"

        try:
            page.goto(self._source_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            self._expand_mt_comment_form()
        except Exception:
            pass

        ok, detail = verify_comment_backlink(
            page, target_url=target_url, keyword=keyword, author=self.last_name,
        )
        if ok:
            return msg + f" · 댓글 등록 완료 — 백링크 확인 ({detail})"
        if page_has_comment_by_author(page, self.last_name, keyword=keyword):
            return msg + " · 댓글 등록 완료 (본문 확인 · 링크 미확인)"
        if submit_state == "moderation":
            return msg + " · 댓글 제출됨 (승인 대기)"
        raise RuntimeError(
            "댓글이 페이지에 표시되지 않습니다. (스팸필터·승인대기·제출 실패)"
        )
