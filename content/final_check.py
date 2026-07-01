from __future__ import annotations

import re

from .sanitizer import unsafe_markers


def final_check(title: str, body_markdown: str, html: str, image_urls: list[str] | None = None) -> dict:
    warnings = []
    source_text = f"{title}\n{body_markdown}"
    if unsafe_markers(source_text):
        warnings.append("unsafe_markers_in_source")
    visible_text = re.sub(r"<[^>]+>", " ", html)
    if re.search(r"```|===본문===|파일명\s*:|프롬프트\s*:|alt\s*태그\s*:|\[이미지\d+\]", visible_text):
        warnings.append("internal_text_visible")
    if re.search(r"(^|\n)\s*#{1,6}\s+", visible_text) or "**" in visible_text:
        warnings.append("markdown_visible")
    urls = image_urls or re.findall(r"<img[^>]+src=[\"']([^\"']+)", html)
    if len(urls) != len(set(urls)):
        warnings.append("duplicate_image_url")
    return {"passed": not warnings, "warnings": warnings}
