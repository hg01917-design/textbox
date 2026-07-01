"""Tistory 블로그 발행 — CDP + playwright-stealth"""
import os
import re
import time
import random
from pathlib import Path

from .browser import connect, get_page


def _md_to_tinymce_html(markdown: str) -> str:
    """마크다운 → Tistory TinyMCE HTML"""
    parts = []
    for line in markdown.split('\n'):
        s = line.strip()
        if not s:
            parts.append('<p data-ke-size="size19">&nbsp;</p>')
        elif s.startswith('### '):
            text = _inline_md(s[4:].strip())
            parts.append(f'<h3 data-ke-size="size23">{text}</h3>')
        elif s.startswith('## ') or s.startswith('# '):
            text = _inline_md(re.sub(r'^#{1,2}\s+', '', s))
            parts.append(f'<h2 data-ke-size="size26">{text}</h2>')
        else:
            parts.append(f'<p data-ke-size="size19">{_inline_md(s)}</p>')
    return '\n'.join(parts)


def _inline_md(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    return text


def _rand(page, lo=500, hi=1200):
    page.wait_for_timeout(random.randint(lo, hi))


def post_tistory(blog_id: str, title: str, content_markdown: str,
                 tags: list = None, image_paths: list = None,
                 status: str = "draft") -> dict:
    """Tistory 임시저장(draft) 또는 발행(publish)

    blog_id: tistory 블로그 아이디 (예: nolja100)
    Returns: {"ok": bool, "url": str, "error": str}
    """
    tags = tags or []
    image_paths = [p for p in (image_paths or []) if Path(p).exists()]

    pw, browser = connect()
    page = None
    try:
        editor_url = f"https://{blog_id}.tistory.com/manage/newpost"
        page = get_page(browser, navigate_to=editor_url)
        _rand(page, 3000, 5000)

        current_url = page.url
        if "about:blank" in current_url:
            return {"ok": False, "error": "페이지 이동 실패 (about:blank) — Chrome CDP 연결 상태를 확인하세요."}

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
        title_el.click()
        _rand(page, 300, 600)
        title_el.triple_click()
        title_el.type(title, delay=random.randint(40, 100))
        _rand(page, 400, 800)

        # TinyMCE 본문 setContent
        page.wait_for_selector("#editor-tistory_ifr", timeout=15000)
        body_html = _md_to_tinymce_html(content_markdown)
        page.evaluate("""(html) => {
            const ed = window.tinymce && (tinymce.get('content') || tinymce.activeEditor);
            if (!ed) return;
            ed.setContent(html);
            ed.fire('change');
            ed.save();
        }""", body_html)
        _rand(page, 800, 1500)

        # 이미지 업로드
        for img_path in image_paths[:3]:
            try:
                # 이미지 버튼 클릭
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
            except Exception:
                page.keyboard.press("Escape")
                time.sleep(0.5)

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
            btn = page.get_by_text("발행", exact=True).first
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
        try:
            pw.stop()
        except Exception:
            pass
