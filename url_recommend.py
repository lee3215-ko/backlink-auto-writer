"""URL 자동 추천·정리 — 붙여넣은 주소를 작업에 맞는 형태로 변환."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from batch_jobs import normalize_board_url
from board_url import (
    extract_wr_id,
    gnuboard_view_url,
    gnuboard_write_url,
    is_gnuboard_view_url,
    is_likely_gnuboard,
    is_likely_zeroboard,
    normalize_board_list_url,
    zeroboard_write_url,
)
from target_jobs import _pick_action
from url_analyzer import classify_url

_STRIP_QUERY_KEYS = frozenset({
    "replytocom",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
})


def _ensure_scheme(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _clean_query(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    kept: dict[str, str] = {}
    for key, values in qs.items():
        if key.lower() in _STRIP_QUERY_KEYS:
            continue
        if values and values[0]:
            kept[key] = values[0]
    query = urlencode(kept) if kept else ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))


def _normalize_trailing_slash(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, ""))


def _dedupe_key(url: str) -> str:
    key = normalize_board_url(url)
    if key:
        return key
    parsed = urlparse(url.lower())
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", parsed.query, ""))


def recommend_url(url: str, *, mode: str = "auto") -> tuple[str, str | None]:
    """
    작업 모드에 맞는 추천 URL 반환.
    returns: (recommended_url, change_note or None)
    """
    raw = url.strip()
    if not raw:
        return raw, None

    cleaned = _normalize_trailing_slash(_clean_query(_strip_fragment(_ensure_scheme(raw))))
    analysis = classify_url(cleaned)
    note: str | None = None
    recommended = cleaned

    if is_likely_gnuboard(cleaned):
        if mode == "post":
            recommended = normalize_board_list_url(cleaned) or gnuboard_write_url(cleaned) or cleaned
            if extract_wr_id(cleaned) or "write.php" in cleaned.lower():
                note = "새 글쓰기용 게시판 목록 URL"
        elif mode == "comment" or (mode == "auto" and analysis.kind == "gnuboard_comment"):
            if extract_wr_id(cleaned) or is_gnuboard_view_url(cleaned):
                recommended = gnuboard_view_url(cleaned) or cleaned
                note = "그누보드 글보기(댓글) URL"
        elif mode == "auto" and analysis.support_post:
            recommended = normalize_board_list_url(cleaned) or gnuboard_write_url(cleaned) or cleaned
            if extract_wr_id(cleaned):
                note = "새 글쓰기용 게시판 목록 URL"

    elif is_likely_zeroboard(cleaned) and mode in ("post", "auto"):
        recommended = zeroboard_write_url(cleaned) or cleaned
        if recommended != cleaned:
            note = "제로보드 글쓰기 URL"

    else:
        action = _pick_action(analysis, mode)
        if action == "post":
            if is_likely_zeroboard(cleaned):
                recommended = zeroboard_write_url(cleaned) or cleaned
                note = "제로보드 글쓰기 URL"
        elif action and action.startswith("comment"):
            if analysis.kind == "wordpress_comment":
                note = "워드프레스 댓글 URL" if cleaned != raw else None
            elif analysis.kind == "movable_type_comment":
                note = "MT 댓글 URL" if cleaned != raw else None
            elif cleaned != raw:
                note = "댓글용 URL 정리"
        elif cleaned != raw:
            note = "URL 형식 정리"

    if recommended.rstrip("/") == raw.rstrip("/") and not note:
        return recommended, None
    if recommended == cleaned and cleaned == raw:
        return recommended, None
    return recommended, note


def recommend_urls(urls: list[str], *, mode: str = "auto") -> tuple[list[str], list[str]]:
    """중복 제거·추천 URL 목록과 변경 내역 반환."""
    seen: set[str] = set()
    out: list[str] = []
    changes: list[str] = []

    for original in urls:
        raw = original.strip()
        if not raw:
            continue
        recommended, note = recommend_url(raw, mode=mode)
        if not recommended:
            continue
        key = _dedupe_key(recommended)
        if key in seen:
            changes.append(f"중복 제거: {raw[:60]}")
            continue
        seen.add(key)
        out.append(recommended)
        if note:
            changes.append(f"{note}: {raw[:50]}")
        elif recommended.strip() != raw:
            changes.append(f"정리: {raw[:50]}")

    return out, changes


def recommend_urls_text(text: str, *, mode: str = "auto") -> tuple[str, list[str]]:
    from batch_jobs import parse_lines

    urls = parse_lines(text)
    if not urls:
        return text, []
    new_urls, changes = recommend_urls(urls, mode=mode)
    if not new_urls:
        return "", changes
    return "\n".join(new_urls) + "\n", changes
