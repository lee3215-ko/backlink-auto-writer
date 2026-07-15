"""게시 제외 URL — 이력에서 삭제 시 재게시 방지."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from batch_jobs import normalize_board_url
from app_paths import data_file, migrate_legacy_data


def _host_key(url_or_host: str) -> str:
    text = (url_or_host or "").strip().lower()
    if not text:
        return ""
    if "://" in text or "/" in text:
        try:
            host = (urlparse(text).netloc or "").lower()
        except Exception:
            host = ""
    else:
        host = text
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


class ExcludedUrlRegistry:
    """삭제(제외)된 게시판·글 URL — 배치에서 자동 스킵."""

    def __init__(self, path: Path | None = None) -> None:
        migrate_legacy_data()
        self.path = path or data_file("excluded_urls.json")
        self.keys: set[str] = set()
        self.raw_urls: set[str] = set()
        self.hosts: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.keys = set(raw.get("keys", []))
            self.raw_urls = set(raw.get("raw_urls", []))
            self.hosts = set(raw.get("hosts", []))
        except Exception:
            self.keys = set()
            self.raw_urls = set()
            self.hosts = set()

    def save(self) -> None:
        data = {
            "keys": sorted(self.keys),
            "raw_urls": sorted(self.raw_urls),
            "hosts": sorted(self.hosts),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, board_key: str, urls: set[str] | list[str]) -> None:
        if board_key:
            self.keys.add(board_key)
            host = _host_key(board_key)
            if host:
                self.hosts.add(host)
        for u in urls:
            u = (u or "").strip()
            if not u:
                continue
            self.raw_urls.add(u)
            key = normalize_board_url(u)
            if key:
                self.keys.add(key)
            host = _host_key(u)
            if host:
                self.hosts.add(host)
        self.save()

    def add_host(self, host: str, urls: set[str] | list[str] | None = None) -> None:
        host = _host_key(host)
        if host:
            self.hosts.add(host)
            self.keys.add(host)
        if urls:
            self.add(host, urls)
        else:
            self.save()

    def is_excluded(self, url: str) -> bool:
        url = (url or "").strip()
        if not url:
            return False
        if url in self.raw_urls:
            return True
        key = normalize_board_url(url)
        if key and key in self.keys:
            return True
        host = _host_key(url)
        if host and host in self.hosts:
            return True
        return False

    def count(self) -> int:
        return len(self.keys) + len(self.hosts)
