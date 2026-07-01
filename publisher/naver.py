"""네이버 블로그 발행 — CDP + playwright-stealth (SmartEditor3)"""
import re
import subprocess
import time
import random
import traceback
from pathlib import Path

from .browser import connect, get_page


# ─── 마크다운 파싱 ──────────────────────────────────────────────

def _parse_sections(markdown: str) -> list:
    """마크다운을 heading/text 섹션 리스트로 분리"""
    sections = []
    current_text = []

    for line in markdown.split('\n'):
        s = line.strip()
        if s.startswith('## ') or s.startswith('# '):
            if current_text:
                body = '\n'.join(current_text).strip()
                if body:
                    sections.append({"type": "text", "body": body})
                current_text = []
            heading = re.sub(r'^#{1,2}\s+', '', s)
            sections.append({"type": "heading", "text": heading})
        elif s.startswith('### '):
            if current_text:
                body = '\n'.join(current_text).strip()
                if body:
                    sections.append({"type": "text", "body": body})
                current_text = []
            heading = s[4:].strip()
            sections.append({"type": "heading", "text": heading})
        else:
            current_text.append(line)

    if current_text:
        body = '\n'.join(current_text).strip()
        if body:
            sections.append({"type": "text", "body": body})

    return sections


def _sections_to_plain_text(sections: list) -> str:
    blocks = []
    for section in sections:
        if section["type"] == "heading":
            text = _clean_markdown_line(section["text"])
            if text:
                blocks.append(text)
            continue
        paragraphs = re.split(r'\n\s*\n', section["body"])
        for para in paragraphs:
            lines = []
            for line in para.split('\n'):
                line = _clean_markdown_line(line)
                if line:
                    lines.append(line)
            if lines:
                blocks.append('\n'.join(lines))
    return "\n\n".join(blocks).strip()


def _section_headings(sections: list) -> list[str]:
    headings = []
    for section in sections:
        if section["type"] == "heading":
            text = _clean_markdown_line(section["text"])
            if text and text not in headings:
                headings.append(text)
    return headings


def _iter_naver_blocks(sections: list):
    for section in sections:
        if section["type"] == "heading":
            text = _clean_markdown_line(section["text"])
            if text:
                yield "heading", text
            continue
        paragraphs = re.split(r'\n\s*\n', section["body"])
        for para in paragraphs:
            lines = [_clean_markdown_line(line) for line in para.split('\n')]
            lines = [line for line in lines if line]
            if lines:
                yield "text", "\n".join(lines)


def _clean_markdown_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if re.fullmatch(r"[-*_]{3,}", line):
        return ""
    if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line):
        return ""
    line = re.sub(r"^#{1,6}\s+", "", line)
    line = re.sub(r"^[-*+]\s+", "", line)
    line = re.sub(r"^>\s*", "", line)
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"__([^_]+?)__", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", line)
    return line.strip()


# ─── 헬퍼 ────────────────────────────────────────────────────────

def _chunked_type(page, text: str, chunk: int = 40, delay: int = 130):
    for i in range(0, len(text), chunk):
        page.keyboard.type(text[i:i + chunk], delay=delay)
        time.sleep(random.uniform(0.1, 0.25))


def _paste_text(page, text: str):
    subprocess.run(["pbcopy"], input=text, text=True, check=False)
    page.keyboard.press("Meta+v")
    time.sleep(random.uniform(0.2, 0.4))


def _type_text(page, text: str, chunk: int = 60, delay: int = 35):
    for i in range(0, len(text), chunk):
        page.keyboard.type(text[i:i + chunk], delay=delay)
        time.sleep(random.uniform(0.05, 0.15))


def _press_enter(page, count: int = 1):
    for _ in range(count):
        page.keyboard.press("Enter")
        time.sleep(random.uniform(0.15, 0.3))


