"""URL 페이지 스냅샷 — 로그인·스팸필터·댓글 폼 구조 추출 (Cursor/AI 분석용)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from playwright.sync_api import sync_playwright

from browser_session import close_browser_session, launch_stealth_browser
from browser_prefs import is_headless

LOGIN_HINTS = (
    "login.php",
    "로그인 후",
    "회원만",
    "권한이 없",
    "로그인이 필요",
    "login required",
    "must be logged in",
    "sign in to",
    "please log in",
)

SPAM_HINTS = (
    "akismet",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "honeypot",
    "anti-spam",
    "스팸",
    "spam filter",
    "cloudflare",
)

_FORM_SCRIPT = """
() => {
  const forms = [];
  for (const form of document.querySelectorAll('form')) {
    const fields = [];
    for (const el of form.querySelectorAll('input, textarea, select')) {
      const type = (el.type || '').toLowerCase();
      if (type === 'hidden' || type === 'submit' || type === 'button') continue;
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      fields.push({
        tag: el.tagName.toLowerCase(),
        type: type,
        name: el.name || '',
        id: el.id || '',
        placeholder: el.placeholder || '',
        required: !!el.required,
      });
    }
    if (fields.length) {
      forms.push({
        action: form.action || '',
        id: form.id || '',
        name: form.name || '',
        method: (form.method || 'get').toLowerCase(),
        fields,
      });
    }
  }
  return forms;
}
"""


@dataclass
class PageSnapshot:
    url: str
    final_url: str = ""
    title: str = ""
    login_required: bool = False
    login_hints: list[str] = field(default_factory=list)
    spam_hints: list[str] = field(default_factory=list)
    captcha_type: str = "none"  # none | numeric | recaptcha | hcaptcha | turnstile | text
    forms: list[dict[str, Any]] = field(default_factory=list)
    comment_form_found: bool = False
    body_excerpt: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def text_block(self) -> str:
        lines = [
            f"URL: {self.url}",
            f"최종 URL: {self.final_url or self.url}",
            f"제목: {self.title}",
            f"로그인 필요: {'예' if self.login_required else '아니오'}",
        ]
        if self.login_hints:
            lines.append(f"로그인 단서: {', '.join(self.login_hints[:5])}")
        if self.spam_hints:
            lines.append(f"스팸/보안 단서: {', '.join(self.spam_hints[:8])}")
        if self.captcha_type != "none":
            lines.append(f"캡차 유형: {self.captcha_type}")
        lines.append(f"댓글 폼 추정: {'있음' if self.comment_form_found else '없음'}")
        if self.forms:
            lines.append("폼 구조:")
            for i, form in enumerate(self.forms[:4], 1):
                lines.append(f"  폼{i}: action={form.get('action', '')[:80]}")
                for f in form.get("fields", [])[:12]:
                    lines.append(
                        f"    - {f.get('tag')} type={f.get('type')} name={f.get('name')} id={f.get('id')}"
                    )
        if self.error:
            lines.append(f"오류: {self.error}")
        elif self.body_excerpt:
            lines.append(f"본문 일부: {self.body_excerpt[:300]}")
        return "\n".join(lines)


def _detect_captcha(page) -> str:
    html = page.content().lower()
    if "hcaptcha" in html or "h-captcha" in html:
        return "hcaptcha"
    if "recaptcha" in html or "g-recaptcha" in html:
        return "recaptcha"
    if "turnstile" in html or "cf-turnstile" in html:
        return "turnstile"
    if re.search(r'captcha|img.*captcha|kaptcha', html):
        if page.locator('input[name="captcha_key"], #captcha_img, img[src*="captcha"]').count():
            return "numeric"
        return "text"
    return "none"


def _has_comment_form(forms: list[dict]) -> bool:
    comment_names = (
        "comment", "wr_content", "content", "memo", "reply", "body",
        "コメント", "comments", "message",
    )
    for form in forms:
        for f in form.get("fields", []):
            name = (f.get("name") or "").lower()
            id_ = (f.get("id") or "").lower()
            if any(x in name or x in id_ for x in comment_names):
                return True
            if f.get("tag") == "textarea":
                return True
    return False


_FORM_SCRIPT_ALL = """
() => {
  const forms = [];
  for (const form of document.querySelectorAll('form')) {
    const fields = [];
    for (const el of form.querySelectorAll('input, textarea, select')) {
      const type = (el.type || '').toLowerCase();
      const style = window.getComputedStyle(el);
      fields.push({
        tag: el.tagName.toLowerCase(),
        type: type,
        name: el.name || '',
        id: el.id || '',
        placeholder: el.placeholder || '',
        required: !!el.required,
        visible: style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0',
      });
    }
    forms.push({
      action: form.action || '',
      id: form.id || '',
      name: form.name || '',
      method: (form.method || 'get').toLowerCase(),
      className: form.className || '',
      fields,
    });
  }
  return forms;
}
"""

_DOM_MARKERS_SCRIPT = """
() => {
  const q = (s) => !!document.querySelector(s);
  return {
    '#comment': q('#comment'),
    '#commentform': q('#commentform'),
    '#comment-form': q('#comment-form'),
    '#respond': q('#respond'),
    '#comments': q('#comments'),
    '#author': q('#author'),
    '#email': q('#email'),
    '#submit': q('#submit'),
    "textarea[name=comment]": q('textarea[name="comment"]'),
    'form.comment-form': q('form.comment-form'),
    'textarea_count': document.querySelectorAll('textarea').length,
    'form_count': document.querySelectorAll('form').length,
    'wp-comments-post': (document.documentElement.innerHTML || '').includes('wp-comments-post'),
  };
}
"""


def capture_snapshot_from_page(page, url: str = "", *, include_hidden_fields: bool = True) -> PageSnapshot:
    """이미 열린 Playwright page에서 스냅샷 (배치 실패 직후용 — 새 브라우저 안 띄움)."""
    snap = PageSnapshot(url=(url or getattr(page, "url", "") or "").strip())
    try:
        snap.final_url = page.url or snap.url
        snap.title = page.title() or ""
        try:
            body = page.locator("body").inner_text(timeout=4000).lower()
        except Exception:
            body = ""
        snap.body_excerpt = body[:500]

        for hint in LOGIN_HINTS:
            if hint.lower() in body or hint.lower() in (snap.final_url or "").lower():
                snap.login_hints.append(hint)
        snap.login_required = bool(snap.login_hints) or "login" in (snap.final_url or "").lower()

        try:
            html_low = page.content().lower()
        except Exception:
            html_low = ""
        for hint in SPAM_HINTS:
            if hint in body or hint in html_low:
                snap.spam_hints.append(hint)

        snap.captcha_type = _detect_captcha(page)

        script = _FORM_SCRIPT_ALL if include_hidden_fields else _FORM_SCRIPT
        try:
            forms = page.evaluate(script)
            snap.forms = forms if isinstance(forms, list) else []
        except Exception as exc:
            snap.error = f"폼 추출 실패: {exc}"
            snap.forms = []

        snap.comment_form_found = _has_comment_form(snap.forms)
        # 숨김 필드만 있어도 WP 표준 폼이면 있음으로
        if not snap.comment_form_found:
            try:
                markers = page.evaluate(_DOM_MARKERS_SCRIPT)
                if isinstance(markers, dict) and (
                    markers.get("#commentform")
                    or markers.get("#comment-form")
                    or markers.get("textarea[name=comment]")
                    or markers.get("#comment")
                ):
                    snap.comment_form_found = True
            except Exception:
                pass
    except Exception as exc:
        snap.error = str(exc)[:200]
    return snap


def capture_dom_markers(page) -> dict:
    try:
        markers = page.evaluate(_DOM_MARKERS_SCRIPT)
        return markers if isinstance(markers, dict) else {}
    except Exception:
        return {}


def capture_html_excerpt(page, *, max_chars: int = 40000) -> str:
    try:
        html = page.content() or ""
    except Exception:
        return ""
    if len(html) <= max_chars:
        return html
    # 댓글 영역 우선
    low = html.lower()
    for key in ("id=\"respond\"", "id=\"commentform\"", "id='commentform'", "comment-form"):
        idx = low.find(key)
        if idx >= 0:
            start = max(0, idx - 2000)
            return html[start : start + max_chars]
    return html[:max_chars]


def capture_page_snapshot(url: str, *, timeout_ms: int = 15000) -> PageSnapshot:
    url = url.strip()
    snap = PageSnapshot(url=url)
    if not url.startswith(("http://", "https://")):
        snap.error = "http(s) URL이 아님"
        return snap

    playwright = None
    browser = None
    context = None
    try:
        playwright = sync_playwright().start()
        browser, context, page = launch_stealth_browser(
            playwright, headless=is_headless(), default_timeout=timeout_ms
        )
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        snap.final_url = page.url
        snap.title = page.title() or ""

        try:
            body = page.locator("body").inner_text(timeout=5000).lower()
        except Exception:
            body = ""
        snap.body_excerpt = body[:500]

        for hint in LOGIN_HINTS:
            if hint.lower() in body or hint.lower() in snap.final_url.lower():
                snap.login_hints.append(hint)
        snap.login_required = bool(snap.login_hints) or "login" in snap.final_url.lower()

        html_low = page.content().lower()
        for hint in SPAM_HINTS:
            if hint in body or hint in html_low:
                snap.spam_hints.append(hint)

        snap.captcha_type = _detect_captcha(page)

        try:
            forms = page.evaluate(_FORM_SCRIPT)
            snap.forms = forms if isinstance(forms, list) else []
        except Exception as exc:
            snap.error = f"폼 추출 실패: {exc}"
            snap.forms = []

        snap.comment_form_found = _has_comment_form(snap.forms)
    except Exception as exc:
        snap.error = str(exc)[:200]
    finally:
        close_browser_session(browser, context, playwright)

    return snap


def capture_snapshots(urls: list[str], *, timeout_ms: int = 15000) -> list[PageSnapshot]:
    return [capture_page_snapshot(u, timeout_ms=timeout_ms) for u in urls if u.strip()]


def snapshots_to_report_blocks(snapshots: list[PageSnapshot]) -> list[dict]:
    return [{"text_block": s.text_block(), "data": s.to_dict()} for s in snapshots]
