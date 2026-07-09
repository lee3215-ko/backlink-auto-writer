"""게시판 글쓰기 폼 자동 입력 모듈."""

from __future__ import annotations

import re
from typing import Optional

from app_logger import log
from browser_prefs import is_headless
from captcha_solver import is_valid_gnuboard_captcha, solve_numeric_captcha
from board_url import extract_sca, gnuboard_write_url
from editor_content import fill_editor_content
from article_builder import build_article_content, build_article_plain, build_article_title
from form_autofill import autofill_extra_fields
from html_mode import ensure_html_mode
from link_utils import pick_primary_link
from page_guard import page_contains_backlink
from faker import Faker
from urllib.parse import urljoin

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

FIXED_PASSWORD = "zhfldk1234"
faker = Faker("en_US")

# 글쓰기 페이지로 이동할 때 시도할 링크 텍스트/패턴
WRITE_LINK_PATTERNS = [
    "글쓰기",
    "글 작성",
    "쓰기",
    "Write",
    "write",
    "등록",
]

# 이름 필드 셀렉터 (그누보드 등 한국 게시판 공통)
NAME_SELECTORS = [
    '#fwrite input[name="wr_name"]',
    '#fwrite #wr_name',
    'input[name="wr_name"]',
    'input[name="name"]',
    'input[id="wr_name"]',
    'input[placeholder*="이름"]',
]

PASSWORD_SELECTORS = [
    '#fwrite input[name="wr_password"]',
    '#fwrite #wr_password',
    'form[name="fwrite"] input[name="wr_password"]',
    'input[name="wr_password"]',
    'input[id="wr_password"]',
]

TITLE_SELECTORS = [
    '#fwrite input[name="wr_subject"]',
    '#fwrite #wr_subject',
    'input[name="wr_subject"]',
    'input[id="wr_subject"]',
    'input[placeholder*="제목"]',
]

CONTENT_SELECTORS = [
    '#fwrite textarea#wr_content',
    '#fwrite textarea[name="wr_content"]',
    'textarea#wr_content',
    'textarea[name="wr_content"]',
]

HTML_CHECKBOX_SELECTORS = [
    'input[name="html"]',
    'input[name="wr_html"]',
    'input[id="html"]',
    'input[type="checkbox"][value="html"]',
    'label[for="html"]',
    'label:has-text("html") input[type="checkbox"]',
    'label:has-text("HTML") input[type="checkbox"]',
]

CAPTCHA_SELECTORS = [
    'input[name="captcha_key"]',
    'input[name="wr_key"]',
    'input[name="kcaptcha"]',
    'input[id="captcha_key"]',
    'input[placeholder*="자동등록"]',
    'input[title*="자동등록"]',
]

CAPTCHA_IMAGE_SELECTORS = [
    "#captcha_img",
    'img[src*="kcaptcha"]',
    'img[src*="captcha"]',
    'img[alt*="자동등록"]',
    'img[id*="captcha"]',
]

CAPTCHA_REFRESH_SELECTORS = [
    "#captcha_reload",
    'button[id*="captcha"]',
    'a[id*="captcha"]',
    'img[src*="captcha"] ~ button',
]

SUBMIT_SELECTORS = [
    'input[type="submit"][value*="작성완료"]',
    'button:has-text("작성완료")',
    'input[type="submit"][value*="등록"]',
    'button:has-text("등록")',
    'input[type="submit"][value*="확인"]',
    'button[type="submit"]',
    'input[type="submit"]',
]


def random_english_name() -> str:
    """영어 랜덤 이름 생성."""
    return faker.name()


def build_anchor_content(
    links: list[tuple[str, str]] | tuple[str, str],
    keyword: str = "",
    *,
    post_index: int = 0,
) -> str:
    """앵커가 분산된 HTML 원고 생성."""
    if isinstance(links, tuple):
        pairs: list[tuple[str, str]] = [(links[0], keyword)]
    else:
        pairs = list(links)
    return build_article_content(pairs, post_index=post_index)


