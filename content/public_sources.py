from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


GOV24_SERVICE_URL = "https://api.odcloud.kr/api/gov24/v3/serviceList"
BOKJIRO_LIST_URL = "http://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"


def fetch_public_source_context(keyword: str, blog_type: str, on_log=None) -> str:
    if blog_type != "정부지원":
        return ""
    contexts = []
    bokjiro = _fetch_bokjiro_context(keyword, on_log=on_log)
    if bokjiro:
        contexts.append(bokjiro)
    gov24 = _fetch_gov24_context(keyword, on_log=on_log)
    if gov24:
        contexts.append(gov24)
    return "\n\n".join(contexts)


def _fetch_gov24_context(keyword: str, on_log=None) -> str:
    key = _public_data_key()
    if not key:
        _log(on_log, "[공공API] PUBLIC_DATA_API_KEY 없음")
        return ""
    search_terms = _search_terms(keyword)
    for term in search_terms:
        _log(on_log, f"[공공API] 정부24 검색: {term}")
        params = {
            "serviceKey": key,
            "page": "1",
            "perPage": "5",
            "cond[서비스명::LIKE]": term,
        }
        data = _get_json(GOV24_SERVICE_URL, params)
        items = data.get("data", []) if isinstance(data, dict) else []
        if items:
            return _format_gov24(term, items)
    _log(on_log, "[공공API] 정부24 검색 결과 없음")
    return ""


def _fetch_bokjiro_context(keyword: str, on_log=None) -> str:
    key = _bokjiro_key() or _public_data_key()
    if not key:
        return ""
    # 복지로는 소상공인 정책자금 같은 사업자 지원에는 부정확할 수 있어 복지성 키워드에만 사용.
    if not any(token in keyword for token in ("복지", "급여", "수당", "바우처", "청년", "아동", "노인", "장애", "한부모")):
        return ""
    for term in _search_terms(keyword):
        _log(on_log, f"[공공API] 복지로 검색: {term}")
        params = {
            "serviceKey": key,
            "callTp": "L",
            "pageNo": "1",
            "numOfRows": "5",
            "srchKeyCode": "003",
            "searchWrd": term,
        }
        raw = _get_bytes(BOKJIRO_LIST_URL, params)
        items = _parse_bokjiro_items(raw) if raw else []
        if items:
            return _format_bokjiro(term, items)
    return ""


def _format_gov24(term: str, items: list[dict]) -> str:
    lines = [f"[공공데이터포털 정부24 공공서비스 ('{term}' 검색 결과)]"]
    for item in items[:3]:
        name = item.get("서비스명", "")
        if not name:
            continue
        lines.append(f"\n[서비스] {name}")
        for label, key, limit in (
            ("소관기관", "소관기관명", 100),
            ("지원대상", "지원대상", 180),
            ("지원내용", "지원내용", 240),
            ("요약", "서비스목적요약", 180),
            ("상세", "상세조회URL", 300),
        ):
            value = str(item.get(key, "") or "").strip()
            if value:
                lines.append(f"  {label}: {value[:limit]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_bokjiro(term: str, items: list[ET.Element]) -> str:
    lines = [f"[공공데이터포털 복지로 복지서비스 ('{term}' 검색 결과)]"]
    for item in items[:3]:
        name = _xml_text(item, "servNm")
        if not name:
            continue
        lines.append(f"\n[서비스] {name}")
        for label, tag, limit in (
            ("소관기관", "jurMnofNm", 100),
            ("지원대상", "trgterIndvdlArray", 180),
            ("급여유형", "srvPvsnNm", 80),
            ("지급주기", "sprtCycNm", 80),
            ("요약", "servDgst", 240),
            ("상세", "servDtlLink", 300),
        ):
            value = _xml_text(item, tag)
            if value:
                lines.append(f"  {label}: {value[:limit]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _search_terms(keyword: str) -> list[str]:
    cleaned = re.sub(
        r"(신청\s*방법|신청\s*자격|조건|금액|대상|사이트|홈페이지|누리집|총정리|완벽정리|한눈에|\d{4}년?|최신|기준)",
        " ",
        keyword,
        flags=re.IGNORECASE,
    )
    words = [word for word in re.split(r"\s+", cleaned.strip()) if len(word) >= 2]
    terms = []
    if words:
        terms.append(" ".join(words[:2]))
        terms.append(words[0])
    terms.append(keyword)
    deduped = []
    for term in terms:
        term = term.strip()
        if term and term not in deduped:
            deduped.append(term)
    return deduped[:4]


def _get_json(url: str, params: dict) -> dict:
    params.setdefault("_type", "json")
    raw = _get_bytes(url, params)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _get_bytes(url: str, params: dict) -> bytes:
    full_url = url + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(1_000_000)
    except Exception:
        return b""


def _parse_bokjiro_items(raw: bytes) -> list[ET.Element]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    code = root.findtext(".//resultCode") or ""
    if code not in ("", "0", "00", "40"):
        return []
    return root.findall(".//servList")


def _xml_text(item: ET.Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def _public_data_key() -> str:
    raw = os.environ.get("PUBLIC_DATA_API_KEY", "").strip()
    return urllib.parse.unquote(raw) if raw else ""


def _bokjiro_key() -> str:
    raw = os.environ.get("BOKJIRO_API_KEY", "").strip()
    return urllib.parse.unquote(raw) if raw else ""


def _log(on_log, message: str) -> None:
    if on_log:
        on_log(message)
