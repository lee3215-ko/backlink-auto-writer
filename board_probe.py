"""그누보드 게시판 호환성 프로브 (제출 없이 폼만 검사)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app_logger import log
from batch_jobs import normalize_board_url
from browser_prefs import is_headless
from board_url import gnuboard_write_url, is_likely_gnuboard
from board_writer import (
    CAPTCHA_IMAGE_SELECTORS,
    CAPTCHA_SELECTORS,
    CONTENT_SELECTORS,
    HTML_CHECKBOX_SELECTORS,
    NAME_SELECTORS,
    PASSWORD_SELECTORS,
    SUBMIT_SELECTORS,
    TITLE_SELECTORS,
    WRITE_LINK_PATTERNS,
)
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

LOGIN_HINTS = ("login.php", "로그인 후", "회원만", "권한이 없", "로그인이 필요")


@dataclass
class BoardProbeResult:
    board_url: str
    write_url: str
    board_key: str
    status: str  # compatible | partial | login | incompatible | error
    score: int
    signals: dict = field(default_factory=dict)
    message: str = ""
    source: str = "probe"
    probed_at: str = ""

    def __post_init__(self) -> None:
        if not self.board_key:
            self.board_key = normalize_board_url(self.board_url)
        if not self.probed_at:
            self.probed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class BoardProber:
    """headless Playwright로 글쓰기 폼 호환성 검사."""

    def __init__(self, timeout_ms: int = 12000) -> None:
        self.timeout_ms = timeout_ms
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._launched_headless: bool | None = None

    def close(self) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self.page = None
        self._launched_headless = None

    def _ensure_browser(self) -> None:
        want = is_headless()
        if self.page and not self.page.is_closed() and self._launched_headless == want:
            return
        from browser_session import chromium_launch_options

        self.close()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(**chromium_launch_options(headless=want))
        self._launched_headless = want
        context = self._browser.new_context(no_viewport=not want)
        self.page = context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self.page.on("dialog", lambda dialog: dialog.accept())

    def probe(self, url: str, *, source: str = "probe") -> BoardProbeResult:
        url = url.strip()
        write_url = gnuboard_write_url(url) or url
        board_key = normalize_board_url(url)

        try:
            self._ensure_browser()
            assert self.page is not None
            page = self.page
            page.goto(write_url, wait_until="domcontentloaded")
            page.wait_for_timeout(900)

            if not self._has_write_form():
                self._navigate_to_write_page()

            signals = self._collect_signals()
            status, score, message = self._score(signals, page.url)

            return BoardProbeResult(
                board_url=url,
                write_url=page.url if self._has_write_form() else write_url,
                board_key=board_key,
                status=status,
                score=score,
                signals=signals,
                message=message,
                source=source,
            )
        except Exception as e:
            log.warning("프로브 실패 %s: %s", url, e)
            return BoardProbeResult(
                board_url=url,
                write_url=write_url,
                board_key=board_key,
                status="error",
                score=0,
                signals={},
                message=str(e)[:300],
                source=source,
            )

    def _find_first(self, selectors: list[str]):
        page = self.page
        assert page is not None
        for sel in selectors:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        return None

    def _has_write_form(self) -> bool:
        for selectors in (TITLE_SELECTORS, CONTENT_SELECTORS):
            if self._find_first(selectors):
                return True
        return False

    def _navigate_to_write_page(self) -> None:
        page = self.page
        assert page is not None
        for pattern in WRITE_LINK_PATTERNS:
            link = page.get_by_role("link", name=re.compile(pattern, re.I))
            if link.count() > 0:
                link.first.click()
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(700)
                if self._has_write_form():
                    return
        write_href = page.locator('a[href*="write"], a[href*="board_write"]')
        if write_href.count() > 0:
            write_href.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(700)

    def _collect_signals(self) -> dict:
        page = self.page
        assert page is not None
        body = ""
        try:
            body = page.inner_text("body")[:3000]
        except Exception:
            pass

        has_recaptcha = page.locator('iframe[src*="recaptcha"], .g-recaptcha').count() > 0
        editor = "none"
        if page.locator("textarea[name='wr_content'], textarea#wr_content").count() > 0:
            editor = "textarea"
        elif page.evaluate("() => typeof window.oEditors !== 'undefined'"):
            editor = "smarteditor2"
        elif page.evaluate("() => typeof window.CHEDITOR !== 'undefined'"):
            editor = "cheditor"

        login_hint = any(h in body for h in LOGIN_HINTS) or "login.php" in page.url

        return {
            "has_fwrite": page.locator("#fwrite, form[name='fwrite']").count() > 0,
            "has_name": self._find_first(NAME_SELECTORS) is not None,
            "has_password": self._find_first(PASSWORD_SELECTORS) is not None,
            "has_title": self._find_first(TITLE_SELECTORS) is not None,
            "has_content": self._find_first(CONTENT_SELECTORS) is not None,
            "has_submit": self._find_first(SUBMIT_SELECTORS) is not None,
            "has_numeric_captcha": self._find_first(CAPTCHA_SELECTORS) is not None
            and self._find_first(CAPTCHA_IMAGE_SELECTORS) is not None,
            "has_html_mode": self._find_first(HTML_CHECKBOX_SELECTORS) is not None,
            "has_recaptcha": has_recaptcha,
            "editor": editor,
            "login_hint": login_hint,
            "gnuboard_url": is_likely_gnuboard(page.url),
        }

    def _score(self, signals: dict, final_url: str) -> tuple[str, int, str]:
        if signals.get("login_hint") or "login.php" in final_url:
            return "login", 10, "로그인 필요 또는 회원 전용"

        if not signals.get("has_title") and not signals.get("has_content"):
            return "incompatible", 0, "글쓰기 폼 없음"

        score = 0
        if signals.get("has_fwrite"):
            score += 15
        if signals.get("has_name"):
            score += 15
        if signals.get("has_password"):
            score += 20
        if signals.get("has_title"):
            score += 15
        if signals.get("has_content"):
            score += 15
        if signals.get("has_submit"):
            score += 10
        if signals.get("has_numeric_captcha"):
            score += 10
        if signals.get("has_html_mode"):
            score += 5
        if signals.get("editor") in ("textarea", "smarteditor2", "cheditor"):
            score += 5

        if signals.get("has_recaptcha"):
            return "partial", score, "reCAPTCHA — 자동 캡차 불가"

        required = (
            signals.get("has_password")
            and signals.get("has_title")
            and signals.get("has_content")
            and signals.get("has_submit")
        )
        if required and score >= 70:
            cap = "숫자 캡차" if signals.get("has_numeric_captcha") else "캡차 없음"
            return "compatible", score, f"자동 작성 가능 ({cap}, {signals.get('editor')})"

        missing = []
        if not signals.get("has_password"):
            missing.append("비밀번호")
        if not signals.get("has_title"):
            missing.append("제목")
        if not signals.get("has_content"):
            missing.append("내용")
        if not signals.get("has_submit"):
            missing.append("등록버튼")
        return "partial", score, "일부만 지원: " + ", ".join(missing) if missing else "조건 미충족"
