"""네이버 블로그 발행 — CDP + playwright-stealth (SmartEditor3)"""
import html
import re
import subprocess
import time
import random
import traceback
from pathlib import Path

from .browser import connect, get_page
from .accounts import close_chrome, ensure_chrome_for, login_account_id
from .login import ensure_naver_login, logout_after_post_enabled, logout_naver


# ─── 마크다운 파싱 ──────────────────────────────────────────────

def _parse_custom_table(table_lines: list[str]) -> list[list[str]]:
    """표 N x M 시작/(r,c) 내용/표 N x M 끝 → 2D 리스트로 파싱."""
    cells: dict[tuple[int, int], str] = {}
    max_r = max_c = 0
    for line in table_lines:
        m = re.match(r"\((\d+),(\d+)\)\s*(.*)", line.strip())
        if m:
            r, c, text = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            cells[(r, c)] = text
            max_r = max(max_r, r)
            max_c = max(max_c, c)
    if not cells:
        return []
    return [
        [cells.get((r, c), "") for c in range(max_c + 1)]
        for r in range(max_r + 1)
    ]


def _parse_sections(markdown: str) -> list:
    """마크다운 + ㅂㅂㅂ소제목 + 표 N x M 형식을 섹션 리스트로 분리."""
    sections = []
    current_text: list[str] = []
    in_table = False
    table_lines: list[str] = []

    def flush_text():
        body = '\n'.join(current_text).strip()
        if body:
            sections.append({"type": "text", "body": body})
        current_text.clear()

    for line in markdown.split('\n'):
        s = line.strip()

        if re.match(r"표\s*\d+\s*x\s*\d+\s*시작", s):
            flush_text()
            in_table = True
            table_lines = []
            continue

        if re.match(r"표\s*\d+\s*x\s*\d+\s*끝", s):
            in_table = False
            rows = _parse_custom_table(table_lines)
            if rows:
                sections.append({"type": "table", "rows": rows})
            table_lines = []
            continue

        if in_table:
            table_lines.append(s)
            continue

        if s.startswith('ㅂㅂㅂ'):
            flush_text()
            sections.append({"type": "heading", "text": s[3:].strip()})
            continue

        if s.startswith('## ') or s.startswith('# '):
            flush_text()
            sections.append({"type": "heading", "text": re.sub(r'^#{1,2}\s+', '', s)})
            continue
        if s.startswith('### '):
            flush_text()
            sections.append({"type": "heading", "text": s[4:].strip()})
            continue

        current_text.append(line)

    flush_text()
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


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _iter_naver_blocks(sections: list):
    for section in sections:
        if section["type"] == "heading":
            text = _clean_markdown_line(section["text"])
            if text:
                yield "heading", text
            continue
        if section["type"] == "table":
            yield "table", section["rows"]
            continue
        paragraphs = re.split(r'\n\s*\n', section["body"])
        for para in paragraphs:
            if _MARKDOWN_LINK_RE.search(para):
                html_body = _markdown_block_to_html(para)
                if html_body:
                    yield "html", html_body
                continue
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
    line = re.sub(r"^\d+\.\s+", "", line)
    line = re.sub(r"^[-*+]\s+", "", line)
    line = re.sub(r"^\[[ xX]\]\s*", "", line)
    line = re.sub(r"^>\s*", "", line)
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"__([^_]+?)__", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", line)
    return line.strip()


def _inline_markdown_to_html(text: str) -> str:
    text = html.escape(text.strip())
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+?)__", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)

    def repl(match):
        label = match.group(1)
        url = match.group(2)
        return f'<a href="{url}" target="_blank" rel="nofollow noopener">{label}</a>'

    return _MARKDOWN_LINK_RE.sub(repl, text)


def _markdown_block_to_html(markdown: str) -> str:
    lines = [line.strip() for line in markdown.split('\n')]
    html_lines: list[str] = []
    list_items: list[str] = []

    def flush_list():
        if list_items:
            html_lines.append(f"<ul>{''.join(list_items)}</ul>")
            list_items.clear()

    for line in lines:
        if not line:
            flush_list()
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            flush_list()
            html_lines.append("<hr>")
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            flush_list()
            html_lines.append(f"<p><strong>{_inline_markdown_to_html(heading.group(1))}</strong></p>")
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        if bullet:
            list_items.append(f"<li>{_inline_markdown_to_html(bullet.group(1))}</li>")
            continue
        flush_list()
        html_lines.append(f"<p>{_inline_markdown_to_html(line)}</p>")

    flush_list()
    return "".join(html_lines).strip()


