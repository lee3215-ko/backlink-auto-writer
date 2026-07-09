"""백링크 URL 정규화·페이지 내 링크 존재 확인."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_link_pairs(links: list[Any]) -> list[tuple[str, str]]:
    """AnchorLink 또는 (url, keyword) 튜플을 통일."""
    out: list[tuple[str, str]] = []
    for item in links or []:
        if isinstance(item, tuple) and len(item) >= 2:
            url, kw = str(item[0]).strip(), str(item[1]).strip()
            if url and kw:
                out.append((normalize_backlink_url(url), kw))
        elif hasattr(item, "site_url") and hasattr(item, "keyword"):
            url, kw = str(item.site_url).strip(), str(item.keyword).strip()
            if url and kw:
                out.append((normalize_backlink_url(url), kw))
    return out


def normalize_backlink_url(url: str) -> str:
    """백링크 대상 URL — https 보장, fragment 제거, 호스트 소문자."""
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    parsed = urlparse(u)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    # Netlify/Manus 등 정적 사이트 — 경로만 있고 trailing slash 유지(루트는 /)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    clean = urlunparse((parsed.scheme or "https", host, path, "", parsed.query, ""))
    return clean


def backlink_host_keys(url: str) -> list[str]:
    """페이지 HTML에서 찾을 호스트 후보."""
    parsed = urlparse(normalize_backlink_url(url))
    host = (parsed.netloc or "").lower()
    if not host:
        return []
    keys = [host]
    if host.startswith("www."):
        keys.append(host[4:])
    else:
        keys.append(f"www.{host}")
    return list(dict.fromkeys(keys))


def href_hosts_in_html(html: str) -> set[str]:
    """HTML 내 a[href]에서 호스트 추출."""
    found: set[str] = set()
    for m in re.finditer(r"""href\s*=\s*["']([^"']+)["']""", html, re.I):
        href = m.group(1).strip()
        if not href.startswith(("http://", "https://")):
            continue
        host = urlparse(href).netloc.lower()
        if host:
            found.add(host)
    return found


def page_html_has_backlink(html: str, target_url: str, *, keyword: str = "") -> tuple[bool, str]:
    """
    페이지 HTML에 백링크가 실제로 있는지 확인.
    returns: (found, detail_message)
    """
    if not target_url:
        return False, "대상 URL 없음"

    norm = normalize_backlink_url(target_url)
    hosts = backlink_host_keys(norm)
    low = html.lower()

    for host in hosts:
        pattern = rf'href\s*=\s*["\'][^"\']*{re.escape(host)}'
        if re.search(pattern, html, re.I):
            return True, f"앵커 href ({host})"

    # URL이 괄호 없이 본문에 노출된 경우 (그누보드 자동링크)
    for variant in (norm, norm + "/", norm.rstrip("/")):
        if variant and variant.lower() in low:
            return True, "본문 URL 텍스트"

    if keyword and keyword.strip():
        kw = keyword.strip()
        # 키워드+URL이 가까이 있는지
        idx = low.find(kw.lower())
        if idx >= 0:
            window = low[max(0, idx - 80) : idx + len(kw) + 120]
            if any(h in window for h in hosts) or "http" in window:
                return True, "키워드·URL 근접"

    return False, "백링크 미확인"


def pick_primary_link(links: list[Any], *, post_index: int = 0) -> tuple[str, str]:
    """댓글·홈페이지 필드용 대표 링크 1개."""
    pairs = normalize_link_pairs(links)
    if not pairs:
        return "", ""
    if len(pairs) == 1:
        return pairs[0]
    return pairs[post_index % len(pairs)]
