"""게시판 HTML 모드 활성화 (앵커 태그 저장용)."""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

# 그누보드 / xe / 커스텀 게시판 공통 패턴
HTML_INPUT_SELECTORS = [
    "#html",
    'input[name="html"]',
    'input[name="wr_html"]',
    'input[id="wr_html"]',
    'input[type="checkbox"][value="html"]',
    'input[type="checkbox"][value="1"][name*="html" i]',
    'input[type="radio"][name*="html" i]',
    'input[type="radio"][value="html"]',
    'input[type="radio"][value="1"][name="html"]',
]

HTML_LABEL_SELECTORS = [
    'label[for="html"]',
    'label[for="wr_html"]',
    'label:has-text("html")',
    'label:has-text("HTML")',
    'label:has-text("Html")',
]

HTML_BUTTON_SELECTORS = [
    'button:has-text("HTML")',
    'button:has-text("html")',
    'a:has-text("HTML")',
    'a:has-text("html")',
    'button:has-text("소스")',
    'a:has-text("소스")',
    '[title*="HTML" i]',
    '[title*="html" i]',
]


@dataclass
class HtmlModeResult:
    enabled: bool
    method: str = ""
    message: str = ""


def _is_visible(page: Page, selector: str) -> bool:
    loc = page.locator(selector)
    if loc.count() == 0:
        return False
    try:
        return loc.first.is_visible()
    except Exception:
        return False


def _click_if_visible(page: Page, selector: str) -> bool:
    loc = page.locator(selector)
    if loc.count() == 0:
        return False
    target = loc.first
    try:
        if target.is_visible():
            target.click()
            return True
    except Exception:
        pass
    return False


def _is_input_checked(page: Page, selector: str) -> bool:
    loc = page.locator(selector)
    if loc.count() == 0:
        return False
    try:
        el = loc.first
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        input_type = (el.get_attribute("type") or "").lower()
        if tag == "input" and input_type in ("checkbox", "radio"):
            return el.is_checked()
        return el.evaluate(
            "e => e.checked === true || e.value === '1' || e.value === 'html'"
        )
    except Exception:
        return False


def _force_gnuboard_html1(page: Page) -> HtmlModeResult:
    """그누보드 checkbox/hidden html — value=html1 (앵커 태그 렌더링 필수)."""
    result = page.evaluate(
        """() => {
            const form = document.querySelector('#fwrite, form[name="fwrite"]');
            if (!form) return { ok: false, reason: 'no_form' };

            let input = form.querySelector('input[name="html"], input[id="html"], input[name="wr_html"]');
            if (input) {
                const type = (input.type || '').toLowerCase();
                if (type === 'checkbox' || type === 'radio') {
                    input.checked = true;
                    input.value = 'html1';
                } else {
                    input.value = 'html1';
                }
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return { ok: true, method: 'set', name: input.name, value: input.value };
            }

            // html 입력 없으면 hidden html1 주입
            input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'html';
            input.value = 'html1';
            form.appendChild(input);
            return { ok: true, method: 'inject', name: 'html', value: 'html1' };
        }"""
    )
    if result and result.get("ok"):
        method = result.get("method", "set")
        return HtmlModeResult(
            enabled=True,
            method=f"gnuboard_html1_{method}",
            message=f"HTML html1 ({method}: {result.get('name')})",
        )
    return HtmlModeResult(enabled=False, message=result.get("reason", "") if result else "")


def _enable_via_javascript(page: Page) -> HtmlModeResult:
    """숨겨진 체크박스·커스텀 UI 대응 JS 강제 설정."""
    forced = _force_gnuboard_html1(page)
    if forced.enabled:
        return forced

    result = page.evaluate(
        """() => {
            const names = ['html', 'wr_html'];
            for (const name of names) {
                const inputs = document.querySelectorAll(
                    `input[name="${name}"], input[id="${name}"]`
                );
                for (const input of inputs) {
                    const type = (input.type || '').toLowerCase();
                    if (type === 'checkbox' || type === 'radio') {
                        input.checked = true;
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('click', { bubbles: true }));
                        return { ok: true, target: name };
                    }
                    if (type === 'hidden' || type === 'text') {
                        input.value = '1';
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return { ok: true, target: name + '_hidden' };
                    }
                }
            }
            return { ok: false };
        }"""
    )
    if result and result.get("ok"):
        return HtmlModeResult(
            enabled=True,
            method=f"javascript:{result.get('target')}",
            message="JavaScript로 HTML 옵션 활성화",
        )
    return HtmlModeResult(enabled=False)


def _enable_via_label_for(page: Page) -> HtmlModeResult:
    """<label for="html"> 형태 — 사용자가 누르는 것과 동일."""
    for sel in HTML_LABEL_SELECTORS:
        loc = page.locator(sel)
        if loc.count() == 0:
            continue
        label = loc.first
        try:
            for_attr = label.get_attribute("for") or ""
            label.click()
            page.wait_for_timeout(200)

            if for_attr and _is_input_checked(page, f"#{for_attr}"):
                return HtmlModeResult(
                    enabled=True,
                    method=f"label_click:for={for_attr}",
                    message=f"HTML 라벨 클릭 (#{for_attr})",
                )

            # 라벨 안 체크박스
            inner = label.locator("input[type='checkbox'], input[type='radio']")
            if inner.count() > 0 and inner.first.is_checked():
                return HtmlModeResult(
                    enabled=True,
                    method="label_click:inner_input",
                    message="HTML 라벨(내부 input) 클릭",
                )
        except Exception:
            continue
    return HtmlModeResult(enabled=False)


