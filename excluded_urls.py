"""게시 제외 URL — 이력에서 삭제 시 재게시 방지."""

from __future__ import annotations

import json
from pathlib import Path

from batch_jobs import normalize_board_url
from app_paths import data_file, migrate_legacy_data


class ExcludedUrlRegistry:
    """삭제(제외)된 게시판·글 URL — 배치에서 자동 스킵."""

    def __init__(self, path: Path | None = None) -> None:
        migrate_legacy_data()
        self.path = path or data_file("excluded_urls.json")
        self.keys: set[str] = set()
        self.raw_urls: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.keys = set(raw.get("keys", []))
            self.raw_urls = set(raw.get("raw_urls", []))
        except Exception:
            self.keys = set()
            self.raw_urls = set()

    def save(self) -> None:
        data = {"keys": sorted(self.keys), "raw_urls": sorted(self.raw_urls)}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, board_key: str, urls: set[str] | list[str]) -> None:
        if board_key:
            self.keys.add(board_key)
        for u in urls:
            u = (u or "").strip()
            if not u:
                continue
            self.raw_urls.add(u)
            key = normalize_board_url(u)
            if key:
                self.keys.add(key)
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
        return False

    def count(self) -> int:
        return len(self.keys)
