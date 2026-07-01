"""수집된 게시판 카탈로그 저장."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from batch_jobs import normalize_board_url

from app_paths import data_file, migrate_legacy_data
from board_url import canonical_board_key

STATUS_LABEL = {
    "compatible": "호환",
    "partial": "부분",
    "login": "로그인",
    "incompatible": "불가",
    "error": "오류",
}


@dataclass
class BoardEntry:
    board_key: str
    board_url: str
    write_url: str
    status: str
    score: int
    signals: dict
    message: str
    source: str
    discovered_at: str
    last_probed_at: str

    @classmethod
    def from_probe(cls, result, *, discovered_at: str | None = None) -> BoardEntry:
        now = result.probed_at
        return cls(
            board_key=result.board_key,
            board_url=result.board_url,
            write_url=result.write_url,
            status=result.status,
            score=result.score,
            signals=result.signals,
            message=result.message,
            source=result.source,
            discovered_at=discovered_at or now,
            last_probed_at=now,
        )


class BoardCatalog:
    def __init__(self, path: Path | None = None) -> None:
        migrate_legacy_data()
        self.path = path or data_file("board_catalog.json")
        self.entries: dict[str, BoardEntry] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.entries = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            merged: dict[str, BoardEntry] = {}
            for e in raw:
                entry = BoardEntry(**e)
                key = canonical_board_key(entry.board_url) or entry.board_key
                entry.board_key = key
                old = merged.get(key)
                if old is None or entry.score > old.score:
                    merged[key] = entry
            self.entries = merged
        except Exception:
            self.entries = {}

    def save(self) -> None:
        data = [asdict(e) for e in self.entries.values()]
        data.sort(key=lambda x: (x["score"], x["last_probed_at"]), reverse=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def has(self, url: str) -> bool:
        key = canonical_board_key(url)
        if not key:
            return False
        return key in self.entries

    def board_keys(self) -> set[str]:
        return set(self.entries.keys())

    def upsert(self, result, *, force: bool = False) -> BoardEntry:
        key = canonical_board_key(result.board_url) or result.board_key or normalize_board_url(result.board_url)
        old = self.entries.get(key)
        if old and not force:
            # 더 좋은 점수만 갱신, 또는 상태가 compatible이면 유지
            if result.score < old.score and old.status == "compatible":
                return old
        entry = BoardEntry.from_probe(
            result,
            discovered_at=old.discovered_at if old else None,
        )
        self.entries[key] = entry
        self.save()
        return entry

    def remove(self, board_key: str) -> None:
        self.entries.pop(board_key, None)
        self.save()

    def clear(self) -> None:
        self.entries = {}
        self.save()

    def list_entries(self, *, status_filter: str | None = None) -> list[BoardEntry]:
        items = list(self.entries.values())
        if status_filter:
            items = [e for e in items if e.status == status_filter]
        items.sort(key=lambda e: (e.score, e.last_probed_at), reverse=True)
        return items

    def compatible_urls(self) -> list[str]:
        return [e.write_url or e.board_url for e in self.list_entries(status_filter="compatible")]

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {k: 0 for k in STATUS_LABEL}
        for e in self.entries.values():
            counts[e.status] = counts.get(e.status, 0) + 1
        counts["total"] = len(self.entries)
        return counts
