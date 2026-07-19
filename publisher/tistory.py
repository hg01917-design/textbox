"""Tistory 블로그 발행 — CDP + playwright-stealth"""
import os
import re
import time
import random
from pathlib import Path

from .browser import connect, get_page
from .accounts import ensure_chrome_for, login_account_id
from .login import ensure_tistory_login, logout_after_post_enabled, logout_tistory


_TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')
_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')
_UL_RE = re.compile(r'^[-*]\s+(.*)$')
_OL_RE = re.compile(r'^\d+\.\s+(.*)$')
_HR_RE = re.compile(r'^(-{3,}|_{3,}|\*{3,})$')
_CHECKBOX_RE = re.compile(r'^\[[ xX]\]\s*')
_CUSTOM_TABLE_START_RE = re.compile(r'^표\s*\d+\s*x\s*\d+\s*시작$')
_CUSTOM_TABLE_END_RE = re.compile(r'^표\s*\d+\s*x\s*\d+\s*끝$')
_CUSTOM_TABLE_CELL_RE = re.compile(r'^\((\d+),(\d+)\)\s*(.*)$')


def _strip_checkbox(text: str) -> str:
    return _CHECKBOX_RE.sub('', text)


def _parse_custom_table(cell_lines: list[str]) -> list[list[str]]:
    """표 N x M 시작/(r,c) 내용/표 N x M 끝 → 2D 리스트로 파싱 (publisher/naver.py와 동일 형식)."""
    cells: dict[tuple[int, int], str] = {}
    max_r = max_c = 0
    for line in cell_lines:
        m = _CUSTOM_TABLE_CELL_RE.match(line.strip())
        if m:
            r, c, text = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            cells[(r, c)] = text
            max_r = max(max_r, r)
            max_c = max(max_c, c)
    if not cells:
        return []
    return [[cells.get((r, c), "") for c in range(max_c + 1)] for r in range(max_r + 1)]


def _split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [cell.strip() for cell in s.split('|')]


def _parse_blocks(markdown: str) -> list[dict]:
    """마크다운을 문단/제목/표/목록/구분선 블록 리스트로 분리."""
    lines = markdown.split('\n')
    n = len(lines)
    blocks = []
    i = 0
    while i < n:
        s = lines[i].strip()

        if not s:
            i += 1
            continue

        if _CUSTOM_TABLE_START_RE.match(s):
            i += 1
            cell_lines = []
            while i < n and not _CUSTOM_TABLE_END_RE.match(lines[i].strip()):
                if lines[i].strip():
                    cell_lines.append(lines[i].strip())
                i += 1
            i += 1  # skip the "끝" line
            rows = _parse_custom_table(cell_lines)
            if rows:
                blocks.append({"type": "table", "rows": rows})
            continue

        if _TABLE_ROW_RE.match(s) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            rows = [_split_table_row(s)]
            i += 2
            while i < n and _TABLE_ROW_RE.match(lines[i].strip()):
                rows.append(_split_table_row(lines[i].strip()))
                i += 1
            blocks.append({"type": "table", "rows": rows})
            continue

        if _HR_RE.match(s):
            blocks.append({"type": "hr"})
            i += 1
            continue

        if s.startswith('### '):
            blocks.append({"type": "h3", "text": s[4:].strip()})
            i += 1
            continue

        if s.startswith('## ') or s.startswith('# '):
            blocks.append({"type": "h2", "text": re.sub(r'^#{1,2}\s+', '', s)})
            i += 1
            continue

        ul_match = _UL_RE.match(s)
        if ul_match:
            items = [_strip_checkbox(ul_match.group(1))]
            i += 1
            while i < n and _UL_RE.match(lines[i].strip()):
                items.append(_strip_checkbox(_UL_RE.match(lines[i].strip()).group(1)))
                i += 1
            blocks.append({"type": "ul", "items": items})
            continue

        ol_match = _OL_RE.match(s)
        if ol_match:
            items = [ol_match.group(1)]
            i += 1
            while i < n and _OL_RE.match(lines[i].strip()):
                items.append(_OL_RE.match(lines[i].strip()).group(1))
                i += 1
            blocks.append({"type": "ol", "items": items})
            continue

        blocks.append({"type": "p", "text": s})
        i += 1

    return blocks


