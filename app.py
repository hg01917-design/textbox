from __future__ import annotations

import json
import os
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

DATA_DIR = Path(__file__).parent / "data"

def _claude_env() -> dict:
    """ANTHROPIC_API_KEY 제거 — .env의 API 키가 OAuth 구독 대신 사용되는 것 방지."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env

from config import (
    BLOG_PROFILES,
    add_wp_account,
    get_blog_profile,
    load_env,
    read_env_values,
    remove_wp_account,
    save_env_values,
    wp_blog_ids,
    wp_credentials,
)
import content.affiliate as affiliate
from content.generator import generate_draft_with_sources
from content.official_url import find_official_urls
from content.prompting import ensure_prompt_files, prompt_for_blog_type, prompt_names, prompt_path
from content.public_sources import fetch_public_source_context
from content.quality import check_draft
from content.source import fetch_sources, format_sources_for_prompt
from keywords.analyzer import analyze_keyword
from keywords.ideas import keyword_ideas
from media.cards import generate_card_images
import publisher.accounts as accounts
import publisher.adsense as adsense
import publisher.stats as stats
from publisher.wordpress import create_post
from publisher.tistory import post_tistory
from publisher.naver import post_naver
from storage.drafts import save_draft
from storage.history import count_today_publishes, record_publish


class TabState:
    """네이버글쓰기/티스토리·워드프레스 탭이 각자 가지는 위젯·변수·초안 상태 묶음.

    탭마다 독립된 '현재 초안'을 유지하기 위해, 예전에는 self.* 에 직접
    두던 것들을 여기 담고 공용 생성/발행 메서드들이 이 객체를 받아서 동작한다."""

    def __init__(self, name: str):
        self.name = name
        self.keyword_var: tk.StringVar | None = None
        self.blog_type_var: tk.StringVar | None = None
        self.source_var: tk.StringVar | None = None
        self.prompt_var: tk.StringVar | None = None
        self.provider_var: tk.StringVar | None = None
        self.card_var: tk.BooleanVar | None = None
        self.affiliate_var: tk.BooleanVar | None = None
        self.public_context_text: tk.Text | None = None
        self.preview: tk.Text | None = None
        self.log_text: tk.Text | None = None
        self.generate_btn: ttk.Button | None = None
        self.idea_btn: ttk.Button | None = None
        self.idea_list: tk.Listbox | None = None
        self.target_var: tk.StringVar | None = None
        self.target_combo: ttk.Combobox | None = None
        self.draft_label_var = tk.StringVar(value="현재 초안: 없음")
        self.current_payload: dict | None = None
        self.current_paths: dict | None = None
        self.sub_frames: dict[str, ttk.Frame] = {}


class BlogDrafterApp(tk.Tk):
    _PUBLISHER_DISPATCH = {
        "WordPress": "_wp_worker",
        "Tistory": "_tistory_worker",
        "Naver": "_naver_worker",
    }
    _PLATFORM_LABEL = {"WordPress": "WordPress", "Tistory": "Tistory", "Naver": "네이버"}
    _PROVIDER_OPTIONS = {
        "Claude 기본 + 실패 시 Codex": "auto",
        "Codex": "codex",
        "Claude": "cli",
        "OpenAI": "openai",
        "Template": "template",
    }

    def __init__(self):
        super().__init__()
        load_env()
        ensure_prompt_files()
        self.title("textbox v2.8")
        self.geometry("1150x780")
        self._build_ui()

    def _build_ui(self):
        wp_ids = [x.strip() for x in os.environ.get("WP_BLOG_IDS", "").split(",") if x.strip()]
        tistory_ids = [x.strip() for x in os.environ.get("TISTORY_BLOG_IDS", "").split(",") if x.strip()]
        naver_ids = [x.strip() for x in os.environ.get("NAVER_BLOG_IDS", "").split(",") if x.strip()]

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(top, textvariable=self.status_var).pack(side="left", padx=6)
        ttk.Button(top, text="설정", command=self.open_settings).pack(side="right")

        self.wp_id_var = tk.StringVar(value=wp_ids[0] if wp_ids else "")
        self.tistory_id_var = tk.StringVar(value=tistory_ids[0] if tistory_ids else "")
        self.naver_id_var = tk.StringVar(value=naver_ids[0] if naver_ids else "")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._NAV_BG = "#e4e4e4"
        self._NAV_FG = "#1a1a1a"
        self._NAV_ACCENT = "#0a66c2"

        sidebar = ttk.Frame(body, width=220)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ttk.Label(sidebar, text="textbox", font=("", 18, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", padx=6, pady=(0, 12))

        container = ttk.Frame(body)
        container.pack(side="left", fill="both", expand=True, padx=(10, 0))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._sections: dict[str, ttk.Frame] = {}
        self._nav_buttons: dict[str, tuple[tk.Frame, tk.Label]] = {}
        sections = [
            ("home", "홈"),
            ("keyword", "키워드분석기"),
            ("naver", "네이버글쓰기"),
            ("tistory_wp", "티스토리/워드프레스"),
            ("public_data", "공공데이터"),
            ("affiliate", "제휴상품"),
        ]
        for name, label in sections:
            frame = ttk.Frame(container)
            frame.grid(row=0, column=0, sticky="nsew")
            self._sections[name] = frame

            # 사이드바 nav 항목: macOS aqua에서는 tk.Button조차 배경색을 무시해서
            # 선택 표시를 '배경색 반전'으로 하면 글씨가 안 보이는 문제가 생긴다.
            # 대신 왼쪽 강조 바(accent) + 글자색으로 선택 상태를 표시한다 (Label/Frame은
            # 네이티브 컨트롤이 아니라서 bg/fg가 항상 제대로 반영됨).
            row = tk.Frame(sidebar, bg=self._NAV_BG)
            row.pack(fill="x", padx=8, pady=4)
            accent = tk.Frame(row, bg=self._NAV_BG, width=5)
            accent.pack(side="left", fill="y")
            lbl = tk.Label(
                row, text=label, font=("", 17, "bold"), anchor="w", justify="left",
                bg=self._NAV_BG, fg=self._NAV_FG, padx=14, pady=18, wraplength=185,
                cursor="pointinghand",
            )
            lbl.pack(side="left", fill="both", expand=True)
            for widget in (row, accent, lbl):
                widget.bind("<Button-1>", lambda e, n=name: self._show_section(n))
            self._nav_buttons[name] = (accent, lbl)

        self.naver_state = TabState("naver")
        self.tw_state = TabState("tistory_wp")

        self._pub_all_items: list = []
        self._pub_filtered: list = []
        self._pub_selected: tuple | None = None

        self._aff_results: list = []
        self._aff_selected: dict | None = None

        self._build_home_tab(self._sections["home"])
        self._build_keyword_tab(self._sections["keyword"])
        self._build_naver_tab(self._sections["naver"], naver_ids)
        self._build_tistory_wp_tab(self._sections["tistory_wp"], wp_ids, tistory_ids)
        self._build_public_data_tab(self._sections["public_data"])
        self._build_affiliate_tab(self._sections["affiliate"])

        self._show_section("home")

    def _show_section(self, name: str):
        self._sections[name].tkraise()
        for section_name, (accent, lbl) in self._nav_buttons.items():
            selected = section_name == name
            accent.configure(bg=self._NAV_ACCENT if selected else self._NAV_BG)
            lbl.configure(fg=self._NAV_ACCENT if selected else self._NAV_FG)
        if name == "home":
            self._populate_home_table()
        if name == "public_data" and not self._pub_all_items:
            self._load_pub_data()

    # ─── 홈 대시보드 ─────────────────────────────────────────────────

    _HOME_BG = "#e9eef5"
    _CARD_BG = "#ffffff"

    def _build_home_tab(self, parent):
        page = tk.Frame(parent, bg=self._HOME_BG)
        page.pack(fill="both", expand=True)

        header = tk.Frame(page, bg=self._HOME_BG, padx=20, pady=20)
        header.pack(fill="x")
        tk.Label(header, text="오늘의 현황", font=("", 17, "bold"), bg=self._HOME_BG, fg="#1a1f29").pack(side="left")
        ttk.Button(header, text="계정 관리", command=self._open_account_manager).pack(side="right")

        cards = tk.Frame(page, bg=self._HOME_BG, padx=14)
        cards.pack(fill="x")
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        self._home_today_var = tk.StringVar(value="-")
        self._home_adsense_var = tk.StringVar(value="연동 필요")

        self._build_stat_card(cards, "오늘 발행한 수", self._home_today_var, column=0)
        self._build_stat_card(cards, "애드센스 수익 (오늘 추정)", self._home_adsense_var, column=1)

        actions = tk.Frame(page, bg=self._HOME_BG, padx=20)
        actions.pack(fill="x", pady=(4, 0))
        self._home_refresh_btn = ttk.Button(actions, text="조회수·수익 새로고침", command=self._refresh_home_stats)
        self._home_refresh_btn.pack(side="left")
        self._home_adsense_connect_btn = ttk.Button(actions, text="애드센스 연동", command=self._connect_adsense)
        self._home_adsense_connect_btn.pack(side="left", padx=(8, 0))

        self._home_status_var = tk.StringVar(value="")
        tk.Label(page, textvariable=self._home_status_var, fg="gray", bg=self._HOME_BG).pack(anchor="w", padx=20, pady=(6, 0))

        table_wrap = tk.Frame(page, bg=self._HOME_BG, padx=20, pady=12)
        table_wrap.pack(fill="both", expand=True)
        columns = ("platform", "blog_id", "today", "views", "status")
        self._home_tree = ttk.Treeview(table_wrap, columns=columns, show="headings", height=10)
        headings = {"platform": "플랫폼", "blog_id": "계정", "today": "오늘 발행", "views": "조회수", "status": "상태"}
        widths = {"platform": 90, "blog_id": 140, "today": 90, "views": 90, "status": 320}
        for col in columns:
            self._home_tree.heading(col, text=headings[col])
            self._home_tree.column(col, width=widths[col], anchor="w")
        self._home_tree.pack(fill="both", expand=True)
        self._home_tree_items: dict[tuple[str, str], str] = {}

    def _build_stat_card(self, parent, label: str, var: tk.StringVar, column: int):
        card = tk.Frame(parent, bg=self._CARD_BG, padx=18, pady=16, highlightbackground="#d6dce5", highlightthickness=1)
        card.grid(row=0, column=column, sticky="nsew", padx=6, pady=6)
        tk.Label(card, text=label, font=("", 11), fg="#5a6472", bg=self._CARD_BG).pack(anchor="w")
        tk.Label(card, textvariable=var, font=("", 24, "bold"), bg=self._CARD_BG, fg="#1a1f29").pack(anchor="w", pady=(6, 0))

    def _home_accounts(self) -> list[tuple[str, str]]:
        rows = [("네이버", blog_id) for blog_id in accounts.naver_blog_ids()]
        rows += [("티스토리", blog_id) for blog_id in accounts.tistory_blog_ids()]
        rows += [("워드프레스", blog_id) for blog_id in wp_blog_ids()]
        return rows

    def _populate_home_table(self):
        """탭 전환 시마다 호출되는 가벼운 갱신 — 오늘 발행 수만 채우고 Chrome/API 호출은 하지 않는다."""
        self._home_today_var.set(f"{count_today_publishes()}건")
        if adsense.is_connected():
            if self._home_adsense_var.get() == "연동 필요":
                self._home_adsense_var.set("새로고침 필요")
        else:
            self._home_adsense_var.set("연동 필요")

        self._home_tree.delete(*self._home_tree.get_children())
        self._home_tree_items = {}
        platform_key = {"네이버": "Naver", "티스토리": "Tistory", "워드프레스": "WordPress"}
        for platform, blog_id in self._home_accounts():
            today_count = count_today_publishes(platform_key[platform], blog_id)
            item = self._home_tree.insert(
                "", "end", values=(platform, blog_id, f"{today_count}건", "-", "새로고침 필요")
            )
            self._home_tree_items[(platform, blog_id)] = item

    def _refresh_home_stats(self):
        """조회수·애드센스 새로고침 버튼 — 계정별 Chrome 실행 + 스크래핑이 있어 시간이 걸린다."""
        self._populate_home_table()
        self._home_refresh_btn.config(state="disabled")
        threading.Thread(target=self._refresh_home_stats_worker, daemon=True).start()

    def _refresh_home_stats_worker(self):
        accounts_list = self._home_accounts()
        for idx, (platform, blog_id) in enumerate(accounts_list, start=1):
            self.after(0, lambda p=platform, b=blog_id, i=idx, n=len(accounts_list): (
                self._home_status_var.set(f"조회수 확인 중... [{i}/{n}] {p} {b}"),
                self._home_tree.set(self._home_tree_items[(p, b)], "status", "확인 중..."),
            ))
            if platform == "네이버":
                result = stats.naver_today_views(blog_id)
            elif platform == "티스토리":
                result = stats.tistory_today_views(blog_id)
            else:
                creds = wp_credentials(blog_id)
                if not all(creds.values()):
                    result = {"ok": False, "error": "워드프레스 자격증명이 없습니다 — 계정 관리에서 등록하세요."}
                else:
                    result = stats.wp_today_views(**creds)
            self.after(0, self._apply_home_stat_result, platform, blog_id, result)

        if adsense.is_connected():
            earnings = adsense.today_earnings()
            self.after(0, self._apply_home_adsense_result, earnings)
        else:
            self.after(0, lambda: self._home_adsense_var.set("연동 필요"))

        self.after(0, lambda: (
            self._home_status_var.set("새로고침 완료"),
            self._home_refresh_btn.config(state="normal"),
        ))

    def _apply_home_stat_result(self, platform: str, blog_id: str, result: dict):
        item = self._home_tree_items.get((platform, blog_id))
        if not item:
            return
        if result.get("ok"):
            self._home_tree.set(item, "views", f"{result.get('views', 0)}회")
            self._home_tree.set(item, "status", "정상")
        else:
            self._home_tree.set(item, "views", "-")
            self._home_tree.set(item, "status", result.get("error", "오류"))

    def _apply_home_adsense_result(self, result: dict):
        if result.get("ok"):
            self._home_adsense_var.set(f"${result.get('amount', '0')}")
        else:
            self._home_adsense_var.set("조회 실패")
            self._home_status_var.set(f"애드센스 오류: {result.get('error', '')}")

    def _connect_adsense(self):
        self._home_adsense_connect_btn.config(state="disabled")
        self._home_status_var.set("브라우저에서 구글 로그인·동의를 완료해주세요...")

        def on_done(ok: bool, message: str):
            self.after(0, lambda: self._home_adsense_connect_btn.config(state="normal"))
            if ok:
                self.after(0, lambda: self._home_status_var.set("애드센스 연동 완료"))
                self.after(0, self._refresh_home_stats)
            else:
                self.after(0, lambda: self._home_status_var.set(f"애드센스 연동 실패: {message}"))

        adsense.start_oauth_flow(on_done=on_done)

    def _open_account_manager(self):
        AccountManagerWindow(self)

    def _show_sub(self, state: TabState, key: str):
        state.sub_frames[key].tkraise()

    def _build_sub_nav(self, parent, state: TabState) -> tuple[ttk.Frame, ttk.Frame]:
        """탭 안에서 '초안작성'/'발행'을 선택하는 2단계 서브탭을 만든다.
        compose_frame(초안작성)과 publish_frame(발행) 두 프레임을 반환한다."""
        subnav = ttk.Frame(parent, padding=(4, 4))
        subnav.pack(fill="x")
        ttk.Button(subnav, text="① 초안작성", command=lambda: self._show_sub(state, "compose")).pack(side="left", padx=(0, 4))
        ttk.Button(subnav, text="② 발행", command=lambda: self._show_sub(state, "publish")).pack(side="left")

        sub_container = ttk.Frame(parent)
        sub_container.pack(fill="both", expand=True)
        sub_container.grid_rowconfigure(0, weight=1)
        sub_container.grid_columnconfigure(0, weight=1)

        compose_frame = ttk.Frame(sub_container)
        compose_frame.grid(row=0, column=0, sticky="nsew")
        publish_frame = ttk.Frame(sub_container)
        publish_frame.grid(row=0, column=0, sticky="nsew")

        state.sub_frames = {"compose": compose_frame, "publish": publish_frame}
        return compose_frame, publish_frame

    # ─── 키워드분석기 탭 ─────────────────────────────────────────────

    def _build_keyword_tab(self, parent):
        control = ttk.Frame(parent, padding=(4, 4))
        control.pack(fill="x")

        ttk.Label(control, text="키워드").grid(row=0, column=0, sticky="w")
        self.kw_analyzer_var = tk.StringVar(value="경기도 청년 지원금 2026")
        ttk.Entry(control, textvariable=self.kw_analyzer_var, width=40).grid(row=0, column=1, sticky="we", padx=6)

        ttk.Label(control, text="블로그 유형 (경쟁도 기준)").grid(row=0, column=2, sticky="w")
        self.kw_analyzer_blog_type_var = tk.StringVar(value="정부지원")
        ttk.Combobox(
            control, textvariable=self.kw_analyzer_blog_type_var,
            values=list(BLOG_PROFILES), width=12, state="readonly",
        ).grid(row=0, column=3, sticky="w", padx=6)

        self.keyword_btn = ttk.Button(control, text="키워드 후보 찾기", command=self.find_keyword_candidates)
        self.keyword_btn.grid(row=0, column=4, padx=6)
        control.columnconfigure(1, weight=1)

        ttk.Label(parent, text="키워드 후보 (더블클릭하면 선택됩니다)").pack(anchor="w", padx=4)
        self.keyword_list = tk.Listbox(parent, height=20)
        self.keyword_list.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        self.keyword_list.bind("<Double-Button-1>", self.use_selected_keyword)
        ttk.Button(parent, text="선택한 키워드 사용", command=self.use_selected_keyword).pack(anchor="e", padx=4, pady=(0, 10))

    def find_keyword_candidates(self):
        keyword = self.kw_analyzer_var.get().strip()
        if not keyword:
            messagebox.showwarning("입력 필요", "큰 주제나 대략적인 키워드를 입력하세요. 예: 청년 지원금")
            return
        self.keyword_btn.config(state="disabled")
        self.keyword_list.delete(0, tk.END)
        self._set_status("키워드 후보 찾는 중...")
        threading.Thread(target=self._keyword_worker, args=(keyword,), daemon=True).start()

    def _keyword_worker(self, keyword: str):
        try:
            blog_type = self.kw_analyzer_blog_type_var.get().strip() or "일반"
            profile = get_blog_profile(blog_type)
            self._set_status(f"키워드 후보 분석 중: {keyword}")
            analysis = analyze_keyword(keyword, max_competition=profile["max_competition"], limit=15)
            rows = analysis.get("candidates", [])
            self.after(0, self._show_keyword_candidates, rows)
        except Exception as exc:
            self._set_status(f"키워드 후보 오류: {exc}")
        finally:
            self.after(0, lambda: self.keyword_btn.config(state="normal"))

    def _show_keyword_candidates(self, rows: list[dict]):
        self.keyword_list.delete(0, tk.END)
        if not rows:
            self.keyword_list.insert(tk.END, "후보를 찾지 못했습니다")
            self._set_status("키워드 후보 없음")
            return
        for row in rows:
            competition = row.get("competition", -1)
            volume = row.get("volume", 0)
            difficulty = row.get("difficulty", "unknown")
            marker = "추천" if row.get("recommended") else "참고"
            self.keyword_list.insert(
                tk.END,
                f"{row['keyword']} | {marker} | 검색량 {volume} | 경쟁 {competition} | {difficulty}",
            )
        self._set_status("키워드 후보 완료: 더블클릭하면 선택됩니다")

    def use_selected_keyword(self, event=None):
        selection = self.keyword_list.curselection()
        if not selection:
            return
        value = self.keyword_list.get(selection[0])
        keyword = value.split(" | ", 1)[0].strip()
        if keyword and keyword != "후보를 찾지 못했습니다":
            self.kw_analyzer_var.set(keyword)
            self._set_status(f"선택한 키워드: {keyword}")

    # ─── 공용 컴포즈 패널 (네이버글쓰기 / 티스토리·워드프레스 공용) ──────────

    def _build_compose_panel(self, parent, state: TabState, default_blog_type: str = "정부지원"):
        control = ttk.Frame(parent, padding=(4, 4))
        control.pack(fill="x")

        ttk.Label(control, text="키워드").grid(row=0, column=0, sticky="w")
        state.keyword_var = tk.StringVar(value="")
        ttk.Entry(control, textvariable=state.keyword_var, width=40).grid(row=0, column=1, sticky="we", padx=6)

        ttk.Label(control, text="블로그 유형").grid(row=0, column=2, sticky="w")
        state.blog_type_var = tk.StringVar(value=default_blog_type)
        blog_type_combo = ttk.Combobox(
            control, textvariable=state.blog_type_var, values=list(BLOG_PROFILES), width=12, state="readonly",
        )
        blog_type_combo.grid(row=0, column=3, sticky="w", padx=6)
        blog_type_combo.bind("<<ComboboxSelected>>", lambda e, s=state: self._on_blog_type_change(s))

        state.generate_btn = ttk.Button(control, text="초안 생성", command=lambda s=state: self.generate_draft(s))
        state.generate_btn.grid(row=0, column=4, padx=6)

        ttk.Label(control, text="source-url / 공공API").grid(row=1, column=0, sticky="w", pady=6)
        state.source_var = tk.StringVar(value="")
        ttk.Entry(control, textvariable=state.source_var, width=60).grid(row=1, column=1, columnspan=3, sticky="we", padx=6, pady=6)

        ttk.Label(control, text="프롬프트").grid(row=2, column=0, sticky="w")
        state.prompt_var = tk.StringVar(value=prompt_for_blog_type(state.blog_type_var.get()))
        ttk.Combobox(
            control, textvariable=state.prompt_var, values=prompt_names(), width=20, state="readonly",
        ).grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(control, text="생성 모델").grid(row=2, column=2, sticky="e", padx=(8, 0))
        state.provider_var = tk.StringVar(value="Claude 기본 + 실패 시 Codex")
        ttk.Combobox(
            control,
            textvariable=state.provider_var,
            values=list(self._PROVIDER_OPTIONS),
            width=22,
            state="readonly",
        ).grid(row=2, column=3, sticky="w", padx=6)

        state.card_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(control, text="카드 이미지 자동 생성", variable=state.card_var).grid(row=3, column=1, sticky="w", padx=6)
        state.affiliate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            control, text="관련 상품 링크 자동 첨부(쿠팡/MRT)", variable=state.affiliate_var,
        ).grid(row=3, column=2, columnspan=2, sticky="w", padx=6)
        ttk.Button(control, text="프롬프트 열기", command=lambda s=state: self.open_prompt_file(s)).grid(row=2, column=4, padx=6)

        idea_frame = ttk.LabelFrame(control, text="키워드가 생각 안 날 때", padding=6)
        idea_frame.grid(row=4, column=0, columnspan=5, sticky="we", pady=(8, 0))
        state.idea_btn = ttk.Button(idea_frame, text="키워드 추천", command=lambda s=state: self.suggest_keywords(s))
        state.idea_btn.pack(side="left", padx=(0, 6))
        state.idea_list = tk.Listbox(idea_frame, height=4, exportselection=False)
        state.idea_list.pack(side="left", fill="x", expand=True, padx=(0, 6))
        state.idea_list.bind("<Double-Button-1>", lambda e, s=state: self.use_suggested_keyword(s, e))
        ttk.Button(idea_frame, text="선택 사용", command=lambda s=state: self.use_suggested_keyword(s)).pack(side="left")

        ttk.Label(control, text="공공데이터 요약 (선택사항 — 공공데이터 탭의 '가져가기'로 자동 입력됨)").grid(
            row=5, column=0, columnspan=5, sticky="w", pady=(8, 0)
        )
        state.public_context_text = tk.Text(control, height=3, wrap="word")
        state.public_context_text.grid(row=6, column=0, columnspan=5, sticky="we", pady=(0, 4))

        ttk.Label(control, textvariable=state.draft_label_var, font=("", 10, "bold")).grid(
            row=7, column=0, columnspan=5, sticky="w", pady=(4, 0)
        )

        control.columnconfigure(1, weight=1)

        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        ttk.Label(left, text="로그 / 결과").pack(anchor="w")
        state.log_text = tk.Text(left, height=18, wrap="word")
        state.log_text.pack(fill="both", expand=True)

        ttk.Label(right, text="초안 미리보기").pack(anchor="w")
        state.preview = tk.Text(right, wrap="word")
        state.preview.pack(fill="both", expand=True)

    def _on_blog_type_change(self, state: TabState, event=None):
        state.prompt_var.set(prompt_for_blog_type(state.blog_type_var.get()))

    def open_prompt_file(self, state: TabState):
        path = prompt_path(state.prompt_var.get())
        subprocess.run(["open", str(path)], check=False)

    def _on_target_change(self, state: TabState, event=None):
        target = self._parse_target(state.target_var.get())
        if not target:
            return
        platform, blog_id = target
        id_var = {"WordPress": self.wp_id_var, "Tistory": self.tistory_id_var, "Naver": self.naver_id_var}.get(platform)
        if id_var is not None:
            id_var.set(blog_id)
        fallback = (state.blog_type_var.get().strip() if state.blog_type_var else "") or "일반"
        inferred = self._infer_blog_type(platform, blog_id, fallback)
        if inferred and state.blog_type_var is not None:
            state.blog_type_var.set(inferred)
            state.prompt_var.set(prompt_for_blog_type(inferred))

    # ─── 초안 생성 (네이버글쓰기 / 티스토리·워드프레스 공용) ────────────────

    def generate_draft(self, state: TabState):
        keyword = state.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("입력 필요", "키워드를 입력하세요.")
            return
        target = self._parse_target(state.target_var.get())
        if not target:
            messagebox.showwarning("작성 대상 필요", "초안을 만들 블로그를 먼저 선택하세요.")
            return
        blog_type = state.blog_type_var.get().strip() or "일반"
        target_context = self._target_context(target, blog_type)
        source_urls = [u.strip() for u in state.source_var.get().split(",") if u.strip()]
        prompt_name = state.prompt_var.get()
        provider_label = state.provider_var.get() if state.provider_var else "Claude 기본 + 실패 시 Codex"
        provider = self._PROVIDER_OPTIONS.get(provider_label, "auto")
        use_cards = state.card_var.get()
        use_affiliate = state.affiliate_var.get()
        extra_context = state.public_context_text.get("1.0", tk.END).strip()
        state.generate_btn.config(state="disabled")
        state.preview.delete("1.0", tk.END)
        state.log_text.delete("1.0", tk.END)
        self._set_status("초안 생성 중...")
        threading.Thread(
            target=self._generate_worker,
            args=(state, keyword, blog_type, target, target_context, source_urls, prompt_name, provider, provider_label, use_cards, use_affiliate, extra_context),
            daemon=True,
        ).start()

    def _generate_worker(
        self,
        state: TabState,
        keyword: str,
        blog_type: str,
        target: tuple[str, str],
        target_context: str,
        source_urls: list[str],
        prompt_name: str,
        provider: str,
        provider_label: str,
        use_cards: bool,
        use_affiliate: bool,
        extra_context: str,
    ):
        try:
            profile = get_blog_profile(blog_type)
            self._log(state, f"작성 대상: {target_context}")
            self._log(state, f"[1/6] 키워드 분석: {keyword}")
            analysis = analyze_keyword(keyword, max_competition=profile["max_competition"], limit=8)
            best_keyword = analysis.get("best_keyword") or keyword
            related = [row["keyword"] for row in analysis.get("candidates", []) if row["keyword"] != best_keyword]

            sources = []
            source_context = ""
            auto_source_urls = find_official_urls(best_keyword, blog_type, on_log=lambda m: self._log(state, m))
            source_urls = _merge_urls(source_urls, auto_source_urls)
            if source_urls:
                self._log(state, f"[2/6] 공식 URL 읽기: {len(source_urls)}개")
                sources = fetch_sources(source_urls)
                source_context = format_sources_for_prompt(sources)
            public_context = fetch_public_source_context(best_keyword, blog_type, on_log=lambda m: self._log(state, m))
            if public_context:
                self._log(state, "[2/6] 공공API 자료 보강 완료")
                source_context = "\n\n".join(part for part in (source_context, public_context) if part)
                sources.append({"url": "public-api:data.go.kr", "ok": True, "error": "", "text": public_context})
            if extra_context:
                self._log(state, "[2/6] 공공데이터 탭에서 가져온 요약 반영")
                source_context = "\n\n".join(part for part in (source_context, extra_context) if part)
                sources.append({"url": "manual:공공데이터-요약", "ok": True, "error": "", "text": extra_context})

            self._log(state, f"[3/6] {provider_label} 초안 생성: {best_keyword}")
            draft = generate_draft_with_sources(
                best_keyword,
                blog_type,
                related,
                provider=provider,
                source_context=source_context,
                prompt_name=prompt_name,
                target_context=target_context,
            )
            if use_affiliate and draft.get("body"):
                self._log(state, "[3/6] 관련 상품 링크 검색 중 (쿠팡/MRT)")
                new_body = affiliate.attach_affiliate_block(blog_type, best_keyword, draft["body"])
                if new_body != draft["body"]:
                    draft["body"] = new_body
                    self._log(state, "[3/6] 관련 상품 링크 첨부 완료")
                else:
                    self._log(state, "[3/6] 관련 상품 검색 결과 없음 — 링크 미첨부")
            images = []
            if use_cards:
                self._log(state, "[4/6] 카드 이미지 생성")
                images = generate_card_images(best_keyword, blog_type, on_log=lambda m: self._log(state, m))
            self._log(state, "[5/6] 품질 검사")
            quality = check_draft(draft, best_keyword, min_chars=1200)
            self._log(state, f"Provider: {draft.get('provider')} / publishable: {draft.get('publishable')}")
            if draft.get("generation_error"):
                self._log(state, f"생성 경고: {draft.get('generation_error')}")
            payload = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "keyword": best_keyword,
                "seed_keyword": keyword,
                "blog_type": blog_type,
                "target": {"platform": target[0], "blog_id": target[1]} if target else {},
                "target_context": target_context,
                "analysis": analysis,
                "draft": draft,
                "quality": quality,
                "source_urls": source_urls,
                "sources": sources,
                "images": images,
            }
            self._log(state, "[6/6] 파일 저장")
            paths = save_draft(payload)
            state.current_payload = payload
            state.current_paths = paths
            self.after(0, self._show_payload, state, payload, paths)
        except Exception as exc:
            self._log(state, f"오류: {exc}")
            self._set_status("오류 발생")
        finally:
            self.after(0, lambda: state.generate_btn.config(state="normal"))

    def _show_payload(self, state: TabState, payload: dict, paths: dict):
        draft = payload["draft"]
        quality = payload["quality"]
        state.preview.delete("1.0", tk.END)
        state.preview.insert(tk.END, f"# {draft['title']}\n\n")
        if not self._is_payload_target(payload, "Naver"):
            state.preview.insert(tk.END, f"메타설명: {draft['meta_description']}\n\n")
        state.preview.insert(tk.END, f"태그: {', '.join(draft['tags'])}\n\n")
        if payload.get("target_context"):
            state.preview.insert(tk.END, f"작성 대상: {payload['target_context']}\n\n")
        images = payload.get("images", [])
        if images:
            state.preview.insert(tk.END, "카드 이미지:\n" + "\n".join(f"- {path}" for path in images) + "\n\n")
        state.preview.insert(tk.END, draft["body"])
        state.draft_label_var.set(f"현재 초안: {draft['title']}")
        self._log(state, f"Markdown: {paths['markdown']}")
        self._log(state, f"JSON: {paths['json']}")
        self._log(state, f"Quality: {'PASS' if quality['passed'] else 'WARN'} {quality['warnings']}")
        self._set_status(f"완료: {'PASS' if quality['passed'] else 'WARN'}")

    def suggest_keywords(self, state: TabState):
        blog_type = state.blog_type_var.get().strip() if state.blog_type_var else "일반"
        if not state.idea_btn or not state.idea_list:
            return
        state.idea_btn.config(state="disabled")
        state.idea_list.delete(0, tk.END)
        state.idea_list.insert(tk.END, "추천 키워드 불러오는 중...")
        self._set_status(f"{blog_type} 키워드 추천 중...")
        threading.Thread(target=self._suggest_keywords_worker, args=(state, blog_type), daemon=True).start()

    def _suggest_keywords_worker(self, state: TabState, blog_type: str):
        try:
            ideas = keyword_ideas(blog_type, limit=12)
            self.after(0, self._show_keyword_ideas, state, ideas)
        except Exception as exc:
            self.after(0, self._show_keyword_ideas, state, [f"추천 실패: {exc}"])
        finally:
            if state.idea_btn:
                self.after(0, lambda: state.idea_btn.config(state="normal"))

    def _show_keyword_ideas(self, state: TabState, ideas: list[str]):
        if not state.idea_list:
            return
        state.idea_list.delete(0, tk.END)
        if not ideas:
            state.idea_list.insert(tk.END, "추천 키워드 없음")
            self._set_status("추천 키워드 없음")
            return
        for idea in ideas:
            state.idea_list.insert(tk.END, idea)
        self._set_status("추천 키워드 완료: 더블클릭하면 입력됩니다")

    def use_suggested_keyword(self, state: TabState, event=None):
        if not state.idea_list or not state.keyword_var:
            return
        selection = state.idea_list.curselection()
        if not selection:
            return
        keyword = state.idea_list.get(selection[0]).strip()
        if not keyword or keyword.startswith("추천 "):
            return
        state.keyword_var.set(keyword)
        self._set_status(f"추천 키워드 선택: {keyword}")

    # ─── 네이버글쓰기 탭 ─────────────────────────────────────────────

    def _build_naver_tab(self, parent, naver_ids: list[str]):
        target_row = ttk.Frame(parent, padding=(4, 4))
        target_row.pack(fill="x")
        ttk.Label(target_row, text="작성 대상 (네이버)").pack(side="left")
        naver_targets = self._build_target_options([], [], naver_ids)
        self.naver_state.target_var = tk.StringVar(value=naver_targets[0] if naver_targets else "")
        self.naver_state.target_combo = ttk.Combobox(
            target_row, textvariable=self.naver_state.target_var, values=naver_targets, width=28, state="readonly",
        )
        self.naver_state.target_combo.pack(side="left", padx=6)
        self.naver_state.target_combo.bind("<<ComboboxSelected>>", lambda e: self._on_target_change(self.naver_state))
        ttk.Label(target_row, text="(초안 생성 전에 반드시 선택)", foreground="gray").pack(side="left", padx=(6, 0))

        compose_frame, publish_frame = self._build_sub_nav(parent, self.naver_state)

        self._build_compose_panel(compose_frame, self.naver_state, default_blog_type="정부지원")

        ttk.Label(publish_frame, textvariable=self.naver_state.draft_label_var, font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(12, 8)
        )
        self.naver_combo = self._build_publish_footer(
            publish_frame, platform="Naver", id_var=self.naver_id_var, ids=naver_ids,
            save_cb=lambda: self.save_platform_draft(self.naver_state, "Naver", self.naver_id_var),
            publish_cb=lambda: self.publish_platform(self.naver_state, "Naver", self.naver_id_var),
            save_label="네이버 임시저장", publish_label="네이버 발행",
        )

        self._show_sub(self.naver_state, "compose")

    # ─── 티스토리/워드프레스 탭 ──────────────────────────────────────

    def _build_tistory_wp_tab(self, parent, wp_ids: list[str], tistory_ids: list[str]):
        target_row = ttk.Frame(parent, padding=(4, 4))
        target_row.pack(fill="x")
        ttk.Label(target_row, text="작성 대상 (Tistory/WordPress)").pack(side="left")
        tw_targets = self._build_target_options(wp_ids, tistory_ids, [])
        self.tw_state.target_var = tk.StringVar(value=tw_targets[0] if tw_targets else "")
        self.tw_state.target_combo = ttk.Combobox(
            target_row, textvariable=self.tw_state.target_var, values=tw_targets, width=28, state="readonly",
        )
        self.tw_state.target_combo.pack(side="left", padx=6)
        self.tw_state.target_combo.bind("<<ComboboxSelected>>", lambda e: self._on_target_change(self.tw_state))
        ttk.Label(target_row, text="(초안 생성 전에 반드시 선택)", foreground="gray").pack(side="left", padx=(6, 0))

        compose_frame, publish_frame = self._build_sub_nav(parent, self.tw_state)

        self._build_compose_panel(compose_frame, self.tw_state, default_blog_type="정부지원")

        ttk.Label(publish_frame, textvariable=self.tw_state.draft_label_var, font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(12, 8)
        )
        self.tistory_combo = self._build_publish_footer(
            publish_frame, platform="Tistory", id_var=self.tistory_id_var, ids=tistory_ids,
            save_cb=lambda: self.save_platform_draft(self.tw_state, "Tistory", self.tistory_id_var),
            publish_cb=lambda: self.publish_platform(self.tw_state, "Tistory", self.tistory_id_var),
            save_label="Tistory 임시저장", publish_label="Tistory 발행",
        )
        self.wp_combo = self._build_publish_footer(
            publish_frame, platform="WordPress", id_var=self.wp_id_var, ids=wp_ids,
            save_cb=lambda: self.save_platform_draft(self.tw_state, "WordPress", self.wp_id_var),
            publish_cb=lambda: self.publish_platform(self.tw_state, "WordPress", self.wp_id_var),
            save_label="WordPress 임시저장", publish_label="WordPress 발행",
        )

        self._show_sub(self.tw_state, "compose")

    # ─── 공용 발행 푸터 ──────────────────────────────────────────────

    def _build_publish_footer(
        self, parent, *, platform: str, id_var: tk.StringVar, ids: list[str],
        save_cb, publish_cb, save_label: str, publish_label: str,
    ) -> ttk.Combobox:
        lf = ttk.LabelFrame(parent, text=platform, padding=8)
        lf.pack(fill="x", padx=8, pady=6)
        row = ttk.Frame(lf)
        row.pack(fill="x")
        ttk.Label(row, text="블로그 ID").pack(side="left")
        # WordPress만 readonly, Tistory/Naver는 자유 입력 — 기존 동작 유지
        combo = ttk.Combobox(
            row, textvariable=id_var, values=ids, width=16,
            state="readonly" if platform == "WordPress" else "normal",
        )
        combo.pack(side="left", padx=6)
        ttk.Button(row, text=save_label, command=save_cb).pack(side="left", padx=4)
        ttk.Button(row, text=publish_label, command=publish_cb).pack(side="left", padx=4)
        return combo

    # ─── 저장/발행 액션 (플랫폼 공용) ────────────────────────────────

    def save_platform_draft(self, state: TabState, platform: str, id_var: tk.StringVar):
        self._publish_or_save(state, platform, id_var, status="draft")

    def publish_platform(self, state: TabState, platform: str, id_var: tk.StringVar):
        label = self._PLATFORM_LABEL[platform]
        self._log(state, f"[{label}] 발행 버튼 클릭")
        self._set_status(f"{label} 발행 준비 중...")
        if not self._require_draft(state, "발행"):
            self._log(state, f"[{label}] 발행 중단: 초안/품질 확인 실패")
            return
        if platform == "Naver" and not self._confirm_naver_images(state):
            self._log(state, f"[{label}] 발행 중단: 이미지 부족 확인에서 취소")
            return
        blog_id = id_var.get().strip()
        if not blog_id:
            self._show_warning("ID 필요", f"{label} 블로그 ID를 입력하세요.")
            self._log(state, f"[{label}] 발행 중단: 블로그 ID 없음")
            return
        self._log(state, f"[{label}] 발행 확인 대기: {blog_id}")
        if not self._ask_yes_no(
            "발행 확인", f"[{blog_id}]\n'{state.current_payload['draft']['title']}'\n\n{label}에 발행하시겠습니까?"
        ):
            self._log(state, f"[{label}] 발행 중단: 사용자가 발행 확인 취소")
            return
        if not self._confirm_target_match(state, platform, blog_id):
            self._log(state, f"[{label}] 발행 중단: 작성 대상 불일치 확인에서 취소")
            return
        draft = state.current_payload["draft"]
        self._set_status(f"{label} 발행 중...")
        self._log(state, f"[{label}] 발행 작업 시작: {blog_id}")
        threading.Thread(
            target=getattr(self, self._PUBLISHER_DISPATCH[platform]),
            args=(state, blog_id, draft, "publish"), daemon=True,
        ).start()

    def _publish_or_save(self, state: TabState, platform: str, id_var: tk.StringVar, status: str):
        label = self._PLATFORM_LABEL[platform]
        action_label = "임시저장" if status == "draft" else "발행"
        self._log(state, f"[{label}] {action_label} 버튼 클릭")
        self._set_status(f"{label} {action_label} 준비 중...")
        if not self._require_draft(state, action_label):
            self._log(state, f"[{label}] {action_label} 중단: 초안/품질 확인 실패")
            return
        if platform == "Naver" and not self._confirm_naver_images(state):
            self._log(state, f"[{label}] {action_label} 중단: 이미지 부족 확인에서 취소")
            return
        blog_id = id_var.get().strip()
        if not blog_id:
            self._show_warning("ID 필요", f"{label} 블로그 ID를 입력하세요.")
            self._log(state, f"[{label}] {action_label} 중단: 블로그 ID 없음")
            return
        if not self._confirm_target_match(state, platform, blog_id):
            self._log(state, f"[{label}] {action_label} 중단: 작성 대상 불일치 확인에서 취소")
            return
        draft = state.current_payload["draft"]
        self._set_status(f"{label} {action_label} 중...")
        self._log(state, f"[{label}] {action_label} 작업 시작: {blog_id}")
        threading.Thread(
            target=getattr(self, self._PUBLISHER_DISPATCH[platform]),
            args=(state, blog_id, draft, status), daemon=True,
        ).start()

    def _wp_worker(self, state: TabState, blog_id: str, draft: dict, status: str):
        kwargs = wp_credentials(blog_id) if blog_id else {}
        blog_type = state.current_payload.get("blog_type", "") if state.current_payload else ""
        image_paths = affiliate.with_disclosure_banner(blog_type, state.current_payload.get("images", []))
        result = create_post(
            title=draft["title"],
            content_markdown=draft["body"],
            tags=draft.get("tags", []),
            status=status,
            image_paths=image_paths,
            **kwargs,
        )
        label = "발행" if status == "publish" else "임시저장"
        if result.get("ok"):
            self._log(state, f"WordPress {label} 완료: {result.get('link')} / 이미지 {result.get('media_count', 0)}장")
            self._set_status(f"WordPress {label} 완료")
            if status == "publish":
                record_publish("WordPress", blog_id, draft["title"])
        else:
            self._log(state, f"WordPress 오류: {result.get('error')}")
            self._set_status("WordPress 오류")

    def _tistory_worker(self, state: TabState, blog_id: str, draft: dict, status: str):
        blog_type = state.current_payload.get("blog_type", "") if state.current_payload else ""
        image_paths = affiliate.with_disclosure_banner(blog_type, state.current_payload.get("images", []))
        result = post_tistory(
            blog_id=blog_id,
            title=draft["title"],
            content_markdown=draft["body"],
            tags=draft.get("tags", []),
            image_paths=image_paths,
            status=status,
            category=state.current_payload.get("blog_type", ""),
        )
        label = "발행" if status == "publish" else "임시저장"
        if result.get("ok"):
            self._log(state, f"Tistory {label} 완료: {result.get('url')}")
            self._set_status(f"Tistory {label} 완료")
            if status == "publish":
                record_publish("Tistory", blog_id, draft["title"])
        else:
            self._log(state, f"Tistory 오류: {result.get('error')}")
            self._set_status("Tistory 오류")

    def _naver_worker(self, state: TabState, blog_id: str, draft: dict, status: str):
        blog_type = state.current_payload.get("blog_type", "") if state.current_payload else ""
        template_name = affiliate.NAVER_TEMPLATE_MAP.get(blog_type, "")
        result = post_naver(
            blog_id=blog_id,
            title=draft["title"],
            content_markdown=draft["body"],
            tags=draft.get("tags", []),
            image_paths=state.current_payload.get("images", []),
            status=status,
            on_log=lambda m: self._log(state, m),
            template_name=template_name,
        )
        label = "발행" if status == "publish" else "임시저장"
        if result.get("ok"):
            self._log(state, f"네이버 {label} 완료: {result.get('url')}")
            self._set_status(f"네이버 {label} 완료")
            if status == "publish":
                record_publish("Naver", blog_id, draft["title"])
        else:
            self._log(state, f"네이버 오류: {result.get('error')}")
            self._set_status("네이버 오류")

    def _require_draft(self, state: TabState, label: str) -> bool:
        if not state.current_payload:
            self._show_warning("초안 없음", "먼저 초안을 생성하세요.")
            self._set_status("초안 없음: 먼저 초안을 생성하세요")
            return False
        if not self._require_publishable(state):
            return False
        quality = state.current_payload["quality"]
        if not quality["passed"]:
            return self._ask_yes_no("품질 경고", f"품질 검사를 통과하지 못했습니다. 그래도 {label}할까요?")
        return True

    def _require_publishable(self, state: TabState) -> bool:
        draft = state.current_payload.get("draft", {}) if state.current_payload else {}
        if draft.get("provider") == "template" or draft.get("publishable") is False:
            self._show_error(
                "발행 차단",
                "Claude/OpenAI 생성에 실패해서 템플릿 초안이 만들어졌습니다.\n"
                "이 초안은 발행할 수 없습니다.\n\n"
                f"오류: {draft.get('generation_error') or '생성 실패 원인 없음'}",
            )
            self._log(state, "발행 차단: template fallback 초안")
            return False
        return True

    def _confirm_naver_images(self, state: TabState) -> bool:
        images = state.current_payload.get("images", []) if state.current_payload else []
        blog_type = state.current_payload.get("blog_type", "") if state.current_payload else ""
        recommended = 5 if blog_type == "여행" else 3
        if len(images) >= recommended:
            return True
        return self._ask_yes_no(
            "이미지 부족",
            f"네이버 글은 이미지가 부족하면 품질이 떨어질 수 있습니다.\n"
            f"현재 이미지: {len(images)}장 / 권장: {recommended}장\n\n"
            "그래도 진행할까요?",
        )

    def _confirm_target_match(self, state: TabState, platform: str, blog_id: str) -> bool:
        target = state.current_payload.get("target", {}) if state.current_payload else {}
        if not target:
            return True
        if target.get("platform") == platform and target.get("blog_id") == blog_id:
            return True
        return self._ask_yes_no(
            "작성 대상 불일치",
            "이 초안은 다른 블로그 대상으로 생성되었습니다.\n\n"
            f"초안 대상: {target.get('platform')}:{target.get('blog_id')}\n"
            f"현재 발행: {platform}:{blog_id}\n\n"
            "그래도 진행할까요?",
        )

    def _is_payload_target(self, payload: dict, platform: str) -> bool:
        target = payload.get("target", {}) if payload else {}
        return target.get("platform") == platform

    # ─── 공공데이터 탭 (정보요약 + 핸드오프 전용) ────────────────────

    def _build_public_data_tab(self, parent):
        # 검색 바
        search_frame = ttk.Frame(parent, padding=(4, 4))
        search_frame.pack(fill="x")

        ttk.Label(search_frame, text="검색:").pack(side="left")
        self._pub_search_var = tk.StringVar()
        self._pub_search_var.trace_add("write", lambda *_: self._filter_pub_list())
        ttk.Entry(search_frame, textvariable=self._pub_search_var, width=36).pack(side="left", padx=4)

        self._pub_src_var = tk.StringVar(value="전체")
        for label in ("전체", "정부24", "복지로"):
            ttk.Radiobutton(
                search_frame, text=label, variable=self._pub_src_var,
                value=label, command=self._filter_pub_list,
            ).pack(side="left", padx=2)

        self._pub_count_var = tk.StringVar(value="로딩 중...")
        ttk.Label(search_frame, textvariable=self._pub_count_var, foreground="gray").pack(side="left", padx=10)
        ttk.Button(search_frame, text="새로고침", command=self._reload_pub_data).pack(side="right")

        # 좌우 분할
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=4, pady=4)

        # ── 왼쪽: 목록
        list_frame = ttk.Frame(paned)
        paned.add(list_frame, weight=2)

        self._pub_listbox = tk.Listbox(list_frame, selectmode="single", activestyle="dotbox")
        scroll_y = ttk.Scrollbar(list_frame, orient="vertical", command=self._pub_listbox.yview)
        self._pub_listbox.config(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self._pub_listbox.pack(fill="both", expand=True)
        self._pub_listbox.bind("<<ListboxSelect>>", self._on_pub_select)

        # ── 오른쪽: 상세 + 요약
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=3)

        vpaned = ttk.PanedWindow(right_frame, orient="vertical")
        vpaned.pack(fill="both", expand=True)

        # 상세 패널
        detail_lf = ttk.LabelFrame(vpaned, text="선택된 항목 상세")
        vpaned.add(detail_lf, weight=1)
        self._pub_detail = tk.Text(detail_lf, height=10, wrap="word", state="disabled", background="#f8f8f8")
        det_scroll = ttk.Scrollbar(detail_lf, orient="vertical", command=self._pub_detail.yview)
        self._pub_detail.config(yscrollcommand=det_scroll.set)
        det_scroll.pack(side="right", fill="y")
        self._pub_detail.pack(fill="both", expand=True)

        # 요약 패널
        summary_lf = ttk.LabelFrame(vpaned, text="정보 요약")
        vpaned.add(summary_lf, weight=1)

        btn_row = ttk.Frame(summary_lf)
        btn_row.pack(fill="x", padx=4, pady=(4, 2))
        self._pub_summarize_btn = ttk.Button(btn_row, text="요약 생성", command=self._pub_summarize)
        self._pub_summarize_btn.pack(side="left")

        self._pub_summary_text = tk.Text(summary_lf, height=6, wrap="word")
        self._pub_summary_text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        send_row = ttk.Frame(summary_lf)
        send_row.pack(fill="x", padx=4, pady=(0, 6))
        ttk.Button(
            send_row, text="네이버글쓰기로 가져가기", command=lambda: self._pub_send_to("naver"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            send_row, text="티스토리·워드프레스로 가져가기", command=lambda: self._pub_send_to("tistory_wp"),
        ).pack(side="left")

    def _load_pub_data(self):
        threading.Thread(target=self._load_pub_data_worker, daemon=True).start()

    def _reload_pub_data(self):
        self._pub_all_items = []
        self._pub_count_var.set("로딩 중...")
        self._pub_listbox.delete(0, tk.END)
        self._load_pub_data()

    def _load_pub_data_worker(self):
        items: list[tuple[str, str, dict]] = []

        gov24_path = DATA_DIR / "gov24_all.json"
        if gov24_path.exists():
            try:
                data = json.loads(gov24_path.read_text(encoding="utf-8"))
                for item in data:
                    name = item.get("서비스명", "").strip()
                    if name:
                        items.append((name, "정부24", item))
            except Exception:
                pass

        bokjiro_path = DATA_DIR / "bokjiro_all.json"
        if bokjiro_path.exists():
            try:
                data = json.loads(bokjiro_path.read_text(encoding="utf-8"))
                for item in data:
                    name = item.get("servNm", "").strip()
                    if name:
                        items.append((name, "복지로", item))
            except Exception:
                pass

        self._pub_all_items = items
        self.after(0, self._filter_pub_list)

    def _filter_pub_list(self, *_):
        search = self._pub_search_var.get().strip().lower()
        src_filter = self._pub_src_var.get()

        filtered = []
        for name, source, item in self._pub_all_items:
            if src_filter != "전체" and source != src_filter:
                continue
            if search:
                haystack = (
                    name
                    + item.get("서비스목적요약", "") + item.get("지원대상", "")
                    + item.get("소관기관명", "") + item.get("servDgst", "")
                    + item.get("trgterIndvdlArray", "") + item.get("lifeArray", "")
                    + item.get("intrsThemaArray", "") + item.get("jurMnofNm", "")
                ).lower()
                if search not in haystack:
                    continue
            filtered.append((name, source, item))

        self._pub_filtered = filtered
        self._pub_listbox.delete(0, tk.END)
        for name, source, _ in filtered:
            self._pub_listbox.insert(tk.END, f"[{source}] {name}")
        self._pub_count_var.set(f"{len(filtered):,}건")

    def _on_pub_select(self, event=None):
        sel = self._pub_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._pub_filtered):
            return
        name, source, item = self._pub_filtered[idx]
        self._pub_selected = (name, source, item)

        detail = self._format_pub_detail(name, source, item)
        self._pub_detail.config(state="normal")
        self._pub_detail.delete("1.0", tk.END)
        self._pub_detail.insert("1.0", detail)
        self._pub_detail.config(state="disabled")

        self._pub_summary_text.delete("1.0", tk.END)

    def _format_pub_detail(self, name: str, source: str, item: dict) -> str:
        lines = [f"■ {name}  [{source}]\n"]
        if source == "정부24":
            fields = [
                ("소관기관", "소관기관명"),
                ("지원대상", "지원대상"),
                ("지원내용", "지원내용"),
                ("지원금액", "지원금액내용"),
                ("신청방법", "신청방법내용"),
                ("신청기간", "신청기간내용"),
                ("구비서류", "구비서류내용"),
                ("접수기관", "접수기관내용"),
                ("요약",     "서비스목적요약"),
                ("상세URL",  "상세조회URL"),
            ]
        else:
            fields = [
                ("소관기관", "jurMnofNm"),
                ("지원대상", "trgterIndvdlArray"),
                ("생애주기", "lifeArray"),
                ("주제",     "intrsThemaArray"),
                ("급여유형", "srvPvsnNm"),
                ("지급주기", "sprtCycNm"),
                ("요약",     "servDgst"),
                ("상세URL",  "servDtlLink"),
            ]
        for label, key in fields:
            value = str(item.get(key, "") or "").strip()
            if value:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def _pub_summarize(self):
        if not self._pub_selected:
            messagebox.showwarning("항목 선택 필요", "왼쪽 목록에서 항목을 먼저 선택하세요.")
            return
        self._pub_summarize_btn.config(state="disabled")
        self._pub_summary_text.delete("1.0", tk.END)
        self._pub_summary_text.insert(tk.END, "요약 생성 중...")
        self._set_status("공공데이터 요약 생성 중...")
        threading.Thread(target=self._pub_summarize_worker, args=(self._pub_selected,), daemon=True).start()

    def _pub_summarize_worker(self, selected: tuple):
        try:
            name, source, item = selected
            detail = self._format_pub_detail(name, source, item)
            prompt = f"""아래 공공서비스 정보를 3~5문장으로 자연스럽게 요약해줘.
