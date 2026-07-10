"""URL 유형 분류 — 게시글/댓글 자동화 가능 여부."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from board_url import (
    extract_bo_table,
    extract_wr_id,
    extract_zboard_id,
    gnuboard_write_url,
    is_gnuboard_view_url,
    is_likely_gnuboard,
    is_likely_zeroboard,
    normalize_board_list_url,
)
from forum_url import (
    is_cafe24_board_list,
    is_cafe24_board_read,
    is_generic_comment_url,
    is_guestbook_url,
    is_phpbb_list_url,
    is_phpbb_thread_url,
    is_smf_thread_url,
)

KIND_LABEL = {
    "gnuboard_post": "그누보드 글쓰기",
    "gnuboard_comment": "그누보드 댓글",
    "wordpress_comment": "워드프레스 댓글",
    "movable_type_comment": "Movable Type 댓글",
    "custom_bbs_comment": "커스텀 BBS 댓글",
    "phpbb_comment": "phpBB 답글",
    "smf_comment": "SMF 답글",
    "generic_comment": "범용 댓글/방명록",
    "cafe24_comment": "카페24 댓글",
    "zeroboard_post": "제로보드 글쓰기",
    "phpbb": "phpBB 포럼",
    "xenforo": "XenForo 포럼",
    "blog_other": "일반 블로그/CMS",
    "unknown": "미분류",
}

SUPPORT_LABEL = {
    "post": "게시글 ✓",
    "comment": "댓글 ✓",
    "partial": "부분 가능",
    "no": "불가",
}


@dataclass
class UrlAnalysis:
    url: str
    kind: str
    support_post: bool
    support_comment: bool
    support_level: str  # post | comment | partial | no
    note: str

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def support_label(self) -> str:
        return SUPPORT_LABEL.get(self.support_level, self.support_level)

    def summary_line(self) -> str:
        return f"[{self.support_label}] {self.kind_label} — {self.note}"


def _is_movable_type_url(path: str, low: str) -> bool:
    """구형 MT 블로그 — /2008/04/5.html, archives/post-*.html, mu.nu 등."""
    if any(x in low for x in ("/cgi/mt/", "mt-comments.cgi", "mt-tb.cgi", "/mt/mt-")):
        return True
    if re.search(r"\.mu\.nu/archives/\d+\.(php|html?)", low):
        return True
    if re.search(r"\.mu\.nu/headlines/archives/\d+\.html?", low):
        return True
    if re.search(r"/archives/\d{4}/\d{2}/post[-_]", path, re.I):
        return True
    if re.search(r"/\d{4}/\d{2}/[^/]+\.html?$", path, re.I):
        return True
    if re.search(r"/\d{4}/\d{2}/\d+\.html?$", path, re.I):
        if not any(x in low for x in ("wp-content", "wp-includes", "wordpress")):
            return True
    if re.search(r"\.html?$", path, re.I) and re.search(r"/\d{4}/\d{2}/", path):
        if not any(x in low for x in ("wp-content", "wp-includes", "wordpress")):
            return True
    if re.search(r"post[-_]\d+\.html?", path, re.I) and "/blog/" in path:
        if "wp-content" not in low:
            return True
    return False


def _is_wordpress_host(host: str) -> bool:
    """호스트만으로 WP 블로그로 추정 — blog.example.com 등."""
    host = (host or "").lower().split(":")[0]
    if host.startswith("blog.") or host.startswith("www.blog."):
        return True
    if host.startswith("wordpress.") or host.endswith(".wordpress.com") or host.endswith(".wp.com"):
        return True
    return False


def _is_wordpress_url(url: str, path: str, low: str, host: str = "") -> bool:
    if _is_movable_type_url(path, low):
        return False
    if ".mu.nu/" in low:
        return False
    if any(x in low for x in ("wp-content", "wp-includes", "wp-json", "xmlrpc.php")):
        return True
    if _is_wordpress_host(host or urlparse(url).netloc):
        return True
    segs = [s for s in unquote((path or "").strip("/")).split("/") if s]
    if segs and segs[0].lower() in _WP_CPT_PREFIXES and len(segs) >= 2:
        return True
    if re.search(r"/\d{4}/\d{2}/", path) and not re.search(r"\.html?$", path, re.I):
        return True
    if "?p=" in low or "&p=" in low:
        return True
    if re.search(r"/blog/", path, re.I):
        return True
    if re.search(r"/archives/\d+", path, re.I):
        return True
    if re.search(r"\.html?$", path, re.I) and any(
        x in low for x in ("/blog/", "/post/", "/article/", "post-", "post_")
    ):
        return True
    if _is_likely_wp_post_permalink(path):
        return True
    return False


_WP_STATIC_SLUGS = frozenset({
    "about", "contact", "privacy", "terms", "login", "cart", "shop", "author",
    "category", "tag", "page", "feed", "wp-admin", "wp-login.php",
    "home", "index", "news", "faq", "search", "sitemap",
})

# Avada / 포트폴리오 CPT 등 — 슬러그만으로 WP 글로 인식
_WP_CPT_PREFIXES = frozenset({
    "avada_portfolio", "portfolio", "project", "projects",
    "works", "work", "gallery", "product", "products",
})


def _decoded_path(url: str) -> str:
    return unquote(urlparse(url).path or "")


def _is_custom_bbs_view_url(url: str) -> bool:
    """techbook.co.kr/view?seq= 형태."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "seq" not in qs or not qs["seq"][0].strip():
        return False
    path = _decoded_path(url).lower()
    return path.endswith("/view") or "/view/" in path or path.rstrip("/").endswith("view")


