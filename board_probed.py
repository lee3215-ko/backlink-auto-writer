"""이미 프로브한 게시판 키 — 호환·비호환 모두 기억해 재수집 방지."""

from __future__ import annotations

import json
from pathlib import Path

from app_paths import data_file, migrate_legacy_data
from board_url import canonical_board_key

_MAX_KEYS = 50_000


class ProbedBoardRegistry:
    def __init__(self, path: Path | None = None) -> None:
        migrate_legacy_data()
        self.path = path or data_file("board_probed.json")
        self.keys: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.keys = set()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self.keys = {canonical_board_key(str(k)) or str(k) for k in raw if k}
            else:
                self.keys = set()
        except Exception:
            self.keys = set()

    def save(self) -> None:
        data = sorted(self.keys)[-_MAX_KEYS:]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")

    def has(self, key: str) -> bool:
        return bool(key) and key in self.keys

    def add(self, key: str) -> None:
        if not key:
            return
        if key in self.keys:
            return
        self.keys.add(key)
        if len(self.keys) > _MAX_KEYS:
            self.keys = set(sorted(self.keys)[-_MAX_KEYS:])
        self.save()

    def add_many(self, keys: set[str]) -> None:
        changed = False
        for key in keys:
            if key and key not in self.keys:
                self.keys.add(key)
                changed = True
        if changed:
            if len(self.keys) > _MAX_KEYS:
                self.keys = set(sorted(self.keys)[-_MAX_KEYS:])
            self.save()
