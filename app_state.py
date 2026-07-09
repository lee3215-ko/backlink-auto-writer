"""프로그램 작업 상태 저장·복원 (재시작 후 유지)."""

from __future__ import annotations

import json
from pathlib import Path

from app_paths import data_file, migrate_legacy_data
from browser_prefs import set_headless

STATE_FILE = data_file("app_state.json")

DEFAULT_STATE: dict = {
    "version": 1,
    "content_sets": [],
    "board_urls": "",
    "titles": "",
    "category": "",
    "write": {
        "post_interval_min": 0,
        "repeat_interval_min": 30,
        "continuous": False,
    },
    "discover": {
        "search_queries": "",
        "seeds": "",
        "delay": 1.5,
        "depth": 1,
        "search_results": 20,
        "continuous": True,
        "search_enabled": True,
        "auto_mode": True,
        "cycle_interval_min": 0,
        "filter": "호환",
    },
    "window": {"geometry": "1120x860"},
    "browser": {"headless": False},
    "ai": {
        "openai_api_key": "",
        "openai_api_base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}


def load_state() -> dict:
    migrate_legacy_data()
    if not STATE_FILE.exists():
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state = json.loads(json.dumps(DEFAULT_STATE))
        state.update({k: v for k, v in raw.items() if k not in ("discover", "write")})
        if isinstance(raw.get("discover"), dict):
            state["discover"].update(raw["discover"])
        if isinstance(raw.get("write"), dict):
            state["write"].update(raw["write"])
        if isinstance(raw.get("window"), dict):
            state["window"].update(raw["window"])
        if isinstance(raw.get("browser"), dict):
            state.setdefault("browser", {}).update(raw["browser"])
        if isinstance(raw.get("ai"), dict):
            state.setdefault("ai", {}).update(raw["ai"])
        return state
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_from_app(app) -> dict:
    """BacklinkApp 인스턴스에서 현재 입력값 수집."""
    state = load_state()

    if hasattr(app, "sets_panel"):
        state["content_sets"] = app.sets_panel.export_data()

    state["board_urls"] = app._get_text("urls") if hasattr(app, "urls_box") else ""
    state["titles"] = app._get_text("titles") if hasattr(app, "titles_box") else ""
    if hasattr(app, "category_var"):
        state["category"] = app.category_var.get()

    w = state["write"]
    if hasattr(app, "write_post_interval_var"):
        w["post_interval_min"] = float(app.write_post_interval_var.get())
    if hasattr(app, "write_repeat_interval_var"):
        w["repeat_interval_min"] = float(app.write_repeat_interval_var.get())
    if hasattr(app, "write_continuous_var"):
        w["continuous"] = bool(app.write_continuous_var.get())

    d = state["discover"]
    if hasattr(app, "discover_search_box"):
        d["search_queries"] = app.discover_search_box.get("1.0", "end").strip()
    if hasattr(app, "discover_seed_box"):
        d["seeds"] = app.discover_seed_box.get("1.0", "end").strip()
    if hasattr(app, "discover_delay_var"):
        d["delay"] = float(app.discover_delay_var.get())
    if hasattr(app, "discover_depth_var"):
        d["depth"] = int(app.discover_depth_var.get())
    if hasattr(app, "discover_search_n_var"):
        d["search_results"] = int(app.discover_search_n_var.get())
    if hasattr(app, "discover_continuous_var"):
        d["continuous"] = bool(app.discover_continuous_var.get())
    if hasattr(app, "discover_search_var"):
        d["search_enabled"] = bool(app.discover_search_var.get())
    if hasattr(app, "discover_cycle_min_var"):
        d["cycle_interval_min"] = float(app.discover_cycle_min_var.get())
    if hasattr(app, "discover_auto_var"):
        d["auto_mode"] = bool(app.discover_auto_var.get())
    if hasattr(app, "discover_filter_var"):
        d["filter"] = app.discover_filter_var.get()

    try:
        state["window"]["geometry"] = app.geometry()
    except Exception:
        pass

    if hasattr(app, "headless_var"):
        state.setdefault("browser", {})["headless"] = bool(app.headless_var.get())

    if hasattr(app, "ai_api_key_var"):
        state.setdefault("ai", {})["openai_api_key"] = app.ai_api_key_var.get().strip()

    return state


def apply_to_app(app, state: dict | None = None) -> None:
    """저장된 상태를 GUI에 반영."""
    if state is None:
        state = load_state()

    if hasattr(app, "sets_panel"):
        sets = state.get("content_sets") or []
        if sets:
            app.sets_panel.import_data(sets)
        elif not app.sets_panel._data:
            app.sets_panel.import_data([
                {"url": "https://hwangticket.com", "keywords_text": "카드깡\n카드깡업체\n카드깡수수료"},
                {"url": "https://cardcashout.com", "keywords_text": "신속입금\n최저수수료"},
            ])

    if hasattr(app, "urls_box") and state.get("board_urls"):
        app.urls_box.delete("1.0", "end")
        app.urls_box.insert("1.0", state["board_urls"].rstrip() + "\n")

    if hasattr(app, "titles_box") and state.get("titles"):
        app.titles_box.delete("1.0", "end")
        app.titles_box.insert("1.0", state["titles"].rstrip() + "\n")

    if hasattr(app, "category_var"):
        app.category_var.set(state.get("category", ""))

    w = state.get("write") or {}
    if hasattr(app, "write_post_interval_var"):
        app.write_post_interval_var.set(w.get("post_interval_min", 0))
    if hasattr(app, "write_repeat_interval_var"):
        app.write_repeat_interval_var.set(w.get("repeat_interval_min", 30))
    if hasattr(app, "write_continuous_var"):
        app.write_continuous_var.set(w.get("continuous", False))

    d = state.get("discover") or {}
    if hasattr(app, "discover_search_box"):
        app.discover_search_box.delete("1.0", "end")
        text = d.get("search_queries", "")
        if text:
            app.discover_search_box.insert("1.0", text)
        elif not d.get("auto_mode", True):
            from board_search import all_preset_lines
            app.discover_search_box.insert("1.0", all_preset_lines())

    if hasattr(app, "discover_seed_box"):
        app.discover_seed_box.delete("1.0", "end")
        if d.get("seeds"):
            app.discover_seed_box.insert("1.0", d["seeds"])

    if hasattr(app, "discover_delay_var"):
        app.discover_delay_var.set(d.get("delay", 1.5))
    if hasattr(app, "discover_depth_var"):
        app.discover_depth_var.set(d.get("depth", 1))
    if hasattr(app, "discover_search_n_var"):
        app.discover_search_n_var.set(d.get("search_results", 20))
    if hasattr(app, "discover_continuous_var"):
        app.discover_continuous_var.set(d.get("continuous", True))
    if hasattr(app, "discover_search_var"):
        app.discover_search_var.set(d.get("search_enabled", True))
    if hasattr(app, "discover_cycle_min_var"):
        app.discover_cycle_min_var.set(d.get("cycle_interval_min", 0))
    if hasattr(app, "discover_auto_var"):
        app.discover_auto_var.set(d.get("auto_mode", True))
    if hasattr(app, "discover_filter_var"):
        app.discover_filter_var.set(d.get("filter", "호환"))

    geo = (state.get("window") or {}).get("geometry")
    if geo:
        try:
            app.geometry(geo)
        except Exception:
            pass

    browser = state.get("browser") or {}
    if hasattr(app, "headless_var"):
        headless = bool(browser.get("headless", False))
        app.headless_var.set(headless)
        set_headless(headless)

    ai = state.get("ai") or {}
    if hasattr(app, "ai_api_key_var"):
        app.ai_api_key_var.set(ai.get("openai_api_key", ""))
