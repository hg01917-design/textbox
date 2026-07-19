from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.request

from config import get_blog_profile
from .cards import card_plan
from .parser import parse_ai_response
from .prompting import load_prompt_template, prompt_for_blog_type
from .sanitizer import sanitize_draft


def _build_prompt(
    keyword: str,
    blog_type: str,
    related_keywords: list[str],
    source_context: str = "",
    prompt_name: str = "",
    target_context: str = "",
) -> str:
    profile = get_blog_profile(blog_type)
    related = ", ".join(related_keywords[:8]) if related_keywords else "없음"
    structure = _engagement_structure(blog_type)
    target_year = _target_year(keyword)
    year_rules = _year_rules(target_year)
    source_block = source_context.strip() or "제공된 공식 근거 자료 없음"
    selected_prompt = prompt_name or prompt_for_blog_type(blog_type)
    template = load_prompt_template(selected_prompt)
    values = {
        "blog_type": blog_type,
        "theme": profile["theme"],
        "tone": profile["tone"],
        "keyword": keyword,
        "related_keywords": related,
        "target_year": target_year or "키워드에 명시된 최신 연도",
        "source_context": source_block,
        "card_plan": card_plan(blog_type),
        "target_context": target_context.strip() or "작성 대상 블로그 미지정",
        "output_format": _output_format(target_context),
        "year_rules": year_rules,
        "structure": structure,
    }
    try:
        formatted = template.format(**values).strip()
        return _append_target_rules(formatted, values["target_context"])
    except KeyError:
        pass
    return _append_target_rules(f"""
검색 유입 독자가 끝까지 읽을 수 있는 블로그 초안을 작성해 주세요.

블로그 유형: {blog_type}
주제 범위: {profile['theme']}
문체: {profile['tone']}
메인 키워드: {keyword}
관련 키워드: {related}
기준연도: {target_year or '키워드에 명시된 최신 연도'}

공식 근거 자료(source-url/공공API):
{source_block}

카드 이미지 계획:
{card_plan(blog_type)}

핵심 목표:
- 글을 정보 나열형 설명문이 아니라 사람이 먼저 걸러주는 블로그 글처럼 구성
- 첫 10초 안에 독자가 "내가 받을 수 있는지부터 보면 되겠다"고 느끼게 만들기
- 검색 유입 독자가 헷갈리는 지점을 줄이고, 공식 페이지에서 뭘 봐야 하는지 알게 만들기

작성 규칙:
- 한국어로 작성
- 과장, 허위 사실, 출처 없는 확정 표현 금지
- 공식 근거 자료가 있으면 해당 자료를 최우선 근거로 사용
- 공식 근거 자료에 없는 금액/기간/대상 조건은 확정값처럼 쓰지 말 것
- 공식 근거 자료를 읽지 못했거나 정보가 부족하면 "공식 공고 확인 필요"라고만 쓰고 과거 기준값으로 대체하지 말 것
- {year_rules}
- 본문 어디에도 "과거 기준", "이전 기준", "작년 기준", "전년도 기준"이라는 표현을 쓰지 말 것. 경고 문맥이어도 금지
- 첫 문단은 정의로 시작하지 말고 독자의 고민/불안/상황으로 시작
- 본문 시작은 소제목 없이 서론 2문단 이상으로 시작. 첫 H2/소제목은 서론 뒤에만 배치
- 서론에서는 독자가 왜 헷갈리는지, 이 글에서 무엇을 먼저 구분할지 자연스럽게 짚을 것
- 메인 키워드를 제목과 첫 문단에 자연스럽게 포함
- 첫 문장을 '"{keyword}"이라고 검색하면', '검색해놓고', '검색하는 분들이 많습니다' 같은 문장으로 시작하지 말 것. 키워드를 억지로 넣은 티가 남
- 첫 문장은 실제 상황 묘사로 시작. 예: '경기도 청년 지원금을 찾아보면 제일 먼저 헷갈리는 게 이름입니다.'
- 원문 자료를 그대로 요약만 하지 말고, 우선순위·비교·실전 판단 등 이 글만의 추가적인 가치를 더할 것 (구글 "Helpful Content" 기준 — 단순 요약이 아닌 실질적 가치)
- 검색엔진 노출만을 노린 나열식 정보 대신, 실제 방문자가 읽고 바로 활용할 수 있는 내용으로 작성할 것
- 제목은 판단형/문제해결형으로 작성하되, "~습니다"/"~있습니다"처럼 완결된 문장으로 끝내지 말 것 — 명사형·구(phrase)로 끝맺을 것. 예: "다 받을 수 있는 건 아닌 이유", "먼저 구분해야 하는 부분", "내가 받을 수 있는 항목부터 보는 법"
- 제목에 "총정리", "한눈에", "완벽정리"를 쓰지 말 것
- 분량은 다루는 내용의 깊이에 따라 자연스럽게 정할 것 — 글자수를 채우려고 내용을 늘리지 말 것 (참고 기준: 1,800자 내외)
- H2 소제목 5개 이상이되, 소제목이 템플릿처럼 보이지 않게 작성
- 핵심요약은 반드시 포함하되 제목을 자연스럽게 작성. 예: "먼저 구분해야 할 것", "처음엔 이렇게 나눠서 보면 됩니다", "이 부분만 먼저 보면 덜 헷갈립니다"
- 체크리스트는 필요하면 포함하되 "나는 대상일까?" 같은 고정 제목을 반복하지 말 것
- 신청 전 확인할 조건은 체크리스트 형태로 3개 이상 포함하되, 제목은 자연스럽게 붙일 것
- 놓치기 쉬운 실수 또는 실패 포인트 포함
- FAQ는 반드시 포함하되 제목을 자연스럽게 작성. 예: "검색하면서 많이 헷갈리는 부분", "여기서 질문이 많이 갈립니다"
- 마지막은 "오늘 바로 할 일", "1단계/2단계/3단계", "세 가지만 하세요" 같은 행동유도 템플릿 금지
- 마지막은 처음 보는 사람이 덜 헷갈리는 순서를 조언하듯 자연스럽게 마무리
- 딱딱한 행정문체보다 실제 블로그 운영자가 직접 찾아보고 걸러준 듯한 문체 사용
- 문단 길이를 일부러 다르게 섞기. 모든 문단을 비슷한 길이로 맞추지 말 것
- 중간중간 짧은 판단 문장 사용: "여기서 많이 헷갈립니다.", "이건 먼저 보세요.", "개인적으로는 이 순서가 낫습니다." 같은 자연스러운 코멘트 허용
- 다만 1인칭 경험을 지어내지 말 것. "제가 신청했다"처럼 사실 확인 불가 경험담 금지
- 표와 체크리스트는 쓰되, 표만으로 글을 채우지 말고 표 앞뒤에 운영자 판단을 붙일 것
- 같은 종결을 반복하지 말 것: "확인하세요", "가능합니다", "필요합니다", "정리했습니다" 반복 금지
- AI 느낌이 강한 표현 금지: "한눈에", "완벽정리", "꼼꼼히", "놓치지 마세요", "도움이 됩니다", "다음과 같습니다", "살펴보겠습니다", "알아보겠습니다", "지금 바로 할 수 있는 것", "세 가지만 하세요", "이 세 단계만 해도", "후보군이 좁혀집니다", "검색해놓고", "검색하는 분들이 많습니다"
- 소제목을 너무 기계적으로 쓰지 말 것. "핵심 정리", "FAQ", "체크리스트"만 반복하지 말고 사람이 붙인 제목처럼 쓰기

권장 구성:
{structure}

- 아래 형식을 정확히 지킬 것

{values["output_format"]}
""".strip(), values["target_context"])