def _block_html(block: dict) -> str:
    t = block["type"]
    if t == "h2":
        return f'<h2 data-ke-size="size26">{_inline_md(block["text"])}</h2>'
    if t == "h3":
        return f'<h3 data-ke-size="size23">{_inline_md(block["text"])}</h3>'
    if t == "p":
        return f'<p data-ke-size="size19">{_inline_md(block["text"])}</p>'
    if t == "hr":
        return '<hr data-ke-style="style6">'
    if t == "ul":
        items = ''.join(f'<li>{_inline_md(item)}</li>' for item in block["items"])
        return f'<ul>{items}</ul>'
    if t == "ol":
        items = ''.join(f'<li>{_inline_md(item)}</li>' for item in block["items"])
        return f'<ol>{items}</ol>'
    if t == "table":
        rows = block["rows"]
        if not rows:
            return ""
        n_cols = max(len(r) for r in rows)
        col_width = round(100 / n_cols, 2)
        trs = []
        for row_idx, row in enumerate(rows):
            cells = []
            for c in range(n_cols):
                text = _inline_md(row[c]) if c < len(row) and row[c] else '&nbsp;'
                if row_idx == 0:
                    text = f'<strong>{text}</strong>'
                cells.append(f'<td style="width: {col_width}%;">{text}</td>')
            trs.append(f'<tr>{"".join(cells)}</tr>')
        return (
            '<table style="border-collapse: collapse; width: 100%;" border="1" data-ke-align="alignLeft">'
            f'<tbody>{"".join(trs)}</tbody></table>'
            '<p data-ke-size="size19">&nbsp;</p>'
        )
    return ""


def _inline_md(text: str) -> str:
    from content.html_renderer import _link_rel

    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(
        r'\[([^\]]+)\]\((https?://[^)]+)\)',
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="{_link_rel(m.group(2))}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    return text


def _rand(page, lo=500, hi=1200):
    page.wait_for_timeout(random.randint(lo, hi))


def _focus_editor_end(page) -> None:
    try:
        page.evaluate("""() => {
            const ed = window.tinymce && (tinymce.get('editor-tistory') || tinymce.activeEditor || (tinymce.editors && tinymce.editors[0]));
            if (!ed) return;
            ed.focus();
            ed.selection.select(ed.getBody(), true);
            ed.selection.collapse(false);
        }""")
    except Exception:
        pass


def _insert_block_html(page, html: str) -> None:
    page.evaluate("""(html) => {
        const ed = window.tinymce && (tinymce.get('editor-tistory') || tinymce.activeEditor || (tinymce.editors && tinymce.editors[0]));
        if (!ed) return;
        ed.focus();
        ed.selection.select(ed.getBody(), true);
        ed.selection.collapse(false);
        ed.insertContent(html);
    }""", html)


def _upload_tistory_image(page, img_path: str) -> bool:
    """현재 커서(본문 맨 끝) 위치에 이미지 업로드."""
    try:
        _focus_editor_end(page)
        page.evaluate("""() => {
            const icons = [...document.querySelectorAll('i.mce-ico.mce-i-image')];
            const visible = icons.find(ico => ico.getBoundingClientRect().width > 0);
            if (visible) visible.closest('button').click();
        }""")
        time.sleep(0.8)
        with page.expect_file_chooser(timeout=5000) as fc_info:
            page.evaluate("""() => {
                const items = [...document.querySelectorAll('.mce-tistory-attach-item')];
                const el = items.find(e => e.textContent.trim() === '사진');
                if (el) el.click();
            }""")
        fc_info.value.set_files(img_path)
        time.sleep(4)
        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.5)
        return False


# 티스토리 "서식" 목록에 저장된 "애드센스광고코드"와 동일한 본문 중간 광고 코드.
# 에디터에 다시 불러오면 <ins> 태그가 화면에서 사라져 보이지만, 이는 TinyMCE가
# 자체 스키마로 재해석하며 표시만 지우는 것일 뿐 실제 저장/발행된 페이지에는
# 정상적으로 남아 광고가 채워진다 (data-ad-slot="3113682298" 기준 실제 발행 글에서 확인됨).
_ADSENSE_MID_AD_HTML = (
    '<script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?'
    'client=ca-pub-1646757278810260"></script>\n'
    '<!-- [디스플레이,사각,반응형]중간광고 -->\n'
    '<ins class="adsbygoogle" style="display: block;" data-ad-client="ca-pub-1646757278810260" '
    'data-ad-slot="3113682298" data-ad-format="auto" data-full-width-responsive="true"></ins>\n'
    '<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>'
)


