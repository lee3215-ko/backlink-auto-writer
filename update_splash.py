"""시작 시 업데이트 스플래시."""

from __future__ import annotations

import tkinter as tk


class UpdateSplash:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("백링크 자동 글쓰기")
        self.root.geometry("360x90")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e3a5f")
        self.label = tk.Label(
            self.root,
            text="시작 중...",
            bg="#1e3a5f",
            fg="#ffffff",
            font=("맑은 고딕", 10),
        )
        self.label.pack(expand=True)
        self.root.update()

    def set_status(self, text: str) -> None:
        self.label.config(text=text)
        self.root.update()

    def close(self) -> None:
        self.root.destroy()
