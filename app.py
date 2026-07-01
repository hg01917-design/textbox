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

from config import BLOG_PROFILES, get_blog_profile, load_env, read_env_values, save_env_values
from content.generator import generate_draft_with_sources
from content.official_url import find_official_urls
from content.prompting import ensure_prompt_files, prompt_for_blog_type, prompt_names, prompt_path
from content.public_sources import fetch_public_source_context
from content.quality import check_draft
from content.source import fetch_sources, format_sources_for_prompt
from keywords.analyzer import analyze_keyword
from media.cards import generate_card_images
from publisher.wordpress import create_post
from publisher.tistory import post_tistory
from publisher.naver import post_naver
from storage.drafts import save_draft


class BlogDrafterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        load_env()
        ensure_prompt_files()
        self.title("Blog Helper")
        self.geometry("1080x760")
        self.current_payload = None
        self.current_paths = None
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="키워드").grid(row=0, column=0, sticky="w")
        self.keyword_var = tk.StringVar(value="경기도 청년 지원금 2026")
        ttk.Entry(top, textvariable=self.keyword_var, width=46).grid(row=0, column=1, sticky="we", padx=6)

        ttk.Label(top, text="블로그 유형").grid(row=0, column=2, sticky="w")
        self.blog_type_var = tk.StringVar(value="정부지원")
        self.blog_type_combo = ttk.Combobox(top, textvariable=self.blog_type_var, values=list(BLOG_PROFILES), width=12, state="readonly")
        self.blog_type_combo.grid(row=0, column=3, sticky="w", padx=6)
        self.blog_type_combo.bind("<<ComboboxSelected>>", self.on_blog_type_change)

        ttk.Label(top, text="source-url / 공공API").grid(row=1, column=0, sticky="w", pady=6)
        self.source_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.source_var, width=80).grid(row=1, column=1, columnspan=3, sticky="we", padx=6, pady=6)

        ttk.Label(top, text="프롬프트").grid(row=2, column=0, sticky="w", pady=6)
        self.prompt_var = tk.StringVar(value=prompt_for_blog_type(self.blog_type_var.get()))
        self.prompt_combo = ttk.Combobox(top, textvariable=self.prompt_var, values=prompt_names(), width=24, state="readonly")
        self.prompt_combo.grid(row=2, column=1, sticky="w", padx=6, pady=6)
        self.card_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="카드 이미지 자동 생성", variable=self.card_var).grid(row=2, column=2, columnspan=2, sticky="w", padx=6)

        self.generate_btn = ttk.Button(top, text="초안 생성", command=self.generate_draft)
        self.generate_btn.grid(row=0, column=4, padx=6)
        self.keyword_btn = ttk.Button(top, text="키워드 후보 찾기", command=self.find_keyword_candidates)
        self.keyword_btn.grid(row=0, column=5, padx=6)
        ttk.Label(top, text="WordPress").grid(row=1, column=2, sticky="w")
        wp_ids = [x.strip() for x in os.environ.get("WP_BLOG_IDS", "").split(",") if x.strip()]
        self.wp_id_var = tk.StringVar(value=wp_ids[0] if wp_ids else "")
        self.wp_combo = ttk.Combobox(top, textvariable=self.wp_id_var, values=wp_ids, width=12, state="readonly")
        self.wp_combo.grid(row=1, column=3, sticky="w", padx=6)
        ttk.Button(top, text="WordPress 임시저장", command=self.save_wordpress_draft).grid(row=1, column=4, padx=6)
        ttk.Button(top, text="설정", command=self.open_settings).grid(row=1, column=5, padx=6)
        ttk.Button(top, text="프롬프트 열기", command=self.open_prompt_file).grid(row=2, column=4, padx=6)
        ttk.Button(top, text="WordPress 발행", command=self.publish_wordpress).grid(row=2, column=5, padx=6)

        ttk.Label(top, text="Tistory ID").grid(row=3, column=0, sticky="w", pady=4)
        tistory_ids = [x.strip() for x in os.environ.get("TISTORY_BLOG_IDS", "").split(",") if x.strip()]
        self.tistory_id_var = tk.StringVar(value=tistory_ids[0] if tistory_ids else "")
        self.tistory_combo = ttk.Combobox(top, textvariable=self.tistory_id_var, values=tistory_ids, width=18)
        self.tistory_combo.grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Tistory 임시저장", command=self.save_tistory_draft).grid(row=3, column=4, padx=6)
        ttk.Button(top, text="Tistory 발행", command=self.publish_tistory).grid(row=3, column=5, padx=6)

        ttk.Label(top, text="Naver ID").grid(row=4, column=0, sticky="w", pady=4)
        naver_ids = [x.strip() for x in os.environ.get("NAVER_BLOG_IDS", "").split(",") if x.strip()]
        self.naver_id_var = tk.StringVar(value=naver_ids[0] if naver_ids else "")
        self.naver_combo = ttk.Combobox(top, textvariable=self.naver_id_var, values=naver_ids, width=18)
        self.naver_combo.grid(row=4, column=1, sticky="w", padx=6)
        ttk.Button(top, text="네이버 임시저장", command=self.save_naver_draft).grid(row=4, column=4, padx=6)
        ttk.Button(top, text="네이버 발행", command=self.publish_naver).grid(row=4, column=5, padx=6)

        ttk.Label(top, text="작성 대상").grid(row=5, column=0, sticky="w", pady=4)
        self.target_options = self._build_target_options(wp_ids, tistory_ids, naver_ids)
        self.target_var = tk.StringVar(value=self.target_options[0] if self.target_options else "")
        self.target_combo = ttk.Combobox(top, textvariable=self.target_var, values=self.target_options, width=28, state="readonly")
        self.target_combo.grid(row=5, column=1, sticky="w", padx=6)
        self.target_combo.bind("<<ComboboxSelected>>", self.on_target_change)
        ttk.Label(top, text="초안 생성 전에 반드시 선택").grid(row=5, column=2, columnspan=2, sticky="w", padx=6)

        top.columnconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(self, textvariable=self.status_var, padding=(10, 0)).pack(fill="x")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

        draft_tab = ttk.Frame(self.notebook)
        self.notebook.add(draft_tab, text="  초안 작성  ")
        self._build_draft_tab(draft_tab)

        pub_tab = ttk.Frame(self.notebook)
        self.notebook.add(pub_tab, text="  공공데이터  ")
        self._pub_all_items: list = []
        self._pub_filtered: list = []
        self._pub_selected: tuple | None = None
        self._build_public_data_tab(pub_tab)

    def _build_draft_tab(self, parent):
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        ttk.Label(left, text="키워드 후보").pack(anchor="w")
        self.keyword_list = tk.Listbox(left, height=9)
        self.keyword_list.pack(fill="x", pady=(0, 6))
        self.keyword_list.bind("<Double-Button-1>", self.use_selected_keyword)
        ttk.Button(left, text="선택한 키워드 사용", command=self.use_selected_keyword).pack(anchor="e", pady=(0, 10))

        ttk.Label(left, text="로그 / 결과").pack(anchor="w")
        self.log_text = tk.Text(left, height=18, wrap="word")
        self.log_text.pack(fill="both", expand=True)

        ttk.Label(right, text="초안 미리보기").pack(anchor="w")
        self.preview = tk.Text(right, wrap="word")
        self.preview.pack(fill="both", expand=True)

    # ─── 공공데이터 탭 ─────────────────────────────────────────────

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

        # ── 오른쪽: 상세 + 프롬프트 + 결과
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

        # 글 작성 패널
        write_lf = ttk.LabelFrame(vpaned, text="글 작성")
        vpaned.add(write_lf, weight=3)

        ttk.Label(write_lf, text="프롬프트 — 이 공공 정보로 어떤 글을 쓸지 지시하세요").pack(anchor="w", padx=4, pady=(4, 0))
        self._pub_prompt = tk.Text(write_lf, height=8, wrap="word")
        self._pub_prompt.pack(fill="x", padx=4, pady=4)
        prompt_scroll = ttk.Scrollbar(write_lf, orient="vertical", command=self._pub_prompt.yview)
        self._pub_prompt.config(yscrollcommand=prompt_scroll.set)
        prompt_scroll.place(relx=1.0, rely=0, relheight=0.28, anchor="ne")
        self._load_default_pub_prompt()

        ctrl_row = ttk.Frame(write_lf)
        ctrl_row.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(ctrl_row, text="블로그 유형:").pack(side="left")
        self._pub_blog_type_var = tk.StringVar(value="정부지원")
        ttk.Combobox(
            ctrl_row, textvariable=self._pub_blog_type_var,
            values=list(BLOG_PROFILES), width=10, state="readonly",
        ).pack(side="left", padx=4)
        self._pub_gen_btn = ttk.Button(ctrl_row, text="글 작성", command=self._pub_generate)
        self._pub_gen_btn.pack(side="left", padx=8)

        ttk.Label(write_lf, text="생성 결과").pack(anchor="w", padx=4)
        self._pub_result = tk.Text(write_lf, wrap="word")
        res_scroll = ttk.Scrollbar(write_lf, orient="vertical", command=self._pub_result.yview)
        self._pub_result.config(yscrollcommand=res_scroll.set)
        res_scroll.pack(side="right", fill="y")
        self._pub_result.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        pub_row = ttk.Frame(write_lf)
        pub_row.pack(fill="x", padx=4, pady=4)
        for text, cmd in (
            ("WP 임시저장",      self.save_wordpress_draft),
            ("WP 발행",          self.publish_wordpress),
            ("Tistory 임시저장", self.save_tistory_draft),
            ("Tistory 발행",     self.publish_tistory),
            ("네이버 임시저장",  self.save_naver_draft),
            ("네이버 발행",      self.publish_naver),
        ):
            ttk.Button(pub_row, text=text, command=cmd).pack(side="left", padx=2)

    def _on_tab_change(self, event=None):
        tab = self.notebook.index(self.notebook.select())
        if tab == 1 and not self._pub_all_items:
            self._load_pub_data()

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

    def _load_default_pub_prompt(self):
        from content.prompting import prompt_path
        from pathlib import Path
        naver_path = prompt_path("naver.txt")
        if naver_path.exists():
            raw = naver_path.read_text(encoding="utf-8")
            # 템플릿 변수 제거하고 규칙만 남기기
            import re as _re
            cleaned = _re.sub(r"\{[^}]+\}", "", raw).strip()
            self._pub_prompt.delete("1.0", tk.END)
            self._pub_prompt.insert("1.0", cleaned)
        else:
            self._pub_prompt.insert("1.0", "위 공공서비스 정보를 바탕으로, 신청 방법과 대상 조건을 중심으로 블로그 독자가 읽기 쉬운 정부지원 안내 글을 작성해주세요.")

    def _pub_generate(self):
        if not self._pub_selected:
            messagebox.showwarning("항목 선택 필요", "왼쪽 목록에서 항목을 선택하세요.")
            return
        user_prompt = self._pub_prompt.get("1.0", tk.END).strip()
        if not user_prompt:
            messagebox.showwarning("프롬프트 필요", "프롬프트를 입력하세요.")
            return
        self._pub_gen_btn.config(state="disabled")
        self._pub_result.delete("1.0", tk.END)
        self._set_status("공공데이터 기반 글 작성 중...")
        threading.Thread(
            target=self._pub_generate_worker,
            args=(self._pub_selected, user_prompt),
            daemon=True,
        ).start()

    def _pub_generate_worker(self, selected: tuple, user_prompt: str):
        try:
            name, source, item = selected
            blog_type = self._pub_blog_type_var.get() or "정부지원"
            source_context = self._format_pub_detail(name, source, item)

            full_prompt = f"""{user_prompt}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
공공서비스 원문 데이터 (출처: {source})
주제: {name}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{source_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
출력 형식 (반드시 준수)

제목을입력해주세요1: (위 주제를 그대로 사용)

본문2:
(인트로부터 시작)

===태그===
태그1, 태그2, 태그3, 태그4, 태그5
===태그끝===
"""
            result = subprocess.run(
                ["claude", "--print"],
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = (result.stdout or "").strip()
            if not output:
                raise ValueError("Claude CLI 응답이 비어 있습니다. claude --print 가 실행 가능한지 확인하세요.")

            from content.parser import parse_ai_response
            draft = parse_ai_response(output, name, blog_type)
            quality = check_draft(draft, name)
            target = self._parse_target(self.target_var.get())
            payload = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "keyword": name,
                "seed_keyword": name,
                "blog_type": blog_type,
                "target": {"platform": target[0], "blog_id": target[1]} if target else {},
                "draft": draft,
                "quality": quality,
                "source_urls": [str(item.get("상세조회URL", "") or item.get("servDtlLink", ""))],
                "sources": [{"url": source, "ok": True, "error": "", "text": source_context}],
                "images": [],
            }
            paths = save_draft(payload)
            self.current_payload = payload
            self.current_paths = paths
            self.after(0, self._show_pub_result, draft, quality, paths)
        except Exception as exc:
            self._log(f"공공데이터 글 작성 오류: {exc}")
            self._set_status("글 작성 오류")
        finally:
            self.after(0, lambda: self._pub_gen_btn.config(state="normal"))

    def _show_pub_result(self, draft: dict, quality: dict, paths: dict):
        self._pub_result.delete("1.0", tk.END)
        self._pub_result.insert(tk.END, f"# {draft.get('title', '')}\n\n")
        self._pub_result.insert(tk.END, draft.get("body", ""))
        self._log(f"공공데이터 글 완료: {draft.get('title', '')}")
        self._log(f"Quality: {'PASS' if quality['passed'] else 'WARN'} {quality['warnings']}")
        self._log(f"저장: {paths['markdown']}")
        self._set_status(f"글 작성 완료 ({'PASS' if quality['passed'] else 'WARN'})")

    def find_keyword_candidates(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("입력 필요", "큰 주제나 대략적인 키워드를 입력하세요. 예: 청년 지원금")
            return
        self.keyword_btn.config(state="disabled")
        self.keyword_list.delete(0, tk.END)
        self._set_status("키워드 후보 찾는 중...")
        threading.Thread(target=self._keyword_worker, args=(keyword,), daemon=True).start()

    def _keyword_worker(self, keyword: str):
        try:
            blog_type = self.blog_type_var.get().strip() or "일반"
            profile = get_blog_profile(blog_type)
            self._log(f"키워드 후보 분석: {keyword}")
            analysis = analyze_keyword(keyword, max_competition=profile["max_competition"], limit=15)
            rows = analysis.get("candidates", [])
            self.after(0, self._show_keyword_candidates, rows)
        except Exception as exc:
            self._log(f"키워드 후보 오류: {exc}")
            self._set_status("키워드 후보 오류")
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
            self.keyword_var.set(keyword)
            self._set_status(f"선택한 키워드: {keyword}")

    def on_blog_type_change(self, event=None):
        self.prompt_var.set(prompt_for_blog_type(self.blog_type_var.get()))

    def on_target_change(self, event=None):
        target = self._parse_target(self.target_var.get())
        if not target:
            return
        platform, blog_id = target
        if platform == "WordPress":
            self.wp_id_var.set(blog_id)
        elif platform == "Tistory":
            self.tistory_id_var.set(blog_id)
        elif platform == "Naver":
            self.naver_id_var.set(blog_id)
        inferred = self._infer_blog_type(platform, blog_id)
        if inferred:
            self.blog_type_var.set(inferred)
            self.prompt_var.set(prompt_for_blog_type(inferred))

    def generate_draft(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("입력 필요", "키워드를 입력하세요.")
            return
        target = self._parse_target(self.target_var.get())
        if not target:
            messagebox.showwarning("작성 대상 필요", "초안을 만들 블로그를 먼저 선택하세요.")
            return
        blog_type = self.blog_type_var.get().strip() or "일반"
        target_context = self._target_context(target, blog_type)
        source_urls = [u.strip() for u in self.source_var.get().split(",") if u.strip()]
        prompt_name = self.prompt_var.get()
        use_cards = self.card_var.get()
        self.generate_btn.config(state="disabled")
        self.preview.delete("1.0", tk.END)
        self.log_text.delete("1.0", tk.END)
        self._set_status("초안 생성 중...")
        threading.Thread(
            target=self._generate_worker,
            args=(keyword, blog_type, target, target_context, source_urls, prompt_name, use_cards),
            daemon=True,
        ).start()

    def _generate_worker(
        self,
        keyword: str,
        blog_type: str,
        target: tuple[str, str],
        target_context: str,
        source_urls: list[str],
        prompt_name: str,
        use_cards: bool,
    ):
        try:
            profile = get_blog_profile(blog_type)
            self._log(f"작성 대상: {target_context}")
            self._log(f"[1/5] 키워드 분석: {keyword}")
            analysis = analyze_keyword(keyword, max_competition=profile["max_competition"], limit=8)
            best_keyword = analysis.get("best_keyword") or keyword
            related = [row["keyword"] for row in analysis.get("candidates", []) if row["keyword"] != best_keyword]

            sources = []
            source_context = ""
            auto_source_urls = find_official_urls(best_keyword, blog_type, on_log=self._log)
            source_urls = _merge_urls(source_urls, auto_source_urls)
            if source_urls:
                self._log(f"[2/5] 공식 URL 읽기: {len(source_urls)}개")
                sources = fetch_sources(source_urls)
                source_context = format_sources_for_prompt(sources)
            public_context = fetch_public_source_context(best_keyword, blog_type, on_log=self._log)
            if public_context:
                self._log("[2/5] 공공API 자료 보강 완료")
                source_context = "\n\n".join(part for part in (source_context, public_context) if part)
                sources.append({"url": "public-api:data.go.kr", "ok": True, "error": "", "text": public_context})

            self._log(f"[3/6] CLI 모델 초안 생성: {best_keyword}")
            draft = generate_draft_with_sources(
                best_keyword,
                blog_type,
                related,
                provider="cli",
                source_context=source_context,
                prompt_name=prompt_name,
                target_context=target_context,
            )
            images = []
            if use_cards:
                self._log("[4/6] 카드 이미지 생성")
                images = generate_card_images(best_keyword, blog_type, on_log=self._log)
            self._log("[5/6] 품질 검사")
            quality = check_draft(draft, best_keyword, min_chars=1200)
            self._log(f"Provider: {draft.get('provider')} / publishable: {draft.get('publishable')}")
            if draft.get("generation_error"):
                self._log(f"생성 경고: {draft.get('generation_error')}")
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
            self._log("[6/6] 파일 저장")
            paths = save_draft(payload)
            self.current_payload = payload
            self.current_paths = paths
            self.after(0, self._show_payload, payload, paths)
        except Exception as exc:
            self._log(f"오류: {exc}")
            self._set_status("오류 발생")
        finally:
            self.after(0, lambda: self.generate_btn.config(state="normal"))

    def _show_payload(self, payload: dict, paths: dict):
        draft = payload["draft"]
        quality = payload["quality"]
        self.preview.delete("1.0", tk.END)
        self.preview.insert(tk.END, f"# {draft['title']}\n\n")
        if not self._is_payload_target(payload, "Naver"):
            self.preview.insert(tk.END, f"메타설명: {draft['meta_description']}\n\n")
        self.preview.insert(tk.END, f"태그: {', '.join(draft['tags'])}\n\n")
        if payload.get("target_context"):
            self.preview.insert(tk.END, f"작성 대상: {payload['target_context']}\n\n")
        images = payload.get("images", [])
        if images:
            self.preview.insert(tk.END, "카드 이미지:\n" + "\n".join(f"- {path}" for path in images) + "\n\n")
        self.preview.insert(tk.END, draft["body"])
        self._log(f"Markdown: {paths['markdown']}")
        self._log(f"JSON: {paths['json']}")
        self._log(f"Quality: {'PASS' if quality['passed'] else 'WARN'} {quality['warnings']}")
        self._set_status(f"완료: {'PASS' if quality['passed'] else 'WARN'}")

    def open_prompt_file(self):
        path = prompt_path(self.prompt_var.get())
        subprocess.run(["open", str(path)], check=False)

    def save_wordpress_draft(self):
        if not self.current_payload:
            messagebox.showwarning("초안 없음", "먼저 초안을 생성하세요.")
            return
        if not self._require_publishable():
            return
        quality = self.current_payload["quality"]
        if not quality["passed"]:
            if not messagebox.askyesno("품질 경고", "품질 검사를 통과하지 못했습니다. 그래도 임시저장할까요?"):
                return
        draft = self.current_payload["draft"]
        blog_id = self.wp_id_var.get().strip()
        if not self._confirm_target_match("WordPress", blog_id):
            return
        self._set_status("WordPress 임시저장 중...")
        threading.Thread(target=self._wp_worker, args=(blog_id, draft, "draft"), daemon=True).start()

    def publish_wordpress(self):
        if not self.current_payload:
            messagebox.showwarning("초안 없음", "먼저 초안을 생성하세요.")
            return
        if not self._require_publishable():
            return
        quality = self.current_payload["quality"]
        if not quality["passed"]:
            if not messagebox.askyesno("품질 경고", "품질 검사를 통과하지 못했습니다. 그래도 발행할까요?"):
                return
        if not messagebox.askyesno("발행 확인", f"'{self.current_payload['draft']['title']}'\n\n바로 발행하시겠습니까?"):
            return
        draft = self.current_payload["draft"]
        blog_id = self.wp_id_var.get().strip()
        if not self._confirm_target_match("WordPress", blog_id):
            return
        self._set_status("WordPress 발행 중...")
        threading.Thread(target=self._wp_worker, args=(blog_id, draft, "publish"), daemon=True).start()

    def _wp_worker(self, blog_id: str, draft: dict, status: str):
        if blog_id == "triplog":
            kwargs = dict(
                site_url=os.environ.get("TRIPLOG_WP_URL", ""),
                user=os.environ.get("TRIPLOG_WP_USER", ""),
                app_password=os.environ.get("TRIPLOG_WP_APP_PASSWORD", ""),
            )
        else:
            kwargs = {}
        result = create_post(
            title=draft["title"],
            content_markdown=draft["body"],
            tags=draft.get("tags", []),
            status=status,
            image_paths=self.current_payload.get("images", []),
            **kwargs,
        )
        label = "발행" if status == "publish" else "임시저장"
        if result.get("ok"):
            self._log(f"WordPress {label} 완료: {result.get('link')} / 이미지 {result.get('media_count', 0)}장")
            self._set_status(f"WordPress {label} 완료")
        else:
            self._log(f"WordPress 오류: {result.get('error')}")
            self._set_status("WordPress 오류")

    def _require_draft(self, label: str) -> bool:
        if not self.current_payload:
            messagebox.showwarning("초안 없음", "먼저 초안을 생성하세요.")
            return False
        if not self._require_publishable():
            return False
        quality = self.current_payload["quality"]
        if not quality["passed"]:
            return messagebox.askyesno("품질 경고", f"품질 검사를 통과하지 못했습니다. 그래도 {label}할까요?")
        return True

    def _require_publishable(self) -> bool:
        draft = self.current_payload.get("draft", {}) if self.current_payload else {}
        if draft.get("provider") == "template" or draft.get("publishable") is False:
            messagebox.showerror(
                "발행 차단",
                "Claude/OpenAI 생성에 실패해서 템플릿 초안이 만들어졌습니다.\n"
                "이 초안은 발행할 수 없습니다.\n\n"
                f"오류: {draft.get('generation_error') or '생성 실패 원인 없음'}",
            )
            self._log("발행 차단: template fallback 초안")
            return False
        return True

    def save_tistory_draft(self):
        if not self._require_draft("임시저장"):
            return
        blog_id = self.tistory_id_var.get().strip()
        if not blog_id:
            messagebox.showwarning("ID 필요", "Tistory 블로그 ID를 입력하세요.")
            return
        if not self._confirm_target_match("Tistory", blog_id):
            return
        draft = self.current_payload["draft"]
        self._set_status("Tistory 임시저장 중...")
        threading.Thread(target=self._tistory_worker, args=(blog_id, draft, "draft"), daemon=True).start()

    def publish_tistory(self):
        if not self._require_draft("발행"):
            return
        blog_id = self.tistory_id_var.get().strip()
        if not blog_id:
            messagebox.showwarning("ID 필요", "Tistory 블로그 ID를 입력하세요.")
            return
        if not messagebox.askyesno("발행 확인", f"[{blog_id}]\n'{self.current_payload['draft']['title']}'\n\nTistory에 발행하시겠습니까?"):
            return
        if not self._confirm_target_match("Tistory", blog_id):
            return
        draft = self.current_payload["draft"]
        self._set_status("Tistory 발행 중...")
        threading.Thread(target=self._tistory_worker, args=(blog_id, draft, "publish"), daemon=True).start()

    def _tistory_worker(self, blog_id: str, draft: dict, status: str):
        result = post_tistory(
            blog_id=blog_id,
            title=draft["title"],
            content_markdown=draft["body"],
            tags=draft.get("tags", []),
            image_paths=self.current_payload.get("images", []),
            status=status,
        )
        label = "발행" if status == "publish" else "임시저장"
        if result.get("ok"):
            self._log(f"Tistory {label} 완료: {result.get('url')}")
            self._set_status(f"Tistory {label} 완료")
        else:
            self._log(f"Tistory 오류: {result.get('error')}")
            self._set_status("Tistory 오류")

    def save_naver_draft(self):
        if not self._require_draft("임시저장"):
            return
        if not self._confirm_naver_images():
            return
        blog_id = self.naver_id_var.get().strip()
        if not blog_id:
            messagebox.showwarning("ID 필요", "네이버 블로그 ID를 입력하세요.")
            return
        if not self._confirm_target_match("Naver", blog_id):
            return
        draft = self.current_payload["draft"]
        self._set_status("네이버 임시저장 중...")
        threading.Thread(target=self._naver_worker, args=(blog_id, draft, "draft"), daemon=True).start()

    def publish_naver(self):
        if not self._require_draft("발행"):
            return
        if not self._confirm_naver_images():
            return
        blog_id = self.naver_id_var.get().strip()
        if not blog_id:
            messagebox.showwarning("ID 필요", "네이버 블로그 ID를 입력하세요.")
            return
        if not messagebox.askyesno("발행 확인", f"[{blog_id}]\n'{self.current_payload['draft']['title']}'\n\n네이버에 발행하시겠습니까?"):
            return
        if not self._confirm_target_match("Naver", blog_id):
            return
        draft = self.current_payload["draft"]
        self._set_status("네이버 발행 중...")
        threading.Thread(target=self._naver_worker, args=(blog_id, draft, "publish"), daemon=True).start()

    def _naver_worker(self, blog_id: str, draft: dict, status: str):
        result = post_naver(
            blog_id=blog_id,
            title=draft["title"],
            content_markdown=draft["body"],
            tags=draft.get("tags", []),
            image_paths=self.current_payload.get("images", []),
            status=status,
            on_log=self._log,
        )
        label = "발행" if status == "publish" else "임시저장"
        if result.get("ok"):
            self._log(f"네이버 {label} 완료: {result.get('url')}")
            self._set_status(f"네이버 {label} 완료")
        else:
            self._log(f"네이버 오류: {result.get('error')}")
            self._set_status("네이버 오류")

    def _confirm_naver_images(self) -> bool:
        images = self.current_payload.get("images", []) if self.current_payload else []
        blog_type = self.current_payload.get("blog_type", "") if self.current_payload else ""
        recommended = 5 if blog_type == "여행" else 3
        if len(images) >= recommended:
            return True
        return messagebox.askyesno(
            "이미지 부족",
            f"네이버 글은 이미지가 부족하면 품질이 떨어질 수 있습니다.\n"
            f"현재 이미지: {len(images)}장 / 권장: {recommended}장\n\n"
            "그래도 진행할까요?",
        )

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

    def _infer_blog_type(self, platform: str, blog_id: str) -> str:
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
        return self.blog_type_var.get().strip() or "일반"

    def _confirm_target_match(self, platform: str, blog_id: str) -> bool:
        target = self.current_payload.get("target", {}) if self.current_payload else {}
        if not target:
            return True
        if target.get("platform") == platform and target.get("blog_id") == blog_id:
            return True
        return messagebox.askyesno(
            "작성 대상 불일치",
            "이 초안은 다른 블로그 대상으로 생성되었습니다.\n\n"
            f"초안 대상: {target.get('platform')}:{target.get('blog_id')}\n"
            f"현재 발행: {platform}:{blog_id}\n\n"
            "그래도 진행할까요?",
        )

    def _is_payload_target(self, payload: dict, platform: str) -> bool:
        target = payload.get("target", {}) if payload else {}
        return target.get("platform") == platform

    def open_settings(self):
        SettingsWindow(self)

    def _log(self, message: str):
        try:
            with open("/tmp/blog-helper.log", "a", encoding="utf-8") as log_file:
                log_file.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
        except Exception:
            pass
        self.after(0, lambda: (self.log_text.insert(tk.END, message + "\n"), self.log_text.see(tk.END)))

    def _set_status(self, message: str):
        self.after(0, lambda: self.status_var.set(message))


def _merge_urls(primary: list[str], secondary: list[str]) -> list[str]:
    merged = []
    for url in primary + secondary:
        if url and url not in merged:
            merged.append(url)
    return merged


class SettingsWindow(tk.Toplevel):
    FIELDS = [
        "WP_BLOG_IDS",
        "WP_SITE_URL",
        "WP_USER",
        "WP_APP_PASSWORD",
        "WP_DEFAULT_STATUS",
        "TRIPLOG_WP_URL",
        "TRIPLOG_WP_USER",
        "TRIPLOG_WP_APP_PASSWORD",
        "TISTORY_BLOG_IDS",
        "NAVER_BLOG_IDS",
        "CHROME_PORT",
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
        DEFAULTS = {"WP_DEFAULT_STATUS": "draft", "CHROME_PORT": "9222"}
        for idx, key in enumerate(self.FIELDS):
            ttk.Label(frame, text=key).grid(row=idx, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=values.get(key, DEFAULTS.get(key, "")))
            show = "*" if key in {
                "WP_APP_PASSWORD",
                "TRIPLOG_WP_APP_PASSWORD",
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
        # 메인 창 블로그 ID 필드 동기화
        try:
            app = self.master
            wp_ids = [x.strip() for x in data.get("WP_BLOG_IDS", "").split(",") if x.strip()]
            if wp_ids:
                app.wp_combo["values"] = wp_ids
                app.wp_id_var.set(wp_ids[0])
            tistory_ids = [x.strip() for x in data.get("TISTORY_BLOG_IDS", "").split(",") if x.strip()]
            if tistory_ids:
                app.tistory_combo["values"] = tistory_ids
                app.tistory_id_var.set(tistory_ids[0])
            naver_ids = [x.strip() for x in data.get("NAVER_BLOG_IDS", "").split(",") if x.strip()]
            if naver_ids:
                app.naver_combo["values"] = naver_ids
                app.naver_id_var.set(naver_ids[0])
            target_options = app._build_target_options(wp_ids, tistory_ids, naver_ids)
            app.target_options = target_options
            app.target_combo["values"] = target_options
            if target_options:
                app.target_var.set(target_options[0])
                app.on_target_change()
        except Exception:
            pass
        messagebox.showinfo("저장 완료", ".env에 저장했습니다.")
        self.destroy()


if __name__ == "__main__":
    BlogDrafterApp().mainloop()