def _insert_tistory_ad(page) -> bool:
    """현재 커서(본문 맨 끝) 위치에 저장된 애드센스 중간광고 코드 삽입."""
    try:
        _insert_block_html(page, _ADSENSE_MID_AD_HTML)
        return True
    except Exception:
        return False


def _select_tistory_category(page, category: str) -> bool:
    """카테고리 드롭다운에서 이름이 일치하는 카테고리를 선택."""
    if not category:
        return False
    try:
        page.evaluate("""() => { const btn = document.querySelector('#category-btn'); if (btn) btn.click(); }""")
        time.sleep(0.6)
        selected = page.evaluate("""(category) => {
            const items = [...document.querySelectorAll('li, [role="option"]')].filter(el => el.getBoundingClientRect().width > 0);
            const target = items.find(el => el.innerText.trim() === category)
                || items.find(el => el.innerText.trim().includes(category));
            if (!target) return false;
            target.click();
            return true;
        }""", category)
        if not selected:
            page.keyboard.press("Escape")
        time.sleep(0.4)
        return bool(selected)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def post_tistory(blog_id: str, title: str, content_markdown: str,
                 tags: list = None, image_paths: list = None,
                 status: str = "draft", insert_ad: bool = True,
                 category: str = "", port: int = None) -> dict:
    """Tistory 임시저장(draft) 또는 발행(publish)

    blog_id: tistory 블로그 아이디 (예: nolja100)
    insert_ad: 본문 중간에 애드센스 광고(에디터) 자동 삽입 여부
    category: 지정 시 해당 이름의 카테고리를 선택 (없으면 카테고리 없음 유지)
    port: 이 blog_id 계정이 로그인되어 있는 Chrome 디버그 포트 (계정별로 분리된
        프로필을 쓸 때 지정). 생략하면 CHROME_PORT(.env, 기본 9222) 사용.
    Returns: {"ok": bool, "url": str, "error": str}
    """
    tags = tags or []
    image_paths = [p for p in (image_paths or []) if Path(p).exists()]

    if port is None:
        port = ensure_chrome_for("tistory", blog_id)
    pw, browser = connect(port=port)
    page = None
    try:
        editor_url = f"https://{blog_id}.tistory.com/manage/newpost"
        page = get_page(browser, navigate_to=editor_url)
        _rand(page, 3000, 5000)

        current_url = page.url
        if "about:blank" in current_url:
            return {"ok": False, "error": "페이지 이동 실패 (about:blank) — Chrome CDP 연결 상태를 확인하세요."}

        if "auth/login" in current_url or "accounts.kakao" in current_url:
            kakao_id = login_account_id("tistory", blog_id)
            logout_tistory(page)
            if not ensure_tistory_login(page, blog_id, kakao_id=kakao_id):
                return {"ok": False, "error": f"티스토리 저장 계정 로그인 실패: {kakao_id or blog_id}"}
            page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
            _rand(page, 2500, 4000)

        # 에디터 로드 대기 — 로그인 필요 시 Chrome에서 로그인하면 자동으로 이어짐 (최대 3분)
        title_el = None
        for sel in ["#post-title-inp", ".tit_post input", "input[placeholder*='제목']", "input[name='title']"]:
            try:
                page.wait_for_selector(sel, timeout=180000)
                title_el = page.query_selector(sel)
                if title_el:
                    break
            except Exception:
                continue
        if not title_el:
            if "auth/login" in page.url or "accounts.kakao" in page.url:
                kakao_id = login_account_id("tistory", blog_id)
                logout_tistory(page)
                if ensure_tistory_login(page, blog_id, kakao_id=kakao_id):
                    page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
                    _rand(page, 2500, 4000)
                    for sel in ["#post-title-inp", ".tit_post input", "input[placeholder*='제목']", "input[name='title']"]:
                        try:
                            page.wait_for_selector(sel, timeout=10000)
                            title_el = page.query_selector(sel)
                            if title_el:
                                break
                        except Exception:
                            continue
            if title_el:
                pass
            else:
                return {"ok": False, "error": f"에디터 로드 실패 (3분 초과) — 현재 URL: {page.url}"}

        # 글 복원 팝업 닫기
        try:
            page.evaluate("""() => {
                for (const btn of document.querySelectorAll('button, a')) {
                    if (['새 글 작성', '취소', '아니오'].some(t => btn.textContent.trim().includes(t))) {
                        btn.click(); return;
                    }
                }
            }""")
            _rand(page, 800, 1500)
        except Exception:
            pass

        # 제목 입력
        title_el.click(click_count=3)
        _rand(page, 300, 600)
        title_el.type(title, delay=random.randint(40, 100))
        _rand(page, 400, 800)

        # 카테고리 선택
        if category:
            _select_tistory_category(page, category)
            _rand(page, 300, 600)

        # TinyMCE 본문 — 블록 단위로 순서대로 삽입하고, 이미지도 본문 사이에 분산 배치
        page.wait_for_selector("#editor-tistory_ifr", timeout=15000)
        blocks = _parse_blocks(content_markdown)
        n_blocks = len(blocks)
        n_images = len(image_paths)

        image_insert_map: dict[int, list[str]] = {}
        if n_images > 0 and n_blocks > 0:
            for img_idx, img_path in enumerate(image_paths):
                block_pos = int(round((img_idx + 1) * n_blocks / (n_images + 1))) - 1
                block_pos = max(0, min(block_pos, n_blocks - 1))
                image_insert_map.setdefault(block_pos, []).append(img_path)

        page.evaluate("""() => {
            const ed = window.tinymce && (tinymce.get('editor-tistory') || tinymce.activeEditor || (tinymce.editors && tinymce.editors[0]));
            if (ed) { ed.setContent(''); ed.fire('change'); }
        }""")
        _rand(page, 300, 600)

        def _adjacent_to_image(pos: int) -> bool:
            return pos in image_insert_map or (pos + 1) in image_insert_map

        ad_block_pos = -1
        if insert_ad and n_blocks >= 6:
            mid = n_blocks // 2
            # 표 바로 위, 또는 소제목 바로 밑을 우선 후보로 삼는다.
            candidates = [
                i - 1 if block["type"] == "table" else i
                for i, block in enumerate(blocks)
                if (block["type"] == "table" and i > 0) or block["type"] in ("h2", "h3")
            ]
            candidates = sorted(set(c for c in candidates if 0 <= c < n_blocks - 1))
            if candidates:
                safe = [c for c in candidates if not _adjacent_to_image(c)]
                pool = safe or candidates
                ad_block_pos = min(pool, key=lambda c: abs(c - mid))
            else:
                ad_block_pos = mid
                while ad_block_pos < n_blocks - 1 and _adjacent_to_image(ad_block_pos):
                    ad_block_pos += 1

        for index, block in enumerate(blocks):
            html = _block_html(block)
            if html:
                _insert_block_html(page, html)
                _rand(page, 60, 160)
            for img_path in image_insert_map.get(index, []):
                _upload_tistory_image(page, img_path)
            if index == ad_block_pos:
                _insert_tistory_ad(page)

        page.evaluate("""() => {
            const ed = window.tinymce && (tinymce.get('editor-tistory') || tinymce.activeEditor || (tinymce.editors && tinymce.editors[0]));
            if (ed) { ed.fire('change'); ed.save(); }
        }""")
        _rand(page, 500, 1000)

        # 태그 입력
        if tags:
            for sel in ["#tagText", ".tag_post input", "input[placeholder*='태그']"]:
                tag_el = page.query_selector(sel)
                if tag_el:
                    for tag in tags[:10]:
                        tag_el.click()
                        tag_el.type(tag, delay=50)
                        time.sleep(0.3)
                        page.keyboard.press("Enter")
                        time.sleep(0.3)
                    break

        # 저장/발행 버튼 클릭
        if status == "publish":
            # "완료" 클릭 시 발행 설정 패널이 열리는데, 기본 공개설정이 "비공개"라
            # 버튼 텍스트가 "비공개 저장"으로 뜬다. "공개"를 먼저 선택해야
            # 버튼 텍스트가 "공개 발행"으로 바뀐다 ("발행" 단독 텍스트는 존재하지 않음).
            page.get_by_text("완료", exact=True).first.click()
            _rand(page, 800, 1500)
            try:
                page.get_by_text("공개", exact=True).first.click()
                _rand(page, 500, 1000)
            except Exception:
                pass
            btn = page.get_by_text("공개 발행", exact=True).first
        else:
            btn = page.get_by_text("임시저장", exact=True).first

        btn.click()
        time.sleep(3)

        return {"ok": True, "url": page.url}

    except Exception as e:
        try:
            if page and not page.is_closed():
                page.close()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        if page and not page.is_closed() and logout_after_post_enabled():
            logout_tistory(page)
        try:
            pw.stop()
        except Exception:
            pass
