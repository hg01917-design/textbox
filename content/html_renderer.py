from __future__ import annotations

import html
import re

from .sanitizer import clean_body

# 가독성 개선: 소제목과 본문 사이 간격, 문단 줄간격, 링크 색상 등을 인라인 스타일로 고정한다.
# (테마 기본 스타일에 기대지 않고 우리가 발행하는 본문 자체가 항상 읽기 편하도록)
_H2_STYLE = "margin:2.2em 0 0.6em;line-height:1.4;"
_H3_STYLE = "margin:1.6em 0 0.5em;line-height:1.4;"
_P_STYLE = "margin:0 0 1.3em;line-height:1.85;"
_UL_STYLE = "margin:0 0 1.3em;padding-left:1.4em;"
_LI_STYLE = "margin-bottom:0.5em;line-height:1.8;"
_LINK_STYLE = "color:#1a56db;text-decoration:underline;font-weight:600;"
_TABLE_STYLE = "border-collapse:collapse;width:100%;margin:0 0 1.3em;"
_TABLE_CELL_STYLE = "border:1px solid #ddd;padding:8px 10px;text-align:left;line-height:1.6;"


def render_html(markdown_text: str, image_urls: list[str] | None = None) -> str:
    text = clean_body(markdown_text)
    lines = text.splitlines()
    parts = []
    i = 0
    in_ul = False
    image_urls = image_urls or []
    image_pos = 0
    paragraph_count = 0

    def close_ul():
        nonlocal in_ul
        if in_ul:
            parts.append("</ul>")
            in_ul = False

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            close_ul()
            i += 1
            continue
        # 커스텀 표 블록: 표 N x M 시작 ... 표 N x M 끝
        if _is_custom_table_start(line):
            close_ul()
            cell_lines = []
            i += 1
            while i < len(lines):
                inner = lines[i].strip()
                if re.match(r"표\s*\d+\s*x\s*\d+\s*끝", inner):
                    i += 1
                    break
                if inner:
                    cell_lines.append(inner)
                i += 1
            parts.append(_custom_table_to_html(cell_lines))
            continue
        # 마크다운 표 블록
        if _is_table_start(lines, i):
            close_ul()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            parts.append(_table_to_html(table_lines))
            continue
        # ㅂㅂㅂ소제목 → <h2>
        if line.startswith("ㅂㅂㅂ"):
            close_ul()
            heading = line[3:].strip()
            parts.append(f'<h2 style="{_H2_STYLE}">{_inline(heading)}</h2>')
            if image_pos < len(image_urls):
                parts.append(_image_html(image_urls[image_pos]))
                image_pos += 1
        elif line.startswith("### "):
            close_ul()
            parts.append(f'<h3 style="{_H3_STYLE}">{_inline(line[4:])}</h3>')
        elif line.startswith("## "):
            close_ul()
            parts.append(f'<h2 style="{_H2_STYLE}">{_inline(line[3:])}</h2>')
            if image_pos < len(image_urls):
                parts.append(_image_html(image_urls[image_pos]))
                image_pos += 1
        elif line.startswith("# "):
            close_ul()
            parts.append(f'<h2 style="{_H2_STYLE}">{_inline(line[2:])}</h2>')
        elif line.startswith("- [ ] ") or line.startswith("- [x] ") or line.startswith("- "):
            if not in_ul:
                parts.append(f'<ul style="{_UL_STYLE}">')
                in_ul = True
            item = re.sub(r"^- \[[ xX]\]\s*", "", line[2:] if not line.startswith("- [") else line)
            item = item[2:].strip() if item.startswith("- ") else item.strip()
            parts.append(f'<li style="{_LI_STYLE}">{_inline(item)}</li>')
        else:
            close_ul()
            parts.append(f'<p style="{_P_STYLE}">{_inline(line)}</p>')
            paragraph_count += 1
            if paragraph_count == 2 and image_pos < len(image_urls):
                parts.append(_image_html(image_urls[image_pos]))
                image_pos += 1
        i += 1
    close_ul()
    while image_pos < len(image_urls):
        parts.append(_image_html(image_urls[image_pos]))
        image_pos += 1
    return "\n".join(part for part in parts if part)


_AFFILIATE_LINK_HOSTS = ("link.coupang.com", "myrealt.rip", "agoda.com")


def _link_rel(url: str) -> str:
    if any(host in url for host in _AFFILIATE_LINK_HOSTS):
        return "noopener sponsored nofollow"
    return "noopener"


def _inline(text: str) -> str:
    safe = html.escape(text.strip(), quote=True)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="{_link_rel(m.group(2))}" style="{_LINK_STYLE}">{m.group(1)}</a>',
        safe,
    )
    safe = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", safe)
    return safe


def _image_html(url: str) -> str:
    safe_url = html.escape(url, quote=True)
    return (
        f'<figure style="text-align:center;margin:0 0 1.3em;">'
        f'<img src="{safe_url}" alt="" loading="lazy" style="display:block;margin:0 auto;max-width:100%;height:auto;" />'
        f'</figure>'
    )


def _is_custom_table_start(line: str) -> bool:
    return bool(re.match(r"표\s*\d+\s*x\s*\d+\s*시작", line))


def _custom_table_to_html(cell_lines: list[str]) -> str:
    cells: dict[tuple[int, int], str] = {}
    max_r = max_c = 0
    for line in cell_lines:
        m = re.match(r"\((\d+),(\d+)\)\s*(.*)", line)
        if m:
            r, c, text = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            cells[(r, c)] = text
            max_r = max(max_r, r)
            max_c = max(max_c, c)
    if not cells:
        return ""
    rows_html = []
    for r in range(max_r + 1):
        cols = [cells.get((r, c), "") for c in range(max_c + 1)]
        if r == 0:
            row = "<tr>" + "".join(f'<th style="{_TABLE_CELL_STYLE}">{_inline(v)}</th>' for v in cols) + "</tr>"
        else:
            row = "<tr>" + "".join(f'<td style="{_TABLE_CELL_STYLE}">{_inline(v)}</td>' for v in cols) + "</tr>"
        rows_html.append(row)
    return f'<table style="{_TABLE_STYLE}"><thead>{rows_html[0]}</thead><tbody>{"".join(rows_html[1:])}</tbody></table>'


def _is_table_start(lines: list[str], index: int) -> bool:
    return index + 1 < len(lines) and lines[index].strip().startswith("|") and re.match(r"^\s*\|?\s*:?-{3,}:?", lines[index + 1] or "")


def _table_to_html(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    head_html = "".join(f'<th style="{_TABLE_CELL_STYLE}">{_inline(cell)}</th>' for cell in header)
    body_html = "".join(
        "<tr>" + "".join(f'<td style="{_TABLE_CELL_STYLE}">{_inline(cell)}</td>' for cell in row) + "</tr>"
        for row in body
    )
    return f'<table style="{_TABLE_STYLE}"><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>'