class BoardWriter:
  """Playwright 기반 게시판 글쓰기 자동화."""

  def __init__(self) -> None:
      self._playwright: Optional[Playwright] = None
      self._browser: Optional[Browser] = None
      self._browser_context = None
      self.page: Optional[Page] = None
      self.last_name: str = ""
      self._cancelled: bool = False
      self._source_url: str = ""
      self._stealth_profile_index: int = 0
      self.last_list_url: str = ""
      self.last_write_url: str = ""
      self.last_post_url: str = ""

  def cancel(self) -> None:
      self._cancelled = True
      log.info("작업 취소 요청")

  def reset_cancel(self) -> None:
      self._cancelled = False
  def is_open(self) -> bool:
      return self.page is not None and not self.page.is_closed()

  def open_browser(self, url: str) -> str:
      """브라우저를 열고 URL로 이동. 글쓰기 폼이 아니면 글쓰기 링크 탐색."""
      self.reset_cancel()
      self._launch_stealth_page(default_timeout=15000)

      target = gnuboard_write_url(url) or url
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
              "글쓰기 폼을 찾을 수 없습니다. 글쓰기 페이지 URL을 직접 입력해 보세요."
          )

      self.last_write_url = self.page.url if self.page else target
      return "브라우저가 열렸습니다."

  def fill_form(
      self,
      title: str,
      links: list[tuple[str, str]],
      *,
      name: Optional[str] = None,
      category: str = "",
      post_index: int = 0,
  ) -> str:
      """글쓰기 폼 자동 입력 (캡차 제외). links = [(site_url, keyword), ...]"""
      if not self.is_open():
          raise RuntimeError("브라우저가 열려 있지 않습니다.")
      if not links:
          raise ValueError("링크(사이트·키워드)가 비어 있습니다.")

      page = self.page
      assert page is not None

      self.last_name = name or random_english_name()
      content_html = build_article_content(links, post_index=post_index)
      primary_site = links[0][0]

      self._fill_first(NAME_SELECTORS, self.last_name)
      self._fill_first(PASSWORD_SELECTORS, FIXED_PASSWORD)

      extra_logs = autofill_extra_fields(
          page,
          site_url=primary_site,
          category=category or extract_sca(self._source_url) or "",
      )
      extra_note = ""
      if extra_logs:
          extra_note = " | 자동필드: " + ", ".join(extra_logs[:3])
          if len(extra_logs) > 3:
              extra_note += f" 외 {len(extra_logs) - 3}건"

      html_result = self._ensure_html_mode()
      content = content_html if html_result.enabled else build_article_plain(links, post_index=post_index)
      self._fill_first(TITLE_SELECTORS, title)
      self._fill_content(content)
      # 본문 입력 후 HTML html1 재확인 (체크만 되고 value 비어있는 경우 방지)
      html_after = self._ensure_html_mode()
      if html_after.enabled and not html_result.enabled:
          html_result = html_after

      html_note = ""
      if html_result.enabled:
          html_note = f" | HTML: {html_result.message}"
      elif html_result.message:
          html_note = f" | {html_result.message}"

      return f"양식 입력 완료 (이름: {self.last_name}{extra_note}{html_note} · 원고 {len(content)}자)"

  def solve_captcha(self) -> tuple[str, str]:
      """페이지 캡차 이미지를 OCR로 인식. (코드, 상세로그)"""
      if not self.is_open():
          raise RuntimeError("브라우저가 열려 있지 않습니다.")

      image_bytes = self._capture_captcha_image()
      code, detail = solve_numeric_captcha(image_bytes)
      log.info("캡차 OCR: %s (%s)", code, detail)

      if not is_valid_gnuboard_captcha(code):
          raise RuntimeError(f"캡차 인식 실패 (결과: {code!r}, {detail})")
      return code, detail

  def submit(self, captcha: Optional[str] = None, *, auto_captcha: bool = False) -> str:
      """캡차 입력 후 작성완료 클릭. auto_captcha=True면 OCR 자동 인식."""
      if not self.is_open():
          raise RuntimeError("브라우저가 열려 있지 않습니다.")

      page = self.page
      assert page is not None

      if auto_captcha or not (captcha or "").strip():
          captcha, detail = self.solve_captcha()
          log.info("자동 캡차 사용: %s", captcha)
      else:
          captcha = captcha.strip()
          log.info("수동 캡차 사용: %s", captcha)

      if not captcha:
          raise ValueError("자동등록방지 숫자를 입력해 주세요.")

      filled = self._fill_first(CAPTCHA_SELECTORS, captcha)
      if not filled:
          raise RuntimeError("자동등록방지 입력 필드를 찾을 수 없습니다.")

      page.wait_for_timeout(200)
      clicked = self._click_submit()
      if not clicked:
          raise RuntimeError("작성완료 버튼을 찾을 수 없습니다.")

      page.wait_for_timeout(1200)
      if self._is_submit_success():
          self._capture_post_url()
          return f"글 등록 완료 (캡차: {captcha})"
      return f"작성완료 클릭 (캡차: {captcha}) — 결과 확인 필요"

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
      """양식 입력 + 캡차 자동 인식 + 제출 (원클릭)."""
      self.reset_cancel()
      fill_msg = self.fill_form(
          title, links, name=name, category=category, post_index=post_index,
      )

      page = self.page
      assert page is not None
      last_error = ""
      ocr_logs: list[str] = []

      for attempt in range(1, max_captcha_retries + 1):
          if self._cancelled:
              raise RuntimeError("작업이 취소되었습니다. 수동 작성완료를 사용하세요.")

          try:
              code, detail = self.solve_captcha()
              ocr_logs.append(f"시도{attempt}: {code} ({detail})")
              log.info("캡차 시도 %d: %s", attempt, detail)

              self._fill_first(CAPTCHA_SELECTORS, code)
              page.wait_for_timeout(200)
              if not self._click_submit():
                  raise RuntimeError("작성완료 버튼을 찾을 수 없습니다.")

              page.wait_for_timeout(1200)
              if self._is_submit_success():
                  self._capture_post_url()
                  bl_note = self._verify_post_backlink(links, post_index=post_index)
                  return (
                      f"{fill_msg}\n캡차 자동 성공 ({code}) → 글 등록 완료{bl_note}\n"
                      + "\n".join(ocr_logs)
                  )

              last_error = f"캡차 오답 추정 (시도 {attempt}/{max_captcha_retries})"
              log.warning(last_error)
              self._refresh_captcha()
              page.wait_for_timeout(500)
          except Exception as e:
              last_error = str(e)
              log.warning("캡차 시도 %d 실패: %s", attempt, e)
              self._refresh_captcha()
              page.wait_for_timeout(500)

      log.error("캡차 자동 실패: %s | %s", last_error, ocr_logs)
      raise RuntimeError(
          f"캡차 자동 인식 실패 ({max_captcha_retries}회): {last_error}\n"
          "아래에 숫자를 직접 입력 후 '수동 작성완료'를 눌러 주세요.\n"
          + "\n".join(ocr_logs)
      )

  def _dismiss_cookie_banners(self) -> None:
      page = self.page
      if not page:
          return
      for sel in (
          'button:has-text("Accept")',
          'button:has-text("Got it")',
          'button:has-text("동의")',
          'button:has-text("同意")',
          ".cc-dismiss",
      ):
          try:
              loc = page.locator(sel)
              if loc.count() > 0 and loc.first.is_visible():
                  loc.first.click(timeout=2000)
                  page.wait_for_timeout(400)
                  return
          except Exception:
              pass

  def close(self) -> None:
      """브라우저 및 Playwright 리소스 정리."""
      from browser_session import close_browser_session

      close_browser_session(self._browser, self._browser_context, self._playwright)
      self._browser = None
      self._browser_context = None
      self._playwright = None
      self.page = None

  def _launch_stealth_page(self, *, default_timeout: int = 15000, profile_index: int | None = None) -> None:
      """WAF 회피용 브라우저 시작 (Firefox UA 우선)."""
      from browser_session import launch_stealth_browser

      if profile_index is not None:
          self._stealth_profile_index = profile_index
      self.close()
      self._playwright = sync_playwright().start()
      self._browser, self._browser_context, self.page = launch_stealth_browser(
          self._playwright,
          headless=is_headless(),
          default_timeout=default_timeout,
          profile_index=self._stealth_profile_index,
      )

  def _is_waf_blocked_page(self) -> bool:
      page = self.page
      if not page:
          return False
      try:
          title = (page.title() or "").lower()
      except Exception:
          return False
      if any(x in title for x in ("403", "forbidden", "access denied")):
          return True
      try:
          body = page.locator("body").inner_text(timeout=2000).lower()[:800]
          if "403 forbidden" in body or "access denied" in body:
              return True
      except Exception:
          pass
      return False

  def _relaunch_alternate_stealth_profile(self, *, default_timeout: int = 15000) -> None:
      """403 등 차단 시 다른 User-Agent 프로필로 재시작."""
      from browser_session import STEALTH_PROFILES

      self._stealth_profile_index = (self._stealth_profile_index + 1) % len(STEALTH_PROFILES)
      log.info("UA 프로필 전환 (%d/%d)", self._stealth_profile_index + 1, len(STEALTH_PROFILES))
      self._launch_stealth_page(default_timeout=default_timeout, profile_index=self._stealth_profile_index)

  def _has_write_form(self) -> bool:
      page = self.page
      assert page is not None
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
              page.wait_for_timeout(800)
              if self._has_write_form():
                  return

      for pattern in WRITE_LINK_PATTERNS:
          btn = page.get_by_role("button", name=re.compile(pattern, re.I))
          if btn.count() > 0:
              btn.first.click()
              page.wait_for_load_state("domcontentloaded")
              page.wait_for_timeout(800)
              if self._has_write_form():
                  return

      # href에 write 포함 링크
      write_href = page.locator('a[href*="write"], a[href*="board_write"]')
      if write_href.count() > 0:
          write_href.first.click()
          page.wait_for_load_state("domcontentloaded")
          page.wait_for_timeout(800)

  def _find_first(self, selectors: list[str]):
      page = self.page
      assert page is not None
      for sel in selectors:
          loc = page.locator(sel)
          if loc.count() > 0 and loc.first.is_visible():
              return loc.first
      return None

  def _fill_first(self, selectors: list[str], value: str) -> bool:
      el = self._find_first(selectors)
      if not el:
          return False
      el.click()
      el.fill("")
      el.fill(value)
      return True

  def _fill_content(self, content: str) -> None:
      page = self.page
      assert page is not None

      ok, method = fill_editor_content(page, content)
      if ok:
          log.info("본문 입력 방식: %s", method)
          return

      raise RuntimeError("내용 입력 영역을 찾을 수 없습니다. (SmartEditor/textarea 미지원)")

  def _ensure_html_mode(self):
      page = self.page
      assert page is not None
      return ensure_html_mode(page)

  def _check_html_if_present(self) -> None:
      self._ensure_html_mode()

  def _click_first(self, selectors: list[str]) -> bool:
      el = self._find_first(selectors)
      if not el:
          return False
      el.click()
      return True

  def _click_submit(self) -> bool:
      """작성완료 클릭 — 페이지 이동 대기 없이 (GUI 멈춤 방지)."""
      page = self.page
      assert page is not None

      for sel in SUBMIT_SELECTORS:
          loc = page.locator(sel)
          if loc.count() == 0:
              continue
          el = loc.first
          try:
              if not el.is_visible():
                  continue
              el.click(timeout=5000, no_wait_after=True)
              log.info("제출 버튼 클릭: %s", sel)
              return True
          except Exception as e:
              log.debug("제출 클릭 실패 %s: %s", sel, e)
              continue
      return False

  def _capture_captcha_image(self) -> bytes:
      page = self.page
      assert page is not None

      for sel in CAPTCHA_IMAGE_SELECTORS:
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
                      log.debug("캡차 이미지 URL 다운로드: %s", full_url)
                      return resp.body()
              return img.screenshot()
          except Exception as e:
              log.debug("캡차 캡처 실패 %s: %s", sel, e)
              continue

      raise RuntimeError("자동등록방지 이미지를 찾을 수 없습니다.")

  def _refresh_captcha(self) -> None:
      page = self.page
      assert page is not None

      for sel in CAPTCHA_REFRESH_SELECTORS:
          loc = page.locator(sel)
          if loc.count() > 0 and loc.first.is_visible():
              loc.first.click()
              return

      img = self._find_first(CAPTCHA_IMAGE_SELECTORS)
      if img:
          img.click()

  def _is_submit_success(self) -> bool:
      page = self.page
      assert page is not None

      url = page.url.lower()
      if "write.php" not in url:
          return True

      body_text = ""
      try:
          body_text = page.locator("body").inner_text(timeout=3000)
      except Exception:
          pass

      fail_keywords = ("틀렸", "올바르지", "다시 입력", "일치하지", "incorrect")
      if any(k in body_text for k in fail_keywords):
          return False

      # write.php 유지 + 오류 없음 → 실패로 간주하고 재시도
      return False

  def _capture_post_url(self) -> None:
      page = self.page
      if not page:
          return
      url = page.url
      if "wr_id=" in url.lower():
          self.last_post_url = url

  def _verify_post_backlink(
      self,
      links: list[tuple[str, str]],
      *,
      post_index: int = 0,
  ) -> str:
      """등록된 글 페이지에서 백링크 href 확인."""
      page = self.page
      if not page or not self.last_post_url:
          return ""
      target_url, keyword = pick_primary_link(links, post_index=post_index)
      if not target_url:
          return ""
      try:
          page.goto(self.last_post_url, wait_until="domcontentloaded", timeout=15000)
          page.wait_for_timeout(1200)
          found, detail = page_contains_backlink(page, target_url, keyword=keyword)
          if found:
              return f" — 백링크 확인 ({detail})"
          return " — 백링크 미확인 (HTML 모드·게시판 필터 확인)"
      except Exception as e:
          log.debug("백링크 검증 스킵: %s", e)
          return ""
