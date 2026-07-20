from __future__ import annotations

from datetime import datetime

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
