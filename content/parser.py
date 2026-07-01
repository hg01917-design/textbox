from __future__ import annotations

import re

# 기존 마커 (===제목=== 방식)
SECTION_RE = {
    "title": r"===제목===\s*\n(.*?)\n*===제목끝===",
    "body":  r"===본문===\s*\n(.*?)\n*===본문끝===",
    "tags":  r"===태그===\s*\n(.*?)\n*===태그끝===",
    "meta_description": r"===메타설명===\s*\n(.*?)\n*===메타설명끝===",
}

# 네이버 스타일 마커 (제목을입력해주세요1: / 본문2:)
_TITLE_NEW_RE = re.compile(r"제목을입력해주세요1\s*:\s*(.*?)(?:\n|$)")
_BODY_NEW_RE  = re.compile(r"본문2\s*:\s*\n(.*)", re.DOTALL)
_TAGS_NEW_RE  = re.compile(r"===태그===\s*\n(.*?)\n*===태그끝===", re.DOTALL)


def parse_ai_response(raw: str, fallback_keyword: str, blog_type: str = "") -> dict:
    # 네이버 마커 우선 감지
    title_new = _TITLE_NEW_RE.search(raw)
    body_new   = _BODY_NEW_RE.search(raw)

    if title_new and body_new:
        title = title_new.group(1).strip()
        body_raw = body_new.group(1).strip()
        # 태그는 별도 마커 또는 body 내에서 추출
        tags_match = _TAGS_NEW_RE.search(body_raw)
        if tags_match:
            tag_text = tags_match.group(1)
            body_raw = body_raw[: tags_match.start()].strip()
        else:
            tag_text = ""
        tags = _parse_tags(tag_text, fallback_keyword)
        meta_description = _auto_meta(body_raw)
        return _build(title, body_raw, tags, meta_description, raw, fallback_keyword, blog_type)

    # 기존 마커
    def section(name: str) -> str:
        match = re.search(SECTION_RE[name], raw, flags=re.DOTALL)
        return match.group(1).strip() if match else ""

    title = section("title") or fallback_keyword
    body  = section("body") or raw.strip()
    tag_text = section("tags")
    tags = _parse_tags(tag_text, fallback_keyword)
    meta_description = section("meta_description") or _auto_meta(body)
    return _build(title, body, tags, meta_description, raw, fallback_keyword, blog_type)


def _build(title: str, body: str, tags: list[str], meta: str, raw: str,
           fallback: str, blog_type: str) -> dict:
    return {
        "title": re.sub(r"^[\d.\-\s]+", "", title).strip()[:80],
        "body": body.strip(),
        "tags": tags[:10],
        "meta_description": meta.strip()[:160],
        "raw": raw,
        "blog_type": blog_type,
    }


def _parse_tags(tag_text: str, fallback: str) -> list[str]:
    tags = [t.strip().lstrip("#") for t in re.split(r"[,\n]", tag_text) if t.strip()]
    return tags if tags else [fallback]


def _auto_meta(body: str) -> str:
    plain = re.sub(r"[#*`>|\-{}\[\]()\n\r]", " ", body)
    plain = re.sub(r"표\s*\d+\s*x\s*\d+\s*(시작|끝)", " ", plain)
    plain = re.sub(r"\(\d+,\d+\)\s*", " ", plain)
    plain = re.sub(r"ㅂㅂㅂ\S*", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()[:150]