블로그 글의 출처 자료로 쓸 것이므로 핵심 사실(지원대상, 지원내용, 신청방법 등)을 빠짐없이 담되 문장은 간결하게 써줘.

{detail}

주의: 요약 문장만 출력. 제목, 설명, 인사말 금지."""
            result = subprocess.run(
                ["claude", "--print", "--dangerously-skip-permissions"],
                input=prompt, capture_output=True, text=True, timeout=90,
                env=_claude_env(),
            )
            output = (result.stdout or "").strip()
            if not output:
                raise ValueError("Claude CLI 응답이 비어 있습니다. claude --print 가 실행 가능한지 확인하세요.")
            self.after(0, self._show_pub_summary, output)
            self._set_status("요약 생성 완료")
        except Exception as exc:
            self.after(0, self._show_pub_summary, f"[오류] {exc}")
            self._set_status("요약 생성 오류")
        finally:
            self.after(0, lambda: self._pub_summarize_btn.config(state="normal"))

    def _show_pub_summary(self, text: str):
        self._pub_summary_text.delete("1.0", tk.END)
        self._pub_summary_text.insert("1.0", text)

    def _pub_send_to(self, target_name: str):
        if not self._pub_selected:
            messagebox.showwarning("항목 선택 필요", "왼쪽 목록에서 항목을 먼저 선택하세요.")
            return
        name, source, item = self._pub_selected
        summary = self._pub_summary_text.get("1.0", tk.END).strip()
        state = self.naver_state if target_name == "naver" else self.tw_state
        state.keyword_var.set(name)
        state.public_context_text.delete("1.0", tk.END)
        state.public_context_text.insert("1.0", summary or self._format_pub_detail(name, source, item))
        self._show_section(target_name)
        label = "네이버글쓰기" if target_name == "naver" else "티스토리/워드프레스"
        self._set_status(f"공공데이터 요약을 {label} 탭으로 가져왔습니다. 초안 생성 버튼을 눌러주세요.")

    # ─── 제휴상품 탭 (쿠팡파트너스/마이리얼트립 검색 + 핸드오프) ────────────

    def _build_affiliate_tab(self, parent):
        key_status = []
        if not affiliate.keys_configured("coupang"):
            key_status.append("쿠팡파트너스 API 키 없음")
        if not affiliate.keys_configured("mrt"):
            key_status.append("마이리얼트립 API 키 없음")
        if key_status:
            ttk.Label(
                parent, text=" / ".join(key_status) + " — .env를 확인하세요.",
                foreground="#b00020",
            ).pack(anchor="w", padx=8, pady=(6, 0))

        search_frame = ttk.Frame(parent, padding=(4, 4))
        search_frame.pack(fill="x")

        ttk.Label(search_frame, text="검색 키워드:").pack(side="left")
        self._aff_search_var = tk.StringVar()
        aff_entry = ttk.Entry(search_frame, textvariable=self._aff_search_var, width=28)
        aff_entry.pack(side="left", padx=4)
        aff_entry.bind("<Return>", lambda e: self._aff_search())

        self._aff_source_var = tk.StringVar(value="쿠팡파트너스")
        for label in ("쿠팡파트너스", "마이리얼트립"):
            ttk.Radiobutton(
                search_frame, text=label, variable=self._aff_source_var, value=label,
            ).pack(side="left", padx=2)

        self._aff_search_btn = ttk.Button(search_frame, text="검색", command=self._aff_search)
        self._aff_search_btn.pack(side="left", padx=6)

        self._aff_count_var = tk.StringVar(value="")
        ttk.Label(search_frame, textvariable=self._aff_count_var, foreground="gray").pack(side="left", padx=10)

        # 인기 여행지 바로가기 — 키워드가 안 떠오를 때 클릭 한 번으로 검색
        quick_frame = ttk.Frame(parent, padding=(4, 0))
        quick_frame.pack(fill="x")
        ttk.Label(quick_frame, text="인기 여행지:").pack(side="left")
        for dest in ("다낭", "오사카", "방콕", "세부", "후쿠오카", "타이베이", "괌", "다낭 나트랑"):
            ttk.Button(
                quick_frame, text=dest, width=8,
                command=lambda d=dest: self._aff_quick_search(d),
            ).pack(side="left", padx=2, pady=4)

        # 좌우 분할
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=4, pady=4)

        # ── 왼쪽: 검색 결과 목록
        list_frame = ttk.Frame(paned)
        paned.add(list_frame, weight=2)

        self._aff_listbox = tk.Listbox(list_frame, selectmode="single", activestyle="dotbox")
        scroll_y = ttk.Scrollbar(list_frame, orient="vertical", command=self._aff_listbox.yview)
        self._aff_listbox.config(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self._aff_listbox.pack(fill="both", expand=True)
        self._aff_listbox.bind("<<ListboxSelect>>", self._on_aff_select)

        # ── 오른쪽: 상세 + 가져가기
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=3)

        detail_lf = ttk.LabelFrame(right_frame, text="선택된 상품 상세")
        detail_lf.pack(fill="both", expand=True)
        self._aff_detail = tk.Text(detail_lf, height=10, wrap="word", state="disabled", background="#f8f8f8")
        det_scroll = ttk.Scrollbar(detail_lf, orient="vertical", command=self._aff_detail.yview)
        self._aff_detail.config(yscrollcommand=det_scroll.set)
        det_scroll.pack(side="right", fill="y")
        self._aff_detail.pack(fill="both", expand=True, padx=4, pady=4)

        send_row = ttk.Frame(right_frame)
        send_row.pack(fill="x", padx=4, pady=(0, 6))
        ttk.Button(
            send_row, text="네이버글쓰기로 가져가기", command=lambda: self._aff_send_to("naver"),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            send_row, text="티스토리·워드프레스로 가져가기", command=lambda: self._aff_send_to("tistory_wp"),
        ).pack(side="left")

    def _aff_quick_search(self, destination: str):
        self._aff_source_var.set("마이리얼트립")
        self._aff_search_var.set(destination)
        self._aff_search()

    def _aff_search(self):
        keyword = self._aff_search_var.get().strip()
        if not keyword:
            messagebox.showwarning("입력 필요", "검색 키워드를 입력하세요.")
            return
        source_label = self._aff_source_var.get()
        source = "coupang" if source_label == "쿠팡파트너스" else "mrt"
        if not affiliate.keys_configured(source):
            messagebox.showwarning("API 키 없음", f"{source_label} API 키가 .env에 설정되어 있지 않습니다.")
            return

        self._aff_search_btn.config(state="disabled")
        self._aff_count_var.set("검색 중...")
        self._aff_listbox.delete(0, tk.END)
        self._aff_results = []
        self._set_status(f"{source_label} 상품 검색 중...")
        threading.Thread(target=self._aff_search_worker, args=(source, source_label, keyword), daemon=True).start()

    def _aff_search_worker(self, source: str, source_label: str, keyword: str):
        try:
            results = affiliate.search_products_for_tab(source, keyword, limit=8)
        except Exception as exc:
            results = []
            self._set_status(f"{source_label} 검색 오류: {exc}")
        self.after(0, self._show_aff_results, results, source_label)

    def _show_aff_results(self, results: list[dict], source_label: str):
        self._aff_results = results
        self._aff_listbox.delete(0, tk.END)
        for p in results:
            price = f" — {p['price']}" if p.get("price") else ""
            self._aff_listbox.insert(tk.END, f"{p['name']}{price}")
        self._aff_count_var.set(f"{len(results):,}건")
        self._aff_search_btn.config(state="normal")
        self._set_status(f"{source_label} 검색 완료 ({len(results)}건)" if results else f"{source_label} 검색 결과 없음")

    def _on_aff_select(self, event=None):
        sel = self._aff_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._aff_results):
            return
        product = self._aff_results[idx]
        self._aff_selected = product

        detail = (
            f"■ {product.get('name', '')}\n\n"
            f"가격: {product.get('price', '') or '-'}\n"
            f"제휴 링크: {product.get('url', '')}\n"
        )
        self._aff_detail.config(state="normal")
        self._aff_detail.delete("1.0", tk.END)
        self._aff_detail.insert("1.0", detail)
        self._aff_detail.config(state="disabled")

    def _aff_send_to(self, target_name: str):
        if not self._aff_selected:
            messagebox.showwarning("상품 선택 필요", "왼쪽 목록에서 상품을 먼저 선택하세요.")
            return
        product = self._aff_selected
        state = self.naver_state if target_name == "naver" else self.tw_state
        state.keyword_var.set(affiliate.clean_product_name(product.get("name", "")))
        state.public_context_text.delete("1.0", tk.END)
        state.public_context_text.insert("1.0", affiliate.format_product_context(product))
        self._show_section(target_name)
        label = "네이버글쓰기" if target_name == "naver" else "티스토리/워드프레스"
        self._set_status(f"제휴 상품 정보를 {label} 탭으로 가져왔습니다. 초안 생성 버튼을 눌러주세요.")

    # ─── 공용 헬퍼 ───────────────────────────────────────────────────

    def _build_target_options(self, wp_ids: list[str], tistory_ids: list[str], naver_ids: list[str]) -> list[str]:
        options = []
        options.extend(f"WordPress:{blog_id}" for blog_id in wp_ids)
        options.extend(f"Tistory:{blog_id}" for blog_id in tistory_ids)
        options.extend(f"Naver:{blog_id}" for blog_id in naver_ids)
        return options

    def _parse_target(self, value: str) -> tuple[str, str] | None:
        if ":" not in value:
            return None
        platform, blog_id = value.split(":", 1)
        platform = platform.strip()
        blog_id = blog_id.strip()
        if not platform or not blog_id:
            return None
        return platform, blog_id

    def _target_context(self, target: tuple[str, str] | None, blog_type: str) -> str:
        if not target:
            return "작성 대상 블로그 미지정"
        platform, blog_id = target
        return f"플랫폼={platform}, 블로그ID={blog_id}, 블로그유형={blog_type}"

    def _infer_blog_type(self, platform: str, blog_id: str, fallback: str = "일반") -> str:
        key = f"BLOG_TYPE_{blog_id.upper().replace('-', '_')}"
        configured = os.environ.get(key, "").strip()
        if configured in BLOG_PROFILES:
            return configured
        name = blog_id.lower()
        if any(token in name for token in ("trip", "travel", "nolja", "여행")):
            return "여행"
        if any(token in name for token in ("salim", "life", "생활")):
            return "생활정보"
        if any(token in name for token in ("it", "goodisak", "tech")):
            return "IT"
        if any(token in name for token in ("baremi", "welfare", "gov", "support")):
            return "정부지원"
        return fallback or "일반"

    def open_settings(self):
        SettingsWindow(self)

    def _bring_dialog_front(self):
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _ask_yes_no(self, title: str, message: str) -> bool:
        self._bring_dialog_front()
        return messagebox.askyesno(title, message, parent=self)

    def _show_warning(self, title: str, message: str):
        self._bring_dialog_front()
        return messagebox.showwarning(title, message, parent=self)

    def _show_error(self, title: str, message: str):
        self._bring_dialog_front()
        return messagebox.showerror(title, message, parent=self)

    def _log(self, state: TabState, message: str):
        try:
            with open("/tmp/blog-helper.log", "a", encoding="utf-8") as log_file:
                log_file.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
        except Exception:
            pass
        self.after(0, lambda: (state.log_text.insert(tk.END, message + "\n"), state.log_text.see(tk.END)))

    def _set_status(self, message: str):
        self.after(0, lambda: self.status_var.set(message))

    def _sync_blog_id_widgets(self):
        """계정 추가/삭제 후 메인 창의 블로그 ID 콤보박스들을 .env 최신 값으로 동기화."""
        try:
            wp_ids = wp_blog_ids()
            if wp_ids:
                self.wp_combo["values"] = wp_ids
                if self.wp_id_var.get() not in wp_ids:
                    self.wp_id_var.set(wp_ids[0])
            tistory_ids = accounts.tistory_blog_ids()
            if tistory_ids:
                self.tistory_combo["values"] = tistory_ids
                if self.tistory_id_var.get() not in tistory_ids:
                    self.tistory_id_var.set(tistory_ids[0])
            naver_ids = accounts.naver_blog_ids()
            if naver_ids:
                self.naver_combo["values"] = naver_ids
                if self.naver_id_var.get() not in naver_ids:
                    self.naver_id_var.set(naver_ids[0])

            naver_targets = self._build_target_options([], [], naver_ids)
            if naver_targets:
                self.naver_state.target_combo["values"] = naver_targets
                if self.naver_state.target_var.get() not in naver_targets:
                    self.naver_state.target_var.set(naver_targets[0])
                    self._on_target_change(self.naver_state)

            tw_targets = self._build_target_options(wp_ids, tistory_ids, [])
            if tw_targets:
                self.tw_state.target_combo["values"] = tw_targets
                if self.tw_state.target_var.get() not in tw_targets:
                    self.tw_state.target_var.set(tw_targets[0])
                    self._on_target_change(self.tw_state)
        except Exception:
            pass
        self._populate_home_table()


def _merge_urls(primary: list[str], secondary: list[str]) -> list[str]:
    merged = []
    for url in primary + secondary:
        if url and url not in merged:
            merged.append(url)
    return merged


class AccountManagerWindow(tk.Toplevel):
    """네이버·티스토리·워드프레스 계정을 추가/삭제하는 창.

    네이버·티스토리는 CHROME_PORT(기본 9222) 하나를 공유하고, 로그인 화면에서
    Chrome에 저장된 계정을 선택해 전환한다. 비밀번호 직접 입력은
    LOGIN_PASSWORD_FALLBACK=1일 때만 사용한다.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("계정 관리")
        self.geometry("640x640")
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self._naver_frame = ttk.Frame(notebook, padding=10)
        self._tistory_frame = ttk.Frame(notebook, padding=10)
        self._wp_frame = ttk.Frame(notebook, padding=10)
        notebook.add(self._naver_frame, text="네이버")
        notebook.add(self._tistory_frame, text="티스토리")
        notebook.add(self._wp_frame, text="워드프레스")

        self._build_chrome_platform_tab(
            self._naver_frame, "naver", accounts.naver_blog_ids,
            accounts.add_naver_account, accounts.remove_naver_account,
        )
        self._build_chrome_platform_tab(
            self._tistory_frame, "tistory", accounts.tistory_blog_ids,
            accounts.add_tistory_account, accounts.remove_tistory_account,
        )
        self._build_wp_tab(self._wp_frame)

    # ─── 네이버 / 티스토리 (Chrome 포트 기반) ──────────────────────────

    def _build_chrome_platform_tab(self, parent, platform: str, list_fn, add_fn, remove_fn):
        label = "네이버" if platform == "naver" else "티스토리"
        columns = ("blog_id", "port", "hint")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=8)
        tree.heading("blog_id", text="블로그 아이디")
        tree.heading("port", text="Chrome 포트")
        tree.heading("hint", text="로그인 계정(참고용)")
        tree.column("blog_id", width=140)
        tree.column("port", width=90)
        tree.column("hint", width=220)
        tree.pack(fill="both", expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for blog_id in list_fn():
                port_fn = accounts.naver_port if platform == "naver" else accounts.tistory_port
                hint = accounts.login_hint(platform, blog_id)
                tree.insert("", "end", values=(blog_id, port_fn(blog_id), hint))

        refresh()

        form = ttk.Frame(parent, padding=(0, 8))
        form.pack(fill="x")
        row1 = ttk.Frame(form)
        row1.pack(fill="x")
        ttk.Label(row1, text=f"{label} 블로그 아이디").pack(side="left")
        id_var = tk.StringVar()
        ttk.Entry(row1, textvariable=id_var, width=20).pack(side="left", padx=6)
        isolated_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1, text="별도 계정으로 등록", variable=isolated_var).pack(side="left", padx=(0, 6))

        row2 = ttk.Frame(form, padding=(0, 4))
        row2.pack(fill="x")
        ttk.Label(row2, text="로그인 시 선택할 저장된 계정 ID").pack(side="left")
        hint_var = tk.StringVar()
        ttk.Entry(row2, textvariable=hint_var, width=24).pack(side="left", padx=6)

        def do_add():
            blog_id = id_var.get().strip()
            if not blog_id:
                return
            isolated = isolated_var.get()
            hint_text = hint_var.get().strip()
            port = add_fn(blog_id, isolated=isolated, login_hint_text=hint_text)
            id_var.set("")
            isolated_var.set(False)
            hint_var.set("")
            refresh()
            self.master._sync_blog_id_widgets()
            if isolated:
                messagebox.showinfo(
                    "계정 추가됨",
                    f"{label} 계정 '{blog_id}'을(를) 추가했습니다.\n"
                    f"발행 시 공유 Chrome(포트 {port})의 저장 계정 목록에서 '{accounts.login_account_id(platform, blog_id)}'을(를) 선택합니다.",
                )
            else:
                messagebox.showinfo(
                    "계정 추가됨",
                    f"{label} 계정 '{blog_id}'을(를) 추가했습니다. 공유 Chrome(포트 {port})에서 저장된 계정을 선택해 로그인합니다.",
                )

        ttk.Button(form, text="추가", command=do_add).pack(side="left", padx=(0, 12))

        def do_open_chrome():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("선택 필요", "목록에서 계정을 먼저 선택하세요.")
                return
            blog_id = tree.item(selection[0], "values")[0]
            port_fn = accounts.naver_port if platform == "naver" else accounts.tistory_port
            port = port_fn(blog_id)

            def launch():
                accounts.ensure_chrome(port)

            threading.Thread(target=launch, daemon=True).start()
            messagebox.showinfo("Chrome 실행", f"공유 Chrome 포트 {port}를 여는 중입니다. 저장 계정이 없으면 이 창에서 최초 1회 로그인해주세요.")

        def do_remove():
            selection = tree.selection()
            if not selection:
                return
            blog_id = tree.item(selection[0], "values")[0]
            if not messagebox.askyesno("계정 삭제", f"'{blog_id}' 계정을 목록에서 삭제할까요?"):
                return
            remove_fn(blog_id)
            refresh()
            self.master._sync_blog_id_widgets()

        ttk.Button(form, text="Chrome 열기", command=do_open_chrome).pack(side="left")
        ttk.Button(form, text="삭제", command=do_remove).pack(side="left", padx=(6, 0))

        ttk.Label(
            parent,
            text="모든 네이버·티스토리 계정은 공유 Chrome(기본 포트 9222) 하나를 사용합니다.\n"
                 "발행 시 저장된 계정 ID를 찾아 클릭하고, 작업 후 로그아웃한 뒤 다음 계정으로 넘어갑니다.\n"
                 "비밀번호 직접 입력은 설정의 LOGIN_PASSWORD_FALLBACK 값을 1로 켠 경우에만 시도합니다.",
            foreground="gray", justify="left",
        ).pack(anchor="w", pady=(6, 0))

    # ─── 워드프레스 (REST API 자격증명 기반) ───────────────────────────

    def _build_wp_tab(self, parent):
        columns = ("blog_id", "site_url", "user")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=8)
        tree.heading("blog_id", text="표시 이름")
        tree.heading("site_url", text="사이트 URL")
        tree.heading("user", text="사용자")
        tree.column("blog_id", width=110)
        tree.column("site_url", width=220)
        tree.column("user", width=160)
        tree.pack(fill="both", expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for blog_id in wp_blog_ids():
                creds = wp_credentials(blog_id)
                tree.insert("", "end", values=(blog_id, creds.get("site_url", ""), creds.get("user", "")))

        refresh()

        form = ttk.Frame(parent, padding=(0, 8))
        form.pack(fill="x")
        labels = ["표시 이름", "사이트 URL", "사용자(이메일)", "앱 비밀번호"]
        vars_ = [tk.StringVar() for _ in labels]
        for idx, text in enumerate(labels):
            ttk.Label(form, text=text).grid(row=idx, column=0, sticky="w", pady=3)
            show = "*" if text == "앱 비밀번호" else ""
            ttk.Entry(form, textvariable=vars_[idx], width=40, show=show).grid(row=idx, column=1, sticky="we", pady=3)
        form.columnconfigure(1, weight=1)

        def do_add():
            blog_id, site_url, user, app_password = (v.get().strip() for v in vars_)
            if not (blog_id and site_url and user and app_password):
                messagebox.showwarning("입력 필요", "표시 이름/사이트 URL/사용자/앱 비밀번호를 모두 입력하세요.")
                return
            add_wp_account(blog_id, site_url, user, app_password)
            for v in vars_:
                v.set("")
            refresh()
            self.master._sync_blog_id_widgets()
            messagebox.showinfo("계정 추가됨", f"워드프레스 계정 '{blog_id}'을(를) 추가했습니다.")

        def do_remove():
            selection = tree.selection()
            if not selection:
                return
            blog_id = tree.item(selection[0], "values")[0]
            if not messagebox.askyesno("계정 삭제", f"'{blog_id}' 계정을 목록에서 삭제할까요?"):
                return
            remove_wp_account(blog_id)
            refresh()
            self.master._sync_blog_id_widgets()

        btn_row = ttk.Frame(parent, padding=(0, 4))
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="추가", command=do_add).pack(side="left")
        ttk.Button(btn_row, text="삭제", command=do_remove).pack(side="left", padx=(6, 0))

        ttk.Label(
            parent,
            text="앱 비밀번호는 워드프레스 관리자 > 사용자 > 프로필의 '응용 프로그램 비밀번호'에서 발급합니다.\n"
                 "Jetpack 통계가 켜져 있으면 조회수도 자동으로 가져옵니다.",
            foreground="gray", justify="left",
        ).pack(anchor="w", pady=(8, 0))


