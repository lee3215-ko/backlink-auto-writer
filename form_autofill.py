"""글쓰기 폼 필수/부가 필드 자동 감지·입력 (분류, 라디오, 미지 필드 등)."""

from __future__ import annotations

from app_logger import log
from playwright.sync_api import Page

WRITE_FORM = "#fwrite, form[name='fwrite']"

SKIP_FIELD_NAMES = {
    "wr_name",
    "wr_password",
    "wr_subject",
    "wr_content",
    "captcha_key",
    "wr_key",
    "kcaptcha",
    "uid",
    "w",
    "bo_table",
    "wr_id",
    "html",
    "wr_html",
    "token",
    "csrf",
    "sca",
    "sfl",
    "stx",
    "spt",
    "sst",
    "sod",
    "page",
    "btn_submit",
}

SKIP_FIELD_PREFIXES = ("bf_file", "wr_file")

CATEGORY_NAME_HINTS = (
    "ca_name",
    "category",
    "cate",
    "board_cate",
    "wr_category",
    "wr_cate",
    "sca_select",
)

_AUTOFILL_SCRIPT = """
({ skipNames, skipPrefix, catNames, siteUrl, categoryOverride }) => {
    const form = document.querySelector('#fwrite, form[name="fwrite"]');
    if (!form) return [];
    const logs = [];
    const skip = new Set(skipNames);
    const catLabelRe = /분류|카테고리|category|구분|유형|종류/i;

    const shouldSkip = (el) => {
        const n = (el.name || '').toLowerCase();
        if (!n || skip.has(n)) return true;
        if (skipPrefix.some(p => n.startsWith(p))) return true;
        if (['hidden','submit','button','file','image','reset'].includes(el.type)) return true;
        if (el.disabled || el.readOnly) return true;
        return false;
    };

    const labelOf = (el) => {
        if (el.id) {
            const lb = form.querySelector('label[for="' + el.id + '"]');
            if (lb) return (lb.textContent || '').trim();
        }
        const wrap = el.closest('label');
        if (wrap) return (wrap.textContent || '').trim();
        const prev = el.closest('.form-group, .write_div, tr, li, div');
        if (prev) {
            const lb = prev.querySelector('label, th, .control-label, .sound_only');
            if (lb) return (lb.textContent || '').trim();
        }
        return '';
    };

    const isPlaceholderOption = (opt) => {
        const v = (opt.value || '').trim();
        const t = (opt.textContent || '').trim();
        if (!v) return true;
        if (/^(선택|선택하|--|choose|select)/i.test(t)) return true;
        return false;
    };

    const isCategoryField = (el, label) => {
        const n = (el.name || '').toLowerCase();
        if (catNames.includes(n)) return true;
        if (catLabelRe.test(label)) return true;
        return false;
    };

    // select — 분류(ca_name) 등
    form.querySelectorAll('select').forEach(sel => {
        if (shouldSkip(sel)) return;
        if ((sel.value || '').trim()) return;
        const label = labelOf(sel);
        const opts = Array.from(sel.options).filter(o => !isPlaceholderOption(o));
        if (!opts.length) return;

        let val = opts[0].value;
        if (categoryOverride && isCategoryField(sel, label)) {
            const hit = opts.find(o =>
                o.value === categoryOverride ||
                (o.textContent || '').trim() === categoryOverride
            );
            if (hit) val = hit.value;
        }
        sel.value = val;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        logs.push('select:' + sel.name + '=' + val + (label ? ' (' + label + ')' : ''));
    });

    // radio — 미선택 그룹 첫 항목
    const radioNames = new Set();
    form.querySelectorAll('input[type="radio"]').forEach(r => {
        if (!shouldSkip(r) && r.name) radioNames.add(r.name);
    });
    radioNames.forEach(name => {
        const group = form.querySelectorAll('input[type="radio"][name="' + name + '"]');
        if (Array.from(group).some(r => r.checked)) return;
        const first = Array.from(group).find(r => !r.disabled);
        if (first) {
            first.checked = true;
            first.dispatchEvent(new Event('change', { bubbles: true }));
            logs.push('radio:' + name + '=' + first.value);
        }
    });

    // text — required만 또는 이메일/홈페이지 등
    form.querySelectorAll('input').forEach(el => {
        if (shouldSkip(el)) return;
        const t = el.type || 'text';
        if (!['text', 'email', 'url', 'tel', 'search', ''].includes(t)) return;
        if ((el.value || '').trim()) return;

        const label = labelOf(el);
        const n = (el.name || '').toLowerCase();
        const required = el.required;
        let val = null;

        if (/email|mail/.test(n) || /이메일|email/i.test(label)) val = 'user@example.com';
        else if (/homepage|home|url|link|website/.test(n) || /홈페이지|링크|사이트/i.test(label))
            val = siteUrl || 'https://example.com';
        else if (/tel|phone|hp|mobile/.test(n) || /전화|휴대|연락/i.test(label)) val = '01012345678';
        else if (required) val = '-';

        if (val) {
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            logs.push('text:' + el.name + '=' + val);
        }
    });

    // required checkbox
    form.querySelectorAll('input[type="checkbox"][required]').forEach(el => {
        if (shouldSkip(el) || el.checked) return;
        el.checked = true;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        logs.push('checkbox:' + el.name);
    });

    return logs;
}
"""


def autofill_extra_fields(
    page: Page,
    *,
    site_url: str = "",
    category: str = "",
) -> list[str]:
    """fwrite 폼의 분류·라디오·미입력 필수 필드 자동 채움."""
    logs: list[str] = page.evaluate(
        _AUTOFILL_SCRIPT,
        {
            "skipNames": list(SKIP_FIELD_NAMES),
            "skipPrefix": list(SKIP_FIELD_PREFIXES),
            "catNames": list(CATEGORY_NAME_HINTS),
            "siteUrl": site_url or "",
            "categoryOverride": category or "",
        },
    )

    for msg in logs:
        log.info("자동필드: %s", msg)
    return logs