def _append_target_rules(prompt: str, target_context: str) -> str:
    is_naver = "플랫폼=Naver" in target_context
    naver_rule = "- 네이버 블로그용이면 메타설명 섹션을 만들지 말 것. 제목, 본문, 태그만 작성" if is_naver else ""
    table_rule = (
        "- 표는 마크다운(|셀|셀|) 문법을 쓰지 말고 반드시 다음 형식만 사용할 것:\n"
        "표 N x M 시작\n(0,0) 헤더1\n(0,1) 헤더2\n(1,0) 내용\n(1,1) 내용\n표 N x M 끝\n"
        "(N=행 개수, M=열 개수, (행,열) 좌표는 0부터 시작)"
        if is_naver
        else "- 표는 마크다운 파이프(|셀|셀|) 문법으로 작성할 것"
    )
    return f"""{prompt}

작성 대상 블로그:
{target_context}

대상 블로그 반영 규칙:
- 글은 반드시 위 작성 대상 블로그의 플랫폼, 주제, 독자에 맞게 작성
- 네이버 블로그용이면 존대어를 유지하되 말하듯 자연스러운 구어체로 작성. 반말, 명령조, 딱딱한 행정문체는 피할 것
- 네이버 블로그용이면 모바일에서 읽기 쉽게 문단을 짧게 나누고, 문장 끝은 "확인해 보세요"만 반복하지 않게 섞을 것
- 네이버 블로그용이면 실제 블로그 운영자가 옆에서 설명하듯 "여기서 헷갈릴 수 있습니다", "이 부분은 먼저 보셔야 합니다" 같은 부드러운 판단 문장을 허용
- 본문은 반드시 소제목 없는 서론 2문단 이상으로 시작하고, 첫 소제목은 서론 다음에 둘 것
{naver_rule}
{table_rule}
- Tistory/WordPress용이면 소제목과 표를 더 정돈해서 검색 유입형 글로 작성
- 작성 대상과 맞지 않는 예시를 재사용하지 말 것
""".strip()


