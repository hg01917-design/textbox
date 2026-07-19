from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


GOV24_LIST_URL    = "https://api.odcloud.kr/api/gov24/v3/serviceList"
GOV24_DETAIL_URL  = "https://api.odcloud.kr/api/gov24/v3/serviceDetail"
GOV24_SUPPORT_URL = "https://api.odcloud.kr/api/gov24/v3/supportList"
BOKJIRO_LIST_URL   = "http://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"
BOKJIRO_DETAIL_URL = "http://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailV001"

DATA_DIR       = Path(__file__).parent.parent / "data"
GOV24_CACHE    = DATA_DIR / "gov24_all.json"
BOKJIRO_CACHE  = DATA_DIR / "bokjiro_all.json"

GOVERNMENT_BLOG_TYPES = {"정부지원", "복지"}
GOVERNMENT_KEYWORDS = (
    "지원금", "정부지원", "복지", "급여", "바우처", "보조금", "정책자금",
    "신청자격", "지원대상", "소상공인", "청년지원", "근로장려금", "자녀장려금",
    "세액공제", "공제", "대출", "보증", "수당", "장려금",
)

# 로컬 캐시에서 키워드당 반환할 최대 건수
LOCAL_TOP_N = 8
# 상세 API 호출 건수
DETAIL_TOP_N = 4


def fetch_public_source_context(keyword: str, blog_type: str, on_log=None) -> str:
    if not should_fetch_public_api(keyword, blog_type):
        _log(on_log, f"[공공API] {blog_type} 글은 공공API 자동 보강을 건너뜁니다: {keyword}")
        return ""
    contexts = []

    gov24 = _fetch_gov24_context(keyword, on_log=on_log)
    if gov24:
        contexts.append(gov24)

    bokjiro = _fetch_bokjiro_context(keyword, on_log=on_log)
    if bokjiro:
        contexts.append(bokjiro)

    support = _fetch_gov24_support_context(keyword, on_log=on_log)
    if support:
        contexts.append(support)

    return "\n\n".join(contexts)


def should_fetch_public_api(keyword: str, blog_type: str) -> bool:
    """블로그 유형과 키워드에 맞을 때만 정부24/복지로 자료를 가져온다."""
    if blog_type in GOVERNMENT_BLOG_TYPES:
        return True
    src = f"{keyword or ''} {blog_type or ''}".replace(" ", "")
    return any(token.replace(" ", "") in src for token in GOVERNMENT_KEYWORDS)


# ─── 정부24 ──────────────────────────────────────────────────────

def _fetch_gov24_context(keyword: str, on_log=None) -> str:
    key = _public_data_key()
    if not key:
        _log(on_log, "[공공API] PUBLIC_DATA_API_KEY 없음")
        return ""

    local = _load_json_cache(GOV24_CACHE)
    if local:
        _log(on_log, f"[공공API] 정부24 로컬 캐시 검색 ({len(local)}건): {keyword}")
        ranked = _rank_gov24(local, keyword)[:LOCAL_TOP_N]
        if ranked:
            pairs = []
            for item in ranked[:DETAIL_TOP_N]:
                svc_id = str(item.get("서비스ID", "")).strip()
                detail = _fetch_gov24_detail(key, svc_id, on_log) if svc_id else {}
                pairs.append((item, detail))
            for item in ranked[DETAIL_TOP_N:]:
                pairs.append((item, {}))
            return _format_gov24(keyword, pairs)
        _log(on_log, "[공공API] 정부24 캐시에서 관련 결과 없음 — API 검색")

    # 로컬 캐시 없으면 API 직접 검색
    for term in _search_terms(keyword):
        _log(on_log, f"[공공API] 정부24 API 검색: {term}")
        params = {
            "serviceKey": key,
            "page": "1",
            "perPage": "10",
            "cond[서비스명::LIKE]": term,
        }
        data = _get_json(GOV24_LIST_URL, params)
        items = data.get("data", []) if isinstance(data, dict) else []
        if not items:
            continue
        pairs = []
        for item in items[:DETAIL_TOP_N]:
            svc_id = str(item.get("서비스ID", "")).strip()
            detail = _fetch_gov24_detail(key, svc_id, on_log) if svc_id else {}
            pairs.append((item, detail))
        for item in items[DETAIL_TOP_N:]:
            pairs.append((item, {}))
        return _format_gov24(term, pairs)

    _log(on_log, "[공공API] 정부24 결과 없음")
    return ""


def _fetch_gov24_detail(key: str, svc_id: str, on_log=None) -> dict:
    params = {
        "serviceKey": key,
        "page": "1",
        "perPage": "1",
        "cond[서비스ID::EQ]": svc_id,
    }
    data = _get_json(GOV24_DETAIL_URL, params)
    items = data.get("data", []) if isinstance(data, dict) else []
    return items[0] if items else {}


