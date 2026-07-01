from __future__ import annotations

import html
import re
import urllib.request


def fetch_source(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1_000_000)
            content_type = resp.headers.get("Content-Type", "")
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc), "text": ""}

    encoding = "utf-8"
    match = re.search(r"charset=([^;]+)", content_type, flags=re.IGNORECASE)
    if match:
        encoding = match.group(1).strip()
    try:
        text = raw.decode(encoding, errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")

    return {"url": url, "ok": True, "error": "", "text": clean_source_text(text)}


def clean_source_text(text: str, limit: int = 8000) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def fetch_sources(urls: list[str]) -> list[dict]:
    return [fetch_source(url) for url in urls]


def format_sources_for_prompt(sources: list[dict]) -> str:
    if not sources:
        return "제공된 공식 근거 자료 없음"
    blocks = []
    for source in sources:
        if source.get("ok"):
            blocks.append(f"[SOURCE] {source['url']}\n{source['text']}")
        else:
            blocks.append(f"[SOURCE-ERROR] {source['url']}\n{source.get('error', 'unknown error')}")
    return "\n\n".join(blocks)
