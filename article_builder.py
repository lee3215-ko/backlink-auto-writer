"""백링크용 원고 생성 — 키워드·링크를 본문에 자연스럽게 분산."""

from __future__ import annotations

import random
import re
from html import escape

from link_utils import normalize_backlink_url


def _norm_url(url: str) -> str:
    return normalize_backlink_url(url)


def _anchor(url: str, keyword: str) -> str:
    return f'<a href="{_norm_url(url)}" target="_blank" rel="noopener noreferrer">{escape(keyword.strip())}</a>'


def _rng(post_index: int, links: list[tuple[str, str]]) -> random.Random:
    key = "|".join(f"{u}:{k}" for u, k in links)
    seed = hash((post_index, key)) & 0xFFFFFFFF
    return random.Random(seed)


INTRO_LINES = [
    "안녕하세요. 오늘은 관련 주제를 정리해 보았습니다.",
    "최근 문의가 많아 참고할 만한 내용을 간단히 남깁니다.",
    "검색하시다 보면 정보가 분산되어 있어 한곳에 모아 보기 쉽게 정리했습니다.",
    "처음 접하시는 분들도 이해하기 쉽도록 핵심만 담아 보았습니다.",
    "실제로 도움이 될 만한 내용 위주로 정리해 두었으니 참고해 주세요.",
]

ANCHOR_LINES = [
    "관련 분야에서 {a}에 대한 관심이 꾸준히 이어지고 있습니다.",
    "많은 분들이 {a} 쪽 정보를 찾고 계신데, 기본만 알아도 선택이 수월해집니다.",
    "비교 검토 시 {a}를 함께 살펴보시면 전체 그림이 잡히기 쉽습니다.",
    "개인적인 경험상 {a} 관련 안내를 미리 확인해 두면 불필요한 시행착오를 줄일 수 있습니다.",
    "궁금해하시는 분들이 많은 {a} 주제에 대해 간단히 말씀드리겠습니다.",
    "참고하실 만한 자료로 {a} 페이지도 함께 확인해 보시길 권합니다.",
    "상세 내용은 {a}에서 정리되어 있어 필요하실 때 방문해 보시면 됩니다.",
    "관심 있으신 분들께 {a} 링크도 남겨 두었으니 시간 되실 때 둘러보세요.",
]

BRIDGE_LINES = [
    "또한 비슷한 맥락에서 다른 선택지도 함께 비교해 보시는 것이 좋습니다.",
    "이어서 관련된 다른 키워드도 간단히 소개해 드리겠습니다.",
    "한 가지 더 참고하실 만한 내용이 있어 이어서 적어 둡니다.",
    "같은 주제 안에서도 세부 항목별로 살펴볼 포인트가 조금씩 다릅니다.",
]

CLOSING_LINES = [
    "위 내용이 궁금하신 점을 해소하는 데 조금이라도 도움이 되었으면 합니다.",
    "상황에 맞게 참고만 하시고, 필요하면 각 링크에서 자세한 안내를 확인해 주세요.",
    "글이 도움이 되셨다면 주변에도 공유해 주시면 감사하겠습니다.",
    "추가로 궁금한 점이 있으시면 댓글로 남겨 주시면 확인해 보겠습니다.",
    "오늘은 여기까지 정리해 보았습니다. 좋은 하루 되세요.",
]

TITLE_TEMPLATES = [
    "{topic} 관련 참고 정리",
    "{topic} 알아보기 — 간단 안내",
    "{topic} 정보 모음",
    "{kw0} · {kw1} 참고 글",
    "{topic} 궁금하신 분들께",
]


def build_article_title(links: list[tuple[str, str]], *, post_index: int = 0) -> str:
    if not links:
        return "참고 정보 안내"
    kws = [k.strip() for _, k in links if k.strip()]
    if not kws:
        return "참고 정보 안내"
    rng = _rng(post_index, links)
    topic = kws[0]
    if len(kws) >= 2:
        tpl = rng.choice(TITLE_TEMPLATES)
        return tpl.format(topic=topic, kw0=kws[0], kw1=kws[1])
    return rng.choice(TITLE_TEMPLATES).format(topic=topic, kw0=topic, kw1=topic)