def _output_format(target_context: str) -> str:
    if "플랫폼=Naver" in target_context:
        return """===제목===
제목 한 줄
===제목끝===

===본문===
소제목 없는 서론 2문단 이상으로 시작한 본문
===본문끝===

===태그===
태그1, 태그2, 태그3, 태그4, 태그5
===태그끝==="""
    return """===제목===
제목 한 줄
===제목끝===

===메타설명===
검색 결과에 보일 120~150자 설명
===메타설명끝===

===본문===
소제목 없는 서론 2문단 이상으로 시작한 본문
===본문끝===

===태그===
태그1, 태그2, 태그3, 태그4, 태그5
===태그끝==="""


def _target_year(keyword: str) -> int | None:
    years = [int(y) for y in re.findall(r"20\d{2}", keyword)]
    return max(years) if years else None


def _year_rules(target_year: int | None) -> str:
    if not target_year:
        return "키워드에 기준연도가 없으면 현재 사용자가 요구한 최신 기준으로 작성하고, 과거 연도 기준 표현은 쓰지 말 것"
    past_examples = ", ".join(f"{year}년 기준" for year in range(max(2020, target_year - 3), target_year))
    return (
        f"글 전체를 반드시 {target_year}년 기준으로 작성. "
        f"{past_examples}, 작년 기준, 이전 기준, 과거 기준 같은 표현 금지. "
        f"{target_year}년 공식 공고에서 확인되지 않은 값은 추정하지 말고 '공식 공고 확인 필요'로 표시"
    )


def _engagement_structure(blog_type: str) -> str:
    if blog_type == "정부지원":
        return """1. 제도 이름과 실제 신청 대상이 달라 헷갈리는 지점으로 시작
2. 먼저 나눠볼 기준: 대상, 지역, 업종/상황, 신청기간
3. 공식 공고에서 확정해야 하는 항목
4. 받을 수 있는지 판단할 때 먼저 볼 순서
5. 신청 전에 자주 막히는 부분
6. 필요서류와 사용처/지급방식 확인 포인트
7. 공식 페이지에서 마지막으로 대조할 내용
8. 많이 헷갈리는 질문
9. 자연스러운 마무리"""
    if blog_type == "여행":
        return """1. 여행자가 실제로 막히는 고민으로 시작
2. 일정, 예산, 동선에서 먼저 판단할 기준
3. 이런 사람에게 맞는 코스 체크리스트
4. 시간대별 추천 동선
5. 비용과 예약 팁
6. 실패하기 쉬운 포인트
7. 상황별 대안
8. FAQ
9. 바로 예약/준비할 항목"""
    if blog_type == "IT":
        return """1. 사용자가 겪는 문제 상황으로 시작
2. 먼저 판단해야 할 결론과 예외
3. 이 기능/제품이 맞는 사람 체크리스트
4. 해결 방법 또는 비교표
5. 장단점과 주의점
6. 실패/오류 포인트
7. 상황별 추천
8. FAQ
9. 다음 실행 단계"""
    if blog_type == "생활정보":
        return """1. 생활 속 불편/비용 문제로 시작
2. 먼저 확인하면 시행착오를 줄이는 기준
3. 내 상황에 맞는지 체크리스트
4. 단계별 방법
5. 비용/시간 절약 팁
6. 자주 하는 실수
7. 상황별 대안
8. FAQ
9. 무리 없이 마무리하는 순서"""
    if blog_type == "리뷰":
        return """1. 이 제품을 찾게 된 상황/고민으로 시작
2. 이런 분께 맞는 제품인지 먼저 판단할 기준
3. 실제 사용 경험 — 장점
4. 실제 사용 경험 — 단점/아쉬운 점
5. 비슷한 제품과 비교
6. 사용 전 체크할 점 또는 자주 하는 실수
7. FAQ
8. 추천 대상 정리"""
    return """1. 독자 고민으로 시작
2. 30초 핵심 요약
3. 대상/상황 체크리스트
4. 핵심 정보 우선순위
5. 실행 방법
6. 실수 방지
7. FAQ
8. 다음 행동"""


def _openai_generate(prompt: str) -> tuple[str | None, str]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None, "OPENAI_API_KEY missing"
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You write practical Korean blog drafts in the exact requested format."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"], ""
    except Exception as exc:
        return None, str(exc)


