"""백링크 게시판 자동 글쓰기 GUI."""

from __future__ import annotations

import app_paths  # noqa: F401 — exe 배포 시 PLAYWRIGHT_BROWSERS_PATH 설정

import hashlib
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from app_constants import APP_DISPLAY_NAME, APP_VERSION, FAIL_SKIP_THRESHOLD, UPDATE_VERSION_URL
from error_messages import is_form_miss_error, is_strengthenable_error, localize_error_message
from app_logger import log
from app_state import apply_to_app, collect_from_app, save_state
from batch_jobs import PostJob, build_jobs, parse_lines, normalize_board_url
from board_writer import BoardWriter, random_english_name
from comment_writer import CommentWriter
from post_history import PostHistory, PostRecord
from excluded_urls import ExcludedUrlRegistry
from target_jobs import TargetJob, build_target_jobs
from url_analyzer import UrlAnalysis, analyze_urls, filter_unsupported, summarize_analyses
from unsupported_report import build_cursor_report, build_url_only_text, save_cursor_report
from page_snapshot import (
    capture_dom_markers,
    capture_html_excerpt,
    capture_snapshot_from_page,
    capture_snapshots,
    snapshots_to_report_blocks,
)
from ai_assist import capabilities_summary, is_configured, login_spam_mitigation_tips, suggest_comment_form_selectors
from url_recommend import recommend_urls
from wordpress_comment import WordPressCommentWriter
from movable_type_comment import MovableTypeCommentWriter
from zeroboard_writer import ZeroBoardWriter
from custom_bbs_comment import CustomBbsCommentWriter
from phpbb_comment import PhpbbCommentWriter
from generic_comment import GenericCommentWriter
from board_catalog import STATUS_LABEL, BoardCatalog
from board_discoverer import BoardDiscoverer, DiscovererStats
from board_search import SEARCH_PRESETS, preset_text
from browser_prefs import is_headless, set_app_window, set_headless
from win_ui import bootstrap_before_tk, install_window_move_guard
from sets_panel import ContentSetsTab
from startup_update import try_startup_update
from update_splash import UpdateSplash
from update_ui import schedule_update_check
from app_paths import is_frozen, playwright_browsers_error_message, playwright_browsers_ready
from log_sync import (
    LogSyncConfig,
    build_failure_case,
    failure_case_to_cursor_report,
    fetch_client_log,
    fetch_failure_case,
    get_pc_id,
    list_client_logs,
    list_failure_cases,
    load_last_sync,
    upload_failure_case,
    upload_failure_cases,
    upload_latest_log,
    verify_repo_access,
)

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
CHK_ON = "☑"
CHK_OFF = "☐"


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
        self.comment_writer = CommentWriter()
        self.wp_comment_writer = WordPressCommentWriter()
        self.mt_comment_writer = MovableTypeCommentWriter()
        self.zboard_writer = ZeroBoardWriter()
        self.custom_bbs_writer = CustomBbsCommentWriter()
        self.phpbb_comment_writer = PhpbbCommentWriter()
        self.generic_comment_writer = GenericCommentWriter()
        self.history = PostHistory()
        self.excluded_urls = ExcludedUrlRegistry()
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
        self._batch_jobs: list[PostJob] | list[TargetJob] = []
        self._write_post_interval_sec = 0.0
        self._write_repeat_interval_sec = 0.0
        self._write_continuous = False
        self._batch_round_lock = threading.Lock()
        self._urls_list_syncing = False
        self._urls_recommend_syncing = False
        self._url_recommend_after_id: str | None = None
        self._url_pick_data: dict[str, str] = {}
        self._url_pick_checked: set[str] = set()
        self._history_pick_checked: set[str] = set()
        self._history_pick_urls: dict[str, str] = {}
        self._window_moving = False
        self._log_pending: list[tuple[str, str]] = []
        self._discover_log_pending: list[str] = []
        self._log_sync_after_id: str | None = None
        self._log_sync_uploading = False
        self._failure_uploading = False
        self._remote_upload_blocked = False
        self._remote_upload_block_msg = ""
        self._remote_failure_cases: list = []
        self._remote_failure_detail: dict | None = None

        self._setup_styles()
        self._build_ui()
        set_app_window(self)
        install_window_move_guard(
            self,
            on_move_start=self._mark_window_moving,
            on_move_end=self._on_window_move_end,
        )
        apply_to_app(self)
        if self.excluded_urls.count():
            self._purge_excluded_urls_from_box()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_autosave()
        self._configure_fail_skip_tree_tags()
        self._sync_urls_pick_list(preserve_selection=False)
        self._refresh_counts()
        self._refresh_history_tree()
        self._refresh_log_sync_status()
        self._schedule_log_sync_timer()
        if is_frozen():
            schedule_update_check(
                self,
                version_url=UPDATE_VERSION_URL,
                current_version=APP_VERSION,
            )
        self.after(300, self._apply_startup_url_recommend)

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

    def _configure_fail_skip_tree_tags(self) -> None:
        bg, fg = "#dde4ec", "#64748b"
        for attr in ("urls_pick_tree", "history_tree"):
            tree = getattr(self, attr, None)
            if tree is not None:
                tree.tag_configure("fail_skip", background=bg, foreground=fg)

    def _is_fail_skipped(self, url: str) -> bool:
        return self.history.is_fail_skipped(url, threshold=FAIL_SKIP_THRESHOLD)

    def _url_pick_display(self, url: str, board_key: str) -> tuple[str, tuple[str, ...]]:
        display = url if len(url) <= 72 else url[:69] + "..."
        fail_n = self.history.get_fail_count(board_key)
        if fail_n >= FAIL_SKIP_THRESHOLD:
            short = display if len(display) <= 58 else display[:55] + "..."
            return f"⚠ 실패{fail_n} · {short}", ("fail_skip",)
        return display, ()

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
        self.log_sync_status_var = tk.StringVar(value="")
        tk.Label(
            self.header_frame,
            textvariable=self.log_sync_status_var,
            bg=COLORS["header"],
            fg="#94a3b8",
            font=(FONT, 8),
        ).pack(side="right", padx=(0, 8))
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
        self._build_check_tab()
        self._build_write_tab()
        self._build_history_tab()
        self._build_remote_logs_tab()

    def _build_sets_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  콘텐츠 세트  ")
        self.sets_panel = ContentSetsTab(tab, on_change=self._on_sets_changed)
        self.sets_panel.pack(fill="both", expand=True, padx=4, pady=4)

    def _go_write_tab(self) -> None:
        self.notebook.select(3)

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
        if self._window_moving:
            self._discover_log_pending.append(msg)
            return
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
        mode = self._get_write_mode()
        urls, _ = recommend_urls(urls, mode=mode)
        existing = set(parse_lines(self._get_text("urls")))
        added = []
        for u in urls:
            if self.excluded_urls.is_excluded(u):
                continue
            if u not in existing:
                added.append(u)
                existing.add(u)
        if not added:
            self._show_toast("이미 글 작성 목록에 있는 URL입니다.", error=True)
            return
        cur = self._get_text("urls").rstrip()
        block = "\n".join(added)
        self.urls_box.insert("end", ("" if not cur else "\n") + block + "\n")
        self._schedule_url_recommend(immediate=True)
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

    def _build_check_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  URL 검사 · 댓글  ")

        outer = ttk.PanedWindow(tab, orient="vertical")
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        top = ttk.Frame(outer)
        outer.add(top, weight=3)

        ttk.Label(
            top,
            text="URL 붙여넣기 → 검사 · 목록에서 Ctrl+C로 URL 복사 · 미지원 URL은 아래에 정리 후 Cursor에 전달",
            style="Hint.TLabel",
        ).pack(anchor="w")

        self.check_url_box = scrolledtext.ScrolledText(top, height=6, font=(FONT_MONO, 9))
        self.check_url_box.pack(fill="both", expand=True, pady=6)

        btn_row = ttk.Frame(top)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="URL 일괄 검사", command=self._on_check_urls).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="지원 URL → 글작성 탭", command=self._send_checked_to_write).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="미지원 URL 복사", command=self._on_check_copy_unsupported).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Cursor용 보고서 저장", command=self._on_check_save_report).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="선택 URL 페이지 분석", command=self._on_check_page_probe).pack(side="left", padx=(0, 6))
        self.check_summary_var = tk.StringVar(value="검사 대기")
        ttk.Label(btn_row, textvariable=self.check_summary_var, style="Count.TLabel").pack(side="left", padx=12)

        tree_f = ttk.Frame(top)
        tree_f.pack(fill="both", expand=True)
        cols = ("support", "kind", "url", "note")
        self.check_tree = ttk.Treeview(tree_f, columns=cols, show="headings", height=10)
        self.check_tree.heading("support", text="지원")
        self.check_tree.heading("kind", text="유형")
        self.check_tree.heading("url", text="URL")
        self.check_tree.heading("note", text="설명")
        self.check_tree.column("support", width=72, anchor="center")
        self.check_tree.column("kind", width=100)
        self.check_tree.column("url", width=340)
        self.check_tree.column("note", width=280)
        self.check_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(tree_f, orient="vertical", command=self.check_tree.yview)
        sb.pack(side="right", fill="y")
        self.check_tree.configure(yscrollcommand=sb.set)
        self.check_tree.bind("<Control-c>", self._on_check_copy)
        self.check_tree.bind("<Control-C>", self._on_check_copy)
        self._check_analyses: list[UrlAnalysis] = []
        self._check_snapshots: list[dict] = []

        bottom = ttk.LabelFrame(outer, text="미지원 · 부분 가능 (Cursor 전달용)", style="Card.TLabelframe", padding=6)
        outer.add(bottom, weight=2)

        self.check_unsupported_box = scrolledtext.ScrolledText(bottom, height=6, font=(FONT_MONO, 9))
        self.check_unsupported_box.pack(fill="both", expand=True)
        self.check_unsupported_box.configure(state="disabled")

        ai_row = ttk.Frame(bottom, style="Card.TFrame")
        ai_row.pack(fill="x", pady=(6, 0))
        ttk.Label(ai_row, text="OpenAI API 키 (선택 — 캡차 Vision·폼 분석)", style="Hint.TLabel").pack(side="left")
        self.ai_api_key_var = tk.StringVar()
        ai_entry = ttk.Entry(ai_row, textvariable=self.ai_api_key_var, width=36, show="*")
        ai_entry.pack(side="left", padx=6)
        ai_entry.bind("<KeyRelease>", lambda _e: self._schedule_save())
        self.check_ai_hint_var = tk.StringVar(value=capabilities_summary())
        ttk.Label(bottom, textvariable=self.check_ai_hint_var, style="Hint.TLabel", wraplength=900).pack(anchor="w", pady=(4, 0))

    def _check_selected_analyses(self) -> list[UrlAnalysis]:
        out: list[UrlAnalysis] = []
        for iid in self.check_tree.selection():
            try:
                idx = int(iid)
                if 0 <= idx < len(self._check_analyses):
                    out.append(self._check_analyses[idx])
            except ValueError:
                continue
        return out

    def _on_check_copy(self, _event=None) -> str:
        items = self._check_selected_analyses()
        if not items:
            return "break"
        urls = [a.url for a in items]
        self.clipboard_clear()
        self.clipboard_append("\n".join(urls))
        self.update_idletasks()
        n = len(urls)
        self._show_toast(f"URL {n}개 복사" if n > 1 else "URL 복사됨")
        return "break"

    def _refresh_check_unsupported_box(self) -> None:
        self.check_unsupported_box.configure(state="normal")
        self.check_unsupported_box.delete("1.0", "end")
        unsupported = filter_unsupported(self._check_analyses)
        if not unsupported:
            self.check_unsupported_box.insert("1.0", "(미지원·부분 가능 URL 없음)")
        else:
            lines = []
            for a in unsupported:
                lines.append(f"[{a.support_label}] {a.kind_label}")
                lines.append(a.url)
                lines.append(f"  → {a.note}")
                lines.append("")
            self.check_unsupported_box.insert("1.0", "\n".join(lines).rstrip())
        self.check_unsupported_box.configure(state="disabled")
        self.check_ai_hint_var.set(capabilities_summary())

    def _on_check_copy_unsupported(self) -> None:
        if not self._check_analyses:
            self._on_check_urls()
        text = build_url_only_text(self._check_analyses)
        if not text:
            messagebox.showinfo("안내", "미지원·부분 가능 URL이 없습니다.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        n = len(text.splitlines())
        self._show_toast(f"미지원 URL {n}개 복사됨")

    def _on_check_save_report(self) -> None:
        if not self._check_analyses:
            self._on_check_urls()
        unsupported = filter_unsupported(self._check_analyses)
        if not unsupported:
            messagebox.showinfo("안내", "저장할 미지원 URL이 없습니다.")
            return
        path = save_cursor_report(self._check_analyses, snapshots=self._check_snapshots or None)
        report = build_cursor_report(self._check_analyses, snapshots=self._check_snapshots or None)
        self.clipboard_clear()
        self.clipboard_append(report)
        self.update_idletasks()
        self._show_toast(f"보고서 저장·클립보드 복사 ({path.name})")

    def _on_check_page_probe(self) -> None:
        selected = self._check_selected_analyses()
        if not selected:
            if not self._check_analyses:
                self._on_check_urls()
            selected = filter_unsupported(self._check_analyses)[:5]
        if not selected:
            messagebox.showinfo("안내", "분석할 URL이 없습니다. 목록에서 선택하거나 미지원 URL을 검사하세요.")
            return
        urls = [a.url for a in selected[:8]]
        self.check_summary_var.set(f"페이지 분석 중… ({len(urls)}건)")
        self.update_idletasks()

        def worker() -> None:
            try:
                snaps = capture_snapshots(urls)
                blocks = snapshots_to_report_blocks(snaps)
                for snap in snaps:
                    tips, err = login_spam_mitigation_tips(snap.text_block())
                    if tips:
                        blocks.append({"text_block": f"--- 대응 팁: {snap.url} ---\n{tips}"})
                    if is_configured() and snap.comment_form_found:
                        sel, sel_err = suggest_comment_form_selectors(snap.text_block())
                        if sel:
                            blocks.append({"text_block": f"--- 셀렉터 제안: {snap.url} ---\n{sel}"})
                        if sel_err:
                            log(f"셀렉터 제안: {sel_err}")
                    if err:
                        log(f"AI 분석: {err}")
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("오류", str(exc)))
                self.after(0, lambda: self.check_summary_var.set("페이지 분석 실패"))
                return

            def done() -> None:
                self._check_snapshots = blocks
                path = save_cursor_report(self._check_analyses, snapshots=blocks)
                self.check_summary_var.set(f"페이지 분석 완료 · {path.name}")
                self._show_toast(f"페이지 분석 {len(snaps)}건 — 보고서 갱신됨")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_check_urls(self) -> None:
        text = self.check_url_box.get("1.0", "end")
        items = analyze_urls(parse_lines(text))
        self._check_analyses = items
        self._check_snapshots = []
        for row in self.check_tree.get_children():
            self.check_tree.delete(row)
        for i, a in enumerate(items):
            display = a.url if len(a.url) <= 52 else a.url[:49] + "..."
            self.check_tree.insert("", "end", iid=str(i), values=(a.support_label, a.kind_label, display, a.note[:80]))
        counts = summarize_analyses(items)
        self.check_summary_var.set(
            f"전체 {len(items)} · 게시글 {counts.get('post', 0)} · 댓글 {counts.get('comment', 0)} · "
            f"부분 {counts.get('partial', 0)} · 불가 {counts.get('no', 0)}"
        )
        self._refresh_check_unsupported_box()

    def _send_checked_to_write(self) -> None:
        if not self._check_analyses:
            self._on_check_urls()
        supported = [a.url for a in self._check_analyses if a.support_level in ("post", "comment")]
        if not supported:
            messagebox.showinfo("안내", "지원되는 URL이 없습니다.")
            return
        self.write_mode_var.set("auto")
        self._append_urls_to_write(supported)
        self._go_write_tab()

    def _build_write_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  글 작성  ")

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        body.add(left, weight=3)

        self._add_text_panel(left, "게시판 URL", "urls", "붙여넣으면 자동으로 추천 URL로 정리됩니다", height=5)
        url_btn_row = ttk.Frame(left, style="Card.TFrame")
        url_btn_row.pack(fill="x", pady=(0, 4))
        ttk.Button(url_btn_row, text="URL 자동 정리", command=self._on_recommend_urls_click).pack(side="left")

        mode_row = ttk.Frame(left, style="Card.TFrame")
        mode_row.pack(fill="x", pady=(0, 4))
        ttk.Label(mode_row, text="작업 유형", style="Hint.TLabel").pack(side="left")
        self.write_mode_var = tk.StringVar(value="auto")
        ttk.Combobox(
            mode_row,
            textvariable=self.write_mode_var,
            values=("auto", "post", "comment"),
            state="readonly",
            width=14,
        ).pack(side="left", padx=8)
        self.write_mode_var.trace_add("write", lambda *_: self._on_write_mode_changed())
        ttk.Label(
            mode_row,
            text="auto=URL별 자동 · post=게시글만 · comment=댓글만",
            style="Hint.TLabel",
        ).pack(side="left")

        pick_f = ttk.LabelFrame(left, text="작업할 URL 선택", style="Card.TLabelframe", padding=6)
        pick_f.pack(fill="both", expand=True, pady=(0, 6))
        ttk.Label(
            pick_f,
            text=f"체크한 URL만 작업 · 체크 없으면 전체 · 실패 {FAIL_SKIP_THRESHOLD}회 이상은 회색(자동 제외)",
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
        self.urls_pick_tree = ttk.Treeview(
            pick_body,
            columns=("url",),
            show="tree headings",
            height=8,
            selectmode="none",
        )
        self.urls_pick_tree.heading("#0", text="✓")
        self.urls_pick_tree.heading("url", text="게시판 URL")
        self.urls_pick_tree.column("#0", width=30, anchor="center", stretch=False)
        self.urls_pick_tree.column("url", width=420, stretch=True)
        pick_sb = ttk.Scrollbar(pick_body, orient="vertical", command=self.urls_pick_tree.yview)
        self.urls_pick_tree.configure(yscrollcommand=pick_sb.set)
        self.urls_pick_tree.pack(side="left", fill="both", expand=True)
        pick_sb.pack(side="right", fill="y")
        self.urls_pick_tree.bind("<ButtonRelease-1>", self._on_urls_pick_tree_click)
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
            text="콘텐츠 세트 탭에서 URL·키워드 등록\n게시글=그누보드 글쓰기 · 댓글=글보기/워드프레스",
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

        self.auto_btn = ttk.Button(right, text="▶  순차 자동 작성/댓글", style="Accent.TButton", command=self._on_batch_auto)
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
        ttk.Button(top, text="전체 이력 삭제", command=self._clear_history).pack(side="right")
        ttk.Button(top, text="새로고침", command=self._refresh_history_tree).pack(side="right", padx=4)
        self.history_delete_btn = ttk.Button(
            top, text="선택 삭제", command=self._exclude_selected_boards_from_history, state="disabled",
        )
        self.history_delete_btn.pack(side="right", padx=4)
        ttk.Button(top, text="실패 사유 삭제", command=self._open_fail_reason_delete_dialog).pack(side="right", padx=2)
        ttk.Button(top, text="동일 사유 선택", command=self._history_select_same_fail_reason).pack(side="right", padx=2)
        ttk.Button(top, text="실패 원격 업로드", command=self._on_history_upload_failures).pack(side="right", padx=2)
        ttk.Button(top, text="선택 해제", command=self._history_pick_clear).pack(side="right", padx=2)
        ttk.Button(top, text="전체 선택", command=self._history_pick_all).pack(side="right", padx=2)
        self.history_pick_count_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.history_pick_count_var, style="Hint.TLabel", width=10).pack(side="right")

        paned = ttk.PanedWindow(tab, orient="vertical")
        paned.pack(fill="both", expand=True)

        list_outer = ttk.LabelFrame(paned, text="게시판 목록", style="Card.TLabelframe", padding=4)
        paned.add(list_outer, weight=1)

        list_body = ttk.Frame(list_outer)
        list_body.pack(fill="both", expand=True)
        cols = ("url", "posts", "ok", "last")
        self.history_tree = ttk.Treeview(
            list_body,
            columns=cols,
            show="tree headings",
            height=8,
            selectmode="browse",
        )
        self.history_tree.heading("#0", text="✓")
        self.history_tree.heading("url", text="게시 URL")
        self.history_tree.heading("posts", text="등록")
        self.history_tree.heading("ok", text="성공")
        self.history_tree.heading("last", text="최근")
        self.history_tree.column("#0", width=30, anchor="center", stretch=False)
        self.history_tree.column("url", width=300, stretch=True)
        self.history_tree.column("posts", width=44, anchor="center", stretch=False)
        self.history_tree.column("ok", width=44, anchor="center", stretch=False)
        self.history_tree.column("last", width=110, stretch=False)
        hist_sb = ttk.Scrollbar(list_body, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=hist_sb.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        hist_sb.pack(side="right", fill="y")
        self.history_tree.bind("<ButtonRelease-1>", self._on_history_tree_click)
        self.history_tree.bind("<<TreeviewSelect>>", self._on_history_tree_select)
        self._selected_history_key: str = ""

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

    def _on_window_move_end(self) -> None:
        self._window_moving = False
        if self._log_pending:
            pending, self._log_pending = self._log_pending, []
            for msg, tag in pending:
                self._log(msg, tag)
        if self._discover_log_pending:
            pending, self._discover_log_pending = self._discover_log_pending, []
            for msg in pending:
                self._discover_log(msg)

    def _mark_window_moving(self) -> None:
        self._window_moving = True

    def _url_pick_mark(self, iid: str) -> str:
        return CHK_ON if iid in self._url_pick_checked else CHK_OFF

    def _history_pick_mark(self, key: str) -> str:
        return CHK_ON if key in self._history_pick_checked else CHK_OFF

    def _on_urls_pick_tree_click(self, event) -> None:
        if not hasattr(self, "urls_pick_tree"):
            return
        tree = self.urls_pick_tree
        row = tree.identify_row(event.y)
        if not row:
            return
        col = tree.identify_column(event.x)
        if col != "#0":
            return
        if row in self._url_pick_checked:
            self._url_pick_checked.discard(row)
        else:
            self._url_pick_checked.add(row)
        tree.item(row, text=self._url_pick_mark(row))
        self._on_urls_pick_changed()

    def _on_history_tree_click(self, event) -> None:
        if not hasattr(self, "history_tree"):
            return
        tree = self.history_tree
        row = tree.identify_row(event.y)
        if not row or row == "_empty":
            return
        col = tree.identify_column(event.x)
        if col == "#0":
            if row in self._history_pick_checked:
                self._history_pick_checked.discard(row)
            else:
                self._history_pick_checked.add(row)
            tree.item(row, text=self._history_pick_mark(row))
            self._update_history_pick_ui()
            return
        if col == "#1":
            self._select_history_board(row)

    def _on_history_tree_select(self, _event=None) -> None:
        sel = self.history_tree.selection()
        if sel and sel[0] != "_empty":
            self._select_history_board(sel[0])

    def _all_parsed_urls(self) -> list[str]:
        return parse_lines(self._get_text("urls"))

    def _urls_pick_filter_query(self) -> str:
        return self.urls_pick_search_var.get().strip().lower() if hasattr(self, "urls_pick_search_var") else ""

    def _url_matches_pick_filter(self, url: str) -> bool:
        q = self._urls_pick_filter_query()
        return not q or q in url.lower()

    def _apply_urls_check_filter(self) -> None:
        if not hasattr(self, "urls_pick_tree"):
            return
        tree = self.urls_pick_tree
        visible = 0
        for iid in self._url_pick_data:
            if self._url_matches_pick_filter(self._url_pick_data[iid]):
                try:
                    tree.reattach(iid, "", "end")
                except tk.TclError:
                    pass
                visible += 1
            else:
                tree.detach(iid)
        total = len(self._url_pick_data)
        q = self._urls_pick_filter_query()
        if hasattr(self, "urls_pick_filter_label"):
            if q:
                self.urls_pick_filter_label.configure(text=f"{visible}/{total}")
            else:
                self.urls_pick_filter_label.configure(text="")

    def _sync_urls_pick_list(self, *, preserve_selection: bool = True) -> None:
        if not hasattr(self, "urls_pick_tree"):
            return
        prev_checked_keys: set[str] = set()
        if preserve_selection:
            prev_checked_keys = {
                normalize_board_url(self._url_pick_data[iid])
                for iid in self._url_pick_checked
                if iid in self._url_pick_data
            }
        self._urls_list_syncing = True
        try:
            tree = self.urls_pick_tree
            for item in tree.get_children():
                tree.delete(item)
            self._url_pick_data.clear()
            self._url_pick_checked.clear()
            idx = 0
            for url in self._all_parsed_urls():
                if self.excluded_urls.is_excluded(url):
                    continue
                key = normalize_board_url(url)
                iid = f"url_{idx}"
                idx += 1
                self._url_pick_data[iid] = url
                if preserve_selection and key in prev_checked_keys:
                    self._url_pick_checked.add(iid)
                display, tags = self._url_pick_display(url, key)
                tree.insert(
                    "",
                    "end",
                    iid=iid,
                    text=self._url_pick_mark(iid),
                    values=(display,),
                    tags=tags,
                )
        finally:
            self._urls_list_syncing = False
        self._apply_urls_check_filter()

    def _pick_list_urls(self) -> list[str]:
        return [self._url_pick_data[iid] for iid in self._url_pick_checked if iid in self._url_pick_data]

    def _has_url_pick_selection(self) -> bool:
        return bool(self._url_pick_checked)

    def _visible_url_pick_keys(self) -> list[str]:
        q = self._urls_pick_filter_query()
        iids = list(self._url_pick_data.keys())
        if not q:
            return iids
        return [iid for iid in iids if self._url_matches_pick_filter(self._url_pick_data[iid])]

    def _pick_all_urls(self) -> None:
        for iid in self._visible_url_pick_keys():
            self._url_pick_checked.add(iid)
            if hasattr(self, "urls_pick_tree"):
                self.urls_pick_tree.item(iid, text=CHK_ON)
        self._refresh_counts()

    def _pick_clear_urls(self) -> None:
        for iid in self._visible_url_pick_keys():
            self._url_pick_checked.discard(iid)
            if hasattr(self, "urls_pick_tree"):
                self.urls_pick_tree.item(iid, text=CHK_OFF)
        self._refresh_counts()

    def _pick_invert_urls(self) -> None:
        for iid in self._visible_url_pick_keys():
            if iid in self._url_pick_checked:
                self._url_pick_checked.discard(iid)
                mark = CHK_OFF
            else:
                self._url_pick_checked.add(iid)
                mark = CHK_ON
            if hasattr(self, "urls_pick_tree"):
                self.urls_pick_tree.item(iid, text=mark)
        self._refresh_counts()

    def _active_job_urls(self) -> list[str]:
        picked = self._pick_list_urls()
        if picked:
            source = picked
        else:
            source = self._all_parsed_urls()
        return [
            u for u in source
            if not self.excluded_urls.is_excluded(u) and not self._is_fail_skipped(u)
        ]

    def _count_fail_skipped_urls(self, urls: list[str] | None = None) -> int:
        items = urls if urls is not None else self._all_parsed_urls()
        return sum(
            1 for u in items
            if self._is_fail_skipped(u) and not self.excluded_urls.is_excluded(u)
        )

    def _get_write_mode(self) -> str:
        mode = getattr(self, "write_mode_var", None)
        return mode.get() if mode else "auto"

    def _schedule_url_recommend(self, *, immediate: bool = False) -> None:
        if self._urls_recommend_syncing or not hasattr(self, "urls_box"):
            return
        if self._url_recommend_after_id:
            self.after_cancel(self._url_recommend_after_id)
            self._url_recommend_after_id = None
        delay = 0 if immediate else 700
        self._url_recommend_after_id = self.after(delay, self._apply_url_recommendations)

    def _recommend_urls_in_box(self, *, show_toast: bool = True) -> int:
        if not hasattr(self, "urls_box"):
            return 0
        urls = parse_lines(self._get_text("urls"))
        if not urls:
            return 0
        new_urls, changes = recommend_urls(urls, mode=self._get_write_mode())
        if new_urls == urls:
            return 0
        self._urls_recommend_syncing = True
        try:
            self.urls_box.delete("1.0", "end")
            if new_urls:
                self.urls_box.insert("1.0", "\n".join(new_urls) + "\n")
        finally:
            self._urls_recommend_syncing = False
        if show_toast and changes:
            self._show_toast(f"URL {len(changes)}건 자동 정리됨")
        return len(changes)

    def _apply_url_recommendations(self) -> None:
        self._url_recommend_after_id = None
        changed = self._recommend_urls_in_box(show_toast=True)
        if changed:
            self._on_input_changed()
            self._sync_urls_pick_list(preserve_selection=True)
            self._refresh_counts()
            self._schedule_save()

    def _apply_startup_url_recommend(self) -> None:
        if self._recommend_urls_in_box(show_toast=False):
            self._sync_urls_pick_list(preserve_selection=False)
            self._refresh_counts()

    def _on_recommend_urls_click(self) -> None:
        n = self._recommend_urls_in_box(show_toast=True)
        if not n:
            self._show_toast("정리할 URL이 없습니다.")
            return
        self._on_input_changed()
        self._sync_urls_pick_list(preserve_selection=True)
        self._refresh_counts()
        self._schedule_save()

    def _on_write_mode_changed(self) -> None:
        if hasattr(self, "urls_box") and parse_lines(self._get_text("urls")):
            self._schedule_url_recommend()

    def _on_urls_input_changed(self) -> None:
        self._on_input_changed()
        if not self._urls_recommend_syncing:
            self._sync_urls_pick_list(preserve_selection=True)
            self._refresh_counts()
            self._schedule_url_recommend()
        else:
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
        fail_skip_n = self._count_fail_skipped_urls(urls)
        titles = parse_lines(self._get_text("titles"))
        try:
            sets = self.sets_panel.get_content_sets() if hasattr(self, "sets_panel") else []
            n_sets = len(sets)
        except Exception:
            n_sets = len(self.sets_panel._data) if hasattr(self, "sets_panel") else 0
        if self._has_url_pick_selection():
            url_label = f"전체 {len(urls)} · 체크 {len(active)}"
            if fail_skip_n:
                url_label += f" · 실패제외 {fail_skip_n}"
            job_label = f"작업 {len(active)}건"
        else:
            url_label = f"{len(urls)}개 URL"
            if fail_skip_n:
                url_label += f" · 실패제외 {fail_skip_n}"
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
        if not hasattr(self, "history_tree"):
            return
        self.history.load()
        tree = self.history_tree
        for item in tree.get_children():
            tree.delete(item)
        self._history_pick_checked.clear()
        self._history_pick_urls.clear()

        summaries = self.history.get_summaries()
        if not summaries:
            tree.insert("", "end", iid="_empty", text="", values=("게시 이력이 없습니다.", "", "", ""))
        else:
            for s in summaries:
                url = s.board_url
                display = url if len(url) <= 54 else url[:51] + "..."
                tags: tuple[str, ...] = ()
                if s.fail_count >= FAIL_SKIP_THRESHOLD:
                    display = f"⚠ 실패{s.fail_count} · {display}"
                    tags = ("fail_skip",)
                self._history_pick_urls[s.board_key] = url
                tree.insert(
                    "",
                    "end",
                    iid=s.board_key,
                    text=CHK_OFF,
                    values=(display, str(s.post_count), str(s.success_count), s.last_at),
                    tags=tags,
                )
        self._update_history_pick_ui()

        if self._selected_history_key:
            still = any(s.board_key == self._selected_history_key for s in summaries)
            if still:
                self._show_history_detail(self._selected_history_key)
            else:
                self._selected_history_key = ""
                self.history_detail.config(state="normal")
                self.history_detail.delete("1.0", "end")
                self.history_detail.config(state="disabled")

    def _selected_history_boards(self) -> list[tuple[str, str]]:
        return [
            (key, self._history_pick_urls[key])
            for key in self._history_pick_checked
            if key in self._history_pick_urls
        ]

    def _update_history_pick_ui(self) -> None:
        if not hasattr(self, "history_pick_count_var"):
            return
        n = len(self._history_pick_checked)
        total = len(self._history_pick_urls)
        if n:
            self.history_pick_count_var.set(f"{n}/{total} 선택")
            if hasattr(self, "history_delete_btn"):
                self.history_delete_btn.config(state="normal")
        else:
            self.history_pick_count_var.set(f"0/{total}" if total else "")
            if hasattr(self, "history_delete_btn"):
                self.history_delete_btn.config(state="disabled")

    def _history_pick_all(self) -> None:
        for key in self._history_pick_urls:
            self._history_pick_checked.add(key)
            self.history_tree.item(key, text=CHK_ON)
        self._update_history_pick_ui()

    def _history_pick_clear(self) -> None:
        self._history_pick_checked.clear()
        for key in self._history_pick_urls:
            self.history_tree.item(key, text=CHK_OFF)
        self._update_history_pick_ui()

    def _history_select_same_fail_reason(self) -> None:
        if not self._selected_history_key:
            messagebox.showinfo("안내", "목록에서 URL을 먼저 클릭해 상세를 연 다음 사용하세요.")
            return
        target_reason = ""
        for reason, _n, boards in self.history.get_fail_reason_groups():
            if any(k == self._selected_history_key for k, _u in boards):
                target_reason = reason
                break
        if not target_reason:
            messagebox.showinfo("안내", "선택한 URL에 실패 이력이 없습니다.")
            return
        keys = set(self.history.board_keys_with_fail_reason(target_reason))
        self._history_pick_checked.clear()
        for key in self._history_pick_urls:
            mark = CHK_ON if key in keys else CHK_OFF
            if key in keys:
                self._history_pick_checked.add(key)
            self.history_tree.item(key, text=mark)
        self._update_history_pick_ui()

    def _open_fail_reason_delete_dialog(self) -> None:
        groups = self.history.get_fail_reason_groups()
        if not groups:
            messagebox.showinfo("안내", "삭제할 실패 이력이 없습니다.")
            return

        win = tk.Toplevel(self)
        win.title("실패 사유별 삭제")
        win.geometry("640x380")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text="삭제할 실패 사유를 선택하세요. (해당 사유의 URL이 모두 제외됩니다)",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        list_frame = ttk.Frame(win)
        list_frame.pack(fill="both", expand=True, padx=12, pady=4)
        sb = ttk.Scrollbar(list_frame, orient="vertical")
        lb = tk.Listbox(list_frame, font=(FONT, 9), yscrollcommand=sb.set, height=12)
        sb.config(command=lb.yview)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        display_rows: list[tuple[str, int, list[tuple[str, str]]]] = []
        for reason, count, boards in groups:
            short = reason if len(reason) <= 72 else reason[:69] + "..."
            lb.insert("end", f"[{count}개] {short}")
            display_rows.append((reason, count, boards))
        if display_rows:
            lb.selection_set(0)

        preview = scrolledtext.ScrolledText(win, height=6, font=(FONT_MONO, 8), state="disabled")
        preview.pack(fill="x", padx=12, pady=(4, 8))

        def show_preview(_event=None) -> None:
            sel = lb.curselection()
            if not sel:
                return
            _reason, _count, boards = display_rows[sel[0]]
            lines = [u for _k, u in boards[:20]]
            if len(boards) > 20:
                lines.append(f"... 외 {len(boards) - 20}개")
            preview.config(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", "\n".join(lines))
            preview.config(state="disabled")

        lb.bind("<<ListboxSelect>>", show_preview)
        show_preview()

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        def on_delete() -> None:
            sel = lb.curselection()
            if not sel:
                messagebox.showwarning("선택", "실패 사유를 선택해 주세요.", parent=win)
                return
            reason, count, boards = display_rows[sel[0]]
            if not messagebox.askyesno(
                "실패 사유 삭제",
                f"아래 사유의 URL {count}개를 모두 삭제(제외)할까요?\n\n{reason}",
                parent=win,
            ):
                return
            win.destroy()
            self._exclude_boards_from_history(boards)

        ttk.Button(btn_row, text="선택 사유 URL 삭제", command=on_delete).pack(side="right", padx=4)
        ttk.Button(btn_row, text="닫기", command=win.destroy).pack(side="right")

    def _select_history_board(self, board_key: str) -> None:
        self._selected_history_key = board_key
        self._show_history_detail(board_key)

    def _show_history_detail(self, board_key: str) -> None:
        text = self.history.format_detail(board_key)
        self.history_detail.config(state="normal")
        self.history_detail.delete("1.0", "end")
        self.history_detail.insert("1.0", text)
        self.history_detail.config(state="disabled")

    def _exclude_board_from_history(self, board_key: str, board_url: str) -> None:
        self._exclude_boards_from_history([(board_key, board_url)])

    def _exclude_selected_boards_from_history(self) -> None:
        selected = self._selected_history_boards()
        if not selected:
            messagebox.showinfo("안내", "삭제할 URL을 체크해 주세요.")
            return
        self._exclude_boards_from_history(selected)

    def _exclude_boards_from_history(self, boards: list[tuple[str, str]]) -> None:
        if not boards:
            return
        n = len(boards)
        if n == 1:
            board_key, board_url = boards[0]
            preview = board_url
        else:
            preview_lines = [u for _, u in boards[:8]]
            if n > 8:
                preview_lines.append(f"... 외 {n - 8}개")
            preview = "\n".join(preview_lines)

        if not messagebox.askyesno(
            "URL 제외",
            f"선택한 {n}개 URL을 삭제(제외)할까요?\n\n{preview}\n\n"
            "· 게시 이력에서 제거\n"
            "· 게시판 URL 목록에서 제거\n"
            "· 다음 배치부터 자동 게시 안 함",
        ):
            return

        removed_total = 0
        for key, url in boards:
            urls = self.history.collect_urls_for_board(key)
            urls.add(url.strip())
            self.excluded_urls.add(key, urls)
            removed_total += self.history.remove_board(key)

        if self._selected_history_key in {k for k, _ in boards}:
            self._selected_history_key = ""

        self._purge_excluded_urls_from_box()
        self._refresh_history_tree()
        self._sync_urls_pick_list(preserve_selection=False)
        self._on_input_changed()
        self._save_app_state()
        log.info("URL 제외 %d건 (이력 %d건)", n, removed_total)
        messagebox.showinfo(
            "완료",
            f"{n}개 URL을 제외 목록에 추가했습니다.\n다음 배치부터 해당 URL은 게시되지 않습니다.",
        )

    def _purge_excluded_urls_from_box(self) -> None:
        if not hasattr(self, "urls_box"):
            return
        remaining = [u for u in self._all_parsed_urls() if not self.excluded_urls.is_excluded(u)]
        self.urls_box.delete("1.0", "end")
        if remaining:
            self.urls_box.insert("1.0", "\n".join(remaining) + "\n")

    def _build_remote_logs_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  원격 로그  ")

        hint = ttk.Label(
            tab,
            text=(
                "이용자 PC 로그·실패 케이스를 GitHub에 올리고, 관리자 PC에서 조회·Cursor 보강에 사용합니다. "
                f"이 PC ID: {get_pc_id()}"
            ),
            style="Hint.TLabel",
            wraplength=980,
        )
        hint.pack(anchor="w", padx=8, pady=(8, 4))

        cfg_f = ttk.LabelFrame(tab, text="동기화 설정", style="Card.TLabelframe", padding=8)
        cfg_f.pack(fill="x", padx=8, pady=4)

        self.log_sync_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            cfg_f,
            text="로그 자동 업로드 켜기",
            variable=self.log_sync_enabled_var,
            command=self._on_log_sync_settings_changed,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)

        self.log_sync_failures_auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            cfg_f,
            text="실패 케이스 자동 업로드 (폼 못 찾음 등)",
            variable=self.log_sync_failures_auto_var,
            command=self._on_log_sync_settings_changed,
        ).grid(row=0, column=2, columnspan=3, sticky="w", pady=2)

        ttk.Label(cfg_f, text="Owner").grid(row=1, column=0, sticky="w", pady=2)
        self.log_sync_owner_var = tk.StringVar(value="lee3215-ko")
        ttk.Entry(cfg_f, textvariable=self.log_sync_owner_var, width=24).grid(
            row=1, column=1, sticky="w", padx=6, pady=2
        )

        ttk.Label(cfg_f, text="Repo").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=2)
        self.log_sync_repo_var = tk.StringVar(value="backlink-writer-logs")
        ttk.Entry(cfg_f, textvariable=self.log_sync_repo_var, width=28).grid(
            row=1, column=3, sticky="w", padx=6, pady=2
        )

        ttk.Label(cfg_f, text="GitHub Token").grid(row=2, column=0, sticky="w", pady=2)
        self.log_sync_token_var = tk.StringVar()
        ttk.Entry(cfg_f, textvariable=self.log_sync_token_var, width=48, show="*").grid(
            row=2, column=1, columnspan=2, sticky="we", padx=6, pady=2
        )

        ttk.Label(cfg_f, text="주기(분)").grid(row=2, column=3, sticky="w", padx=(12, 0), pady=2)
        self.log_sync_interval_var = tk.DoubleVar(value=30)
        ttk.Entry(cfg_f, textvariable=self.log_sync_interval_var, width=8).grid(
            row=2, column=4, sticky="w", padx=6, pady=2
        )

        btn_row = ttk.Frame(cfg_f)
        btn_row.grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Button(btn_row, text="설정 저장", command=self._on_log_sync_settings_changed).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="연결 테스트", command=self._on_log_sync_test_connection).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="지금 이 PC 로그 업로드", command=self._on_log_sync_upload_now).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="실패 케이스 업로드", command=self._on_upload_local_failures).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="원격 PC 목록 새로고침", command=self._on_remote_logs_refresh).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="원격 실패 목록", command=self._on_remote_failures_refresh).pack(
            side="left", padx=(0, 6)
        )
        self.log_sync_detail_var = tk.StringVar(value="동기화 대기")
        ttk.Label(btn_row, textvariable=self.log_sync_detail_var, style="Hint.TLabel").pack(
            side="left", padx=8
        )

        help_txt = (
            "1) GitHub private 저장소 backlink-writer-logs + PAT(Contents)  "
            "2) 이용자 PC: 로그·실패 자동 업로드 켜기  "
            "3) 관리자: 원격 실패 목록 → Cursor 보고서로 기능 보강"
        )
        ttk.Label(cfg_f, text=help_txt, style="Hint.TLabel", wraplength=980).grid(
            row=4, column=0, columnspan=5, sticky="w", pady=(8, 0)
        )

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left_nb = ttk.Notebook(body)
        body.add(left_nb, weight=1)

        left = ttk.Frame(left_nb, padding=4)
        left_nb.add(left, text="PC 로그")
        cols = ("pc", "size")
        self.remote_log_tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
        self.remote_log_tree.heading("pc", text="PC ID")
        self.remote_log_tree.heading("size", text="크기")
        self.remote_log_tree.column("pc", width=220)
        self.remote_log_tree.column("size", width=90, anchor="e")
        self.remote_log_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.remote_log_tree.yview)
        sb.pack(side="right", fill="y")
        self.remote_log_tree.configure(yscrollcommand=sb.set)
        self.remote_log_tree.bind("<<TreeviewSelect>>", self._on_remote_log_select)

        fail_left = ttk.Frame(left_nb, padding=4)
        left_nb.add(fail_left, text="실패 케이스")
        fcols = ("pc", "reason", "url")
        self.remote_fail_tree = ttk.Treeview(fail_left, columns=fcols, show="headings", height=14)
        self.remote_fail_tree.heading("pc", text="PC")
        self.remote_fail_tree.heading("reason", text="사유")
        self.remote_fail_tree.heading("url", text="URL")
        self.remote_fail_tree.column("pc", width=100)
        self.remote_fail_tree.column("reason", width=140)
        self.remote_fail_tree.column("url", width=180)
        self.remote_fail_tree.pack(fill="both", expand=True, side="left")
        fsb = ttk.Scrollbar(fail_left, orient="vertical", command=self.remote_fail_tree.yview)
        fsb.pack(side="right", fill="y")
        self.remote_fail_tree.configure(yscrollcommand=fsb.set)
        self.remote_fail_tree.bind("<<TreeviewSelect>>", self._on_remote_failure_select)

        right = ttk.LabelFrame(body, text="내용", style="Card.TLabelframe", padding=4)
        body.add(right, weight=3)
        rbtn = ttk.Frame(right)
        rbtn.pack(fill="x", pady=(0, 4))
        ttk.Button(rbtn, text="클립보드 복사", command=self._on_remote_log_copy).pack(side="left", padx=(0, 6))
        ttk.Button(rbtn, text="파일로 저장", command=self._on_remote_log_save).pack(side="left", padx=(0, 6))
        ttk.Button(rbtn, text="Cursor 보고서 복사", command=self._on_remote_failure_cursor_copy).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(rbtn, text="Cursor 보고서 저장", command=self._on_remote_failure_cursor_save).pack(
            side="left"
        )
        self.remote_log_box = scrolledtext.ScrolledText(
            right, height=18, font=(FONT_MONO, 9),
            bg=COLORS["log_bg"], fg=COLORS["log_fg"], relief="flat", padx=8, pady=8,
        )
        self.remote_log_box.pack(fill="both", expand=True)
        self._remote_log_text = ""

        for var in (
            self.log_sync_owner_var,
            self.log_sync_repo_var,
            self.log_sync_token_var,
            self.log_sync_interval_var,
        ):
            try:
                var.trace_add("write", lambda *_a: self._schedule_save())
            except Exception:
                pass

    def _get_log_sync_config(self) -> LogSyncConfig:
        try:
            interval = float(self.log_sync_interval_var.get())
        except (TypeError, ValueError, tk.TclError):
            interval = 30.0
        return LogSyncConfig(
            enabled=bool(self.log_sync_enabled_var.get()),
            owner=self.log_sync_owner_var.get().strip(),
            repo=self.log_sync_repo_var.get().strip(),
            token=self.log_sync_token_var.get().strip(),
            interval_min=interval,
            upload_failures_auto=bool(
                self.log_sync_failures_auto_var.get()
                if hasattr(self, "log_sync_failures_auto_var")
                else True
            ),
        )

    def _on_log_sync_settings_changed(self) -> None:
        self._schedule_save()
        self._refresh_log_sync_status()
        self._schedule_log_sync_timer()
        self._show_toast("로그 동기화 설정 저장됨")

    def _refresh_log_sync_status(self) -> None:
        if not hasattr(self, "log_sync_status_var"):
            return
        cfg = self._get_log_sync_config() if hasattr(self, "log_sync_enabled_var") else None
        last = load_last_sync()
        if cfg and cfg.enabled and cfg.token:
            base = "로그동기화 ON"
        elif cfg and cfg.enabled:
            base = "로그동기화 (토큰 없음)"
        else:
            base = "로그동기화 OFF"
        if last.get("at"):
            mark = "✓" if last.get("ok") else "✗"
            base = f"{base} · {mark} {last.get('at')}"
        self.log_sync_status_var.set(base)
        if hasattr(self, "log_sync_detail_var"):
            self.log_sync_detail_var.set(last.get("message") or f"이 PC: {get_pc_id()}")

    def _schedule_log_sync_timer(self) -> None:
        if self._log_sync_after_id:
            try:
                self.after_cancel(self._log_sync_after_id)
            except Exception:
                pass
            self._log_sync_after_id = None
        if not hasattr(self, "log_sync_enabled_var"):
            return
        cfg = self._get_log_sync_config()
        if not cfg.is_ready():
            return
        minutes = max(5.0, float(cfg.interval_min or 30))
        self._log_sync_after_id = self.after(int(minutes * 60 * 1000), self._on_log_sync_timer)

    def _on_log_sync_timer(self) -> None:
        self._log_sync_after_id = None
        self._trigger_log_sync_upload(note="주기 업로드", silent=True)
        self._schedule_log_sync_timer()

    def _on_log_sync_test_connection(self) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.token:
            messagebox.showwarning("안내", "GitHub Token을 입력해 주세요.")
            return

        def worker() -> None:
            ok, msg = verify_repo_access(cfg)

            def done() -> None:
                self.log_sync_detail_var.set(msg.split("\n")[0][:120])
                if ok:
                    self._remote_upload_blocked = False
                    self._remote_upload_block_msg = ""
                    messagebox.showinfo("연결 테스트", msg)
                else:
                    self._remote_upload_blocked = True
                    self._remote_upload_block_msg = msg
                    messagebox.showerror("연결 실패", msg)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_log_sync_upload_now(self) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.token:
            messagebox.showwarning("안내", "GitHub Token을 입력해 주세요.")
            return
        if not cfg.enabled:
            if not messagebox.askyesno("안내", "자동 업로드가 꺼져 있습니다. 지금 한 번만 업로드할까요?"):
                return
        self._remote_upload_blocked = False
        self._trigger_log_sync_upload(note="수동 업로드", silent=False)

    def _note_remote_upload_result(self, ok: bool, msg: str) -> None:
        """404/401이면 배치 중 반복 업로드를 막아 로그 스팸·지연을 줄인다."""
        if ok:
            self._remote_upload_blocked = False
            self._remote_upload_block_msg = ""
            return
        if any(x in msg for x in ("HTTP 404", "HTTP 401", "HTTP 403", "저장소 접근 불가", "토큰이 잘못")):
            if not self._remote_upload_blocked:
                self._log(
                    "[원격업로드] 권한/토큰 문제로 자동 업로드를 멈춥니다. "
                    "원격 로그 탭「연결 테스트」후 PAT에 backlink-writer-logs Contents 권한을 주세요.",
                    "err",
                )
            self._remote_upload_blocked = True
            self._remote_upload_block_msg = msg

    def _trigger_log_sync_upload(self, *, note: str = "", silent: bool = True) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.token or not cfg.owner or not cfg.repo:
            return
        if self._log_sync_uploading:
            return
        if silent and self._remote_upload_blocked:
            return
        self._log_sync_uploading = True

        def worker() -> None:
            ok, msg = upload_latest_log(cfg, note=note)

            def done() -> None:
                self._log_sync_uploading = False
                self._note_remote_upload_result(ok, msg)
                self._refresh_log_sync_status()
                if not silent:
                    if ok:
                        self._show_toast(msg)
                    else:
                        self._show_toast(msg, error=True)
                        messagebox.showerror("업로드 실패", msg)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_logs_refresh(self) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.token:
            messagebox.showwarning("안내", "GitHub Token을 입력해 주세요.")
            return
        self.log_sync_detail_var.set("원격 목록 불러오는 중…")

        def worker() -> None:
            entries, err = list_client_logs(cfg)

            def done() -> None:
                for row in self.remote_log_tree.get_children():
                    self.remote_log_tree.delete(row)
                if err:
                    self.log_sync_detail_var.set(err)
                    if "없음" not in err:
                        messagebox.showerror("조회 실패", err)
                    return
                for e in entries:
                    size = f"{e.size:,}" if e.size else "-"
                    self.remote_log_tree.insert("", "end", iid=e.pc_id, values=(e.pc_id, size))
                self.log_sync_detail_var.set(f"원격 PC {len(entries)}대")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_log_select(self, _event=None) -> None:
        sel = self.remote_log_tree.selection()
        if not sel:
            return
        pc_id = sel[0]
        cfg = self._get_log_sync_config()
        self.log_sync_detail_var.set(f"{pc_id} 로그 불러오는 중…")

        def worker() -> None:
            text, err = fetch_client_log(cfg, pc_id)

            def done() -> None:
                self.remote_log_box.delete("1.0", "end")
                if err:
                    self._remote_log_text = ""
                    self.remote_log_box.insert("1.0", err)
                    self.log_sync_detail_var.set(err)
                    return
                self._remote_log_text = text
                self.remote_log_box.insert("1.0", text)
                self.log_sync_detail_var.set(f"{pc_id} · {len(text):,}자")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_log_copy(self) -> None:
        text = self._remote_log_text or self.remote_log_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("안내", "복사할 로그가 없습니다.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self._show_toast("원격 로그 복사됨")

    def _on_remote_log_save(self) -> None:
        from tkinter import filedialog

        text = self._remote_log_text or self.remote_log_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("안내", "저장할 로그가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            title="원격 로그 저장",
            defaultextension=".log",
            filetypes=[("Log", "*.log"), ("Text", "*.txt"), ("All", "*.*")],
            initialfile=f"remote-{get_pc_id()}.log",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._show_toast(f"저장됨: {path}")
        except OSError as exc:
            messagebox.showerror("저장 실패", str(exc))

    def _writer_for_job(self, job: PostJob | TargetJob):
        w = self.writer
        if isinstance(job, TargetJob):
            if job.action == "comment_gnuboard":
                w = self.comment_writer
            elif job.action == "comment_wordpress":
                w = self.wp_comment_writer
            elif job.action == "comment_movable_type":
                w = self.mt_comment_writer
            elif job.action == "comment_custom_bbs":
                w = self.custom_bbs_writer
            elif job.action == "comment_phpbb":
                w = self.phpbb_comment_writer
            elif job.action == "comment_generic":
                w = self.generic_comment_writer
            elif job.kind == "zeroboard_post":
                w = self.zboard_writer
        return w

    def _capture_failure_case(self, job: PostJob | TargetJob, raw_error: str) -> dict | None:
        """브라우저가 아직 열린 상태에서 실패 케이스 JSON 구성."""
        if not is_strengthenable_error(raw_error):
            return None
        w = self._writer_for_job(job)
        url = getattr(job, "board_url", "") or ""
        action = getattr(job, "action", "") if isinstance(job, TargetJob) else "post"
        kind = getattr(job, "kind", "") if isinstance(job, TargetJob) else "board_post"
        snap_dict: dict = {}
        markers: dict = {}
        html = ""
        try:
            if w.is_open() and w.page is not None:
                snap = capture_snapshot_from_page(w.page, url)
                snap_dict = snap.to_dict()
                markers = capture_dom_markers(w.page)
                if is_form_miss_error(raw_error) or snap.comment_form_found:
                    html = capture_html_excerpt(w.page, max_chars=35000)
        except Exception as exc:
            snap_dict = {"error": f"스냅샷 실패: {exc}"}
        return build_failure_case(
            url=url,
            raw_error=raw_error,
            localized_reason=localize_error_message(raw_error),
            action=str(action),
            kind=str(kind),
            app_version=APP_VERSION,
            writer_urls={
                "list_url": getattr(w, "last_list_url", "") or "",
                "write_url": getattr(w, "last_write_url", "") or "",
                "final_url": (w.page.url if w.is_open() and w.page else "") or "",
            },
            snapshot=snap_dict,
            dom_markers=markers,
            html_excerpt=html,
            note="batch_fail",
        )

    def _queue_failure_case_upload(self, case: dict | None) -> None:
        if not case:
            return
        if self._remote_upload_blocked:
            return
        cfg = self._get_log_sync_config()
        if not cfg.can_upload():
            return
        if not cfg.upload_failures_auto:
            return

        def worker() -> None:
            ok, msg = upload_failure_case(cfg, case)

            def done() -> None:
                self._note_remote_upload_result(ok, msg)
                if ok:
                    self._log(f"[실패업로드] {msg}", "info")
                elif not self._remote_upload_blocked:
                    self._log(f"[실패업로드] {msg}", "err")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_upload_local_failures(self) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.token:
            messagebox.showwarning("안내", "GitHub Token을 입력해 주세요.")
            return
        fails = self.history.get_recent_failures(40)
        if not fails:
            messagebox.showinfo("안내", "업로드할 실패 이력이 없습니다.")
            return
        cases = []
        for r in fails:
            if not is_strengthenable_error(r.message):
                continue
            cases.append(
                build_failure_case(
                    url=r.post_url or r.list_url or r.board_url,
                    raw_error=r.message,
                    localized_reason=localize_error_message(r.message),
                    action="",
                    kind="",
                    app_version=APP_VERSION,
                    writer_urls={"list_url": r.list_url, "write_url": r.write_url},
                    note=f"history:{r.timestamp}",
                )
            )
        if not cases:
            messagebox.showinfo("안내", "보강 대상 실패(폼 미인식 등)가 없습니다.")
            return
        if not messagebox.askyesno(
            "실패 케이스 업로드",
            f"최근 실패 {len(cases)}건을 원격 저장소에 올릴까요?\n"
            "(이력 기반 — 페이지 스냅샷은 배치 실패 시에만 포함)",
        ):
            return
        self._upload_failure_cases_async(cases, silent=False)

    def _on_history_upload_failures(self) -> None:
        self._on_upload_local_failures()

    def _upload_failure_cases_async(self, cases: list[dict], *, silent: bool = True) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.can_upload() or not cases:
            return
        if self._failure_uploading:
            return
        self._failure_uploading = True

        def worker() -> None:
            ok_n, fail_n, last = upload_failure_cases(cfg, cases)

            def done() -> None:
                self._failure_uploading = False
                msg = f"실패 케이스 업로드 성공 {ok_n} / 실패 {fail_n}"
                if hasattr(self, "log_sync_detail_var"):
                    self.log_sync_detail_var.set(msg)
                if not silent:
                    if fail_n and not ok_n:
                        messagebox.showerror("업로드 실패", last or msg)
                    else:
                        self._show_toast(msg)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_failures_refresh(self) -> None:
        cfg = self._get_log_sync_config()
        if not cfg.token:
            messagebox.showwarning("안내", "GitHub Token을 입력해 주세요.")
            return
        self.log_sync_detail_var.set("원격 실패 목록 불러오는 중…")

        def worker() -> None:
            entries, err = list_failure_cases(cfg)

            def done() -> None:
                self._remote_failure_cases = entries
                for row in self.remote_fail_tree.get_children():
                    self.remote_fail_tree.delete(row)
                if err:
                    self.log_sync_detail_var.set(err)
                    if "없음" not in err:
                        messagebox.showerror("조회 실패", err)
                    return
                for e in entries:
                    iid = f"{e.pc_id}::{e.case_id}"
                    reason = (e.reason or "")[:40]
                    if e.form_in_dom:
                        reason = "⚠폼있음·" + reason
                    self.remote_fail_tree.insert(
                        "",
                        "end",
                        iid=iid,
                        values=(e.pc_id[:18], reason, (e.url or "")[:60]),
                    )
                self.log_sync_detail_var.set(f"원격 실패 {len(entries)}건")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_failure_select(self, _event=None) -> None:
        sel = self.remote_fail_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if "::" not in iid:
            return
        pc_id, case_id = iid.split("::", 1)
        cfg = self._get_log_sync_config()
        self.log_sync_detail_var.set(f"{case_id} 불러오는 중…")

        def worker() -> None:
            data, err = fetch_failure_case(cfg, pc_id, case_id)

            def done() -> None:
                self.remote_log_box.delete("1.0", "end")
                if err or not data:
                    self._remote_log_text = ""
                    self._remote_failure_detail = None
                    self.remote_log_box.insert("1.0", err or "없음")
                    self.log_sync_detail_var.set(err or "없음")
                    return
                self._remote_failure_detail = data
                report = failure_case_to_cursor_report(data)
                self._remote_log_text = report
                self.remote_log_box.insert("1.0", report)
                mark = "폼DOM있음" if data.get("form_in_dom") else "폼DOM불명"
                self.log_sync_detail_var.set(f"{pc_id} · {case_id} · {mark}")

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_remote_failure_cursor_copy(self) -> None:
        text = ""
        if self._remote_failure_detail:
            text = failure_case_to_cursor_report(self._remote_failure_detail)
        else:
            text = self._remote_log_text or self.remote_log_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("안내", "먼저 실패 케이스를 선택해 주세요.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self._show_toast("Cursor 보고서 복사됨 — 채팅에 붙여넣으세요")

    def _on_remote_failure_cursor_save(self) -> None:
        from tkinter import filedialog

        text = ""
        if self._remote_failure_detail:
            text = failure_case_to_cursor_report(self._remote_failure_detail)
        else:
            text = self._remote_log_text or self.remote_log_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("안내", "먼저 실패 케이스를 선택해 주세요.")
            return
        path = filedialog.asksaveasfilename(
            title="Cursor 실패 보고서 저장",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All", "*.*")],
            initialfile="failure_case_cursor.md",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._show_toast("Cursor 보고서 저장됨")
        except OSError as exc:
            messagebox.showerror("저장 실패", str(exc))

    def _on_history_select(self, _event=None) -> None:
        pass

    def _clear_history(self) -> None:
        if messagebox.askyesno("이력 삭제", "게시 이력을 모두 삭제할까요?"):
            self.history.clear()
            self._refresh_history_tree()
            self.history_detail.config(state="normal")
            self.history_detail.delete("1.0", "end")
            self.history_detail.config(state="disabled")

    def _record_history(self, job: PostJob | TargetJob, status: str, message: str) -> None:
        w = self._writer_for_job(job)
        rec = PostRecord.from_job(
            job.board_url,
            job.title,
            job.links,
            status=status,
            message=localize_error_message(message) if status == "fail" else message,
            list_url=w.last_list_url or job.board_url,
            write_url=w.last_write_url,
            post_url=w.last_post_url if status == "success" else "",
        )
        self.history.add(rec)
        if status == "fail":
            key = rec.board_key
            if self.history.get_fail_count(key) == FAIL_SKIP_THRESHOLD:
                skip_url = rec.post_url or rec.list_url or rec.board_url
                self.after(
                    0,
                    lambda u=skip_url: self._log(
                        f"[자동제외] {u} — 실패 {FAIL_SKIP_THRESHOLD}회 도달, 이후 배치에서 건너뜁니다",
                        "info",
                    ),
                )
        def _after_record() -> None:
            self._refresh_history_tree()
            self._sync_urls_pick_list(preserve_selection=True)
            self._refresh_counts()

        self.after(0, _after_record)

    def _build_jobs_for_active_urls(self) -> list[PostJob] | list[TargetJob]:
        urls = self._active_job_urls()
        if not urls:
            excluded_n = self.excluded_urls.count()
            fail_skip_n = self._count_fail_skipped_urls()
            if excluded_n or fail_skip_n:
                parts = []
                if excluded_n:
                    parts.append(f"수동 제외 {excluded_n}건")
                if fail_skip_n:
                    parts.append(f"실패 {FAIL_SKIP_THRESHOLD}회 이상 자동제외 {fail_skip_n}건")
                raise ValueError(
                    f"작업할 URL이 없습니다. ({' · '.join(parts)} — 해당 URL은 배치에서 건너뜁니다.)"
                )
            raise ValueError("작업할 URL을 선택하거나 목록에 URL을 입력해 주세요.")
        content_sets = self.sets_panel.get_content_sets()
        mode = getattr(self, "write_mode_var", None)
        mode_val = mode.get() if mode else "post"
        if mode_val in ("auto", "comment"):
            jobs, _analyses = build_target_jobs(
                "\n".join(urls),
                content_sets,
                mode=mode_val,
                titles_text=self._get_text("titles"),
            )
            if not jobs:
                raise ValueError(
                    f"작업 가능한 URL이 없습니다. (모드: {mode_val}) — URL 검사 탭에서 형식을 확인하세요."
                )
            return jobs
        return build_jobs(
            "\n".join(urls),
            content_sets,
            self._get_text("titles"),
            self.category_var.get(),
        )

    def _preview_name(self) -> None:
        self._log(f"랜덤 이름: {random_english_name()}", "info")

    def _validate_jobs(self) -> list[PostJob] | list[TargetJob] | None:
        try:
            jobs = self._build_jobs_for_active_urls()
            self._batch_jobs = jobs
            self._refresh_counts()
            return jobs
        except ValueError as e:
            messagebox.showwarning("입력 오류", str(e))
            return None

    def _rebuild_jobs_for_next_round(self) -> list[PostJob] | list[TargetJob] | None:
        holder: list[list[PostJob] | list[TargetJob] | None] = [None]
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
        if self._window_moving:
            self._log_pending.append((msg, tag))
            return
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
        self.comment_writer.reset_cancel()
        self.wp_comment_writer.reset_cancel()
        self.mt_comment_writer.reset_cancel()
        self.zboard_writer.reset_cancel()
        self.custom_bbs_writer.reset_cancel()

        def worker():
            from browser_session import _reset_playwright_event_loop

            _reset_playwright_event_loop()
            try:
                result = fn()
                self.after(0, lambda r=result: self._on_async_done(None, r, on_success))
            except Exception as e:
                self.after(0, lambda err=e: self._on_async_done(err, None, on_success))
            finally:
                self._close_all_writers()
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
        self.comment_writer.cancel()
        self.wp_comment_writer.cancel()
        self.mt_comment_writer.cancel()
        self.zboard_writer.cancel()
        self.custom_bbs_writer.cancel()
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
            if self._any_cancelled():
                return False
            self.after(0, lambda r=remaining, lb=label: self.status_var.set(f"{lb} {r // 60}분{r % 60}초"))
            time.sleep(1)
        return True

    def _post_writer_for(self, job: PostJob | TargetJob):
        if isinstance(job, TargetJob) and job.kind == "zeroboard_post":
            return self.zboard_writer
        return self.writer

    def _any_cancelled(self) -> bool:
        return (
            self.writer._cancelled
            or self.comment_writer._cancelled
            or self.wp_comment_writer._cancelled
            or self.mt_comment_writer._cancelled
            or self.zboard_writer._cancelled
            or self.custom_bbs_writer._cancelled
            or self.phpbb_comment_writer._cancelled
            or self.generic_comment_writer._cancelled
        )

    def _close_all_writers(self) -> None:
        """배치 건마다 Playwright·asyncio 루프 정리 (연속 작업 시 충돌 방지)."""
        for w in (
            self.writer,
            self.comment_writer,
            self.wp_comment_writer,
            self.mt_comment_writer,
            self.zboard_writer,
            self.custom_bbs_writer,
            self.phpbb_comment_writer,
            self.generic_comment_writer,
        ):
            try:
                w.close()
            except Exception:
                pass

    def _execute_job(self, job: PostJob | TargetJob, *, auto_submit: bool) -> str:
        self.writer.reset_cancel()
        self.comment_writer.reset_cancel()
        self.wp_comment_writer.reset_cancel()
        self.mt_comment_writer.reset_cancel()
        self.custom_bbs_writer.reset_cancel()
        self.phpbb_comment_writer.reset_cancel()
        self.generic_comment_writer.reset_cancel()
        self.zboard_writer.reset_cancel()
        links = _job_links(job)
        idx = job.index - 1

        if isinstance(job, TargetJob):
            if job.action == "comment_gnuboard":
                self.comment_writer.open_comment_page(job.url)
                if auto_submit:
                    return self.comment_writer.fill_and_submit_comment(links, post_index=idx)
                return self.comment_writer.fill_comment(links, post_index=idx)
            if job.action == "comment_wordpress":
                self.wp_comment_writer.open_post(job.url)
                if auto_submit:
                    return self.wp_comment_writer.fill_and_submit_comment(links, post_index=idx)
                return self.wp_comment_writer.fill_comment(links, post_index=idx)
            if job.action == "comment_movable_type":
                self.mt_comment_writer.open_post(job.url)
                if auto_submit:
                    return self.mt_comment_writer.fill_and_submit_comment(links, post_index=idx)
                return self.mt_comment_writer.fill_comment(links, post_index=idx)
            if job.action == "comment_custom_bbs":
                self.custom_bbs_writer.open_post(job.url)
                if auto_submit:
                    return self.custom_bbs_writer.fill_and_submit_comment(links, post_index=idx)
                return self.custom_bbs_writer.fill_comment(links, post_index=idx)
            if job.action == "comment_phpbb":
                self.phpbb_comment_writer.open_post(job.url)
                if auto_submit:
                    return self.phpbb_comment_writer.fill_and_submit_comment(links, post_index=idx)
                return self.phpbb_comment_writer.fill_comment(links, post_index=idx)
            if job.action == "comment_generic":
                self.generic_comment_writer.open_post(job.url)
                if auto_submit:
                    return self.generic_comment_writer.fill_and_submit_comment(links, post_index=idx)
                return self.generic_comment_writer.fill_comment(links, post_index=idx)
            # post via board writer (그누보드 / 제로보드)
            post_w = self._post_writer_for(job)
            post_w.open_browser(job.url)
            if auto_submit:
                return post_w.fill_and_submit(
                    job.title, links, category=job.category, post_index=idx,
                )
            return post_w.fill_form(
                job.title, links, category=job.category, post_index=idx,
            )

        post_w = self._post_writer_for(job)
        post_w.open_browser(job.board_url)
        if auto_submit:
            return post_w.fill_and_submit(
                job.title, links, category=job.category, post_index=idx,
            )
        return post_w.fill_form(
            job.title, links, category=job.category, post_index=idx,
        )

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
                if self._any_cancelled():
                    break

                picks = job.picks_summary
                action_note = ""
                if isinstance(job, TargetJob):
                    action_note = f" [{job.action_label}]"
                else:
                    action_note = ""
                self.after(0, lambda j=job, an=action_note: self._set_progress(j.index - 1, j.total, j.label + an))
                self.after(0, lambda j=job, an=action_note: self._log(f"\n══ {j.label}{an} ══", "head"))
                self.after(0, lambda p=picks: self._log(f"  {p}", "info"))

                try:
                    msg = self._execute_job(job, auto_submit=auto_submit)
                    ok += 1
                    self.after(0, lambda m=msg: self._log(m, "ok"))
                    self._record_history(job, "success", msg)
                except Exception as e:
                    fail += 1
                    raw_err = str(e)
                    err_ko = localize_error_message(raw_err)
                    self.after(0, lambda er=err_ko, j=job: self._log(f"✗ {j.label}: {er}", "err"))
                    # 브라우저 닫기 전에 스냅샷 수집 → 원격 업로드
                    try:
                        case = self._capture_failure_case(job, raw_err)
                        self._queue_failure_case_upload(case)
                    except Exception:
                        pass
                    self._record_history(job, "fail", raw_err)
                finally:
                    self._close_all_writers()

                self.after(0, lambda j=job: self._set_progress(j.index, j.total, f"{j.index}/{j.total}"))

                if (
                    idx < len(jobs) - 1
                    and self._write_post_interval_sec > 0
                    and not self._any_cancelled()
                ):
                    if not self._sleep_writing(self._write_post_interval_sec, "다음 게시까지"):
                        break

            if self._any_cancelled() or not self._write_continuous:
                break
            if not self._sleep_writing(self._write_repeat_interval_sec, "다음 회차까지"):
                break

        self.after(0, lambda: self.status_var.set(f"완료 — 성공 {ok} / 실패 {fail}"))
        self.after(0, lambda: self.progress_var.set(100))
        self.after(
            0,
            lambda: self._trigger_log_sync_upload(
                note=f"배치 종료 성공{ok}/실패{fail}",
                silent=True,
            ),
        )
        return f"════ 결과: 성공 {ok} / 실패 {fail} / 전체 {len(jobs)} ════"

    def _on_batch_auto(self) -> None:
        if self.discoverer.is_running():
            messagebox.showwarning(
                "안내",
                "게시판 수집이 진행 중입니다.\n수집을 중지한 뒤 배치 작성을 실행해 주세요.\n"
                "(동시 실행 시 브라우저 엔진 충돌로 전체 실패할 수 있습니다.)",
            )
            return
        self._sync_urls_pick_list(preserve_selection=True)
        fail_skip_n = self._count_fail_skipped_urls()
        jobs = self._validate_jobs()
        if not jobs:
            return
        if fail_skip_n:
            self._log(
                f"[자동제외] 실패 {FAIL_SKIP_THRESHOLD}회 이상 URL {fail_skip_n}건 — 배치에서 제외됨",
                "info",
            )
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
            return self._execute_job(job, auto_submit=False)

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
        if self._log_sync_after_id:
            try:
                self.after_cancel(self._log_sync_after_id)
            except Exception:
                pass
            self._log_sync_after_id = None
        self._save_app_state()
        self.writer.cancel()
        self._close_all_writers()
        self.destroy()


def main() -> None:
    from app_paths import migrate_legacy_data

    bootstrap_before_tk()
    migrate_legacy_data()

    if is_frozen():
        splash = UpdateSplash()
        try:
            try_startup_update(on_status=splash.set_status)
        finally:
            splash.close()

    if is_frozen() and not playwright_browsers_ready():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("브라우저 없음", playwright_browsers_error_message())
        root.destroy()
        return

    BacklinkApp().mainloop()


if __name__ == "__main__":
    main()
