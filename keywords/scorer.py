from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.parse
import urllib.request


def _ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def opportunity_score(volume: int, competition: int) -> float:
    denom = volume + competition
    return (volume * volume) / denom if denom > 0 else 0.0


def _signature(timestamp: str, method: str, uri: str, secret_key: str) -> str:
    msg = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def get_search_volume(keyword: str) -> int:
    api_key = os.environ.get("NAVER_API_KEY", "")
    secret_key = os.environ.get("NAVER_SECRET_KEY", "")
    customer_id = os.environ.get("NAVER_CUSTOMER_ID", "")
    if not api_key or not secret_key or not customer_id:
        return 0

    if api_key.isdigit() and not customer_id.isdigit():
        api_key, customer_id = customer_id, api_key

    timestamp = str(int(time.time() * 1000))
    uri = "/keywordstool"
    hint = keyword.replace(" ", "")
    params = urllib.parse.urlencode({"hintKeywords": hint, "showDetail": "1"})
    headers = {
        "X-Timestamp": timestamp,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": _signature(timestamp, "GET", uri, secret_key),
    }

    try:
        req = urllib.request.Request(f"https://api.searchad.naver.com{uri}?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            data = json.loads(resp.read())
        items = data.get("keywordList", [])
        exact = [item for item in items if item.get("relKeyword", "").replace(" ", "") == hint]
        item = (exact or items or [{}])[0]
        pc = int(item.get("monthlyPcQcCnt", 0) or 0)
        mobile = int(item.get("monthlyMobileQcCnt", 0) or 0)
        return pc + mobile
    except Exception:
        return 0


def score_keyword(keyword: str, competition: int) -> dict:
    volume = get_search_volume(keyword)
    score_competition = competition if competition > 0 else 0
    return {
        "keyword": keyword,
        "volume": volume,
        "competition": competition,
        "score": round(opportunity_score(volume, score_competition), 2),
    }
