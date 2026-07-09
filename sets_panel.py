"""콘텐츠 세트 전용 탭 — 목록 + 편집 분할 UI."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable

from batch_jobs import ContentSet, parse_lines, preview_post_links

COLORS = {
    "bg": "#f0f4f8",
    "card": "#ffffff",
    "header": "#1e3a5f",
    "accent": "#2563eb",
    "accent_light": "#dbeafe",
    "text": "#1e293b",
    "muted": "#64748b",
    "border": "#cbd5e1",
    "set_bg": "#f8fafc",
    "list_sel": "#2563eb",
}

FONT = "맑은 고딕"
FONT_MONO = "Consolas"


class ContentSetsTab(ttk.Frame):
    """세트 목록(좌) + 편집(우) + 미리보기(하)."""

    def __init__(self, parent, on_change: Callable | None = None) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._ready = False
        self._current: int = -1
        self._data: list[dict] = []  # {url, keywords_text}
        self._block_save = False
        self._block_select_event = False

        self._build_toolbar()
        self._build_split()
        self._build_preview()
        self._rebuild_tree()
        self._ready = True

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1)
        bar.pack(fill="x", pady=(0, 8), padx=2)

        inner = tk.Frame(bar, bg=COLORS["card"], padx=10, pady=8)
        inner.pack(fill="x")

        tk.Label(inner, text="콘텐츠 세트 관리", bg=COLORS["card"], fg=COLORS["header"], font=(FONT, 11, "bold")).pack(side="left")
        self.count_var = tk.StringVar(value="0개")
        tk.Label(inner, textvariable=self.count_var, bg=COLORS["card"], fg=COLORS["accent"], font=(FONT, 9, "bold")).pack(side="right")

        btn = tk.Frame(bar, bg=COLORS["card"], padx=10)
        btn.pack(fill="x", pady=(0, 8))
        for text, cmd in [
            ("+ 세트 추가", self._add_new),
            ("예시 불러오기", self._load_example),
            ("선택 삭제", self._delete_selected),
        ]:
            tk.Button(
                btn, text=text, command=cmd, bg=COLORS["accent_light"], fg=COLORS["header"],
                relief="flat", padx=10, pady=4, cursor="hand2", font=(FONT, 9),
            ).pack(side="left", padx=(0, 6))

        tk.Label(
            bar,
            text="세트마다 백링크 URL 1개 + 키워드 여러 개 · URL은 https:// 포함 권장 (Netlify/Manus 등)",
            bg=COLORS["card"], fg=COLORS["muted"], font=(FONT, 8), padx=10,
        ).pack(anchor="w", pady=(0, 8))

    def _build_split(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=4)

        # ── 좌: 세트 목록 ──
        left = ttk.LabelFrame(paned, text="세트 목록", padding=4)
        paned.add(left, weight=2)

        cols = ("no", "url", "kw")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=14, selectmode="browse")
        self.tree.heading("no", text="#")
        self.tree.heading("url", text="사이트 URL")
        self.tree.heading("kw", text="키워드")
        self.tree.column("no", width=36, anchor="center")
        self.tree.column("url", width=280)
        self.tree.column("kw", width=56, anchor="center")

        search_row = tk.Frame(left, bg=COLORS["card"])
        search_row.pack(fill="x", pady=(0, 4))
        tk.Label(search_row, text="검색", bg=COLORS["card"], fg=COLORS["muted"], font=(FONT, 9)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_search_filter())
        tk.Entry(search_row, textvariable=self.search_var, font=(FONT_MONO, 9)).pack(
            side="left", fill="x", expand=True, padx=6,
        )
        self.search_filter_label = tk.Label(search_row, text="", bg=COLORS["card"], fg=COLORS["accent"], font=(FONT, 8))
        self.search_filter_label.pack(side="right")

        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # ── 우: 편집 ──
        right = ttk.LabelFrame(paned, text="세트 편집", padding=10)
        paned.add(right, weight=3)

        self.edit_title = tk.Label(right, text="세트를 선택하세요", font=(FONT, 10, "bold"))
        self.edit_title.pack(anchor="w", pady=(0, 8))

        tk.Label(right, text="사이트 URL", font=(FONT, 9), foreground=COLORS["muted"]).pack(anchor="w")
        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", lambda *_: self._save_editor())
        tk.Entry(right, textvariable=self.url_var, font=(FONT_MONO, 10)).pack(fill="x", pady=(2, 10))

        tk.Label(right, text="키워드 (한 줄에 하나 · 게시물마다 1개씩 순환)", font=(FONT, 9), foreground=COLORS["muted"]).pack(anchor="w")
        self.kw_box = scrolledtext.ScrolledText(right, height=10, font=(FONT, 10), wrap="word", relief="solid", bd=1)
        self.kw_box.pack(fill="both", expand=True, pady=(2, 0))
        self.kw_box.bind("<KeyRelease>", lambda _e: self._save_editor())

    def _build_preview(self) -> None:
        frame = tk.Frame(self, bg=COLORS["set_bg"], highlightbackground=COLORS["border"], highlightthickness=1)
        frame.pack(fill="x", pady=(8, 0))
        tk.Label(frame, text="원고 미리보기 (링크·키워드 분산)", bg=COLORS["set_bg"], fg=COLORS["muted"], font=(FONT, 8)).pack(
            anchor="w", padx=10, pady=(6, 0)
        )
        self.preview_box = scrolledtext.ScrolledText(
            frame, height=7, font=(FONT_MONO, 9), bg=COLORS["set_bg"], fg=COLORS["text"],
            relief="flat", padx=10, pady=6, state="disabled",
        )
        self.preview_box.pack(fill="x", padx=4, pady=4)

    def _add_set_data(self, url: str = "", keywords: str = "") -> None:
        self._data.append({"url": url, "keywords_text": keywords})

    def _search_query(self) -> str:
        return self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""

    def _matches_search(self, item: dict) -> bool:
        q = self._search_query()
        if not q:
            return True
        if q in item["url"].strip().lower():
            return True
        return q in item["keywords_text"].lower()

    def _apply_search_filter(self) -> None:
        if not hasattr(self, "search_var"):
            return
        visible = 0
        for i, item in enumerate(self._data):
            iid = str(i)
            if not self.tree.exists(iid):
                continue
            if self._matches_search(item):
                try:
                    self.tree.reattach(iid, "", "end")
                except tk.TclError:
                    pass
                visible += 1
            else:
                self.tree.detach(iid)
        total = len(self._data)
        q = self._search_query()
        if q:
            self.search_filter_label.configure(text=f"{visible}/{total}")
        else:
            self.search_filter_label.configure(text="")

    def _rebuild_tree(self, reselect: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self._data):
            url = item["url"].strip()
            kws = parse_lines(item["keywords_text"])
            preview = (url[:38] + "…") if len(url) > 39 else (url or "(URL 없음)")
            iid = str(i)
            self.tree.insert("", "end", iid=iid, values=(i + 1, preview, f"{len(kws)}개"))
        self.count_var.set(f"{len(self._data)}개 세트")
        self._apply_search_filter()
        if reselect is not None:
            self._set_tree_selection(reselect)

    def _set_tree_selection(self, index: int) -> None:
        iid = str(index)
        if not self.tree.exists(iid):
            return
        self._block_select_event = True
        try:
            self.tree.selection_set(iid)
            self.tree.see(iid)
        finally:
            self._block_select_event = False

    def _load_editor(self, index: int) -> None:
        if not self._data or index < 0 or index >= len(self._data):
            return
        self._current = index
        self._block_save = True
        try:
            item = self._data[index]
            self.url_var.set(item["url"])
            self.kw_box.delete("1.0", "end")
            self.kw_box.insert("1.0", item["keywords_text"].strip())
            self.edit_title.config(text=f"세트 #{index + 1} 편집")
        finally:
            self._block_save = False

    def _select(self, index: int) -> None:
        self._load_editor(index)
        self._set_tree_selection(index)

    def _on_tree_select(self, _event=None) -> None:
        if self._block_select_event:
            return
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx == self._current:
            return
        self._load_editor(idx)

    def _save_editor(self) -> None:
        if self._block_save or self._current < 0:
            return
        self._data[self._current]["url"] = self.url_var.get()
        self._data[self._current]["keywords_text"] = self.kw_box.get("1.0", "end")
        self._rebuild_tree(reselect=self._current)
        self._refresh_preview()
        self._notify()

    def _add_new(self) -> None:
        self._add_set_data("", "")
        self._rebuild_tree()
        self._select(len(self._data) - 1)
        self._notify()

    def _delete_selected(self) -> None:
        if self._current < 0 or not self._data:
            return
        if len(self._data) == 1:
            messagebox.showinfo("안내", "최소 1개 세트는 필요합니다.")
            return
        if not messagebox.askyesno("삭제", f"세트 #{self._current + 1}을 삭제할까요?"):
            return
        del self._data[self._current]
        self._rebuild_tree()
        self._select(min(self._current, len(self._data) - 1))
        self._notify()

    def _load_example(self) -> None:
        self._data.clear()
        self._add_set_data("https://hwangticket.com", "카드깡\n카드깡업체\n카드깡수수료")
        self._add_set_data("https://cardcashout.com", "신속입금\n최저수수료\n5분입금")
        self._rebuild_tree()
        self._select(0)
        self._notify()

    def _refresh_preview(self) -> None:
        self.preview_box.config(state="normal")
        self.preview_box.delete("1.0", "end")
        try:
            sets = self.get_content_sets()
            if not sets:
                self.preview_box.insert("1.0", "세트를 추가하세요")
            else:
                lines = [f"1번째 글: {preview_post_links(sets, 0)}"]
                if any(len(s.keywords) > 1 for s in sets):
                    lines.append(f"2번째 글: {preview_post_links(sets, 1)}")
                    lines.append(f"3번째 글: {preview_post_links(sets, 2)}")
                self.preview_box.insert("1.0", "\n".join(lines))
        except Exception as e:
            self.preview_box.insert("1.0", str(e))
        self.preview_box.config(state="disabled")

    def _notify(self) -> None:
        if self._ready and self._on_change:
            self._on_change()

    def export_data(self) -> list[dict]:
        """저장용 — 편집 중인 세트 내용 포함."""
        if self._current >= 0 and not self._block_save:
            self._data[self._current]["url"] = self.url_var.get()
            self._data[self._current]["keywords_text"] = self.kw_box.get("1.0", "end")
        return [
            {"url": item["url"], "keywords_text": item["keywords_text"].strip()}
            for item in self._data
        ]

    def import_data(self, items: list[dict]) -> None:
        """저장된 세트 목록 복원."""
        self._data.clear()
        for item in items:
            self._add_set_data(
                item.get("url", ""),
                item.get("keywords_text", ""),
            )
        if not self._data:
            self._add_set_data()
        self._rebuild_tree()
        self._select(0)
        self._refresh_preview()
        self._notify()

    def get_content_sets(self) -> list[ContentSet]:
        sets: list[ContentSet] = []
        for i, item in enumerate(self._data, 1):
            url = item["url"].strip()
            keywords = parse_lines(item["keywords_text"])
            sets.append(ContentSet(index=i, site_url=url, keywords=keywords))
        return sets


# 하위 호환
SetsPanel = ContentSetsTab
