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
