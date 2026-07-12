"""워드프레스 비회원 댓글 폼 자동 입력."""

from __future__ import annotations

from typing import Optional

from app_logger import log
from article_builder import _pick_comment_links, build_comment_content
from board_writer import BoardWriter, random_english_name
from board_url import url_access_candidates
from link_utils import normalize_backlink_url, pick_primary_link
from page_guard import assert_page_accessible, detect_comment_submit_message, page_contains_backlink, page_has_comment_by_author

WP_COMMENT_SELECTORS = [
    "#comment",
    'textarea[name="comment"]',
    "#commentform textarea",
    "#comment-form textarea",
    "#respond textarea",
    ".comment-form textarea",
    'form.comment-form textarea[name="comment"]',
    '#comment-form textarea[name="comment"]',
    'textarea[placeholder*="Message" i]',
    'textarea[placeholder*="Kommentar" i]',
    'textarea[placeholder*="댓글" i]',
]

WP_COMMENT_ROLE_NAMES = ("Comment", "Message", "Kommentar", "댓글")

WP_AUTHOR_SELECTORS = [
    "#author",
    'input[name="author"]',
    "#commentform input[name='author']",
    'input[placeholder*="Name" i]',
    'input[aria-label*="Name" i]',
    'input[placeholder*="이름" i]',
]

WP_EMAIL_SELECTORS = [
    "#email",
    'input[name="email"]',
    'input[type="email"]',
    'input[placeholder*="Email" i]',
    'input[placeholder*="이메일" i]',
]

WP_URL_SELECTORS = [
    "#url",
    'input[name="url"]',
    'input[placeholder*="Website" i]',
    'input[placeholder*="URL" i]',
    'input[placeholder*="웹사이트" i]',
]

WP_FORM_ROOT_SELECTORS = (
    "#commentform",
    "#comment-form",
    "form.comment-form",
    "#respond form",
    ".comment-respond form",
)

WP_SUBMIT_SELECTORS = [
    "#submit",
    'input[name="submit"]',
    "#commentform input[type='submit']",
    '#comment-form input[type="submit"]',
    '#respond input[type="submit"]',
    'button[type="submit"]',
    'input[value*="Post Comment"]',
    'input[value*="Submit Comment"]',
    'input[value*="댓글"]',
    'input[value*="등록"]',
    'button:has-text("Post Comment")',
    'button:has-text("Submit Comment")',
    'button:has-text("댓글")',
    'input[value*="Kommentar"]',
    'button:has-text("Kommentar")',
    'input[value*="Kommentar abschicken"]',
    'button:has-text("Kommentar abschicken")',
]

WP_SUBMIT_LABELS = (
    "Post Comment",
    "Post comment",
    "Submit Comment",
    "댓글 남기기",
    "댓글 달기",
    "댓글 등록",
    "댓글",
    "등록",
    "Kommentar abschicken",
    "Kommentar",
)


