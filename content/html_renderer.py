from __future__ import annotations

import html
import re

from .sanitizer import clean_body


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
            parts.append(f"<h2>{_inline(heading)}</h2>")
            if image_pos < len(image_urls):
                parts.append(_image_html(image_urls[image_pos]))
                image_pos += 1
        elif line.startswith("### "):
            close_ul()
            parts.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_ul()
            parts.append(f"<h2>{_inline(line[3:])}</h2>")
            if image_pos < len(image_urls):
                parts.append(_image_html(image_urls[image_pos]))
                image_pos += 1
        elif line.startswith("# "):
            close_ul()
            parts.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith("- [ ] ") or line.startswith("- [x] ") or line.startswith("- "):
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            item = re.sub(r"^- \[[ xX]\]\s*", "", line[2:] if not line.startswith("- [") else line)
            item = item[2:].strip() if item.startswith("- ") else item.strip()
            parts.append(f"<li>{_inline(item)}</li>")
        else:
            close_ul()
            parts.append(f"<p>{_inline(line)}</p>")
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


def _inline(text: str) -> str:
    safe = html.escape(text.strip(), quote=True)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', safe)
    safe = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", safe)
    return safe


def _image_html(url: str) -> str:
    safe_url = html.escape(url, quote=True)
    return f'<figure><img src="{safe_url}" alt="" loading="lazy" /></figure>'


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
            row = "<tr>" + "".join(f"<th>{_inline(v)}</th>" for v in cols) + "</tr>"
        else:
            row = "<tr>" + "".join(f"<td>{_inline(v)}</td>" for v in cols) + "</tr>"
        rows_html.append(row)
    return f"<table><thead>{rows_html[0]}</thead><tbody>{''.join(rows_html[1:])}</tbody></table>"


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
    head_html = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
    body_html = "".join("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in body)
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
