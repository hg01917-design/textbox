from __future__ import annotations

import re


SECTION_RE = {
    "title": r"===제목===\s*\n(.*?)\n*===제목끝===",
    "body": r"===본문===\s*\n(.*?)\n*===본문끝===",
    "tags": r"===태그===\s*\n(.*?)\n*===태그끝===",
    "meta_description": r"===메타설명===\s*\n(.*?)\n*===메타설명끝===",
}


def parse_ai_response(raw: str, fallback_keyword: str) -> dict:
    def section(name: str) -> str:
        match = re.search(SECTION_RE[name], raw, flags=re.DOTALL)
        return match.group(1).strip() if match else ""

    title = section("title") or fallback_keyword
    body = section("body") or raw.strip()
    tag_text = section("tags")
    tags = [tag.strip().lstrip("#") for tag in re.split(r"[,\n]", tag_text) if tag.strip()]
    if not tags:
        tags = [fallback_keyword]

    meta_description = section("meta_description")
    if not meta_description:
        plain = re.sub(r"[#*`>|\-]", "", body)
        meta_description = re.sub(r"\s+", " ", plain).strip()[:150]

    return {
        "title": re.sub(r"^[\d.\-\s]+", "", title).strip()[:80],
        "body": body.strip(),
        "tags": tags[:10],
        "meta_description": meta_description.strip()[:160],
        "raw": raw,
    }