class WordPressCommentWriter(BoardWriter):
    """워드프레스 글 페이지 댓글 (비회원 폼)."""

    def open_post(self, url: str) -> str:
        self.reset_cancel()
        self._stealth_profile_index = 0
        self._launch_stealth_page(default_timeout=45000)
        self._source_url = url.strip()
        assert self.page is not None
        self._goto_post_page(self._source_url)
        if self._is_waf_blocked_page():
            self._relaunch_alternate_stealth_profile(default_timeout=45000)
            self._goto_post_page(self._source_url)
        self.page.wait_for_timeout(2000)

        assert_page_accessible(self.page)
        self._dismiss_cookie_banners()
        if self._is_member_only_comments():
            raise RuntimeError(
                "워드프레스 댓글은 회원 로그인 후에만 가능합니다. (비회원 폼 없음)"
            )
        self._wait_for_comment_form(max_sec=25)
        self._open_wp_comment_form()
        self._ensure_comment_fields_ready()

        if not self._has_wp_comment_form():
            if self._is_member_only_comments():
                raise RuntimeError(
                    "워드프레스 댓글은 회원 로그인 후에만 가능합니다. (비회원 폼 없음)"
                )
            detail = self._comment_form_miss_detail()
            raise RuntimeError(
                "워드프레스 댓글 폼을 찾을 수 없습니다. (로딩 지연·댓글 차단·회원 전용)"
                + (f" [{detail}]" if detail else "")
            )

        return "워드프레스 글 열림"

    def _goto_post_page(self, url: str) -> None:
        page = self.page
        assert page is not None
        last_err: Exception | None = None
        for candidate in url_access_candidates(url):
            for wait_until in ("domcontentloaded", "load"):
                try:
                    page.goto(candidate, wait_until=wait_until, timeout=45000)
                    self._source_url = page.url or candidate
                    log.info("WP 페이지 로드: %s", self._source_url[:80])
                    return
                except Exception as e:
                    last_err = e
                    err = str(e).lower()
                    if any(x in err for x in ("net::", "ssl", "timeout", "err_connection", "err_name")):
                        continue
        if last_err:
            raise RuntimeError(f"페이지 로딩 실패 (SSL·리다이렉트·차단): {last_err}") from last_err
        raise RuntimeError("페이지 로딩 실패 — URL을 확인해 주세요.")

    def _is_member_only_comments(self) -> bool:
        page = self.page
        assert page is not None
        try:
            return bool(
                page.evaluate(
                    """() => {
                        if (document.querySelector('.must-log-in, a.comment-reply-login')) return true;
                        const r = document.querySelector('#respond, .comment-respond');
                        if (!r) return false;
                        if (document.querySelector('#commentform textarea[name="comment"], #comment, textarea[name="comment"]'))
                            return false;
                        const t = (r.innerText || '').toLowerCase();
                        return (
                            t.includes('must be logged') ||
                            t.includes('logged in to') ||
                            (t.includes('로그인') && (t.includes('댓글') || t.includes('reply') || t.includes('남겨')))
                        );
                    }"""
                )
            )
        except Exception:
            return False

    def _comment_form_in_dom(self) -> bool:
        """표시 여부와 무관 — DOM에 표준 WP 댓글 폼이 있는지."""
        page = self.page
        assert page is not None
        for _ in range(3):
            try:
                if page.evaluate(
                    """() => {
                        const ta = document.querySelector('#comment, textarea[name="comment"]');
                        if (!ta) return false;
                        // 숨김·크기 0이어도 DOM 존재면 폼으로 인정
                        return true;
                    }"""
                ):
                    return True
            except Exception:
                try:
                    page.wait_for_timeout(400)
                except Exception:
                    pass
        try:
            for sel in WP_FORM_ROOT_SELECTORS:
                if page.locator(sel).count() > 0:
                    if page.locator("#comment, textarea[name='comment']").count() > 0:
                        return True
            if page.locator("#comment, textarea[name='comment']").count() > 0:
                return True
        except Exception:
            pass
        return False

    def _comment_textarea_visible(self) -> bool:
        page = self.page
        assert page is not None
        for sel in WP_COMMENT_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                    return True
            except Exception:
                pass
        for name in WP_COMMENT_ROLE_NAMES:
            try:
                loc = page.get_by_role("textbox", name=name)
                if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                    return True
            except Exception:
                pass
        return False

    def _wait_for_comment_form(self, max_sec: int = 40) -> None:
        """느린 페이지 — 댓글 영역이 나타날 때까지 스크롤·대기."""
        page = self.page
        assert page is not None
        # 서버 렌더 폼(Neve/Avada 등) — attached 만으로도 충분
        try:
            page.wait_for_selector(
                "#commentform, #comment-form, form.comment-form, #comment, textarea[name='comment']",
                state="attached",
                timeout=8000,
            )
        except Exception:
            pass
        for step in range(max_sec):
            if self._cancelled:
                raise RuntimeError(
                    "작업이 취소되었습니다. (취소 버튼을 누르지 않았다면 로딩 중 취소된 것일 수 있습니다.)"
                )
            if step > 0 and step % 6 == 0:
                try:
                    assert_page_accessible(page)
                except RuntimeError:
                    raise
            if self._comment_textarea_visible() or self._comment_form_in_dom():
                self._scroll_to_comment_area()
                return
            if step >= 4 and self._has_comment_reply_links():
                self._scroll_to_comment_area()
                return
            # 점진적 스크롤 (맨 아래까지 여러 번)
            page.evaluate(
                """(step) => {
                    const h = document.body.scrollHeight;
                    window.scrollTo(0, h * Math.min(1, (step + 1) / 8));
                }""",
                step,
            )
            if step % 4 == 3:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            if step == 10:
                self._dismiss_cookie_banners()
            page.wait_for_timeout(1000)
        self._scroll_to_comment_area()

    def _ensure_comment_fields_ready(self) -> None:
        """뷰포트 밖·오버레이에 가려진 폼도 스크롤 후 입력 가능하게."""
        page = self.page
        assert page is not None
        self._scroll_to_comment_area()
        try:
            loc = page.locator("#comment, textarea[name='comment']")
            if loc.count() > 0:
                loc.first.scroll_into_view_if_needed(timeout=4000)
                page.wait_for_timeout(300)
        except Exception:
            pass

    def _comment_form_miss_detail(self) -> str:
        page = self.page
        assert page is not None
        try:
            title = (page.title() or "")[:60]
            cur = (page.url or "")[:80]
            has_wp = "wp-comments-post" in (page.content() or "")[:200000]
            return f"url={cur} title={title!r} wp-post={'Y' if has_wp else 'N'}"
        except Exception:
            return ""

    def _dismiss_cookie_banners(self) -> None:
        page = self.page
        assert page is not None
        for sel in (
            'button:has-text("Accept")',
            'button:has-text("Got it")',
            'button:has-text("동의")',
            'button:has-text("확인")',
            'button:has-text("닫기")',
            'a:has-text("동의")',
            ".cc-dismiss",
            "#cookie-notice .cn-set-cookie",
            ".cookie-accept",
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                pass

    def _scroll_to_comment_area(self) -> None:
        page = self.page
        assert page is not None
        for sel in ("#comments", "#respond", ".comment-respond", "#commentform", ".comments-area"):
            loc = page.locator(sel)
            if loc.count() > 0:
                try:
                    loc.first.scroll_into_view_if_needed(timeout=4000)
                    page.wait_for_timeout(500)
                    return
                except Exception:
                    pass
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)

    def _has_comment_reply_links(self) -> bool:
        page = self.page
        assert page is not None
        for sel in (
            "a.comment-reply-link",
            'a[rel="nofollow"].comment-reply-link',
            'a[href*="replytocom"]',
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return True
            except Exception:
                pass
        return False

    def _click_comment_reply_link(self) -> bool:
        """기존 댓글의 Reply 링크 클릭 — 인라인 답글 폼 표시."""
        page = self.page
        assert page is not None
        selectors = (
            "a.comment-reply-link",
            'a[rel="nofollow"].comment-reply-link',
            'a[href*="replytocom"]',
            'a[aria-label*="Reply" i]',
            'a:has-text("Reply")',
            'a:has-text("답글")',
        )
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = loc.count()
            except Exception:
                continue
            for i in range(count):
                try:
                    link = loc.nth(i)
                    if not link.is_visible():
                        continue
                    link.scroll_into_view_if_needed(timeout=4000)
                    page.wait_for_timeout(300)
                    link.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    if self._comment_textarea_visible():
                        log.info("WP Reply 링크 클릭 — 답글 폼 표시됨")
                        return True
                except Exception:
                    continue
        return False

    def _open_wp_comment_form(self) -> None:
        """하단 댓글 폼 또는 Reply 링크로 입력 폼을 연다."""
        page = self.page
        assert page is not None
        if self._comment_textarea_visible():
            self._scroll_to_comment_area()
            return

        self._scroll_to_comment_area()

        for sel in (
            'a:has-text("Leave a Reply")',
            'a:has-text("Leave Your Reply")',
            'a:has-text("Leave a comment")',
            'a:has-text("leave a reply")',
            'a:has-text("댓글 남기기")',
            'a:has-text("댓글 달기")',
            'a:has-text("댓글쓰기")',
            'a:has-text("Kommentar hinterlassen")',
            'a:has-text("Schreibe einen Kommentar")',
            "#respond a.comment-reply-login",
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.scroll_into_view_if_needed(timeout=3000)
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    if self._comment_textarea_visible():
                        return
            except Exception:
                pass

        if self._click_comment_reply_link():
            self._scroll_to_comment_area()
            return

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        self._click_comment_reply_link()

    def _expand_reply_form(self) -> None:
        """호환용 — _open_wp_comment_form 으로 위임."""
        self._open_wp_comment_form()

    def _has_wp_comment_form(self) -> bool:
        if self._comment_textarea_visible() or self._comment_form_in_dom():
            return True
        page = self.page
        assert page is not None
        for name in WP_COMMENT_ROLE_NAMES:
            try:
                if page.get_by_role("textbox", name=name).count() > 0:
                    return True
            except Exception:
                pass
        return False

    def _fill_wp_locator(self, loc, value: str) -> bool:
        """visible 여부와 무관하게 스크롤 후 fill (force)."""
        page = self.page
        assert page is not None
        try:
            if loc.count() == 0:
                return False
            el = loc.first
            try:
                el.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                el.fill(value, timeout=5000)
                return True
            except Exception:
                el.fill(value, force=True, timeout=5000)
                return True
        except Exception:
            return False

    def _fill_wp_field(self, selectors: list[str], value: str, *, role_name: str = "") -> bool:
        page = self.page
        assert page is not None
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and self._fill_wp_locator(loc, value):
                    return True
            except Exception:
                pass
        if role_name:
            try:
                loc = page.get_by_role("textbox", name=role_name)
                if loc.count() > 0 and self._fill_wp_locator(loc, value):
                    return True
            except Exception:
                pass
            # 한글 라벨 (이름 / 이메일 / 웹사이트)
            ko_map = {
                "Name": ("이름", "성명"),
                "Email": ("이메일", "메일"),
                "Website": ("웹사이트", "사이트"),
            }
            for alt in ko_map.get(role_name, ()):
                try:
                    loc = page.get_by_role("textbox", name=alt)
                    if loc.count() > 0 and self._fill_wp_locator(loc, value):
                        return True
                except Exception:
                    pass
        return False

    def _fill_wp_comment(self, body: str) -> bool:
        if self._fill_wp_field(WP_COMMENT_SELECTORS, body, role_name=""):
            return True
        page = self.page
        assert page is not None
        for name in WP_COMMENT_ROLE_NAMES:
            if self._fill_wp_field([], body, role_name=name):
                return True
        for ph in ("Message", "Kommentar", "Comment", "댓글"):
            try:
                loc = page.get_by_placeholder(ph)
                if loc.count() > 0 and self._fill_wp_locator(loc, body):
                    return True
            except Exception:
                pass
        # 최후: JS로 직접 값 설정 (일부 테마에서 Playwright fill 실패)
        try:
            ok = page.evaluate(
                """(text) => {
                    const el = document.querySelector('#comment, textarea[name="comment"]');
                    if (!el) return false;
                    el.focus();
                    el.value = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }""",
                body,
            )
            if ok:
                return True
        except Exception:
            pass
        return False

    def _accept_wp_comment_extras(self) -> None:
        """GDPR 쿠키 동의·Akismet 타임스탬프 (littlefootprintsnj 등)."""
        page = self.page
        assert page is not None
        for sel in (
            "#wp-comment-cookies-consent",
            'input[name="wp-comment-cookies-consent"]',
            'input[name="comment-cookies-consent"]',
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible() and not loc.first.is_checked():
                    loc.first.check(timeout=2000)
                    page.wait_for_timeout(200)
            except Exception:
                pass
        try:
            page.evaluate(
                """() => {
                    const hp = document.querySelector('textarea[name="ak_hp_textarea"]');
                    if (hp) hp.value = '';
                    const ak = document.querySelector('input[name="ak_js"]');
                    if (ak) ak.value = String(Date.now());
                }"""
            )
        except Exception:
            pass

    def _click_wp_submit(self) -> None:
        page = self.page
        assert page is not None
        for sel in WP_SUBMIT_SELECTORS:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            el = loc.first
            try:
                el.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                if el.is_visible():
                    el.click(no_wait_after=True)
                    return
            except Exception:
                pass
            try:
                el.click(force=True, no_wait_after=True)
                return
            except Exception:
                continue
        for label in WP_SUBMIT_LABELS:
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count() > 0:
                    btn.first.click(force=True, no_wait_after=True)
                    return
            except Exception:
                pass
            try:
                inp = page.locator(f'input[type="submit"][value="{label}"]')
                if inp.count() > 0:
                    inp.first.click(force=True, no_wait_after=True)
                    return
            except Exception:
                pass
        # 최후: #submit / form submit
        try:
            clicked = page.evaluate(
                """() => {
                    const btn = document.querySelector('#submit, #commentform input[type="submit"], form.comment-form input[type="submit"]');
                    if (!btn) return false;
                    btn.click();
                    return true;
                }"""
            )
            if clicked:
                return
        except Exception:
            pass
        raise RuntimeError("댓글 등록 버튼을 찾을 수 없습니다.")

    def _wp_submit_succeeded(self, *, keyword: str, target_url: str = "") -> str:
        """제출 후 상태 — success | moderation | fail."""
        page = self.page
        assert page is not None
        url = (page.url or "").lower()
        if "unapproved" in url or "moderation-hash" in url or "comment-page" in url:
            return "moderation"
        submit_state = detect_comment_submit_message(page)
        if submit_state == "error":
            return "fail"
        if submit_state == "moderation":
            return "moderation"
        if page_has_comment_by_author(page, self.last_name, keyword=keyword):
            if target_url:
                found, _ = page_contains_backlink(page, target_url, keyword=keyword)
                if found:
                    return "success"
            return "success"
        try:
            body = page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            body = ""
        wait_kw = (
            "대기 중", "moderation", "검토", "awaiting", "held for",
            "승인", "보호 중", "확인 중", "duplicate", "중복",
            "감사합니다", "thank you for", "your comment is awaiting",
            "댓글이 등록", "검토 후",
        )
        if any(k in body for k in wait_kw):
            return "moderation"
        # 제출 후 입력란이 비워졌으면 서버가 접수(승인 대기)한 경우가 많음
        try:
            cleared = page.evaluate(
                """() => {
                    const ta = document.querySelector('#comment, textarea[name="comment"]');
                    if (!ta) return true; // 폼 사라짐 = 제출 후 리다이렉트성
                    return !(ta.value || '').trim();
                }"""
            )
            if cleared and self.last_name:
                return "moderation"
        except Exception:
            pass
        if keyword and keyword in (page.content() or ""):
            return "success"
        if self.last_name and self.last_name.lower() in body:
            return "moderation"
        return "fail"

    def fill_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
    ) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")

        self.last_name = name or random_english_name()
        picked = _pick_comment_links(links, post_index=post_index)
        primary_url = normalize_backlink_url(picked[0][0]) if picked else ""
        self._last_keyword = picked[0][1] if picked else ""
        self._last_backlink_url = primary_url
        body = build_comment_content(links, post_index=post_index, style="anchors")

        self._fill_wp_field(WP_AUTHOR_SELECTORS, self.last_name, role_name="Name")
        self._fill_wp_field(WP_EMAIL_SELECTORS, "writer@example.com", role_name="Email")
        if primary_url:
            self._fill_wp_field(WP_URL_SELECTORS, primary_url, role_name="Website")
        if not self._fill_wp_comment(body):
            raise RuntimeError("댓글 입력란을 찾을 수 없습니다.")
        # 이름/이메일이 비어 있으면 JS로 강제 입력 (일부 테마 visibility 이슈)
        try:
            page = self.page
            assert page is not None
            page.evaluate(
                """({name, email, url}) => {
                    const set = (sel, v) => {
                      const el = document.querySelector(sel);
                      if (el && !(el.value||'').trim()) {
                        el.value = v;
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                      }
                    };
                    set('#author, input[name="author"]', name);
                    set('#email, input[name="email"]', email);
                    if (url) set('#url, input[name="url"]', url);
                }""",
                {"name": self.last_name, "email": "writer@example.com", "url": primary_url or ""},
            )
        except Exception:
            pass

        self._accept_wp_comment_extras()

        log.info("WP 댓글 입력 완료 (%s, %d자)", self.last_name, len(body))
        return f"WP 댓글 입력 완료 ({self.last_name})"

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

        try:
            with page.expect_navigation(timeout=20000, wait_until="domcontentloaded"):
                self._click_wp_submit()
        except Exception:
            self._click_wp_submit()
            page.wait_for_timeout(2500)

        keyword = getattr(self, "_last_keyword", links[0][1] if links else "")
        target_url = getattr(self, "_last_backlink_url", "")
        state = self._wp_submit_succeeded(keyword=keyword, target_url=target_url)
        if state == "fail":
            try:
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                self._open_wp_comment_form()
                state = self._wp_submit_succeeded(keyword=keyword, target_url=target_url)
            except Exception:
                pass

        if state == "success":
            found, bl_detail = page_contains_backlink(page, target_url, keyword=keyword)
            if found:
                return f"{fill_msg}\n댓글 등록 완료 — 백링크 확인 ({bl_detail})"
            if page_has_comment_by_author(page, self.last_name, keyword=keyword):
                return f"{fill_msg}\n댓글 등록 완료 (본문 확인 · 링크는 승인 후 표시될 수 있음)"
            return f"{fill_msg}\n댓글 등록 완료 (댓글 본문 확인됨)"
        if state == "moderation":
            return f"{fill_msg}\n댓글 제출됨 — 승인 대기 중 (아직 공개 전일 수 있음)"

        raise RuntimeError(
            "댓글이 페이지에 표시되지 않습니다. (스팸필터·승인대기·제출 실패)"
        )
