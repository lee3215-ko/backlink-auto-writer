"""Windows Tk UI bootstrap — DPI·창 이동 시 끊김 완화."""

from __future__ import annotations

import sys
from typing import Callable

_IS_WIN = sys.platform == "win32"


def bootstrap_before_tk() -> None:
    """Tk 생성 전 호출 — 고DPI·창 이동 렌더 지연 완화."""
    if not _IS_WIN:
        return
    try:
        import ctypes

        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def install_window_move_guard(
    root,
    *,
    on_move_start: Callable[[], None] | None = None,
    on_move_end: Callable[[], None] | None = None,
) -> None:
    """창 이동(위치만 변경) 중에는 on_move_end 콜백을 지연."""
    if not _IS_WIN:
        return

    state = {"w": 0, "h": 0, "after_id": None, "moving": False}

    def _finish() -> None:
        state["after_id"] = None
        state["moving"] = False
        if on_move_end:
            on_move_end()

    def _on_configure(event) -> None:
        if event.widget is not root:
            return
        w, h = int(event.width), int(event.height)
        if state["w"] == w and state["h"] == h:
            if not state["moving"]:
                state["moving"] = True
                if on_move_start:
                    on_move_start()
            if state["after_id"]:
                root.after_cancel(state["after_id"])
            state["after_id"] = root.after(180, _finish)
            return
        state["w"], state["h"] = w, h

    root.bind("<Configure>", _on_configure, add="+")