def _is_likely_wp_post_permalink(path: str) -> bool:
    """
    워드프레스 글 고유주소:
    - /rafting-on-the-martha-brae-river/
    - /category/post-slug/  (예: bintangunggas.com)
    - /2024/03/post-slug/
    """
    path = unquote((path or "").strip("/"))
    if not path or "." in path.split("/")[-1]:
        return False
    low = path.lower()
    if any(x in low for x in ("bbs", "board.php", "forum", "memberlist", "showthread")):
        return False
    segments = [s for s in path.split("/") if s]
    if not segments or segments[0].lower() in _WP_STATIC_SLUGS:
        return False
    if not all(re.match(r"^[\w\-]+$", s, re.UNICODE) for s in segments):
        return False
    # /2024/03/slug/
    if (
        len(segments) == 3
        and re.match(r"^\d{4}$", segments[0])
        and re.match(r"^\d{2}$", segments[1])
        and len(segments[2]) >= 3
    ):
        return True
    # /slug/ 또는 /category/slug/ [/sub...] — 마지막 세그먼트가 글 제목형 슬러그
    last = segments[-1]
    if len(last) < 3:
        return False
    if len(segments) == 1:
        return True
    if 2 <= len(segments) <= 4:
        # 짧은 정적형 경로 제외 (/shop/cart 등)
        if any(s.lower() in _WP_STATIC_SLUGS for s in segments):
            return False
        return "-" in last or len(last) >= 12
    return False


