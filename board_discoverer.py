"""시드 URL에서 그누보드 링크 크롤 + 호환성 프로브 + 연속 수집."""

from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

from app_logger import log
from batch_jobs import normalize_board_url, parse_lines
from board_catalog import BoardCatalog, BoardEntry
from board_probed import ProbedBoardRegistry
from board_probe import BoardProbeResult, BoardProber
from board_search import parse_search_text, search_queries, search_query
from board_auto_search import AutoQueryGenerator
from board_url import extract_bo_table, gnuboard_write_url, is_likely_gnuboard, normalize_board_list_url, canonical_board_key

# HTML/JS에서 그누보드 게시판 링크 추출
BOARD_HREF_RE = re.compile(
    r"""href\s*=\s*["']([^"']*(?:board\.php|write\.php)[^"']*bo_table=[^"']+)["']""",
    re.I,
)
BO_TABLE_RE = re.compile(r"bo_table=([a-zA-Z0-9_]+)", re.I)

SKIP_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".pdf", ".zip", ".css", ".js",
    ".ico", ".svg", ".mp4", ".mp3", ".woff", ".woff2",
)


@dataclass
class DiscovererStats:
    pages_crawled: int = 0
    boards_probed: int = 0
    compatible_found: int = 0
    searches_run: int = 0
    search_boards_found: int = 0
    skipped_duplicate: int = 0
    queue_crawl: int = 0
    queue_probe: int = 0
    cycle_wait_sec: int = 0  # 다음 사이클까지 남은 초 (0이면 작업 중)


