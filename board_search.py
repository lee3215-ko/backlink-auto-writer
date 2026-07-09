"""검색엔진(DuckDuckGo)으로 그누보드 게시판 URL 수집."""

from __future__ import annotations

import re
import time
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from app_logger import log
from batch_jobs import parse_lines
from browser_prefs import is_headless
from board_url import extract_bo_table, is_likely_gnuboard, normalize_board_list_url

# 검색 결과에서 bo_table URL 직접 추출
BO_TABLE_URL_RE = re.compile(
    r"""https?://[^\s"'<>]+(?:board|write)\.php[^\s"'<>]*bo_table=[a-zA-Z0-9_]+[^\s"'<>]*""",
    re.I,
)

SKIP_HOST_PARTS = (
    "google.", "bing.", "yahoo.", "duckduckgo.", "wikipedia.",
    "facebook.", "instagram.", "youtube.", "twitter.", "x.com",
    "naver.com/search", "daum.net/search", "tistory.com/manage",
    "github.com", "microsoft.com",
    "linkedin.com", "embibe.com", "example.com",
    "caddy.community", "stackoverflow.com", "reddit.com",
    "sir.kr", "discourse.",
)

# 검색 시드(사이트 내부 크롤) — 실제 /bbs/ 경로만
CRAWL_SEED_SKIP_IN_PATH = (
    "/t/", "/jobs/", "/search", "/questions/", "/wiki/",
)

# GUI 예시 / 프리셋 (짧은 검색어 — 한글·따옴표 많은 쿼리는 검색엔진에서 실패함)
SEARCH_PRESETS: dict[str, list[str]] = {
    "그누보드 기본": [
        "inurl:board.php bo_table site:kr",
        "inurl:board.php bo_table site:co.kr",
        "inurl:/bbs/board.php bo_table site:kr",
    ],
    "비회원·캡차": [
        "inurl:board.php bo_table wr_password site:kr",
        "inurl:board.php bo_table site:co.kr",
    ],
    "한국 사이트": [
        "inurl:board.php bo_table site:kr",
        "inurl:board.php bo_table site:co.kr",
        "inurl:board.php bo_table site:or.kr",
    ],
    "한국 키워드": [
        "inurl:board.php bo_table free site:kr",
        "inurl:board.php bo_table qna site:co.kr",
        "inurl:board.php bo_table gallery site:kr",
    ],
    "확장": [
        "inurl:board.php bo_table notice site:kr",
        "inurl:board.php bo_table community site:co.kr",
        "inurl:/bbs/board.php bo_table site:kr",
    ],
}


def preset_text(name: str) -> str:
    lines = SEARCH_PRESETS.get(name, [])
    return "\n".join(lines)


def all_preset_lines() -> str:
    lines: list[str] = []
    for qs in SEARCH_PRESETS.values():
        lines.extend(qs)
    return "\n".join(lines)


def _should_skip_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(p in host for p in SKIP_HOST_PARTS)


def board_url_from_hit(url: str) -> str | None:
    """검색 결과 URL을 게시판 board.php URL로 정규화."""
    if _should_skip_host(url):
        return None
    return normalize_board_list_url(url)


def extract_board_urls_from_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in BO_TABLE_URL_RE.finditer(text):
        u = m.group(0).replace("&amp;", "&").rstrip(").,;]")
        norm = board_url_from_hit(u)
        if norm and norm not in seen:
            seen.add(norm)
            found.append(norm)
    return found