def _enable_via_input(page: Page) -> HtmlModeResult:
    """체크박스/라디오 직접 check."""
    for sel in HTML_INPUT_SELECTORS:
        loc = page.locator(sel)
        if loc.count() == 0:
            continue
        box = loc.first
        try:
            if not box.is_visible():
                # 숨김 input은 JS로 처리
                continue
            input_type = (box.get_attribute("type") or "").lower()
            if input_type in ("checkbox", "radio"):
                if not box.is_checked():
                    box.check(force=True)
                page.wait_for_timeout(150)
                if box.is_checked():
                    return HtmlModeResult(
                        enabled=True,
                        method=f"input_check:{sel}",
                        message=f"HTML input 체크 ({sel})",
                    )
        except Exception:
            try:
                box.click(force=True)
                page.wait_for_timeout(150)
                if _is_input_checked(page, sel):
                    return HtmlModeResult(
                        enabled=True,
                        method=f"input_click:{sel}",
                        message=f"HTML input 클릭 ({sel})",
                    )
            except Exception:
                continue
    return HtmlModeResult(enabled=False)


def _enable_via_mode_button(page: Page) -> HtmlModeResult:
    """에디터 HTML/소스 모드 전환 버튼."""
    for sel in HTML_BUTTON_SELECTORS:
        if _click_if_visible(page, sel):
            page.wait_for_timeout(300)
            return HtmlModeResult(
                enabled=True,
                method=f"mode_button:{sel}",
                message="HTML/소스 모드 버튼 클릭",
            )
    return HtmlModeResult(enabled=False)


def _verify_html_enabled(page: Page) -> bool:
    """HTML 옵션이 실제로 켜졌는지 확인."""
    ok = page.evaluate(
        """() => {
            const form = document.querySelector('#fwrite, form[name="fwrite"]');
            const scope = form || document;
            const el = scope.querySelector('input[name="html"], input[id="html"], input[name="wr_html"]');
            if (!el) return false;
            const v = (el.value || '').toLowerCase();
            const type = (el.type || '').toLowerCase();
            if (type === 'hidden' && (v === 'html1' || v === 'html2' || v === '1' || v === 'html')) return true;
            if ((type === 'checkbox' || type === 'radio') && el.checked && (v === 'html1' || v === 'html2')) return true;
            return false;
        }"""
    )
    if ok:
        return True

    for sel in HTML_INPUT_SELECTORS:
        if _is_input_checked(page, sel):
            return True

    checked = page.evaluate(
        """() => {
            const pick = (sel) => document.querySelector(sel);
            const targets = ['#html', 'input[name="html"]', 'input[name="wr_html"]'];
            for (const sel of targets) {
                const el = pick(sel);
                if (!el) continue;
                if (el.type === 'checkbox' || el.type === 'radio') return el.checked;
                if (el.type === 'hidden') return el.value === '1' || el.value === 'html';
            }
            return false;
        }"""
    )
    return bool(checked)


def enable_html_mode(page: Page) -> HtmlModeResult:
    """
    HTML 모드 활성화 — 여러 전략 순차 시도.
    그누보드 <label for="html">, 숨김 html1, 숨김 체크박스 등 대응.
    """
    if _verify_html_enabled(page):
        return HtmlModeResult(
            enabled=True,
            method="already_enabled",
            message="HTML 모드 이미 활성 (html1)",
        )

    if not _has_html_option(page):
        return HtmlModeResult(
            enabled=False,
            message="HTML 옵션 없음 (에디터가 HTML 기본일 수 있음)",
        )

    strategies = (
        _force_gnuboard_html1,
        _enable_via_label_for,
        _enable_via_input,
        _enable_via_javascript,
        _enable_via_mode_button,
    )

    last = HtmlModeResult(enabled=False)
    for strategy in strategies:
        result = strategy(page)
        page.wait_for_timeout(150)
        if _verify_html_enabled(page):
            if not result.enabled:
                result = HtmlModeResult(enabled=True, method="verify_only")
            result.enabled = True
            result.message = result.message or "HTML 모드 활성화 확인"
            return result
        if result.enabled:
            last = result

    if _verify_html_enabled(page):
        return HtmlModeResult(enabled=True, method="final_verify", message="HTML 모드 활성화")

    return HtmlModeResult(
        enabled=False,
        method=last.method,
        message="HTML 옵션을 찾았으나 활성화 실패 — 수동 확인 필요",
    )


def _has_html_option(page: Page) -> bool:
    """페이지에 HTML 관련 옵션이 있는지."""
    if any(_is_visible(page, sel) for sel in HTML_INPUT_SELECTORS):
        return True
    if any(page.locator(sel).count() > 0 for sel in HTML_INPUT_SELECTORS):
        return True
    if any(page.locator(sel).count() > 0 for sel in HTML_LABEL_SELECTORS):
        return True
    if any(_is_visible(page, sel) for sel in HTML_BUTTON_SELECTORS):
        return True

    return bool(
        page.evaluate(
            """() => !!(
                document.querySelector('#html, input[name="html"], input[name="wr_html"], label[for="html"]')
            )"""
        )
    )


def ensure_html_mode(page: Page) -> HtmlModeResult:
    """내용 입력 전·후 HTML 모드 재확인."""
    result = enable_html_mode(page)
    if result.enabled and _verify_html_enabled(page):
        return result
    # 그누보드 checkbox html1 강제 재시도
    forced = _force_gnuboard_html1(page)
    if _verify_html_enabled(page):
        forced.enabled = True
        if not forced.message:
            forced.message = "HTML html1 강제 설정"
        return forced
    return result
