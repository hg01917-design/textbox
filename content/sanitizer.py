from __future__ import annotations

import re


FORBIDDEN_TEXT_PATTERNS = [
    r"```",
    r"```\s*(html|markdown|javascript|json)?",
    r"===\s*(제목|본문|태그|메타설명)",
    r"\[이미지\s*\d+\]",
    r"(?im)^\s*(파일명|프롬프트|이미지\s*프롬프트|alt\s*태그|alt)\s*:",
    r"\[출처 필요\]|\[검증 필요\]",
]


def sanitize_draft(draft: dict) -> dict:
    cleaned = dict(draft)
    cleaned["title"] = clean_text(cleaned.get("title", ""), allow_markdown=False)[:80]
    cleaned["meta_description"] = clean_text(cleaned.get("meta_description", ""), allow_markdown=False)[:160]
    cleaned["body"] = clean_body(cleaned.get("body", ""))
    cleaned["tags"] = [clean_text(tag, allow_markdown=False).lstrip("#") for tag in cleaned.get("tags", []) if tag]
    return cleaned


def clean_body(text: str) -> str:
    text = _strip_code_fences(text or "")
    text = re.sub(r"(?is)<(script|style|iframe|object|embed).*?>.*?</\1>", "", text)
    text = re.sub(r"(?is)</?(?:div|span|p|br|strong|b|em|i|h[1-6]|ul|ol|li|table|thead|tbody|tr|th|td|a|img)[^>]*>", "", text)
    text = re.sub(r"(?im)^\s*(파일명|프롬프트|이미지\s*프롬프트|alt\s*태그|alt|Gemini프롬프트)\s*:.*$", "", text)
    text = re.sub(r"\[/?이미지\s*\d+\]", "", text)
    text = re.sub(r"===.*?===", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_text(text: str, allow_markdown: bool = True) -> str:
    text = _strip_code_fences(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    if not allow_markdown:
        text = re.sub(r"[#*_`>|\[\]()]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def unsafe_markers(text: str) -> list[str]:
    found = []
    for pattern in FORBIDDEN_TEXT_PATTERNS:
        if re.search(pattern, text or ""):
            found.append(pattern)
    if re.search(r"(?is)<\s*(script|style|iframe|object|embed|html|body|head)\b", text or ""):
        found.append("dangerous_html_tag")
    return found


def _strip_code_fences(text: str) -> str:
    text = re.sub(r"```\w*\s*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    return text.replace("```", "")