def _fetch_gov24_support_context(keyword: str, on_log=None) -> str:
    key = _public_data_key()
    if not key:
        return ""
    for term in _search_terms(keyword)[:2]:
        _log(on_log, f"[공공API] 정부24 지원목록 검색: {term}")
        params = {
            "serviceKey": key,
            "page": "1",
            "perPage": "5",
            "cond[서비스명::LIKE]": term,
        }
        data = _get_json(GOV24_SUPPORT_URL, params)
        items = data.get("data", []) if isinstance(data, dict) else []
        if items:
            return _format_gov24_support(term, items)
    return ""


# ─── 복지로 ──────────────────────────────────────────────────────

def _fetch_bokjiro_context(keyword: str, on_log=None) -> str:
    key = _bokjiro_key() or _public_data_key()
    if not key:
        return ""

    local = _load_json_cache(BOKJIRO_CACHE)
    if local:
        _log(on_log, f"[공공API] 복지로 로컬 캐시 검색 ({len(local)}건): {keyword}")
        ranked = _rank_bokjiro(local, keyword)[:LOCAL_TOP_N]
        if ranked:
            pairs = []
            for item in ranked[:DETAIL_TOP_N]:
                serv_id = item.get("servId", "")
                detail = _fetch_bokjiro_detail(key, serv_id, on_log) if serv_id else None
                pairs.append((item, detail))
            for item in ranked[DETAIL_TOP_N:]:
                pairs.append((item, None))
            return _format_bokjiro_dicts(keyword, pairs)
        _log(on_log, "[공공API] 복지로 캐시에서 관련 결과 없음 — API 검색")

    # 로컬 캐시 없으면 API 직접 검색
    for term in _search_terms(keyword):
        _log(on_log, f"[공공API] 복지로 API 검색: {term}")
        params = {
            "serviceKey": key,
            "callTp": "L",
            "pageNo": "1",
            "numOfRows": "10",
            "srchKeyCode": "003",
            "searchWrd": term,
        }
        raw = _get_bytes(BOKJIRO_LIST_URL, params)
        items = _parse_bokjiro_items(raw) if raw else []
        if not items:
            continue
        pairs_xml = []
        for item in items[:DETAIL_TOP_N]:
            serv_id = _xml_text(item, "servId")
            detail = _fetch_bokjiro_detail(key, serv_id, on_log) if serv_id else None
            pairs_xml.append((item, detail))
        for item in items[DETAIL_TOP_N:]:
            pairs_xml.append((item, None))
        return _format_bokjiro_xml(term, pairs_xml)

    return ""


def _fetch_bokjiro_detail(key: str, serv_id: str, on_log=None) -> ET.Element | None:
    params = {"serviceKey": key, "servId": serv_id}
    raw = _get_bytes(BOKJIRO_DETAIL_URL, params)
    if not raw:
        return None
    try:
        root = ET.fromstring(raw)
        return root.find(".//servDtlList") or root.find(".//result")
    except ET.ParseError:
        return None


# ─── 로컬 캐시 관련도 점수 ───────────────────────────────────────

def _rank_gov24(items: list[dict], keyword: str) -> list[dict]:
    tokens = _tokens(keyword)
    scored = []
    for item in items:
        text = " ".join([
            item.get("서비스명", "") * 3,
            item.get("서비스목적요약", ""),
            item.get("지원대상", ""),
            item.get("지원내용", ""),
            item.get("소관기관명", ""),
        ])
        score = _score(text, tokens)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


def _rank_bokjiro(items: list[dict], keyword: str) -> list[dict]:
    tokens = _tokens(keyword)
    scored = []
    for item in items:
        text = " ".join([
            item.get("servNm", "") * 3,
            item.get("servDgst", ""),
            item.get("trgterIndvdlArray", ""),
            item.get("intrsThemaArray", ""),
            item.get("lifeArray", ""),
            item.get("jurMnofNm", ""),
        ])
        score = _score(text, tokens)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


def _tokens(keyword: str) -> list[str]:
    cleaned = re.sub(r"[\d년도\s]+", " ", keyword)
    return [w for w in re.split(r"\s+", cleaned.strip()) if len(w) >= 2]


def _score(text: str, tokens: list[str]) -> int:
    return sum(text.count(t) for t in tokens)


# ─── 포맷 ────────────────────────────────────────────────────────

