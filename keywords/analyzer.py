from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from .scorer import score_keyword
from .suggest import expand_keyword


NAVER_BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"


def _naver_headers() -> dict:
    return {
        "X-Naver-Client-Id": os.environ.get("NAVER_SEARCH_CLIENT_ID", ""),
        "X-Naver-Client-Secret": os.environ.get("NAVER_SEARCH_CLIENT_SECRET", ""),
        "User-Agent": "Mozilla/5.0",
    }


def get_blog_count(keyword: str) -> int:
    if not os.environ.get("NAVER_SEARCH_CLIENT_ID"):
        return -1
    try:
        url = f"{NAVER_BLOG_URL}?query={urllib.parse.quote(keyword)}&display=1"
        req = urllib.request.Request(url, headers=_naver_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return int(data.get("total", 0))
    except Exception:
        return -1


def difficulty_label(total: int) -> str:
    if total < 0:
        return "unknown"
    if total < 3_000:
        return "very_low"
    if total < 10_000:
        return "low"
    if total < 50_000:
        return "medium"
    return "high"


def analyze_keyword(keyword: str, max_competition: int = 50_000, limit: int = 20) -> dict:
    candidates = expand_keyword(keyword, limit=limit)
    rows = []
    for candidate in candidates:
        competition = get_blog_count(candidate)
        scored = score_keyword(candidate, competition)
        scored["difficulty"] = difficulty_label(competition)
        scored["recommended"] = 0 <= competition <= max_competition
        rows.append(scored)
        time.sleep(0.1)

    rows.sort(key=lambda row: (not row["recommended"], -row["score"], row["competition"]))
    return {
        "seed_keyword": keyword,
        "main_competition": get_blog_count(keyword),
        "candidates": rows,
        "best_keyword": rows[0]["keyword"] if rows else keyword,
    }