def _cli_generate(prompt: str) -> tuple[str | None, str]:
    command = os.environ.get("DRAFTER_CLI_COMMAND", "claude").strip() or "claude"
    args = os.environ.get("DRAFTER_CLI_ARGS", "--print --dangerously-skip-permissions").strip()
    argv = [command] + (shlex.split(args) if args else [])
    child_env = os.environ.copy()
    if command == "claude" and os.environ.get("DRAFTER_CLI_USE_API_KEY", "").strip() != "1":
        # Claude Code CLI should use the logged-in subscription by default.
        # If ANTHROPIC_API_KEY is inherited, it may use depleted API credits instead.
        child_env.pop("ANTHROPIC_API_KEY", None)
        # Remove Claude Code session vars that cause "Credit balance is too low" errors
        # when the app is launched from within a Claude Code session.
        for _var in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
                     "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TUI_JUST_SWITCHED",
                     "CLAUDECODE", "AI_AGENT"):
            child_env.pop(_var, None)
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=360,
            check=False,
            env=child_env,
        )
    except Exception as exc:
        return None, str(exc)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        return None, f"{command} exited {result.returncode}: {stderr[:1000]}"
    output = result.stdout.strip()
    if not output:
        return None, f"{command} returned empty output"
    return output, ""


def _codex_generate(prompt: str) -> tuple[str | None, str]:
    """Claude CLI가 사용량 소진 등으로 실패했을 때 쓰는 대체 경로 (OpenAI Codex CLI).

    `codex login`으로 이미 로그인되어 있어야 하며, API 키는 필요 없다.
    """
    import tempfile

    codex_bin = shutil.which("codex") or os.path.expanduser("~/.npm-global/bin/codex")
    if not codex_bin:
        return None, "codex CLI가 설치되어 있지 않음"

    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as tmp:
        out_path = tmp.name
    try:
        result = subprocess.run(
            [codex_bin, "exec", "--output-last-message", out_path, prompt],
            text=True,
            capture_output=True,
            timeout=360,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return None, f"codex exited {result.returncode}: {stderr[:1000]}"
        try:
            with open(out_path, encoding="utf-8") as f:
                output = f.read().strip()
        except FileNotFoundError:
            output = ""
        if not output:
            return None, "codex returned empty output"
        return output, ""
    except Exception as exc:
        return None, str(exc)
    finally:
        try:
            os.unlink(out_path)
        except Exception:
            pass


def _template_generate(keyword: str, blog_type: str, related_keywords: list[str]) -> str:
    title = f"{keyword} 초안 생성 실패"
    tags = ", ".join(dict.fromkeys([keyword, blog_type]))
    body = """
초안 본문이 생성되지 않았습니다.

Claude 또는 OpenAI 생성이 실패해서 발행 가능한 글을 만들지 못했습니다. 이 화면은 발행용 초안이 아니라 오류 확인용 안내입니다.

앱의 로그에서 생성 실패 원인을 확인한 뒤 다시 초안 생성을 실행해 주세요.
""".strip()
    return f"""
===제목===
{title}
===제목끝===

===메타설명===
초안 생성 실패 안내입니다. 발행용 글이 아닙니다.
===메타설명끝===

===본문===
{body}
===본문끝===

===태그===
{tags}
===태그끝===
""".strip()


def generate_draft(keyword: str, blog_type: str, related_keywords: list[str], provider: str = "auto") -> dict:
    return generate_draft_with_sources(keyword, blog_type, related_keywords, provider=provider, source_context="")


def generate_draft_with_sources(
    keyword: str,
    blog_type: str,
    related_keywords: list[str],
    provider: str = "auto",
    source_context: str = "",
    prompt_name: str = "",
    target_context: str = "",
) -> dict:
    prompt = _build_prompt(
        keyword,
        blog_type,
        related_keywords,
        source_context=source_context,
        prompt_name=prompt_name,
        target_context=target_context,
    )
    provider = provider.lower().strip()
    used_provider = "template"

    ai_raw = None
    generation_errors = []
    if provider in {"auto", "cli"}:
        ai_raw, err = _cli_generate(prompt)
        if ai_raw:
            used_provider = "cli"
        elif err:
            generation_errors.append(f"cli: {err}")
    if not ai_raw and provider in {"auto", "cli", "codex"}:
        ai_raw, err = _codex_generate(prompt)
        if ai_raw:
            used_provider = "codex"
        elif err:
            generation_errors.append(f"codex: {err}")
    if not ai_raw and provider in {"auto", "openai"}:
        ai_raw, err = _openai_generate(prompt)
        if ai_raw:
            used_provider = "openai"
        elif err:
            generation_errors.append(f"openai: {err}")

    raw = ai_raw or _template_generate(keyword, blog_type, related_keywords)
    draft = sanitize_draft(parse_ai_response(raw, fallback_keyword=keyword))
    draft["prompt"] = prompt
    draft["used_ai"] = used_provider != "template"
    draft["provider"] = used_provider
    draft["publishable"] = used_provider != "template"
    draft["generation_error"] = " | ".join(generation_errors)
    return draft
