"""게시글·댓글 혼합 배치 작업."""

from __future__ import annotations

from dataclasses import dataclass

from batch_jobs import AnchorLink, ContentSet, build_article_title, build_links_for_post, parse_lines, validate_content_sets
from board_url import gnuboard_write_url, normalize_board_list_url
from url_analyzer import UrlAnalysis, classify_url


@dataclass
class TargetJob:
    index: int
    total: int
    url: str
    action: str  # post | comment_gnuboard | comment_wordpress | comment_movable_type | comment_custom_bbs
    kind: str
    title: str
    links: list[AnchorLink]
    note: str = ""

    @property
    def label(self) -> str:
        return f"[{self.index}/{self.total}] {self.url[:55]}"

    @property
    def picks_summary(self) -> str:
        return " | ".join(f"세트{l.set_index}:{l.keyword}" for l in self.links)

    @property
    def board_url(self) -> str:
        return self.url

    @property
    def category(self) -> str:
        return ""

    @property
    def action_label(self) -> str:
        return {"post": "게시글", "comment_gnuboard": "그누보드 댓글", "comment_wordpress": "WP 댓글", "comment_movable_type": "MT 댓글", "comment_custom_bbs": "BBS 댓글"}.get(
            self.action, self.action
        )


def build_target_jobs(
    urls_text: str,
    content_sets: list[ContentSet],
    *,
    mode: str = "auto",
    titles_text: str = "",
) -> tuple[list[TargetJob], list[UrlAnalysis]]:
    """
    mode: auto | post | comment
    auto — URL 형식에 맞게 게시글/댓글 자동 선택
    """
    urls = parse_lines(urls_text)
    if not urls:
        raise ValueError("URL을 한 줄에 하나씩 입력해 주세요.")
    validate_content_sets(content_sets)
    titles = parse_lines(titles_text)
    analyses = [classify_url(u) for u in urls]

    jobs: list[TargetJob] = []
    skipped: list[str] = []

    for i, (url, analysis) in enumerate(zip(urls, analyses)):
        action = _pick_action(analysis, mode)
        if not action:
            skipped.append(f"{url[:60]} → {analysis.note}")
            continue

        links = build_links_for_post(content_sets, i)
        link_tuples = [(l.site_url, l.keyword) for l in links]
        if titles:
            title = titles[i % len(titles)] if len(titles) > 1 else titles[0]
        else:
            title = build_article_title(link_tuples, post_index=i)

        jobs.append(
            TargetJob(
                index=len(jobs) + 1,
                total=0,
                url=_job_url(url, action),
                action=action,
                kind=analysis.kind,
                title=title,
                links=links,
                note=analysis.note,
            )
        )

    total = len(jobs)
    for j in jobs:
        j.total = total

    return jobs, analyses


def _pick_action(analysis: UrlAnalysis, mode: str) -> str | None:
    if mode == "post":
        if analysis.support_post:
            return "post"
        return None
    if mode == "comment":
        if analysis.kind == "gnuboard_comment":
            return "comment_gnuboard"
        if analysis.kind == "wordpress_comment":
            return "comment_wordpress"
        if analysis.kind == "movable_type_comment":
            return "comment_movable_type"
        if analysis.kind == "custom_bbs_comment":
            return "comment_custom_bbs"
        return None
    # auto
    if analysis.kind == "gnuboard_comment":
        return "comment_gnuboard"
    if analysis.kind == "wordpress_comment":
        return "comment_wordpress"
    if analysis.kind == "movable_type_comment":
        return "comment_movable_type"
    if analysis.kind == "custom_bbs_comment":
        return "comment_custom_bbs"
    if analysis.support_post:
        return "post"
    return None


def _job_url(url: str, action: str) -> str:
    """게시/댓글 작업에 맞는 추천 URL."""
    from url_recommend import recommend_url

    mode = "post" if action == "post" else "comment" if action.startswith("comment") else "auto"
    recommended, _ = recommend_url(url, mode=mode)
    return recommended or url
