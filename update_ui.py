"""시작 시 업데이트 확인 및 자동 다운로드."""

from __future__ import annotations

import tempfile
import threading
import urllib.error
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from app_constants import APP_NAME, EXE_NAME
from updater import (
    UpdateInfo,
    can_auto_update,
    check_for_update,
    download_file,
    schedule_apply_update,
)


def schedule_update_check(
    root,
    *,
    version_url: str,
    current_version: str,
    app_name: str = APP_NAME,
    exe_name: str = EXE_NAME,
    delay_ms: int = 2500,
    zip_inner_folder: str | None = "BacklinkWriter",
    on_notify=None,
) -> None:
    if not version_url.strip():
        return

    def worker() -> None:
        info = check_for_update(version_url, current_version, app_name=app_name)
        if info is not None:
            root.after(
                0,
                lambda: _handle_update(root, info, current_version, app_name, exe_name, zip_inner_folder, on_notify),
            )

    root.after(delay_ms, lambda: threading.Thread(target=worker, daemon=True).start())


def _handle_update(
    root,
    info: UpdateInfo,
    current_version: str,
    app_name: str,
    exe_name: str,
    zip_inner_folder,
    on_notify,
) -> None:
    summary = f"새 버전 {info.version} (현재 {current_version})"
    if info.notes:
        summary += f" — {info.notes}"

    if on_notify:
        on_notify(summary, error=False)

    if can_auto_update() and info.url:
        if messagebox.askyesno(
            "업데이트",
            f"{summary}\n\n지금 자동으로 다운로드하고 재시작할까요?",
            parent=root,
        ):
            _auto_update(root, info, app_name, exe_name, zip_inner_folder)
        return

    if messagebox.askyesno(
        "업데이트",
        f"{summary}\n\nGitHub에서 zip을 받을까요?",
        parent=root,
    ) and info.url:
        webbrowser.open(info.url)


def _auto_update(root, info: UpdateInfo, app_name: str, exe_name: str, zip_inner_folder):
    dialog = __import__("tkinter").Toplevel(root)
    dialog.title("업데이트 중")
    dialog.geometry("340x100")
    dialog.transient(root)
    dialog.grab_set()

    status = ttk.Label(dialog, text="다운로드 중...")
    status.pack(padx=16, pady=(16, 8))
    bar = ttk.Progressbar(dialog, length=300, mode="determinate")
    bar.pack(padx=16, pady=8)

    def on_progress(done: int, total: int) -> None:
        if total > 0:
            pct = min(int(done * 100 / total), 100)
            root.after(0, lambda: (bar.configure(value=pct), status.configure(text=f"다운로드 {pct}%")))
        else:
            root.after(0, lambda: status.configure(text="다운로드 중..."))

    def worker() -> None:
        zip_path = Path(tempfile.gettempdir()) / f"{app_name}-{info.version}.zip"
        try:
            download_file(
                info.url,
                zip_path,
                user_agent=f"{app_name}/{info.version}",
                on_progress=on_progress,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            root.after(0, dialog.destroy)
            root.after(0, lambda: messagebox.showerror("업데이트 실패", str(exc), parent=root))
            return

        def finish() -> None:
            try:
                schedule_apply_update(
                    zip_path,
                    exe_name=exe_name,
                    zip_inner_folder=zip_inner_folder,
                    app_slug=app_name,
                )
            except RuntimeError as exc:
                messagebox.showerror("업데이트 실패", str(exc), parent=root)
                dialog.destroy()
                return
            dialog.destroy()
            root.quit()

        root.after(0, lambda: status.configure(text="설치 준비 중..."))
        root.after(500, finish)

    threading.Thread(target=worker, daemon=True).start()
