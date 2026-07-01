from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request


NAVER_WEB_URL = "https://openapi.naver.com/v1/search/webkr.json"

OFFICIAL_DOMAIN_SCORES = {
    "go.kr": 100,
    "gov.kr": 95,
    "semas.or.kr": 95,
    "sbiz.or.kr": 95,
    "mss.go.kr": 95,
    "bokjiro.go.kr": 90,
    "or.kr": 70,
    "data.go.kr": 60,
}

KEYWORD_URL_HINTS = [
    (("소상공인정책자금", "소상공인 정책자금"), [
        "https://ols.semas.or.kr/",
        "https://www.semas.or.kr/",
        "https://www.mss.go.kr/",
    ]),
    (("소상공인", "정책자금"), [
        "https://ols.semas.or.kr/",
        "https://www.semas.or.kr/",
        "https://www.mss.go.kr/",
    ]),
    (("복지로", "복지"), [
        "https://www.bokjiro.go.kr/",
    ]),
    (("정부24",), [
        "https://www.gov.kr/",
    ]),
]


def find_official_urls(keyword: str, blog_type: str, limit: int = 3, on_log=None) -> list[str]:
    if blog_type not in {"정부지원", "생활정보"}:
        return []
    candidates = []
    candidates.extend(_hint_urls(keyword))
    candidates.extend(_naver_official_urls(keyword, on_log=on_log))
    ranked = _rank_urls(candidates, keyword)
    urls = []
    for url in ranked:
        normalized = _normalize_url(url)
        if normalized and normalized not in urls:
            urls.append(normalized)
        if len(urls) >= limit:
            break
    if urls:
        _log(on_log, "[공식URL] 자동 발견: " + ", ".join(urls))
    else:
        _log(on_log, "[공식URL] 자동 발견 결과 없음")
    return urls


def _naver_official_urls(keyword: str, on_log=None) -> list[str]:
    client_id = os.environ.get("NAVER_SEARCH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("NAVER_SEARCH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        _log(on_log, "[공식URL] 네이버 검색 API 키 없음")
        return []
    urls = []
    for query in _queries(keyword):
        _log(on_log, f"[공식URL] 검색: {query}")
        params = urllib.parse.urlencode({"query": query, "display": "10", "start": "1"})
        req = urllib.request.Request(
            f"{NAVER_WEB_URL}?{params}",
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            _log(on_log, f"[공식URL] 검색 실패: {exc}")
            continue
        for item in data.get("items", []):
            link = html.unescape(re.sub(r"<[^>]+>", "", item.get("link", ""))).strip()
            if _official_score(link) > 0:
                urls.append(link)
    return urls


def _queries(keyword: str) -> list[str]:
    base = keyword.strip()
    cleaned = re.sub(r"(사이트|홈페이지|누리집|신청방법|신청|자격|조건|대상)", " ", base).strip()
    queries = [
        f"{base} 공식",
        f"{base} 신청 공식",
        f"{cleaned or base} 공식 누리집",
    ]
    return [query for query in dict.fromkeys(queries) if query.strip()]


def _hint_urls(keyword: str) -> list[str]:
    urls = []
    normalized_keyword = keyword.replace(" ", "")
    for terms, hints in KEYWORD_URL_HINTS:
        if any(term.replace(" ", "") in normalized_keyword for term in terms):
            urls.extend(hints)
    return urls


def _rank_urls(urls: list[str], keyword: str) -> list[str]:
    scored = []
    compact_keyword = keyword.replace(" ", "").lower()
    for index, url in enumerate(urls):
        score = _official_score(url)
        compact_url = url.replace("-", "").replace("_", "").lower()
        if "소상공인정책자금" in keyword.replace(" ", "") and "ols.semas.or.kr" in url:
            score += 80
        if "소상공인" in keyword and any(domain in url for domain in ("semas.or.kr", "sbiz.or.kr", "mss.go.kr")):
            score += 40
        if any(token and token in compact_url for token in re.split(r"\s+", compact_keyword)):
            score += 5
        if score > 0:
            scored.append((score, -index, url))
    scored.sort(reverse=True)
    return [url for _, _, url in scored]


def _official_score(url: str) -> int:
    host = urllib.parse.urlparse(url).netloc.lower()
    if not host:
        return 0
    return max((score for domain, score in OFFICIAL_DOMAIN_SCORES.items() if host == domain or host.endswith("." + domain)), default=0)


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def _log(on_log, message: str) -> None:
    if on_log:
        on_log(message)