def crawl_seed_from_hit(url: str) -> str | None:
    """검색 결과 중 같은 사이트 크롤용 — /bbs/ 실경로만 (포럼·Q&A 제외)."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None
    if _should_skip_host(url):
        return None
    if board_url_from_hit(url):
        return None
    low = url.lower()
    if any(x in low for x in ("login.php", "search?", "javascript:", "apiDetail")):
        return None
    path = urlparse(url).path.lower()
    if "/bbs/" not in path:
        return None
    if any(x in path for x in CRAWL_SEED_SKIP_IN_PATH):
        return None
    return url


def _get_ddgs():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
            return DDGS
        except ImportError:
            return None


def _search_ddgs(query: str, max_results: int, *, retries: int = 2) -> list[dict]:
    DDGS = _get_ddgs()
    if DDGS is None:
        return []

    results: list[dict] = []
    for attempt in range(retries):
        try:
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=max_results, region="kr-kr"):
                    if item.get("href"):
                        results.append(item)
        except Exception as e:
            log.warning("DDGS 검색 실패 (%s, %d/%d): %s", query, attempt + 1, retries, e)
        if results:
            break
        if attempt + 1 < retries:
            time.sleep(4.0 * (attempt + 1))
    return results


def _search_playwright(query: str, max_results: int) -> list[str]:
    """duckduckgo-search 미설치·차단 시 Playwright HTML 검색."""
    from urllib.parse import quote

    from playwright.sync_api import sync_playwright

    urls: list[str] = []
    try:
        from browser_session import chromium_launch_options

        with sync_playwright() as p:
            browser = p.chromium.launch(**chromium_launch_options(headless=is_headless()))
            context = browser.new_context(no_viewport=not is_headless())
            page = context.new_page()
            page.goto(
                f"https://html.duckduckgo.com/html/?q={quote(query)}",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            page.wait_for_timeout(800)
            for a in page.locator("a.result__a").all()[:max_results]:
                href = a.get_attribute("href")
                if href:
                    urls.append(href)
            # 본문에서 bo_table URL 추가 추출
            body = page.content()
            urls.extend(extract_board_urls_from_text(body))
            browser.close()
    except Exception as e:
        log.warning("Playwright 검색 실패 (%s): %s", query, e)
    return urls


def search_query(
    query: str,
    *,
    max_results: int = 25,
    on_log: Callable[[str], None] | None = None,
    include_crawl_seeds: bool = True,
) -> tuple[list[str], list[str]]:
    """
    검색 1회 실행.
    Returns: (board_urls, crawl_seeds)
    """
    query = query.strip()
    if not query:
        return [], []

    def _log(msg: str) -> None:
        log.info(msg)
        if on_log:
            on_log(msg)

    _log(f"  검색: {query}")

    board_urls: list[str] = []
    crawl_seeds: list[str] = []
    seen_b: set[str] = set()
    seen_c: set[str] = set()

    items = _search_ddgs(query, max_results)
    hit_urls: list[str] = []
    snippets: list[str] = []

    if items:
        for it in items:
            hit_urls.append(it.get("href", ""))
            snippets.append(it.get("body", "") or "")
    if not hit_urls:
        _log("  API 검색 결과 없음 → Playwright 검색 시도")
        hit_urls = _search_playwright(query, max_results)

    for snip in snippets:
        for b in extract_board_urls_from_text(snip):
            if b not in seen_b:
                seen_b.add(b)
                board_urls.append(b)

    for raw in hit_urls:
        for b in extract_board_urls_from_text(raw):
            if b not in seen_b:
                seen_b.add(b)
                board_urls.append(b)
        b = board_url_from_hit(raw)
        if b and b not in seen_b:
            seen_b.add(b)
            board_urls.append(b)
        if include_crawl_seeds:
            c = crawl_seed_from_hit(raw)
            if c and c not in seen_c:
                seen_c.add(c)
                crawl_seeds.append(c)

    _log(f"  → 게시판 {len(board_urls)} · 시드 {len(crawl_seeds)}")
    return board_urls, crawl_seeds


def search_queries(
    queries: list[str],
    *,
    max_results: int = 25,
    delay: float = 2.0,
    on_log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[str], list[str]]:
    """여러 검색어 순차 실행."""
    all_boards: list[str] = []
    all_seeds: list[str] = []
    seen_b: set[str] = set()
    seen_c: set[str] = set()

    for q in queries:
        if should_stop and should_stop():
            break
        boards, seeds = search_query(q, max_results=max_results, on_log=on_log)
        for b in boards:
            if b not in seen_b:
                seen_b.add(b)
                all_boards.append(b)
        for s in seeds:
            if s not in seen_c:
                seen_c.add(s)
                all_seeds.append(s)
        time.sleep(max(3.0, delay))

    return all_boards, all_seeds


def parse_search_text(text: str) -> list[str]:
    return parse_lines(text)
