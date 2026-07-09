"""제로보드 / DQ Revolution BBS 글쓰기 (write.php?id=)."""

from __future__ import annotations

from typing import Optional

from app_logger import log
from article_builder import build_article_content, build_article_plain
from board_url import zeroboard_write_url
from board_writer import BoardWriter, FIXED_PASSWORD, random_english_name
from html_mode import HtmlModeResult, ensure_html_mode

ZB_NAME_SELECTORS = [
    'form[name="zbform"] input[name="name"]',
    'form#zbform input[name="name"]',
    'input[name="name"]',
]

ZB_PASSWORD_SELECTORS = [
    'form[name="zbform"] input[name="password"]',
    'form#zbform input[name="password"]',
    'input[name="password"]',
]

ZB_EMAIL_SELECTORS = [
    'form[name="zbform"] input[name="email"]',
    'input[name="email"]',
]

ZB_HOMEPAGE_SELECTORS = [
    'form[name="zbform"] input[name="homepage"]',
    'input[name="homepage"]',
]

ZB_TITLE_SELECTORS = [
    'form[name="zbform"] input[name="subject"]',
    'input[name="subject"]',
]

ZB_CONTENT_SELECTORS = [
    'form[name="zbform"] textarea[name="memo"]',
    'textarea#memo',
    'textarea[name="memo"]',
]

ZB_HTML_SELECTORS = [
    'input[name="use_html"]',
    'input#use_html',
]

ZB_SUBMIT_SELECTORS = [
    'form[name="zbform"] input[type="image"]',
    'form#zbform input[type="image"]',
    'input[type="image"][accesskey="s"]',
    'input[name="post"]',
    'input[type="submit"][value*="등록"]',
    'input[type="submit"][value*="작성"]',
]


class ZeroBoardWriter(BoardWriter):
    """제로보드·DQ Revolution — name/password/subject/memo 폼."""

    def open_browser(self, url: str) -> str:
        self.reset_cancel()
        self._launch_stealth_page(default_timeout=20000)

        target = zeroboard_write_url(url) or url.strip()
        self._source_url = url
        self.last_list_url = url
        self.last_write_url = ""
        self.last_post_url = ""
        self.page.goto(target, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1200)

        if not self._has_write_form():
            self._navigate_to_write_page()

        if not self._has_write_form():
            raise RuntimeError(
                "제로보드 글쓰기 폼을 찾을 수 없습니다. write.php?id= URL을 직접 입력해 보세요."
            )

        self.last_write_url = self.page.url if self.page else target
        return "제로보드 글쓰기 페이지가 열렸습니다."

    def _has_write_form(self) -> bool:
        return bool(self._find_first(ZB_TITLE_SELECTORS) and self._find_first(ZB_CONTENT_SELECTORS))

    def _navigate_to_write_page(self) -> None:
        page = self.page
        assert page is not None
        for sel in (
            'a[href*="write.php"]',
            'img[src*="write"]',
            'a:has-text("글쓰기")',
            'a:has-text("쓰기")',
        ):
            loc = page.locator(sel)
            if loc.count() > 0:
                try:
                    loc.first.click(timeout=3000)
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(800)
                    if self._has_write_form():
                        return
                except Exception:
                    pass

    def _ensure_zboard_html(self) -> HtmlModeResult:
        page = self.page
        assert page is not None
        result = ensure_html_mode(page)
        if result.enabled:
            return result
        for sel in ZB_HTML_SELECTORS:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            box = loc.first
            try:
                if not box.is_checked():
                    box.check(force=True)
                return HtmlModeResult(enabled=True, method=sel, message="use_html 체크")
            except Exception:
                try:
                    box.click(force=True)
                    return HtmlModeResult(enabled=True, method=f"click:{sel}", message="use_html 클릭")
                except Exception:
                    continue
        return HtmlModeResult(enabled=False, message="HTML 옵션 없음 — 텍스트로 입력")

    def _has_captcha_field(self) -> bool:
        page = self.page
        assert page is not None
        for sel in (
            'input[name="captcha"]',
            'input[name="captcha_key"]',
            'img[src*="captcha"]',
        ):
            loc = page.locator(sel)
            if loc.count() > 0:
                try:
                    if loc.first.is_visible():
                        return True
                except Exception:
                    return True
        return False

    def fill_form(
        self,
        title: str,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        category: str = "",
        post_index: int = 0,
    ) -> str:
        if not self.is_open():
            raise RuntimeError("브라우저가 열려 있지 않습니다.")
        if not links:
            raise ValueError("링크(사이트·키워드)가 비어 있습니다.")

        self.last_name = name or random_english_name()
        email = f"{self.last_name.lower().replace(' ', '.')}@mail.com"
        primary_site = links[0][0]
        content_html = build_article_content(links, post_index=post_index)

        self._fill_first(ZB_NAME_SELECTORS, self.last_name)
        self._fill_first(ZB_PASSWORD_SELECTORS, FIXED_PASSWORD)
        self._fill_first(ZB_EMAIL_SELECTORS, email)
        self._fill_first(ZB_HOMEPAGE_SELECTORS, primary_site)

        html_result = self._ensure_zboard_html()
        content = content_html if html_result.enabled else build_article_plain(links, post_index=post_index)
        self._fill_first(ZB_TITLE_SELECTORS, title)
        if not self._fill_first(ZB_CONTENT_SELECTORS, content):
            raise RuntimeError("본문(memo) 입력란을 찾을 수 없습니다.")

        html_note = f" | HTML: {html_result.message}" if html_result.enabled else ""
        return f"제로보드 양식 입력 (이름: {self.last_name}{html_note} · {len(content)}자)"

    def fill_and_submit(
        self,
        title: str,
        links: list[tuple[str, str]],
        *,
        name: Optional[str] = None,
        category: str = "",
        max_captcha_retries: int = 5,
        post_index: int = 0,
    ) -> str:
        self.reset_cancel()
        fill_msg = self.fill_form(
            title, links, name=name, category=category, post_index=post_index,
        )
        page = self.page
        assert page is not None

        if self._has_captcha_field():
            sub = self.submit(auto_captcha=True)
            return f"{fill_msg}\n{sub}"

        if not self._click_first(ZB_SUBMIT_SELECTORS):
            raise RuntimeError("등록(작성) 버튼을 찾을 수 없습니다.")

        page.wait_for_timeout(2000)
        if self._is_submit_success():
            self._capture_post_url()
            return f"{fill_msg}\n글 등록 완료"
        return f"{fill_msg}\n등록 버튼 클릭 — 결과 확인 필요"

    def _is_submit_success(self) -> bool:
        page = self.page
        assert page is not None
        url = page.url.lower()
        if "write.php" not in url:
            return True
        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            return False
        if any(k in body for k in ("등록되었", "작성되었", "글을 등록", "완료")):
            return True
        fail_keywords = ("틀렸", "올바르지", "다시 입력", "일치하지", "incorrect", "오류")
        if any(k in body for k in fail_keywords):
            return False
        return False

    def _capture_post_url(self) -> None:
        page = self.page
        if not page:
            return
        url = page.url
        if "view.php" in url.lower() or "no=" in url.lower():
            self.last_post_url = url
