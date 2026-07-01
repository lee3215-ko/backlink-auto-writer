"""백링크 게시판 자동 글쓰기 GUI."""

from __future__ import annotations

import hashlib
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from app_constants import APP_DISPLAY_NAME, APP_VERSION
from app_logger import log
from app_state import apply_to_app, collect_from_app, save_state
from batch_jobs import PostJob, build_jobs, parse_lines, normalize_board_url
from board_writer import BoardWriter, random_english_name
from post_history import PostHistory, PostRecord
from board_catalog import STATUS_LABEL, BoardCatalog
from board_discoverer import BoardDiscoverer, DiscovererStats
from board_search import SEARCH_PRESETS, preset_text
from browser_prefs import is_headless, set_headless
from sets_panel import ContentSetsTab
from startup_update import try_startup_update
from update_splash import UpdateSplash

COLORS = {
    "bg": "#f0f4f8",
    "card": "#ffffff",
    "header": "#1e3a5f",
    "accent": "#2563eb",
    "accent_light": "#dbeafe",
    "accent_dark": "#1d4ed8",
    "tab_idle": "#dce3ed",
    "tab_hover": "#c8d4e3",
    "text": "#1e293b",
    "muted": "#64748b",
    "border": "#cbd5e1",
    "log_bg": "#0f172a",
    "log_fg": "#e2e8f0",
}

FONT = "맑은 고딕"
FONT_MONO = "Consolas"


def _job_links(job: PostJob) -> list[tuple[str, str]]:
    return [(l.site_url, l.keyword) for l in job.links]


class BacklinkApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_DISPLAY_NAME} v{APP_VERSION}")
        self.geometry("1120x860")
        self.minsize(940, 720)
        self.configure(bg=COLORS["bg"])

        self.writer = BoardWriter()
        self.history = PostHistory()
        self.catalog = BoardCatalog()
        self.discoverer = BoardDiscoverer(
            self.catalog,
            on_log=lambda m: self.after(0, lambda msg=m: self._discover_log(msg)),
            on_entry=lambda e: self.after(0, lambda ent=e: self._on_catalog_entry(ent)),
            on_stats=lambda s: self.after(0, lambda st=s: self._on_discover_stats(st)),
            on_compatible=lambda e: self.after(0, lambda ent=e: self._on_compatible_found(ent)),
        )
        self._discover_thread: threading.Thread | None = None
        self._catalog_iid_to_key: dict[str, str] = {}
        self._save_after_id: str | None = None
        self._toast_after_id: str | None = None
        self._busy = False
        self._batch_jobs: list[PostJob] = []
        self._write_post_interval_sec = 0.0
        self._write_repeat_interval_sec = 0.0
        self._write_continuous = False
        self._batch_round_lock = threading.Lock()
        self._urls_list_syncing = False
        self._url_check_rows: list[tuple[str, tk.BooleanVar, ttk.Checkbutton]] = []

        self._setup_styles()
        self._build_ui()
        apply_to_app(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_autosave()
        self._sync_urls_pick_list(preserve_selection=False)
        self._refresh_counts()
        self._refresh_history_tree()

    def _on_headless_toggle(self) -> None:
        set_headless(bool(self.headless_var.get()))
        if self.writer.is_open():
            self.writer.close()
            if hasattr(self, "status_var"):
                self.status_var.set("브라우저 닫음 — 다음 작업부터 헤드리스 설정 적용")
        self._schedule_save()
        if self.headless_var.get():
            self._show_toast("헤드리스 켜짐 — 브라우저 창 없이 실행")
        else:
            self._show_toast("헤드리스 꺼짐 — 브라우저 창이 표시됩니다")

    def _show_toast(self, message: str, *, error: bool = False) -> None:
        if self._toast_after_id:
            self.after_cancel(self._toast_after_id)
        bg = "#fee2e2" if error else "#d1fae5"
        fg = "#991b1b" if error else "#065f46"
        self.toast_frame.configure(bg=bg)
        self.toast_label.configure(bg=bg, fg=fg, text=message)
        self.toast_frame.pack(fill="x", after=self.header_frame)
        self._toast_after_id = self.after(6000, self._hide_toast)

    def _hide_toast(self) -> None:
        self._toast_after_id = None
        self.toast_frame.pack_forget()

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=(FONT, 10))
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure("Card.TLabelframe", background=COLORS["card"], bordercolor=COLORS["border"])
        style.configure("Card.TLabelframe.Label", background=COLORS["card"], foreground=COLORS["header"], font=(FONT, 10, "bold"))
        style.configure("Count.TLabel", background=COLORS["card"], foreground=COLORS["accent"], font=(FONT, 9, "bold"))
        style.configure("Hint.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=(FONT, 8))
        style.configure("Status.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=(FONT, 10))
        style.configure("Accent.TButton", font=(FONT, 10, "bold"), padding=(12, 6))

        # 탭 스타일 — 선택된 탭 강조
        style.configure(
            "TNotebook",
            background=COLORS["bg"],
            borderwidth=0,
            tabmargins=(4, 4, 4, 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(22, 10),
            font=(FONT, 10),
            background=COLORS["tab_idle"],
            foreground=COLORS["text"],
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", COLORS["accent"]),
                ("active", COLORS["tab_hover"]),
                ("!selected", COLORS["tab_idle"]),
            ],
            foreground=[
                ("selected", "#ffffff"),
                ("active", COLORS["header"]),
                ("!selected", COLORS["muted"]),
            ],
            font=[("selected", (FONT, 10, "bold"))],
            expand=[("selected", [1, 2, 1, 0])],
        )

    def _build_ui(self) -> None:
        self.header_frame = tk.Frame(self, bg=COLORS["header"], height=64)
        self.header_frame.pack(fill="x")
        self.header_frame.pack_propagate(False)
        tk.Label(
            self.header_frame, text=APP_DISPLAY_NAME, bg=COLORS["header"], fg="#fff",
            font=(FONT, 16, "bold"),
        ).pack(side="left", padx=20, pady=16)
        tk.Label(
            self.header_frame,
            text="콘텐츠 세트 · 게시판 수집 · 순차 등록",
            bg=COLORS["header"],
            fg="#94a3b8",
            font=(FONT, 9),
        ).pack(side="left")
        tk.Label(
            self.header_frame, text=f"v{APP_VERSION}",
            bg=COLORS["header"], fg="#64748b", font=(FONT, 9),
        ).pack(side="right", padx=(0, 12))
        self.headless_var = tk.BooleanVar(value=False)
        self.headless_btn = tk.Checkbutton(
            self.header_frame,
            text="헤드리스",
            variable=self.headless_var,
            command=self._on_headless_toggle,
            bg=COLORS["header"],
            fg="#e2e8f0",
            selectcolor="#334155",
            activebackground=COLORS["header"],
            activeforeground="#ffffff",
            font=(FONT, 9),
            cursor="hand2",
            indicatoron=True,
        )
        self.headless_btn.pack(side="right", padx=8, pady=16)

        self.toast_frame = tk.Frame(self, bg="#d1fae5", height=34)
        self.toast_frame.pack_propagate(False)
        self.toast_label = tk.Label(
            self.toast_frame, text="", bg="#d1fae5", fg="#065f46", font=(FONT, 9), anchor="w",
        )
        self.toast_label.pack(fill="both", expand=True, padx=16, pady=6)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=10)

        self._build_sets_tab()
        self._build_discover_tab()
        self._build_write_tab()
        self._build_history_tab()

    def _build_sets_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  콘텐츠 세트  ")
        self.sets_panel = ContentSetsTab(tab, on_change=self._on_sets_changed)
        self.sets_panel.pack(fill="both", expand=True, padx=4, pady=4)

    def _go_write_tab(self) -> None:
        self.notebook.select(2)

    def _build_discover_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  게시판 수집  ")

        top = ttk.PanedWindow(tab, orient="horizontal")
        top.pack(fill="both", expand=True)

        left = ttk.Frame(top)
        top.add(left, weight=2)

        seed_f = ttk.LabelFrame(left, text="검색어 (선택 · 수동 추가)", style="Card.TLabelframe", padding=6)
        seed_f.pack(fill="both", expand=True, pady=(0, 6))
        ttk.Label(
            seed_f,
            text="자동 모드면 비워도 됨 · 키워드는 프로그램이 랜덤 생성",
            style="Hint.TLabel",
        ).pack(anchor="w")
        self.discover_search_box = scrolledtext.ScrolledText(seed_f, height=5, font=(FONT_MONO, 9))
        self.discover_search_box.pack(fill="both", expand=True, pady=4)

        preset_row = ttk.Frame(seed_f)
        preset_row.pack(fill="x", pady=2)
        ttk.Label(preset_row, text="프리셋:", style="Hint.TLabel").pack(side="left")
        for name in SEARCH_PRESETS:
            ttk.Button(
                preset_row, text=name, width=10,
                command=lambda n=name: self._load_search_preset(n),
            ).pack(side="left", padx=2)

        crawl_f = ttk.LabelFrame(left, text="시드 URL (선택 · 사이트 크롤)", style="Card.TLabelframe", padding=6)
        crawl_f.pack(fill="x", pady=(0, 6))
        ttk.Label(
            crawl_f,
            text="검색만으로도 수집 가능 · 시드는 해당 사이트 내부 링크 추가 탐색용",
            style="Hint.TLabel",
        ).pack(anchor="w")
        self.discover_seed_box = scrolledtext.ScrolledText(crawl_f, height=3, font=(FONT_MONO, 9))
        self.discover_seed_box.pack(fill="x", pady=4)

        opt_f = ttk.LabelFrame(left, text="수집 설정", style="Card.TLabelframe", padding=6)
        opt_f.pack(fill="x", pady=4)
        self.discover_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt_f,
            text="자동 수집 (랜덤 키워드 · 호환 게시판만 카탈로그 저장)",
            variable=self.discover_auto_var,
        ).pack(anchor="w")
        row1 = ttk.Frame(opt_f)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="요청 간격(초)", style="Hint.TLabel").pack(side="left")
        self.discover_delay_var = tk.DoubleVar(value=1.5)
        ttk.Spinbox(row1, from_=0.5, to=10.0, increment=0.5, textvariable=self.discover_delay_var, width=6).pack(side="left", padx=8)
        ttk.Label(row1, text="검색결과", style="Hint.TLabel").pack(side="left", padx=(12, 0))
        self.discover_search_n_var = tk.IntVar(value=20)
        ttk.Spinbox(row1, from_=5, to=50, textvariable=self.discover_search_n_var, width=4).pack(side="left", padx=8)
        row1b = ttk.Frame(opt_f)
        row1b.pack(fill="x", pady=2)
        ttk.Label(row1b, text="크롤 깊이", style="Hint.TLabel").pack(side="left")
        self.discover_depth_var = tk.IntVar(value=1)
        ttk.Spinbox(row1b, from_=0, to=5, textvariable=self.discover_depth_var, width=4).pack(side="left", padx=8)
        ttk.Label(row1b, text="수동 모드 전용", style="Hint.TLabel").pack(side="left", padx=4)
        row2 = ttk.Frame(opt_f)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="반복 대기(분)", style="Hint.TLabel").pack(side="left")
        self.discover_cycle_min_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(row2, from_=0, to=1440, increment=1, textvariable=self.discover_cycle_min_var, width=6).pack(side="left", padx=8)
        ttk.Label(row2, text="수동 모드 · 자동은 대기 없이 계속", style="Hint.TLabel").pack(side="left", padx=4)
        self.discover_continuous_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_f, text="연속 수집", variable=self.discover_continuous_var).pack(anchor="w")
        self.discover_search_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_f, text="수동 검색어 보조 사용", variable=self.discover_search_var).pack(anchor="w")

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=6)
        self.discover_start_btn = ttk.Button(btn_row, text="▶ 자동 수집 시작", style="Accent.TButton", command=self._on_discover_start)
        self.discover_start_btn.pack(side="left", padx=(0, 6))
        self.discover_stop_btn = ttk.Button(btn_row, text="■ 중지", command=self._on_discover_stop, state="disabled")
        self.discover_stop_btn.pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="이력→프로브", command=self._probe_from_history).pack(side="left")

        self.discover_stats_var = tk.StringVar(value="대기 중")
        ttk.Label(left, textvariable=self.discover_stats_var, style="Count.TLabel").pack(anchor="w")

        log_f = ttk.LabelFrame(left, text="수집 로그", style="Card.TLabelframe", padding=4)
        log_f.pack(fill="both", expand=True, pady=4)
        self.discover_log_box = scrolledtext.ScrolledText(
            log_f, height=8, state="disabled", font=(FONT_MONO, 8),
            bg=COLORS["log_bg"], fg=COLORS["log_fg"], relief="flat",
        )
        self.discover_log_box.pack(fill="both", expand=True)

        right = ttk.Frame(top)
        top.add(right, weight=3)

        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="수집된 게시판 (호환성 검사 결과)", style="Hint.TLabel").pack(side="left")
        self.discover_filter_var = tk.StringVar(value="호환")
        filt = ttk.Combobox(
            bar, textvariable=self.discover_filter_var, width=10, state="readonly",
            values=["전체", "호환", "부분", "로그인", "불가"],
        )
        filt.pack(side="right", padx=4)
        filt.bind("<<ComboboxSelected>>", lambda _e: self._refresh_catalog_tree())
        ttk.Button(bar, text="새로고침", command=self._refresh_catalog_tree).pack(side="right", padx=2)
        ttk.Button(bar, text="삭제", command=self._delete_catalog_entry).pack(side="right")

        tree_f = ttk.LabelFrame(right, text="카탈로그", style="Card.TLabelframe", padding=4)
        tree_f.pack(fill="both", expand=True)

        cols = ("status", "score", "url", "message")
        self.catalog_tree = ttk.Treeview(tree_f, columns=cols, show="headings", height=14, selectmode="extended")
        self.catalog_tree.heading("status", text="상태")
        self.catalog_tree.heading("score", text="점수")
        self.catalog_tree.heading("url", text="게시판 URL")
        self.catalog_tree.heading("message", text="메모")
        self.catalog_tree.column("status", width=56, anchor="center")
        self.catalog_tree.column("score", width=40, anchor="center")
        self.catalog_tree.column("url", width=320)
        self.catalog_tree.column("message", width=200)
        self.catalog_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(tree_f, orient="vertical", command=self.catalog_tree.yview)
        sb.pack(side="right", fill="y")
        self.catalog_tree.configure(yscrollcommand=sb.set)
        self.catalog_tree.bind("<<TreeviewSelect>>", self._on_catalog_select)
        self.catalog_tree.bind("<Control-c>", self._on_catalog_copy)
        self.catalog_tree.bind("<Control-C>", self._on_catalog_copy)
        self.catalog_tree.bind("<Delete>", self._on_catalog_delete_key)

        act = ttk.Frame(right)
        act.pack(fill="x", pady=6)
        ttk.Button(act, text="선택 → 글 작성", command=self._send_catalog_to_write).pack(side="left", padx=(0, 6))
        ttk.Button(act, text="호환 전체 → 글 작성", command=self._send_all_compatible).pack(side="left", padx=(0, 6))
        ttk.Button(act, text="카탈로그 비우기", command=self._clear_catalog).pack(side="right")

        self.catalog_detail = scrolledtext.ScrolledText(
            right, height=6, font=(FONT_MONO, 9), state="disabled", bg="#f8fafc", relief="flat",
        )
        self.catalog_detail.pack(fill="x", pady=4)

        self._refresh_catalog_tree()

    def _on_sets_changed(self) -> None:
        self._refresh_counts()
        self._schedule_save()

    def _on_input_changed(self) -> None:
        self._refresh_counts()
        self._schedule_save()

    def _bind_autosave(self) -> None:
        self.category_var.trace_add("write", lambda *_: self._schedule_save())
        for box in (
            getattr(self, "discover_search_box", None),
            getattr(self, "discover_seed_box", None),
        ):
            if box:
                box.bind("<KeyRelease>", lambda _e: self._schedule_save())
        self.headless_var.trace_add("write", lambda *_: self._schedule_save())
        for var in (
            self.discover_delay_var,
            self.discover_depth_var,
            self.discover_search_n_var,
            self.discover_continuous_var,
            self.discover_search_var,
            self.discover_auto_var,
            self.discover_cycle_min_var,
            self.write_post_interval_var,
            self.write_repeat_interval_var,
            self.write_continuous_var,
        ):
            var.trace_add("write", lambda *_: self._schedule_save())

    def _schedule_save(self) -> None:
        if self._save_after_id:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(600, self._save_app_state)

    def _save_app_state(self) -> None:
        self._save_after_id = None
        try:
            save_state(collect_from_app(self))
        except Exception as e:
            log.warning("상태 저장 실패: %s", e)

    def _discover_log(self, msg: str) -> None:
        self.discover_log_box.config(state="normal")
        self.discover_log_box.insert("end", msg + "\n")
        self.discover_log_box.see("end")
        self.discover_log_box.config(state="disabled")

    def _on_discover_stats(self, stats: DiscovererStats) -> None:
        st = self.catalog.stats()
        wait = stats.cycle_wait_sec
        wait_txt = f" · 대기 {wait // 60}분{wait % 60}초" if wait > 0 else ""
        compat = st.get("compatible", 0)
        partial = st.get("partial", 0)
        self.discover_stats_var.set(
            f"검색 {stats.searches_run} · 프로브 {stats.boards_probed} · "
            f"중복건너뜀 {stats.skipped_duplicate} · "
            f"이번 호환 +{stats.compatible_found} · 카탈로그 호환 {compat} · 부분 {partial} · "
            f"대기(프로브 {stats.queue_probe}){wait_txt}"
        )

    def _on_compatible_found(self, entry) -> None:
        self._refresh_catalog_tree(select_key=entry.board_key)
        url = entry.write_url or entry.board_url
        short = url if len(url) <= 50 else url[:47] + "..."
        self._show_toast(f"호환 게시판 발견 (+1): {short}")

    def _catalog_tree_iid(self, board_key: str) -> str:
        return "c_" + hashlib.md5(board_key.encode()).hexdigest()[:20]

    def _catalog_filter_status(self) -> str | None:
        mapping = {
            "전체": None,
            "호환": "compatible",
            "부분": "partial",
            "로그인": "login",
            "불가": "incompatible",
            # 이전 설정 호환
            "all": None,
            "compatible": "compatible",
            "partial": "partial",
            "login": "login",
            "incompatible": "incompatible",
        }
        return mapping.get(self.discover_filter_var.get(), None)

    def _on_catalog_entry(self, entry) -> None:
        self._refresh_catalog_tree(select_key=entry.board_key)

    def _refresh_catalog_tree(self, select_key: str | None = None) -> None:
        status_filter = self._catalog_filter_status()
        for item in self.catalog_tree.get_children():
            self.catalog_tree.delete(item)
        self._catalog_iid_to_key.clear()
        self.catalog.load()
        for e in self.catalog.list_entries(status_filter=status_filter):
            label = STATUS_LABEL.get(e.status, e.status)
            display = e.board_url if len(e.board_url) <= 55 else e.board_url[:52] + "..."
            iid = self._catalog_tree_iid(e.board_key)
            self._catalog_iid_to_key[iid] = e.board_key
            self.catalog_tree.insert(
                "", "end", iid=iid,
                values=(label, e.score, display, e.message[:60]),
            )
        if select_key:
            iid = self._catalog_tree_iid(select_key)
            if self.catalog_tree.exists(iid):
                self.catalog_tree.selection_set(iid)
                self.catalog_tree.see(iid)

    def _catalog_entry_key(self, tree_iid: str) -> str:
        return self._catalog_iid_to_key.get(tree_iid, tree_iid)

    def _catalog_selected_board_urls(self) -> list[str]:
        urls: list[str] = []
        for iid in self.catalog_tree.selection():
            key = self._catalog_entry_key(iid)
            entry = self.catalog.entries.get(key)
            if entry and entry.board_url:
                urls.append(entry.board_url)
        return urls

    def _on_catalog_copy(self, _event=None) -> str:
        urls = self._catalog_selected_board_urls()
        if not urls:
            return "break"
        self.clipboard_clear()
        self.clipboard_append("\n".join(urls))
        self.update_idletasks()
        n = len(urls)
        self._show_toast(f"게시판 URL {n}개 복사" if n > 1 else "게시판 URL 복사됨")
        return "break"

    def _on_catalog_select(self, _event=None) -> None:
        sel = self.catalog_tree.selection()
        if not sel:
            return
        key = self._catalog_entry_key(sel[0])
        entry = self.catalog.entries.get(key)
        if not entry:
            return
        sig = entry.signals
        lines = [
            f"게시판: {entry.board_url}",
            f"글쓰기: {entry.write_url}",
            f"상태: {STATUS_LABEL.get(entry.status, entry.status)} ({entry.score}점)",
            f"메시지: {entry.message}",
            f"발견: {entry.discovered_at} · 최근검사: {entry.last_probed_at}",
            "",
            "=== 신호 ===",
            f"  fwrite={sig.get('has_fwrite')}  이름={sig.get('has_name')}  비번={sig.get('has_password')}",
            f"  제목={sig.get('has_title')}  내용={sig.get('has_content')}  등록={sig.get('has_submit')}",
            f"  숫자캡차={sig.get('has_numeric_captcha')}  reCAPTCHA={sig.get('has_recaptcha')}",
            f"  HTML={sig.get('has_html_mode')}  에디터={sig.get('editor')}",
        ]
        self.catalog_detail.config(state="normal")
        self.catalog_detail.delete("1.0", "end")
        self.catalog_detail.insert("1.0", "\n".join(lines))
        self.catalog_detail.config(state="disabled")

    def _delete_catalog_entry(self) -> None:
        sel = self.catalog_tree.selection()
        if not sel:
            return
        keys = [self._catalog_entry_key(iid) for iid in sel]
        n = len(keys)
        msg = f"선택한 게시판 {n}개를 카탈로그에서 삭제할까요?" if n > 1 else "선택한 게시판을 카탈로그에서 삭제할까요?"
        if messagebox.askyesno("삭제", msg):
            for key in keys:
                self.catalog.remove(key)
            self._refresh_catalog_tree()
            self.catalog_detail.config(state="normal")
            self.catalog_detail.delete("1.0", "end")
            self.catalog_detail.config(state="disabled")
            self._show_toast(f"카탈로그에서 {n}개 삭제" if n > 1 else "카탈로그에서 삭제됨")

    def _on_catalog_delete_key(self, _event=None) -> str:
        self._delete_catalog_entry()
        return "break"

    def _clear_catalog(self) -> None:
        if messagebox.askyesno("비우기", "수집 카탈로그를 모두 삭제할까요?"):
            self.catalog.clear()
            self._refresh_catalog_tree()
            self.catalog_detail.config(state="normal")
            self.catalog_detail.delete("1.0", "end")
            self.catalog_detail.config(state="disabled")

    def _catalog_urls_for_selection(self) -> list[str]:
        urls: list[str] = []
        for iid in self.catalog_tree.selection():
            entry = self.catalog.entries.get(self._catalog_entry_key(iid))
            if entry:
                urls.append(entry.write_url or entry.board_url)
        return urls

    def _append_urls_to_write(self, urls: list[str]) -> None:
        if not urls:
            self._show_toast("추가할 URL이 없습니다.", error=True)
            return
        existing = set(parse_lines(self._get_text("urls")))
        added = []
        for u in urls:
            if u not in existing:
                added.append(u)
                existing.add(u)
        if not added:
            self._show_toast("이미 글 작성 목록에 있는 URL입니다.", error=True)
            return
        cur = self._get_text("urls").rstrip()
        block = "\n".join(added)
        self.urls_box.insert("end", ("" if not cur else "\n") + block + "\n")
        self._sync_urls_pick_list(preserve_selection=False)
        self._refresh_counts()
        self._schedule_save()
        n = len(added)
        self._show_toast(f"글 작성 목록에 URL {n}개 추가됨" if n > 1 else "글 작성 목록에 URL 추가됨")

    def _send_catalog_to_write(self) -> None:
        self._append_urls_to_write(self._catalog_urls_for_selection())

    def _send_all_compatible(self) -> None:
        self._append_urls_to_write(self.catalog.compatible_urls())

    def _load_search_preset(self, name: str) -> None:
        text = preset_text(name)
        self.discover_search_box.delete("1.0", "end")
        self.discover_search_box.insert("1.0", text)

    def _probe_from_history(self) -> None:
        if self._discover_thread and self._discover_thread.is_alive():
            messagebox.showwarning("안내", "수집이 진행 중입니다.")
            return
        urls = [s.board_url for s in self.history.get_summaries() if s.success_count > 0]
        if not urls:
            messagebox.showinfo("안내", "성공한 게시 이력이 없습니다.")
            return
        self.discoverer.configure(
            delay=float(self.discover_delay_var.get()),
            continuous=False,
        )

        def worker():
            self.discoverer.probe_urls_direct(urls)

        self._discover_thread = threading.Thread(target=worker, daemon=True)
        self.discover_start_btn.config(state="disabled")
        self.discover_stop_btn.config(state="normal")
        self._discover_thread.start()
        self.after(500, self._watch_discover_thread)

    def _on_discover_start(self) -> None:
        if self._discover_thread and self._discover_thread.is_alive():
            messagebox.showwarning("안내", "이미 수집 중입니다.")
            return
        seeds = self.discover_seed_box.get("1.0", "end")
        searches = self.discover_search_box.get("1.0", "end")
        self.discoverer.set_seeds(seeds)
        auto = bool(self.discover_auto_var.get())
        if auto:
            self.discoverer.set_search_queries(searches if self.discover_search_var.get() else "")
        else:
            self.discoverer.set_search_queries(searches)
        if not auto and not self.discoverer._seeds and not self.discoverer._search_queries:
            messagebox.showwarning("입력", "수동 모드: 검색어 또는 시드 URL을 입력하세요.")
            return
        self.discoverer.configure(
            delay=float(self.discover_delay_var.get()),
            max_depth=int(self.discover_depth_var.get()),
            continuous=bool(self.discover_continuous_var.get()),
            search_enabled=bool(self.discover_search_var.get()),
            search_max_results=int(self.discover_search_n_var.get()),
            cycle_interval_min=float(self.discover_cycle_min_var.get()),
            auto_mode=auto,
            catalog_usable_only=auto,
        )
        self._discover_log("--- 수집 시작 ---")
        if auto:
            self._discover_log("[모드] 자동 — 랜덤 키워드 검색 → 호환 게시판만 저장")

        def worker():
            self.discoverer.run()

        self._discover_thread = threading.Thread(target=worker, daemon=True)
        self.discover_start_btn.config(state="disabled")
        self.discover_stop_btn.config(state="normal")
        self._discover_thread.start()
        self.after(500, self._watch_discover_thread)

    def _on_discover_stop(self) -> None:
        self.discoverer.stop()
        self.discover_stop_btn.config(state="disabled")

    def _go_sets_tab(self) -> None:
        self.notebook.select(0)

    def _watch_discover_thread(self) -> None:
        if self._discover_thread and self._discover_thread.is_alive():
            self.after(500, self._watch_discover_thread)
            return
        self.discover_start_btn.config(state="normal")
        self.discover_stop_btn.config(state="disabled")
        self._refresh_catalog_tree()
        self._discover_log("--- 수집 종료 ---")

    def _build_write_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  글 작성  ")

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        body.add(left, weight=3)

        self._add_text_panel(left, "게시판 URL", "urls", "한 줄에 URL 하나 · 붙여넣기 후 아래에서 체크", height=5)

        pick_f = ttk.LabelFrame(left, text="작업할 URL 선택", style="Card.TLabelframe", padding=6)
        pick_f.pack(fill="both", expand=True, pady=(0, 6))
        ttk.Label(
            pick_f,
            text="체크한 URL만 작업 · 체크 없으면 전체 작업",
            style="Hint.TLabel",
        ).pack(anchor="w")
        search_row = ttk.Frame(pick_f)
        search_row.pack(fill="x", pady=(4, 2))
        ttk.Label(search_row, text="검색", style="Hint.TLabel").pack(side="left")
        self.urls_pick_search_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=self.urls_pick_search_var).pack(side="left", fill="x", expand=True, padx=6)
        self.urls_pick_filter_label = ttk.Label(search_row, text="", style="Hint.TLabel")
        self.urls_pick_filter_label.pack(side="right")
        self.urls_pick_search_var.trace_add("write", lambda *_: self._apply_urls_check_filter())
        pick_body = ttk.Frame(pick_f)
        pick_body.pack(fill="both", expand=True, pady=4)
        self.urls_check_canvas = tk.Canvas(pick_body, highlightthickness=0, height=160, bg=COLORS["card"])
        pick_sb = ttk.Scrollbar(pick_body, orient="vertical", command=self.urls_check_canvas.yview)
        self.urls_check_canvas.configure(yscrollcommand=pick_sb.set)
        self.urls_check_canvas.pack(side="left", fill="both", expand=True)
        pick_sb.pack(side="right", fill="y")
        self.urls_check_frame = ttk.Frame(self.urls_check_canvas)
        self._urls_check_window = self.urls_check_canvas.create_window((0, 0), window=self.urls_check_frame, anchor="nw")
        self.urls_check_frame.bind("<Configure>", self._on_urls_check_frame_configure)
        self.urls_check_canvas.bind("<Configure>", self._on_urls_check_canvas_configure)
        self.urls_check_canvas.bind("<Enter>", lambda _e: self.urls_check_canvas.bind_all("<MouseWheel>", self._on_urls_check_mousewheel))
        self.urls_check_canvas.bind("<Leave>", lambda _e: self.urls_check_canvas.unbind_all("<MouseWheel>"))
        pick_btn = ttk.Frame(pick_f)
        pick_btn.pack(fill="x")
        ttk.Button(pick_btn, text="전체 체크", command=self._pick_all_urls).pack(side="left", padx=(0, 4))
        ttk.Button(pick_btn, text="체크 해제", command=self._pick_clear_urls).pack(side="left", padx=(0, 4))
        ttk.Button(pick_btn, text="체크 반전", command=self._pick_invert_urls).pack(side="left")

        self._add_text_panel(left, "제목 (선택)", "titles", "비우면 키워드 기반 원고 제목 자동 생성", height=2)

        sets_info = tk.Frame(left, bg=COLORS["accent_light"], highlightbackground=COLORS["accent"], highlightthickness=1)
        sets_info.pack(fill="x", pady=6, padx=2)
        inner = tk.Frame(sets_info, bg=COLORS["accent_light"], padx=10, pady=8)
        inner.pack(fill="x")
        self.sets_summary_var = tk.StringVar(value="콘텐츠 세트: 0개")
        tk.Label(inner, textvariable=self.sets_summary_var, bg=COLORS["accent_light"], fg=COLORS["header"], font=(FONT, 9)).pack(side="left")
        tk.Button(
            inner, text="세트 관리 →", command=self._go_sets_tab,
            bg=COLORS["accent"], fg="#fff", relief="flat", padx=10, pady=2, cursor="hand2", font=(FONT, 9),
        ).pack(side="right")

        cat_row = ttk.Frame(left, style="Card.TFrame")
        cat_row.pack(fill="x", pady=4)
        ttk.Label(cat_row, text="분류", style="Hint.TLabel").pack(side="left")
        self.category_var = tk.StringVar()
        ttk.Entry(cat_row, textvariable=self.category_var, width=28).pack(side="left", padx=8)

        right = ttk.Frame(body)
        body.add(right, weight=2)

        ttk.Label(
            right,
            text="콘텐츠 세트 탭에서 URL·키워드 등록\n한 글 = 세트별 키워드 1개 + 링크 분산 원고 자동 작성",
            style="Hint.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        prog = ttk.LabelFrame(right, text="진행", style="Card.TLabelframe", padding=8)
        prog.pack(fill="x", pady=4)
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(prog, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w")
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(prog, variable=self.progress_var, maximum=100).pack(fill="x", pady=4)
        self.job_count_var = tk.StringVar(value="작업: 0건")
        ttk.Label(prog, textvariable=self.job_count_var, style="Count.TLabel").pack(anchor="w")

        sched = ttk.Frame(prog)
        sched.pack(fill="x", pady=(6, 0))
        ttk.Label(sched, text="게시 간격(분)", style="Hint.TLabel").pack(side="left")
        self.write_post_interval_var = tk.DoubleVar(value=0)
        ttk.Spinbox(sched, from_=0, to=120, increment=1, textvariable=self.write_post_interval_var, width=5).pack(side="left", padx=4)
        ttk.Label(sched, text="반복 대기(분)", style="Hint.TLabel").pack(side="left", padx=(8, 0))
        self.write_repeat_interval_var = tk.DoubleVar(value=30)
        ttk.Spinbox(sched, from_=0, to=1440, increment=5, textvariable=self.write_repeat_interval_var, width=5).pack(side="left", padx=4)
        self.write_continuous_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            prog, text="연속 작성 (목록 끝 → 대기 후 처음부터 반복)",
            variable=self.write_continuous_var,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            prog,
            text="게시 간격 = URL마다 대기 · 반복 대기 = 한 바퀴 끝난 뒤 대기",
            style="Hint.TLabel",
        ).pack(anchor="w")

        self.auto_btn = ttk.Button(right, text="▶  순차 자동 작성", style="Accent.TButton", command=self._on_batch_auto)
        self.auto_btn.pack(fill="x", pady=4)
        row = ttk.Frame(right)
        row.pack(fill="x")
        self.open_btn = ttk.Button(row, text="양식만 (1건)", command=self._on_single_fill)
        self.open_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.cancel_btn = ttk.Button(row, text="취소", command=self._on_cancel)
        self.cancel_btn.pack(side="left", padx=(0, 4))
        ttk.Button(row, text="이름 미리보기", command=self._preview_name).pack(side="left")

        cap_f = ttk.LabelFrame(right, text="캡차 수동", style="Card.TLabelframe", padding=6)
        cap_f.pack(fill="x", pady=4)
        cr = ttk.Frame(cap_f)
        cr.pack(fill="x")
        self.captcha_var = tk.StringVar()
        ttk.Entry(cr, textvariable=self.captcha_var, width=12).pack(side="left", padx=4)
        ttk.Button(cr, text="수동 작성완료", command=self._on_submit).pack(side="left")

        log_f = ttk.LabelFrame(right, text="실행 로그", style="Card.TLabelframe", padding=4)
        log_f.pack(fill="both", expand=True, pady=4)
        self.log_box = scrolledtext.ScrolledText(
            log_f, height=14, state="disabled", font=(FONT_MONO, 9),
            bg=COLORS["log_bg"], fg=COLORS["log_fg"], relief="flat", padx=8, pady=8,
        )
        self.log_box.pack(fill="both", expand=True)
        self.log_box.tag_configure("ok", foreground="#4ade80")
        self.log_box.tag_configure("err", foreground="#f87171")
        self.log_box.tag_configure("info", foreground="#93c5fd")
        self.log_box.tag_configure("head", foreground="#fbbf24", font=(FONT_MONO, 9, "bold"))

        self.urls_box.bind("<KeyRelease>", lambda _e: self._on_urls_input_changed())
        self.titles_box.bind("<KeyRelease>", lambda _e: self._on_input_changed())

    def _build_history_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  게시 이력  ")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="실제 등록된 게시판 URL · 동일 게시판 반복 등록 상세", style="Hint.TLabel").pack(side="left")
        ttk.Button(top, text="새로고침", command=self._refresh_history_tree).pack(side="right", padx=4)
        ttk.Button(top, text="이력 삭제", command=self._clear_history).pack(side="right")

        paned = ttk.PanedWindow(tab, orient="vertical")
        paned.pack(fill="both", expand=True)

        tree_frame = ttk.LabelFrame(paned, text="게시판 목록", style="Card.TLabelframe", padding=4)
        paned.add(tree_frame, weight=1)

        cols = ("board", "posts", "success", "last")
        self.history_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10)
        self.history_tree.heading("board", text="게시판 URL")
        self.history_tree.heading("posts", text="등록")
        self.history_tree.heading("success", text="성공")
        self.history_tree.heading("last", text="최근")
        self.history_tree.column("board", width=480)
        self.history_tree.column("posts", width=50, anchor="center")
        self.history_tree.column("success", width=50, anchor="center")
        self.history_tree.column("last", width=130)
        self.history_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.history_tree.yview)
        sb.pack(side="right", fill="y")
        self.history_tree.configure(yscrollcommand=sb.set)
        self.history_tree.bind("<<TreeviewSelect>>", self._on_history_select)

        detail_frame = ttk.LabelFrame(paned, text="상세 (사이트·키워드별 등록 횟수)", style="Card.TLabelframe", padding=4)
        paned.add(detail_frame, weight=2)

        self.history_detail = scrolledtext.ScrolledText(
            detail_frame, font=(FONT_MONO, 9), state="disabled",
            bg="#f8fafc", fg=COLORS["text"], relief="flat", padx=10, pady=10,
        )
        self.history_detail.pack(fill="both", expand=True)

    def _add_text_panel(self, parent, title, attr, hint, height, placeholder="") -> None:
        frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=8)
        frame.pack(fill="x", pady=(0, 6))
        top = ttk.Frame(frame, style="Card.TFrame")
        top.pack(fill="x")
        count_var = tk.StringVar(value="0")
        setattr(self, f"{attr}_count_var", count_var)
        ttk.Label(top, textvariable=count_var, style="Count.TLabel").pack(side="right")
        ttk.Label(top, text=hint, style="Hint.TLabel").pack(side="left")
        box = scrolledtext.ScrolledText(frame, height=height, font=(FONT_MONO, 9), relief="solid", borderwidth=1, wrap="none")
        box.pack(fill="x", pady=(4, 0))
        if placeholder:
            box.insert("1.0", placeholder)
            box.configure(fg=COLORS["muted"])
            box.bind("<FocusIn>", lambda e, b=box, p=placeholder: self._clear_placeholder(b, p))
        setattr(self, f"{attr}_box", box)

    def _clear_placeholder(self, box, placeholder: str) -> None:
        if box.get("1.0", "end").strip() == placeholder.strip():
            box.delete("1.0", "end")
            box.configure(fg=COLORS["text"])

    def _on_urls_check_frame_configure(self, _event=None) -> None:
        self.urls_check_canvas.configure(scrollregion=self.urls_check_canvas.bbox("all"))

    def _on_urls_check_canvas_configure(self, event=None) -> None:
        if event is not None:
            self.urls_check_canvas.itemconfigure(self._urls_check_window, width=event.width)

    def _on_urls_check_mousewheel(self, event) -> None:
        if hasattr(self, "urls_check_canvas"):
            self.urls_check_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _all_parsed_urls(self) -> list[str]:
        return parse_lines(self._get_text("urls"))

    def _urls_pick_filter_query(self) -> str:
        return self.urls_pick_search_var.get().strip().lower() if hasattr(self, "urls_pick_search_var") else ""

    def _url_matches_pick_filter(self, url: str) -> bool:
        q = self._urls_pick_filter_query()
        return not q or q in url.lower()

    def _apply_urls_check_filter(self) -> None:
        if not hasattr(self, "urls_check_frame"):
            return
        visible = 0
        for url, _var, cb in self._url_check_rows:
            if self._url_matches_pick_filter(url):
                cb.pack(anchor="w", fill="x", pady=1)
                visible += 1
            else:
                cb.pack_forget()
        total = len(self._url_check_rows)
        q = self._urls_pick_filter_query()
        if hasattr(self, "urls_pick_filter_label"):
            if q:
                self.urls_pick_filter_label.configure(text=f"{visible}/{total}")
            else:
                self.urls_pick_filter_label.configure(text="")
        self._on_urls_check_frame_configure()

    def _sync_urls_pick_list(self, *, preserve_selection: bool = True) -> None:
        if not hasattr(self, "urls_check_frame"):
            return
        prev_checked: set[str] = set()
        if preserve_selection:
            for url, var, _cb in self._url_check_rows:
                if var.get():
                    prev_checked.add(normalize_board_url(url))
        self._urls_list_syncing = True
        try:
            for child in self.urls_check_frame.winfo_children():
                child.destroy()
            self._url_check_rows.clear()
            for url in self._all_parsed_urls():
                key = normalize_board_url(url)
                checked = key in prev_checked if preserve_selection else False
                var = tk.BooleanVar(value=checked)
                display = url if len(url) <= 72 else url[:69] + "..."
                cb = ttk.Checkbutton(
                    self.urls_check_frame,
                    text=display,
                    variable=var,
                    command=self._on_urls_pick_changed,
                )
                self._url_check_rows.append((url, var, cb))
        finally:
            self._urls_list_syncing = False
        self._apply_urls_check_filter()

    def _pick_list_urls(self) -> list[str]:
        return [url for url, var, _cb in self._url_check_rows if var.get()]

    def _has_url_pick_selection(self) -> bool:
        return any(var.get() for _, var, _cb in self._url_check_rows)

    def _active_job_urls(self) -> list[str]:
        picked = self._pick_list_urls()
        if picked:
            return picked
        return self._all_parsed_urls()

    def _visible_url_check_rows(self) -> list[tuple[str, tk.BooleanVar, ttk.Checkbutton]]:
        q = self._urls_pick_filter_query()
        if not q:
            return self._url_check_rows
        return [row for row in self._url_check_rows if self._url_matches_pick_filter(row[0])]

    def _pick_all_urls(self) -> None:
        for _, var, _cb in self._visible_url_check_rows():
            var.set(True)
        self._refresh_counts()

    def _pick_clear_urls(self) -> None:
        for _, var, _cb in self._visible_url_check_rows():
            var.set(False)
        self._refresh_counts()

    def _pick_invert_urls(self) -> None:
        for _, var, _cb in self._visible_url_check_rows():
            var.set(not var.get())
        self._refresh_counts()

    def _on_urls_input_changed(self) -> None:
        self._on_input_changed()
        self._sync_urls_pick_list(preserve_selection=True)
        self._refresh_counts()

    def _on_urls_pick_changed(self) -> None:
        if self._urls_list_syncing:
            return
        if self._busy and self._has_url_pick_selection():
            n = len(self._pick_list_urls())
            self._show_toast(f"체크 {n}개 — 현재 회차 종료 후 반영")
        self._refresh_counts()

    def _get_text(self, attr: str) -> str:
        box = getattr(self, f"{attr}_box", None)
        if box is None:
            return ""
        return box.get("1.0", "end")

    def _refresh_counts(self) -> None:
        if not hasattr(self, "urls_box") or not hasattr(self, "titles_box"):
            return
        urls = self._all_parsed_urls()
        active = self._active_job_urls()
        titles = parse_lines(self._get_text("titles"))
        try:
            sets = self.sets_panel.get_content_sets() if hasattr(self, "sets_panel") else []
            n_sets = len(sets)
        except Exception:
            n_sets = len(self.sets_panel._data) if hasattr(self, "sets_panel") else 0
        if self._has_url_pick_selection():
            url_label = f"전체 {len(urls)} · 체크 {len(active)}"
            job_label = f"작업 {len(active)}건"
        else:
            url_label = f"{len(urls)}개 URL"
            job_label = f"작업 {len(active)}건 (전체)"
        self.urls_count_var.set(url_label)
        self.job_count_var.set(
            f"{job_label} · {n_sets}개 세트 · 글당 앵커 {n_sets}개"
        )
        if hasattr(self, "sets_summary_var"):
            self.sets_summary_var.set(f"콘텐츠 세트: {n_sets}개 등록됨  (글당 앵커 {n_sets}개)")
        if hasattr(self, "titles_count_var"):
            self.titles_count_var.set(f"{len(titles)}개 제목")

    def _refresh_history_tree(self) -> None:
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        self.history.load()
        for s in self.history.get_summaries():
            display = s.board_url if len(s.board_url) <= 70 else s.board_url[:67] + "..."
            self.history_tree.insert(
                "", "end", iid=s.board_key,
                values=(display, s.post_count, s.success_count, s.last_at),
            )

    def _on_history_select(self, _event=None) -> None:
        sel = self.history_tree.selection()
        if not sel:
            return
        key = sel[0]
        text = self.history.format_detail(key)
        self.history_detail.config(state="normal")
        self.history_detail.delete("1.0", "end")
        self.history_detail.insert("1.0", text)
        self.history_detail.config(state="disabled")

    def _clear_history(self) -> None:
        if messagebox.askyesno("이력 삭제", "게시 이력을 모두 삭제할까요?"):
            self.history.clear()
            self._refresh_history_tree()
            self.history_detail.config(state="normal")
            self.history_detail.delete("1.0", "end")
            self.history_detail.config(state="disabled")

    def _record_history(self, job: PostJob, status: str, message: str) -> None:
        rec = PostRecord.from_job(
            job.board_url,
            job.title,
            job.links,
            status=status,
            message=message,
            list_url=self.writer.last_list_url or job.board_url,
            write_url=self.writer.last_write_url,
            post_url=self.writer.last_post_url if status == "success" else "",
        )
        self.history.add(rec)
        self.after(0, self._refresh_history_tree)

    def _build_jobs_for_active_urls(self) -> list[PostJob]:
        urls = self._active_job_urls()
        if not urls:
            raise ValueError("작업할 URL을 선택하거나 목록에 URL을 입력해 주세요.")
        content_sets = self.sets_panel.get_content_sets()
        return build_jobs(
            "\n".join(urls),
            content_sets,
            self._get_text("titles"),
            self.category_var.get(),
        )

    def _preview_name(self) -> None:
        self._log(f"랜덤 이름: {random_english_name()}", "info")

    def _validate_jobs(self) -> list[PostJob] | None:
        try:
            jobs = self._build_jobs_for_active_urls()
            self._batch_jobs = jobs
            self._refresh_counts()
            return jobs
        except ValueError as e:
            messagebox.showwarning("입력 오류", str(e))
            return None

    def _rebuild_jobs_for_next_round(self) -> list[PostJob] | None:
        holder: list[list[PostJob] | None] = [None]
        err_holder: list[Exception | None] = [None]
        done = threading.Event()

        def work() -> None:
            try:
                holder[0] = self._build_jobs_for_active_urls()
            except ValueError as e:
                err_holder[0] = e
            finally:
                done.set()

        self.after(0, work)
        done.wait(timeout=10.0)
        if err_holder[0]:
            self.after(0, lambda: self._log(f"[안내] 작업할 URL 없음: {err_holder[0]}", "info"))
            return None
        return holder[0]

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        st = "disabled" if busy else "normal"
        self.auto_btn.config(state=st)
        self.open_btn.config(state=st)

    def _set_progress(self, cur: int, total: int, msg: str = "") -> None:
        self.progress_var.set((cur / total * 100) if total else 0)
        self.status_var.set(msg or f"{cur}/{total}")

    def _log(self, msg: str, tag: str = "") -> None:
        self.log_box.config(state="normal")
        if tag:
            self.log_box.insert("end", msg + "\n", tag)
        else:
            self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")
        log.info(msg)

    def _run_async(self, fn, on_success=None) -> None:
        if self._busy:
            self._log("[안내] 작업 중 — 취소 후 재시도", "info")
            return
        self.writer.reset_cancel()

        def worker():
            try:
                result = fn()
                self.after(0, lambda r=result: self._on_async_done(None, r, on_success))
            except Exception as e:
                self.after(0, lambda err=e: self._on_async_done(err, None, on_success))
            finally:
                self.after(0, lambda: self._set_busy(False))

        self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    def _on_async_done(self, error, result, on_success) -> None:
        if error:
            self._log(f"[오류] {error}", "err")
            if not self._batch_jobs:
                messagebox.showerror("오류", str(error))
        elif result:
            self._log(result, "ok")
        if on_success:
            on_success()

    def _on_cancel(self) -> None:
        self.writer.cancel()
        self._busy = False
        self._set_busy(False)
        self.status_var.set("취소됨")
        self._log("[취소]", "info")

    def _apply_write_schedule(self) -> None:
        self._write_post_interval_sec = max(0.0, float(self.write_post_interval_var.get()) * 60.0)
        self._write_repeat_interval_sec = max(0.0, float(self.write_repeat_interval_var.get()) * 60.0)
        self._write_continuous = bool(self.write_continuous_var.get())

    def _sleep_writing(self, seconds: float, label: str) -> bool:
        """대기 중 취소 시 False. 단위: 초."""
        total = int(seconds)
        if total <= 0:
            return True
        self.after(0, lambda: self._log(f"[대기] {label} {total // 60}분 {total % 60}초", "info"))
        for remaining in range(total, 0, -1):
            if self.writer._cancelled:
                return False
            self.after(0, lambda r=remaining, lb=label: self.status_var.set(f"{lb} {r // 60}분{r % 60}초"))
            time.sleep(1)
        return True

    def _run_batch(self, *, auto_submit: bool) -> str:
        ok, fail = 0, 0
        round_num = 0
        jobs = self._batch_jobs

        while True:
            round_num += 1
            if round_num > 1:
                new_jobs = self._rebuild_jobs_for_next_round()
                if not new_jobs:
                    break
                jobs = new_jobs
                self._batch_jobs = jobs
                self.after(0, lambda n=round_num, c=len(jobs): self._log(f"\n--- {n}회차 반복 ({c}건) ---", "head"))

            for idx, job in enumerate(jobs):
                if self.writer._cancelled:
                    break

                picks = job.picks_summary
                self.after(0, lambda j=job: self._set_progress(j.index - 1, j.total, j.label))
                self.after(0, lambda j=job: self._log(f"\n══ {j.label} ══", "head"))
                self.after(0, lambda p=picks: self._log(f"  {p}", "info"))

                try:
                    self.writer.open_browser(job.board_url)
                    links = _job_links(job)
                    if auto_submit:
                        msg = self.writer.fill_and_submit(
                            job.title, links, category=job.category, post_index=job.index - 1,
                        )
                    else:
                        msg = self.writer.fill_form(
                            job.title, links, category=job.category, post_index=job.index - 1,
                        )
                    ok += 1
                    self.after(0, lambda m=msg: self._log(m, "ok"))
                    self._record_history(job, "success", msg)
                except Exception as e:
                    fail += 1
                    self.after(0, lambda er=str(e), j=job: self._log(f"✗ {j.label}: {er}", "err"))
                    self._record_history(job, "fail", str(e))

                self.after(0, lambda j=job: self._set_progress(j.index, j.total, f"{j.index}/{j.total}"))

                if (
                    idx < len(jobs) - 1
                    and self._write_post_interval_sec > 0
                    and not self.writer._cancelled
                ):
                    if not self._sleep_writing(self._write_post_interval_sec, "다음 게시까지"):
                        break

            if self.writer._cancelled or not self._write_continuous:
                break
            if not self._sleep_writing(self._write_repeat_interval_sec, "다음 회차까지"):
                break

        self.after(0, lambda: self.status_var.set(f"완료 — 성공 {ok} / 실패 {fail}"))
        self.after(0, lambda: self.progress_var.set(100))
        return f"════ 결과: 성공 {ok} / 실패 {fail} / 전체 {len(jobs)} ════"

    def _on_batch_auto(self) -> None:
        self._sync_urls_pick_list(preserve_selection=True)
        jobs = self._validate_jobs()
        if not jobs:
            return
        self._apply_write_schedule()
        if self._has_url_pick_selection():
            sel_note = f"\n체크한 {len(jobs)}개 URL만 작업"
        else:
            sel_note = f"\n전체 {len(jobs)}개 URL 작업"
        extra = ""
        if self._write_continuous:
            extra = f"\n\n연속 작성: 한 바퀴 후 {int(self._write_repeat_interval_sec // 60)}분 대기 후 반복"
        if self._write_post_interval_sec > 0:
            extra += f"\n게시 간격: {int(self._write_post_interval_sec // 60)}분"
        if not messagebox.askyesno("순차 작성", f"{len(jobs)}개 URL에 순서대로 작성합니다.{extra}{sel_note}"):
            return
        self._log(f"--- 배치 {len(jobs)}건 ---", "head")
        self._run_async(lambda: self._run_batch(auto_submit=True))

    def _on_single_fill(self) -> None:
        jobs = self._validate_jobs()
        if not jobs:
            return
        job = jobs[0]

        def task():
            self.writer.open_browser(job.board_url)
            return self.writer.fill_form(
                job.title, _job_links(job), category=job.category, post_index=job.index - 1,
            )

        self._log(f"--- 양식: {job.label} ---", "head")
        self._run_async(task)

    def _on_submit(self) -> None:
        if not self.captcha_var.get().strip():
            messagebox.showwarning("입력", "캡차 숫자를 입력하세요.")
            return
        if not self.writer.is_open():
            messagebox.showwarning("안내", "먼저 작성을 실행하세요.")
            return
        cap = self.captcha_var.get().strip()
        self._run_async(lambda: self.writer.submit(cap, auto_captcha=False),
                        on_success=lambda: self.captcha_var.set(""))

    def _on_close(self) -> None:
        self.discoverer.stop()
        if self._save_after_id:
            self.after_cancel(self._save_after_id)
        self._save_app_state()
        self.writer.cancel()
        self.writer.close()
        self.destroy()


def main() -> None:
    from app_paths import is_frozen, migrate_legacy_data

    migrate_legacy_data()

    if is_frozen():
        splash = UpdateSplash()
        try:
            try_startup_update(on_status=splash.set_status)
        finally:
            splash.close()

    BacklinkApp().mainloop()


if __name__ == "__main__":
    main()
