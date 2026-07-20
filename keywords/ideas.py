from __future__ import annotations

import json
from datetime import datetime

from config import DATA_DIR, DRAFTS_DIR, load_env
from .analyzer import difficulty_label, get_blog_count
from .scorer import get_search_volume, opportunity_score
from .suggest import google_suggest, naver_suggest


_IDEAS = {
    "정부지원": [
        "소상공인 정책자금 신청방법",
        "청년 월세 지원 신청 조건",
        "근로장려금 신청 자격",
        "긴급복지 생계지원 신청방법",
        "국민내일배움카드 신청방법",
        "부모급여 신청 방법",
        "에너지바우처 신청 대상",
        "기초연금 수급자격 확인",
    ],
    "여행": [
        "오사카 3박4일 여행 코스",
        "도쿄 가족여행 코스",
        "다낭 공항 픽업 예약 팁",
        "제주도 비오는 날 여행 코스",
        "후쿠오카 첫 여행 코스",
        "여수 밤바다 여행 코스",
        "부산 1박2일 가족여행 코스",
        "나트랑 자유여행 준비물",
    ],
    "IT": [
        "아이폰 저장공간 부족 해결",
        "갤럭시 배터리 빨리 닳음 해결",
        "카카오톡 사진 원본 보내는 방법",
        "구글 드라이브 용량 정리 방법",
        "맥북 느려졌을 때 해결 방법",
        "와이파이 연결은 되는데 인터넷 안됨",
        "노트북 발열 줄이는 방법",
        "스마트폰 사진 백업 방법",
    ],
    "생활정보": [
        "여름철 싱크대 배수구 냄새 제거",
        "러브버그 퇴치방법",
        "장마철 빨래 냄새 제거",
        "에어컨 전기세 줄이는 방법",
        "냉장고 냄새 제거 방법",
        "화장실 곰팡이 제거 방법",
        "초파리 없애는 방법",
        "옷장 습기 제거 방법",
    ],
    "일반": [
        "아침 루틴 만드는 방법",
        "집중력 높이는 방법",
        "생활비 절약 방법",
        "시간관리 잘하는 방법",
        "중고거래 사기 예방법",
        "이사 전 체크리스트",
        "혼자 사는 집 정리 방법",
        "건강검진 전 주의사항",
    ],
    "리뷰": [
        "여름 냉장고 정리 수납템 추천",
        "실리콘 접이식 밀폐용기 추천",
        "무선 넥밴드 선풍기 추천",
        "욕실 청소솔 추천",
        "주방 음식물 쓰레기통 추천",
        "장마철 제습용품 추천",
        "여름 이불 커버 추천",
        "휴대용 보조배터리 추천",
    ],
}

_MONTHLY_IDEAS = {
    1: ["연말정산 간소화 사용방법", "겨울 난방비 절약 방법"],
    2: ["입학 준비물 체크리스트", "봄 이사 준비 체크리스트"],
    3: ["봄맞이 대청소 순서", "미세먼지 환기 방법"],
    4: ["봄꽃 여행 코스", "알레르기 비염 관리 방법"],
    5: ["가정의달 선물 추천", "여름옷 정리 방법"],
    6: ["장마철 제습 방법", "초파리 없애는 방법"],
    7: ["여름철 싱크대 배수구 냄새 제거", "에어컨 전기세 줄이는 방법"],
    8: ["휴가철 여행 준비물", "여름 빨래 냄새 제거"],
    9: ["추석 선물 추천", "가을 옷장 정리 방법"],
    10: ["단풍 여행 코스", "환절기 감기 예방 방법"],
    11: ["김장 준비물 체크리스트", "겨울 이불 관리 방법"],
    12: ["연말정산 준비 서류", "겨울철 결로 방지 방법"],
}


def keyword_ideas(blog_type: str, limit: int = 12) -> list[str]:
    """Return ready-to-use seed keywords for users who do not know what to type."""
    rows = keyword_opportunities(blog_type, limit=limit)
    if rows:
        return [row["keyword"] for row in rows]
    return _seed_keywords(blog_type, limit=limit)


