"""게시 이력 저장·조회."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from batch_jobs import AnchorLink, normalize_board_url

from app_paths import data_file, migrate_legacy_data


@dataclass
class PostRecord:
    board_url: str
    board_key: str
    title: str
    links: list[dict]
    status: str  # success | fail
    message: str
    timestamp: str
    set_index: int = 0
    list_url: str = ""
    write_url: str = ""
    post_url: str = ""

    @classmethod
    def from_job(
        cls,
        board_url: str,
        title: str,
        links: list[AnchorLink],
        *,
        status: str,
        message: str,
        set_index: int = 0,
        list_url: str = "",
        write_url: str = "",
        post_url: str = "",
    ) -> PostRecord:
        list_url = list_url or board_url
        return cls(
            board_url=board_url,
            board_key=normalize_board_url(list_url),
            title=title,
            links=[
                {"site_url": l.site_url, "keyword": l.keyword, "set_index": getattr(l, "set_index", 0)}
                for l in links
            ],
            status=status,
            message=message[:500],
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            set_index=set_index,
            list_url=list_url,
            write_url=write_url,
            post_url=post_url,
        )


@dataclass
class BoardSummary:
    board_key: str
    board_url: str
    post_count: int
    success_count: int
    last_at: str
    link_stats: list[tuple[str, str, int]]  # site, keyword, count


class PostHistory:
    def __init__(self, path: Path | None = None) -> None:
        migrate_legacy_data()
        self.path = path or data_file("post_history.json")
        self.records: list[PostRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.records = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = []
            for r in raw:
                if "list_url" not in r:
                    r = {
                        **r,
                        "list_url": r.get("board_url", ""),
                        "write_url": r.get("write_url", ""),
                        "post_url": r.get("post_url", ""),
                    }
                self.records.append(PostRecord(**r))
        except Exception:
            self.records = []

    def save(self) -> None:
        data = [asdict(r) for r in self.records]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, record: PostRecord) -> None:
        self.records.append(record)
        self.save()

    def clear(self) -> None:
        self.records = []
        self.save()

    def get_summaries(self) -> list[BoardSummary]:
        grouped: dict[str, list[PostRecord]] = defaultdict(list)
        for r in self.records:
            grouped[r.board_key].append(r)

        summaries: list[BoardSummary] = []
        for key, recs in grouped.items():
            success = [r for r in recs if r.status == "success"]
            counter: Counter[tuple[str, str]] = Counter()
            for r in recs:
                if r.status != "success":
                    continue
                for link in r.links:
                    counter[(link["site_url"], link["keyword"])] += 1

            last_at = max(r.timestamp for r in recs)
            latest = recs[-1]
            latest_url = latest.post_url or latest.list_url or latest.board_url
            summaries.append(
                BoardSummary(
                    board_key=key,
                    board_url=latest_url,
                    post_count=len(recs),
                    success_count=len(success),
                    last_at=last_at,
                    link_stats=sorted(counter.items(), key=lambda x: -x[1]),
                )
            )

        summaries.sort(key=lambda s: s.last_at, reverse=True)
        return summaries

    def get_records_for_board(self, board_key: str) -> list[PostRecord]:
        return [r for r in self.records if r.board_key == board_key]

    def format_detail(self, board_key: str) -> str:
        recs = self.get_records_for_board(board_key)
        if not recs:
            return "기록 없음"

        lines = [f"게시판: {board_key}", f"총 {len(recs)}회 등록 시도", ""]
        counter: Counter[tuple[str, str]] = Counter()
        for r in recs:
            if r.status == "success":
                for link in r.links:
                    counter[(link["site_url"], link["keyword"])] += 1

        lines.append("=== 사이트·키워드별 등록 횟수 (성공) ===")
        board_by_link: dict[tuple[str, str], tuple[str, str, str]] = {}
        for r in recs:
            if r.status != "success":
                continue
            for link in r.links:
                key = (link["site_url"], link["keyword"])
                board_by_link[key] = (r.list_url, r.write_url, r.post_url)
        for (site, kw), cnt in sorted(counter.items(), key=lambda x: -x[1]):
            lines.append(f"  [{cnt}회] {kw}  →  {site}")
            lst, wr, post = board_by_link.get((site, kw), ("", "", ""))
            if lst:
                lines.append(f"         목록: {lst}")
            if wr:
                lines.append(f"         글쓰기: {wr}")
            if post:
                lines.append(f"         등록글: {post}")

        lines.append("")
        lines.append("=== 세트별 등록 횟수 ===")
        set_counter: Counter[tuple[int, str, str]] = Counter()
        for r in recs:
            if r.status != "success":
                continue
            for link in r.links:
                si = link.get("set_index", 0)
                set_counter[(si, link["keyword"], link["site_url"])] += 1
        for (si, kw, site), cnt in sorted(set_counter.items(), key=lambda x: (-x[1], x[0][0])):
            lines.append(f"  세트{si} [{cnt}회] {kw} → {site[:50]}")
            for r in reversed(recs):
                if r.status != "success":
                    continue
                if not any(
                    lnk.get("set_index", 0) == si and lnk["keyword"] == kw and lnk["site_url"] == site
                    for lnk in r.links
                ):
                    continue
                if r.list_url:
                    lines.append(f"           목록: {r.list_url}")
                if r.write_url:
                    lines.append(f"           글쓰기: {r.write_url}")
                if r.post_url:
                    lines.append(f"           등록글: {r.post_url}")
                break

        lines.append("")
        lines.append("=== 등록 이력 ===")
        for r in reversed(recs[-50:]):
            icon = "✓" if r.status == "success" else "✗"
            picks = " | ".join(
                f"세트{lnk.get('set_index','?')}:{lnk['keyword']}" for lnk in r.links
            )
            lines.append(f"{icon} {r.timestamp}  [{picks}]")
            if r.list_url:
                lines.append(f"    목록: {r.list_url}")
            if r.write_url:
                lines.append(f"    글쓰기: {r.write_url}")
            if r.post_url:
                lines.append(f"    등록글: {r.post_url}")
            elif not r.list_url and r.board_url:
                lines.append(f"    URL: {r.board_url}")
            if r.status == "fail":
                lines.append(f"    사유: {r.message[:120]}")

        return "\n".join(lines)
