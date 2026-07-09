"""그누보드 글 보기 페이지 댓글 자동 작성."""

from __future__ import annotations

from typing import Optional

from app_logger import log
from article_builder import build_comment_content
from board_url import extract_bo_table, extract_wr_id, gnuboard_view_url, normalize_board_list_url
from board_writer import (
    CAPTCHA_IMAGE_SELECTORS,
    CAPTCHA_SELECTORS,
    FIXED_PASSWORD,
    NAME_SELECTORS,
    PASSWORD_SELECTORS,
    BoardWriter,
    random_english_name,
)
from link_utils import normalize_backlink_url, pick_primary_link
from page_guard import verify_comment_backlink

COMMENT_NAME_SELECTORS = [
    '#fviewcomment input[name="wr_name"]',
    '#bo_vc_w input[name="wr_name"]',
    'form[name="fviewcomment"] input[name="wr_name"]',
    '#bo_vc input[name="wr_name"]',
    *NAME_SELECTORS,
]

COMMENT_PASSWORD_SELECTORS = [
    '#fviewcomment input[name="wr_password"]',
    '#bo_vc_w input[name="wr_password"]',
    'form[name="fviewcomment"] input[name="wr_password"]',
    *PASSWORD_SELECTORS,
]

COMMENT_HOMEPAGE_SELECTORS = [
    '#fviewcomment input[name="wr_homepage"]',
    '#bo_vc_w input[name="wr_homepage"]',
    'form[name="fviewcomment"] input[name="wr_homepage"]',
    '#fviewcomment input[name="wr_link1"]',
    '#bo_vc_w input[name="wr_link1"]',
    'input[name="wr_homepage"]',
    'input[name="wr_link1"]',
    'input[name="url"]',
    'input[placeholder*="홈페이지" i]',
    'input[placeholder*="homepage" i]',
]

COMMENT_CONTENT_SELECTORS = [
    '#fviewcomment textarea[name="wr_content"]',
    '#bo_vc_w textarea#wr_content',
    '#bo_vc_w textarea[name="wr_content"]',
    'form[name="fviewcomment"] textarea',
    '#bo_vc textarea[name="wr_content"]',
    'textarea#wr_content',
]

COMMENT_SUBMIT_SELECTORS = [
    '#fviewcomment input[type="submit"]',
    '#bo_vc_w input[type="submit"]',
    'form[name="fviewcomment"] input[type="submit"]',
    'input[type="submit"][value*="댓글"]',
    'button:has-text("댓글")',
    'input[type="submit"][value*="등록"]',
    'button:has-text("등록")',
]

COMMENT_CAPTCHA_SELECTORS = [
    '#fviewcomment input[name="captcha_key"]',
    '#bo_vc_w input[name="captcha_key"]',
    *CAPTCHA_SELECTORS,
]

COMMENT_CAPTCHA_IMAGE_SELECTORS = [
    '#fviewcomment #captcha_img',
    '#bo_vc_w #captcha_img',
    *CAPTCHA_IMAGE_SELECTORS,
]


class CommentWriter(BoardWriter):
    """그누보드 게시글 보기 페이지에서 댓글 작성."""

    def open_comment_page(self, url: str) -> str:
        self.reset_cancel()
        target = gnuboard_view_url(url) or url.strip()
        self._source_url = url
        self.last_list_url = url
        self.last_write_url = ""
        self.last_post_url = target

        self._launch_stealth_page(default_timeout=15000)
        assert self.page is not None
        self.page.goto(target, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1200)
        self._scroll_to_comment_form()

        if not self._has_comment_form():
            bo = extract_bo_table(url)
            wr = extract_wr_id(url)
            if bo and not wr:
                raise RuntimeError(
                    "게시판 목록 URL입니다. 댓글이 아니라 새 글 작성 대상입니다. "
                    "글작성 탭에서 모드를 '게시글' 또는 'auto'로 설정하세요."
                )
            if bo and wr:
                list_url = normalize_board_list_url(url) or f"board.php?bo_table={bo}"
                raise RuntimeError(
                    "댓글 입력 폼을 찾을 수 없습니다. (회원 전용·댓글 비활성 가능) "
                    f"이 URL은 글 보기(wr_id) 링크입니다. 새 글을 쓰려면 wr_id 없는 목록 URL을 사용하세요: "
                    f"{list_url}"
                )
            raise RuntimeError("댓글 입력 폼을 찾을 수 없습니다. (회원 전용·댓글 비활성 가능)")

        self.last_write_url = self.page.url
        return "댓글 페이지 열림"

    def _scroll_to_comment_form(self) -> None:
        page = self.page
        assert page is not None
        for sel in ("#bo_vc", "#bo_vc_w", "#fviewcomment", "form[name='fviewcomment']"):
            loc = page.locator(sel)
            if loc.count() > 0:
                try:
                    loc.first.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(400)
                    return
                except Exception:
                    pass
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)

    def _has_comment_form(self) -> bool:
        return self._find_first(COMMENT_CONTENT_SELECTORS) is not None

    def _enable_comment_html(self) -> bool:
        """댓글 폼 HTML 허용 시 앵커 태그 사용."""
        page = self.page
        assert page is not None
        result = page.evaluate(
            """() => {
                const form = document.querySelector(
                    '#fviewcomment, #bo_vc_w form, form[name="fviewcomment"]'
                );
                if (!form) return false;
                const el = form.querySelector(
                    'input[name="html"], input[name="wr_html"], input[id="html"]'
                );
                if (!el) return false;
                const type = (el.type || '').toLowerCase();
                if (type === 'checkbox' || type === 'radio') {
                    el.checked = true;
                    el.value = 'html1';
                } else {
                    el.value = 'html1';
                }
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }"""
        )
        return bool(result)

    def fill_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        post_index: int = 0,
    ) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")
        if not links:
            raise ValueError("링크가 비어 있습니다.")

        page = self.page
        assert page is not None

        self.last_name = name or random_english_name()
        primary_url, primary_kw = pick_primary_link(links, post_index=post_index)
        self._last_backlink_url = primary_url
        self._last_keyword = primary_kw

        html_enabled = self._enable_comment_html()
        if html_enabled:
            content = build_comment_content(links, post_index=post_index, style="anchors")
            mode_note = "HTML 앵커"
        else:
            content = build_comment_content(links, post_index=post_index, style="smart")
            mode_note = "URL 자동링크"

        self._fill_first(COMMENT_NAME_SELECTORS, self.last_name)
        self._fill_first(COMMENT_PASSWORD_SELECTORS, FIXED_PASSWORD)
        if primary_url:
            self._fill_first(COMMENT_HOMEPAGE_SELECTORS, normalize_backlink_url(primary_url))

        el = self._find_first(COMMENT_CONTENT_SELECTORS)
        if not el:
            raise RuntimeError("댓글 입력란을 찾을 수 없습니다.")
        el.click()
        el.fill("")
        el.fill(content)
        log.info("댓글 입력 %d자 (%s)", len(content), mode_note)

        return f"댓글 양식 입력 완료 (이름: {self.last_name} · {mode_note} · {len(content)}자)"

    def fill_and_submit_comment(
        self,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        max_captcha_retries: int = 5,
        post_index: int = 0,
    ) -> str:
        self.reset_cancel()
        fill_msg = self.fill_comment(links, name=name, post_index=post_index)
        page = self.page
        assert page is not None
        target_url = getattr(self, "_last_backlink_url", "")
        keyword = getattr(self, "_last_keyword", "")
        last_error = ""

        for attempt in range(1, max_captcha_retries + 1):
            if self._cancelled:
                raise RuntimeError("작업이 취소되었습니다.")
            try:
                code, detail = self.solve_captcha()
                log.info("댓글 캡차 시도 %d: %s", attempt, detail)
                if not self._fill_first(COMMENT_CAPTCHA_SELECTORS, code):
                    self._fill_first(COMMENT_CAPTCHA_SELECTORS, code)
                page.wait_for_timeout(200)
                if not self._click_comment_submit():
                    raise RuntimeError("댓글 등록 버튼을 찾을 수 없습니다.")
                page.wait_for_timeout(2000)
                try:
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    self._scroll_to_comment_form()
                except Exception:
                    pass

                ok, detail = verify_comment_backlink(
                    page,
                    target_url=target_url,
                    keyword=keyword,
                    author=self.last_name,
                )
                if ok:
                    return f"{fill_msg}\n댓글 등록 완료 — 백링크 확인 ({detail})"

                if self._is_comment_success():
                    if ok:
                        return f"{fill_msg}\n댓글 등록 완료 — 백링크 확인 ({detail})"
                    last_error = f"댓글 등록됐으나 백링크 미확인 ({detail})"
                else:
                    last_error = f"캡차/등록 실패 추정 (시도 {attempt})"
                self._refresh_captcha()
            except Exception as e:
                last_error = str(e)
                self._refresh_captcha()
                page.wait_for_timeout(500)

        raise RuntimeError(f"댓글 등록 실패: {last_error}")

    def _click_comment_submit(self) -> bool:
        page = self.page
        assert page is not None
        for sel in COMMENT_SUBMIT_SELECTORS:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            el = loc.first
            try:
                if not el.is_visible():
                    continue
                el.click(timeout=5000, no_wait_after=True)
                log.info("댓글 제출: %s", sel)
                return True
            except Exception:
                continue
        return False

    def _is_comment_success(self) -> bool:
        page = self.page
        assert page is not None
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            pass
        fail_kw = ("틀렸", "올바르지", "다시 입력", "로그인", "권한", "불가")
        if any(k in body for k in fail_kw):
            return False
        ok_kw = ("등록", "작성", "댓글")
        return any(k in body for k in ok_kw)

    def _capture_captcha_image(self) -> bytes:
        page = self.page
        assert page is not None
        from urllib.parse import urljoin

        for sel in COMMENT_CAPTCHA_IMAGE_SELECTORS:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            img = loc.first
            try:
                if not img.is_visible():
                    continue
                src = img.get_attribute("src")
                if src:
                    full_url = urljoin(page.url, src)
                    resp = page.context.request.get(full_url)
                    if resp.ok and resp.body():
                        return resp.body()
                return img.screenshot()
            except Exception:
                continue
        return super()._capture_captcha_image()
