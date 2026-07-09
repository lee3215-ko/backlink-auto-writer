"""미지원 URL 보고서 — Cursor 채팅에 붙여넣어 코드 확장 요청용."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app_constants import APP_VERSION
from app_paths import data_file
from url_analyzer import UrlAnalysis, filter_unsupported


def format_analysis_block(a: UrlAnalysis) -> str:
    return (
        f"[{a.support_label}] {a.kind_label}\n"
        f"URL: {a.url}\n"
        f"설명: {a.note}"
    )


def build_cursor_report(
    items: list[UrlAnalysis],
    *,
    snapshots: list[dict] | None = None,
    app_version: str = APP_VERSION,
) -> str:
    unsupported = filter_unsupported(items)
    lines = [
        "# 백링크 자동화 — 미지원·부분 가능 URL 보고서",
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"앱 버전: {app_version}",
        "",
        "아래 URL은 댓글/게시글 자동화가 **불가**, **부분 가능**, **미지원**으로 분류되었습니다.",
        "이 목록 전체를 Cursor 채팅에 붙여넣고 「댓글 달 수 있게 코드 추가해줘」라고 요청하세요.",
        "",
        f"총 {len(unsupported)}건 (전체 검사 {len(items)}건)",
        "",
        "=" * 60,
    ]
    for i, a in enumerate(unsupported, 1):
        lines.append(f"\n## {i}. {a.support_label} · {a.kind_label}")
        lines.append(format_analysis_block(a))
        lines.append("")

    if snapshots:
        lines.append("\n" + "=" * 60)
        lines.append("# 페이지 스냅샷 (로그인·캡차·폼 구조)")
        for snap in snapshots:
            lines.append("")
            lines.append(snap.get("text_block", "").strip())

    lines.append("")
    lines.append("---")
    lines.append("요청 예시: 위 URL 중 자동 댓글이 가능한 사이트는 writer 모듈을 추가해 주세요.")
    return "\n".join(lines)


def build_url_only_text(items: list[UrlAnalysis]) -> str:
    return "\n".join(a.url for a in filter_unsupported(items))


def save_cursor_report(
    items: list[UrlAnalysis],
    *,
    snapshots: list[dict] | None = None,
    path: Path | None = None,
) -> Path:
    target = path or data_file("unsupported_urls_report.txt")
    text = build_cursor_report(items, snapshots=snapshots)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
