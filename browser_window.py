"""헤드리스 OFF 시 브라우저를 앱 모니터에 열기 (드래그 지연 없이)."""

from __future__ import annotations

import sys

from browser_prefs import get_app_window, is_headless

_IS_WIN = sys.platform == "win32"
MONITOR_DEFAULTTONEAREST = 2

# Windows — 창 드래그 시 끊김 완화 (Chromium occlusion 계산 비활성)
_WIN_SMOOTH_WINDOW_ARGS = [
    "--disable-features=CalculateNativeWinOcclusion",
]


def get_app_monitor_work_area() -> tuple[int, int, int, int] | None:
    """Tk 앱 창이 있는 모니터 작업 영역 — (left, top, width, height)."""
    if not _IS_WIN:
        return None
    app = get_app_window()
    if app is None:
        return None
    try:
        hwnd = int(app.winfo_id())
    except Exception:
        return None
    if hwnd <= 0:
        return None

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    work = info.rcWork
    return (
        int(work.left),
        int(work.top),
        int(work.right - work.left),
        int(work.bottom - work.top),
    )


def compute_browser_bounds(
    work_left: int,
    work_top: int,
    work_width: int,
    work_height: int,
) -> tuple[int, int, int, int]:
    """앱 모니터 우하단에 브라우저 배치."""
    margin = 20
    width = min(1120, max(800, work_width - margin * 2))
    height = min(760, max(520, work_height - margin * 2))
    x = work_left + work_width - width - margin
    y = work_top + work_height - height - margin
    return x, y, width, height


def chromium_window_args() -> list[str]:
    """Chromium 실행 인자 — 앱 모니터에 창 열기 (CDP/HWND 조작 없음)."""
    args = [
        "--no-first-run",
        "--no-default-browser-check",
        *_WIN_SMOOTH_WINDOW_ARGS,
    ]
    if is_headless():
        return args
    area = get_app_monitor_work_area()
    if not area:
        return args
    x, y, w, h = compute_browser_bounds(*area)
    args.extend([
        f"--window-position={x},{y}",
        f"--window-size={w},{h}",
    ])
    return args


def place_browser_at_launch(_context, _page) -> None:
    """호환용 no-op — 창 위치는 chromium_window_args()로만 설정 (드래그 지연 방지)."""
