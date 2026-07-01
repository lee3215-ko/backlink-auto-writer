"""다량 URL / 콘텐츠 세트 배치 작업 구성."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from article_builder import build_article_content, build_article_title, plain_text_from_html

_URL_RE = re.compile(r"^https?://", re.I)


@dataclass
class AnchorLink:
    site_url: str
    keyword: str
    set_index: int = 0


@dataclass
class ContentSet:
    """URL 1개 + 키워드 여러 개. 게시물마다 키워드 1개만 선택."""

    index: int
    site_url: str
    keywords: list[str] = field(default_factory=list)

    def pick_keyword(self, post_index: int) -> str:
        if not self.keywords:
            raise ValueError(f"세트 {self.index}: 키워드가 없습니다.")
        return self.keywords[post_index % len(self.keywords)]

    @property
    def summary(self) -> str:
        kws = ", ".join(self.keywords[:4])
        if len(self.keywords) > 4:
            kws += f" 외{len(self.keywords) - 4}"
        return f"{self.site_url[:45]} → [{kws}]"


@dataclass
class PostJob:
    index: int
    total: int
    board_url: str
    title: str
    links: list[AnchorLink]
    category: str = ""

    @property
    def label(self) -> str:
        return f"[{self.index}/{self.total}] {self.board_url[:60]}"

    @property
    def primary_site(self) -> str:
        return self.links[0].site_url if self.links else ""

    @property
    def picks_summary(self) -> str:
        return " | ".join(f"세트{l.set_index}:{l.keyword}" for l in self.links)


def parse_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def normalize_board_url(url: str) -> str:
    from board_url import canonical_board_key

    return canonical_board_key(url)


def validate_content_sets(sets: list[ContentSet]) -> None:
    if not sets:
        raise ValueError("콘텐츠 세트를 1개 이상 추가해 주세요.")
    for s in sets:
        if not s.site_url.strip():
            raise ValueError(f"세트 {s.index}: 사이트 URL을 입력해 주세요.")
        if not s.keywords:
            raise ValueError(f"세트 {s.index}: 키워드를 1개 이상 입력해 주세요.")


def build_links_for_post(content_sets: list[ContentSet], post_index: int) -> list[AnchorLink]:
    """한 게시물: 모든 세트에서 키워드 1개씩 선택."""
    links: list[AnchorLink] = []
    for cset in content_sets:
        kw = cset.pick_keyword(post_index)
        links.append(AnchorLink(site_url=cset.site_url.strip(), keyword=kw, set_index=cset.index))
    return links


def preview_post_links(content_sets: list[ContentSet], post_index: int = 0) -> str:
    links = build_links_for_post(content_sets, post_index)
    tuples = [(l.site_url, l.keyword) for l in links]
    title = build_article_title(tuples, post_index=post_index)
    body = plain_text_from_html(build_article_content(tuples, post_index=post_index))
    if len(body) > 220:
        body = body[:220].rstrip() + "…"
    picks = " | ".join(f"세트{l.set_index}:{l.keyword}" for l in links)
    return f"제목: {title}\n키워드: {picks}\n본문: {body}"


def build_jobs(
    urls_text: str,
    content_sets: list[ContentSet],
    titles_text: str = "",
    category: str = "",
    exclude_keys: set[str] | None = None,
) -> list[PostJob]:
    urls = parse_lines(urls_text)
    if exclude_keys:
        urls = [u for u in urls if normalize_board_url(u) not in exclude_keys]
    if not urls:
        raise ValueError("게시판 URL을 한 줄에 하나씩 입력해 주세요.")

    validate_content_sets(content_sets)
    titles = parse_lines(titles_text)
    category = category.strip()
    total = len(urls)

    jobs: list[PostJob] = []
    for i, url in enumerate(urls):
        links = build_links_for_post(content_sets, i)
        link_tuples = [(l.site_url, l.keyword) for l in links]
        if titles:
            title = titles[i % len(titles)] if len(titles) > 1 else titles[0]
        else:
            title = build_article_title(link_tuples, post_index=i)

        jobs.append(
            PostJob(
                index=i + 1,
                total=total,
                board_url=url,
                title=title,
                links=links,
                category=category,
            )
        )
    return jobs
