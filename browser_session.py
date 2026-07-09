"""Playwright 브라우저 — WAF 회피용 일반 Chrome UA."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FIREFOX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)

# (user_agent, locale) — Chrome 차단 사이트(emilios 등)는 Firefox 우선
STEALTH_PROFILES: list[tuple[str, str]] = [
    (FIREFOX_USER_AGENT, "en-US"),
    (CHROME_USER_AGENT, "ko-KR"),
]

_STEALTH_INIT_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


def chromium_launch_options(*, headless: bool) -> dict:
    from browser_window import chromium_window_args

    return {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            *chromium_window_args(),
        ],
        "ignore_default_args": ["--enable-automation"],
    }


def _stealth_context_options(*, headless: bool, profile_index: int = 0) -> dict:
    idx = profile_index % len(STEALTH_PROFILES)
    ua, locale = STEALTH_PROFILES[idx]
    return {
        "user_agent": ua,
        "locale": locale,
        "viewport": {"width": 1280, "height": 800},
        "ignore_https_errors": True,
        "no_viewport": not headless,
        "extra_http_headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{locale},en;q=0.8",
        },
    }


def launch_stealth_browser(
    playwright: Playwright,
    *,
    headless: bool,
    default_timeout: int = 15000,
    profile_index: int = 0,
) -> tuple[Browser, BrowserContext, Page]:
  """WAF 회피 — Firefox UA 우선, 자동화 플래그 완화."""
  import asyncio

  try:
      asyncio.get_running_loop()
  except RuntimeError:
      pass
  else:
      asyncio.set_event_loop(asyncio.new_event_loop())

  browser = playwright.chromium.launch(**chromium_launch_options(headless=headless))
  context = browser.new_context(**_stealth_context_options(headless=headless, profile_index=profile_index))
  context.add_init_script(_STEALTH_INIT_SCRIPT)
  page = context.new_page()
  page.set_default_timeout(default_timeout)
  page.on("dialog", lambda dialog: dialog.accept())
  return browser, context, page


def close_browser_session(
    browser: Browser | None,
    context: BrowserContext | None,
    playwright: Playwright | None,
) -> None:
  if context:
      try:
          context.close()
      except Exception:
          pass
  if browser:
      try:
          browser.close()
      except Exception:
          pass
  if playwright:
      try:
          playwright.stop()
      except Exception:
          pass
  _reset_playwright_event_loop()


def _reset_playwright_event_loop() -> None:
    """배치 연속 작업 시 sync Playwright 재시작을 위해 스레드 이벤트 루프 초기화."""
    import asyncio

    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass
