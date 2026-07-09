"""게시판 URL 유틸."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

def extract_bo_table(url: str) -> str | None:
    """URL에서 bo_table 파라미터 추출 (그누보드)."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    values = qs.get("bo_table")
    return values[0] if values else None


def extract_zboard_id(url: str) -> str | None:
    """제로보드/DQ BBS 게시판 id (write.php?id=… · zboard.php?id=…)."""
    if extract_bo_table(url):
        return None
    parsed = urlparse(url)
    path = parsed.path.lower()
    if not any(x in path for x in ("write.php", "zboard.php", "view.php", "list.php")):
        return None
    qs = parse_qs(parsed.query)
    values = qs.get("id")
    return values[0] if values else None


def is_likely_zeroboard(url: str) -> bool:
    return extract_zboard_id(url) is not None


def zeroboard_write_url(url: str) -> str | None:
    """제로보드 글쓰기 URL로 정규화."""
    zid = extract_zboard_id(url)
    if not zid:
        return None
    parsed = urlparse(url)
    path = parsed.path
    if re.search(r"write\.php", path, re.I):
        return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode({"id": zid}), ""))
    new_path = re.sub(r"(zboard|view|list)\.php", "write.php", path, flags=re.I)
    if new_path == path:
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        new_path = f"{base}/write.php" if base else "/write.php"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", urlencode({"id": zid}), ""))


def extract_sca(url: str) -> str | None:
    """URL에서 분류(sca) 파라미터 추출."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    values = qs.get("sca")
    return values[0] if values else None


def extract_wr_id(url: str) -> str | None:
    """그누보드 글 보기 wr_id."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    values = qs.get("wr_id")
    return values[0] if values else None


def is_gnuboard_view_url(url: str) -> bool:
    return is_likely_gnuboard(url) and bool(extract_wr_id(url))


def gnuboard_view_url(url: str) -> str | None:
    """글 보기 URL 정규화 (bo_table + wr_id 유지)."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None
    bo_table = extract_bo_table(url)
    wr_id = extract_wr_id(url)
    if not bo_table or not wr_id:
        return None
    parsed = urlparse(url)
    path = parsed.path
    if not re.search(r"board\.php", path, re.I):
        return None
    params: dict[str, str] = {"bo_table": bo_table, "wr_id": wr_id}
    sca = extract_sca(url)
    if sca:
        params["sca"] = sca
    return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode(params), ""))


def gnuboard_write_url(url: str) -> str | None:
    """그누보드 board.php URL이면 write.php URL로 변환."""
    bo_table = extract_bo_table(url)
    if not bo_table:
        return None

    parsed = urlparse(url)
    path = parsed.path
    if "board.php" not in path and "write.php" not in path:
        return None

    if "write.php" in path:
        return url

    new_path = path.replace("board.php", "write.php")
    params = {"bo_table": bo_table}
    sca = extract_sca(url)
    if sca:
        params["sca"] = sca
    new_query = urlencode(params)
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", urlencode(params), ""))


def canonical_board_key(url: str) -> str:
    """
    게시판 고유 키 — http/https, www 유무 차이로 같은 사이트가 중복 수집되지 않도록 통일.
    """
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    clean = normalize_board_list_url(url)
    if clean:
        url = clean

    p = urlparse(url)
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    q = parse_qs(p.query, keep_blank_values=False)
    keep: dict[str, str] = {}
    for key in ("bo_table", "sca"):
        if key in q and q[key][0]:
            keep[key] = q[key][0]

    path = p.path or "/"
    path = re.sub(r"write\.php", "board.php", path, flags=re.I)
    path = path.rstrip("/") or "/"
    new_q = urlencode(keep, doseq=False) if keep else ""
    return urlunparse(("http", netloc, path, "", new_q, ""))


def is_likely_gnuboard(url: str) -> bool:
    return extract_bo_table(url) is not None and (
        "board.php" in url or "write.php" in url or "/bbs/" in url
    )


def normalize_board_list_url(url: str) -> str | None:
    """
    게시판 목록 URL로 정리 (wr_id·w 등 제거).
    글 보기 URL이 아닌 board.php?bo_table=... 형태만 반환.
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None
    low = url.lower()
    if any(x in low for x in ("inurl:", "example.com", "print.php", "write_comment")):
        return None
    bo_table = extract_bo_table(url)
    if not bo_table:
        return None
    parsed = urlparse(url)
    path = parsed.path
    if not re.search(r"(?:^|/)board\.php$|/board\.php|/write\.php", path, re.I):
        return None
    new_path = re.sub(r"write\.php", "board.php", path, flags=re.I)
    params: dict[str, str] = {"bo_table": bo_table}
    sca = extract_sca(url)
    if sca:
        params["sca"] = sca
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", urlencode(params), ""))


def url_access_candidates(url: str) -> list[str]:
    """Playwright 접속 후보 — http/https·www 조합 (SSL·리다이렉트 오류 완화)."""
    url = url.strip()
    if not url:
        return []
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        return [url]
    path = parsed.path or "/"
    tail = ""
    if parsed.query:
        tail += "?" + parsed.query
    if parsed.fragment:
        tail += "#" + parsed.fragment
    path_tail = path + tail

    host = parsed.netloc
    host_low = host.lower()
    hosts = [host]
    if host_low.startswith("www."):
        hosts.append(host[4:])
    else:
        hosts.append(f"www.{host}")

    seen: set[str] = set()
    ordered: list[str] = []

    def add(u: str) -> None:
        if u not in seen:
            seen.add(u)
            ordered.append(u)

    add(url)
    for h in hosts:
        add(f"https://{h}{path_tail}")
        add(f"http://{h}{path_tail}")
    return ordered
