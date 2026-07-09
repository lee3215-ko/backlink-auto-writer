"""포럼·방명록 URL 판별·정리."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def _path(url: str) -> str:
    return (urlparse(url).path or "").lower()


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=False)


def is_phpbb_thread_url(url: str) -> bool:
    low = url.lower()
    if "viewtopic.php" in low:
        return True
    if "showthread.php" in low:
        return True
    if re.search(r"showthread\.php", low) and any(k in low for k in ("t=", "tid=", "p=", "topic=")):
        return True
    return False


def is_phpbb_list_url(url: str) -> bool:
    low = url.lower()
    if is_phpbb_thread_url(url):
        return False
    return any(x in low for x in ("viewforum.php", "forumdisplay.php", "forumdisplay"))


def is_smf_thread_url(url: str) -> bool:
    low = url.lower()
    if "index.php" not in low:
        return False
    qs = _query(url)
    if "topic" in qs and qs["topic"][0].replace(".", "").isdigit():
        return True
    if re.search(r"index\.php\?[^#]*topic=\d+", low):
        return True
    return False


def is_generic_comment_url(url: str) -> bool:
    path = _path(url)
    low = url.lower()
    if path.endswith("comment.php") or "/comment.php" in path:
        return True
    if "add-comment.php" in path:
        return True
    if re.search(r"comment\.php/\d+", low):
        return True
    if "comment.php?" in low and "gb_id" in low:
        return True
    return False


def is_guestbook_url(url: str) -> bool:
    low = url.lower()
    path = _path(url)
    if "guestbook" not in low:
        return False
    return any(x in low for x in ("comment", "gb_id", "guestbook.html", "guestbook/", "gb.php"))


def is_cafe24_board_read(url: str) -> bool:
    low = url.lower()
    return "cafe24.com" in low and "/board/" in low and "/read.html" in low


def is_cafe24_board_list(url: str) -> bool:
    low = url.lower()
    return "cafe24.com" in low and "/board/" in low and "/list.html" in low


def phpbb_reply_url(url: str) -> str:
    """phpBB 답글 작성 URL 추정."""
    parsed = urlparse(url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for drop in ("p", "page", "start", "view", "sid", "highlight"):
        qs.pop(drop, None)
    qs["mode"] = ["reply"]
    query = urlencode({k: v[0] for k, v in qs.items() if v}, doseq=False)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))


def smf_reply_url(url: str) -> str:
    parsed = urlparse(url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=True)
    topic = (qs.get("topic") or [""])[0]
    if not topic:
        return url
    base = topic.split(".")[0]
    qs["action"] = ["post"]
    qs["topic"] = [f"{base}.0"]
    query = urlencode({k: v[0] for k, v in qs.items() if v}, doseq=False)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))
