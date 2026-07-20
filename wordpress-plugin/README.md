# Textbox Stats WordPress Plugin

Jetpack 없이 Textbox 앱 홈 화면의 `오늘 조회수`를 가져오기 위한 작은 WordPress 플러그인입니다.

## 설치

1. `textbox-stats.zip` 파일을 WordPress 관리자 `플러그인 > 새로 추가 > 플러그인 업로드`로 올립니다.
2. 플러그인을 활성화합니다.
3. 앱에서 홈 `조회수·수익 새로고침`을 누릅니다.

## API

앱은 Application Password 인증으로 아래 엔드포인트를 먼저 호출합니다.

```text
/wp-json/textbox/v1/stats/today
```

응답 예시:

```json
{"ok":true,"provider":"textbox-stats","date":"2026-07-20","views":12}
```

플러그인이 없으면 앱은 기존 Jetpack 통계 API를 fallback으로 시도합니다.