def build_article_content(
    links: list[tuple[str, str]],
    *,
    post_index: int = 0,
) -> str:
    """
    HTML 원고 생성. 링크를 문단마다 분산 배치.
    links: [(site_url, keyword), ...]
    """
    if not links:
        return ""

    # 단일 링크·구형 호출 호환
    cleaned = [(u.strip(), k.strip()) for u, k in links if u.strip() and k.strip()]
    if not cleaned:
        return ""

    rng = _rng(post_index, cleaned)
    paragraphs: list[str] = []

    paragraphs.append(f"<p>{rng.choice(INTRO_LINES)}</p>")

    if len(cleaned) == 1:
        url, kw = cleaned[0]
        a = _anchor(url, kw)
        line = rng.choice(ANCHOR_LINES).format(a=a)
        paragraphs.append(f"<p>{line}</p>")
        paragraphs.append(
            f"<p>{rng.choice(BRIDGE_LINES)} "
            f"필요하신 경우 {a}에서 자세한 설명을 확인하실 수 있습니다.</p>"
        )
    else:
        # 첫 링크 — 본문 초반
        url0, kw0 = cleaned[0]
        a0 = _anchor(url0, kw0)
        paragraphs.append(f"<p>{rng.choice(ANCHOR_LINES).format(a=a0)}</p>")

        if len(cleaned) > 1:
            paragraphs.append(f"<p>{rng.choice(BRIDGE_LINES)}</p>")

        # 중간 링크들
        for url, kw in cleaned[1:-1]:
            a = _anchor(url, kw)
            paragraphs.append(f"<p>{rng.choice(ANCHOR_LINES).format(a=a)}</p>")

        # 마지막 링크 — 마무리 직전
        if len(cleaned) > 1:
            url_l, kw_l = cleaned[-1]
            a_l = _anchor(url_l, kw_l)
            paragraphs.append(
                f"<p>마지막으로 {a_l}도 참고하시면 "
                f"{escape(kw_l)} 관련 내용을 한눈에 보기 좋습니다.</p>"
            )

    paragraphs.append(f"<p>{rng.choice(CLOSING_LINES)}</p>")

    # 키워드 요약 줄 (검색·가독용, 링크 한 번 더 분산)
    if len(cleaned) >= 2:
        tags = " · ".join(_anchor(u, k) for u, k in cleaned[:4])
        if len(cleaned) > 4:
            tags += f" · 외 {len(cleaned) - 4}건"
        paragraphs.append(f"<p><strong>관련 링크:</strong> {tags}</p>")

    return "\n".join(paragraphs)


def build_article_plain(
    links: list[tuple[str, str]],
    *,
    post_index: int = 0,
) -> str:
    """HTML 미지원 게시판용 — URL을 문장에 포함한 텍스트 원고."""
    if not links:
        return ""
    cleaned = [(u.strip(), k.strip()) for u, k in links if u.strip() and k.strip()]
    if not cleaned:
        return ""
    rng = _rng(post_index, cleaned)

    def ref(url: str, kw: str) -> str:
        return f"{kw}\n{_norm_url(url)}"

    lines = [rng.choice(INTRO_LINES), ""]
    for i, (url, kw) in enumerate(cleaned):
        r = ref(url, kw)
        if i == 0:
            lines.append(rng.choice(ANCHOR_LINES).format(a=r))
        else:
            lines.append(rng.choice(ANCHOR_LINES).format(a=r))
        lines.append("")
    lines.append(rng.choice(CLOSING_LINES))
    return "\n".join(lines)


COMMENT_INTROS = [
    "관련해서 참고하실 만한 정보입니다.",
    "도움이 될 만한 링크 남깁니다.",
    "비슷한 내용 정리해 둔 페이지가 있어 공유합니다.",
]

COMMENT_CLOSINGS = [
    "필요하시면 확인해 보세요.",
    "참고만 하셔도 좋을 것 같습니다.",
]


def _pick_comment_links(
    links: list[tuple[str, str]],
    *,
    post_index: int = 0,
) -> list[tuple[str, str]]:
    """댓글 스팸 완화 — 링크가 여러 개면 게시마다 하나만 순환 사용."""
    cleaned = [(u.strip(), k.strip()) for u, k in links if u.strip() and k.strip()]
    if len(cleaned) <= 1:
        return cleaned
    return [cleaned[post_index % len(cleaned)]]


def build_comment_content(
    links: list[tuple[str, str]],
    *,
    post_index: int = 0,
    html: bool = True,
    style: str = "auto",
) -> str:
    """
    댓글용 짧은 본문 — 링크 1개 (세트가 여러 개면 post_index로 순환).
    style: auto | plain | smart | anchors | html
      smart   — 키워드 + URL 별도 줄 (그누보드 자동링크 최적)
      anchors — <a> 앵커만 (WP·커스텀 BBS, HTML 허용 시)
      plain   — 키워드 + URL 별도 줄 (smart와 동일)
      html    — <p> 래핑 HTML
    """
    if not links:
        return ""
    cleaned = _pick_comment_links(links, post_index=post_index)
    if not cleaned:
        return ""
    rng = _rng(post_index, cleaned)
    intro = rng.choice(COMMENT_INTROS)
    closing = rng.choice(COMMENT_CLOSINGS)

    use_style = style
    if use_style == "auto":
        use_style = "anchors" if html else "plain"

    if use_style == "plain":
        parts = [intro]
        for url, kw in cleaned:
            parts.append(f"{kw}\n{_norm_url(url)}")
        parts.append(closing)
        return "\n".join(parts)

    if use_style == "smart":
        # 그누보드 등 — 괄호 URL은 자동링크 안 됨 → URL 단독 줄 배치
        parts = [intro]
        for url, kw in cleaned:
            parts.append(f"{kw}")
            parts.append(_norm_url(url))
        parts.append(closing)
        return "\n".join(parts)

    if use_style == "anchors":
        anchors = " ".join(_anchor(url, kw) for url, kw in cleaned)
        return f"{intro} {anchors} {closing}"

    parts: list[str] = [intro]
    for url, kw in cleaned:
        parts.append(_anchor(url, kw))
    parts.append(closing)
    return "<p>" + "</p><p>".join(parts) + "</p>"


def build_anchor_content(links: list[tuple[str, str]] | tuple[str, str], keyword: str = "") -> str:
    """하위 호환 — 원고 생성으로 위임."""
    if isinstance(links, tuple):
        pairs: list[tuple[str, str]] = [(links[0], keyword)]
    else:
        pairs = list(links)
    return build_article_content(pairs, post_index=0)


def plain_text_from_html(html: str) -> str:
    """HTML 모드 미지원 게시판용 텍스트."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