def _move_to_body_end(page) -> bool:
    try:
        moved = page.evaluate(r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
            };
            const paragraphs = Array.from(document.querySelectorAll(
                '.se-content .se-component.se-text .se-text-paragraph, .se-content .se-component.se-text [contenteditable="true"]'
            )).filter(el => visible(el) && !el.closest('.se-documentTitle'));
            const target = paragraphs[paragraphs.length - 1];
            if (!target) return false;
            target.scrollIntoView({block: 'center'});
            target.click();
            const range = document.createRange();
            range.selectNodeContents(target);
            range.collapse(false);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            return true;
        }""")
        if moved:
            time.sleep(0.2)
            return True
    except Exception:
        pass
    return _focus_body(page)


def _new_paragraph_at_end(page, count: int = 1):
    _move_to_body_end(page)
    page.keyboard.press("End")
    for _ in range(count):
        page.keyboard.press("Enter")
        time.sleep(random.uniform(0.15, 0.3))


def _dismiss_overlays(page, on_log=None):
    """팝업/도움말 닫기.

    Naver's restored-draft popup asks whether to continue writing. Choosing
    confirm loads the old draft and causes overlapping text, so that popup must
    be cancelled.
    """
    try:
        result = page.evaluate(r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
            };
            const dialogs = Array.from(document.querySelectorAll(
                '.se-popup, .se-popup-alert, .se-popup-alert-confirm, [role="dialog"]'
            )).filter(visible);
            for (const dialog of dialogs) {
                const text = (dialog.innerText || dialog.textContent || '').replace(/\s+/g, ' ').trim();
                const buttons = Array.from(dialog.querySelectorAll('button, a, [role="button"]')).filter(visible);
                const isContinueDraft = /작성\s*중|이어서\s*작성|임시\s*저장|작성중/.test(text);
                if (isContinueDraft) {
                    const cancel = buttons.find(btn => /취소|아니오|새\s*글|새로\s*작성/.test((btn.innerText || btn.textContent || '').trim())) || buttons[0];
                    if (cancel) {
                        cancel.click();
                        return {clicked: true, action: 'cancel_continue_draft', text};
                    }
                }
                const close = buttons.find(btn => /닫기|취소|확인/.test((btn.innerText || btn.textContent || '').trim()));
                if (close) {
                    close.click();
                    return {clicked: true, action: 'close_popup', text};
                }
            }
            const help = document.querySelector('button.se-help-panel-close-button');
            if (visible(help)) {
                help.click();
                return {clicked: true, action: 'close_help', text: ''};
            }
            return {clicked: false, action: '', text: ''};
        }""")
        if on_log and result.get("clicked"):
            on_log(f"[네이버팝업] {result.get('action')}: {result.get('text', '')[:120]}")
        time.sleep(0.5)
        return result
    except Exception:
        pass
    return {"clicked": False, "action": "", "text": ""}


