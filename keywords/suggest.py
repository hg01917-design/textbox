from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request


def _ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def google_suggest(query: str, limit: int = 10) -> list[str]:
    url = (
        "https://suggestqueries.google.com/complete/search"
        f"?client=firefox&hl=ko&q={urllib.parse.quote(query)}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5, context=_ssl_context()) as resp:
            data = json.loads(resp.read())
        return [item for item in data[1] if item and item != query][:limit]
    except Exception:
        return []


def naver_suggest(query: str, limit: int = 10) -> list[str]:
    params = urllib.parse.urlencode({
        "q": query,
        "q_enc": "UTF-8",
        "st": 100,
        "frm": "nv",
        "r_format": "json",
        "r_enc": "UTF-8",
    })
    url = f"https://ac.search.naver.com/nx/ac?{params}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.naver.com"},
        )
        with urllib.request.urlopen(req, timeout=5, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items", [[]])[0]
        return [item[0] for item in items[:limit] if item]
    except Exception:
        return []


def expand_keyword(keyword: str, limit: int = 20) -> list[str]:
    compact = keyword.replace(" ", "")
    candidates = [keyword]
    candidates.extend(google_suggest(keyword, limit=limit))
    if compact != keyword:
        candidates.extend(google_suggest(compact, limit=limit))
    candidates.extend(naver_suggest(keyword, limit=limit))

    seen = set()
    result = []
    for item in candidates:
        item = " ".join(item.split())
        if len(item) < 2 or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result
