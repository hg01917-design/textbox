from __future__ import annotations

import re


FORBIDDEN_MARKERS = [
    "===제목===",
    "===본문===",
    "===태그===",
    "[출처 필요]",
    "[검증 필요]",
    "프롬프트:",
    "파일명:",
]


def check_draft(draft: dict, keyword: str, min_chars: int = 1200) -> dict:
    warnings = []
    title = draft.get("title", "")
    body = draft.get("body", "")
    plain = re.sub(r"[#*`>|\-{}\[\]()]", "", body)
    char_count = len(re.sub(r"\s+", "", plain))

    if draft.get("provider") == "template" or draft.get("used_ai") is False or draft.get("publishable") is False:
        warnings.append("template_fallback_not_publishable")
    if _template_leak(body):
        warnings.append("template_text_left")
    if _bad_government_heading(body):
        warnings.append("awkward_template_heading")

    if not _keyword_present(title + body, keyword):
        warnings.append("keyword_missing")
    if len(title) < 8:
        warnings.append("title_too_short")
    if len(title) > 80:
        warnings.append("title_too_long")
    if char_count < min_chars:
        warnings.append(f"body_too_short:{char_count}")
    if any(marker in body or marker in title for marker in FORBIDDEN_MARKERS):
        warnings.append("internal_marker_left")
    if len(draft.get("tags", [])) < 3:
        warnings.append("few_tags")
    if _starts_like_definition(body, keyword):
        warnings.append("weak_opening_definition_start")
    if not _has_intro_before_heading(body):
        warnings.append("missing_intro_before_heading")
    if _awkward_search_opening(body):
        warnings.append("awkward_search_opening")
    if _casual_banmal_endings(body):
        warnings.append("banmal_tone_detected")
    if not _first_paragraph_has_keyword(body, keyword):
        warnings.append("first_paragraph_keyword_missing")
    if not _has_summary_box(body):
        warnings.append("summary_box_missing")
    if not _has_checklist(body):
        warnings.append("checklist_missing")
    if not _has_faq(body):
        warnings.append("faq_missing")
    if _generic_title(title):
        warnings.append("generic_title")
    if _mechanical_cta(body):
        warnings.append("mechanical_cta")
    warnings.extend(_ai_style_warnings(title + "\n" + body))
    stale = _stale_year_warnings(title + "\n" + body, keyword)
    warnings.extend(stale)

    return {
        "passed": not warnings,
        "warnings": warnings,
        "char_count": char_count,
    }


def _first_paragraph_has_keyword(body: str, keyword: str) -> bool:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("##")]
    if not paragraphs:
        return False
    intro = " ".join(paragraphs[:2])
    return _keyword_present(intro, keyword, min_ratio=0.75)


def _has_intro_before_heading(body: str) -> bool:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    intro_count = 0
    for paragraph in paragraphs:
        if paragraph.startswith("#"):
            break
        intro_count += 1
        if intro_count >= 2:
            return True
    return False


def _keyword_present(text: str, keyword: str, min_ratio: float = 0.75) -> bool:
    compact_text = text.replace(" ", "").lower()
    compact_keyword = keyword.replace(" ", "").lower()
    if compact_keyword in compact_text:
        return True
    tokens = [token.lower() for token in keyword.split() if len(token) >= 2]
    if not tokens:
        return False
    matched = sum(1 for token in tokens if token in text.lower())
    return matched / len(tokens) >= min_ratio


def _starts_like_definition(body: str, keyword: str) -> bool:
    text = re.sub(r"^[#\s]+", "", body.strip())
    if text.startswith(f"{keyword}은") or text.startswith(f"{keyword}는"):
        return True
    return bool(re.match(r"^[^\n]{0,40}(은|는) .{0,40}(제도|서비스|프로그램|방법)입니다", text))


def _awkward_search_opening(body: str) -> bool:
    first = " ".join([p.strip() for p in body.split("\n\n") if p.strip()][:2])
    patterns = [
        r"^\"?.+\"?이라고\s+검색",
        r"검색해놓고",
        r"검색하는\s+분들이\s+많습니다",
        r"검색하면\s+결과가\s+너무\s+많",
    ]
    return any(re.search(pattern, first) for pattern in patterns)


def _has_summary_box(body: str) -> bool:
    patterns = [
        "먼저 30초", "30초 요약", "핵심 요약", "빠른 요약", "먼저 확인", "먼저 이 부분",
        "먼저 구분", "구분해야", "나눠서 보면", "이 부분만 먼저", "덜 헷갈",
    ]
    return any(pattern in body for pattern in patterns)


def _has_checklist(body: str) -> bool:
    return "체크리스트" in body or "[ ]" in body or "- [ ]" in body or "나는 대상" in body


def _has_faq(body: str) -> bool:
    return "FAQ" in body or len(re.findall(r"Q\d*\.", body)) >= 2 or len(re.findall(r"### Q", body)) >= 2


