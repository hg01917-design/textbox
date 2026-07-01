from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from config import DRAFTS_DIR


def slugify(text: str) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣\s-]", "", text).strip()
    text = re.sub(r"\s+", "-", text)
    return text[:80] or "draft"


def to_markdown(payload: dict) -> str:
    draft = payload["draft"]
    analysis = payload["analysis"]
    quality = payload["quality"]
    candidates = analysis.get("candidates", [])[:10]
    source_urls = payload.get("source_urls", [])
    source_text = "\n".join(f"- {url}" for url in source_urls) if source_urls else "- 없음"
    image_paths = payload.get("images", [])
    image_text = "\n".join(f"- {path}" for path in image_paths) if image_paths else "- 없음"
    meta_text = "" if _is_target(payload, "Naver") else f"\n메타설명: {draft['meta_description']}\n"
    rows = "\n".join(
        f"| {item['keyword']} | {_display_unknown(item['competition'])} | {_display_unknown(item['volume'])} | {item['score']} | {item['difficulty']} |"
        for item in candidates
    )
    return f"""---
keyword: {payload['keyword']}
blog_type: {payload['blog_type']}
title: {draft['title']}
quality_passed: {quality['passed']}
created_at: {payload['created_at']}
---

# {draft['title']}
{meta_text}

태그: {', '.join(draft['tags'])}

## 공식 근거 자료

{source_text}

## 키워드 분석

| 키워드 | 발행량 | 검색량 | 점수 | 난이도 |
|---|---:|---:|---:|---|
{rows}

## 품질 검사

- 통과: {quality['passed']}
- 글자수: {quality['char_count']}
- 경고: {', '.join(quality['warnings']) if quality['warnings'] else '없음'}

## 카드 이미지

{image_text}

## 본문 초안

{draft['body']}
""".strip() + "\n"


def _display_unknown(value: int | float) -> str:
    return "unknown" if value < 0 else str(value)


def _is_target(payload: dict, platform: str) -> bool:
    target = payload.get("target", {}) if payload else {}
    return target.get("platform") == platform


def save_draft(payload: dict) -> dict:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = slugify(payload["draft"]["title"])
    base = DRAFTS_DIR / f"{stamp}_{slug}"
    md_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}