class SettingsWindow(tk.Toplevel):
    FIELDS = [
        "WP_BLOG_IDS",
        "WP_SITE_URL",
        "WP_USER",
        "WP_APP_PASSWORD",
        "WP_DEFAULT_STATUS",
        "BAREMI542_WP_URL",
        "BAREMI542_WP_USER",
        "BAREMI542_WP_APP_PASSWORD",
        "TISTORY_BLOG_IDS",
        "NAVER_BLOG_IDS",
        "CHROME_PORT",
        "CHROME_USER_DATA_DIR",
        "LOGIN_SELECT_SAVED_ACCOUNT",
        "LOGIN_PASSWORD_FALLBACK",
        "LOGIN_LOGOUT_AFTER_POST",
        "NAVER_SEARCH_CLIENT_ID",
        "NAVER_SEARCH_CLIENT_SECRET",
        "NAVER_API_KEY",
        "NAVER_SECRET_KEY",
        "NAVER_CUSTOMER_ID",
        "PUBLIC_DATA_API_KEY",
        "BOKJIRO_API_KEY",
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("설정 (.env)")
        self.geometry("620x700")
        values = read_env_values()
        self.vars = {}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        DEFAULTS = {
            "WP_DEFAULT_STATUS": "draft",
            "CHROME_PORT": "9222",
            "LOGIN_SELECT_SAVED_ACCOUNT": "1",
            "LOGIN_PASSWORD_FALLBACK": "0",
            "LOGIN_LOGOUT_AFTER_POST": "1",
        }
        for idx, key in enumerate(self.FIELDS):
            ttk.Label(frame, text=key).grid(row=idx, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=values.get(key, DEFAULTS.get(key, "")))
            show = "*" if key in {
                "WP_APP_PASSWORD",
                "BAREMI542_WP_APP_PASSWORD",
                "NAVER_SEARCH_CLIENT_SECRET",
                "NAVER_SECRET_KEY",
                "PUBLIC_DATA_API_KEY",
                "BOKJIRO_API_KEY",
            } else ""
            ttk.Entry(frame, textvariable=var, show=show, width=54).grid(row=idx, column=1, sticky="we", pady=4)
            self.vars[key] = var
        frame.columnconfigure(1, weight=1)
        ttk.Button(frame, text="저장", command=self.save).grid(row=len(self.FIELDS), column=1, sticky="e", pady=12)

    def save(self):
        data = {key: var.get().strip() for key, var in self.vars.items()}
        save_env_values(data)
        self.master._sync_blog_id_widgets()
        messagebox.showinfo("저장 완료", ".env에 저장했습니다.")
        self.destroy()


if __name__ == "__main__":
    BlogDrafterApp().mainloop()
