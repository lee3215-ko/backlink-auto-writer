"""Playwright 브라우저 표시(헤드리스) 전역 설정."""

from __future__ import annotations

from typing import Any

_headless: bool = False
_app_window: Any = None


def is_headless() -> bool:
    return _headless


def set_headless(value: bool) -> None:
    global _headless
    _headless = bool(value)


def set_app_window(widget: Any) -> None:
    """메인 Tk 창 — 브라우저를 같은 모니터에 배치할 때 사용."""
    global _app_window
    _app_window = widget


def get_app_window() -> Any:
    return _app_window