def _html_to_plain_text(html_text: str) -> str:
    text = re.sub(r"<\s*li[^>]*>", "- ", html_text)
    text = re.sub(r"<\s*/\s*li\s*>", "\n", text)
    text = re.sub(r"<\s*/\s*(p|div|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 (\1)", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _links_from_html(html_text: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in re.finditer(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html_text, flags=re.I | re.S):
        url = html.unescape(match.group(1)).strip()
        label = re.sub(r"<[^>]+>", "", match.group(2))
        label = html.unescape(label).strip()
        if label and url.startswith("http"):
            links.append({"label": label, "url": url})
    return links


# ─── 헬퍼 ────────────────────────────────────────────────────────

def _chunked_type(page, text: str, chunk: int = 40, delay: int = 130):
    for i in range(0, len(text), chunk):
        page.keyboard.type(text[i:i + chunk], delay=delay)
        time.sleep(random.uniform(0.1, 0.25))


def _paste_text(page, text: str):
    subprocess.run(["pbcopy"], input=text, text=True, check=False)
    page.keyboard.press("Meta+v")
    time.sleep(random.uniform(0.2, 0.4))


def _paste_html(page, html_text: str) -> bool:
    plain_text = _html_to_plain_text(html_text)
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeHTML, NSPasteboardTypeString

        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(html_text, NSPasteboardTypeHTML)
        pasteboard.setString_forType_(plain_text, NSPasteboardTypeString)
    except Exception:
        _paste_text(page, plain_text)
        return False

    page.keyboard.press("Meta+v")
    time.sleep(random.uniform(0.35, 0.7))
    return True


def _linkify_pasted_block(page, html_text: str, on_log=None) -> int:
    links = _links_from_html(html_text)
    if not links:
        return 0
    try:
        count = page.evaluate(r"""(links) => {
            const content = document.querySelector('.se-content');
            if (!content) return 0;
            const paragraphs = Array.from(content.querySelectorAll(
                '.se-component.se-text .se-text-paragraph, .se-component.se-text [contenteditable="true"]'
            )).filter(el => !el.closest('.se-documentTitle')).reverse();

            const normalize = s => (s || '').replace(/\s+/g, ' ').trim();
            const textNodes = root => {
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                    acceptNode(node) {
                        if (!node.nodeValue || !normalize(node.nodeValue)) return NodeFilter.FILTER_REJECT;
                        if (node.parentElement && node.parentElement.closest('a')) return NodeFilter.FILTER_REJECT;
                        return NodeFilter.FILTER_ACCEPT;
                    }
                });
                const nodes = [];
                let node;
                while ((node = walker.nextNode())) nodes.push(node);
                return nodes;
            };

            const replaceOnce = (root, label, url) => {
                const existing = Array.from(root.querySelectorAll('a[href]')).find(a =>
                    normalize(a.textContent) === label && (a.href === url || a.getAttribute('href') === url)
                );
                if (existing) return true;

                for (const node of textNodes(root)) {
                    const value = node.nodeValue || '';
                    const idx = value.indexOf(label);
                    if (idx < 0) continue;

                    const before = value.slice(0, idx);
                    let after = value.slice(idx + label.length);
                    const visibleUrl = ` (${url})`;
                    if (after.startsWith(visibleUrl)) after = after.slice(visibleUrl.length);

                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.target = '_blank';
                    anchor.rel = 'nofollow noopener';
                    anchor.textContent = label;

                    const frag = document.createDocumentFragment();
                    if (before) frag.appendChild(document.createTextNode(before));
                    frag.appendChild(anchor);
                    if (after) frag.appendChild(document.createTextNode(after));
                    node.parentNode.replaceChild(frag, node);

                    const editable = root.closest('[contenteditable="true"]') || root.querySelector('[contenteditable="true"]') || root;
                    editable.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'formatSetBlockTextDirection'}));
                    editable.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                return false;
            };

            let linked = 0;
            for (const link of links) {
                const label = normalize(link.label);
                const url = link.url;
                if (!label || !url) continue;
                for (const paragraph of paragraphs) {
                    if (!normalize(paragraph.innerText || paragraph.textContent || '').includes(label)) continue;
                    if (replaceOnce(paragraph, label, url)) {
                        linked += 1;
                        break;
                    }
                }
            }
            return linked;
        }""", links)
        if count:
            _log(on_log, f"[네이버] 링크 후처리 완료: {count}개")
        return int(count or 0)
    except Exception as exc:
        _log(on_log, f"[네이버] 링크 후처리 실패: {exc}")
        return 0


def _select_pasted_link_text(page, label: str) -> bool:
    try:
        return bool(page.evaluate(r"""(label) => {
            const content = document.querySelector('.se-content');
            if (!content || !label) return false;
            const paragraphs = Array.from(content.querySelectorAll(
                '.se-component.se-text .se-text-paragraph, .se-component.se-text [contenteditable="true"]'
            )).filter(el => !el.closest('.se-documentTitle')).reverse();

            for (const paragraph of paragraphs) {
                const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT, {
                    acceptNode(node) {
                        if (!node.nodeValue || !node.nodeValue.includes(label)) return NodeFilter.FILTER_REJECT;
                        return NodeFilter.FILTER_ACCEPT;
                    }
                });
                let node;
                while ((node = walker.nextNode())) {
                    const idx = node.nodeValue.indexOf(label);
                    if (idx < 0) continue;
                    paragraph.scrollIntoView({block: 'center'});
                    const range = document.createRange();
                    range.setStart(node, idx);
                    range.setEnd(node, idx + label.length);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    const editable = paragraph.closest('[contenteditable="true"]') || paragraph.querySelector('[contenteditable="true"]') || paragraph;
                    editable.focus();
                    return true;
                }
            }
            return false;
        }""", label))
    except Exception:
        return False


def _click_link_toolbar_button(page) -> bool:
    selectors = [
        'button[data-name="link"]',
        'button[data-command="link"]',
        'button[class*="link"]',
        '.se-toolbar button:has-text("링크")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click()
                time.sleep(0.4)
                return True
        except Exception:
            pass
    try:
        clicked = page.evaluate(r"""() => {
            const visible = el => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
            };
            const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(visible);
            const btn = buttons.find(el => {
                const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.title || '').trim();
                const cls = el.className ? String(el.className) : '';
                return /링크|URL|url|link/i.test(text) || /link/i.test(cls);
            });
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        if clicked:
            time.sleep(0.4)
            return True
    except Exception:
        pass
    return False


def _fill_link_dialog(page, url: str) -> bool:
    input_selectors = [
        'input[type="url"]',
        'input[placeholder*="URL"]',
        'input[placeholder*="url"]',
        'input[placeholder*="링크"]',
        'input[class*="url"]',
        'input[class*="link"]',
        '.se-popup input',
        '[role="dialog"] input',
    ]
    for sel in input_selectors:
        try:
            locator = page.locator(sel).last
            if locator.is_visible(timeout=1200):
                locator.fill(url)
                time.sleep(0.2)
                break
        except Exception:
            continue
    else:
        return False

    try:
        confirmed = page.evaluate(r"""() => {
            const visible = el => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
            };
            const dialogs = Array.from(document.querySelectorAll('.se-popup, [role="dialog"], body')).filter(visible);
            for (const dialog of dialogs) {
                const buttons = Array.from(dialog.querySelectorAll('button, a, [role="button"]')).filter(visible);
                const ok = buttons.find(btn => /확인|적용|삽입|완료|저장|입력/.test((btn.innerText || btn.textContent || '').trim()));
                if (ok) {
                    ok.click();
                    return true;
                }
            }
            return false;
        }""")
        if not confirmed:
            page.keyboard.press("Enter")
        time.sleep(0.5)
        return True
    except Exception:
        try:
            page.keyboard.press("Enter")
            time.sleep(0.5)
            return True
        except Exception:
            return False


def _insert_links_with_editor_ui(page, html_text: str, on_log=None) -> int:
    links = _links_from_html(html_text)
    inserted = 0
    for link in links:
        label = link.get("label", "")
        url = link.get("url", "")
        if not label or not url:
            continue
        if not _select_pasted_link_text(page, label):
            _log(on_log, f"[네이버] 링크 텍스트 선택 실패: {label[:40]}")
            continue
        time.sleep(0.2)

        opened = False
        try:
            page.keyboard.press("Meta+k")
            time.sleep(0.5)
            opened = True
        except Exception:
            opened = False
        if not _fill_link_dialog(page, url):
            if not opened or _click_link_toolbar_button(page):
                if not _fill_link_dialog(page, url):
                    _log(on_log, f"[네이버] 링크 URL 입력 실패: {label[:40]}")
                    continue
            else:
                _log(on_log, f"[네이버] 링크 버튼 열기 실패: {label[:40]}")
                continue
        inserted += 1
        _close_editor_popups(page)
    if inserted:
        _log(on_log, f"[네이버] 에디터 링크 삽입 완료: {inserted}개")
    return inserted


def _close_editor_popups(page) -> None:
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            scope.evaluate(r"""() => {
                const visible = el => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
                };
                for (const btn of document.querySelectorAll('.se-popup-close-button, .se-popup-button-confirm, button[aria-label*="닫기"]')) {
                    if (visible(btn)) btn.click();
                }
                for (const popup of document.querySelectorAll('.se-popup, .se-popup-alert, .se-popup-dim, .se-popup-dim-white')) {
                    if (visible(popup)) popup.style.display = 'none';
                }
            }""")
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.2)
    except Exception:
        pass


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


def _table_shape(page) -> tuple[int, int]:
    """마지막 표 컴포넌트의 (행, 열) 개수."""
    shape = page.evaluate("""() => {
        const tables = document.querySelectorAll('.se-component.se-table, .se-table-component');
        const last = tables[tables.length - 1];
        if (!last) return [0, 0];
        const content = last.querySelector('.se-table-content') || last;
        const trs = Array.from(content.querySelectorAll('tr'));
        const cols = trs[0] ? trs[0].querySelectorAll('td').length : 0;
        return [trs.length, cols];
    }""")
    return tuple(shape)


def _click_first_table_cell(page) -> None:
    """마지막 표의 첫 셀을 클릭해 행/열 선택 상태를 초기화한다."""
    try:
        tables = page.query_selector_all(".se-component.se-table, .se-table-component")
        if not tables:
            return
        cell = tables[-1].query_selector("td")
        if cell:
            cell.click()
            time.sleep(0.3)
    except Exception:
        pass


def _click_axis_add(page, axis: str) -> bool:
    return page.evaluate(f"""() => {{
        const tables = document.querySelectorAll('.se-component.se-table, .se-table-component');
        const last = tables[tables.length - 1];
        if (!last) return false;
        const bar = last.querySelector('.se-cell-controlbar-{axis}');
        if (!bar) return false;
        bar.dispatchEvent(new MouseEvent('mouseenter', {{bubbles: true}}));
        bar.dispatchEvent(new MouseEvent('mouseover', {{bubbles: true}}));
        const items = bar.querySelectorAll('.se-cell-controlbar-item');
        if (!items.length) return false;
        const addBtn = items[items.length - 1].querySelector('.se-cell-add-button');
        if (!addBtn) return false;
        addBtn.click();
        return true;
    }}""")


def _click_axis_delete(page, axis: str) -> bool:
    # 선택과 삭제를 하나의 evaluate 호출 안에서 동기적으로 처리해야 한다.
    # 두 번의 page.evaluate 호출로 나누면 그 사이 재렌더링 때문에
    # "마지막" 삭제 버튼을 잘못 짚어 삭제가 조용히 실패할 수 있다
    # (실측으로 확인됨 — 별도 호출로 나누면 셀 수가 줄지 않았다).
    return page.evaluate(f"""() => {{
        const tables = document.querySelectorAll('.se-component.se-table, .se-table-component');
        const last = tables[tables.length - 1];
        if (!last) return false;
        const bar = last.querySelector('.se-cell-controlbar-{axis}');
        if (!bar) return false;
        const items = bar.querySelectorAll('.se-cell-controlbar-item');
        if (items.length <= 1) return false;
        items[items.length - 1].querySelector('.se-cell-select-button').click();
        const btns = Array.from(document.querySelectorAll('.se-context-menu-button-delete'));
        const onscreen = btns.filter(b => {{
            const r = b.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }});
        if (!onscreen.length) return false;
        onscreen[onscreen.length - 1].click();
        return true;
    }}""")


def _resize_table_axis(page, axis: str, delta: int, on_log=None) -> bool:
    """표의 마지막 행/열을 delta만큼 추가(양수)하거나 삭제(음수)한다.

    SmartEditor3는 표 버튼 클릭 시 크기 선택 팝업 없이 곧바로 기본 3x3 표를
    삽입하므로, 원하는 N x M 크기는 행/열 컨트롤바의 +(추가)/삭제 버튼으로
    맞춰야 한다 (실제 로그인 세션에서 확인).

    행/열 개수가 늘어나는(add) 반응은 클릭 직후 바로 반영되지 않고 비동기로
    조금 늦게 렌더링될 때가 있다 — 고정된 sleep 한 번만으로는 이 지연을
    실패로 오판해서 아직 진행 중인 조정을 중간에 포기하는 문제가 실측으로
    확인됨. 그래서 클릭 후에는 실제로 개수가 바뀔 때까지 짧게 폴링하고,
    반영이 안 되면 한 번 더 클릭을 재시도한다.
    """
    label = "행" if axis == "row" else "열"
    axis_index = 0 if axis == "row" else 1
    for _ in range(abs(delta)):
        before = _table_shape(page)[axis_index]
        expected = before + (1 if delta > 0 else -1)

        ok = _click_axis_add(page, axis) if delta > 0 else _click_axis_delete(page, axis)
        if not ok:
            _log(on_log, f"[네이버] 표 {label} {'추가' if delta > 0 else '삭제'} 실패")
            return False

        reached = False
        for _attempt in range(10):
            time.sleep(0.15)
            if _table_shape(page)[axis_index] == expected:
                reached = True
                break
        if not reached:
            # 반영이 늦어지는 경우를 대비해 한 번 더 시도
            ok = _click_axis_add(page, axis) if delta > 0 else _click_axis_delete(page, axis)
            for _attempt in range(10):
                time.sleep(0.15)
                if _table_shape(page)[axis_index] == expected:
                    reached = True
                    break
        if not reached:
            _log(on_log, f"[네이버] 표 {label} {'추가' if delta > 0 else '삭제'} 반영 확인 실패")
            return False
        time.sleep(0.35)
    return True


def _insert_naver_table(page, rows: list[list[str]], on_log=None) -> bool:
    """SmartEditor3에 실제 표 삽입: 표 버튼 클릭(기본 3x3 생성) → 행/열 크기 맞춤 → 셀별 클릭 입력.

    구버전 SmartEditor는 표 버튼 클릭 시 크기를 고르는 그리드 팝업을 보여줬지만,
    현재 버전은 팝업 없이 곧바로 기본 3x3 표를 삽입한다. 그래서 원하는 N x M
    크기는 행/열 컨트롤바의 +/삭제 버튼으로 별도 조정해야 하고, 셀 이동도
    Tab 키로는 동작하지 않아(모든 텍스트가 첫 셀에 누적됨) 셀을 직접 클릭해야
    한다. 모두 실제 로그인 세션에서 확인한 내용이다.
    """
    if not rows:
        return False
    n_rows = len(rows)
    n_cols = max(len(r) for r in rows)
    if n_rows == 0 or n_cols == 0:
        return False

    def _fallback_as_text():
        page.keyboard.press("Escape")
        for row in rows:
            _type_text(page, " | ".join(cell for cell in row))
            _press_enter(page, 1)

    def _remove_malformed_table_and_fallback():
        # 크기 조정에 실패한 불완전한 표(내용 없이 빈 셀만 있는 표)를 그대로
        # 남겨두면 빈 표만 덩그러니 남는다 — 텍스트로 대체하기 전에 방금
        # 만들다 만 표만 지운다 (이전에 이미 완성된 다른 표는 건드리지 않음).
        try:
            page.evaluate("""() => {
                const tables = document.querySelectorAll('.se-component.se-table, .se-table-component');
                const last = tables[tables.length - 1];
                if (last) last.remove();
            }""")
            time.sleep(0.2)
        except Exception:
            pass
        _fallback_as_text()

    _dismiss_overlays(page)
    table_btn = page.query_selector('button[data-name="table"]')
    if not table_btn:
        _log(on_log, "[네이버] 표 버튼을 찾을 수 없어 텍스트로 대체합니다.")
        _fallback_as_text()
        return False

    try:
        table_count_before = page.evaluate(
            "() => document.querySelectorAll('.se-component.se-table, .se-table-component').length"
        )
        table_btn.click(timeout=5000)
        time.sleep(0.6)

        # 표가 "새로" 삽입되었는지 확인 — 페이지 전체 표 개수(절대값)로는
        # 이전에 삽입된 다른 표 때문에 이번 시도의 실패를 성공으로 오판할 수 있어
        # 클릭 전/후 개수를 비교(delta)한다.
        table_count_after = page.evaluate(
            "() => document.querySelectorAll('.se-component.se-table, .se-table-component').length"
        )
        if table_count_after <= table_count_before:
            _log(on_log, "[네이버] 표 삽입 실패 — 텍스트로 대체합니다.")
            _fallback_as_text()
            return False

        default_rows, default_cols = _table_shape(page)
        _log(on_log, f"[네이버] 표 기본 크기 {default_rows}x{default_cols} → 목표 {n_rows}x{n_cols}로 조정")

        if not _resize_table_axis(page, "column", n_cols - default_cols, on_log=on_log):
            _remove_malformed_table_and_fallback()
            return False

        # 열 삭제/추가 직후에는 선택 상태가 남아 있어 행 컨트롤바의 hover
        # 인식이 실패할 수 있다 (실측으로 확인됨) — 첫 셀을 클릭해 선택
        # 상태를 초기화한 뒤 행 크기 조정으로 넘어간다.
        _click_first_table_cell(page)

        if not _resize_table_axis(page, "row", n_rows - default_rows, on_log=on_log):
            _remove_malformed_table_and_fallback()
            return False

        final_rows, final_cols = _table_shape(page)
        if (final_rows, final_cols) != (n_rows, n_cols):
            _log(on_log, f"[네이버] 표 크기 조정 결과 불일치 ({final_rows}x{final_cols}) — 텍스트로 대체합니다.")
            _remove_malformed_table_and_fallback()
            return False

        # 셀별로 직접 클릭 후 입력 — Tab 키로는 셀 간 포커스 이동이 되지 않고
        # 모든 텍스트가 첫 셀에 누적되는 현상이 실측으로 확인됨.
        tables = page.query_selector_all(".se-component.se-table, .se-table-component")
        if not tables:
            _fallback_as_text()
            return False
        cells = tables[-1].query_selector_all("td")
        idx = 0
        for row in rows:
            for cell_text in row:
                if idx >= len(cells):
                    break
                if cell_text:
                    cells[idx].click()
                    time.sleep(0.12)
                    _type_text(page, cell_text)
                idx += 1
        time.sleep(0.3)

        # 셀 입력 직후에는 표가 선택된 상태(초록 테두리 + 삭제 아이콘)로 남아
        # 있어 아래 좌표 클릭이 그 위에 뜬 부유 아이콘에 막힐 수 있다
        # (실측으로 확인됨) — Escape로 선택 상태를 먼저 해제한다.
        page.keyboard.press("Escape")
        time.sleep(0.2)

        # 표 밖으로 커서 이동: 표 아래 좌표를 마우스로 클릭
        table_rect = page.evaluate("""() => {
            const tables = document.querySelectorAll('.se-component.se-table, .se-table-component');
            const lastTable = tables[tables.length - 1];
            if (!lastTable) return null;
            const rect = lastTable.getBoundingClientRect();
            return {x: rect.left + rect.width / 2, y: rect.bottom + 50};
        }""")
        if table_rect:
            page.mouse.click(table_rect['x'], table_rect['y'])
            time.sleep(0.4)
        else:
            # 폴백: 표 안 문단 제외하고 마지막 텍스트 문단 클릭
            page.evaluate("""() => {
                const paras = Array.from(document.querySelectorAll(
                    '.se-content .se-component.se-text .se-text-paragraph'
                )).filter(p => !p.closest('.se-component.se-table'));
                const last = paras[paras.length - 1];
                if (!last) return;
                last.click();
                const range = document.createRange();
                range.selectNodeContents(last);
                range.collapse(false);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            }""")
            time.sleep(0.3)
        return True

    except Exception as exc:
        _log(on_log, f"[네이버] 표 삽입 오류: {exc}")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


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


def _insert_template(page, template_name: str, on_log=None) -> bool:
    """SmartEditor의 "내 템플릿"에 저장된 템플릿을 현재 커서 위치에 삽입한다.

    사용자가 SmartEditor에서 직접 만들어 저장한 템플릿(예: 실제 링크가 걸린
    제휴 쿠폰 배너 이미지)을 그대로 불러와 붙여넣는다 — 이 함수 자체는 어떤
    콘텐츠를 넣을지 전혀 판단하지 않고, 이름으로 지정된 저장된 템플릿을 그대로
    불러오기만 한다.
    """
    def editor_size() -> int:
        try:
            return int(page.evaluate(r"""() => {
                const content = document.querySelector('.se-content');
                return content ? (content.innerText || '').length + (content.innerHTML || '').length : 0;
            }""") or 0)
        except Exception:
            return 0

    try:
        before_size = editor_size()
        toolbar_btn = page.query_selector(".se-template-toolbar-button")
        if not toolbar_btn:
            _log(on_log, "[네이버] 템플릿 버튼을 찾을 수 없습니다.")
            return False
        toolbar_btn.click()
        time.sleep(1.0)

        # "작성 중인 글이 있습니다" 같은 이어서 작성 팝업이 뜨면 취소
        page.evaluate(r"""() => {
            for (const b of document.querySelectorAll('button')) {
                if ((b.innerText || '').trim() === '취소') { b.click(); return; }
            }
        }""")
        time.sleep(0.5)

        # "내 템플릿" 탭 클릭
        clicked_tab = page.evaluate(r"""() => {
            for (const el of document.querySelectorAll('*')) {
                if ((el.innerText || '').trim() === '내 템플릿' && el.children.length === 0) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        if not clicked_tab:
            _log(on_log, "[네이버] '내 템플릿' 탭을 찾을 수 없습니다.")
            return False
        time.sleep(0.8)

        # 이름이 일치하는 템플릿 카드 클릭. 네이버 UI는 계정/버전에 따라
        # 카드 클릭만으로 삽입되거나, 선택 후 적용/삽입 버튼을 눌러야 한다.
        clicked = page.evaluate(r"""(name) => {
            const items = document.querySelectorAll('.se-doc-template-item');
            for (const item of items) {
                const titleEl = item.querySelector('.se-doc-template-title');
                if (titleEl && titleEl.innerText.trim() === name) {
                    const link = item.querySelector('.se-doc-template') || item;
                    link.scrollIntoView({block: 'center'});
                    link.click();
                    return true;
                }
            }
            return false;
        }""", template_name)
        if not clicked:
            _log(on_log, f"[네이버] '{template_name}' 템플릿을 목록에서 찾을 수 없습니다.")
            page.keyboard.press("Escape")
            return False
        time.sleep(1.2)

        def inserted() -> bool:
            time.sleep(0.4)
            return editor_size() > before_size + 20

        if not inserted():
            page.evaluate(r"""() => {
                const visible = el => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
                };
                const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(visible);
                const apply = buttons.find(btn => /적용|삽입|사용|확인|불러오기/.test((btn.innerText || btn.textContent || '').trim()));
                if (apply) apply.click();
            }""")
            time.sleep(1.0)

        if not inserted():
            page.evaluate(r"""(name) => {
                const items = document.querySelectorAll('.se-doc-template-item');
                for (const item of items) {
                    const titleEl = item.querySelector('.se-doc-template-title');
                    if (titleEl && titleEl.innerText.trim() === name) {
                        const link = item.querySelector('.se-doc-template') || item;
                        link.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, cancelable: true, view: window}));
                        return true;
                    }
                }
                return false;
            }""", template_name)
            time.sleep(1.2)

        if not inserted():
            _log(on_log, f"[네이버] 템플릿 '{template_name}' 선택은 했지만 본문 적용 확인 실패")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        # 패널 닫기
        page.keyboard.press("Escape")
        time.sleep(0.3)
        _log(on_log, f"[네이버] 템플릿 '{template_name}' 삽입 완료")
        return True
    except Exception as exc:
        _log(on_log, f"[네이버] 템플릿 삽입 오류: {exc}")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _cleanup_inserted_template(page, template_name: str, on_log=None) -> None:
    """Remove template title/placeholder text that can collide with generated body."""
    labels = {template_name.strip(), "쿠팡", "마이리얼트립", "제목"}
    labels = {label for label in labels if label}
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            removed = scope.evaluate(r"""(labels) => {
                const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                let count = 0;
                for (const comp of Array.from(document.querySelectorAll('.se-content .se-component.se-text, .se-content .se-component.se-sectionTitle'))) {
                    if (comp.closest('.se-documentTitle')) continue;
                    const text = clean(comp.innerText || comp.textContent || '');
                    if (!text || labels.includes(text)) {
                        comp.remove();
                        count += 1;
                    }
                }
                return count;
            }""", list(labels))
            if removed:
                _log(on_log, f"[네이버] 템플릿 잔여 텍스트 정리: {removed}개")
        except Exception:
            continue


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
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            text = scope.evaluate(r"""() => {
                const el = document.querySelector('.se-documentTitle');
                return el ? (el.innerText || el.textContent || '') : '';
            }""")
            text = _clean_editor_text(text)
            if text and text not in {"제목", "제목을 입력하세요"}:
                return text
        except Exception:
            continue
    return ""


def _raw_title_text(page) -> str:
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            text = scope.evaluate(r"""() => {
                const para = document.querySelector('.se-documentTitle .se-text-paragraph');
                return para ? (para.innerText || para.textContent || '') : '';
            }""")
            text = _clean_editor_text(text)
            if text:
                return text
        except Exception:
            continue
    return ""


def _replace_title(page, title: str, on_log=None) -> bool:
    """템플릿이 제목 영역에 남긴 문구를 실제 글 제목으로 완전히 교체한다."""
    scopes = [page, *getattr(page, "frames", [])]
    for scope in scopes:
        try:
            scope.wait_for_selector(".se-documentTitle .se-text-paragraph, .se-documentTitle [contenteditable='true']", timeout=5000)
        except Exception:
            continue
        try:
            try:
                title_box = scope.locator('.se-documentTitle .se-text-paragraph').first.bounding_box(timeout=1500)
            except Exception:
                title_box = None
            if title_box:
                page.mouse.click(
                    title_box["x"] + min(title_box["width"] - 10, 650),
                    title_box["y"] + title_box["height"] / 2,
                )
                time.sleep(0.25)
                for _ in range(120):
                    page.keyboard.press("Backspace")
                    time.sleep(0.003)
                time.sleep(0.15)
                _type_text(page, title, chunk=80, delay=25)
                time.sleep(0.8)
                page.keyboard.press("Tab")
                time.sleep(0.4)
                current = _raw_title_text(page)
                if current == title:
                    _log(on_log, f"[네이버] 제목 입력 완료(끝클릭삭제): {title[:50]}")
                    _remove_leading_editor_body_title(page, title, on_log=on_log)
                    return True
                _log(on_log, f"[네이버] 제목 끝클릭삭제 확인 실패: {current[:80]}")

            command_result = scope.evaluate(r"""(title) => {
                const visible = el => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
                };
                const target = Array.from(document.querySelectorAll(
                    '.se-documentTitle .se-text-paragraph, .se-documentTitle [contenteditable="true"], [class*="documentTitle"] [contenteditable="true"], .se-documentTitle'
                )).find(visible);
                if (!target) return {ok: false, text: ''};
                target.scrollIntoView({block: 'center'});
                target.focus();
                target.click();

                const range = document.createRange();
                range.selectNodeContents(target);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);

                const inserted = document.execCommand('insertText', false, title);
                target.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: title}));
                target.dispatchEvent(new Event('change', {bubbles: true}));
                target.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Process'}));
                const text = (target.innerText || target.textContent || '').replace(/\s+/g, ' ').trim();
                return {ok: inserted && text === title, text};
            }""", title)
            time.sleep(0.6)
            current = _raw_title_text(page)
            if command_result and command_result.get("ok") and current == title:
                _log(on_log, f"[네이버] 제목 입력 완료(입력명령): {title[:50]}")
                _remove_leading_editor_body_title(page, title, on_log=on_log)
                return True
            _log(on_log, f"[네이버] 제목 입력명령 확인 실패: {(command_result or {}).get('text', '')[:80] or current[:80]}")

            attempts = [
                ("타이핑", lambda: _type_text(page, title, chunk=80, delay=25)),
                ("붙여넣기", lambda: _paste_text(page, title)),
            ]
            for label, writer in attempts:
                if not _select_title_field(scope, page):
                    _log(on_log, f"[네이버] 제목칸 선택 실패: {label}")
                    continue
                time.sleep(0.2)
                page.keyboard.press("Meta+a")
                time.sleep(0.1)
                page.keyboard.press("Backspace")
                time.sleep(0.2)
                writer()
                time.sleep(0.8)
                page.keyboard.press("Tab")
                time.sleep(0.5)
                current = _raw_title_text(page)
                if current == title:
                    _log(on_log, f"[네이버] 제목 입력 완료({label}): {title[:50]}")
                    _remove_leading_editor_body_title(page, title, on_log=on_log)
                    return True
                _log(on_log, f"[네이버] 제목 {label} 확인 실패: {current[:80]}")
        except Exception as exc:
            _log(on_log, f"[네이버] 제목 교체 오류: {exc}")
            continue
    return False


def _select_title_field(scope, page) -> bool:
    try:
        selected = scope.evaluate(r"""() => {
            const visible = el => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
            };
            const title = document.querySelector('.se-documentTitle');
            const target = Array.from(document.querySelectorAll(
                '.se-documentTitle .se-text-paragraph, .se-documentTitle [contenteditable="true"], [class*="documentTitle"] [contenteditable="true"], .se-documentTitle'
            )).find(visible);
            if (!target || !title) return false;
            target.scrollIntoView({block: 'center'});
            target.focus();
            target.click();

            const range = document.createRange();
            range.selectNodeContents(target);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);

            // SmartEditor sometimes leaves the caret inside the first text node
            // after a click. Re-apply a whole-title range after focus settles.
            setTimeout(() => {
                try {
                    const retryRange = document.createRange();
                    retryRange.selectNodeContents(target);
                    const retrySel = window.getSelection();
                    retrySel.removeAllRanges();
                    retrySel.addRange(retryRange);
                } catch (_) {}
            }, 0);
            return true;
        }""")
        if selected:
            time.sleep(0.2)
            return True
    except Exception:
        pass
    selectors = [
        ".se-documentTitle .se-text-paragraph",
        ".se-documentTitle [contenteditable='true']",
        "[class*='documentTitle'] [contenteditable='true']",
        ".se-documentTitle",
    ]
    for selector in selectors:
        try:
            target = scope.locator(selector).first
            if target.is_visible(timeout=800):
                target.click(timeout=2000, force=True, click_count=3)
                time.sleep(0.2)
                return True
        except Exception:
            continue
    return False


def _remove_leading_editor_body_title(page, title: str, on_log=None) -> bool:
    clean_title = _clean_markdown_line((title or "").split('\n')[0]).strip()
    if not clean_title:
        return False
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            removed = scope.evaluate(r"""(title) => {
                const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                const bodyParas = Array.from(document.querySelectorAll(
                    '.se-content .se-component.se-text .se-text-paragraph, .se-content .se-component.se-text [contenteditable="true"]'
                )).filter(el => !el.closest('.se-documentTitle'));
                const first = bodyParas.find(el => clean(el.innerText || el.textContent));
                if (!first) return false;
                const text = first.innerText || first.textContent || '';
                if (!clean(text).startsWith(title)) return false;

                const walker = document.createTreeWalker(first, NodeFilter.SHOW_TEXT, {
                    acceptNode(node) {
                        return node.nodeValue && node.nodeValue.includes(title)
                            ? NodeFilter.FILTER_ACCEPT
                            : NodeFilter.FILTER_REJECT;
                    }
                });
                let node;
                while ((node = walker.nextNode())) {
                    const idx = node.nodeValue.indexOf(title);
                    if (idx < 0) continue;
                    node.nodeValue = node.nodeValue.slice(0, idx) + node.nodeValue.slice(idx + title.length).replace(/^\s+/, '');
                    first.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContentBackward'}));
                    first.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                return false;
            }""", clean_title)
            if removed:
                _log(on_log, "[네이버] 본문에 잘못 들어간 제목을 제거했습니다.")
                return True
        except Exception:
            continue
    return False


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


def _wait_for_editor_after_login(page, editor_url: str, blog_id: str = "", on_log=None, timeout: int = 300) -> bool:
    """Wait until SmartEditor is available, helping the user-driven login flow.

    If Naver shows the login page, the user signs in manually in the same Chrome
    window. After login, Naver may stay on a non-editor page, so we periodically
    navigate back to the postwrite URL instead of waiting forever on the old page.
    """
    deadline = time.time() + timeout
    last_log = 0.0
    last_postwrite_retry = 0.0
    auto_login_attempted = False
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
            if blog_id and not auto_login_attempted:
                auto_login_attempted = True
                account_id = login_account_id("naver", blog_id)
                _log(on_log, f"[네이버] 로그인 화면 감지 — 저장 계정 '{account_id}' 자동 로그인 시도")
                if ensure_naver_login(page, blog_id, account_id=account_id, on_log=on_log):
                    try:
                        page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
                        last_postwrite_retry = time.time()
                        time.sleep(2)
                    except Exception as exc:
                        _log(on_log, f"[네이버] 로그인 후 글쓰기 재이동 실패: {exc}")
                    continue
                _log(on_log, f"[네이버] 저장 계정 자동 로그인 실패: {account_id}")
            else:
                _log(on_log, "[네이버] 로그인 화면입니다. 자동 로그인 재시도 없이 대기합니다.")
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
        if not has_editor and now - last_postwrite_retry > 12:
            try:
                _log(on_log, "[네이버] 로그인 후 글쓰기 화면으로 다시 이동합니다.")
                page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
                last_postwrite_retry = time.time()
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


def _strip_leading_body_title(markdown: str, title: str) -> tuple[str, bool]:
    clean_title = _clean_markdown_line((title or "").split('\n')[0]).strip()
    if not clean_title:
        return markdown, False

    lines = markdown.split('\n')
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or re.fullmatch(r"[-*_]{3,}", stripped):
            continue
        candidate = re.sub(r"^(제목\s*[:：]|title\s*[:：])\s*", "", stripped, flags=re.I)
        candidate = _clean_markdown_line(candidate)
        if candidate == clean_title:
            del lines[idx]
            while lines and (not lines[0].strip() or re.fullmatch(r"[-*_]{3,}", lines[0].strip())):
                lines.pop(0)
            return '\n'.join(lines).lstrip(), True
        return markdown, False
    return markdown, False


def _naver_hashtag_text(title: str, content_markdown: str, tags: list[str]) -> str:
    tag_list = _build_naver_tags(title, content_markdown, tags)
    return " ".join(f"#{tag}" for tag in tag_list)


def _build_naver_tags(title: str, content_markdown: str, tags: list[str], limit: int = 20) -> list[str]:
    stop_words = {
        "추천", "후기", "리뷰", "정리", "기준", "방법", "순서", "먼저", "실제", "직접",
        "써보고", "남은", "좋은", "상품", "관련", "많이", "봤던", "것들", "전에",
        "사기", "전", "체크", "체크할", "체크리스트", "부분", "도움된", "자주", "묻는", "질문",
        "따라", "구매", "추천을", "청소에", "고르는", "나눠서", "보는",
        "쿠팡", "쿠팡파트너스", "마이리얼트립", "파트너스", "제휴", "활동", "포스팅",
    }

    def clean_tag(value: str) -> str:
        value = re.sub(r"https?://\S+", "", value or "")
        value = value.lstrip("#").strip()
        value = re.sub(r"[\[\]()`*_~!@#$%^&=+{}|;:'\"<>,.?/\\]+", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return ""
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value)[:24]

    def add(value: str):
        tag = clean_tag(value)
        if not tag or len(tag) < 2:
            return
        if _bad_naver_tag(tag):
            return
        if tag in stop_words or any(word in tag for word in ("수수료", "제공받", "포스팅은")):
            return
        if tag not in result:
            result.append(tag)

    result: list[str] = []
    for tag in tags or []:
        add(tag)

    title_text = re.split(r"[,—\-:：|]", title or "")[0]
    words = [w for w in re.findall(r"[0-9A-Za-z가-힣]+", title_text) if w and w not in stop_words]
    if len(words) >= 2:
        add(" ".join(words[: min(5, len(words))]))
    if len(words) >= 2:
        add(" ".join(words[:2]))
    if words:
        add(words[0])
    if len(words) >= 2:
        add(words[1])
    for n in (3, 2):
        for i in range(max(0, len(words) - n + 1)):
            phrase = words[i:i + n]
            if phrase and phrase[-1] in {"추천", "후기", "리뷰"}:
                add(" ".join(phrase))
            elif n == 2:
                add(" ".join(phrase))
            if len(result) >= limit:
                return result[:limit]

    title_base = words[:2]
    if title_base:
        base = " ".join(title_base)
        for suffix in ["비교", "후기", "가성비", "실사용", "선택", "장단점", "체크리스트"]:
            add(f"{base} {suffix}")
            if len(result) >= limit:
                break

    product_terms = _extract_product_terms(content_markdown, stop_words)
    for term in product_terms:
        add(term)
        if title_base:
            add(f"{term} 추천")
        if len(result) >= limit:
            break

    headings = re.findall(r"(?m)^#{2,3}\s+(.+)$|^ㅂㅂㅂ\s*(.+)$", content_markdown or "")
    for pair in headings:
        heading = next((x for x in pair if x), "")
        heading_words = [w for w in re.findall(r"[0-9A-Za-z가-힣]+", heading) if w not in stop_words]
        if len(heading_words) == 2:
            add(" ".join(heading_words))
        if len(result) >= limit:
            break

    return result[:limit]


def _bad_naver_tag(tag: str) -> bool:
    bad_exact = {
        "있어요", "있습니다", "아이가", "합니다", "됩니다", "있다면", "없다면", "보세요",
        "그리고", "하지만", "그래서", "이런", "저런", "해당", "정도", "경우", "제품은",
    }
    if tag in bad_exact:
        return True
    bad_parts = [
        "부터", "보면", "실제로", "좋았던", "아쉬운", "비슷한", "이런분", "함께본",
        "나눠", "몰랐던", "제품들과", "선택인지", "확인해야", "추천대상", "비추천",
    ]
    if any(part in tag for part in bad_parts):
        return True
    if re.search(r"(이가|가요|나요|세요|어요|아요|니다|다면|지만|라고|으로|에서|에게|에는|에도|부터|까지|하면|되면|와|과|을|를|은|는|이|가)$", tag):
        return True
    return False


def _extract_product_terms(content_markdown: str, stop_words: set[str]) -> list[str]:
    terms: list[str] = []
    allowed_suffixes = (
        "매트", "발매트", "커버", "슬리퍼", "제습제", "제습기", "청소솔", "브러쉬", "용기",
        "선풍기", "보조배터리", "수납템", "건조대", "우산꽂이", "베개커버", "소재",
        "규조토", "극세사", "실리콘", "나일론", "EVA", "PVC", "TPE", "코르크",
    )
    for word in re.findall(r"[0-9A-Za-z가-힣]{2,}", content_markdown or ""):
        if word in stop_words or _bad_naver_tag(word) or re.fullmatch(r"\d+", word):
            continue
        if word in {"소재", "재질", "제품", "선택", "관리"}:
            continue
        if any(suffix.lower() in word.lower() for suffix in allowed_suffixes):
            if word not in terms:
                terms.append(word)
        if len(terms) >= 12:
            break
    return terms


def _append_naver_hashtags(page, hashtag_text: str, on_log=None) -> bool:
    if not hashtag_text:
        return False
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            inserted = scope.evaluate(r"""(hashtagText) => {
                const content = document.querySelector('.se-content');
                if (!content) return false;
                const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                for (const comp of Array.from(content.querySelectorAll('.se-component'))) {
                    if (!comp.closest('.se-documentTitle') && clean(comp.innerText || comp.textContent) === hashtagText) return 'exists';
                }

                const firstTag = hashtagText.split(/\s+/)[0];
                for (const comp of Array.from(content.querySelectorAll('.se-component'))) {
                    if (comp.closest('.se-documentTitle')) continue;
                    if (clean(comp.innerText || comp.textContent).startsWith(firstTag)) comp.remove();
                }

                const id = () => 'SE-' + (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(16).slice(2));
                const compId = id();
                const moduleId = id();
                const paraId = id();
                const spanId = id();

                const comp = document.createElement('div');
                comp.className = 'se-component se-text se-l-default';
                comp.id = compId;
                comp.dataset.compid = compId;
                comp.dataset.a11yTitle = '본문';
                comp.innerHTML = `
                    <div class="se-component-content">
                        <div class="se-section se-section-text se-l-default">
                            <div id="${moduleId}" class="se-module se-module-text __se-unit">
                                <p id="${paraId}" class="se-text-paragraph se-text-paragraph-align-left" style="line-height: 1.8;">
                                    <span id="${spanId}" class="se-ff-nanumgothic se-fs19 __se-node" style="color: rgb(0, 0, 0);"></span>
                                </p>
                            </div>
                        </div>
                    </div>`;
                comp.querySelector('span').textContent = hashtagText;

                const components = Array.from(content.querySelectorAll('.se-component'))
                    .filter(el => !el.closest('.se-documentTitle'));
                const last = components[components.length - 1];
                if (last) last.insertAdjacentElement('afterend', comp);
                else content.appendChild(comp);

                comp.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: hashtagText}));
                comp.dispatchEvent(new Event('change', {bubbles: true}));
                comp.scrollIntoView({block: 'center'});
                return true;
            }""", hashtag_text)
            if inserted:
                _log(on_log, f"[네이버] 본문 해시태그 추가: {hashtag_text}")
                return True
        except Exception:
            continue
    return False


def _fill_naver_publish_tags(page, tags: list[str], on_log=None) -> int:
    if not tags:
        return 0
    inserted = 0
    for tag in tags[:20]:
        tag = str(tag or "").strip().lstrip("#")
        if not tag:
            continue
        for scope in [page, *getattr(page, "frames", [])]:
            try:
                # 링크 삽입 팝업이 남아 있으면 URL 입력창이 포커스를 빼앗는다.
                scope.evaluate(r"""() => {
                    for (const btn of document.querySelectorAll('.se-popup-close-button, button[aria-label*="닫기"]')) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) btn.click();
                    }
                }""")
            except Exception:
                pass
            try:
                locator = scope.locator('#tag-input, input[id*="tag"], input[class*="tag"], input[placeholder*="태그"]').first
                if not locator.is_visible(timeout=900):
                    continue
                before = scope.evaluate(r"""() => Array.from(document.querySelectorAll('[class*="tag"]'))
                    .map(el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim())
                    .filter(Boolean).join('\n')""")
                locator.click(timeout=2000)
                time.sleep(0.15)
                locator.fill(tag)
                time.sleep(0.1)
                locator.press("Enter")
                time.sleep(random.uniform(0.35, 0.7))
                after = scope.evaluate(r"""() => Array.from(document.querySelectorAll('[class*="tag"]'))
                    .map(el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim())
                    .filter(Boolean).join('\n')""")
                if tag in after or before != after:
                    inserted += 1
                    break
            except Exception:
                continue
    if inserted:
        _log(on_log, f"[네이버] 발행 태그 입력 완료: {inserted}개")
    return inserted


def _open_publish_panel(page, on_log=None) -> bool:
    _close_editor_popups(page)

    def has_tag_input() -> bool:
        for scope in [page, *getattr(page, "frames", [])]:
            try:
                if scope.evaluate("() => !!document.querySelector('#tag-input, input[placeholder*=\"태그\"]')"):
                    return True
            except Exception:
                continue
        return False

    if has_tag_input():
        _log(on_log, "[네이버] 발행 패널 열림")
        return True

    selectors = [
        '.publish_btn__m9KHH',
        'button[class*="publish_btn"]',
        'button[class*="publishBtn"]',
    ]
    for attempt in range(3):
        for scope in [page, *getattr(page, "frames", [])]:
            clicked = False
            for sel in selectors:
                try:
                    btn = scope.locator(sel).first
                    if btn.is_visible(timeout=700):
                        btn.click(timeout=3000, force=True)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                try:
                    result = scope.evaluate(r"""() => {
                        const visible = el => {
                            const rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
                        };
                        const buttons = Array.from(document.querySelectorAll('button')).filter(visible);
                        const btn = buttons.find(b => (b.textContent || '').trim() === '발행' && b.getBoundingClientRect().top < 120);
                        if (!btn) return null;
                        const rect = btn.getBoundingClientRect();
                        btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                        btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                        btn.click();
                        return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
                    }""")
                    clicked = bool(result)
                    if result:
                        time.sleep(0.3)
                        if not has_tag_input():
                            page.mouse.click(result["x"], result["y"])
                except Exception:
                    clicked = False
            if clicked:
                deadline = time.time() + 10
                while time.time() < deadline:
                    if has_tag_input():
                        _log(on_log, "[네이버] 발행 패널 열림")
                        return True
                    time.sleep(0.4)
        if has_tag_input():
            _log(on_log, "[네이버] 발행 패널 열림")
            return True
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass
    _log(on_log, "[네이버] 발행 패널 열기 실패")
    return False


def _close_publish_panel(page) -> None:
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            closed = scope.evaluate(r"""() => {
                for (const btn of document.querySelectorAll('button, a, [role="button"]')) {
                    const text = (btn.textContent || btn.getAttribute('aria-label') || btn.title || '').trim();
                    if (!/발행\s*설정\s*닫기|닫기|취소/.test(text)) continue;
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if closed:
                time.sleep(0.5)
                return
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.4)
    except Exception:
        pass


def _save_naver_draft(page, on_log=None):
    for scope in [page, *getattr(page, "frames", [])]:
        try:
            saved = scope.evaluate(r"""() => {
                for (const btn of document.querySelectorAll('button')) {
                    const t = (btn.textContent || '').trim();
                    if (t === '발행' || t === '공개발행' || t === '발행하기') continue;
                    const rect = btn.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    if (t === '임시저장' || t === '저장') {
                        btn.click();
                        return t;
                    }
                }
                return false;
            }""")
            if saved:
                return saved
        except Exception:
            pass
        for sel in ['.save_btn__bzc5B', 'button[class*="save_btn"]', 'button[class*="saveBtn"]']:
            try:
                btn = scope.locator(sel).first
                if btn.is_visible(timeout=700):
                    btn.click()
                    return True
            except Exception:
                continue
    _log(on_log, "[네이버] 임시저장 버튼을 찾지 못했습니다.")
    return False


# ─── 메인 함수 ───────────────────────────────────────────────────

def post_naver(blog_id: str, title: str, content_markdown: str,
               tags: list = None, image_paths: list = None,
               category: str = "", status: str = "draft", on_log=None,
               template_name: str = "", port: int = None) -> dict:
    """네이버 블로그 임시저장(draft) 또는 발행(publish)

    blog_id: 네이버 블로그 아이디 (예: salim1su)
    status: "draft" → 임시저장, "publish" → 발행
    template_name: 지정하면 본문 맨 앞에 SmartEditor "내 템플릿"에 저장된
        해당 이름의 템플릿을 불러와 삽입한다 (예: "마이리얼트립"). 실패해도
        발행 자체는 중단하지 않는다.
    port: 이 blog_id 계정이 로그인되어 있는 Chrome 디버그 포트 (계정별로 분리된
        프로필을 쓸 때 지정). 생략하면 CHROME_PORT(.env, 기본 9222) 사용.
    Returns: {"ok": bool, "url": str, "error": str}
    """
    tags = tags or []
    image_paths = [p for p in (image_paths or []) if Path(p).exists()]
    content_markdown, removed_body_title = _strip_leading_body_title(content_markdown, title)
    if removed_body_title:
        _log(on_log, "[네이버] 본문 첫 줄의 중복 제목을 제거했습니다.")
    naver_tags = _build_naver_tags(title, content_markdown, tags)
    sections = _parse_sections(content_markdown)

    if port is None:
        port = ensure_chrome_for("naver", blog_id, on_log=on_log)
    pw, browser = connect(port=port)
    page = None
    completed_ok = False
    try:
        editor_url = f"https://blog.naver.com/{blog_id}/postwrite"
        page = get_page(browser, navigate_to=None)
        opened = _open_editor(page, blog_id, on_log=on_log)
        time.sleep(random.uniform(3, 5))

        if _looks_like_login_url(page.url) or not opened:
            account_id = login_account_id("naver", blog_id)
            _log(on_log, f"[네이버] 로그인 필요 또는 계정 불일치 감지 — 저장 계정 '{account_id}' 선택 시도")
            if not _looks_like_login_url(page.url):
                logout_naver(page, on_log=on_log)
            if not ensure_naver_login(page, blog_id, account_id=account_id, on_log=on_log):
                return {"ok": False, "error": f"네이버 저장 계정 로그인 실패: {account_id or blog_id}"}
            opened = _open_editor(page, blog_id, on_log=on_log)
            time.sleep(random.uniform(2, 3))
            if not opened and not page.query_selector(".se-content"):
                _log(on_log, f"[네이버] 즉시 글쓰기 진입 실패 — 대기 재시도 계속: {page.url}")

        current_url = page.url
        if "about:blank" in current_url:
            return {"ok": False, "error": "페이지 이동 실패 (about:blank) — Chrome CDP 연결 상태를 확인하세요."}

        # 에디터 로드 대기 — 로그인 필요 시 Chrome에서 로그인하면 글쓰기 화면으로 재이동
        if not _wait_for_editor_after_login(page, editor_url, blog_id=blog_id, on_log=on_log, timeout=300):
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

        clean_title = title.split('\n')[0].strip()

        # ── 템플릿 삽입: 먼저 쿠팡/MRT 템플릿을 실제 적용한 뒤,
        # 템플릿 제목(예: 쿠팡)을 글 제목으로 덮어쓴다.
        if template_name:
            if not _focus_body(page):
                _log(on_log, "[네이버] 템플릿 삽입 전 본문 포커스 실패 — 제목 아래 영역 클릭 후 계속 진행합니다.")
                _click_body_area_by_position(page)
            time.sleep(0.3)
            if _insert_template(page, template_name, on_log=on_log):
                _cleanup_inserted_template(page, template_name, on_log=on_log)
            else:
                _log(on_log, "[네이버] 템플릿 삽입 실패 — 템플릿 없이 계속 진행합니다.")

        # ── 제목 입력: 템플릿이 제목칸에 넣은 '쿠팡' 같은 문구를 실제 제목으로 덮어쓰기 ──
        if not _replace_title(page, clean_title, on_log=on_log):
            return {"ok": False, "error": f"네이버 제목 입력 실패 — 현재 제목: {_title_text(page)[:120]}"}

        # ── 본문 영역 포커스: 템플릿을 먼저 넣은 경우 템플릿 뒤 새 문단에서 작성 ──
        if template_name:
            if not _move_to_body_end(page):
                _log(on_log, "[네이버] 템플릿 뒤 본문 위치 확인 실패 — 제목 아래 영역 클릭 후 계속 진행합니다.")
                _click_body_area_by_position(page)
            _press_enter(page, 1)
        elif not _focus_body(page):
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
        _log(on_log, f"[네이버] 본문 입력 시작 — 총 {n_blocks}개 블록 (약 {n_blocks * 3 // 2}초 소요 예상)")
        for index, (block_type, content) in enumerate(blocks):
            if index > 0:
                _press_enter(page, 1)
            if block_type == "table":
                rows = content
                preview = f"표 {len(rows)}x{max(len(r) for r in rows)}"
                _log(on_log, f"[네이버] 블록 입력 중 [{index + 1}/{n_blocks}] {preview}")
                _insert_naver_table(page, rows, on_log=on_log)
            elif block_type == "html":
                preview = _html_to_plain_text(content).replace("\n", " ")
                _log(on_log, f"[네이버] 링크 블록 입력 중 [{index + 1}/{n_blocks}] {preview[:20]}{'...' if len(preview) > 20 else ''}")
                pasted_as_html = _paste_html(page, content)
                if not pasted_as_html:
                    _log(on_log, "[네이버] HTML 붙여넣기 실패 — URL 포함 텍스트로 대체")
                linked_count = _insert_links_with_editor_ui(page, content, on_log=on_log)
                if linked_count == 0:
                    linked_count = _linkify_pasted_block(page, content, on_log=on_log)
                if linked_count == 0:
                    _log(on_log, "[네이버] 에디터 링크 삽입 결과 0개 — 수동 확인 필요")
                _press_enter(page, 1)
            else:
                text = content
                _log(on_log, f"[네이버] 블록 입력 중 [{index + 1}/{n_blocks}] {text[:20]}{'...' if len(text) > 20 else ''}")
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

        final_title_text = _raw_title_text(page)
        if final_title_text != clean_title:
            _log(on_log, f"[네이버] 저장 전 제목 재확인 — 현재 제목: {final_title_text[:80]}")
            if not _replace_title(page, clean_title, on_log=on_log):
                return {"ok": False, "error": f"네이버 저장 전 제목 재입력 실패 — 현재 제목: {_raw_title_text(page)[:120]}"}

        time.sleep(random.uniform(1.0, 2.0))

        # ── 발행 패널 열기: 실제 발행뿐 아니라 임시저장도 태그 편집을 위해 사용 ──
        panel_opened = False
        if status == "publish" or naver_tags:
            panel_opened = _open_publish_panel(page, on_log=on_log)

        if panel_opened:
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
            if naver_tags:
                inserted_tags = _fill_naver_publish_tags(page, naver_tags, on_log=on_log)
                if inserted_tags == 0:
                    _log(on_log, "[네이버] 발행 태그 입력창을 찾지 못했습니다.")

            if status != "publish":
                _close_publish_panel(page)

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
            saved = _save_naver_draft(page, on_log=on_log)
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
        completed_ok = True
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
        if page and not page.is_closed() and logout_after_post_enabled():
            logout_naver(page, on_log=on_log)
        try:
            pw.stop()
        except Exception:
            pass
        if completed_ok:
            close_chrome(port, on_log=on_log)
