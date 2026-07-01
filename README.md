# textbox

CLI 기반 블로그 초안 생성 + 자동 발행 도구.

키워드 분석 → 공식 출처 수집 → AI 초안 생성 → 품질 검사 → WordPress/네이버/티스토리 발행까지 한 번에 실행합니다.

## Quick Start

```bash
# 초안 생성만 (파일로 저장)
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원"

# WordPress에 임시저장(draft)으로 올리기
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원" --wp-draft

# WordPress에 바로 발행하기
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원" --wp-publish

# 로컬 Claude CLI 사용
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원" --provider cli

# 공식 출처 URL 지정 (여러 개 가능)
python3 main.py --keyword "경기도 청년 지원금 2026" --blog-type "정부지원" --source-url "https://example.go.kr/notice"

# 카드 이미지 생성 포함
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원" --cards --wp-publish
```

## 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--keyword` | 시드 키워드 (필수) |
| `--blog-type` | 블로그 유형: `정부지원`, `여행`, `IT`, `생활정보`, `일반` |
| `--provider` | AI 모델: `auto`, `cli`, `openai`, `template` |
| `--source-url` | 공식 출처 URL (여러 번 사용 가능) |
| `--wp-draft` | 품질 통과 시 WordPress에 임시저장 |
| `--wp-publish` | 품질 통과 시 WordPress에 즉시 발행 |
| `--wp-category` | WordPress 카테고리 이름 |
| `--cards` | 카드 이미지 생성 후 첨부 |
| `--min-chars` | 품질 검사 최소 글자수 (기본 1200) |
| `--limit` | 연관 키워드 후보 수 (기본 15) |

## 실행 흐름

1. **키워드 분석** — 네이버 SearchAd API로 검색량 조회, 최적 키워드 선정
2. **출처 수집** — 지정된 URL 또는 공식 출처 자동 탐색 + 공공데이터 API 보강
3. **초안 생성** — Claude CLI / OpenAI API / 템플릿 중 선택
4. **품질 검사** — 글자수, 연도 오류, 중복 등 검사
5. **파일 저장** — `drafts/` 에 `.md` + `.json` 저장
6. **발행** (선택) — WordPress REST API로 발행

## 발행 모듈

- `publisher/wordpress.py` — WordPress REST API 발행
- `publisher/naver.py` — Playwright CDP + stealth로 네이버 블로그 발행
- `publisher/tistory.py` — TinyMCE 에디터 조작으로 티스토리 발행
- `publisher/browser.py` — CDP 브라우저 연결 공통 모듈

## API 키 설정

`.env.example`을 `.env`로 복사하고 필요한 항목만 채웁니다.

```bash
cp .env.example .env
```

| 환경변수 | 용도 |
|----------|------|
| `NAVER_SEARCH_CLIENT_ID/SECRET` | 네이버 블로그 결과 수 조회 |
| `NAVER_API_KEY/SECRET_KEY/CUSTOMER_ID` | 네이버 SearchAd 월간 검색량 |
| `OPENAI_API_KEY` | OpenAI로 초안 생성 |
| `OPENAI_MODEL` | 사용할 모델 (기본 `gpt-4.1-mini`) |
| `DRAFTER_CLI_COMMAND/ARGS` | 로컬 CLI 모델 명령어 (기본 `claude --print`) |
| `WP_SITE_URL/USER/APP_PASSWORD` | WordPress REST API 발행 |
| `WP_DEFAULT_STATUS` | 기본 발행 상태 (기본 `draft`) |

## 출력

- `drafts/YYYYMMDD_HHMMSS_제목.md` — 마크다운 초안
- `drafts/YYYYMMDD_HHMMSS_제목.json` — 메타데이터 + 품질 리포트 포함
