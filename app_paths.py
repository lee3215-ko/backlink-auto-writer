"""사용자 데이터 경로 — 업데이트 시에도 유지 (%APPDATA%)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_FOLDER_NAME = "BacklinkWriter"

_LEGACY_FILES = (
    "app_state.json",
    "post_history.json",
    "board_catalog.json",
    "board_probed.json",
    "auto_search_state.json",
    "backlink.log",
)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_data_dir() -> Path:
    if is_frozen():
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        path = base / APP_FOLDER_NAME
    else:
        path = Path(__file__).resolve().parent / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_file(name: str) -> Path:
    return get_data_dir() / name


def migrate_legacy_data() -> None:
    """설치 폴더 logs/ → AppData (exe 옆 logs가 더 최신이면 덮어씀)."""
    if not is_frozen():
        return
    legacy = get_install_dir() / "logs"
    if not legacy.is_dir():
        return
    target = get_data_dir()
    for name in _LEGACY_FILES:
        src, dst = legacy / name, target / name
        if not src.exists():
            continue
        try:
            if not dst.exists() or src.stat().st_mtime >= dst.stat().st_mtime:
                shutil.copy2(src, dst)
        except OSError:
            pass


def get_playwright_browsers_dir() -> Path:
    """배포 zip에 포함된 Chromium (ms-playwright)."""
    return get_install_dir() / "ms-playwright"


def configure_playwright_env() -> Path | None:
    """exe 실행 시 번들 브라우저 경로 지정 — 미설정 시 _internal 경로를 찾다 실패함."""
    if not is_frozen():
        return None
    browsers = get_playwright_browsers_dir()
    if browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
        return browsers
    return None


def find_chromium_executable() -> Path | None:
    browsers = get_playwright_browsers_dir()
    if not browsers.is_dir():
        return None
    for pattern in (
        "chromium-*/chrome-win/chrome.exe",
        "chromium_headless_shell-*/chrome-win/headless_shell.exe",
    ):
        matches = sorted(browsers.glob(pattern))
        if matches:
            return matches[0]
    return None


def playwright_browsers_ready() -> bool:
    if not is_frozen():
        return True
    return find_chromium_executable() is not None


def playwright_browsers_error_message() -> str:
    install = get_install_dir()
    browsers = get_playwright_browsers_dir()
    return (
        "내장 Chrome(Playwright)을 찾을 수 없습니다.\n\n"
        f"설치 폴더: {install}\n"
        f"필요 경로: {browsers}\\chromium-*\\chrome-win\\chrome.exe\n\n"
        "해결 방법:\n"
        "1) 최신 BacklinkWriter.zip 을 다시 받아 전체 폴더를 덮어쓰기\n"
        "2) ms-playwright 폴더가 exe 옆에 있는지 확인\n"
        "3) 백신이 chrome.exe 를 차단했는지 확인 후 예외 등록"
    )


configure_playwright_env()