def _seed_keywords(blog_type: str, limit: int = 12) -> list[str]:
    base = list(_IDEAS.get(blog_type, _IDEAS["일반"]))
    if blog_type in {"생활정보", "일반"}:
        base = list(_MONTHLY_IDEAS.get(datetime.now().month, [])) + base

    expanded = []
    for seed in base[:4]:
        expanded.extend(naver_suggest(seed, limit=3))
        expanded.extend(google_suggest(seed, limit=3))

    seen = set()
    result = []
    for item in base + expanded:
        item = " ".join(item.split())
        if len(item) < 2 or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _intent_score(keyword: str, blog_type: str) -> int:
    terms = {
        "정부지원": ["신청", "조건", "대상", "자격", "서류", "지원금", "혜택"],
        "여행": ["코스", "예약", "준비물", "비용", "숙소", "패스", "공항", "가족여행"],
        "IT": ["해결", "방법", "추천", "비교", "설정", "백업", "오류"],
        "생활정보": ["제거", "줄이는", "방법", "청소", "냄새", "전기세", "습기", "곰팡이"],
        "리뷰": ["추천", "비교", "후기", "선택", "가성비", "실사용"],
    }.get(blog_type, ["방법", "추천", "비교", "체크리스트"])
    score = sum(12 for term in terms if term in keyword)
    words = len(keyword.split())
    if 3 <= words <= 6:
        score += 25
    elif words > 6:
        score += 10
    if any(term in keyword for term in ["추천", "예약", "비교", "지원금", "신청"]):
        score += 15
    return score


def _blocked_keyword(keyword: str) -> bool:
    lowered = keyword.lower()
    blocked_terms = ["디시", "dcinside", "클리앙", "뽐뿌", "더쿠", "나무위키", "reddit", "펨코"]
    return any(term in lowered for term in blocked_terms)


def _recent_keywords() -> set[str]:
    recent = set()
    log_path = DATA_DIR / "publish_log.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-120:]:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            title = str(entry.get("title", "")).strip()
            if title:
                recent.add(title)
    for path in sorted(DRAFTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for value in [payload.get("keyword"), payload.get("seed_keyword"), payload.get("draft", {}).get("title")]:
            if value:
                recent.add(str(value).strip())
    return recent


def _recently_used(keyword: str, recent: set[str]) -> bool:
    compact = keyword.replace(" ", "")
    for item in recent:
        item_compact = item.replace(" ", "")
        if compact and (compact in item_compact or item_compact in compact):
            return True
    return False


def _competition_score(competition: int) -> int:
    if competition < 0:
        return 35
    if competition < 3_000:
        return 80
    if competition < 10_000:
        return 95
    if competition < 50_000:
        return 75
    if competition < 120_000:
        return 35
    return 10


def keyword_opportunities(blog_type: str, limit: int = 12) -> list[dict]:
    """Rank keyword candidates for revenue-oriented posts.

    Sources: curated seasonal/category seeds + Naver/Google autocomplete.
    Signals: search volume when SearchAd keys exist, Naver blog competition, and
    commercial/problem-solving intent for the selected blog type.
    """
    load_env()
    recent = _recent_keywords()
    candidates = [
        keyword for keyword in _seed_keywords(blog_type, limit=24)
        if not _blocked_keyword(keyword) and not _recently_used(keyword, recent)
    ]
    if not candidates:
        candidates = [keyword for keyword in _seed_keywords(blog_type, limit=24) if not _blocked_keyword(keyword)]
    rows = []
    for keyword in candidates:
        competition = get_blog_count(keyword)
        volume = get_search_volume(keyword)
        if volume > 0:
            score = opportunity_score(volume, max(competition, 0)) + _intent_score(keyword, blog_type)
        else:
            score = _competition_score(competition) + _intent_score(keyword, blog_type)
        rows.append({
            "keyword": keyword,
            "volume": volume,
            "competition": competition,
            "difficulty": difficulty_label(competition),
            "score": round(score, 2),
        })
    rows.sort(key=lambda row: (-row["score"], row["competition"] if row["competition"] >= 0 else 999999999))
    return rows[:limit]


def best_keyword_idea(blog_type: str) -> dict:
    rows = keyword_opportunities(blog_type, limit=1)
    if rows:
        return rows[0]
    seeds = _seed_keywords(blog_type, limit=1)
    keyword = seeds[0] if seeds else "생활비 절약 방법"
    return {"keyword": keyword, "volume": 0, "competition": -1, "difficulty": "unknown", "score": 0}