def _generic_title(title: str) -> bool:
    generic = ["총정리", "한눈에", "완벽정리", "완벽 가이드"]
    if any(word in title for word in generic):
        return True
    problem_words = ["받을 수", "헷갈", "먼저", "구분", "조건", "아닙니다", "어디서", "어떻게"]
    return not any(word in title for word in problem_words)


def _stale_year_warnings(text: str, keyword: str) -> list[str]:
    target_years = [int(year) for year in re.findall(r"20\d{2}", keyword)]
    if not target_years:
        return []
    target_year = max(target_years)
    warnings = []
    stale_phrases = [
        r"20\d{2}년\s*기준",
        r"작년\s*기준",
        r"이전\s*기준",
        r"과거\s*기준",
        r"전년도\s*기준",
    ]
    for pattern in stale_phrases:
        for match in re.finditer(pattern, text):
            phrase = match.group(0)
            years = [int(year) for year in re.findall(r"20\d{2}", phrase)]
            if not years or any(year < target_year for year in years):
                warnings.append(f"stale_year_phrase:{phrase}")
    for year in sorted({int(year) for year in re.findall(r"20\d{2}", text)}):
        if year < target_year:
            warnings.append(f"past_year_reference:{year}")
    return warnings


def _ai_style_warnings(text: str) -> list[str]:
    warnings = []
    cliche_limits = {
        "확인하세요": 7,
        "가능합니다": 7,
        "필요합니다": 7,
        "정리했습니다": 3,
        "한눈에": 1,
        "핵심 요약": 2,
        "총정리": 1,
        "다음과 같습니다": 0,
        "살펴보겠습니다": 0,
        "알아보겠습니다": 0,
        "도움이 됩니다": 1,
        "놓치지 마세요": 0,
        "완벽정리": 0,
        "꼼꼼히": 0,
        "지금 바로 할 수 있는 것": 0,
        "세 가지만 하세요": 0,
        "이 세 단계만 해도": 0,
        "후보군이 좁혀집니다": 0,
        "단계별로 진행하세요": 0,
        "정리하면": 1,
        "검색해놓고": 0,
        "검색하는 분들이 많습니다": 0,
    }
    for phrase, limit in cliche_limits.items():
        count = text.count(phrase)
        if count > limit:
            warnings.append(f"ai_cliche:{phrase}:{count}")

    endings = re.findall(r"[가-힣A-Za-z0-9\s]{3,30}(?:합니다|됩니다|있습니다)\.", text)
    if len(endings) > 30:
        warnings.append(f"too_many_formal_endings:{len(endings)}")

    headings = re.findall(r"^##+\s+(.+)$", text, flags=re.MULTILINE)
    generic_heading_count = sum(
        1 for heading in headings
        if any(word in heading for word in ("핵심", "요약", "정리", "체크리스트", "FAQ"))
    )
    if headings and generic_heading_count / len(headings) >= 0.6:
        warnings.append("template_like_headings")
    return warnings


def _casual_banmal_endings(body: str) -> bool:
    text = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    # Avoid false positives from headings/lists by checking sentence endings only.
    patterns = [
        r"[가-힣]{2,}(?:다|요|죠|네요|습니다)\s*[.!?]",  # 존대/서술 종결은 제외용 기준
        r"[가-힣]{2,}(?:한다|된다|있다|없다|맞다|아니다|좋다|쉽다|어렵다)\s*[.!?]",
        r"[가-힣]{2,}(?:해라|하자|봐라|된다니까|거든)\s*[.!?]",
    ]
    formal_or_polite = len(re.findall(patterns[0], text))
    banmal = len(re.findall(patterns[1], text)) + len(re.findall(patterns[2], text))
    return banmal >= 4 and banmal > formal_or_polite * 0.4


def _mechanical_cta(body: str) -> bool:
    tail = body[-800:]
    mechanical_patterns = [
        r"지금\s*바로\s*할\s*수\s*있는\s*것",
        r"세\s*가지만\s*하(?:세요|면)",
        r"이\s*세\s*단계",
        r"1단계.*2단계.*3단계",
        r"후보군이\s*.*좁혀집니다",
        r"단계별로\s*진행하세요",
    ]
    return any(re.search(pattern, tail, flags=re.DOTALL) for pattern in mechanical_patterns)


def _template_leak(body: str) -> bool:
    phrases = [
        "검수용 초안",
        "관련 키워드로 확장할 부분",
        "내부 링크 후보",
        "발행 전 검수용",
        "공식 근거 자료로 확인된 정보만 확정값으로 보완",
        "메인 키워드와 내 지역 또는 상황이 일치한다",
        "청년 지원금이라도 어떤 건 나이 제한",
        "이 키워드들은 본문 소제목",
    ]
    return any(phrase in body for phrase in phrases)


def _bad_government_heading(body: str) -> bool:
    bad_headings = [
        "내 상황에서 먼저 볼 조건",
        "관련 키워드로 확장할 부분",
    ]
    return any(f"## {heading}" in body for heading in bad_headings)