class BoardDiscoverer:
    """백그라운드에서 페이지 크롤 → 게시판 프로브 → 카탈로그 저장."""

    def __init__(
        self,
        catalog: BoardCatalog,
        *,
        on_log: Callable[[str], None] | None = None,
        on_entry: Callable[[BoardEntry], None] | None = None,
        on_stats: Callable[[DiscovererStats], None] | None = None,
        on_compatible: Callable[[BoardEntry], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.on_log = on_log or (lambda _m: None)
        self.on_entry = on_entry or (lambda _e: None)
        self.on_stats = on_stats or (lambda _s: None)
        self.on_compatible = on_compatible or (lambda _e: None)

        self._prober = BoardProber()
        self._probed = ProbedBoardRegistry()
        self._running = False
        self._auto_mode = True
        self._catalog_usable_only = True
        self._continuous = True
        self._delay = 1.5
        self._max_depth = 1
        self._max_pages_per_cycle = 15
        self._max_host_crawl = 8
        self._seeds: list[str] = []
        self._search_queries: list[str] = []
        self._search_enabled = True
        self._search_max_results = 25
        self._cycle_interval = 0.0

        self._crawl_queue: deque[tuple[str, int]] = deque()
        self._host_crawl_queue: deque[tuple[str, int]] = deque()
        self._probe_queue: deque[str] = deque()
        self._visited_pages: set[str] = set()
        self._known_boards: set[str] = set()
        self._host_crawled: set[str] = set()
        self._auto_queries = AutoQueryGenerator()
        self.stats = DiscovererStats()

    def configure(
        self,
        *,
        delay: float = 1.5,
        max_depth: int = 1,
        continuous: bool = True,
        max_pages: int = 15,
        search_enabled: bool = True,
        search_max_results: int = 20,
        cycle_interval_min: float = 0.0,
        auto_mode: bool = True,
        catalog_usable_only: bool = True,
    ) -> None:
        self._delay = max(0.5, delay)
        self._max_depth = max(0, max_depth)
        self._continuous = continuous
        self._max_pages_per_cycle = max(5, max_pages)
        self._search_enabled = search_enabled
        self._search_max_results = max(5, min(50, search_max_results))
        self._cycle_interval = max(0.0, float(cycle_interval_min) * 60.0)
        self._auto_mode = auto_mode
        self._catalog_usable_only = catalog_usable_only

    def set_seeds(self, text: str) -> None:
        self._seeds = [u for u in parse_lines(text) if u.startswith(("http://", "https://"))]

    def set_search_queries(self, text: str) -> None:
        self._search_queries = parse_search_text(text)

    def stop(self) -> None:
        self._running = False
        self._log("[수집] 중지 요청")

    def is_running(self) -> bool:
        return self._running

    def _log(self, msg: str) -> None:
        log.info(msg)
        self.on_log(msg)

    def _emit_stats(self) -> None:
        self.stats.queue_crawl = len(self._crawl_queue)
        self.stats.queue_probe = len(self._probe_queue)
        self.on_stats(self.stats)

    def _wait_between_cycles(self) -> None:
        """연속 수집 시 사이클 사이 대기 (중지 요청에 즉시 반응)."""
        total = int(self._cycle_interval)
        if total <= 0:
            return
        self._log(f"[대기] 다음 수집까지 {total // 60}분 {total % 60}초")
        remaining = total
        while remaining > 0 and self._running:
            self.stats.cycle_wait_sec = remaining
            self._emit_stats()
            time.sleep(1.0)
            remaining -= 1
        self.stats.cycle_wait_sec = 0
        self._emit_stats()

    def _normalize_page(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()

    def _same_host(self, a: str, b: str) -> bool:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()

    def _should_crawl(self, url: str) -> bool:
        low = url.lower()
        if not low.startswith(("http://", "https://")):
            return False
        path = urlparse(url).path.lower()
        if re.search(r"\.(css|js|png|jpe?g|gif|pdf|zip|ico|svg|woff2?|mp[34])(\?|$)", path):
            return False
        if any(x in low for x in ("download.do", "apiDetail", "/jobs/", "write_comment")):
            return False
        if re.search(r"\.(rss|xml|webmanifest)(\?|$)", path) or "opensearch" in path:
            return False
        if "/t/" in path and "/bbs/" not in path:
            return False
        if "logout" in low or ("login.php" in low and "write" not in low):
            return False
        return True

    def extract_board_links(self, html: str, base_url: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for m in BOARD_HREF_RE.finditer(html):
            href = m.group(1).replace("&amp;", "&")
            full = urljoin(base_url, href)
            clean = normalize_board_list_url(full)
            if not clean:
                continue
            key = normalize_board_url(clean)
            if key in seen:
                continue
            seen.add(key)
            found.append(clean)
        return found

    def extract_page_links(self, html: str, base_url: str) -> list[str]:
        links: list[str] = []
        host = urlparse(base_url).netloc.lower()
        for m in re.finditer(r"""href\s*=\s*["']([^"'#]+)["']""", html, re.I):
            href = m.group(1).strip()
            if href.startswith(("mailto:", "javascript:", "tel:")):
                continue
            full = urljoin(base_url, href)
            if urlparse(full).netloc.lower() != host:
                continue
            if self._should_crawl(full):
                links.append(full)
        return links

    def _board_key(self, url: str) -> str:
        clean = normalize_board_list_url(url) or url.strip()
        return canonical_board_key(clean)

    def _is_known_board(self, url: str) -> bool:
        key = self._board_key(url)
        return not key or key in self._known_boards

    def _remember_board(self, url: str) -> str:
        key = self._board_key(url)
        if key:
            self._known_boards.add(key)
        return key

    def _enqueue_board(self, url: str, source: str = "crawl") -> bool:
        clean = normalize_board_list_url(url)
        if not clean:
            return False
        key = self._board_key(clean)
        if not key:
            return False
        if self._is_known_board(clean):
            self.stats.skipped_duplicate += 1
            self._remember_board(clean)
            return False
        self._remember_board(clean)
        self._probe_queue.append(clean)
        self._log(f"[대기] 프로브: {clean[:70]}")
        return True

    def _seed_queues(self) -> None:
        self._crawl_queue.clear()
        self._visited_pages.clear()
        for seed in self._seeds:
            norm = self._normalize_page(seed)
            self._crawl_queue.append((seed, 0))
            self._visited_pages.add(norm)
            if is_likely_gnuboard(seed):
                self._enqueue_board(seed, "seed")

    def _run_search_phase(self) -> None:
        if not self._search_enabled or not self._search_queries:
            return
        self._log(f"[검색] {len(self._search_queries)}개 검색어 실행")
        boards, seeds = search_queries(
            self._search_queries,
            max_results=self._search_max_results,
            delay=self._delay,
            on_log=self._log,
            should_stop=lambda: not self._running,
        )
        self.stats.searches_run += len(self._search_queries)
        self.stats.search_boards_found += len(boards)
        probe_before = len(self._probe_queue)
        skipped_known = 0
        for url in boards:
            if not self._enqueue_board(url, "search"):
                skipped_known += 1
        new_probe = len(self._probe_queue) - probe_before
        if boards:
            self._log(
                f"[검색] 신규 {new_probe} · 중복 {skipped_known} / 검색 {len(boards)}"
            )
        for url in seeds:
            norm = self._normalize_page(url)
            if norm in self._visited_pages:
                continue
            self._visited_pages.add(norm)
            self._crawl_queue.append((url, 0))
            self._log(f"[검색시드] {url[:70]}")

    def _seed_known_from_catalog(self) -> None:
        self.catalog.load()
        self._probed.load()
        self._probed.add_many(self.catalog.board_keys())
        for key in self.catalog.board_keys():
            self._known_boards.add(key)
        for key in self._probed.keys:
            self._known_boards.add(key)
        total = len(self._known_boards)
        if total:
            self._log(f"[기억] 카탈로그·이전 검사 {total}개 — 중복 수집 건너뜀")

    def reset_for_run(self) -> None:
        """수집 시작 시 큐·통계 초기화 (카탈로그·프로브 이력은 유지)."""
        self.stats = DiscovererStats()
        self._known_boards.clear()
        self._seed_known_from_catalog()
        self._crawl_queue.clear()
        self._host_crawl_queue.clear()
        self._probe_queue.clear()
        self._visited_pages.clear()
        self._host_crawled.clear()

    def run(self) -> None:
        """메인 루프 — 별도 스레드에서 호출."""
        if self._auto_mode:
            self._run_auto()
            return
        if not self._seeds and not self._search_queries:
            self._log("[수집] 시드 URL 또는 검색어가 필요합니다.")
            return
        self._run_manual()

    def _run_auto(self) -> None:
        """랜덤 키워드 → 검색 → 즉시 프로브 (호환 게시판 위주)."""
        self.reset_for_run()
        self._running = True
        self._log("[자동수집] 시작 — 랜덤 키워드로 호환 게시판 탐색")
        self._log("[자동수집] 검색 1회 → 프로브 → 반복 (쓸모없는 크롤 최소화)")

        host_pages = 0
        try:
            while self._running:
                # 1) 프로브 대기열 우선
                if self._probe_queue:
                    self._probe_one(self._probe_queue.popleft())
                    time.sleep(max(0.5, self._delay * 0.4))
                    self._emit_stats()
                    continue

                # 2) 호환 사이트에서 추가 게시판 탐색
                if self._host_crawl_queue and host_pages < self._max_host_crawl:
                    page_url, depth = self._host_crawl_queue.popleft()
                    self._crawl_one(page_url, depth, host_only=True)
                    host_pages += 1
                    time.sleep(0.4)
                    self._emit_stats()
                    continue
                host_pages = 0

                if not self._continuous and self.stats.searches_run > 0:
                    break

                # 3) 랜덤 검색 1회
                query = self._auto_queries.next_query()
                self.stats.searches_run += 1
                boards, _ = search_query(
                    query,
                    max_results=self._search_max_results,
                    on_log=self._log,
                    include_crawl_seeds=False,
                )
                self.stats.search_boards_found += len(boards)
                added = 0
                skipped = 0
                for url in boards:
                    if self._enqueue_board(url, "auto"):
                        added += 1
                    else:
                        skipped += 1
                if boards:
                    self._log(f"[자동검색] 신규 {added} · 중복 {skipped} / 검색 {len(boards)}")
                elif self.stats.searches_run % 5 == 0:
                    self._log("[자동검색] 결과 없음 — 다른 키워드로 계속 시도")

                # 수동 검색어 보조 (있으면 가끔 섞음)
                if self._search_queries and self._search_enabled and self.stats.searches_run % 8 == 0:
                    self._run_supplement_search()

                self._emit_stats()
                time.sleep(max(1.0, self._delay))

        finally:
            self._running = False
            self._prober.close()
            self._log(
                f"[자동수집] 종료 — 검색 {self.stats.searches_run}회, "
                f"프로브 {self.stats.boards_probed}개, 호환 {self.stats.compatible_found}개"
            )
            self._emit_stats()

    def _run_supplement_search(self) -> None:
        """수동 입력 검색어를 가끔 추가 실행."""
        q = random.choice(self._search_queries)
        self._log(f"[보조검색] {q}")
        boards, _ = search_query(
            q,
            max_results=self._search_max_results,
            on_log=self._log,
            include_crawl_seeds=False,
        )
        for url in boards:
            self._enqueue_board(url, "manual")

    def _run_manual(self) -> None:
        self.reset_for_run()
        self._running = True
        self._seed_queues()
        self._run_search_phase()
        mode = []
        if self._seeds:
            mode.append(f"시드 {len(self._seeds)}")
        if self._search_queries:
            mode.append(f"검색 {len(self._search_queries)}")
        self._log(f"[수집] 시작 — {' · '.join(mode)}, 연속={self._continuous}")

        try:
            pages_this_cycle = 0
            while self._running:
                if self._probe_queue:
                    board_url = self._probe_queue.popleft()
                    self._probe_one(board_url)
                    pages_this_cycle = 0
                    time.sleep(self._delay)
                    self._emit_stats()
                    continue

                if self._crawl_queue and pages_this_cycle < self._max_pages_per_cycle:
                    page_url, depth = self._crawl_queue.popleft()
                    self._crawl_one(page_url, depth)
                    pages_this_cycle += 1
                    time.sleep(self._delay * 0.5)
                    self._emit_stats()
                    continue

                if self._continuous and self._running:
                    self._log("[수집] 한 사이클 완료 — 검색·시드 재시작")
                    self._wait_between_cycles()
                    if not self._running:
                        break
                    self._seed_queues()
                    self._run_search_phase()
                    pages_this_cycle = 0
                    continue

                break

            self._log(
                f"[수집] 종료 — 크롤 {self.stats.pages_crawled}페이지, "
                f"프로브 {self.stats.boards_probed}개, 호환 {self.stats.compatible_found}개"
            )
        finally:
            self._running = False
            self._prober.close()
            self._emit_stats()

    def _schedule_host_crawl(self, board_url: str) -> None:
        """호환 게시판 발견 시 같은 사이트 /bbs/ 추가 탐색."""
        parsed = urlparse(board_url)
        origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if origin in self._host_crawled:
            return
        self._host_crawled.add(origin)
        norm = self._normalize_page(board_url)
        if norm not in self._visited_pages:
            self._visited_pages.add(norm)
            self._host_crawl_queue.append((board_url, 0))
            self._log(f"[확장] 같은 사이트 추가 탐색: {origin}")

    def _probe_one(self, board_url: str, *, save_all: bool = False) -> None:
        self._log(f"[프로브] {board_url[:75]}")
        result = self._prober.probe(board_url, source="auto" if self._auto_mode else "crawl")
        self.stats.boards_probed += 1

        key = self._board_key(board_url)
        if key:
            self._probed.add(key)
            self._remember_board(board_url)

        usable = result.status in ("compatible", "partial")
        if self._catalog_usable_only and not usable and not save_all:
            icon = {"login": "🔒", "incompatible": "✗", "error": "!"}.get(result.status, "?")
            self._log(f"  {icon} [{result.score}점] {result.message} (카탈로그 제외 · 재검사 안 함)")
            return

        entry = self.catalog.upsert(result)
        if result.status == "compatible":
            self.stats.compatible_found += 1
            self._schedule_host_crawl(board_url)
            self.on_compatible(entry)
        icon = {"compatible": "✓", "partial": "~", "login": "🔒", "incompatible": "✗", "error": "!"}.get(
            result.status, "?"
        )
        self._log(f"  {icon} [{result.score}점] {result.message}")
        self.on_entry(entry)

    def _crawl_one(self, page_url: str, depth: int, *, host_only: bool = False) -> None:
        self._log(f"[크롤] d{depth} {page_url[:75]}")
        try:
            self._prober._ensure_browser()
            page = self._prober.page
            assert page is not None
            page.goto(page_url, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(400)
            html = page.content()
            base = page.url

            for board in self.extract_board_links(html, base):
                self._enqueue_board(board)

            if host_only:
                if depth < 1:
                    for link in self.extract_page_links(html, base):
                        if "/bbs/" not in link.lower():
                            continue
                        norm = self._normalize_page(link)
                        if norm in self._visited_pages:
                            continue
                        self._visited_pages.add(norm)
                        self._host_crawl_queue.append((link, depth + 1))
            elif depth < self._max_depth:
                for link in self.extract_page_links(html, base):
                    norm = self._normalize_page(link)
                    if norm in self._visited_pages:
                        continue
                    self._visited_pages.add(norm)
                    self._crawl_queue.append((link, depth + 1))

            self.stats.pages_crawled += 1
        except Exception as e:
            self._log(f"  크롤 실패: {e}")

    def probe_urls_direct(self, urls: list[str]) -> None:
        """시드 없이 URL 목록만 즉시 프로브."""
        self._running = True
        try:
            for url in urls:
                if not self._running:
                    break
                if is_likely_gnuboard(url):
                    self._probe_one(url, save_all=True)
                else:
                    w = gnuboard_write_url(url)
                    if w:
                        self._probe_one(url, save_all=True)
                time.sleep(self._delay)
        finally:
            self._prober.close()
            self._running = False