def _apply_heading_format(page) -> bool:
    """현재 커서 줄에 SE3 소제목(sectionTitle) 서식 적용"""
    fmt_btn = None
    for sel in [
        '.se-text-format-toolbar-button',
        'button[data-name="text-format"]',
        'button[class*="text-format"]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                fmt_btn = el
                break
        except Exception:
            pass

    if not fmt_btn:
        return False

    for _ in range(3):
        try:
            fmt_btn.click()
            time.sleep(0.7)
            for sub_sel in [
                'button.se-toolbar-option-text-format-sectionTitle-button',
                'button[data-type="sectionTitle"]',
                'button[class*="sectionTitle"]',
            ]:
                sub = page.query_selector(sub_sel)
                if sub and sub.is_visible():
                    sub.click()
                    time.sleep(0.4)
                    return True
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            time.sleep(0.3)
    return False


def _apply_heading_formats(page, headings: list[str], on_log=None) -> int:
    try:
        applied = page.evaluate(r"""(headings) => {
            const clean = s => (s || '').replace(/\s+/g, ' ').trim();
            const headingSet = new Set(headings.map(clean).filter(Boolean));
            let count = 0;
            const paragraphs = Array.from(document.querySelectorAll('.se-content .se-text-paragraph'))
                .filter(paragraph => !paragraph.closest('.se-documentTitle'));
            for (const paragraph of paragraphs) {
                const component = paragraph.closest('.se-component');
                if (component) {
                    component.classList.remove('se-sectionTitle');
                    component.classList.add('se-text');
                }
                paragraph.style.lineHeight = '1.8';
                for (const span of paragraph.querySelectorAll('span')) {
                    for (const cls of Array.from(span.classList)) {
                        if (/^se-fs\d+$/.test(cls)) span.classList.remove(cls);
                    }
                    span.classList.add('se-fs19');
                }
            }
            for (const paragraph of paragraphs) {
                if (paragraph.closest('.se-documentTitle')) continue;
                const text = clean(paragraph.innerText || paragraph.textContent || '');
                const component = paragraph.closest('.se-component');
                if (!component) continue;
                const isHeading = headingSet.has(text);
                if (isHeading) {
                    component.classList.remove('se-text');
                    component.classList.add('se-sectionTitle');
                    paragraph.style.lineHeight = '1.5';
                    for (const span of paragraph.querySelectorAll('span')) {
                        for (const cls of Array.from(span.classList)) {
                            if (/^se-fs\d+$/.test(cls)) span.classList.remove(cls);
                        }
                        span.classList.add('se-fs30');
                    }
                    count += 1;
                }
            }
            return count;
        }""", headings)
        for heading in headings:
            _log(on_log, f"[네이버] 소제목 서식 대상: {heading}")
        _log(on_log, f"[네이버] 소제목 서식 적용 {applied}개")
        return int(applied or 0)
    except Exception as exc:
        _log(on_log, f"[네이버] 소제목 서식 오류: {exc}")
        return 0


def _upload_image(page, filepath: str) -> bool:
    """네이버 에디터에 이미지 업로드"""
    if not Path(filepath).exists():
        return False

    _dismiss_overlays(page)
    photo_btn = page.query_selector('button[data-name="image"]')
    if not photo_btn:
        photo_btn = page.query_selector('.se-image-toolbar-button')
    if not photo_btn:
        return False

    try:
        img_count_before = page.evaluate(
            "() => document.querySelectorAll('.se-component.se-image').length"
        )
        with page.expect_file_chooser(timeout=8000) as fc_info:
            photo_btn.click(timeout=5000)
        fc_info.value.set_files(filepath)
        time.sleep(4)

        img_count_after = page.evaluate(
            "() => document.querySelectorAll('.se-component.se-image').length"
        )
        if img_count_after <= img_count_before:
            # 패널 확인 버튼 클릭 시도
            page.evaluate("""() => {
                const sels = ['.se-popup-button-confirm', 'button.confirm', '.btn-confirm'];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el && el.offsetParent !== null) { el.click(); return; }
                }
            }""")
            time.sleep(2)

        page.keyboard.press("Escape")
        time.sleep(0.3)

        # 이미지 뒤 커서 복귀
        _clear_image_caption_placeholders(page)
        body_ps = page.query_selector_all(".se-component.se-text .se-text-paragraph")
        if body_ps:
            body_ps[-1].click()
            time.sleep(0.3)

        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _clear_image_caption_placeholders(page):
    try:
        page.evaluate(r"""() => {
            for (const el of document.querySelectorAll(
                '.se-caption, .se-module-image-caption, [class*="caption"], .se-component.se-image .se-text-paragraph, .se-component.se-image span'
            )) {
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                if (text === '사진 설명을 입력하세요' || text === '사진 설명을 입력하세요.' || text === '설명을 입력하세요') {
                    el.innerHTML = '';
                    el.textContent = '';
                }
            }
        }""")
    except Exception:
        pass


def _body_text(page) -> str:
    try:
        text = page.evaluate(r"""() => Array.from(document.querySelectorAll(
                '.se-content .se-component.se-text .se-text-paragraph, .se-content .se-component.se-text [contenteditable="true"]'
            ))
            .filter(el => !el.closest('.se-documentTitle'))
            .map(el => el.innerText || el.textContent || '')
            .join('\n')""")
        return _clean_editor_text(text)
    except Exception:
        return ""


def _guard_body_text(page) -> str:
    """Broader real-content check before typing.

    _body_text is intentionally narrow for blank-editor detection. This one is
    broader and is used to prevent appending into a restored draft.
    """
    try:
        text = page.evaluate(r"""() => {
            const clone = document.querySelector('.se-content')?.cloneNode(true);
            if (!clone) return '';
            for (const sel of [
                '.se-documentTitle', '.se-toolbar', '.se-popup', '[role="dialog"]',
                '.se-help-panel', '.se-side-toolbar', '.se-floating-toolbar'
            ]) {
                for (const el of clone.querySelectorAll(sel)) el.remove();
            }
            return clone.innerText || clone.textContent || '';
        }""")
        return _clean_editor_text(text)
    except Exception:
        return ""


def _clean_editor_text(text: str) -> str:
    text = (text or "").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    placeholders = [
        "본문에 #을 입력하면",
        "본문에 #을 입력",
        "본문 추가",
        "추가할 컴포넌트를 선택하세요",
        "사진, 동영상",
        "사진",
        "동영상",
        "스티커, 인용구",
        "스티커",
        "구분선",
        "인용구",
        "장소 등을 추가",
        "장소",
        "내용을 입력하세요",
        "본문을 입력하세요",
        "텍스트를 입력하세요",
        "나를 돌아보는 회고",
        "뜻밖의 발견을 기다립니다",
        "#모두의회고",
    ]
    for placeholder in placeholders:
        text = text.replace(placeholder, "")
    text = re.sub(r"구분선\d+", "", text)
    text = re.sub(r"인용구\d+", "", text)
    text = re.sub(r"사진\d+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[\s,./|·ㆍ:;\-]+|[\s,./|·ㆍ:;\-]+$", "", text).strip()
    if not re.search(r"[가-힣A-Za-z0-9]", text):
        return ""
    if re.fullmatch(r"[0-9\s,./|·ㆍ:;\-]+", text):
        return ""
    return "" if text in {"제목", "본문"} else text


def _focus_body(page) -> bool:
    try:
        focused = page.evaluate(r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
            };
            const candidates = Array.from(document.querySelectorAll(
                '.se-content .se-component.se-text .se-text-paragraph, .se-content [contenteditable="true"]'
            )).filter(el => visible(el) && !el.closest('.se-documentTitle'));
            const target = candidates[candidates.length - 1];
            if (!target) return false;
            target.scrollIntoView({block: 'center'});
            target.click();
            return true;
        }""")
        if focused:
            time.sleep(0.3)
            return _is_body_focused(page)
    except Exception:
        pass

    try:
        inserted = page.evaluate(r"""() => {
            const content = document.querySelector('.se-content');
            if (!content) return false;
            const title = document.querySelector('.se-documentTitle');
            const titleBox = title ? title.getBoundingClientRect() : null;
            const x = Math.max(80, window.innerWidth / 2);
            const y = titleBox ? Math.min(window.innerHeight - 100, titleBox.bottom + 120) : 300;
            const el = document.elementFromPoint(x, y);
            if (el) {
                const clickable = el.closest('.se-component.se-text, [contenteditable="true"], .se-content') || el;
                clickable.click();
                return true;
            }
            return false;
        }""")
        if inserted:
            time.sleep(0.3)
            if _is_body_focused(page):
                return True
    except Exception:
        pass

    # Fallback: click under the title and use Tab to move into the body editor.
    try:
        title = page.query_selector('.se-documentTitle')
        if title:
            box = title.bounding_box()
            if box:
                page.mouse.click(box["x"] + min(box["width"] / 2, 300), box["y"] + box["height"] + 80)
                time.sleep(0.3)
                if _is_body_focused(page):
                    return True
                page.keyboard.press("Tab")
                time.sleep(0.3)
                return _is_body_focused(page)
    except Exception:
        pass
    return False


def _is_body_focused(page) -> bool:
    try:
        return bool(page.evaluate("""() => {
            const sel = window.getSelection();
            const node = sel && sel.focusNode ? (sel.focusNode.nodeType === 1 ? sel.focusNode : sel.focusNode.parentElement) : document.activeElement;
            return !!(node && node.closest('.se-component.se-text') && !node.closest('.se-documentTitle'));
        }"""))
    except Exception:
        return False


def _click_body_area_by_position(page) -> bool:
    try:
        title = page.query_selector('.se-documentTitle')
        box = title.bounding_box() if title else None
        if box:
            x = box["x"] + min(max(box["width"] / 2, 160), 520)
            y = box["y"] + box["height"] + 120
        else:
            viewport = page.viewport_size or {"width": 1200, "height": 800}
            x = viewport["width"] / 2
            y = 320
        page.mouse.click(x, y)
        time.sleep(0.3)
        return True
    except Exception:
        return False


def _clear_body(page, on_log=None) -> bool:
    """Clear SmartEditor body before typing a new post.

    Naver often restores the previous draft in one text component. The old code
    only cleared when multiple components existed, which caused new text to be
    appended to an existing draft.
    """
    popup = _dismiss_overlays(page, on_log=on_log)
    if popup.get("action") == "cancel_continue_draft":
        # Cancelling Naver's restored-draft popup should open a blank editor.
        # Give the editor time to remove restored content before checking.
        time.sleep(1)
        if _is_body_empty(page) and len(_guard_body_text(page)) <= 5:
            return True
    if _is_body_empty(page) and len(_guard_body_text(page)) <= 5:
        return True
    for _ in range(5):
        if not _focus_body(page):
            _click_body_area_by_position(page)
        page.keyboard.press("Meta+a")
        time.sleep(0.2)
        page.keyboard.press("Meta+a")
        time.sleep(0.2)
        page.keyboard.press("Delete")
        time.sleep(0.8)
        if _is_body_empty(page) and len(_guard_body_text(page)) <= 5:
            _focus_body(page)
            return True

    # Last resort: use the editor's contenteditable nodes, then verify. If this
    # does not take, abort instead of writing over an old draft.
    try:
        page.evaluate("""() => {
            for (const el of document.querySelectorAll('.se-component.se-text [contenteditable="true"], .se-component.se-text .se-text-paragraph')) {
                el.innerHTML = '<span></span>';
                el.textContent = '';
            }
            for (const el of document.querySelectorAll('.se-component.se-image, .se-component.se-horizontalLine, .se-component.se-oglink')) {
                el.remove();
            }
        }""")
        time.sleep(0.8)
    except Exception:
        pass
    if _is_body_empty(page) and len(_guard_body_text(page)) <= 5:
        _focus_body(page)
        return True
    return False


def _title_text(page) -> str:
    try:
        text = page.evaluate(r"""() => {
            const el = document.querySelector('.se-documentTitle');
            return el ? (el.innerText || el.textContent || '') : '';
        }""")
        text = _clean_editor_text(text)
        return "" if text in {"제목", "제목을 입력하세요"} else text
    except Exception:
        return ""


def _is_body_empty(page) -> bool:
    return len(_body_text(page)) <= 5


def _click_new_write_if_available(page, on_log=None) -> bool:
    """Open a fresh editor if Naver restored an existing draft.

    Naver changes class names often, so this uses visible text first and keeps
    the selector intentionally broad. If no new-write button exists, callers can
    fall back to verified body clearing.
    """
    popup = _dismiss_overlays(page, on_log=on_log)
    if popup.get("action") == "cancel_continue_draft":
        time.sleep(1)
        return True
    labels = ("새 글쓰기", "새글쓰기", "새 글 쓰기", "새로 작성", "새 글 작성")
    try:
        clicked = page.evaluate(r"""(labels) => {
            const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], span'));
            for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
                if (!text || !labels.some(label => text.includes(label))) continue;
                const rect = node.getBoundingClientRect();
                const visible = rect.width > 0 && rect.height > 0 && window.getComputedStyle(node).visibility !== 'hidden';
                if (!visible) continue;
                const clickable = node.closest('button, a, [role="button"]') || node;
                clickable.click();
                return true;
            }
            return false;
        }""", list(labels))
    except Exception:
        clicked = False
    if not clicked:
        return False

    time.sleep(1.5)
    _dismiss_overlays(page, on_log=on_log)
    try:
        page.wait_for_selector(".se-content", timeout=30000)
    except Exception:
        pass
    time.sleep(1)
    return True


def _ensure_fresh_editor(page, on_log=None) -> bool:
    if _click_new_write_if_available(page, on_log=on_log):
        # A successful new-write click or cancelling the restored-draft popup
        # means Naver is on the new post flow. Body focus is checked later.
        return True
    return _clear_body(page, on_log=on_log)


def _wait_for_editor_after_login(page, editor_url: str, on_log=None, timeout: int = 300) -> bool:
    """Wait until SmartEditor is available, helping the user-driven login flow.

    If Naver shows the login page, the user signs in manually in the same Chrome
    window. After login, Naver may stay on a non-editor page, so we periodically
    navigate back to the postwrite URL instead of waiting forever on the old page.
    """
    deadline = time.time() + timeout
    last_log = 0.0
    retried_postwrite = False
    while time.time() < deadline:
        try:
            if page.query_selector(".se-content"):
                return True
        except Exception:
            pass

        url = page.url
        now = time.time()
        if now - last_log > 15:
            _log(on_log, f"[네이버] 에디터 대기 중: {url[:100]}")
            last_log = now

        if _looks_like_login_url(url):
            _log(on_log, "[네이버] 로그인 화면입니다. Chrome 창에서 직접 로그인하세요 (캡챠가 뜨면 풀어주세요).")
            try:
                page.bring_to_front()
            except Exception:
                pass
            time.sleep(5)
            continue

        has_editor = False
        try:
            has_editor = bool(page.query_selector(".se-content"))
        except Exception:
            has_editor = False
        if not retried_postwrite and not has_editor:
            try:
                _log(on_log, "[네이버] 로그인 후 글쓰기 화면으로 다시 이동합니다.")
                page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
                retried_postwrite = True
                time.sleep(2)
                continue
            except Exception as exc:
                _log(on_log, f"[네이버] 글쓰기 재이동 실패: {exc}")
        time.sleep(2)
    return False


def _open_editor(page, blog_id: str, on_log=None) -> bool:
    urls = [
        f"https://blog.naver.com/{blog_id}/postwrite",
        f"https://blog.naver.com/{blog_id}/postwrite?categoryNo=8",
    ]
    for url in urls:
        try:
            _log(on_log, f"[네이버] 글쓰기 URL 이동: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            if page.query_selector(".se-content"):
                return True
        except Exception as exc:
            _log(on_log, f"[네이버] 글쓰기 URL 이동 실패: {exc}")

    write_url = _find_write_url_in_frames(page, blog_id)
    if write_url:
        try:
            _log(on_log, f"[네이버] iframe 글쓰기 링크 이동: {write_url}")
            page.goto(write_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            return bool(page.query_selector(".se-content"))
        except Exception as exc:
            _log(on_log, f"[네이버] iframe 글쓰기 링크 이동 실패: {exc}")
    return False


def _find_write_url_in_frames(page, blog_id: str) -> str:
    pattern = f"/{blog_id}/postwrite"
    for frame in page.frames:
        try:
            urls = frame.evaluate(r"""() => Array.from(document.querySelectorAll('a'))
                .map(a => ({text: (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim(), href: a.href || ''}))
                .filter(x => x.href && (x.href.includes('/postwrite') || x.text.includes('글쓰기')))
                .map(x => x.href)""")
        except Exception:
            continue
        for url in urls:
            if pattern in url:
                return url
    return ""


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return "nid.naver.com" in lowered or "login" in lowered


def _log(on_log, message: str) -> None:
    if on_log:
        on_log(message)


# ─── 메인 함수 ───────────────────────────────────────────────────

def post_naver(blog_id: str, title: str, content_markdown: str,
               tags: list = None, image_paths: list = None,
               category: str = "", status: str = "draft", on_log=None) -> dict:
    """네이버 블로그 임시저장(draft) 또는 발행(publish)

    blog_id: 네이버 블로그 아이디 (예: salim1su)
    status: "draft" → 임시저장, "publish" → 발행
    Returns: {"ok": bool, "url": str, "error": str}
    """
    tags = tags or []
    image_paths = [p for p in (image_paths or []) if Path(p).exists()]
    sections = _parse_sections(content_markdown)

    pw, browser = connect()
    page = None
    try:
        editor_url = f"https://blog.naver.com/{blog_id}/postwrite"
        page = get_page(browser, navigate_to=None)
        _open_editor(page, blog_id, on_log=on_log)
        time.sleep(random.uniform(3, 5))

        current_url = page.url
        if "about:blank" in current_url:
            return {"ok": False, "error": "페이지 이동 실패 (about:blank) — Chrome CDP 연결 상태를 확인하세요."}

        # 에디터 로드 대기 — 로그인 필요 시 Chrome에서 로그인하면 글쓰기 화면으로 재이동
        if not _wait_for_editor_after_login(page, editor_url, on_log=on_log, timeout=300):
            return {"ok": False, "error": f"에디터 로드 실패 (5분 초과) — 현재 URL: {page.url}"}
        time.sleep(1)
        initial_popup = _dismiss_overlays(page, on_log=on_log)

        # 네이버가 이전 임시글을 복원하면 먼저 새 글쓰기 화면으로 전환한다.
        if initial_popup.get("action") == "cancel_continue_draft":
            _log(on_log, "[네이버] 이어쓰기 팝업을 취소했으므로 새 글 화면으로 계속 진행합니다.")
            time.sleep(1.5)
        elif not _ensure_fresh_editor(page, on_log=on_log):
            existing = _body_text(page)[:120] or _title_text(page)[:120]
            _log(
                on_log,
                "[네이버] 새 글 화면 확인이 애매하지만 사용자가 볼 수 있는 에디터에서 계속 진행합니다. "
                f"감지된 내용: {existing[:80]}",
            )

        existing_body = _guard_body_text(page)
        if len(existing_body) > 5:
            _log(on_log, f"[네이버] 기존 본문 감지, 작성 전 초기화 시도: {existing_body[:80]}")
            if not _clear_body(page, on_log=on_log):
                existing_body = _guard_body_text(page)
                return {
                    "ok": False,
                    "error": "네이버 에디터에 기존 본문이 남아 있어 중단했습니다. "
                             f"남은 내용: {existing_body[:120]}",
                }

        # ── 제목 입력 ──
        clean_title = title.split('\n')[0].strip()
        title_sel = ".se-documentTitle .se-text-paragraph"
        page.wait_for_selector(title_sel, timeout=10000)
        title_el = page.query_selector(title_sel)
        title_el.click()
        time.sleep(0.5)
        page.keyboard.press("Meta+a")
        time.sleep(0.1)
        page.keyboard.press("Delete")
        time.sleep(0.3)
        _paste_text(page, clean_title)
        time.sleep(0.5)

        # ── 본문 영역 포커스 ──
        if not _focus_body(page):
            _log(on_log, "[네이버] 본문 포커스 자동 확인 실패 — 제목 아래 영역 클릭 후 계속 진행합니다.")
            _click_body_area_by_position(page)
        time.sleep(random.uniform(0.5, 1.0))

        # ── 본문 입력 + 이미지 분산 삽입 ──
        # 이미지를 맨 끝에 몰지 않고 본문 블록 사이에 균등하게 배치한다.
        # n_images개 이미지를 n_blocks개 블록에 분산: i번째 이미지는
        # (i+1)*n_blocks/(n_images+1) 위치의 블록 뒤에 삽입.
        blocks = list(_iter_naver_blocks(sections))
        n_blocks = len(blocks)
        n_images = len(image_paths)

        # 블록 인덱스 → 삽입할 이미지 경로 목록 매핑
        image_insert_map: dict[int, list[str]] = {}
        if n_images > 0 and n_blocks > 0:
            for img_idx, img_path in enumerate(image_paths):
                block_pos = int(round((img_idx + 1) * n_blocks / (n_images + 1))) - 1
                block_pos = max(0, min(block_pos, n_blocks - 1))
                image_insert_map.setdefault(block_pos, []).append(img_path)

        if n_images > 0:
            _log(on_log, f"[네이버] 카드 이미지 {n_images}장을 본문 사이에 분산 삽입합니다.")

        uploaded = 0
        for index, (block_type, text) in enumerate(blocks):
            if index > 0:
                _press_enter(page, 1)
            _type_text(page, text)
            if block_type == "heading":
                time.sleep(0.2)
                if _apply_heading_format(page):
                    _log(on_log, f"[네이버] 소제목 서식 적용: {text}")
                else:
                    _log(on_log, f"[네이버] 소제목 서식 적용 실패: {text}")
            _press_enter(page, 1)

            for img_path in image_insert_map.get(index, []):
                if _upload_image(page, img_path):
                    uploaded += 1
                    _log(on_log, f"[네이버] 이미지 삽입 완료 [{uploaded}/{n_images}]: {img_path.split('/')[-1]}")
                    _press_enter(page, 1)
                else:
                    _log(on_log, f"[네이버] 이미지 삽입 실패: {img_path.split('/')[-1]}")

        if n_images > 0:
            _log(on_log, f"[네이버] 카드 이미지 삽입 결과: {uploaded}/{n_images}장")

        time.sleep(random.uniform(1.0, 2.0))

        # ── 발행 패널 열기 ──
        if status == "publish":
            panel_opened = False

            # 방법 1: 알려진 class명 시도 (네이버가 자주 바꿈)
            for sel in [
                '.publish_btn__m9KHH',
                'button[class*="publish_btn"]',
                'button[class*="publishBtn"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=1500):
                        btn.click()
                        panel_opened = True
                        break
                except Exception:
                    pass

            # 방법 2: 화면 상단 '발행' 텍스트 버튼 (class명 무관)
            if not panel_opened:
                try:
                    panel_opened = bool(page.evaluate("""() => {
                        for (const btn of document.querySelectorAll('button')) {
                            const t = (btn.textContent || '').trim();
                            if (t !== '발행') continue;
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && rect.top < 200) {
                                btn.click(); return true;
                            }
                        }
                        return false;
                    }"""))
                except Exception:
                    pass

            _log(on_log, "[네이버] 발행 패널 열림" if panel_opened else "[네이버] 발행 패널 열기 실패 — 직접 발행 버튼 탐색으로 진행")
            time.sleep(random.uniform(1.5, 2.5))

            # 카테고리
            if category:
                try:
                    cat_btn = page.locator('.selectbox_button__jb1Dt')
                    if cat_btn.is_visible(timeout=2000):
                        cat_btn.click()
                        time.sleep(1)
                        items = page.locator('.item__sAGX9')
                        for i in range(items.count()):
                            item = items.nth(i)
                            if category in (item.text_content() or ""):
                                item.click()
                                time.sleep(0.5)
                                break
                except Exception:
                    pass

            # 태그
            if tags:
                try:
                    tag_input = page.locator('#tag-input')
                    if tag_input.is_visible(timeout=2000):
                        for tag in tags[:10]:
                            tag_input.click()
                            time.sleep(0.2)
                            tag_input.fill(tag.strip())
                            page.keyboard.press("Enter")
                            time.sleep(random.uniform(0.5, 1.0))
                except Exception:
                    pass

        # ── 임시저장 또는 발행 ──
        time.sleep(random.uniform(1.0, 1.5))

        if status == "publish":
            # 패널 안의 '발행' 버튼 클릭
            # 툴바 '발행' 버튼은 화면 최상단(top < 100)에 있고,
            # 패널 안의 '발행' 버튼은 패널 내부(top > 200)에 위치한다.
            saved = page.evaluate("""() => {
                const PANEL_MARKER = '발행 설정 닫기';
                const CONFIRM_TEXTS = ['발행', '공개발행', '발행하기'];

                const panelOpen = Array.from(document.querySelectorAll('button'))
                    .some(b => (b.textContent || '').trim() === PANEL_MARKER);
                if (!panelOpen) return 'PANEL_NOT_OPEN';

                // top > 200 으로 툴바 버튼(top < 100)과 패널 버튼 구분
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim();
                    if (!CONFIRM_TEXTS.includes(t)) continue;
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && rect.top > 200) {
                        btn.click(); return t;
                    }
                }
                return false;
            }""")
            if saved == 'PANEL_NOT_OPEN':
                _log(on_log, "[네이버] 발행 패널이 열리지 않았습니다.")
                saved = False
            elif not saved:
                _log(on_log, "[네이버] 패널 내 발행 버튼을 찾지 못했습니다.")
            else:
                _log(on_log, f"[네이버] 패널 내 발행 버튼 클릭: '{saved}'")
        else:
            # 임시저장
            saved = page.evaluate("""() => {
                for (const btn of document.querySelectorAll('button')) {
                    const t = btn.textContent.trim();
                    if (t === '발행' || t === '공개발행' || t === '발행하기') continue;
                    if (t === '임시저장' || t.includes('저장')) {
                        btn.click(); return t;
                    }
                }
                return false;
            }""")
            if not saved:
                try:
                    save_btn = page.locator('.save_btn__bzc5B')
                    if save_btn.is_visible(timeout=2000):
                        save_btn.click()
                        saved = True
                except Exception:
                    pass
            if not saved:
                page.keyboard.press("Meta+s")

        if status == "publish":
            # 발행 후 URL이 /postwrite에서 벗어날 때까지 대기 (최대 15초)
            try:
                page.wait_for_url(lambda url: "postwrite" not in url, timeout=15000)
                _log(on_log, f"[네이버] 발행 완료, 이동된 URL: {page.url}")
            except Exception:
                _log(on_log, f"[네이버] URL 변경 대기 초과 — 현재 URL: {page.url}")
        else:
            time.sleep(random.uniform(2.0, 3.0))

        label = "발행" if status == "publish" else "임시저장"
        return {"ok": True, "url": page.url, "label": label}

    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        _log(on_log, f"[네이버오류] {error}\n{traceback.format_exc()}")
        try:
            if page and not page.is_closed():
                page.close()
        except Exception:
            pass
        return {"ok": False, "error": error}
    finally:
        try:
            pw.stop()
        except Exception:
            pass
