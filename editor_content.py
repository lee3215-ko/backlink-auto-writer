"""게시판 에디터(SmartEditor2 등) 본문 입력."""

from __future__ import annotations

from app_logger import log
from playwright.sync_api import Page

WRITE_FORM = '#fwrite, form[name="fwrite"]'

CONTENT_TEXTAREA_SELECTORS = [
    f"{WRITE_FORM} textarea#wr_content",
    f"{WRITE_FORM} textarea[name='wr_content']",
    f"{WRITE_FORM} textarea[name='content']",
    "#wr_content",
    'textarea[name="wr_content"]',
]

SMARTEDITOR_IFRAME_SELECTORS = [
    "iframe.se2_input_wysiwyg",
    "iframe[id*='se2']",
    "iframe[src*='smarteditor']",
]


def fill_editor_content(page: Page, content: str) -> tuple[bool, str]:
    """본문 입력 — SmartEditor2 우선, 일반 textarea 폴백."""
    if _fill_smarteditor2(page, content):
        return True, "SmartEditor2 SET_IR"

    if _fill_cheditor(page, content):
        return True, "Cheditor"

    if _fill_textarea_js(page, content):
        return True, "textarea JS"

    if _fill_smarteditor_iframe(page, content):
        return True, "SmartEditor iframe"

    if _fill_visible_textarea(page, content):
        return True, "textarea fill"

    return False, "에디터를 찾지 못함"


def _fill_smarteditor2(page: Page, content: str) -> bool:
    try:
        page.wait_for_function(
            "() => window.oEditors && oEditors.getById && oEditors.getById['wr_content']",
            timeout=20000,
        )
        page.wait_for_timeout(500)
        page.evaluate(
            """(content) => {
                const ed = oEditors.getById['wr_content'];
                if (!ed) throw new Error('no editor');
                try {
                    ed.exec('SET_IR', [content]);
                } catch (e) {
                    ed.exec('SET_CONTENTS', [content]);
                }
                ed.exec('UPDATE_CONTENTS_FIELD', []);
            }""",
            content,
        )
        log.info("SmartEditor2 본문 입력 완료")
        return True
    except Exception as e:
        log.debug("SmartEditor2 실패: %s", e)
        return False


def _fill_cheditor(page: Page, content: str) -> bool:
    try:
        ok = page.evaluate(
            """(content) => {
                if (typeof ed_wr_content !== 'undefined' && ed_wr_content) {
                    ed_wr_content.replaceContents(content);
                    return true;
                }
                if (typeof CHEDITOR !== 'undefined') {
                    const inst = CHEDITOR.instances['wr_content'];
                    if (inst) { inst.replaceContents(content); return true; }
                }
                return false;
            }""",
            content,
        )
        if ok:
            log.info("Cheditor 본문 입력 완료")
        return bool(ok)
    except Exception as e:
        log.debug("Cheditor 실패: %s", e)
        return False


def _fill_textarea_js(page: Page, content: str) -> bool:
    """페이지 내 모든 wr_content(textarea/hidden) 동기화."""
    try:
        ok = page.evaluate(
            """(content) => {
                const targets = document.querySelectorAll(
                    'textarea[name="wr_content"], input[name="wr_content"]'
                );
                if (!targets.length) return false;
                targets.forEach(el => {
                    el.value = content;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                });
                return true;
            }""",
            content,
        )
        if ok:
            n = page.evaluate(
                '() => document.querySelectorAll(\'textarea[name="wr_content"], input[name="wr_content"]\').length'
            )
            log.info("wr_content %d개 필드 동기화 완료", n)
        return bool(ok)
    except Exception as e:
        log.debug("textarea JS 실패: %s", e)
        return False


def _fill_smarteditor_iframe(page: Page, content: str) -> bool:
    """SmartEditor WYSIWYG iframe body에 HTML 삽입."""
    for sel in SMARTEDITOR_IFRAME_SELECTORS:
        frame_loc = page.locator(sel)
        if frame_loc.count() == 0:
            continue
        try:
            frame = frame_loc.first.content_frame()
            if not frame:
                continue
            body = frame.locator("body")
            if body.count() == 0:
                continue
            body.evaluate("(el, html) => { el.innerHTML = html; }", content)
            # textarea 동기화 시도
            page.evaluate(
                """() => {
                    if (window.oEditors && oEditors.getById && oEditors.getById['wr_content']) {
                        oEditors.getById['wr_content'].exec('UPDATE_CONTENTS_FIELD', []);
                    }
                }"""
            )
            log.info("SmartEditor iframe 본문 입력: %s", sel)
            return True
        except Exception as e:
            log.debug("iframe 입력 실패 %s: %s", sel, e)
    return False


def _fill_visible_textarea(page: Page, content: str) -> bool:
    for sel in CONTENT_TEXTAREA_SELECTORS:
        loc = page.locator(sel)
        if loc.count() == 0:
            continue
        ta = loc.first
        try:
            if not ta.is_visible():
                continue
            ta.fill(content)
            log.info("visible textarea fill: %s", sel)
            return True
        except Exception:
            continue
    return False