def _format_gov24(term: str, detail_pairs: list[tuple[dict, dict]]) -> str:
    lines = [f"[정부24 공공서비스 — '{term}' 관련 (출처: data.go.kr)]"]
    for item, detail in detail_pairs:
        name = item.get("서비스명", "")
        if not name:
            continue
        lines.append(f"\n■ {name}")
        merged = {**item, **detail}
        for label, key, limit in (
            ("소관기관",  "소관기관명",     60),
            ("지원대상",  "지원대상",       300),
            ("지원내용",  "지원내용",       400),
            ("지원금액",  "지원금액내용",   200),
            ("신청기간",  "신청기간내용",   120),
            ("신청방법",  "신청방법내용",   200),
            ("구비서류",  "구비서류내용",   200),
            ("접수기관",  "접수기관내용",   150),
            ("요약",      "서비스목적요약", 200),
            ("상세URL",   "상세조회URL",    200),
        ):
            value = str(merged.get(key, "") or "").strip()
            if value:
                lines.append(f"  {label}: {value[:limit]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_gov24_support(term: str, items: list[dict]) -> str:
    lines = [f"[정부24 지원목록 — '{term}' 관련]"]
    for item in items[:3]:
        name = item.get("서비스명", "")
        if not name:
            continue
        lines.append(f"\n■ {name}")
        for label, key, limit in (
            ("지원금액", "지원금액",  200),
            ("지원조건", "지원조건",  300),
            ("신청방법", "신청방법",  200),
        ):
            value = str(item.get(key, "") or "").strip()
            if value:
                lines.append(f"  {label}: {value[:limit]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_bokjiro_dicts(term: str, pairs: list[tuple[dict, ET.Element | None]]) -> str:
    lines = [f"[복지로 복지서비스 — '{term}' 관련 (출처: bokjiro.go.kr)]"]
    for item, detail in pairs:
        name = item.get("servNm", "")
        if not name:
            continue
        lines.append(f"\n■ {name}")
        for label, key, limit in (
            ("소관기관",  "jurMnofNm",         60),
            ("지원대상",  "trgterIndvdlArray",  300),
            ("생애주기",  "lifeArray",          80),
            ("주제",      "intrsThemaArray",    100),
            ("급여유형",  "srvPvsnNm",          80),
            ("지급주기",  "sprtCycNm",          60),
            ("요약",      "servDgst",           300),
            ("상세URL",   "servDtlLink",        200),
        ):
            value = item.get(key, "")
            if value:
                lines.append(f"  {label}: {value[:limit]}")
        if detail is not None:
            for label, tag, limit in (
                ("신청방법",  "aplyMtdCn",     300),
                ("지원내용",  "servCn",        400),
                ("신청기간",  "aplyPrdCn",     150),
                ("구비서류",  "docList",       250),
                ("지원금액",  "sprtAmt",       150),
                ("문의처",    "inqplCtadrCn",  150),
            ):
                value = _xml_text(detail, tag)
                if value:
                    lines.append(f"  {label}: {value[:limit]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_bokjiro_xml(term: str, pairs: list[tuple[ET.Element, ET.Element | None]]) -> str:
    lines = [f"[복지로 복지서비스 — '{term}' 관련 (출처: bokjiro.go.kr)]"]
    for item, detail in pairs:
        name = _xml_text(item, "servNm")
        if not name:
            continue
        lines.append(f"\n■ {name}")
        for label, tag, limit in (
            ("소관기관",  "jurMnofNm",         60),
            ("지원대상",  "trgterIndvdlArray",  300),
            ("급여유형",  "srvPvsnNm",          80),
            ("지급주기",  "sprtCycNm",          60),
            ("요약",      "servDgst",           300),
            ("상세URL",   "servDtlLink",        200),
        ):
            value = _xml_text(item, tag)
            if value:
                lines.append(f"  {label}: {value[:limit]}")
        if detail is not None:
            for label, tag, limit in (
                ("신청방법",  "aplyMtdCn",     300),
                ("지원내용",  "servCn",        400),
                ("신청기간",  "aplyPrdCn",     150),
                ("구비서류",  "docList",       250),
                ("지원금액",  "sprtAmt",       150),
                ("문의처",    "inqplCtadrCn",  150),
            ):
                value = _xml_text(detail, tag)
                if value:
                    lines.append(f"  {label}: {value[:limit]}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ─── 유틸 ────────────────────────────────────────────────────────

def _load_json_cache(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _search_terms(keyword: str) -> list[str]:
    cleaned = re.sub(
        r"(신청\s*방법|신청\s*자격|조건|금액|대상|사이트|홈페이지|누리집|총정리|완벽정리|한눈에|\d{4}년?|최신|기준)",
        " ",
        keyword,
        flags=re.IGNORECASE,
    )
    words = [w for w in re.split(r"\s+", cleaned.strip()) if len(w) >= 2]
    terms = []
    if words:
        terms.append(" ".join(words[:2]))
        terms.append(words[0])
    terms.append(keyword)
    deduped = []
    for t in terms:
        t = t.strip()
        if t and t not in deduped:
            deduped.append(t)
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
        req = urllib.request.Request(
            full_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json, application/xml"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read(2_000_000)
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


def _xml_text(item: ET.Element | None, tag: str) -> str:
    if item is None:
        return ""
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
