from __future__ import annotations


CARD_TYPES = {
    "정부지원": ["대상 조건", "신청 방법", "필요 서류", "주의사항"],
    "여행": ["추천 동선", "예상 비용", "준비물", "현장 체크"],
    "생활정보": ["핵심 요약", "준비물", "진행 순서", "실수 방지"],
    "IT": ["문제 원인", "해결 순서", "설정 체크", "비교 기준"],
    "일반": ["핵심 요약", "체크리스트", "주의사항"],
}


def card_plan(blog_type: str) -> str:
    cards = CARD_TYPES.get(blog_type, CARD_TYPES["일반"])
    return "\n".join(f"- {name} 카드" for name in cards)


def card_items(blog_type: str, keyword: str) -> list[dict]:
    items = []
    for idx, name in enumerate(CARD_TYPES.get(blog_type, CARD_TYPES["일반"]), start=1):
        items.append({
            "index": idx,
            "title": f"{keyword} {name}",
            "subtitle": name,
            "bullets": _default_bullets(blog_type, name),
        })
    return items


def _default_bullets(blog_type: str, name: str) -> list[str]:
    if blog_type == "여행":
        if "동선" in name:
            return ["출발지와 도착지를 먼저 정하기", "이동시간이 긴 구간은 앞쪽에 배치", "비 오는 날 대체 코스 준비"]
        if "비용" in name:
            return ["교통비와 숙박비를 분리해서 계산", "성수기 가격 변동 확인", "현장 결제 비용 따로 남기기"]
        return ["예약 가능 여부 확인", "이동 동선과 영업시간 확인", "혼잡 시간대 피하기"]
    if blog_type == "정부지원":
        return ["나이/거주지/소득 조건 확인", "신청 기간과 예산 소진 여부 확인", "필요 서류를 먼저 준비"]
    if blog_type == "IT":
        return ["기기와 OS 지원 여부 확인", "무료/유료 기능 차이 확인", "권한과 보안 설정 점검"]
    if blog_type == "생활정보":
        return ["준비물을 먼저 확인", "순서를 바꾸면 실패하기 쉬운 부분 체크", "비용과 시간을 따로 계산"]
    return ["핵심 조건 확인", "내 상황과 맞는지 비교", "실수하기 쉬운 부분 점검"]
