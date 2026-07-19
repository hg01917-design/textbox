# textbox

CLI 기반 블로그 초안 생성 + 자동 발행 도구.

키워드 분석 → 공식 출처 수집 → AI 초안 생성 → 품질 검사 → WordPress/네이버/티스토리 발행까지 한 번에 실행합니다.

## 버전 히스토리

| 버전 | 주요 변경사항 |
|------|-------------|
| v2.6 | 홈 대시보드에 계정별(네이버·티스토리·워드프레스) 오늘 발행/조회수 표 추가; 애드센스 오늘 추정 수익 연동(OAuth); "계정 관리" 창에서 블로그 계정 추가/삭제 지원; 누락되어 있던 `publisher/accounts.py`(계정↔Chrome 포트 매핑) 복구 — 이전에는 네이버/티스토리 발행이 항상 실패하는 상태였음 |
| v2.5 | 네이버 발행 시 표를 파이프 텍스트 대신 SmartEditor3 실제 표로 삽입; 목차 번호 이중 표기(`1.1.`) 제거; 공공데이터 이미지 미생성 안내 로그 추가 |
| v2.4 | 네이버 발행 시 `ㅂㅂㅂ소제목` → 소제목 서식, `표 N x M` → 파이프 표로 자동 변환 수정; 프롬프트 말투를 자연스러운 블로그 말투로 개선 (어미 반복 금지, AI 표현 금지 강화) |
| v2.3 | 네이버 임시저장/발행 시 블록별 진행 로그 추가 — "블록 입력 중 [N/M]"으로 진행 상황 표시, 예상 소요 시간 안내 |
| v2.2 | 글 작성 중 진행 상황 표시 ("⏳ 글 작성 중..."), 공공데이터 글 작성 오류 수정 (`analysis` KeyError), 에러 시 결과 영역에 표시 |
| v2.1 | 제목 후보 생성 오류 수정 — `.env`의 `ANTHROPIC_API_KEY`가 Claude OAuth 구독을 덮어쓰는 문제 해결 (`_claude_env()` 적용), `--dangerously-skip-permissions` 플래그 추가, 크레딧 부족 메시지 명확화 |
| v2.0 | 공공데이터 탭 추가 (정부24 10,968개 + 복지로 460개 로컬 캐시), 네이버 블로그 전용 프롬프트 3종 (검색용/홈판용/AI탭노출용), 제목 후보 생성 기능, `sync.py` 전체 데이터 동기화 |
| v1.0 | 초안 작성 탭 (키워드 분석 → AI 생성 → WordPress/네이버/티스토리 자동 발행) |

## Quick Start

```bash
# GUI 앱 실행
python3 app.py

# 공공데이터 동기화 (최초 1회 또는 갱신 시)
python3 sync.py --gov24 --bokjiro

# CLI 초안 생성
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원"

# WordPress에 임시저장(draft)으로 올리기
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원" --wp-draft

# WordPress에 바로 발행하기
python3 main.py --keyword "청년 지원금 2026" --blog-type "정부지원" --wp-publish
```

## 주요 기능

### 공공데이터 탭 (v2.0+)
- 정부24 · 복지로 전체 서비스 목록 로컬 캐시 (11,400+ 항목)
- 서비스 선택 → 콘텐츠 유형(검색용/홈판용/AI탭노출용) 선택 → 제목 후보 3개 자동 생성
- 선택한 제목 + 네이버 최적화 프롬프트로 블로그 글 자동 작성
- 작성된 글을 WordPress/네이버/티스토리에 바로 발행

### 네이버 블로그 프롬프트 3종
| 유형 | 제목 규칙 | 분량 | 특징 |
|------|---------|------|------|
| 검색용 | 15~25자, 세부키워드+연도+액션어 | 1,800자+ | 표 필수, 소제목 5개+ |
| 홈판용 | 15자 이내, 클릭 유도형 | 800~1,200자 | 스토리텔링, 감성 |
| AI탭노출용 | 20~35자, 자연어 질문형 | 1,500자 내외 | Q&A 구조, 출처 명시 |

### 초안 작성 탭
- 키워드 분석 (네이버 SearchAd API)
- 공식 출처 자동 수집 + 공공데이터 API 보강
- Claude CLI / OpenAI API 선택
- 품질 검사 (글자수, 연도 오류, 중복 등)
- WordPress REST API / 네이버 블로그 / 티스토리 자동 발행

## 주요 옵션 (CLI)

| 옵션 | 설명 |
|------|------|
| `--keyword` | 시드 키워드 (필수) |
| `--blog-type` | 블로그 유형: `정부지원`, `여행`, `IT`, `생활정보`, `일반` |
| `--provider` | AI 모델: `auto`, `cli`, `openai`, `template` |
| `--source-url` | 공식 출처 URL (여러 번 사용 가능) |
| `--wp-draft` | 품질 통과 시 WordPress에 임시저장 |
| `--wp-publish` | 품질 통과 시 WordPress에 즉시 발행 |
| `--cards` | 카드 이미지 생성 후 첨부 |
| `--min-chars` | 품질 검사 최소 글자수 (기본 1200) |

## API 키 설정

`.env` 파일에 필요한 항목을 설정합니다.

| 환경변수 | 용도 |
|----------|------|
| `NAVER_SEARCH_CLIENT_ID/SECRET` | 네이버 블로그 결과 수 조회 |
| `NAVER_API_KEY/SECRET_KEY/CUSTOMER_ID` | 네이버 SearchAd 월간 검색량 |
| `PUBLIC_DATA_API_KEY` | 정부24 / 복지로 공공데이터 API |
| `WP_USER/WP_APP_PASSWORD` | WordPress REST API 발행 |

> ⚠️ `.env`에 `ANTHROPIC_API_KEY`를 설정하면 Claude OAuth 구독 대신 API 크레딧을 사용합니다. API 크레딧이 없을 경우 글 생성이 실패합니다.

## 출력

- `drafts/YYYYMMDD_HHMMSS_제목.md` — 마크다운 초안
- `drafts/YYYYMMDD_HHMMSS_제목.json` — 메타데이터 + 품질 리포트

## 발행 모듈

- `publisher/wordpress.py` — WordPress REST API 발행
- `publisher/naver.py` — Playwright CDP + stealth로 네이버 블로그 발행
- `publisher/tistory.py` — TinyMCE 에디터 조작으로 티스토리 발행
