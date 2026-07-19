from __future__ import annotations

from pathlib import Path

from config import PROMPTS_DIR


PROMPT_BY_TYPE = {
    "정부지원": "government.txt",
    "여행": "travel.txt",
    "IT": "it.txt",
    "생활정보": "life.txt",
    "일반": "default.txt",
    "리뷰": "review.txt",
    "네이버": "naver.txt",
}


DEFAULT_PROMPT = """
검색 유입 독자가 끝까지 읽을 수 있는 블로그 초안을 작성해 주세요.

블로그 유형: {blog_type}
주제 범위: {theme}
문체: {tone}
메인 키워드: {keyword}
관련 키워드: {related_keywords}
기준연도: {target_year}

공식 근거 자료(source-url/공공API):
{source_context}

카드 이미지 계획:
{card_plan}

작성 규칙:
- 한국어로 작성
- 존대어를 기본으로 작성하되, 너무 딱딱하지 않은 자연스러운 구어체를 사용할 것
- 반말, 명령조, 과한 친근체는 쓰지 말 것
- 과장, 허위 사실, 출처 없는 확정 표현 금지
- 공식 근거 자료가 있으면 해당 자료를 최우선 근거로 사용
- 공식 근거 자료에 없는 금액/기간/대상 조건은 확정값처럼 쓰지 말 것
- {year_rules}
- HTML 태그를 쓰지 말 것
- 코드블록을 쓰지 말 것
- 이미지 프롬프트, 파일명, alt태그 같은 내부 작업 문구를 본문에 쓰지 말 것
- 원문 자료를 그대로 요약만 하지 말고, 우선순위·비교·실전 판단 등 이 글만의 추가적인 가치를 더할 것 (구글 "Helpful Content" 기준 — 단순 요약이 아닌 실질적 가치)
- 검색엔진 노출만을 노린 나열식 정보 대신, 실제 방문자가 읽고 바로 활용할 수 있는 내용으로 작성할 것
- 제목은 판단형/문제해결형으로 작성하되, "~습니다"/"~있습니다"처럼 완결된 문장으로 끝내지 말 것 — 명사형·구(phrase)로 끝맺을 것 (예: "먼저 확인할 순서", "놓치기 쉬운 이유", "헷갈리기 쉬운 부분")
- 제목에 "총정리", "한눈에", "완벽정리"를 쓰지 말 것
- 메인 키워드를 제목과 첫 문단에 자연스럽게 포함할 것 (약어로 대체하지 말고 최소 1회는 그대로 포함)
- 본문 시작은 소제목 없이 서론 2문단 이상으로 시작
- 첫 H2/소제목은 서론 다음에 배치
- 분량은 다루는 내용의 깊이에 따라 자연스럽게 정할 것 — 글자수를 채우려고 내용을 늘리지 말 것 (참고 기준: 1,800자 내외)
- H2 소제목 5개 이상
- 서론 또는 본문 초반에 핵심 요약을 자연스러운 문장으로 포함할 것 (예: "먼저 확인할 것은 ~", "핵심만 먼저 구분해야 하는 부분은 ~", "이렇게 나눠서 보면 됩니다")
- 체크리스트, FAQ를 자연스럽게 포함
- 마지막은 기계적인 CTA 없이 자연스럽게 마무리

권장 구성:
{structure}

아래 형식을 정확히 지킬 것:

{output_format}
""".strip()


def ensure_prompt_files() -> None:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
        "default.txt": DEFAULT_PROMPT,
        "government.txt": DEFAULT_PROMPT + "\n\n정부지원 글은 대상, 신청기간, 신청방법, 필요서류, 주의사항을 특히 분명히 나눠 쓰세요.",
        "travel.txt": DEFAULT_PROMPT + "\n\n여행 글은 동선, 비용, 교통, 숙소/맛집, 계절별 주의사항을 실제 일정처럼 연결하세요.",
        "life.txt": DEFAULT_PROMPT + "\n\n생활정보 글은 준비물, 순서, 비용/시간 절약, 자주 하는 실수를 구체적으로 쓰세요.",
        "it.txt": DEFAULT_PROMPT + "\n\nIT 글은 문제 원인, 해결 순서, 설정 체크리스트, 비교 기준을 명확히 쓰세요.",
        "review.txt": DEFAULT_PROMPT + "\n\n리뷰 글은 실제 사용 경험, 장단점, 다른 제품과의 비교, 이런 분께 추천하는지를 구체적으로 쓰세요.",
    }
    for name, text in defaults.items():
        path = PROMPTS_DIR / name
        if not path.exists():
            path.write_text(text + "\n", encoding="utf-8")


def prompt_names() -> list[str]:
    ensure_prompt_files()
    return sorted(path.name for path in PROMPTS_DIR.glob("*.txt"))


def prompt_for_blog_type(blog_type: str) -> str:
    ensure_prompt_files()
    return PROMPT_BY_TYPE.get(blog_type, "default.txt")


def prompt_path(name: str) -> Path:
    ensure_prompt_files()
    safe = Path(name).name or "default.txt"
    path = PROMPTS_DIR / safe
    if not path.exists():
        path = PROMPTS_DIR / "default.txt"
    return path


def load_prompt_template(name: str) -> str:
    return prompt_path(name).read_text(encoding="utf-8")