def classify_url(url: str) -> UrlAnalysis:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return UrlAnalysis(url, "unknown", False, False, "no", "http(s) URL이 아님")

    parsed = urlparse(url)
    path = _decoded_path(url).lower()
    low = url.lower()
    host = parsed.netloc.lower()

    if is_gnuboard_view_url(url):
        list_hint = normalize_board_list_url(url) or url.split("?")[0] + f"?bo_table={extract_bo_table(url)}"
        return UrlAnalysis(
            url, "gnuboard_comment", False, True, "comment",
            f"글 보기(댓글) — wr_id={extract_wr_id(url)} · 새 글은 목록 URL 사용: {list_hint}",
        )

    if is_likely_gnuboard(url) and extract_bo_table(url):
        if gnuboard_write_url(url):
            write_u = gnuboard_write_url(url)
            return UrlAnalysis(
                url, "gnuboard_post", True, False, "post",
                f"게시판 글쓰기 — write.php ({write_u})",
            )
        return UrlAnalysis(url, "gnuboard_post", True, False, "partial", "그누보드 추정 — URL 확인 필요")

    if is_likely_zeroboard(url):
        zid = extract_zboard_id(url) or "?"
        return UrlAnalysis(
            url, "zeroboard_post", True, False, "post",
            f"제로보드/DQ BBS 글쓰기 (id={zid})",
        )

    if is_phpbb_thread_url(url):
        return UrlAnalysis(
            url, "phpbb_comment", False, True, "comment",
            "phpBB 글 — 게스트 답글 시도 (로그인·캡차 필요할 수 있음)",
        )

    if is_smf_thread_url(url):
        return UrlAnalysis(
            url, "smf_comment", False, True, "comment",
            "SMF 포럼 글 — 게스트 답글 시도",
        )

    if is_generic_comment_url(url) or is_guestbook_url(url):
        return UrlAnalysis(
            url, "generic_comment", False, True, "comment",
            "comment.php / 방명록 — 댓글 시도",
        )

    if is_cafe24_board_read(url):
        return UrlAnalysis(
            url, "cafe24_comment", False, True, "comment",
            "카페24 게시판 글 — 댓글 시도",
        )

    if is_phpbb_list_url(url) or is_cafe24_board_list(url):
        return UrlAnalysis(
            url, "phpbb", False, False, "partial",
            "게시판 목록 페이지 — 글(showthread/viewtopic/read) URL 필요",
        )

    if "/threads/" in path and "viewtopic" not in low:
        return UrlAnalysis(url, "xenforo", False, False, "partial", "XenForo — 회원 로그인 필요할 수 있음")

    if _is_custom_bbs_view_url(url):
        return UrlAnalysis(
            url, "custom_bbs_comment", False, True, "comment",
            "커스텀 BBS 글 보기 — view?seq= 댓글 (HTML 앵커)",
        )

    if _is_movable_type_url(path, low):
        note = "Movable Type 글 — 비회원 댓글 (名前/メール/URL/コメント)"
        if ".mu.nu/archives/" in low:
            note = "mu.nu 블로그 — meep.cgi 댓글 (Name/Email/URL/Comments)"
        return UrlAnalysis(
            url, "movable_type_comment", False, True, "comment",
            note,
        )

    if _is_wordpress_url(url, path, low, host):
        if path in ("", "/") and _is_wordpress_host(host):
            note = "워드프레스 블로그 홈 — 비회원 댓글 (앵커 HTML)"
        else:
            note = "워드프레스 글 — 비회원 댓글 (앵커 HTML)"
        return UrlAnalysis(
            url, "wordpress_comment", False, True, "comment",
            note,
        )

    # 인코딩된 한글 슬러그 WP 글 (lawchat.kr 등)
    slug_path = _decoded_path(url).strip("/")
    if slug_path and "/" not in slug_path and not slug_path.endswith((".php", ".html")):
        if _is_likely_wp_post_permalink("/" + slug_path):
            return UrlAnalysis(
                url, "wordpress_comment", False, True, "comment",
                "워드프레스 글 슬러그 — 비회원 댓글 (앵커 HTML)",
            )

    if any(x in path for x in ("/bbs/", "board.php", "write.php")):
        return UrlAnalysis(url, "blog_other", False, False, "no", "게시판 형태이나 그누보드 아님")

    if any(x in low for x in ("mu.nu/archives", ".php?", "mt-", "/mt_")):
        return UrlAnalysis(url, "blog_other", False, False, "no", "개인 블로그 — 댓글 폼 없거나 비표준")

    if re.search(r"\.(html?|php)$", path, re.I) or "/archives/" in path:
        if "/blog/" in path or "post-" in low or "post_" in low:
            return UrlAnalysis(
                url, "wordpress_comment", False, True, "comment",
                "워드프레스/블로그 글 — 댓글 폼 시도",
            )
        return UrlAnalysis(url, "blog_other", False, False, "no", f"블로그/CMS ({host}) — 자동화 미지원")

    if "memberlist" in low or "mode=joined" in low:
        return UrlAnalysis(url, "unknown", False, False, "no", "회원 목록 페이지 — 글/댓글 대상 아님")

    return UrlAnalysis(url, "unknown", False, False, "no", "지원하지 않는 형식")


def analyze_urls(urls: list[str]) -> list[UrlAnalysis]:
    return [classify_url(u) for u in urls if u.strip()]


def summarize_analyses(items: list[UrlAnalysis]) -> dict[str, int]:
    counts: dict[str, int] = {"post": 0, "comment": 0, "partial": 0, "no": 0}
    for a in items:
        counts[a.support_level] = counts.get(a.support_level, 0) + 1
    return counts


WRITABLE_LEVELS = frozenset({"post", "comment"})


def is_writable(analysis: UrlAnalysis) -> bool:
    return analysis.support_level in WRITABLE_LEVELS


def filter_unsupported(items: list[UrlAnalysis]) -> list[UrlAnalysis]:
    return [a for a in items if not is_writable(a)]


def unsupported_urls(items: list[UrlAnalysis]) -> list[str]:
    return [a.url for a in filter_unsupported(items)]
